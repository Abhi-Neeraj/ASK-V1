// The locket over the computer's own Bluetooth, from Chrome or Edge on Windows (Web Bluetooth). It lets the
// website do what the phone app does (pair with the emailed code, Start/Stop, receive meetings, upload them), so
// the whole chain can be tried on a laptop before the phone app is installed.
//
// What differs from the phone (getLocketLink.native.ts):
// - No background scanning: the browser shows its own chooser, and only right after a click. A locket picked once
//   is remembered for the life of the tab, so reconnecting needs no chooser. After a reload, pick it again
//   (Lockets, Find lockets nearby).
// - Pairing: reading BOND_PROOF, which needs an authenticated link, makes the browser ask for the code.
// - The browser refuses two Bluetooth operations at once, so every read, write and subscription is queued.
// - The packet size cannot be read; Windows normally agrees a size large enough for the locket's frames.
//
// NOT RUN where this was written against a real locket: tested with a fake browser Bluetooth only.
import type { FoundLocket, LocketLink } from "@/domain/locket/link";
import { LOCKET_UUIDS } from "@/domain/locket/uuids";

// The few parts of the browser's Bluetooth API this file uses (TypeScript has no built-in types for it).
type ValueEvent = { target: unknown };
export type WebBluetoothCharacteristic = {
  value?: DataView | null;
  readValue(): Promise<DataView>;
  writeValueWithResponse(value: BufferSource): Promise<void>;
  startNotifications(): Promise<WebBluetoothCharacteristic>;
  addEventListener(type: "characteristicvaluechanged", listener: (event: ValueEvent) => void): void;
  removeEventListener(type: "characteristicvaluechanged", listener: (event: ValueEvent) => void): void;
};
type WebBluetoothService = { getCharacteristic(uuid: string): Promise<WebBluetoothCharacteristic> };
type WebBluetoothServer = { getPrimaryService(uuid: string): Promise<WebBluetoothService> };
export type WebBluetoothDevice = {
  id: string;
  name?: string;
  gatt?: { connected: boolean; connect(): Promise<WebBluetoothServer>; disconnect(): void };
  addEventListener(type: "gattserverdisconnected", listener: () => void): void;
  removeEventListener(type: "gattserverdisconnected", listener: () => void): void;
};
export type WebBluetooth = {
  requestDevice(request: {
    filters: Array<{ services?: string[]; namePrefix?: string }>;
    optionalServices: string[];
  }): Promise<WebBluetoothDevice>;
  getAvailability?(): Promise<boolean>;
};

const PICK_AGAIN = "Open Lockets and press Find lockets nearby to choose the locket in this browser tab.";
const text = (view: DataView) => new TextDecoder().decode(new Uint8Array(view.buffer, view.byteOffset, view.byteLength));
const bytes = (view: DataView) => new Uint8Array(view.buffer.slice(view.byteOffset, view.byteOffset + view.byteLength));

export class WebBluetoothLocketLink implements LocketLink {
  private readonly known = new Map<string, WebBluetoothDevice>();
  private device: WebBluetoothDevice | null = null;
  private chars = new Map<string, WebBluetoothCharacteristic>();
  private queue: Promise<unknown> = Promise.resolve();
  private readonly bondRetryMs: number;

  constructor(
    private readonly bluetooth: WebBluetooth,
    /** True right after a click: the only time the browser lets a page open its Bluetooth chooser. */
    private readonly mayOpenChooser: () => boolean,
    options: { bondRetryMs?: number } = {},
  ) {
    this.bondRetryMs = options.bondRetryMs ?? 1500;
  }

  /** Runs Bluetooth operations one after another. */
  private run<T>(operation: () => Promise<T>): Promise<T> {
    const next = this.queue.then(operation, operation);
    this.queue = next.catch(() => undefined);
    return next;
  }

  private characteristic(uuid: string): WebBluetoothCharacteristic {
    const found = this.chars.get(uuid);
    if (!found) throw new Error("Not connected to a locket.");
    return found;
  }

  scan(onFound: (locket: FoundLocket) => void, onError?: (message: string) => void): () => void {
    let stopped = false;
    for (const device of this.known.values()) {
      if (stopped) break;
      onFound({ id: device.id, name: device.name ?? "", rssi: 0 });
    }
    if (stopped) return () => undefined;
    if (!this.mayOpenChooser()) {
      if (this.known.size === 0) queueMicrotask(() => !stopped && onError?.(PICK_AGAIN));
      return () => {
        stopped = true;
      };
    }
    // A moment later (a caller that matched a known locket stops the scan as soon as scan() returns), but still
    // while the click counts: the browser refuses the chooser seconds later.
    queueMicrotask(() => {
      if (!stopped) this.choose(() => stopped, onFound, onError);
    });
    return () => {
      stopped = true;
    };
  }

