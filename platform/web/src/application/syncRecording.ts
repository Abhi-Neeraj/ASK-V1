// What happens to audio after the locket hands it over:
// 1. savePart: the part is written to ASK/pending on the phone first. Only then may the locket delete it.
// 2. retryPending: every waiting part is uploaded, with the meeting's session ID as X-Session-Ref and the part
//    number as X-Sequence; its upload ID is "<session>-<part>", so uploading twice never makes two recordings.
//    An uploaded part moves to ASK/<recordingId>/audio.wav. Offline, signed out or a busy server: it waits.
//    A file the server refuses for good (too long, not audio...) moves to ASK/rejected so it cannot block the
//    parts behind it. A 404 (this account is not paired with that locket) is not a refusal of the audio: the part
//    stays waiting, and parts an older version set aside for that reason are put back on the waiting list.
// 3. saveTextFiles: transcription.txt and summary.txt once the server has them.

import type { CapturedPart } from "@/application/captureRecording";
import { ApiError } from "@/infrastructure/api/client";
import type { UploadAck } from "@/infrastructure/api/types";
import type { LocalFiles } from "@/infrastructure/files/localFiles";

type UploadApi = {
  uploadRecording(
    productId: string,
    clientId: string,
    wav: Uint8Array,
    recordedAt: Date,
    group?: { sessionRef: string; sequence: number },
  ): Promise<UploadAck>;
};

type PendingMeta = { productId: string; sessionId: string; part: number; startedAt: string };

const PENDING = "ASK/pending";
const REJECTED = "ASK/rejected";

export async function savePart({
  files,
  productId,
  part,
}: {
  files: LocalFiles;
  productId: string;
  part: CapturedPart;
}): Promise<void> {
  const key = `${part.sessionId}-${part.part}`;
  const meta: PendingMeta = {
    productId,
    sessionId: part.sessionId,
    part: part.part,
    startedAt: part.startedAt.toISOString(),
  };
  await files.write(`${PENDING}/${key}.wav`, part.wav);
  await files.write(`${PENDING}/${key}.json`, JSON.stringify(meta));
}

/**
 * Throw away every recording waiting on this phone, and every one set aside: for a clip that can never upload (made
 * when the locket had another ID, or one this account is not paired with), so it stops being offered. Returns how
 * many recordings were removed.
 */
export async function discardPending(files: LocalFiles): Promise<number> {
  let removed = 0;
  for (const directory of [PENDING, REJECTED]) {
    for (const name of await files.list(directory)) {
      await files.remove(`${directory}/${name}`);
      if (name.endsWith(".wav")) removed += 1;
    }
  }
  return removed;
}

export async function pendingCount(files: LocalFiles): Promise<number> {
  return (await files.list(PENDING)).filter((name) => name.endsWith(".wav")).length;
}

/** Errors that mean "try again later" rather than "this file will never be accepted". */
function retryLater(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 0 || error.status === 401 || error.status === 408 || error.status === 429 || error.status >= 500;
}

/**
 * A 404 on an upload means this account is not on that locket (it was never paired here, or it was removed). The
 * audio itself is fine, so it stays waiting on the phone until the account is on the locket: it is never set aside.
 */
function notOnLocket(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/**
 * Parts set aside for a reason that was not about the audio go back to the waiting list, so nothing is lost: a 404
 * (the account was not paired with the locket yet) and "body_too_large" (the server used to refuse any upload over
 * 1 MB, about 30 seconds of audio, which was a server mistake, since fixed). Parts refused for any other reason stay
 * set aside.
 */
async function requeueUnpaired(files: LocalFiles): Promise<void> {
  for (const name of (await files.list(REJECTED)).filter((n) => n.endsWith(".json"))) {
    const key = name.slice(0, -5);
    const metaBytes = await files.read(`${REJECTED}/${key}.json`);
    const wav = await files.read(`${REJECTED}/${key}.wav`);
    if (!metaBytes || !wav) continue;
    const { reason, ...meta } = JSON.parse(new TextDecoder().decode(metaBytes)) as PendingMeta & { reason?: string };
    if (reason !== "not_found" && reason !== "body_too_large") continue;
    await files.write(`${PENDING}/${key}.wav`, wav);
    await files.write(`${PENDING}/${key}.json`, JSON.stringify(meta));
    await files.remove(`${REJECTED}/${key}.wav`);
    await files.remove(`${REJECTED}/${key}.json`);
  }
}

export async function retryPending({
  api,
  files,
}: {
  api: UploadApi;
  files: LocalFiles;
}): Promise<{ uploaded: string[]; rejected: string[]; reasons: string[]; waiting: string[] }> {
  const uploaded: string[] = [];
  const rejected: string[] = [];
  const reasons: string[] = [];
  const waiting: string[] = []; // product IDs this account is not on: their recordings stay on the phone
  await requeueUnpaired(files);
  const names = (await files.list(PENDING)).filter((name) => name.endsWith(".wav")).sort();
  for (const name of names) {
    const key = name.slice(0, -4);
    const wav = await files.read(`${PENDING}/${key}.wav`);
    const metaBytes = await files.read(`${PENDING}/${key}.json`);
    if (!wav || !metaBytes) continue;
    const meta = JSON.parse(new TextDecoder().decode(metaBytes)) as PendingMeta;
    try {
      const ack = await api.uploadRecording(meta.productId, key, wav, new Date(meta.startedAt), {
        sessionRef: meta.sessionId,
        sequence: meta.part,
      });
      await files.write(`ASK/${ack.recordingId}/audio.wav`, wav);
      await files.remove(`${PENDING}/${key}.wav`);
      await files.remove(`${PENDING}/${key}.json`);
      uploaded.push(ack.recordingId);
    } catch (error) {
      if (notOnLocket(error)) {
        // Not paired with this locket: keep the part and go on, other lockets' parts may still upload.
        if (!waiting.includes(meta.productId)) waiting.push(meta.productId);
        continue;
      }
      if (retryLater(error)) break; // offline, signed out or busy: the rest waits too
      await files.write(`${REJECTED}/${key}.wav`, wav);
      await files.write(`${REJECTED}/${key}.json`, JSON.stringify({ ...meta, reason: (error as ApiError).code }));
      await files.remove(`${PENDING}/${key}.wav`);
      await files.remove(`${PENDING}/${key}.json`);
      rejected.push(key);
      // Say why: the server's code and its words, so a refusal can be told apart (a bad time, silence, a wrong format).
      const refusal = error as ApiError;
      reasons.push(`${refusal.code}: ${refusal.message}`);
    }
  }
  return { uploaded, rejected, reasons, waiting };
}

type TextApi = { textFile(id: string, which: "transcription" | "summary"): Promise<string> };

/** Save transcription.txt and summary.txt for a recording. A missing summary is not an error. */
export async function saveTextFiles({
  api,
  files,
  recordingId,
}: {
  api: TextApi;
  files: LocalFiles;
  recordingId: string;
}): Promise<{ transcript: boolean; summary: boolean | "not_ready" }> {
  const notReady = (error: unknown) => error instanceof ApiError && error.status === 404;
  try {
    await files.write(`ASK/${recordingId}/transcription.txt`, await api.textFile(recordingId, "transcription"));
  } catch (error) {
    if (!notReady(error)) throw error;
    return { transcript: false, summary: "not_ready" };
  }
  try {
    await files.write(`ASK/${recordingId}/summary.txt`, await api.textFile(recordingId, "summary"));
    return { transcript: true, summary: true };
  } catch (error) {
    if (!notReady(error)) throw error;
    return { transcript: true, summary: "not_ready" };
  }
}
