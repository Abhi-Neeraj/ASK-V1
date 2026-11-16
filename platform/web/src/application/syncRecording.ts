// What happens to audio after the locket hands it over:
// 1. savePart: the part is written to ASK/pending on the phone first. Only then may the locket delete it.
// 2. retryPending: every waiting part is uploaded, with the meeting's session ID as X-Session-Ref and the part
//    number as X-Sequence; its upload ID is "<session>-<part>", so uploading twice never makes two recordings.
//    An uploaded part moves to ASK/<recordingId>/audio.wav. Offline, signed out or a busy server: it waits.
//    A file the server refuses for good (too long, not audio...) moves to ASK/rejected so it cannot block the
//    parts behind it.
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

export async function pendingCount(files: LocalFiles): Promise<number> {
  return (await files.list(PENDING)).filter((name) => name.endsWith(".wav")).length;
}

/** Errors that mean "try again later" rather than "this file will never be accepted". */
function retryLater(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.status === 0 || error.status === 401 || error.status === 408 || error.status === 429 || error.status >= 500;
}

export async function retryPending({
  api,
  files,
}: {
  api: UploadApi;
  files: LocalFiles;
}): Promise<{ uploaded: string[]; rejected: string[]; reasons: string[] }> {
  const uploaded: string[] = [];
  const rejected: string[] = [];
  const reasons: string[] = [];
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
  return { uploaded, rejected, reasons };
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
