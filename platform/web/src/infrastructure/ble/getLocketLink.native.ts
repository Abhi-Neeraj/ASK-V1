// Phone build: the locket over Bluetooth Low Energy with react-native-ble-plx. Needs a development build
// (`npx expo run:android` or `run:ios`); Expo Go has no Bluetooth. NOT RUN where this was written: there was no
// phone and no locket, so this file is type-checked only.
import { PermissionsAndroid, Platform } from "react-native";
import { BleManager, State, type Subscription } from "react-native-ble-plx";
import type { FoundLocket, LocketLink } from "@/domain/locket/link";
import { BLOCK_BYTES } from "@/domain/locket/adpcm";
import { HEADER_LEN } from "@/domain/device/framing";
import { LOCKET_UUIDS, REQUESTED_MTU } from "@/domain/locket/uuids";

/** One AUDIO frame (header + block) per notification, plus the 3 bytes the link itself uses. */
const MIN_MTU = HEADER_LEN + BLOCK_BYTES + 3;

const fromBase64 = (value: string | null | undefined): Uint8Array => {
  if (!value) return new Uint8Array(0);
  const binary = atob(value);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
  return out;
};

const toBase64 = (bytes: Uint8Array): string => {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
};

const text = (bytes: Uint8Array) => new TextDecoder().decode(bytes);

/** Android 12+ asks for "nearby devices"; older Android asks for location to scan. iOS asks by itself. */
async function permitted(): Promise<boolean> {
  if (Platform.OS !== "android") return true;
  const level = typeof Platform.Version === "number" ? Platform.Version : Number(Platform.Version);
  const wanted =
    level >= 31
      ? [PermissionsAndroid.PERMISSIONS.BLUETOOTH_SCAN, PermissionsAndroid.PERMISSIONS.BLUETOOTH_CONNECT]
      : [PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION];
  const answers = await PermissionsAndroid.requestMultiple(wanted);
  return wanted.every((p) => answers[p] === PermissionsAndroid.RESULTS.GRANTED);
}

class BlePlxLocketLink implements LocketLink {
  private readonly manager = new BleManager();
  private deviceId: string | null = null;
  private monitors: Subscription[] = [];

  private requireDevice(): string {
    if (!this.deviceId) throw new Error("Not connected to a locket.");
    return this.deviceId;
  }

  private async read(characteristic: string): Promise<Uint8Array> {
    const result = await this.manager.readCharacteristicForDevice(this.requireDevice(), LOCKET_UUIDS.service, characteristic);
    return fromBase64(result.value);
  }

  private async bluetoothOn(): Promise<string | null> {
    if (!(await permitted())) return "ASK needs permission to use Bluetooth. Allow it in the phone's settings.";
    const state = await this.manager.state();
    if (state === State.PoweredOn) return null;
    if (state === State.Unauthorized) return "ASK needs permission to use Bluetooth. Allow it in the phone's settings.";
    if (state === State.Unsupported) return "This phone has no Bluetooth Low Energy.";
    return "Turn Bluetooth on, then try again.";
  }

  scan(onFound: (locket: FoundLocket) => void, onError?: (message: string) => void): () => void {
    let stopped = false;
    void this.bluetoothOn().then((problem) => {
      if (stopped) return;
      if (problem) {
        onError?.(problem);
        return;
      }
      this.manager
        .startDeviceScan([LOCKET_UUIDS.service], { allowDuplicates: false }, (error, device) => {
          if (error) {
            onError?.(error.message || "Scanning for lockets failed.");
            return;
          }
          const name = device?.name ?? device?.localName ?? "";
          if (device && name.startsWith("ASK-")) onFound({ id: device.id, name, rssi: device.rssi ?? 0 });
        })
        .catch((error: unknown) => onError?.(error instanceof Error ? error.message : "Scanning for lockets failed."));
    });
    return () => {
      stopped = true;
      this.manager.stopDeviceScan().catch(() => undefined);
    };
  }

  async connect(id: string): Promise<void> {
    const problem = await this.bluetoothOn();
    if (problem) throw new Error(problem);
    await this.manager.stopDeviceScan().catch(() => undefined);
    if (await this.manager.isDeviceConnected(id)) {
      this.deviceId = id;
      return;
    }
    let device = await this.manager.connectToDevice(id, { requestMTU: REQUESTED_MTU });
    if (device.mtu < MIN_MTU) device = await this.manager.requestMTUForDevice(id, REQUESTED_MTU);
    if (device.mtu < MIN_MTU) {
      await this.manager.cancelDeviceConnection(id).catch(() => undefined);
      throw new Error(`This phone's Bluetooth packets are too small for the locket (${device.mtu} bytes).`);
    }
    await this.manager.discoverAllServicesAndCharacteristicsForDevice(id);
    this.deviceId = id;
  }

  async disconnect(): Promise<void> {
    this.monitors.forEach((m) => m.remove());
    this.monitors = [];
    if (this.deviceId) await this.manager.cancelDeviceConnection(this.deviceId).catch(() => undefined);
    this.deviceId = null;
  }

  onDisconnected(listener: () => void): () => void {
    const subscription = this.manager.onDeviceDisconnected(this.requireDevice(), () => listener());
    return () => subscription.remove();
  }

  async readInfo(): Promise<string> {
    return text(await this.read(LOCKET_UUIDS.info));
  }

  async readNonce(): Promise<Uint8Array> {
    return this.read(LOCKET_UUIDS.pairNonce);
  }

  async bond(): Promise<void> {
    // Reading an encrypted characteristic makes the phone's operating system pair, asking for the passkey.
    await this.read(LOCKET_UUIDS.bondProof);
  }

  async readProof(): Promise<string> {
    return text(await this.read(LOCKET_UUIDS.bondProof)).trim();
  }

  async send(command: string): Promise<void> {
    await this.manager.writeCharacteristicWithResponseForDevice(
      this.requireDevice(),
      LOCKET_UUIDS.service,
      LOCKET_UUIDS.control,
      toBase64(new TextEncoder().encode(command)),
    );
  }

  private monitor(characteristic: string, listener: (bytes: Uint8Array) => void): () => void {
    const subscription = this.manager.monitorCharacteristicForDevice(
      this.requireDevice(),
      LOCKET_UUIDS.service,
      characteristic,
      (error, value) => {
        // Errors here mean the link dropped; onDisconnected reports that.
        if (!error && value?.value) listener(fromBase64(value.value));
      },
    );
    this.monitors.push(subscription);
    return () => {
      subscription.remove();
      this.monitors = this.monitors.filter((m) => m !== subscription);
    };
  }

  onStatus(listener: (text: string) => void): () => void {
    return this.monitor(LOCKET_UUIDS.status, (bytes) => listener(text(bytes)));
  }

  onAudio(listener: (frame: Uint8Array) => void): () => void {
    return this.monitor(LOCKET_UUIDS.audio, listener);
  }
}

let shared: BlePlxLocketLink | null = null;

export function getLocketLink(): LocketLink | null {
  shared ??= new BlePlxLocketLink();
  return shared;
}
