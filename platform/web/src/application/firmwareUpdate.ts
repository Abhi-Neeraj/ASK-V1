// Firmware updates over Wi-Fi (spec 5.5): the phone offers an update when the server has a newer build than
// the locket runs, gives the locket the Wi-Fi details (once) and then the download link, size and checksum.

import { ota, wifi } from "@/domain/locket/commands";
import type { LocketLink } from "@/domain/locket/link";
import { compareVersions } from "@/domain/locket/version";
import { ApiError } from "@/infrastructure/api/client";
import type { FirmwareLatest } from "@/infrastructure/api/types";

export async function firmwareOffer(
  api: { latestFirmware(): Promise<FirmwareLatest> },
  locket: { firmwareVersion: string | null },
): Promise<FirmwareLatest | null> {
  try {
    const latest = await api.latestFirmware();
    return compareVersions(latest.version, locket.firmwareVersion ?? "0.0.0") > 0 ? latest : null;
  } catch (error) {
    if (error instanceof ApiError && error.code === "no_firmware") return null;
    throw error;
  }
}

export async function startFirmwareUpdate({
  link,
  latest,
  wifi: network,
}: {
  link: LocketLink;
  latest: FirmwareLatest;
  wifi?: { ssid: string; password: string };
}): Promise<void> {
  if (network) await link.send(wifi(network.ssid, network.password));
  await link.send(ota(latest.version, latest.sizeBytes, latest.sha256, latest.downloadUrl));
}
