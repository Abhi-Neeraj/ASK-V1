# App: sign-in, server-backed screens, Bluetooth pairing and recording — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the ASK app in `platform/web` (Expo Router; runs on phones and as a website) into the client of the server built in the server plan: sign in, see recordings with transcript and summary, search, manage lockets (pair with an emailed code, people, limit, firmware), team and account, with every button doing something real. Add the Bluetooth logic that pairs with a locket, receives its audio, uploads it and keeps the `.txt` backup.

**Architecture:** Pure, tested logic under `src/domain/locket` (frames, ADPCM, commands, pairing, capture) and `src/application` (sync, firmware, session); thin adapters under `src/infrastructure` (HTTP client, Bluetooth via `react-native-ble-plx` on phones, files via `expo-file-system` on phones and downloads on the web). Screens only call `src/application` and `src/infrastructure/api`. On the web the app is served by `serve.mjs`, which also forwards `/api/*` to the FastAPI server so cookies stay same-origin.

**Tech Stack:** Expo 57, Expo Router, React Native 0.86 / React Native Web, TypeScript, Vitest (node environment, `src/**/*.test.ts`), new dependency `react-native-ble-plx` (with its Expo config plugin).

**Spec:** `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md` (sections 5.2, 5.3, 5.4, 5.5, 5.6). Server API: `platform/docs/API.md`.

All paths are relative to `platform/web` unless they start with `platform/` or `docs/`. Commands run from `platform/web`; Node is at the winget install path (see `.claude/launch.json` in the repo root).

## Global Constraints

- Design system: `src/theme/tokens.ts` only (dark, violet `#5B2EFF`, system font); no raw hex in screens.
- Every visible button performs a real server or Bluetooth action, or navigates to a screen that does. No "Soon", no hard-coded numbers presented as data, no demo data presented as real.
- Bluetooth is phone-only. On the web, Bluetooth buttons are **hidden** (not disabled); the Lockets screen says "Pair from the ASK phone app".
- Bluetooth service and characteristic UUIDs, commands and payloads exactly as spec 5.3. Frames: the existing binary format in `src/domain/device/framing.ts` (42-byte header + payload, CRC32), one frame per notification.
- AUDIO payload (fixed here, spec updated in Task 1): 4-byte block header `predictor int16 little-endian, stepIndex uint8, reserved 0`, then 128 bytes of IMA ADPCM nibbles (low nibble first) = 256 samples at 16 kHz. Frame = 174 bytes, so the phone requests MTU 185.
- Never store the CSRF token or session cookie in AsyncStorage; native keeps the token in `expo-secure-store`, web keeps it in memory (re-read from `GET /api/auth/me`).
- Upload client recording ID = the locket session ID (16 ASCII characters, `[A-Za-z0-9_-]`), so re-uploading is idempotent on the server.
- Local backup paths (phone): `<documentDirectory>/ASK/<recordingId>/audio.wav`, `transcription.txt`, `summary.txt`. Web: the two `.txt` files download through the browser.
- Copy: calm, no em dashes, no model or library names on screen ("speech-to-text", "summary").

## Review Focus

- The session expires while the app is open: every screen must send the person to Sign in, not show an empty list or a crash. → Task 2 test `client_raises_unauthenticated_and_session_signs_out`.
- The phone loses the locket mid-meeting and reconnects: frames already received must not be requested again, missing ones must be (RESEND from `nextSequence`), and the finished WAV must have no gap or duplicate. → Task 5 test `resumes_after_a_disconnect_without_gaps_or_duplicates`.
- The emailed code is typed wrong in the system pairing box: the app must ask for a new code (new nonce), never retry the old one. → Task 4 test `a_failed_bond_asks_for_a_new_code`.
- Upload fails (no network): the WAV must stay on the phone and be retried on the next sync, and a second sync of the same session must not create a second recording. → Task 6 test `failed_upload_is_kept_and_retried_once_online`.
- The summary is not ready yet: Save files must save `transcription.txt` and say the summary is still being written, not fail. → Task 6 test `saves_the_transcript_even_when_the_summary_is_not_ready`.

---

### Task 1: Spec update, web proxy and API base

**Files:**
- Modify: `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md` (5.3: binary frame format, AUDIO payload layout, MTU)
- Modify: `serve.mjs` (forward `/api/*` to `API_ORIGIN`, default `http://localhost:8000`, streaming bodies both ways, `X-Forwarded-For` set, `Host` rewritten)
- Create: `src/infrastructure/api/base.ts`

**Interfaces:**
- Produces: `apiBase(): string` — `process.env.EXPO_PUBLIC_API_URL` without a trailing slash, or `""` (same origin, the web build behind `serve.mjs`).

