import { cn } from "@/lib/cn";

/*
 * Exploded view of the locket: front shell, connection board, power cell. Each layer is drawn in plan
 * view and squashed vertically to read as a disc in perspective; strokes keep a constant width.
 * Numbered markers are HTML (so they stay legible at any size) and match LEGEND below.
 */

export const LOCKET_PARTS = [
  { n: 1, title: "Audio capture", body: "Records speech through the front grille.", x: 260, y: 168 },
  { n: 2, title: "Status light", body: "Shows when the locket is recording.", x: 260, y: 140 },
  { n: 3, title: "Connection board", body: "Runs the locket and sends uploads over Wi-Fi.", x: 236, y: 296 },
  { n: 4, title: "Local storage", body: "Keeps every clip until the server confirms it.", x: 322, y: 308 },
  { n: 5, title: "Power", body: "A rechargeable cell that powers everything.", x: 260, y: 436 },
] as const;

const W = 520;
const H = 540;
const SQUASH = 0.36;

function Layer({ cy, side, children }: { cy: number; side: string; children: React.ReactNode }) {
  return (
    <g>
      {/* thickness: a band under the disc */}
      <path d={`M110 ${cy}v16a150 54 0 0 0 300 0v-16z`} fill={side} />
      <g transform={`translate(260 ${cy}) scale(1 ${SQUASH})`}>{children}</g>
    </g>
  );
}

export function LocketExploded({ className, uid = "exploded" }: { className?: string; uid?: string }) {
  const id = (name: string) => `${uid}-${name}`;
  const ref = (name: string) => `url(#${id(name)})`;
  const stroke = { vectorEffect: "non-scaling-stroke" as const };

  return (
    <div className={cn("relative mx-auto w-full max-w-[32rem]", className)}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Exploded view of the locket: the front shell with audio capture and a status light, the connection board with local storage, and the power cell."
        className="h-auto w-full overflow-visible"
      >
        <defs>
          <linearGradient id={id("rim")} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#7fd0f5" />
            <stop offset="0.55" stopColor="#007ba7" />
            <stop offset="1" stopColor="#ff2d95" />
          </linearGradient>
          <radialGradient id={id("shell")} cx="0.3" cy="0.3" r="0.9">
            <stop offset="0" stopColor="#2a3744" />
            <stop offset="0.6" stopColor="#12171c" />
            <stop offset="1" stopColor="#0a0a0a" />
          </radialGradient>
          <radialGradient id={id("board")} cx="0.5" cy="0.5" r="0.7">
            <stop offset="0" stopColor="#10222d" />
            <stop offset="1" stopColor="#0b1319" />
          </radialGradient>
          <linearGradient id={id("cell")} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#ff7ab8" />
            <stop offset="1" stopColor="#c01a70" />
          </linearGradient>
          <pattern id={id("dots")} width="14" height="14" patternUnits="userSpaceOnUse">
            <circle cx="7" cy="7" r="2.1" fill="#9aa4af" fillOpacity="0.65" />
          </pattern>
          <radialGradient id={id("halo")}>
            <stop offset="0" stopColor="#007ba7" stopOpacity="0.45" />
            <stop offset="1" stopColor="#007ba7" stopOpacity="0" />
          </radialGradient>
          <filter id={id("blur")} x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="5" />
          </filter>
        </defs>

        <ellipse cx="260" cy="300" rx="250" ry="150" fill={ref("halo")} />

        {/* alignment lines between layers */}
        {[110, 260, 410].map((x) => (
          <path key={x} d={`M${x} 170V440`} stroke="#38b6e0" strokeOpacity="0.28" strokeDasharray="3 7" fill="none" />
        ))}

        {/* power cell (bottom) */}
        <Layer cy={436} side="#1a0d15">
          <circle r="150" fill="#12090f" stroke="#ff2d95" strokeOpacity="0.5" strokeWidth="2" {...stroke} />
          <rect x="-78" y="-46" width="156" height="92" rx="18" fill={ref("cell")} />
          <rect x="78" y="-14" width="14" height="28" rx="5" fill="#ffb3d6" />
          <path d="M-46 0h22M-35 -11v22M16 0h30" stroke="#fff" strokeOpacity="0.85" strokeWidth="4" strokeLinecap="round" {...stroke} />
        </Layer>

        {/* circuit board (middle) */}
        <Layer cy={300} side="#0a1218">
          <circle r="150" fill={ref("board")} stroke="#38b6e0" strokeOpacity="0.6" strokeWidth="2" {...stroke} />
          {/* traces */}
          <g fill="none" stroke="#38b6e0" strokeOpacity="0.55" strokeWidth="1.6" {...stroke}>
            <path d="M-20 -10H-110M-20 6H-90V52M-20 -26H-70L-100 -60M10 -34V-110M30 -34V-90H70" />
            <path d="M-100 -60h24M-110 -10v-24" />
          </g>
          {/* antenna trace */}
          <path d="M-110 -80l14 -16 14 16 14 -16 14 16" fill="none" stroke="#7fd0f5" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" {...stroke} />
          {/* main chip */}
          <rect x="-58" y="-50" width="76" height="76" rx="10" fill="#18232d" stroke="#7fd0f5" strokeWidth="2" {...stroke} />
          <rect x="-40" y="-32" width="40" height="40" rx="6" fill="#0b1319" stroke="#38b6e0" strokeOpacity="0.7" {...stroke} />
          {/* storage slot */}
          <rect x="34" y="-34" width="62" height="84" rx="8" fill="#1d2a35" stroke="#ff7ab8" strokeOpacity="0.85" strokeWidth="2" {...stroke} />
          <rect x="46" y="-20" width="38" height="52" rx="4" fill="#0b1319" />
          <path d="M84 -34v12" stroke="#0b1319" strokeWidth="6" />
        </Layer>

        {/* front shell (top) */}
        <Layer cy={170} side="#0c1015">
          <circle r="150" fill={ref("shell")} stroke={ref("rim")} strokeWidth="3.2" {...stroke} />
          <circle r="124" fill="none" stroke="#fff" strokeOpacity="0.1" {...stroke} />
          <circle r="56" fill={ref("dots")} />
          <circle r="56" fill="none" stroke="#fff" strokeOpacity="0.14" {...stroke} />
          <circle cx="0" cy="-78" r="12" fill="#38b6e0" filter={ref("blur")} />
          <circle cx="0" cy="-78" r="5" fill="#d9f3ff" />
        </Layer>
      </svg>

      {/* numbered markers */}
      <ol className="contents">
        {LOCKET_PARTS.map((part) => (
          <li
            key={part.n}
            style={{ left: `${(part.x / W) * 100}%`, top: `${(part.y / H) * 100}%` }}
            className="absolute -translate-x-1/2 -translate-y-1/2"
          >
            <span className="grid size-6 place-items-center rounded-full border border-rose bg-ink font-mono text-[0.6875rem] text-rose shadow-[0_0_16px_-2px_rgb(255_45_149/0.8)]">
              {part.n}
              <span className="sr-only"> {part.title}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
