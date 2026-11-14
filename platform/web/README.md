# ASK web

Next.js (App Router) frontend for the ASK voice locket platform: public marketing site, company sign-in, and the authenticated dashboard. Separate from the Expo app at the repo root.

Status: UI complete against the API contract in `src/lib/api/`, and checked in a browser against the real FastAPI backend (`platform/api`): sign-in, the dashboard, creating, pairing, assigning and revoking a locket, online status with live updates and the connection popup, a recording arriving and its audio streaming (including range requests), adding a person and recording consent, inviting a teammate, the employee's role-limited view, retention settings calls, and the contact form. Processing screens were checked against a real API with a worker running the placeholder stub: the "waiting for a processing worker" notice, placeholder badges and warnings on lists and recording pages, "Process again", the people page when no speaker model exists, and phone width on the recording page. The voice enrollment dialog was viewed only in demo mode (a real API here has no speaker model), where the denied-microphone message was seen; **recording from a real microphone has not been tried** (the pane blocks it). Not exercised in a browser: pressing play, saving settings from the form, withdrawing consent, removing a person and revoking an invitation (covered by component and API tests). Without the API running, the real client shows "can't reach the server" states. See `../docs/PLAN.md` and `../docs/AI.md`.

## Commands

Run from `platform/web` (Node 20.9 or newer; developed on Node 24).

| Task | Command |
| --- | --- |
| Install | `npm install` |
| Dev server (needs the API) | `npm run dev` then open http://localhost:3000 |
| Dev server with sample data, no API | `npm run dev:demo` |
| Lint | `npm run lint` |
| Typecheck | `npm run typecheck` |
| Tests | `npm test` |
| Production build | `npm run build`, then `npm start` |

Copy `.env.example` to `.env.local` to change the API address. `/api/*` is proxied to `API_ORIGIN` (default `http://127.0.0.1:8000`) so the browser only ever talks to one origin.

## Demo mode

`NEXT_PUBLIC_DEMO_MODE=true` (set by `npm run dev:demo`) swaps the real API for an in-memory demo with invented data, so the dashboard can be reviewed without a backend. It is off by default, every dashboard page shows a "Demo mode" banner while it is on, and the sign-in page says that any details will work. Demo data lives in `src/lib/demo/` and resets on reload.

## Layout

```text
src/app/(marketing)   public pages: /, /product, /technology, /privacy, /contact
src/app/(auth)        /login, /register (invitation only), /forgot-password, /reset-password
src/app/dashboard     /dashboard, /devices, /recordings, /recordings/[id], /people, /audit (administrators), /settings
src/components/ui     buttons, inputs, dialog, badges, skeletons, empty and error states
src/components/brand  logo, locket artwork, dashboard preview, exploded locket, card art
src/lib/api           typed client, API contract (contract.ts), TanStack Query hooks
src/lib/demo          demo-mode implementation of that contract
src/lib/audio         in-browser microphone capture and the 16 kHz mono WAV encoder used for voice enrollment
src/proxy.ts          optimistic redirect to /login when no session cookie is present
```

## Design system

- Colors are exactly Jet Black `#0A0A0A`, Cerulean `#007BA7`, Neon Rose `#FF2D95`, plus derived neutrals and status colors. Tokens live in `src/app/globals.css`. Raw Cerulean is 4.1:1 on black, so small text uses the lighter `cerulean-bright` / `cerulean-glow` tints and white-on-Cerulean is used for filled buttons (4.8:1).
- Wide display type (Michroma) is for marketing headlines only and always uppercase. App and card text uses Geist.
- Motion respects `prefers-reduced-motion`: scroll reveals are CSS-only and content is fully visible without them, and a global rule shortens remaining animations.
- No people or photography. Artwork is original SVG and markup.
- Visitor-facing text never names hardware parts or third-party products or models.

## Testing

`npm test` runs Vitest with jsdom and Testing Library: API client (CSRF, error envelope, network failure), validation, open-redirect guard, login and contact forms, recordings library (search, filter, pagination), recording page (placeholder and not-separated notices, waiting-for-a-worker wording, process again), people page and voice states, voice enrollment dialog, the live-update stream, the WAV encoder, exports, the demo API, and axe-core accessibility checks on the main components and the new processing screens.

Not covered: color contrast (jsdom has no layout, so it is checked from the tokens), real-browser end-to-end flows, anything that needs the backend, and capturing from a real microphone (a test has none: the recorder is replaced by a stand-in, and only the WAV encoder is tested directly).

## Troubleshooting

- A style or component edit does not show up: on Windows the dev server can miss a file-watcher event. Save the file again, or restart `npm run dev`.
- The browser console shows a failed `ws://localhost:3000/_next/hmr` connection when the page is opened through some proxies or preview panes. It only affects hot reload.
- `next build` fails with a message about `usePathname`, `params` or `searchParams` outside `<Suspense>`: Cache Components requires request-time values to sit behind a Suspense boundary. See `src/app/dashboard/layout.tsx` for the pattern.
