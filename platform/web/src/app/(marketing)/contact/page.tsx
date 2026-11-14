import type { Metadata } from "next";
import Link from "next/link";
import { ContactForm } from "@/components/marketing/contact-form";
import { Neon, PageHero, Section } from "@/components/marketing/section";

export const metadata: Metadata = {
  title: "Contact",
  description: "Talk to the ASK team about a pilot, a deployment, or a question about privacy and security.",
};

export default function ContactPage() {
  return (
    <>
      <PageHero
        eyebrow="Contact"
        title={
          <>
            Let&apos;s <Neon>talk</Neon>
          </>
        }
        description="Tell us about your team and what you want to capture. We read every message."
      />
      <Section className="pt-16 sm:pt-20">
        <div className="grid gap-14 lg:grid-cols-[0.8fr_1.2fr] lg:gap-24">
          <div className="space-y-8 text-base leading-relaxed text-fg-muted">
            <div>
              <h2 className="caption text-cerulean-glow">Pilots and deployments</h2>
              <p className="mt-3">
                Describe your use case, how many lockets you expect to use, and where you plan to run the
                server.
              </p>
            </div>
            <div>
              <h2 className="caption text-cerulean-glow">Privacy and security</h2>
              <p className="mt-3">
                Read the <Link href="/privacy" className="text-fg underline underline-offset-4">privacy approach</Link>{" "}
                and the <Link href="/technology#security" className="text-fg underline underline-offset-4">security model</Link>{" "}
                first, then ask us anything they do not answer.
              </p>
            </div>
            <div>
              <h2 className="caption text-cerulean-glow">Already using ASK?</h2>
              <p className="mt-3">
                Questions about your recordings or account go to your company administrator.{" "}
                <Link href="/login" className="text-fg underline underline-offset-4">Sign in</Link> to manage devices and people.
              </p>
            </div>
          </div>
          <div className="relative">
            <ContactForm />
          </div>
        </div>
      </Section>
    </>
  );
}
