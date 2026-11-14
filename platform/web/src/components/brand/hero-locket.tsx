import { cn } from "@/lib/cn";

/*
 * Hero artwork: the ASK locket as a lit product object, graded in cerulean on the left and
 * neon rose on the right, inside a radial "voice ring" of light. No people, no photography.
 * Everything fades out before the edges of the viewBox so there is no visible frame.
 */

const CX = 360;
const CY = 400;

const BLUE = [127, 208, 245] as const; // cerulean glow
const ROSE = [255, 45, 149] as const; // neon rose

function mix(t: number): string {
  const c = BLUE.map((b, i) => Math.round(b + (ROSE[i] - b) * t));
  return `rgb(${c[0]} ${c[1]} ${c[2]})`;
}

// 96 radial bars. Length is a deterministic blend of sines so server and client render identically.
const BARS = Array.from({ length: 96 }, (_, i) => {
  const angle = (i / 96) * Math.PI * 2;
  const length = 12 + 40 * Math.abs(Math.sin(i * 0.55) * Math.cos(i * 0.21)) + 12 * Math.abs(Math.sin(i * 1.3));
  const start = 200;
  const end = start + length;
  const f = (n: number) => n.toFixed(1);
  return {
    x1: f(CX + start * Math.cos(angle)),
    y1: f(CY + start * Math.sin(angle)),
    x2: f(CX + end * Math.cos(angle)),
    y2: f(CY + end * Math.sin(angle)),
    // Blue on the left of the ring, rose on the right.
    color: mix((Math.cos(angle) + 1) / 2),
    opacity: 0.35 + ((i * 17) % 60) / 100,
  };
});

export function HeroLocket({ className, uid = "hero-locket" }: { className?: string; uid?: string }) {
  const id = (name: string) => `${uid}-${name}`;
  const ref = (name: string) => `url(#${id(name)})`;

  return (
    <svg
      viewBox="0 0 720 800"
      role="img"
      aria-label="Illustration of the ASK voice locket on a chain, ringed by a circle of light bars like a sound wave"
      // Everything drawn here fits inside the viewBox; ambient glow comes from page-level gradients.
      className={cn("h-auto w-full", className)}
    >
      <defs>
        <linearGradient id={id("rim")} x1="0" y1="0" x2="1" y2="0.35">
          <stop offset="0" stopColor="#bfeaff" />
          <stop offset="0.35" stopColor="#38b6e0" />
          <stop offset="0.62" stopColor="#7a3a9e" />
          <stop offset="1" stopColor="#ff2d95" />
        </linearGradient>
        <radialGradient id={id("face")} cx="0.3" cy="0.25" r="0.95">
          <stop offset="0" stopColor="#274a63" />
          <stop offset="0.45" stopColor="#12171c" />
          <stop offset="1" stopColor="#0a0a0a" />
        </radialGradient>
        <radialGradient id={id("tint-r")} cx="0.85" cy="0.78" r="0.6">
          <stop offset="0" stopColor="#ff2d95" stopOpacity="0.55" />
          <stop offset="1" stopColor="#ff2d95" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={id("chain")} gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="250">
          <stop offset="0" stopColor="#d7dde4" stopOpacity="0" />
          <stop offset="1" stopColor="#d7dde4" stopOpacity="0.85" />
        </linearGradient>
        <pattern id={id("dots")} width="13" height="13" patternUnits="userSpaceOnUse">
          <circle cx="6.5" cy="6.5" r="2" fill="#9aa4af" fillOpacity="0.6" />
        </pattern>
        <clipPath id={id("grille")}>
          <circle cx={CX} cy={CY + 24} r="64" />
        </clipPath>
        <clipPath id={id("body")}>
          <circle cx={CX} cy={CY} r="140" />
        </clipPath>
        <filter id={id("glow")} x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="10" />
        </filter>
        <filter id={id("glow-sm")} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4" />
        </filter>
      </defs>

      {/* HUD hairlines */}
      <circle cx={CX} cy={CY} r="318" fill="none" stroke="#fff" strokeOpacity="0.06" />
      <circle cx={CX} cy={CY} r="290" fill="none" stroke="#fff" strokeOpacity="0.12" strokeDasharray="2 11" strokeLinecap="round" />

      {/* pulse rings (static under reduced motion) */}
      {[0, 1, 2].map((i) => (
        <circle
          key={i}
          cx={CX}
          cy={CY}
          r="170"
          fill="none"
          stroke="#7fd0f5"
          strokeWidth="1.5"
          strokeOpacity="0.18"
          className="origin-center [transform-box:fill-box] motion-safe:animate-ring"
          style={{ animationDelay: `${i * 1.2}s` }}
        />
      ))}

      {/* voice ring */}
      <g strokeLinecap="round" strokeWidth="4">
        {BARS.map((bar, index) => (
          <line key={index} x1={bar.x1} y1={bar.y1} x2={bar.x2} y2={bar.y2} stroke={bar.color} opacity={bar.opacity} />
        ))}
      </g>

      {/* chain */}
      <path d="M360 250C330 180 250 90 200 0" fill="none" stroke={ref("chain")} strokeWidth="4" strokeLinecap="round" strokeDasharray="0.5 8" />
      <path d="M360 250C390 180 470 90 520 0" fill="none" stroke={ref("chain")} strokeWidth="4" strokeLinecap="round" strokeDasharray="0.5 8" />
      <rect x="345" y="236" width="30" height="42" rx="15" fill="none" stroke="#c9d1d9" strokeOpacity="0.85" strokeWidth="3.2" />

      {/* neon rim: blurred copy for the glow, then the crisp edge */}
      <circle cx={CX} cy={CY} r="140" fill="none" stroke={ref("rim")} strokeWidth="14" opacity="0.7" filter={ref("glow")} />

      {/* body */}
      <circle cx={CX} cy={CY} r="140" fill={ref("face")} />
      <g clipPath={ref("body")}>
        <circle cx={CX} cy={CY} r="140" fill={ref("tint-r")} />
      </g>
      <circle cx={CX} cy={CY} r="140" fill="none" stroke={ref("rim")} strokeWidth="4" />
      <circle cx={CX} cy={CY} r="117" fill="none" stroke="#fff" strokeOpacity="0.08" strokeWidth="1.5" />
      <path d="M262 318A128 128 0 0 1 350 272" fill="none" stroke="#fff" strokeOpacity="0.38" strokeWidth="3" strokeLinecap="round" />
      <path d="M452 500A128 128 0 0 0 478 440" fill="none" stroke="#ff7ab8" strokeOpacity="0.6" strokeWidth="3" strokeLinecap="round" />

      {/* front grille */}
      <g clipPath={ref("grille")}>
        <rect x={CX - 70} y={CY - 46} width="140" height="140" fill={ref("dots")} />
      </g>
      <circle cx={CX} cy={CY + 24} r="64" fill="none" stroke="#fff" strokeOpacity="0.1" />

      {/* status LED */}
      <circle cx={CX} cy={CY - 96} r="9" fill="#38b6e0" opacity="0.9" filter={ref("glow-sm")} />
      <circle cx={CX} cy={CY - 96} r="3.6" fill="#d9f3ff" />
    </svg>
  );
}
