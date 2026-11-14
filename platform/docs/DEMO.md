# The demo site

A way to look at the whole interface, with nothing to install except the web app and nothing real in it. It runs **on your own computer**, at <http://localhost:3000>. It is not published anywhere.

## Start it

From the repository root:

```text
npm install --prefix platform/web          (first time only)
npm run dev:demo --prefix platform/web
```

Open <http://localhost:3000>. Click **Sign in** and type any email and password: demo mode accepts anything. Stop it with Ctrl+C.

## What it is

- The real interface, driven by invented sample data held in the browser's memory (`platform/web/src/lib/demo/`). Nothing is sent anywhere; there is no backend, database or locket behind it.
- A banner reads **"Demo mode"** on every dashboard page, and the sign-in page says that any details will work.
- Actions seem to work (pair a locket, add a person, record consent, correct a transcript, invite a teammate, switch someone off, process a recording again, enroll a voice) but **reset when you reload the page**.
- After a few seconds, a second locket "comes online" so you can see the connection prompt.
- The signed-in demo person is an **administrator**, so you also see the Audit log and the team controls. The sample people, lockets, transcripts and audit entries are fictional.

## What it is not

- **Not real recognition.** Transcripts are invented text. No speech model runs, nobody is really identified, and the voice enrollment dialog saves nothing (a microphone prompt is real, in a browser that allows it, but the recording is not kept or sent).
- **Not evidence of anything about the backend.** It shows the screens, not that they work with a real server. The real client talks to the FastAPI backend and has been exercised against it separately.
- **Not for sharing.** It has no sign-in and no data of its own, but it is a development server. To put a demo on a public address, build it with `NEXT_PUBLIC_DEMO_MODE=true` and host it deliberately, with the banner left on.

## Seeing it with the real backend instead

Without `dev:demo`, the same screens talk to the real API:

```text
(from platform/api)  .venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --timeout-graceful-shutdown 3
(from the root)      npm run dev --prefix platform/web
```

You need an account (`python -m app.cli bootstrap`, see [api/README.md](../api/README.md)). To see recordings flow through, run the placeholder worker as well (`ASK_AI_PROVIDER=stub python -m app.worker`) and a locket simulator (`tools/simulate_device.py`). Everything the stub produces is labelled as placeholder on every screen.
