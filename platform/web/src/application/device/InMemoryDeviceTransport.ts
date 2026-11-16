import { AskFrame, encodeFrame } from "@/domain/device/framing";
import { AskSession, SessionListener } from "@/domain/device/transport";

// An in-memory transport for the app/gateway side: receives frames (as if from
// a WebSocket/HTTP endpoint) and hands complete sessions to the processor.
// This is the seam where a real WSS/HTTPS server would plug in.
export class InMemoryDeviceTransport implements AskSessionTransport {
  readonly kind = "wifi_websocket";
  private listener?: SessionListener;
  private onFrame?: (bytes: Uint8Array) => void;

  onSession(listener: SessionListener): void {
    this.listener = listener;
  }

  async connect(): Promise<void> {}

  async send(frame: AskFrame): Promise<void> {
    this.onFrame?.(encodeFrame(frame));
  }

  async disconnect(): Promise<void> {}

  // Gateway hook: feed raw frame bytes into the transport.
  inject(bytes: Uint8Array): void {
    this.onFrame?.(bytes);
  }

  // Called by the ingestion service to receive frames.
  _setFrameHandler(handler: (bytes: Uint8Array) => void): void {
    this.onFrame = handler;
  }

  _emit(session: AskSession): void {
    this.listener?.(session);
  }
}

export type AskSessionTransport = {
  kind: "wifi_websocket" | "wifi_http" | "ble";
  connect(): Promise<void>;
  send(frame: AskFrame): Promise<void>;
  disconnect(): Promise<void>;
  onSession(listener: SessionListener): void;
};
