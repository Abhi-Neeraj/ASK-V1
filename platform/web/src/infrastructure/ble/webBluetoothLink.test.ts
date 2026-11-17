import { describe, expect, it, vi } from "vitest";
import { LOCKET_UUIDS } from "@/domain/locket/uuids";
import {
  type WebBluetooth,
  type WebBluetoothCharacteristic,
  type WebBluetoothDevice,
  WebBluetoothLocketLink,
} from "@/infrastructure/ble/webBluetoothLink";

type Listener = (event: { target: unknown }) => void;

class FakeCharacteristic implements WebBluetoothCharacteristic {
  value: DataView | null = null;
  reads: Array<() => Promise<Uint8Array>> = [];
  written: string[] = [];
  notifying = false;
  log: string[];
  private listeners: Listener[] = [];
  constructor(
    readonly uuid: string,
    log: string[],
    private readonly content: Uint8Array = new Uint8Array(0),
  ) {
    this.log = log;
  }
  async readValue(): Promise<DataView> {
    const next = this.reads.shift();
    const bytes = next ? await next() : this.content;
    return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  }
  async writeValueWithResponse(value: BufferSource): Promise<void> {
    this.log.push(`write-start ${this.uuid}`);
    await new Promise((resolve) => setTimeout(resolve, 5));
    this.written.push(new TextDecoder().decode(value as Uint8Array));
    this.log.push(`write-end ${this.uuid}`);
  }
  async startNotifications(): Promise<WebBluetoothCharacteristic> {
    this.notifying = true;
    return this;
  }
  addEventListener(_type: "characteristicvaluechanged", listener: Listener): void {
    this.listeners.push(listener);
  }
  removeEventListener(_type: "characteristicvaluechanged", listener: Listener): void {
    this.listeners = this.listeners.filter((l) => l !== listener);
  }
  emit(bytes: Uint8Array): void {
    this.value = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.listeners.forEach((l) => l({ target: this }));
  }
  listenerCount(): number {
    return this.listeners.length;
  }
}

const text = (s: string) => new TextEncoder().encode(s);

function fakeLocket(name = "ASK-7K2M9Q") {
  const log: string[] = [];
  const chars = new Map<string, FakeCharacteristic>([
    [LOCKET_UUIDS.info, new FakeCharacteristic(LOCKET_UUIDS.info, log, text("ASKINFO/1 ASK-7K2M9Q 1.0.0 -1 0"))],
    [LOCKET_UUIDS.pairNonce, new FakeCharacteristic(LOCKET_UUIDS.pairNonce, log, new Uint8Array(16).fill(7))],
    [LOCKET_UUIDS.control, new FakeCharacteristic(LOCKET_UUIDS.control, log)],
    [LOCKET_UUIDS.bondProof, new FakeCharacteristic(LOCKET_UUIDS.bondProof, log, text("ab".repeat(32)))],
    [LOCKET_UUIDS.status, new FakeCharacteristic(LOCKET_UUIDS.status, log)],
    [LOCKET_UUIDS.audio, new FakeCharacteristic(LOCKET_UUIDS.audio, log)],
  ]);
  const disconnectListeners: Array<() => void> = [];
  const device: WebBluetoothDevice = {
    id: "dev-1",
    name,
    gatt: {
      connected: false,
      async connect() {
        this.connected = true;
        return {
          async getPrimaryService(uuid: string) {
            if (uuid !== LOCKET_UUIDS.service) throw new Error("no such service");
            return {
              async getCharacteristic(c: string) {
                const found = chars.get(c);
                if (!found) throw new Error("no such characteristic");
                return found;
              },
            };
          },
        };
      },
      disconnect() {
        this.connected = false;
        disconnectListeners.forEach((l) => l());
      },
    },
    addEventListener: (_type, listener) => disconnectListeners.push(listener),
    removeEventListener: (_type, listener) => {
      const i = disconnectListeners.indexOf(listener);
      if (i >= 0) disconnectListeners.splice(i, 1);
    },
  };
  return { device, chars, log, dropLink: () => disconnectListeners.slice().forEach((l) => l()) };
}

