"use client";

import { RotateCw } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";

/** Last-resort boundary for unexpected render errors. Shows no technical detail to the user. */
export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="grid min-h-dvh place-items-center px-5">
      <main id="main" role="alert" className="flex max-w-lg flex-col items-center text-center">
        <p className="caption">Something went wrong</p>
        <h1 className="mt-6 font-display text-[clamp(1.8rem,5vw,3rem)] uppercase leading-[1.1] text-fg">
          That did not work
        </h1>
        <p className="mt-6 text-base leading-relaxed text-fg-muted">
          An unexpected error stopped this page from loading. Try again, or go back to the home page.
        </p>
        <div className="mt-10 flex flex-wrap justify-center gap-4">
          <Button onClick={reset}>
            <RotateCw className="size-4" aria-hidden="true" />
            Try again
          </Button>
          <Button asChild variant="secondary">
            <Link href="/">Home</Link>
          </Button>
        </div>
      </main>
    </div>
  );
}
