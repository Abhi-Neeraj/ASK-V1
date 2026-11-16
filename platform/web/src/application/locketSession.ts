// The phone's live connection to its locket, as plain logic (the React provider in locketController.tsx only
// shows its state). It:
// - connects to a locket the person is already on, by finding its product ID among nearby lockets;
// - adopts the connection that pairing just opened (no second connect);
// - keeps ONE receiver for the whole meeting, so a dropout does not restart the recording: after it reconnects
//   it asks the locket only for what it missed;
// - reconnects by itself until the person disconnects;
// - saves every part on the phone before the locket may delete it, then uploads; offline it keeps it and tries
//   again after each connect and save.

import { type CapturedPart, RecordingCapture } from "@/application/captureRecording";
import { pendingCount, retryPending, savePart } from "@/application/syncRecording";
import { type LocketStatus, parseInfo, parseStatus, time } from "@/domain/locket/commands";
import type { FoundLocket, LocketLink } from "@/domain/locket/link";
import type { UploadAck } from "@/infrastructure/api/types";
import type { LocalFiles } from "@/infrastructure/files/localFiles";

export type SessionState = {
  productId: string | null;
  connected: boolean;
  reconnecting: boolean;
  status: LocketStatus | null;
  firmwareVersion: string | null;
  message: string | null;
  pending: number;
  lastRecordingId: string | null;
};

type UploadApi = {
  uploadRecording(
    productId: string,
    clientId: string,
    wav: Uint8Array,
    recordedAt: Date,
    group?: { sessionRef: string; sequence: number },
  ): Promise<UploadAck>;
};

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 20000, 30000];

export class LocketSession {
  private state: SessionState = {
    productId: null,
    connected: false,
    reconnecting: false,
    status: null,
    firmwareVersion: null,
    message: null,
    pending: 0,
    lastRecordingId: null,
  };
  private deviceId: string | null = null;
  private wanted = false;
  private capture: RecordingCapture | null = null;
  private stops: (() => void)[] = [];
  private uploading: Promise<void> | null = null;

  constructor(
    private readonly deps: {
      link: LocketLink;
      api: UploadApi;
      files: LocalFiles;
      onChange: (state: SessionState) => void;
      scanTimeoutMs?: number;
      sleep?: (ms: number) => Promise<void>;
    },
  ) {}

  get current(): SessionState {
    return this.state;
  }

  private set(patch: Partial<SessionState>): void {
    this.state = { ...this.state, ...patch };
    this.deps.onChange(this.state);
  }

  private sleep(ms: number): Promise<void> {
    return this.deps.sleep ? this.deps.sleep(ms) : new Promise((resolve) => setTimeout(resolve, ms));
  }

  /** Find the locket with this product ID nearby and connect to it. */
  async connectTo(productId: string): Promise<void> {
    this.set({ message: null });
    const found = await this.find(productId);
    if (!found) {
      this.set({ connected: false, message: `${productId} was not found nearby. Turn it on and keep it close.` });
      return;
    }
    try {
      await this.deps.link.connect(found.id);
    } catch (error) {
      this.set({ connected: false, message: error instanceof Error ? error.message : "Could not connect." });
      return;
    }
    await this.attach(found);
  }

  /** Take over a connection that is already open (right after pairing). */
  async attach(found: FoundLocket): Promise<void> {
    this.deviceId = found.id;
    this.wanted = true;
    this.subscribe();
    await this.afterConnect(found.name);
  }

  async disconnect(): Promise<void> {
    this.wanted = false;
    this.unsubscribe();
    await this.deps.link.disconnect();
    this.set({ connected: false, reconnecting: false, status: null });
  }

  /** Upload everything waiting on this phone. Safe to call often: one run at a time. */
  uploadWaiting(): Promise<void> {
    this.uploading ??= (async () => {
      try {
        const { uploaded, rejected, reasons } = await retryPending({ api: this.deps.api, files: this.deps.files });
        const pending = await pendingCount(this.deps.files);
        const patch: Partial<SessionState> = { pending };
        if (uploaded.length) patch.lastRecordingId = uploaded[uploaded.length - 1];
        if (rejected.length) {
          const why = [...new Set(reasons)].join("; ");
          patch.message = `${rejected.length} recording(s) were refused by the server and set aside${why ? ` (${why})` : ""}.`;
        }
        else if (pending) patch.message = "Recordings are saved on this phone and will upload when you are online.";
        else if (uploaded.length) patch.message = null;
        this.set(patch);
      } finally {
        this.uploading = null;
      }
    })();
    return this.uploading;
  }

  private find(productId: string): Promise<FoundLocket | null> {
    return new Promise((resolve) => {
      let done = false;
      let stop: () => void = () => undefined;
      const finish = (value: FoundLocket | null) => {
        if (done) return;
        done = true;
        stop();
        resolve(value);
      };
      stop = this.deps.link.scan(
        (locket) => {
          if (locket.name === productId) finish(locket);
        },
        (message) => {
          this.set({ message });
          finish(null);
        },
      );
      if (done) stop();
      setTimeout(() => finish(null), this.deps.scanTimeoutMs ?? 12000);
    });
  }

  private subscribe(): void {
    this.unsubscribe();
    const link = this.deps.link;
    this.capture ??= new RecordingCapture(link, (part) => this.keep(part), {
      onError: () => this.set({ message: "A recording could not be saved on this phone. The locket keeps it; free some space and reconnect." }),
    });
    this.stops.push(this.capture.start());
    this.stops.push(link.onStatus((text) => this.set({ status: parseStatus(text) })));
    this.stops.push(link.onDisconnected(() => void this.lost()));
  }

  private unsubscribe(): void {
    this.stops.forEach((stop) => stop());
    this.stops = [];
  }

  private async afterConnect(fallbackName: string): Promise<void> {
    const info = parseInfo(await this.deps.link.readInfo().catch(() => ""));
    this.set({
      productId: info?.productId ?? fallbackName,
      firmwareVersion: info?.firmwareVersion ?? null,
      connected: true,
      reconnecting: false,
    });
    await this.deps.link.send(time(Date.now())).catch(() => undefined);
    void this.uploadWaiting();
  }

  private async keep(part: CapturedPart): Promise<void> {
    await savePart({ files: this.deps.files, productId: this.state.productId ?? "", part });
    this.set({ pending: await pendingCount(this.deps.files) });
    void this.uploadWaiting();
  }

  private async lost(): Promise<void> {
    this.set({ connected: false, status: null });
    if (!this.wanted || !this.deviceId) return;
    this.set({ reconnecting: true });
    for (const delay of RECONNECT_DELAYS_MS.concat(Array(1000).fill(30000))) {
      if (!this.wanted) return;
      try {
        await this.deps.link.connect(this.deviceId);
        if (!this.wanted) return;
        this.subscribe();
        this.capture?.reconnected();
        await this.afterConnect(this.state.productId ?? "");
        return;
      } catch {
        await this.sleep(delay);
      }
    }
  }
}
