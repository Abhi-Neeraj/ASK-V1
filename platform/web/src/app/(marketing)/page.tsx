import { Anatomy } from "@/components/marketing/home/anatomy";
import { Capabilities } from "@/components/marketing/home/capabilities";
import { Faq } from "@/components/marketing/home/faq";
import { FinalCta } from "@/components/marketing/home/final-cta";
import { Hero } from "@/components/marketing/home/hero";
import { PrivacyBand } from "@/components/marketing/home/privacy-band";
import { Showcase } from "@/components/marketing/home/showcase";
import { Workflow } from "@/components/marketing/home/workflow";

export default function HomePage() {
  return (
    <>
      <Hero />
      <Showcase />
      <Anatomy />
      <Workflow />
      <Capabilities />
      <PrivacyBand />
      <Faq />
      <FinalCta />
    </>
  );
}
