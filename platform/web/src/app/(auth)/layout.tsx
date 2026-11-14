import type { ReactNode } from "react";
import { HeroLocket } from "@/components/brand/hero-locket";
import { Logo } from "@/components/brand/logo";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative isolate grid min-h-dvh overflow-hidden lg:grid-cols-[minmax(0,34rem)_1fr]">
      {/* Ambient light is plain radial gradients with no box edges. There is no divider line either. */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10">
        <div className="absolute -left-40 -top-32 size-[46rem] bg-[radial-gradient(closest-side,rgb(0_123_167/0.32),transparent)]" />
        <div className="absolute -bottom-48 -right-40 size-[54rem] bg-[radial-gradient(closest-side,rgb(255_45_149/0.2),transparent)]" />
        <div className="bg-grain absolute inset-0 opacity-[0.04]" />
      </div>

      <div className="flex flex-col px-5 py-8 sm:px-10 lg:px-14">
        <Logo />
        <main id="main" className="flex flex-1 flex-col justify-center py-12">
          {children}
        </main>
        <p className="caption text-fg-subtle">Voice intelligence for teams</p>
      </div>

      <div aria-hidden="true" className="hidden items-center justify-center lg:flex">
        <HeroLocket uid="auth-locket" className="max-w-lg" />
      </div>
    </div>
  );
}
