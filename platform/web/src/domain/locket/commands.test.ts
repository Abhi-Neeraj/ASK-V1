import { describe, expect, it } from "vitest";
import {
  ack,
  forget,
  ota,
  parseInfo,
  parseStatus,
  recStart,
  recStop,
  resend,
  time,
  wifi,
  wifiClear,
} from "@/domain/locket/commands";
import { compareVersions } from "@/domain/locket/version";
import { LOCKET_UUIDS } from "@/domain/locket/uuids";

describe("locket commands", () => {
  it("formats_commands_exactly", () => {
    expect(recStart()).toBe("REC START");
    expect(recStop()).toBe("REC STOP");
    expect(ack("SESSION000000001", 32)).toBe("ACK SESSION000000001 32");
    expect(resend("SESSION000000001", 33)).toBe("RESEND SESSION000000001 33");
    expect(time(1763114400000)).toBe("TIME 1763114400000");
    expect(wifiClear()).toBe("WIFI CLEAR");
    expect(forget()).toBe("FORGET");
  });

  it("hex_encodes_wifi_and_ota_arguments", () => {
    expect(wifi("Home Net", "p@ss wörd")).toBe("WIFI 486f6d65204e6574 704073732077c3b67264");
    expect(ota("1.2.0", 1074604, "ab".repeat(32), "https://ask.example/api/firmware/1.2.0/download?token=1.x")).toBe(
      `OTA 1.2.0 1074604 ${"ab".repeat(32)} ${Buffer.from(
        "https://ask.example/api/firmware/1.2.0/download?token=1.x",
      ).toString("hex")}`,
    );
  });

  it("parses_info_and_status_and_rejects_garbage", () => {
    expect(parseInfo("ASKINFO/1 ASK-7KQ2MX 1.0.0 87 1")).toEqual({
      productId: "ASK-7KQ2MX",
      firmwareVersion: "1.0.0",
      batteryPercent: 87,
      bonded: true,
    });
    expect(parseStatus("ASKSTAT/1 recording 87 SESSION000000001 120 0")).toEqual({
      state: "recording",
      batteryPercent: 87,
      sessionId: "SESSION000000001",
      lastSeq: 120,
      otaPercent: 0,
    });
    expect(parseStatus("ASKSTAT/1 idle 50 - 0 0")?.sessionId).toBeNull();
    expect(parseInfo("hello")).toBeNull();
    expect(parseInfo("ASKINFO/2 ASK-7KQ2MX 1.0.0 87 1")).toBeNull();
    expect(parseStatus("ASKSTAT/1 idle lots - 0 0")).toBeNull();
  });

  it("uuids_match_the_design", () => {
    expect(LOCKET_UUIDS.service).toBe("69bc378a-a795-4f0c-984d-e2cee6c5ea54");
    expect(LOCKET_UUIDS.bondProof).toBe("7bac8620-f67c-4bd2-ac89-b6fcc04ebad7");
    expect(Object.keys(LOCKET_UUIDS)).toHaveLength(7);
  });
});

describe("compareVersions", () => {
  it("compares_versions_numerically", () => {
    expect(compareVersions("1.10.0", "1.9.0")).toBe(1);
    expect(compareVersions("1.2.0", "1.2.0")).toBe(0);
    expect(compareVersions("0.9.9", "1.0.0")).toBe(-1);
  });
});
