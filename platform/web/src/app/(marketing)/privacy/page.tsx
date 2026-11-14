import { Server, ShieldCheck, Trash2, Users } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { ConsentArt } from "@/components/brand/capability-art";
import { Callout } from "@/components/marketing/blocks";
import { Neon, PageHero, Section } from "@/components/marketing/section";

const GLANCE = [
  { icon: Server, title: "Your server", body: "Audio and transcripts stay on infrastructure you run." },
  { icon: ShieldCheck, title: "Consent first", body: "No voice profile without a consent record." },
  { icon: Users, title: "Role-based", body: "Only your company's members, by role and device." },
  { icon: Trash2, title: "Deletable", body: "Recordings and profiles can be removed, with a log." },
] as const;

export const metadata: Metadata = {
  title: "Privacy",
  description:
    "What ASK stores, where it lives, how long it is kept, how speaker profiles and consent work, and how deletion works.",
};

const SECTIONS = [
  {
    id: "stores",
    title: "What ASK stores",
    body: (
      <>
        <p>ASK stores the following for your company workspace:</p>
        <ul>
          <li>Audio recordings uploaded by your lockets.</li>
          <li>Transcripts, with timestamps, speaker labels and the models and versions that produced them.</li>
          <li>Voice profiles for people who have consented: speaker embeddings and the consent record.</li>
          <li>Account, membership and device information needed to run the workspace.</li>
          <li>Audit events for sensitive actions such as pairing, revocation, deletion, enrollment and export.</li>
        </ul>
        <p>Raw audio and voice embeddings are never written to ordinary application logs.</p>
      </>
    ),
  },
  {
    id: "where",
    title: "Where it is stored",
    body: (
      <>
        <p>
          Audio is on the locket until your server confirms it has stored the file, then in
          durable storage on your ASK server. Metadata and transcripts are in your database. Audio is never kept
          in the browser.
        </p>
        <p>
          Core processing runs on your own hardware with local models. It does not require a paid or third-party
          AI service. Downloading the model files needs an internet connection once, and some models require
          their license terms to be accepted first.
        </p>
      </>
    ),
  },
  {
    id: "retention",
    title: "Retention",
    body: (
      <>
        <p>
          Your administrator sets the retention policy in the workspace settings. Until a retention period is
          configured, recordings are kept until someone with permission deletes them.
        </p>
      </>
    ),
  },
  {
    id: "profiles",
    title: "Speaker profiles and consent",
    body: (
      <>
        <p>
          A voice profile is created only after a consent record is captured for that person, including who
          recorded it and when. Voice samples must meet minimum length and quality checks.
        </p>
        <p>
          A speaker is identified only when a voice match meets the calibrated confidence and audio-quality
          criteria. Otherwise the speaker is shown as Unknown. A person is never identified from a name that
          appears in the transcript.
        </p>
        <p>Authorized users can replace or delete a person&apos;s voice profile at any time.</p>
        <ConsentArt className="max-w-md" />
      </>
    ),
  },
  {
    id: "access",
    title: "Access controls",
    body: (
      <>
        <p>
          Members join a workspace by invitation, never because of their email domain. Roles (administrator,
          manager, employee) and device permissions decide who can see devices, recordings, transcripts and
          voice profiles. Every request is checked on the server, and one company can never reach another
          company&apos;s data. Downloading audio requires authorization.
        </p>
      </>
    ),
  },
  {
    id: "deletion",
    title: "Deletion",
    body: (
      <>
        <ul>
          <li>
            <strong>Recording:</strong> deleting a recording removes it from the library and queues its audio
            file and transcript for permanent removal after a short grace period set by your administrator.
          </li>
          <li>
            <strong>Voice profile:</strong> deleting a profile removes its embeddings and enrollment samples.
            Existing transcripts keep their text, and the speaker becomes Unknown.
          </li>
          <li>
            <strong>Device:</strong> revoking a locket invalidates its credential immediately. Its existing
            recordings are kept until deleted.
          </li>
        </ul>
        <p>
          Each of these actions needs confirmation and is written to the audit log. Deleted data can remain in
          backups until those backups expire, which your operator controls.
        </p>
      </>
    ),
  },
  {
    id: "requests",
    title: "Requesting deletion",
    body: (
      <>
        <p>
          If you are recorded by a locket and want your recordings or voice profile removed, ask your company
          administrator, who can delete them from the dashboard. You can also{" "}
          <Link href="/contact">contact us</Link> and we will point you to the right administrator.
        </p>
      </>
    ),
  },
] as const;

export default function PrivacyPage() {
  return (
    <>
      <PageHero
        eyebrow="Privacy"
        title={
          <>
            Privacy, <Neon tone="rose">stated plainly</Neon>
          </>
        }
        description="What the software stores, where it lives, who can see it, and how it is removed. Recordings and voice profiles are sensitive, and this page says exactly how ASK treats them."
      />

      <Section className="pb-0 pt-14 sm:pb-0 sm:pt-16">
        <ul aria-label="At a glance" className="grid border-l border-t border-line-strong sm:grid-cols-2 lg:grid-cols-4">
          {GLANCE.map(({ icon: Icon, title, body }) => (
            <li key={title} className="border-b border-r border-line-strong p-7">
              <Icon className="size-6 text-cerulean-glow" aria-hidden="true" />
              <p className="mt-6 font-heading text-sm font-medium uppercase tracking-[0.2em] text-fg">{title}</p>
              <p className="mt-2 text-sm text-fg-muted">{body}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section className="pt-14 sm:pt-20">
        <div className="grid gap-12 lg:grid-cols-[14rem_1fr] lg:gap-20">
          <nav aria-label="On this page" className="lg:sticky lg:top-28 lg:self-start">
            <p className="caption text-fg-subtle">On this page</p>
            <ul className="mt-5 space-y-3 border-l border-line-strong">
              {SECTIONS.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    className="-ml-px block border-l border-transparent py-0.5 pl-4 text-sm text-fg-muted transition-colors hover:border-cerulean-glow hover:text-fg"
                  >
                    {section.title}
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          <div className="max-w-3xl space-y-16">
            {SECTIONS.map((section) => (
              <section key={section.id} id={section.id} className="scroll-mt-28">
                <h2 className="font-heading text-sm font-medium uppercase tracking-[0.22em] text-cerulean-glow">
                  {section.title}
                </h2>
                <div className="mt-5 space-y-4 text-base leading-relaxed text-fg-muted [&_li]:pl-1 [&_strong]:font-medium [&_strong]:text-fg [&_a]:text-fg [&_a]:underline [&_a]:underline-offset-4 [&_ul]:list-disc [&_ul]:space-y-3 [&_ul]:pl-5 [&_ul]:marker:text-rose">
                  {section.body}
                </div>
              </section>
            ))}

            <Callout title="What this page is not">
              <p>
                This page describes how the software behaves. It is not legal advice and does not claim
                compliance with any specific law or standard. Your organization is responsible for lawful use,
                for informing the people it records, and for obtaining their consent.
              </p>
            </Callout>
          </div>
        </div>
      </Section>
    </>
  );
}
