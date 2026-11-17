import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { clearCache, readCache, writeCache } from "@/application/cache";

function fakeSessionStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => data.get(key) ?? null,
    key: (index: number) => [...data.keys()][index] ?? null,
    removeItem: (key: string) => void data.delete(key),
    setItem: (key: string, value: string) => void data.set(key, value),
  };
}

describe("the screen cache", () => {
  beforeEach(() => {
    (globalThis as { sessionStorage?: Storage }).sessionStorage = fakeSessionStorage();
    clearCache();
  });
  afterEach(() => {
    delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
    clearCache();
  });

  it("returns_the_last_answer_written", () => {
    expect(readCache("u1:lockets")).toBeUndefined();
    writeCache("u1:lockets", [{ productId: "ASK-1" }]);
    expect(readCache("u1:lockets")).toEqual([{ productId: "ASK-1" }]);
  });

  it("survives_a_reload_of_the_tab_through_session_storage", () => {
    writeCache("u1:recent", [1, 2, 3]);
    // A reload empties the page's memory but not the tab's session storage: only storage is left.
    const kept = (globalThis as { sessionStorage: Storage }).sessionStorage;
    clearCache("never-matches-anything:");
    expect(kept.length).toBe(1);
    expect(readCache("u1:recent")).toEqual([1, 2, 3]);
  });

  it("keeps_what_people_said_in_memory_only", () => {
    writeCache("u1:recording:r1", { transcript: "private words" }, false);
    expect(readCache("u1:recording:r1")).toEqual({ transcript: "private words" });
    expect((globalThis as { sessionStorage: Storage }).sessionStorage.length).toBe(0);
  });

  it("is_emptied_at_sign_out_or_by_prefix", () => {
    writeCache("u1:lockets", [1]);
    writeCache("u1:recent", [2]);
    writeCache("u2:lockets", [3]);
    clearCache("u1:");
    expect(readCache("u1:lockets")).toBeUndefined();
    expect(readCache("u1:recent")).toBeUndefined();
    expect(readCache("u2:lockets")).toEqual([3]);
    clearCache();
    expect(readCache("u2:lockets")).toBeUndefined();
    expect((globalThis as { sessionStorage: Storage }).sessionStorage.length).toBe(0);
  });

  it("works_without_any_storage", () => {
    delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
    writeCache("u1:x", { a: 1 });
    expect(readCache("u1:x")).toEqual({ a: 1 });
    expect(() => clearCache()).not.toThrow();
  });
});
