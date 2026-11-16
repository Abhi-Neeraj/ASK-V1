// Files for the website build. Parts received from the locket wait in ASK/pending until the server has them
// (and ASK/rejected if it refuses them): those are kept in the browser's own storage (IndexedDB), because the
// locket deletes its copy as soon as this page says it has the part, and a reload must not lose it. The finished
// copies the phone keeps (audio.wav, transcription.txt, summary.txt) are handed to the browser as downloads.

import type { LocalFiles } from "@/infrastructure/files/localFiles";

export type KeyValueStore = {
  get(key: string): Promise<Uint8Array | null>;
  put(key: string, value: Uint8Array): Promise<void>;
  delete(key: string): Promise<void>;
  keys(): Promise<string[]>;
};

const KEPT = ["ASK/pending/", "ASK/rejected/"];
const kept = (path: string) => KEPT.some((dir) => path.startsWith(dir));
const asBytes = (data: Uint8Array | string) => (typeof data === "string" ? new TextEncoder().encode(data) : data);

/** "ASK/<recordingId>/audio.wav" -> "ASK-<first 8 of the id>-audio.wav", so downloads do not overwrite each other. */
function downloadName(path: string): string {
  const parts = path.split("/");
  const file = parts.pop() ?? "file";
  const folder = parts.pop();
  return folder && folder !== "ASK" ? `ASK-${folder.slice(0, 8)}-${file}` : file;
}

export function makeWebFiles(
  store: KeyValueStore,
  download: (name: string, data: Uint8Array | string) => void,
): LocalFiles {
  return {
    write: async (path, data) => {
      if (kept(path)) await store.put(path, asBytes(data));
      else download(downloadName(path), data);
    },
    read: async (path) => (kept(path) ? store.get(path) : null),
    list: async (dir) => {
      const prefix = `${dir.replace(/\/+$/, "")}/`;
      return (await store.keys())
        .filter((key) => key.startsWith(prefix) && !key.slice(prefix.length).includes("/"))
        .map((key) => key.slice(prefix.length))
        .sort();
    },
    remove: async (path) => {
      if (kept(path)) await store.delete(path);
    },
  };
}

export function memoryStore(): KeyValueStore {
  const map = new Map<string, Uint8Array>();
  return {
    get: async (key) => map.get(key) ?? null,
    put: async (key, value) => void map.set(key, value.slice()),
    delete: async (key) => void map.delete(key),
    keys: async () => [...map.keys()],
  };
}

/** The browser's IndexedDB, one object store of path -> bytes. */
export function indexedDbStore(name = "ask-files"): KeyValueStore {
  let opened: Promise<IDBDatabase> | null = null;
  const db = () =>
    (opened ??= new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(name, 1);
      request.onupgradeneeded = () => request.result.createObjectStore("files");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("The browser's storage could not be opened."));
    }));
  const run = async <T>(mode: IDBTransactionMode, work: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    const database = await db();
    return new Promise<T>((resolve, reject) => {
      const transaction = database.transaction("files", mode);
      const request = work(transaction.objectStore("files"));
      transaction.oncomplete = () => resolve(request.result);
      transaction.onerror = () => reject(transaction.error ?? new Error("The browser's storage refused the change."));
      transaction.onabort = () => reject(transaction.error ?? new Error("The browser's storage is full."));
    });
  };
  return {
    get: async (key) => {
      const value = await run<unknown>("readonly", (s) => s.get(key));
      return value instanceof Uint8Array ? value : value instanceof ArrayBuffer ? new Uint8Array(value) : null;
    },
    put: async (key, value) => void (await run("readwrite", (s) => s.put(value.slice(), key))),
    delete: async (key) => void (await run("readwrite", (s) => s.delete(key))),
    keys: async () => (await run<IDBValidKey[]>("readonly", (s) => s.getAllKeys())).map(String),
  };
}
