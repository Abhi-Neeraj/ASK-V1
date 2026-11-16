// The phone's local backup. Paths are relative to the app's documents folder: ASK/<recordingId>/audio.wav,
// transcription.txt, summary.txt, and ASK/pending/ for recordings not uploaded yet.
//
// This is the web version (webFiles.ts): parts waiting to upload are kept in the browser's own storage, so a
// reload does not lose them; the finished files are downloaded through the browser. The phone build uses
// localFiles.native.ts.
import { indexedDbStore, makeWebFiles, memoryStore } from "@/infrastructure/files/webFiles";

export type LocalFiles = {
  write(path: string, data: Uint8Array | string): Promise<void>;
  read(path: string): Promise<Uint8Array | null>;
  list(dir: string): Promise<string[]>;
  remove(path: string): Promise<void>;
};

function download(name: string, data: Uint8Array | string): void {
  if (typeof document === "undefined") return;
  const type = name.endsWith(".txt") ? "text/plain;charset=utf-8" : "application/octet-stream";
  const blob = new Blob([data as BlobPart], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const localFiles: LocalFiles = makeWebFiles(
  typeof indexedDB === "undefined" ? memoryStore() : indexedDbStore(),
  download,
);

export const KEEPS_FILES = false;
