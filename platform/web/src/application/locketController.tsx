// React access to the locket connection (the logic is LocketSession): the phone app, or Chrome/Edge on a computer.
// Where there is no Bluetooth (Safari, Firefox), `available` is false and screens hide every Bluetooth control.

import React from "react";
import { LocketSession, type SessionState } from "@/application/locketSession";
import { useSession } from "@/application/session";
import { useKeepAwake } from "@/application/useKeepAwake";
import { forget, recStart, recStop } from "@/domain/locket/commands";
import type { FoundLocket, LocketLink } from "@/domain/locket/link";
import { getLocketLink } from "@/infrastructure/ble/getLocketLink";
import { localFiles } from "@/infrastructure/files/localFiles";

type ControllerValue = SessionState & {
  available: boolean;
  link: LocketLink | null;
  busy: boolean;
  scan(onFound: (found: FoundLocket) => void, onError?: (message: string) => void): () => void;
  connectTo(productId: string): Promise<void>;
  attach(found: FoundLocket): Promise<void>;
  disconnect(): Promise<void>;
  forgetConnected(): Promise<void>;
  startRecording(): Promise<void>;
  stopRecording(): Promise<void>;
  uploadWaiting(): Promise<void>;
  discardWaiting(): Promise<void>;
};

const Ctx = React.createContext<ControllerValue | null>(null);

export function LocketControllerProvider({ children }: { children: React.ReactNode }) {
  const { api, status: signedIn } = useSession();
  const link = React.useMemo(() => getLocketLink(), []);
  const [state, setState] = React.useState<SessionState | null>(null);
  const [busy, setBusy] = React.useState(false);
  const session = React.useMemo(
    () => (link ? new LocketSession({ link, api, files: localFiles, onChange: setState }) : null),
    [link, api],
  );

  // After sign-in (and each app start), upload whatever is still waiting on this phone.
  React.useEffect(() => {
    if (session && signedIn === "signedIn") void session.uploadWaiting();
  }, [session, signedIn]);

  const run = React.useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await work();
    } finally {
      setBusy(false);
    }
  }, []);

  const value = React.useMemo<ControllerValue>(() => {
    const base: SessionState = state ??
      session?.current ?? {
        productId: null,
        connected: false,
        reconnecting: false,
        status: null,
        firmwareVersion: null,
        message: null,
        pending: 0,
        notPaired: false,
        lastRecordingId: null,
      };
    const send = (command: string) => run(async () => link?.send(command));
    return {
      ...base,
      available: session !== null,
      link,
      busy,
      scan: (onFound, onError) => (link ? link.scan(onFound, onError) : () => undefined),
      connectTo: (productId) => run(async () => session?.connectTo(productId)),
      attach: (found) => run(async () => session?.attach(found)),
      disconnect: () => run(async () => session?.disconnect()),
      forgetConnected: () =>
        run(async () => {
          await link?.send(forget()).catch(() => undefined);
          await session?.disconnect();
        }),
      startRecording: () => send(recStart()),
      stopRecording: () => send(recStop()),
      uploadWaiting: () => run(async () => session?.uploadWaiting()),
      discardWaiting: () => run(async () => session?.discardWaiting()),
    };
  }, [state, session, link, busy, run]);

  // A locket is connected and may be sending a meeting of hours: keep the screen on so the page keeps receiving.
  useKeepAwake(value.connected);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLocketController(): ControllerValue {
  const value = React.useContext(Ctx);
  if (value === null) throw new Error("useLocketController must be used inside LocketControllerProvider");
  return value;
}
