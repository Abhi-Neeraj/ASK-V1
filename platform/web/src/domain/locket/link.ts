// What the app needs from a Bluetooth connection to one locket. The phone build implements it with
// react-native-ble-plx (src/infrastructure/ble), Chrome and Edge with Web Bluetooth (webBluetoothLink.ts); tests use
// fakes. Keeping the app logic behind this interface is what lets pairing and capture be tested without a
// phone or a locket.

export type FoundLocket = { id: string; name: string; rssi: number };

export interface LocketLink {
  /** Report lockets advertising the ASK service. Returns a function that stops scanning. */
  scan(onFound: (locket: FoundLocket) => void, onError?: (message: string) => void): () => void;
  connect(id: string): Promise<void>;
  disconnect(): Promise<void>;
  /** Called when the connection drops (locket out of range, turned off). Returns a function that stops listening. */
  onDisconnected(listener: () => void): () => void;
  /** The INFO text, readable without pairing. */
  readInfo(): Promise<string>;
  /** 16 fresh bytes from PAIR_NONCE, readable without pairing. */
  readNonce(): Promise<Uint8Array>;
  /** Start bonding: the operating system asks for the passkey. Rejects if it was wrong or cancelled. */
  bond(): Promise<void>;
  /** The BOND_PROOF value (64 hex), readable only over the bonded, encrypted link. */
  readProof(): Promise<string>;
  /** Write one CONTROL command. */
  send(command: string): Promise<void>;
  onStatus(listener: (text: string) => void): () => void;
  onAudio(listener: (frame: Uint8Array) => void): () => void;
}

export const toHex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
