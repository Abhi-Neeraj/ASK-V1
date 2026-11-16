// Text commands the phone writes to the locket's CONTROL characteristic, and the two text values the locket
// shows (INFO and STATUS). Exactly as spec 5.3. Free text (Wi-Fi name, password, URL) goes as UTF-8 hex so a
// space or odd character can never split a command.

const hex = (text: string) =>
  Array.from(new TextEncoder().encode(text), (b) => b.toString(16).padStart(2, "0")).join("");

export const recStart = () => "REC START";
export const recStop = () => "REC STOP";
export const ack = (sessionId: string, seq: number) => `ACK ${sessionId} ${seq}`;
export const resend = (sessionId: string, fromSeq: number) => `RESEND ${sessionId} ${fromSeq}`;
export const time = (unixMs: number) => `TIME ${Math.trunc(unixMs)}`;
export const wifi = (ssid: string, password: string) => `WIFI ${hex(ssid)} ${hex(password)}`;
export const wifiClear = () => "WIFI CLEAR";
export const ota = (version: string, sizeBytes: number, sha256: string, url: string) =>
  `OTA ${version} ${sizeBytes} ${sha256} ${hex(url)}`;
export const forget = () => "FORGET";

export type LocketInfo = { productId: string; firmwareVersion: string; batteryPercent: number; bonded: boolean };

export type LocketStatus = {
  state: string;
  batteryPercent: number;
  sessionId: string | null;
  lastSeq: number;
  otaPercent: number;
};

const whole = (text: string) => (/^\d+$/.test(text) ? Number(text) : null);

export function parseInfo(text: string): LocketInfo | null {
  const parts = text.trim().split(/\s+/);
  if (parts.length !== 5 || parts[0] !== "ASKINFO/1") return null;
  const battery = whole(parts[3]);
  if (!/^ASK-[A-Z0-9]{6}$/.test(parts[1]) || battery === null || !["0", "1"].includes(parts[4])) return null;
  return { productId: parts[1], firmwareVersion: parts[2], batteryPercent: battery, bonded: parts[4] === "1" };
}

export function parseStatus(text: string): LocketStatus | null {
  const parts = text.trim().split(/\s+/);
  if (parts.length !== 6 || parts[0] !== "ASKSTAT/1") return null;
  const [battery, lastSeq, ota] = [whole(parts[2]), whole(parts[4]), whole(parts[5])];
  if (battery === null || lastSeq === null || ota === null) return null;
  return {
    state: parts[1],
    batteryPercent: battery,
    sessionId: parts[3] === "-" ? null : parts[3],
    lastSeq,
    otaPercent: ota,
  };
}
