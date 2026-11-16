import { createHash, randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";
import { sha256Hex } from "@/domain/crypto/sha256";

const node = (data: Uint8Array) => createHash("sha256").update(data).digest("hex");

describe("sha256Hex", () => {
  it("matches_the_standard_test_vectors", () => {
    expect(sha256Hex(new Uint8Array(0))).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(sha256Hex(new TextEncoder().encode("abc"))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("matches_node_for_every_length_around_block_edges", () => {
    for (const length of [1, 55, 56, 63, 64, 65, 119, 120, 1000]) {
      const data = new Uint8Array(randomBytes(length));
      expect(sha256Hex(data)).toBe(node(data));
    }
  });

  it("handles_a_large_wav", () => {
    const data = new Uint8Array(randomBytes(3_000_000));
    expect(sha256Hex(data)).toBe(node(data));
  });
});
