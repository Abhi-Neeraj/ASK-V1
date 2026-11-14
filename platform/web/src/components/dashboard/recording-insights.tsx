"use client";

import { User } from "lucide-react";
import { SectionTitle } from "@/components/dashboard/page-header";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { Analysis, AnalysisItem, RecordingVoice, SummaryLine } from "@/lib/api/types";
import { formatDuration, formatPitch } from "@/lib/format";

/** Wording and colour for each key activity. The set is closed: it is the same set the memory registry allows. */
const ACTIVITY_TONE: Record<string, BadgeTone> = {
  DECISION: "cerulean",
  ACTION: "cerulean",
  COMMITMENT: "cerulean",
  QUESTION: "neutral",
  REQUIREMENT: "neutral",
  ISSUE: "warning",
  RISK: "warning",
  APPROVAL: "success",
  REJECTION: "danger",
  NOTE: "neutral",
};

function toneOf(activity: string): BadgeTone {
  return ACTIVITY_TONE[activity] ?? "neutral";
}

/** How the matching was done, in words that name no product. */
export function engineNote(engine: Analysis["engine"]): string {
  return engine === "semantic"
    ? "Sentences were matched to activities by meaning and by key phrases."
    : "Sentences were matched to activities by key phrases and shared words.";
}

function matchedLabel(voice: RecordingVoice): { text: string; tone: BadgeTone } {
  if (voice.name && voice.matchedBy === "signature") return { text: "Matched by voice signature", tone: "cerulean" };
  if (voice.name) return { text: "Matched by voice print", tone: "cerulean" };
  return { text: "Not matched to anyone", tone: "neutral" };
}

function Pitch({ label, hz }: { label: string; hz: number }) {
  return (
    <div>
      <dt className="caption text-fg-subtle">{label}</dt>
      <dd className="mt-1.5 font-mono text-sm tabular-nums text-fg">{formatPitch(hz)}</dd>
    </div>
  );
}

/** Each speaker in the recording: how much they spoke, their pitch (base, low, high) and how they were named. */
export function RecordingVoices({ analysis }: { analysis: Analysis }) {
  if (analysis.voices.length === 0) return null;
  return (
    <section aria-labelledby="voices-title" className="mb-8">
      <SectionTitle>
        <span id="voices-title">Speakers</span>
      </SectionTitle>
      <Card className="overflow-hidden">
        <ul className="divide-y divide-line">
          {analysis.voices.map((voice) => {
            const matched = matchedLabel(voice);
            const percent = Math.round(voice.share * 100);
            return (
              <li key={voice.label} className="grid gap-4 px-5 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="grid size-8 shrink-0 place-items-center rounded-full border border-line-strong bg-white/[0.03] text-fg-muted">
                      <User className="size-4" aria-hidden="true" />
                    </span>
                    <span className={voice.name ? "text-sm font-medium text-fg" : "text-sm font-medium text-fg-muted"}>
                      {voice.title}
                    </span>
                    <span className="font-mono text-[0.6875rem] text-fg-subtle">{voice.label}</span>
                    <Badge tone={matched.tone}>{matched.text}</Badge>
                    {voice.matchScore !== null ? (
                      <Badge
                        tone="neutral"
                        title="How alike the voice was to the registered voice. A similarity, not the probability that the name is right."
                      >
                        {Math.round(voice.matchScore * 100)}% alike
                      </Badge>
                    ) : null}
                  </div>
                  <div className="mt-3 flex items-center gap-3">
                    <span
                      role="meter"
                      aria-label={`${voice.title}'s share of the speech`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={percent}
                      className="h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-white/10"
                    >
                      <span className="block h-full bg-cerulean" style={{ width: `${percent}%` }} />
                    </span>
                    <span className="whitespace-nowrap text-xs text-fg-muted">
                      {percent}% of the speech · {formatDuration(voice.talkMs)} · {voice.turns} {voice.turns === 1 ? "turn" : "turns"}
                    </span>
                  </div>
                </div>

                {voice.pitch ? (
                  <dl className="grid grid-cols-3 gap-x-8" aria-label={`Pitch of ${voice.title}`}>
                    <Pitch label="Base pitch" hz={voice.pitch.baseHz} />
                    <Pitch label="Low pitch" hz={voice.pitch.lowHz} />
                    <Pitch label="High pitch" hz={voice.pitch.highHz} />
                  </dl>
                ) : (
                  <p className="text-xs text-fg-subtle">Too little clear speech to measure pitch.</p>
                )}
              </li>
            );
          })}
        </ul>
      </Card>
      <p className="mt-3 text-xs leading-relaxed text-fg-subtle">
        Pitch is how high or low a voice sits in this recording: base is where it sits most of the time, low and high are
        the bottom and top of its range. Pitch alone never names anyone, and a match by voice is not proof of who spoke.
      </p>
    </section>
  );
}

function LineMeta({ line }: { line: SummaryLine }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-fg-muted">
      {line.owner ? (
        <span>
          <span className="caption text-fg-subtle">Owner </span>
          {line.owner}
        </span>
      ) : null}
      {line.due ? (
        <span>
          <span className="caption text-fg-subtle">Due </span>
          {line.due}
        </span>
      ) : null}
      <span>
        <span className="caption text-fg-subtle">Said by </span>
        {line.speaker} at {formatDuration(line.atMs)}
      </span>
    </div>
  );
}

