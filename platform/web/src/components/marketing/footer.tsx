import Link from "next/link";
import { Logo } from "@/components/brand/logo";

const COLUMNS = [
  {
    title: "Platform",
    links: [
      { href: "/product", label: "Product" },
      { href: "/technology", label: "Technology" },
      { href: "/login", label: "Sign in" },
    ],
  },
  {
    title: "Trust",
    links: [
      { href: "/privacy", label: "Privacy" },
      { href: "/technology#security", label: "Security" },
      { href: "/contact", label: "Contact" },
    ],
  },
] as const;

export function MarketingFooter() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto grid max-w-7xl gap-12 px-5 py-14 sm:px-8 md:grid-cols-[1.4fr_1fr_1fr]">
        <div className="max-w-sm space-y-4">
          <Logo />
          <p className="text-sm leading-relaxed text-fg-muted">
            A voice locket and a private processing platform for company teams. Recording, storage and
            transcription run on infrastructure your organization controls.
          </p>
        </div>
        {COLUMNS.map((column) => (
          <nav key={column.title} aria-label={column.title}>
            <h2 className="text-sm font-semibold text-fg">{column.title}</h2>
            <ul className="mt-4 space-y-3">
              {column.links.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-sm text-fg-muted transition-colors hover:text-fg">
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
      <div className="border-t border-line">
        <p className="mx-auto max-w-7xl px-5 py-6 text-xs leading-relaxed text-fg-subtle sm:px-8">
          Recording people requires their informed consent and compliance with the laws that apply where you
          operate. ASK provides consent records and access controls; it does not provide legal advice or a
          compliance guarantee.
        </p>
      </div>
    </footer>
  );
}
