import type { Analysis, RecordingDetail, TranscriptSegment } from "@/lib/api/types";
import { formatDuration, formatPitch, formatTimestamp } from "@/lib/format";

export const UNKNOWN_SPEAKER = "Unknown speaker";

export function speakerName(segment: TranscriptSegment): string {
  return segment.person?.name ?? UNKNOWN_SPEAKER;
}

/** Stamped on every export of placeholder text, so a saved copy cannot pass for a real transcript. */
export const PLACEHOLDER_NOTICE =
  "NOTICE: this transcript is placeholder text from a development stand-in. It is not what was said.";

/** The summary and the speakers' pitch as text. How the summary was built is said in the file itself. */
export function analysisToText(analysis: Analysis): string {
  const out: string[] = ["SUMMARY (built from the transcript by fixed rules, with no generative AI)", ""];
  out.push(...analysis.summary.narrative);
  for (const section of analysis.summary.sections) {
    out.push("", `${section.title} (${section.total})`);
    for (const line of section.lines) {
      const parts = [`- [${formatDuration(line.atMs)}] ${line.text}`];
      if (line.owner) parts.push(`owner: ${line.owner}`);
      if (line.due) parts.push(`due: ${line.due}`);
      if (line.open === true) parts.push("unanswered");
      out.push(parts.join("  "));
    }
  }
  out.push("", "SPEAKERS");
  for (const voice of analysis.voices) {
    const pitch = voice.pitch
      ? `base ${formatPitch(voice.pitch.baseHz)}, low ${formatPitch(voice.pitch.lowHz)}, high ${formatPitch(voice.pitch.highHz)}`
      : "pitch not measured";
    out.push(`- ${voice.title}: ${Math.round(voice.share * 100)}% of the speech, ${pitch}`);
  }
  return out.join("\n");
}

/** One line per segment: "00:00:02.100–00:00:05.800 | Name | text", then the summary when there is one. */
export function transcriptToText(detail: RecordingDetail): string {
  const segments = detail.transcript?.segments ?? [];
  const lines = segments.map(
    (s) => `${formatTimestamp(s.startMs)}–${formatTimestamp(s.endMs)} | ${speakerName(s)} | ${s.text}`,
  );
  const body = (detail.transcript?.placeholder ? [PLACEHOLDER_NOTICE, "", ...lines] : lines).join("\n");
  return detail.analysis ? `${body}\n\n${analysisToText(detail.analysis)}` : body;
}

/**
 * The model that produced a transcript is deliberately not written out: the file says what matters
 * (is it real, were speakers separated) in plain terms, and does not name third-party products.
 */
export function transcriptToJson(detail: RecordingDetail): string {
  const placeholder = detail.transcript?.placeholder ?? false;
  return JSON.stringify(
    {
      recordingId: detail.id,
      device: detail.deviceName,
      startedAt: detail.startedAt,
      durationMs: detail.durationMs,
      placeholder,
      notice: placeholder ? PLACEHOLDER_NOTICE : null,
      language: detail.transcript?.language ?? null,
      speakersSeparated: Boolean(detail.transcript?.diarizationModel),
      speakerSeparationExperimental: detail.transcript?.separationExperimental ?? false,
      segments: (detail.transcript?.segments ?? []).map((s) => ({
        startMs: s.startMs,
        endMs: s.endMs,
        speaker: speakerName(s),
        speakerLabel: s.speakerLabel,
        confidence: s.confidence,
        text: s.text,
        corrected: s.corrected,
      })),
      // Only present when a summary was made. Built by fixed rules, with no generative AI.
      ...(detail.analysis
        ? {
            summary: {
              builtBy: "fixed rules and templates, no generative AI",
              narrative: detail.analysis.summary.narrative,
              sections: detail.analysis.summary.sections.map((section) => ({
                title: section.title,
                total: section.total,
                lines: section.lines.map((line) => ({
                  activity: line.activityLabel,
                  text: line.text,
                  owner: line.owner,
                  due: line.due,
                  hasDeadline: line.dated === true,
                  speaker: line.speaker,
                  atMs: line.atMs,
                  unanswered: line.open === true,
                })),
              })),
            },
            voices: detail.analysis.voices.map((voice) => ({
              speaker: voice.title,
              speakerLabel: voice.label,
              talkMs: voice.talkMs,
              share: voice.share,
              pitch: voice.pitch,
            })),
          }
        : {}),
    },
    null,
    2,
  );
}

/** Saves text the browser already holds. Nothing is sent anywhere. */
export function downloadTextFile(filename: string, mime: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: `${mime};charset=utf-8` }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