- [ ] **Step 1:** update the spec section; implement the proxy (Node `http.request`, pipe request and response, 502 JSON `{error:{code:"api_unreachable"}}` when the API is down).
- [ ] **Step 2: Verify:** `npm run build:web`, start the API on 8000 and `node serve.mjs`; `curl -s localhost:3000/api/health` returns the API's health JSON; with the API stopped it returns 502 `api_unreachable`. (Manual check; `serve.mjs` is outside the Vitest scope.)

### Task 2: HTTP client, endpoints and sign-in session

**Files:**
- Create: `src/infrastructure/api/client.ts`, `src/infrastructure/api/endpoints.ts`, `src/infrastructure/api/types.ts`, `src/infrastructure/api/tokenStore.ts` (+ `tokenStore.native.ts`)
- Create: `src/application/session.tsx`
- Test: `src/infrastructure/api/client.test.ts`, `src/infrastructure/api/endpoints.test.ts`

**Interfaces:**
- Produces: `class ApiError extends Error { status: number; code: string }`; `createClient({ base, fetchImpl, tokens }): ApiClient` with `request<T>(method, path, opts?: { json?: unknown; body?: BodyInit; headers?: Record<string,string>; raw?: boolean }): Promise<T>` (credentials `include`; `X-CSRF-Token` on POST/PUT/PATCH/DELETE from `tokens.get()`; JSON errors become `ApiError`; 204 returns `undefined`; `raw` returns the `Response`).
- `TokenStore { get(): string | null; set(token: string | null): Promise<void>; load(): Promise<void> }`.
- `endpoints(client)` returning typed calls for every route the screens use, with paths copied from `platform/docs/API.md`: auth (`login`, `me`, `logout`, `requestPasswordReset`, `changePassword`), recordings (`listRecordings({ q?, page? })`, `getRecording`, `getTranscript`, `transcriptionText`, `summaryText`, `deleteRecording`, `reprocess`, `audioUrl`), lockets (`listLockets`, `registerLocket`, `requestPairingCode`, `confirmPaired(productId, nonce, proof)`, `removeMember`, `setLimit`, `uploadRecording(productId, clientId, wav: Uint8Array, recordedAt: Date)` with `X-Content-SHA256`), firmware (`latestFirmware`), team (`listMembers`, `invite`, `listInvitations`, `cancelInvitation`, `updateMember`), `workspaceSummary`. Types in `types.ts` mirror the server's camelCase JSON (read the Pydantic schemas in `platform/api/app/schemas`).
- `SessionProvider`, `useSession(): { status: "loading" | "signedOut" | "signedIn"; user: UserOut | null; signIn(email, password): Promise<void>; signOut(): Promise<void>; refresh(): Promise<void> }`; any `ApiError` with status 401 anywhere sets `signedOut`.

- [ ] **Step 1: Failing tests:** `sends_credentials_and_csrf_on_writes_only`, `turns_error_json_into_ApiError`, `client_raises_unauthenticated_and_session_signs_out` (401 calls the `onUnauthenticated` hook), `login_stores_the_csrf_token_from_the_body`, `upload_sends_wav_with_sha256_and_recorded_at` (SHA-256 hex of the bytes, ISO time), `paths_match_the_api_reference` (each endpoint hits the documented method + path).
- [ ] **Step 2:** `npx vitest run src/infrastructure/api` — FAIL. **Step 3:** implement (SHA-256 via `crypto.subtle.digest`). **Step 4:** PASS, then `npm run check`.

### Task 3: Locket protocol pieces (ADPCM, commands, status)

**Files:**
- Create: `src/domain/locket/adpcm.ts`, `src/domain/locket/commands.ts`, `src/domain/locket/uuids.ts`, `src/domain/locket/link.ts`, `src/domain/locket/version.ts`
- Test: `src/domain/locket/adpcm.test.ts`, `src/domain/locket/commands.test.ts`

