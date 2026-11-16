# ASK app (`platform/web`)

The ASK app (Expo Router, React Native for Web): the same code runs as a phone app and as a website. It is the
client of the ASK server in `../api`: sign in, see recordings with their transcript and summary, search, manage
lockets (pair with an emailed code, people, limit, firmware updates), team and account. Design:
`docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md`; plan:
`docs/superpowers/plans/2026-11-14-app-lockets-cloud-pipeline.md`.

## Run the website against a local server

```text
(in ../api)  .venv/Scripts/python -m uvicorn app.main:create_app --factory --port 8000
(in ../api)  .venv/Scripts/python -m app.worker          optional: processes uploads
npm ci
npm run build:web                                        static files in dist/
npm run serve:web                                        http://localhost:3000
```

`serve.mjs` serves the app and forwards `/api/*` to `API_ORIGIN` (default `http://localhost:8000`), so the
browser sees one origin and the session cookie stays first-party. If the server is down, `/api` answers
`502 api_unreachable`.

## Run on a phone (needed for Bluetooth)

Bluetooth needs a development build; Expo Go and the website cannot reach the locket.

```text
npm ci
EXPO_PUBLIC_API_URL=https://your-ask-server npx expo run:android    (or run:ios on a Mac)
```

The phone talks to the server at `EXPO_PUBLIC_API_URL` and keeps its backup in the app's documents folder:
`ASK/<recordingId>/audio.wav`, `transcription.txt`, `summary.txt` (`ASK/pending/` holds recordings waiting
to upload).

## Check

```text
npm run check     TypeScript
npm test          Vitest (logic: API client, pairing, receiving audio, upload and backup, firmware offer)
```

## Layout

- `src/app` screens: Sign in, Forgot password, Today, Ask (search), Activity (recordings), You, Recording,
  Lockets, Locket, Team, Account.
- `src/application` what screens call: session, pairing (`pairLocket`), receiving a recording
  (`captureRecording`), upload and backup (`syncRecording`), firmware (`firmwareUpdate`), the phone's live
  locket connection (`locketController`).
- `src/domain/locket` the Bluetooth protocol pieces: ADPCM audio blocks, CONTROL commands, INFO and STATUS,
  UUIDs, the `LocketLink` interface. Frames: `src/domain/device/framing.ts`.
- `src/infrastructure` HTTP client and endpoints, Bluetooth (`react-native-ble-plx`, phone only), files
  (`expo-file-system` on phones, downloads on the web), CSRF token store (secure store on phones).

## What was and was not run

- Run: type check, unit tests, web build, and the website by hand against a local server with the placeholder
  worker (sign in, Today, recording page, Process again, search, Activity, locket limit, team invitation,
  change password, sign out).
- **Not run:** anything with Bluetooth (no phone, no locket here: `getLocketLink.native.ts` is type-checked
  only), the phone build, the phone's file backup and audio playback, and the website's file download
  buttons by hand (their server endpoints were checked).
