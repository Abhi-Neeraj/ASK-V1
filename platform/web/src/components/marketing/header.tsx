"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Logo } from "@/components/brand/logo";
import { cn } from "@/lib/cn";

export const NAV_LINKS = [
  { href: "/product", label: "Product" },
  { href: "/technology", label: "Technology" },
  { href: "/privacy", label: "Privacy" },
  { href: "/contact", label: "Contact" },
] as const;

const linkClass =
  "rounded-sm px-1 py-2 text-[0.6875rem] uppercase tracking-[0.28em] transition-colors hover:text-fg aria-[current=page]:text-cerulean-glow";

/** Minimal masthead: logo, tiny tracked-caps links, and a hairline rule beneath. */
export function MarketingHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="sticky top-0 z-40 bg-ink/90">
      <div className="mx-auto max-w-7xl px-5 sm:px-8">
        <div className="flex h-16 items-center justify-between gap-6 border-b border-line-strong">
          <Logo />

          <nav aria-label="Primary" className="hidden items-center gap-9 md:flex">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={pathname === link.href ? "page" : undefined}
                className={cn(linkClass, "text-fg-muted")}
              >
                {link.label}
              </Link>
            ))}
            <Link
              href="/login"
              className="rounded-full border border-cerulean/60 px-4 py-2 text-[0.6875rem] uppercase tracking-[0.28em] text-cerulean-glow transition-colors hover:bg-cerulean/15 hover:text-fg"
            >
              Sign in
            </Link>
          </nav>

          <button
            type="button"
            className="-mr-2 grid size-10 place-items-center rounded-full text-fg md:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? <X className="size-5" aria-hidden="true" /> : <Menu className="size-5" aria-hidden="true" />}
          </button>
        </div>
      </div>

      {open ? (
        <div id="mobile-nav" className="border-b border-line bg-ink px-5 pb-8 pt-4 md:hidden">
          <nav aria-label="Mobile primary" className="flex flex-col">
            {[...NAV_LINKS, { href: "/login", label: "Sign in" }].map((link) => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={pathname === link.href ? "page" : undefined}
                onClick={() => setOpen(false)}
                className="border-b border-line py-4 text-sm uppercase tracking-[0.28em] text-fg-muted transition-colors hover:text-fg aria-[current=page]:text-cerulean-glow"
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
      ) : null}
    </header>
  );
}
