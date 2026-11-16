import { describe, expect, it } from "vitest";
import { type KeyValueStore, makeWebFiles, memoryStore } from "@/infrastructure/files/webFiles";

function setup(store: KeyValueStore = memoryStore()) {
  const downloads: Array<{ name: string; size: number }> = [];
  const files = makeWebFiles(store, (name, data) => downloads.push({ name, size: typeof data === "string" ? data.length : data.length }));
  return { files, downloads, store };
}

describe("web files", () => {
  it("keeps parts waiting to upload, so a reload does not lose them, and reads them back", async () => {
    const store = memoryStore();
    const first = setup(store);
    await first.files.write("ASK/pending/S1-0.wav", new Uint8Array([1, 2, 3]));
    await first.files.write("ASK/pending/S1-0.json", '{"part":0}');
    const reloaded = setup(store); // a new page load sees the same store
    expect(await reloaded.files.list("ASK/pending")).toEqual(["S1-0.json", "S1-0.wav"]);
    expect(Array.from((await reloaded.files.read("ASK/pending/S1-0.wav"))!)).toEqual([1, 2, 3]);
    expect(new TextDecoder().decode((await reloaded.files.read("ASK/pending/S1-0.json"))!)).toBe('{"part":0}');
    expect(first.downloads).toEqual([]);
  });

  it("removes a part once it is uploaded", async () => {
    const { files } = setup();
    await files.write("ASK/pending/S1-0.wav", new Uint8Array([1]));
    await files.remove("ASK/pending/S1-0.wav");
    expect(await files.list("ASK/pending")).toEqual([]);
    expect(await files.read("ASK/pending/S1-0.wav")).toBeNull();
  });

  it("keeps parts the server refused apart from the waiting ones", async () => {
    const { files } = setup();
    await files.write("ASK/rejected/S1-0.wav", new Uint8Array([1]));
    expect(await files.list("ASK/pending")).toEqual([]);
    expect(await files.list("ASK/rejected")).toEqual(["S1-0.wav"]);
  });

  it("hands the finished copies (audio, transcription, summary) to the browser as downloads named after the recording", async () => {
    const { files, downloads } = setup();
    await files.write("ASK/0d9c41f2-7b1e-4c55-9a0e-2f6b8c1d3e4f/transcription.txt", "Hello");
    await files.write("ASK/0d9c41f2-7b1e-4c55-9a0e-2f6b8c1d3e4f/audio.wav", new Uint8Array(10));
    expect(downloads).toEqual([
      { name: "ASK-0d9c41f2-transcription.txt", size: 5 },
      { name: "ASK-0d9c41f2-audio.wav", size: 10 },
    ]);
    expect(await files.read("ASK/0d9c41f2-7b1e-4c55-9a0e-2f6b8c1d3e4f/audio.wav")).toBeNull();
  });
});