/** What was decided and asked, and who has to do what: the summary, built by fixed rules from the transcript. */
export function RecordingSummary({ analysis }: { analysis: Analysis }) {
  const { summary } = analysis;
  const [headline, ...rest] = summary.narrative;
  return (
    <section aria-labelledby="summary-title" className="mb-8">
      <SectionTitle>
        <span id="summary-title">Summary</span>
      </SectionTitle>
      <Card className="overflow-hidden">
        <div className="space-y-2 px-5 py-5 sm:px-6">
          <p className="text-[0.9375rem] leading-relaxed text-fg">{headline}</p>
          {rest.map((paragraph, index) => (
            <p key={index} className="text-sm leading-relaxed text-fg-muted">
              {paragraph}
            </p>
          ))}
        </div>

        {summary.sections.map((section) => (
          <div key={section.key} className="border-t border-line">
            <h3 className="caption px-5 pt-5 text-fg sm:px-6">
              {section.title} <span className="text-fg-subtle">({section.total})</span>
            </h3>
            <ul className="divide-y divide-line">
              {section.lines.map((line) => (
                <li key={`${line.segmentIndex}-${line.atMs}-${line.text}`} className="px-5 py-4 sm:px-6">
                  <div className="flex flex-wrap items-start gap-3">
                    <Badge tone={toneOf(line.activity)}>{line.activityLabel}</Badge>
                    {line.open === true ? <Badge tone="rose">Unanswered</Badge> : null}
                    <p className="min-w-0 flex-1 text-[0.9375rem] leading-relaxed text-fg">{line.text}</p>
                  </div>
                  <LineMeta line={line} />
                </li>
              ))}
            </ul>
            {section.total > section.lines.length ? (
              <p className="px-5 pb-4 text-xs text-fg-subtle sm:px-6">
                {section.total - section.lines.length} more in the sentence-by-sentence list below.
              </p>
            ) : null}
          </div>
        ))}

        <details className="group border-t border-line">
          <summary className="cursor-pointer px-5 py-4 text-sm text-fg-muted hover:text-fg sm:px-6">
            Sentence by sentence ({analysis.items.length})
          </summary>
          <ol className="divide-y divide-line border-t border-line" aria-label="Every sentence, analysed">
            {analysis.items.map((item) => (
              <SentenceRow
                key={`${item.segmentIndex}-${item.startMs}-${item.what}`}
                item={item}
                topic={topicStartingAt(item, analysis)}
              />
            ))}
          </ol>
        </details>

        <p className="border-t border-line px-5 py-4 text-xs leading-relaxed text-fg-subtle sm:px-6">
          Built from the transcript by fixed rules and templates, with no generative AI: the same transcript always gives
          the same summary. {engineNote(analysis.engine)} Check it against the transcript before relying on it
          {summary.truncated ? ". This recording was long, so only its first part was analysed." : "."}
        </p>
      </Card>
    </section>
  );
}