**Interfaces:**
- Produces: `decodeAdpcmBlock(payload: Uint8Array): Int16Array` (132-byte payload → 256 samples; throws on wrong length) and `encodeAdpcmBlock(samples: Int16Array, state: { predictor: number; index: number }): Uint8Array` (reference for tests and firmware vectors); standard IMA step and index tables.
- `commands`: `recStart() => "REC START"`, `recStop()`, `ack(sessionId, seq)`, `resend(sessionId, fromSeq)`, `time(ms)`, `wifi(ssid, password)` (UTF-8 hex), `wifiClear()`, `ota(version, size, sha256, url)` (url UTF-8 hex), `forget()`; `parseInfo("ASKINFO/1 ASK-7KQ2MX 1.0.0 87 1") => { productId, firmwareVersion, batteryPercent, bonded }`; `parseStatus("ASKSTAT/1 recording 87 SESSIONID0000001 120 0") => { state, batteryPercent, sessionId, lastSeq, otaPercent }`; both return `null` for anything malformed.
- `uuids`: the six UUIDs from the spec, lower-case.
- `LocketLink` interface: `scan(onFound: (d: { id: string; name: string; rssi: number }) => void): () => void`, `connect(id): Promise<void>`, `disconnect(): Promise<void>`, `readInfo(): Promise<string>`, `readNonce(): Promise<Uint8Array>`, `bond(): Promise<void>` (triggers the OS passkey box by reading an encrypted characteristic; rejects if the passkey is wrong), `readProof(): Promise<string>`, `send(command: string): Promise<void>`, `onStatus(cb: (text: string) => void): () => void`, `onAudio(cb: (frame: Uint8Array) => void): () => void`.
- `compareVersions(a, b): -1 | 0 | 1` for `MAJOR.MINOR.PATCH`.

- [ ] **Step 1: Failing tests:** `round_trips_a_sine_within_tolerance` (encode then decode a 1 kHz sine; max error < 2% of full scale), `decodes_a_known_block` (fixed 132-byte vector → first 8 samples exact), `rejects_wrong_block_length`, `formats_commands_exactly`, `hex_encodes_wifi_and_ota_arguments`, `parses_info_and_status_and_rejects_garbage`, `compares_versions_numerically` (1.10.0 > 1.9.0).
- [ ] **Steps 2–4:** run (FAIL), implement, run `npx vitest run src/domain/locket` (PASS) and `npm run check`.

### Task 4: Pairing with the emailed code

**Files:**
- Create: `src/application/pairLocket.ts`
- Test: `src/application/pairLocket.test.ts`

**Interfaces:**
- Consumes: `LocketLink` (Task 3), `endpoints` (Task 2).
- Produces: `pairLocket({ link, api, deviceId, productId, onCodeSent }): Promise<PairOutcome>` where `PairOutcome = { ok: true } | { ok: false; reason: "locket_full" | "wrong_code" | "proof_rejected" | "expired" | "network" ; message: string }`. Order: connect → readNonce → `requestPairingCode(productId, hex(nonce))` → `onCodeSent(sentTo)` → `bond()` → `readProof()` → `confirmPaired(productId, hex(nonce), proof)`. A rejected `bond()` disconnects and returns `wrong_code` (the next attempt reads a new nonce).

- [ ] **Step 1: Failing tests** (fake link + fake api): `pairs_in_order_and_sends_the_proof`, `a_full_locket_stops_before_bonding`, `a_failed_bond_asks_for_a_new_code` (second call reads a different nonce), `a_rejected_proof_is_reported`, `an_expired_request_is_reported`.
- [ ] **Steps 2–4:** run, implement, `npx vitest run src/application/pairLocket.test.ts` PASS.

### Task 5: Receiving a recording over Bluetooth

**Files:**
- Create: `src/application/captureRecording.ts`
- Test: `src/application/captureRecording.test.ts`

**Interfaces:**
- Consumes: `AskSessionAssembler` (`src/domain/device/sessionAssembler.ts`), `decodeAdpcmBlock`, `commands`, `pcmToWav` (existing `src/infrastructure/asr/pcmToWav.ts`; move it to `src/domain/audio/wav.ts` if it pulls in anything platform-specific).
- Produces: `class RecordingCapture { constructor(link: LocketLink, onDone: (r: CapturedRecording) => void); start(): () => void; resumeFrom(): { sessionId: string; seq: number } | null }` with `CapturedRecording = { sessionId: string; startedAt: Date; wav: Uint8Array; durationMs: number }`. It ACKs every 32 frames and at END; on reconnect (a STATUS for the same session) it sends `RESEND <session> <nextSequence>`.

- [ ] **Step 1: Failing tests:** `assembles_a_session_into_a_wav` (START + 10 AUDIO + END → 2560 samples, 16 kHz mono header), `acks_every_32_frames_and_at_the_end`, `resumes_after_a_disconnect_without_gaps_or_duplicates`, `ignores_corrupt_frames_and_asks_for_them_again`.
- [ ] **Steps 2–4:** run, implement, PASS.

### Task 6: Upload, local backup and firmware offer

**Files:**
- Create: `src/application/syncRecording.ts`, `src/application/firmwareUpdate.ts`, `src/infrastructure/files/localFiles.ts` (+ `.native.ts`, `.web.ts`)
- Test: `src/application/syncRecording.test.ts`, `src/application/firmwareUpdate.test.ts`

