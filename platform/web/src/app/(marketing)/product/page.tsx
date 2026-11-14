import { ClipboardCheck, HardDriveDownload, Layers, RefreshCcw } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { SpeakerArt } from "@/components/brand/capability-art";
import { LocketExploded } from "@/components/brand/locket-exploded";
import { ProductPreview } from "@/components/brand/product-preview";
import { Callout, Compare, FeatureGrid, SpecList, SplitSection, Steps } from "@/components/marketing/blocks";
import { FinalCta } from "@/components/marketing/home/final-cta";
import { Neon, PageHero } from "@/components/marketing/section";

export const metadata: Metadata = {
  title: "Product",
  description:
    "The ASK locket, its recording and upload workflow, local transcription, speaker recognition and the company dashboard.",
};

export default function ProductPage() {
  return (
    <>
      <PageHero
        eyebrow="Product"
        title={
          <>
            The locket, the pipeline, the <Neon tone="rose">library</Neon>
          </>
        }
        description="A wearable that records, a server that understands, and a dashboard where your team finds what was said."
      />

      <SplitSection index="01" eyebrow="The locket" title={<>A small device with a <Neon>clear job</Neon></>}>
        <LocketExploded uid="product-exploded" />
        <SpecList
          items={[
            { label: "Recording", value: "Short audio clips, saved as WAV files" },
            { label: "Storage", value: "Kept on the locket until your server confirms them" },
            { label: "Network", value: "Wi-Fi, with the locket's own credential" },
            { label: "Power", value: "Rechargeable" },
            { label: "Clip length", value: "Configurable (10 seconds to start)" },
          ]}
        />
        <p className="text-sm">Status details show in the dashboard only when the locket really reports them.</p>
      </SplitSection>

      <SplitSection index="02" eyebrow="Recording workflow" title={<>From a clip to a <Neon tone="rose">confirmed</Neon> recording</>}>
        <Steps
          items={[
            { title: "Record", body: "A WAV file is saved on the locket with a unique ID." },
            { title: "Queue", body: "Finished clips wait in order on the device." },
            { title: "Upload", body: "Sent over Wi-Fi with the device credential, retried with backoff." },
            { title: "Validate", body: "The server checks name, size, format, sample rate and audio." },
            { title: "Store", body: "The file is saved durably and its details are committed." },
            { title: "Acknowledge", body: "Only then does the locket archive its own copy." },
          ]}
        />
      </SplitSection>

      <SplitSection index="03" eyebrow="Reliable transfer" title={<>A bad connection <Neon>loses nothing</Neon></>}>
        <FeatureGrid
          className="sm:grid-cols-2 lg:grid-cols-2"
          items={[
            { icon: HardDriveDownload, title: "Stored first", body: "Audio is saved on the locket before any upload." },
            { icon: RefreshCcw, title: "Safe retries", body: "A retried clip never creates a duplicate." },
            { icon: Layers, title: "Ordered clips", body: "Multi-file recordings keep their order." },
            { icon: ClipboardCheck, title: "Acknowledged", body: "Success means the server confirmed it." },
          ]}
        />
      </SplitSection>

      <SplitSection index="04" eyebrow="Transcription and speakers" title={<>Who spoke when, and <Neon tone="rose">who is that</Neon></>}>
        <SpeakerArt />
        <Compare
          items={[
            {
              label: "Speaker separation",
              title: "Speaker turns",
              body: "Splits a recording into turns labelled SPEAKER_00, SPEAKER_01. It does not know who anyone is.",
              tone: "blue",
            },
            {
              label: "Speaker recognition",
              title: "Matching a voice to a person",
              body: "Compares a turn with people who consented. Below the threshold, the speaker stays Unknown.",
              tone: "rose",
            },
          ]}
        />
        <p className="text-sm">A name in the transcript never identifies anyone. Only a confident voice match does.</p>
      </SplitSection>

      <SplitSection index="05" eyebrow="Dashboard" title={<>One place to <Neon>find</Neon> and <Neon tone="rose">review</Neon></>}>
        <ProductPreview />
        <Callout title="How long does processing take?">
          <p>It depends on your hardware and the clip length. The dashboard shows each stage live, and ASK does not promise a fixed time.</p>
        </Callout>
        <p className="text-sm">
          Have an account?{" "}
          <Link href="/login" className="text-cerulean-glow underline underline-offset-4 hover:text-fg">
            Sign in
          </Link>
          .
        </p>
      </SplitSection>

      <FinalCta />
    </>
  );
}