function fakeBluetooth(device: WebBluetoothDevice | null, options: { cancel?: boolean } = {}) {
  const requests: unknown[] = [];
  const bluetooth: WebBluetooth = {
    async requestDevice(request) {
      requests.push(request);
      if (options.cancel || !device) {
        const error = new Error("User cancelled the requestDevice() chooser.");
        error.name = "NotFoundError";
        throw error;
      }
      return device;
    },
  };
  return { bluetooth, requests };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("WebBluetoothLocketLink", () => {
  it("opens the browser's chooser for ASK lockets after a click and reports the one picked", async () => {
    const { device } = fakeLocket();
    const { bluetooth, requests } = fakeBluetooth(device);
    const link = new WebBluetoothLocketLink(bluetooth, () => true);
    const found: string[] = [];
    link.scan((l) => found.push(`${l.id} ${l.name}`));
    await flush();
    expect(found).toEqual(["dev-1 ASK-7K2M9Q"]);
    expect(requests).toEqual([
      { filters: [{ services: [LOCKET_UUIDS.service] }, { namePrefix: "ASK-" }], optionalServices: [LOCKET_UUIDS.service] },
    ]);
  });

  it("reconnects to a locket picked earlier without opening the chooser again", async () => {
    const { device } = fakeLocket();
    const { bluetooth, requests } = fakeBluetooth(device);
    const link = new WebBluetoothLocketLink(bluetooth, () => true);
    link.scan(() => undefined);
    await flush();
    // Exactly what LocketSession.find does: stop on a match, and once more after scan() returns.
    let stop: () => void = () => undefined;
    let done = false;
    const found: string[] = [];
    stop = link.scan((l) => {
      if (done) return;
      done = true;
      found.push(l.name);
      stop();
    });
    if (done) stop();
    await flush();
    expect(found).toEqual(["ASK-7K2M9Q"]);
    expect(requests).toHaveLength(1);
  });

  it("explains what to do when it cannot open the chooser (no click, nothing picked yet)", async () => {
    const { bluetooth, requests } = fakeBluetooth(null);
    const link = new WebBluetoothLocketLink(bluetooth, () => false);
    const errors: string[] = [];
    link.scan(() => undefined, (m) => errors.push(m));
    await flush();
    expect(requests).toHaveLength(0);
    expect(errors[0]).toMatch(/Find lockets nearby/);
  });

  it("says so when the chooser is closed without picking a locket", async () => {
    const { bluetooth } = fakeBluetooth(null, { cancel: true });
    const link = new WebBluetoothLocketLink(bluetooth, () => true);
    const errors: string[] = [];
    link.scan(() => undefined, (m) => errors.push(m));
    await flush();
    expect(errors).toEqual(["No locket was chosen."]);
  });

  it("connects to the picked locket and reads INFO and the pairing number", async () => {
    const { device } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true);
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    expect(await link.readInfo()).toBe("ASKINFO/1 ASK-7K2M9Q 1.0.0 -1 0");
    expect(Array.from(await link.readNonce())).toEqual(new Array(16).fill(7));
  });

  it("a read that never answers gives up and does not hold back what comes next", async () => {
    const { device, chars } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true);
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    chars.get(LOCKET_UUIDS.info)?.reads.push(() => new Promise<Uint8Array>(() => undefined)); // the radio drops it
    vi.useFakeTimers();
    try {
      const stuck = link.readInfo();
      const next = link.send("ACK SESSION000000001 64");
      const outcome = expect(stuck).rejects.toThrow(/did not answer/);
      await vi.advanceTimersByTimeAsync(15_001);
      await outcome;
      await vi.advanceTimersByTimeAsync(20);
      await next;
      expect(chars.get(LOCKET_UUIDS.control)?.written).toEqual(["ACK SESSION000000001 64"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refuses to connect to a locket that was never picked in this browser tab", async () => {
    const link = new WebBluetoothLocketLink(fakeBluetooth(null).bluetooth, () => true);
    await expect(link.connect("unknown")).rejects.toThrow(/Find lockets nearby/);
  });

  it("sends commands one at a time (the browser refuses two Bluetooth operations at once)", async () => {
    const { device, chars, log } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true);
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    await Promise.all([link.send("ACK SESSION000000001 32"), link.send("TIME 1763114400000")]);
    expect(chars.get(LOCKET_UUIDS.control)?.written).toEqual(["ACK SESSION000000001 32", "TIME 1763114400000"]);
    expect(log).toEqual([
      `write-start ${LOCKET_UUIDS.control}`,
      `write-end ${LOCKET_UUIDS.control}`,
      `write-start ${LOCKET_UUIDS.control}`,
      `write-end ${LOCKET_UUIDS.control}`,
    ]);
  });

  it("passes AUDIO and STATUS notifications on, and stops when asked", async () => {
    const { device, chars } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true);
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    const frames: number[][] = [];
    const statuses: string[] = [];
    const stopAudio = link.onAudio((f) => frames.push(Array.from(f)));
    link.onStatus((s) => statuses.push(s));
    await flush();
    const audio = chars.get(LOCKET_UUIDS.audio)!;
    expect(audio.notifying).toBe(true);
    audio.emit(new Uint8Array([0x41, 0x53, 1]));
    chars.get(LOCKET_UUIDS.status)!.emit(text("ASKSTAT/1 idle -1 - 0 0"));
    stopAudio();
    audio.emit(new Uint8Array([9]));
    expect(frames).toEqual([[0x41, 0x53, 1]]);
    expect(statuses).toEqual(["ASKSTAT/1 idle -1 - 0 0"]);
    expect(audio.listenerCount()).toBe(0);
  });

  it("bonds by reading BOND_PROOF (the browser asks for the code), trying once more if the first read fails", async () => {
    const { device, chars } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true, { bondRetryMs: 1 });
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    const proof = chars.get(LOCKET_UUIDS.bondProof)!;
    proof.reads.push(() => Promise.reject(new Error("GATT operation failed for unknown reason.")));
    await link.bond();
    expect(await link.readProof()).toBe("ab".repeat(32));
  });

  it("fails bonding when reading BOND_PROOF keeps failing (wrong code or cancelled)", async () => {
    const { device, chars } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true, { bondRetryMs: 1 });
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    const proof = chars.get(LOCKET_UUIDS.bondProof)!;
    const refuse = () => Promise.reject(new Error("Authentication failed."));
    proof.reads.push(refuse, refuse);
    await expect(link.bond()).rejects.toThrow();
  });

  it("reports a dropped link", async () => {
    const { device, dropLink } = fakeLocket();
    const link = new WebBluetoothLocketLink(fakeBluetooth(device).bluetooth, () => true);
    link.scan(() => undefined);
    await flush();
    await link.connect("dev-1");
    let dropped = 0;
    const stop = link.onDisconnected(() => (dropped += 1));
    dropLink();
    stop();
    dropLink();
    expect(dropped).toBe(1);
  });
});