**Interfaces:**
- Produces: `LocalFiles { write(path: string, data: Uint8Array | string): Promise<void>; read(path): Promise<Uint8Array | null>; list(dir): Promise<string[]>; remove(path): Promise<void> }` (native: `expo-file-system` under `documentDirectory`; web: `write` downloads the file, the rest are no-ops returning empty).
- `syncRecording({ api, files, productId, captured }): Promise<{ recordingId: string }>` — writes `ASK/pending/<sessionId>.wav` first, uploads, then moves it to `ASK/<recordingId>/audio.wav`; `retryPending({ api, files, productId })` uploads every leftover pending WAV.
- `saveTextFiles({ api, files, recordingId }): Promise<{ transcript: boolean; summary: boolean | "not_ready" }>`.
- `firmwareOffer(api, info): Promise<FirmwareLatest | null>` (null unless the server's latest is newer than `info.firmwareVersion`); `startFirmwareUpdate({ link, latest, wifi?: { ssid; password } })` sends `WIFI` (when given) then `OTA`.

- [ ] **Step 1: Failing tests:** `uploads_and_keeps_audio_under_the_recording_id`, `failed_upload_is_kept_and_retried_once_online`, `saves_the_transcript_even_when_the_summary_is_not_ready`, `offers_only_newer_firmware`, `sends_wifi_then_ota`.
- [ ] **Steps 2–4:** run, implement, PASS.

### Task 7: Screens wired to the server; demo screens removed

**Files:**
- Modify: `src/app/_layout.tsx` (wrap in `SessionProvider`; redirect to `/sign-in` when signed out; drop `initializeLiveStore`)
- Create: `src/app/sign-in.tsx`, `src/app/forgot-password.tsx`, `src/app/recording/[id].tsx`, `src/app/lockets/index.tsx`, `src/app/lockets/[productId].tsx`, `src/app/team.tsx` (rewrite), `src/app/account.tsx`
- Modify: `src/app/(tabs)/home.tsx` (Today: locket card from `listLockets` + Bluetooth status on phones; Start/Stop on phones; latest 5 recordings), `src/app/(tabs)/activity.tsx` (all recordings, paged, with state), `src/app/(tabs)/ask.tsx` (search with `listRecordings({ q })`), `src/app/(tabs)/you.tsx` (Lockets, Team for managers, Account, Sign out)
- Delete: `src/app/{understand,memory-graph,needs-you,assign-follow-up,agents-settings,people,your-data,device-settings,record}.tsx`, `src/app/conversation/`, `src/app/person/`, `src/app/onboarding/`, and any `src/components` or `src/data` module that nothing imports afterwards (check with `npm run check` and a grep)
- Create: `src/infrastructure/ble/getLocketLink.ts` (+ `.native.ts` with `BlePlxLocketLink`, `.web.ts` returning `null`), `app.json` plugin entry for `react-native-ble-plx`

**Interfaces:**
- Consumes everything above. Recording screen buttons: Play (audio URL), Save files (`saveTextFiles`), Process again (managers; `reprocess`), Delete (managers; confirm first). Locket screen buttons: Pair (phones), Update firmware (when offered; asks for Wi-Fi name and password once), Raise limit (admins), Remove person (managers, or yourself as "Forget this locket"), Register locket (admins; shows the secret once with a copy button and the `provision` line).

- [ ] **Step 1:** implement screens; keep the tab bar (Today, Ask, Activity, You).
- [ ] **Step 2: Verify:** `npm run check` and `npm test` pass; grep shows no `Soon`, `onPress={() => {}}`, or imports of deleted modules.
- [ ] **Step 3: Verify in the browser** against a local API (SQLite, first admin from `python -m app.cli bootstrap`, a registered locket, one recording uploaded through `PUT /api/lockets/.../recordings/...` with a test WAV and a transcript written by the stub worker): sign in, Today, Activity, open the recording, Save files downloads `transcription.txt`, Ask finds it, Lockets lists the locket and an admin can raise the limit, Team invites, Account changes the password, Sign out returns to Sign in. Record what was clicked and seen.

### Task 8: Docs and full check

**Files:**
- Modify: `platform/web/README.md`, `platform/docs/DEMO.md`, `CLAUDE.md` (app section)

- [ ] **Step 1:** document running the app with the API (`npm run build:web`, `API_ORIGIN=... node serve.mjs`), the phone build requirement for Bluetooth (`npx expo run:android|ios` after `npm install`), and what was not run (no phone, no locket, no Bluetooth on this machine).
- [ ] **Step 2:** `npm run check && npm test && npm run build:web` — all pass (report counts).
