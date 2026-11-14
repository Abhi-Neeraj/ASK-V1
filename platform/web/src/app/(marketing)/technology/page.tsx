import { ArrowRight, Cpu, Database, HardDrive, KeyRound, Laptop, LayoutDashboard, ScrollText, ShieldCheck, Timer, Wifi } from "lucide-react";
import type { Metadata } from "next";
import { Fragment } from "react";
import { PipelineStates } from "@/components/brand/pipeline-states";
import { Callout, FeatureGrid, SplitSection } from "@/components/marketing/blocks";
import { FinalCta } from "@/components/marketing/home/final-cta";
import { Neon, PageHero } from "@/components/marketing/section";

export const metadata: Metadata = {
  title: "Technology",
  description:
    "How ASK is built: device uploads, a durable processing queue, local AI models, tenant isolation and the security model.",
};

const NODES = [
  { icon: HardDrive, title: "Locket", body: "Records WAV clips." },
  { icon: Wifi, title: "Wi-Fi", body: "Authenticated uploads." },
  { icon: Database, title: "API", body: "Validates and stores." },
  { icon: Cpu, title: "Worker", body: "Transcribes and matches." },
  { icon: LayoutDashboard, title: "Dashboard", body: "Search and review." },
] as const;

export default function TechnologyPage() {
  return (
    <>
      <PageHero
        eyebrow="Technology"
        title={
          <>
            Architecture you can <Neon>inspect</Neon>
          </>
        }
        description="Plain components with clear boundaries. Everything runs where you choose to run it."
      />

      <SplitSection index="01" eyebrow="Architecture" title={<>Five parts, <Neon tone="rose">one direction</Neon></>}>
        <ol className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-stretch">
          {NODES.map(({ icon: Icon, title, body }, index) => (
            <Fragment key={title}>
              <li className="flex-1 basis-36 border border-line-strong bg-ink-raised/70 p-5">
                <Icon className="size-5 text-cerulean-glow" aria-hidden="true" />
                <h3 className="mt-4 font-heading text-sm font-medium uppercase tracking-[0.18em] text-fg">{title}</h3>
                <p className="mt-2 text-xs text-fg-muted">{body}</p>
              </li>
              {index < NODES.length - 1 ? (
                <li aria-hidden="true" className="hidden self-center text-rose sm:block">
                  <ArrowRight className="size-4" />
                </li>
              ) : null}
            </Fragment>
          ))}
        </ol>
        <p className="text-sm">Each step is confirmed before the next relies on it. That is what lets the locket safely archive its copy.</p>
      </SplitSection>

      <SplitSection index="02" eyebrow="Processing" title={<>A durable queue, <Neon>models loaded once</Neon></>}>
        <p>Uploads never wait for AI. A job is queued in the database and a separate worker picks it up, so a restart cannot lose work.</p>
        <PipelineStates />
        <FeatureGrid
          className="sm:grid-cols-2 lg:grid-cols-2"
          items={[
            { icon: ScrollText, title: "Local transcription", body: "Speech-to-text runs on your hardware, with word timestamps where supported." },
            { icon: Timer, title: "Reliable jobs", body: "Locking, idempotency, retries and bounded concurrency." },
          ]}
        />
        <p className="text-sm">Speaker separation and speaker matching also run locally. Some models need their license terms accepted before download. Model versions are saved with every transcript.</p>
      </SplitSection>

      <SplitSection index="03" eyebrow="Data" title={<>Where each kind of data <Neon tone="rose">lives</Neon></>}>
        <FeatureGrid
          className="sm:grid-cols-2 lg:grid-cols-2"
          items={[
            { icon: HardDrive, title: "Audio", body: "Durable file storage on your server. Never in the browser or a database blob." },
            { icon: Database, title: "Metadata", body: "Recordings, devices, jobs, transcripts and audit events in the database." },
            { icon: ShieldCheck, title: "Voice profiles", body: "Protected embeddings and consent records, scoped to one company." },
            { icon: ScrollText, title: "Logs", body: "Structured, with no raw audio and no voice embeddings." },
          ]}
        />
      </SplitSection>

      <SplitSection id="security" index="04" eyebrow="Security" title={<>Authorization on <Neon>every object</Neon></>}>
        <FeatureGrid
          className="sm:grid-cols-2 lg:grid-cols-2"
          items={[
            { icon: Laptop, title: "Sessions", body: "HTTP-only cookies, CSRF protection, strong password hashing and sign-in rate limits." },
            { icon: KeyRound, title: "Device credentials", body: "One credential per locket. Same Wi-Fi never means trusted." },
            { icon: Database, title: "Tenant isolation", body: "Every query is scoped to your company. Changing an ID in a URL reaches nothing else." },
            { icon: ShieldCheck, title: "Audit trail", body: "Pairing, revocation, deletion, enrollment and exports are recorded." },
          ]}
        />
      </SplitSection>

      <SplitSection index="05" eyebrow="Limits" title={<>What it does, and <Neon>what it does not</Neon></>}>
        <Callout title="Detection is heartbeat-based">
          <p>A locket is online while its authenticated heartbeats keep arriving, and offline when they stop. Nothing is guessed from nearby devices.</p>
        </Callout>
        <Callout title="Local network first">
          <p>The first deployment is a laptop running the backend and models, so the locket and browser must reach it. Do not expose it directly to the internet. Secure hosting is the documented next step.</p>
        </Callout>
        <Callout title="Verification status">
          <p>Firmware and timing targets are described as working only after real-hardware testing. Until then they are documented as designed, not tested.</p>
        </Callout>
      </SplitSection>

      <FinalCta />
    </>
  );
}