  private choose(
    stopped: () => boolean,
    onFound: (locket: FoundLocket) => void,
    onError?: (message: string) => void,
  ): void {
    this.bluetooth
      .requestDevice({
        filters: [{ services: [LOCKET_UUIDS.service] }, { namePrefix: "ASK-" }],
        optionalServices: [LOCKET_UUIDS.service],
      })
      .then((device) => {
        this.known.set(device.id, device);
        if (!stopped()) onFound({ id: device.id, name: device.name ?? "", rssi: 0 });
      })
      .catch((error: unknown) => {
        if (stopped()) return;
        const name = error instanceof Error ? error.name : "";
        if (name === "NotFoundError") onError?.("No locket was chosen.");
        else if (name === "SecurityError") onError?.("The browser only shows Bluetooth devices right after a click. " + PICK_AGAIN);
        else onError?.(error instanceof Error ? error.message : "The browser could not look for lockets.");
      });
  }

  async connect(id: string): Promise<void> {
    const device = this.known.get(id);
    if (!device?.gatt) throw new Error(`This browser tab does not know that locket yet. ${PICK_AGAIN}`);
    await this.run(async () => {
      const server = await device.gatt!.connect();
      const service = await server.getPrimaryService(LOCKET_UUIDS.service);
      const chars = new Map<string, WebBluetoothCharacteristic>();
      for (const uuid of [
        LOCKET_UUIDS.info,
        LOCKET_UUIDS.pairNonce,
        LOCKET_UUIDS.control,
        LOCKET_UUIDS.bondProof,
        LOCKET_UUIDS.status,
        LOCKET_UUIDS.audio,
      ]) {
        chars.set(uuid, await service.getCharacteristic(uuid));
      }
      this.chars = chars;
      this.device = device;
    });
  }

  async disconnect(): Promise<void> {
    const device = this.device;
    this.device = null;
    this.chars = new Map();
    if (device?.gatt?.connected) device.gatt.disconnect();
  }

  onDisconnected(listener: () => void): () => void {
    const device = this.device;
    if (!device) throw new Error("Not connected to a locket.");
    const handler = () => listener();
    device.addEventListener("gattserverdisconnected", handler);
    return () => device.removeEventListener("gattserverdisconnected", handler);
  }

  async readInfo(): Promise<string> {
    return this.run(async () => text(await this.characteristic(LOCKET_UUIDS.info).readValue()));
  }

  async readNonce(): Promise<Uint8Array> {
    return this.run(async () => bytes(await this.characteristic(LOCKET_UUIDS.pairNonce).readValue()));
  }

  async bond(): Promise<void> {
    // BOND_PROOF needs an authenticated link: reading it makes the browser (or Windows) ask for the code. If the
    // first read fails while the pairing is still settling, try once more; a second failure means a wrong code
    // or a cancelled box, and pairing starts again with a new code.
    try {
      await this.readProof();
    } catch {
      await new Promise((resolve) => setTimeout(resolve, this.bondRetryMs));
      await this.readProof();
    }
  }

  async readProof(): Promise<string> {
    return this.run(async () => text(await this.characteristic(LOCKET_UUIDS.bondProof).readValue()).trim());
  }

  async send(command: string): Promise<void> {
    await this.run(() => this.characteristic(LOCKET_UUIDS.control).writeValueWithResponse(new TextEncoder().encode(command)));
  }

  private monitor(uuid: string, listener: (value: DataView) => void): () => void {
    const characteristic = this.characteristic(uuid);
    const handler = (event: ValueEvent) => {
      const value = (event.target as WebBluetoothCharacteristic).value;
      if (value) listener(value);
    };
    characteristic.addEventListener("characteristicvaluechanged", handler);
    // A failure here means the link dropped; onDisconnected reports that.
    void this.run(() => characteristic.startNotifications()).catch(() => undefined);
    return () => characteristic.removeEventListener("characteristicvaluechanged", handler);
  }

  onStatus(listener: (text: string) => void): () => void {
    return this.monitor(LOCKET_UUIDS.status, (value) => listener(text(value)));
  }

  onAudio(listener: (frame: Uint8Array) => void): () => void {
    return this.monitor(LOCKET_UUIDS.audio, (value) => listener(bytes(value)));
  }
}
