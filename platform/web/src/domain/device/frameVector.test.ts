// A frame the locket firmware must produce byte for byte (platform/firmware/tests/test_ble_core.cpp has the
// same hex). If either side changes the frame format, one of the two tests fails.
import { describe, expect, it } from "vitest";
import { decodeFrame, encodeFrame } from "@/domain/device/framing";

const SHARED =
  "41530103002a53455353494f4e303030303030303031000000050000019a81ceb9000000000301630d2c010203";

describe("frame format shared with the firmware", () => {
  it("encodes_the_shared_vector", () => {
    const bytes = encodeFrame({
      type: "AUDIO",
      flags: 0,
      sessionId: "SESSION000000001",
      sequence: 5,
      timestampMs: 1763114400000,
      payload: new Uint8Array([1, 2, 3]),
      crc32: 0,
    });
    expect(Buffer.from(bytes).toString("hex")).toBe(SHARED);
  });

  it("decodes_the_shared_vector", () => {
    const decoded = decodeFrame(new Uint8Array(Buffer.from(SHARED, "hex")));
    expect(decoded.ok).toBe(true);
    if (decoded.ok) expect(decoded.frame).toMatchObject({ type: "AUDIO", sequence: 5, sessionId: "SESSION000000001" });
  });
});