/** The topic that starts at this sentence, where contexts were worked out and this sentence opens one. */
export function topicStartingAt(item: AnalysisItem, analysis: Analysis): { number: number; keywords: string[] } | null {
  if (item.context.newContext !== true || item.context.group == null) return null;
  const context = analysis.contexts?.find((c) => c.group === item.context.group);
  return { number: item.context.group + 1, keywords: context?.keywords ?? [] };
}

function SentenceRow({ item, topic }: { item: AnalysisItem; topic: { number: number; keywords: string[] } | null }) {
  return (
    <>
      {topic ? (
        <li aria-label={`New topic ${topic.number}`} className="bg-white/[0.02] px-5 py-2 sm:px-6">
          <span className="caption text-cerulean-glow">New topic {topic.number}</span>
          {topic.keywords.length > 0 ? <span className="ml-3 text-xs text-fg-muted">{topic.keywords.join(", ")}</span> : null}
        </li>
      ) : null}
      <SentenceItem item={item} />
    </>
  );
}

function SentenceItem({ item }: { item: AnalysisItem }) {
  return (
    <li className="grid gap-x-6 gap-y-2 px-5 py-4 sm:grid-cols-[6rem_1fr] sm:px-6">
      <p className="font-mono text-xs leading-6 text-fg-subtle">{formatDuration(item.startMs)}</p>
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-fg">{item.speaker}</span>
          <Badge tone={toneOf(item.activity)}>{item.activityLabel}</Badge>
          {item.open === true ? <Badge tone="rose">Unanswered</Badge> : null}
          {item.dated ? <Badge tone="cerulean">Has a deadline</Badge> : null}
        </div>
        <p className="text-sm leading-relaxed text-fg">{item.what}</p>
        <dl className="grid gap-x-6 gap-y-1.5 text-xs text-fg-muted sm:grid-cols-2">
          {item.task ? (
            <div>
              <dt className="caption text-fg-subtle">Task</dt>
              <dd className="mt-0.5">{item.task}</dd>
            </div>
          ) : null}
          {item.owner ? (
            <div>
              <dt className="caption text-fg-subtle">Owner</dt>
              <dd className="mt-0.5">{item.owner}</dd>
            </div>
          ) : null}
          {item.due ? (
            <div>
              <dt className="caption text-fg-subtle">Due</dt>
              <dd className="mt-0.5">{item.due}</dd>
            </div>
          ) : null}
          {item.verb ? (
            <div>
              <dt className="caption text-fg-subtle">Action verb</dt>
              <dd className="mt-0.5">{item.verb}</dd>
            </div>
          ) : null}
          {item.keywords && item.keywords.length > 0 ? (
            <div>
              <dt className="caption text-fg-subtle">Main words</dt>
              <dd className="mt-0.5">{item.keywords.join(", ")}</dd>
            </div>
          ) : null}
          {item.context.previous ? (
            <div className="sm:col-span-2">
              <dt className="caption text-fg-subtle">Context</dt>
              <dd className="mt-0.5">
                After {item.context.previousSpeaker ?? "an earlier speaker"}: &ldquo;{item.context.previous}&rdquo;
                {item.context.topic.length > 0 ? ` · about ${item.context.topic.join(", ")}` : ""}
              </dd>
            </div>
          ) : null}
        </dl>
      </div>
    </li>
  );
}
