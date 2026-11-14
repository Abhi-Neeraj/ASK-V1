import type { NextConfig } from "next";
import { fileURLToPath } from "node:url";

// The browser only talks to this Next.js origin. /api/* is proxied to FastAPI so
// session cookies stay same-origin (no cross-site cookie or CORS handling needed).
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const isDev = process.env.NODE_ENV === "development";

// Content-Security-Policy without nonces. A nonce needs every page rendered per request, and this app
// pre-renders a static shell (cacheComponents / partialPrefetching), which Next.js documents as incompatible
// with nonces (node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md). So scripts and styles
// allow 'unsafe-inline' for Next.js's own bootstrap code; everything else is locked down:
//  - no script, style, image, font, audio or connection can come from another origin, so injected markup
//    cannot load a remote script or send data to another site;
//  - no plugins (object-src), no <base> tag tricks, forms only post to this site, and no page may be framed.
// 'unsafe-eval' is only for `next dev` (React's debugging needs it); the websocket is its hot reload.
// blob: is for the voice recorder's preview and for saving a transcript, data: for inlined images.
const contentSecurityPolicy = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  "media-src 'self' blob:",
  `connect-src 'self'${isDev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: contentSecurityPolicy },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  // microphone=(self): voice enrollment records in the browser. Nothing else is requested.
  { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self), payment=(), usb=()" },
];

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    // The repo root holds an unrelated Expo app and lockfile; pin the root to this project.
    root: fileURLToPath(new URL(".", import.meta.url)),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
};

export default nextConfig;
