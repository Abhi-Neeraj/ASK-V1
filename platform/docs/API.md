# ASK platform API reference

Base URL in development: `http://localhost:8000`. The web app proxies `/api/*` to it, so a browser only ever talks to `http://localhost:3000`. Interactive OpenAPI docs are served at `/api/docs` (schema at `/api/openapi.json`) outside production.

The route list below was generated from the running application, not written by hand. If the code and this page disagree, the code wins and this page is a bug.

## Conventions

- JSON bodies and responses use **camelCase**. Timestamps are ISO 8601 in UTC.
- Errors always use one envelope:

  ```json
  { "error": { "code": "validation_error", "message": "Some fields are not valid.", "details": { "retentionDays": "Input should be greater than or equal to 1" } } }
  ```

  `details` is present only for field-level validation errors (field name to message).
- A date or time sent without a time zone (`?dateFrom=2026-10-01`) is read as UTC.
- Ordinary (JSON) requests are limited to `ASK_MAX_JSON_BYTES` (1 MiB); larger ones are refused with `413 body_too_large` before they are read. Audio uploads have their own limits (below).
- Lists that can grow are paginated: `?page=1&pageSize=25`, answered as `{ items, page, pageSize, total }`.
- A missing object, an object in another company and a soft-deleted object all answer `404 not_found`. The API never confirms that something exists in a company you do not belong to.

## Two ways to authenticate

| Caller | How | CSRF |
| --- | --- | --- |
| Web app (people) | `ask_session` cookie: HTTP-only, SameSite=Lax, `Secure` in production. Only a hash of the token is stored. | Required on `POST`, `PUT`, `PATCH`, `DELETE`. Copy the `ask_csrf` cookie into the `x-csrf-token` header. The `Origin` header must be one of `ASK_WEB_ORIGINS`. |
| Locket | `Authorization: Bearer <device id>.<secret>`. The secret is returned once, at pairing, and only a hash is stored. | Not applicable (no cookies are accepted on `/api/device/v1`). |

Sessions end after `ASK_SESSION_IDLE_HOURS` without use or `ASK_SESSION_TTL_HOURS` in total. Changing or resetting a password ends the other sessions.

## Roles and visibility

| Role | Can do |
| --- | --- |
| `admin` | Everything a manager can, plus `PATCH /api/settings`, changing other people's role or switching them off, and reading the audit log. |
| `manager` | Create, pair, assign and revoke lockets; invite and revoke invitations; people and consent; delete and reprocess recordings; download audio files. |
| `employee` | See only the lockets assigned to them now, and the recordings that **arrived while a locket was assigned to them**: what a locket recorded stays with whoever had it, and passing the locket on does not pass its history on. Play audio, read transcripts, correct transcript text, connect and disconnect their own lockets. |

Membership comes only from an invitation or from company bootstrap (`python -m app.cli bootstrap`). It is never inferred from an email domain.

## Endpoints

"Public" needs no sign-in. Everything else needs a session, at the least role shown.

### Health

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/health` | Public | Process is up. |
| `GET /api/ready` | Public | `200` only when the database answers and audio storage is writable. Otherwise `503` with `database_unavailable` or `storage_unavailable`. Use this for load-balancer and container health checks. |

### Accounts

| Method and path | Access | Notes |
| --- | --- | --- |
| `POST /api/auth/login` | Public | `{email, password}` returns the user and sets both cookies. Wrong email or password is always `401 invalid_credentials`, identical for unknown addresses. Rate limited per source address plus email, and per source address overall. A failed attempt on a real member's account is written to that company's audit log. |
| `POST /api/auth/logout` | Session | Ends the session on the server and clears cookies. |
| `GET /api/auth/me` | Employee | Current user, company and role. Also refreshes the CSRF cookie. |
| `POST /api/auth/password-reset/request` | Public | Always `204`, whether or not the email exists. Sends an email only if it does. |
| `POST /api/auth/password-reset/confirm` | Public | `{token, password}`. Ends every session of that user. |
| `POST /api/auth/change-password` | Employee | `{currentPassword, newPassword}`. Keeps only the current session. |
| `POST /api/auth/verify-email/request`, `.../confirm` | Employee / Public | Email verification links. |
| `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{sessionId}` | Employee | List and end your own sessions. |
| `GET /api/invitations/{token}` | Public | What the invitation is for (company, role, email). |
| `POST /api/invitations/{token}/accept` | Public | `{name, password}`. Creates the account and signs in. |
| `POST /api/invitations`, `GET /api/invitations`, `DELETE /api/invitations/{id}` | Manager | `{email, role}`. Managers may invite employees only (`403 forbidden` otherwise); administrators may invite any role. An address that already has an account is `409 account_exists`. A new invitation to the same address replaces any older pending one. The accept link (`/register?token=...`) is emailed. **When mail really goes out (`ASK_EMAIL_BACKEND=smtp`) it is not in the response**, so the person who invites cannot accept it themselves; with the development mail backends `acceptUrl` is returned once so a developer can click it. At most `ASK_INVITES_PER_HOUR` (30) invitations per person per hour (`429`). Lists show only invitations that are pending and unexpired. |
| `GET /api/members` | Employee | People in your company: `name`, `role`, `isActive`, and `email`. Email addresses are sent only to managers and administrators (everyone else gets `null`), and people who have been switched off are listed only for managers and administrators. |
| `PATCH /api/members/{userId}` | Admin | `{role?, isActive?}`, at least one. Changes a person's role (effective at once, no sign-out needed) or switches them off or back on. Switching off ends all their sessions and stops them signing in; their recordings, and the lockets assigned to them, are left alone. Never for yourself (`409 cannot_change_self`), so an administrator is always left. `409 shared_account` if the account also belongs to another company. A switched-off person cannot be given a locket. Audited. |
| `DELETE /api/members/{userId}/sessions` | Admin | Signs a person out everywhere without switching them off (`204`). Not for yourself. Audited. |

### Lockets (web)

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/devices` | Employee | Managers: every locket. Employees: only their own. `status` is `online`, `offline`, `pending` (not paired yet) or `revoked`. `simulated` is true for development stand-ins. |
| `POST /api/devices` | Manager | `{name}`. Returns a one-time pairing code (`code`, `expiresAt`). |
| `GET /api/devices/{id}`, `GET /api/devices/{id}/status` | Employee | Same document. |
| `PATCH /api/devices/{id}` | Manager | `{name?, assignedUserId?}`. The user must belong to your company. Reassigning removes the previous user's connection. |
| `POST /api/devices/{id}/pairing` | Manager | A fresh code for a locket that is still `pending`. `409 already_paired` once it is paired, `409 device_revoked` if it was revoked. |
| `POST /api/devices/{id}/revoke` | Manager | Invalidates the credential at once, ends everyone's connection and voids unused pairing codes. Recordings already uploaded are kept. A revoked locket cannot be paired again; add the hardware as a new locket. |
| `POST /api/devices/{id}/connect` | Employee | Only while the locket is online, otherwise `409 device_offline`. Application-level only; nothing is sent to the hardware. |
| `POST /api/devices/{id}/disconnect` | Employee | Ends your connection. Recordings are untouched. |
| `GET /api/devices/{id}/events` | Manager | Last 100 lifecycle events. |

A locket is `online` while its last authenticated heartbeat is younger than `ASK_HEARTBEAT_EXPIRY_SECONDS` (default 90). That is the only signal used. A browser cannot see a Wi-Fi device directly, and the platform does not claim to.

### Bluetooth lockets (`/api/lockets`, by product ID)

A locket's product ID (`ASK-` and six characters) is fixed when it is registered and can never change (the
database refuses it). People on a locket are limited per locket: 1 by default, raised only by an
administrator. Pairing a phone needs a six-digit code the server emails to the person; the locket checks the same
code itself, with no internet (see `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md`).

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/lockets` | Employee | Managers: every locket. Others: only lockets they are on. `{productId, name, maxUsers, members:[{id, name, email}], firmwareVersion, batteryPercent, lastSeenAt}`. |
| `POST /api/lockets` | Admin | `{name}`. Returns `{productId, name, secretHex}` **once**: write the secret into the locket over USB (`provision <productId> <secretHex>`). `503 locket_keys_missing` until `ASK_LOCKET_MASTER_KEY` is set. |
| `POST /api/lockets/{productId}/pairing-code` | Employee | `{nonce}` (32 hex characters read from the locket). Emails the caller the code; returns `{sentTo, expiresAt}` (address masked, never the code). `409 locket_full` (and no email) when the locket is full and the caller is not on it. 5 per person and 10 per locket per hour, counted before the locket is looked up. Only Bluetooth lockets (registered here); Wi-Fi devices answer 404. |
| `POST /api/lockets/{productId}/paired` | Employee | `{nonce, proof}` after Bluetooth pairing succeeded; `proof` is the 64-hex value the locket shows on its encrypted BOND_PROOF characteristic. Puts the caller on the locket. `403 pairing_proof_invalid` without the real locket's proof; `404 pairing_request_not_found` if the request expired (10 minutes) or was used; `409 locket_full` if the locket filled up meanwhile. |
| `DELETE /api/lockets/{productId}/members/{userId}` | Employee (self), Manager (anyone) | Takes someone off the locket and ends their connection. Recordings that arrived while they were on stay visible to them. |
| `PATCH /api/lockets/{productId}/limit` | Admin | `{maxUsers}` 1 to 50. `409 limit_below_members` when lower than the people already on it. |
| `PUT /api/lockets/{productId}/recordings/{clientRecordingId}` | Employee (on the locket) | The phone uploads a WAV the locket sent it over Bluetooth. Same headers and answers as the locket's own upload (`X-Content-SHA256`, `X-Recorded-At`; `201` stored, `200` already stored). Everyone on the locket at that moment may see the recording; people who join later do not. Cookie session and `X-CSRF-Token` (the phone takes the token from `csrfToken` in the sign-in answer). |

### Firmware updates over Wi-Fi (`/api/firmware`)

| Method and path | Access | Notes |
| --- | --- | --- |
| `PUT /api/firmware/{version}` | Admin of the publisher company | The raw `.bin` as the body (`application/octet-stream`). `version` is `MAJOR.MINOR.PATCH`. Returns `{version, sizeBytes, sha256}`. `403 firmware_publisher_only` unless your company is `ASK_FIRMWARE_PUBLISHER_COMPANY_ID` (unset: nobody). `409 version_exists`, `413 upload_too_large` above `ASK_FIRMWARE_MAX_BYTES` (4 MiB). |
| `GET /api/firmware/latest` | Employee | The highest version, `{version, sizeBytes, sha256, downloadUrl}`. `downloadUrl` carries a signed token valid for `ASK_FIRMWARE_LINK_MINUTES` (30); the phone hands it to the locket over Bluetooth with the size and SHA-256. `404 no_firmware` before the first upload. |
| `GET /api/firmware/{version}/download` | Public, signed link | `?token=` from `latest`. The locket downloads over Wi-Fi with no account. `403 link_invalid` / `403 link_expired`. |

### Locket API (`/api/device/v1`, bearer credential)

| Method and path | Notes |
| --- | --- |
| `POST /pair` | `{code, firmwareVersion?, simulated?}`. Redeems a one-time code. Returns `{deviceId, credential, config}`. The credential is shown once. Wrong, expired or used codes all answer `400 invalid_pairing_code`. Rate limited per address. `simulated: true` is sent only by the development simulator (`tools/simulate_device.py`); real firmware leaves it out. A server that accepts only real lockets (production, unless `ASK_ALLOW_SIMULATED_DEVICES=true`) answers `422 simulation_not_allowed` and does not use up the code. A locket's `simulated` flag is fixed when it pairs and is shown on its card, on its recordings and in the connection prompt. |
| `POST /heartbeat` | `{firmwareVersion?, batteryPercent?, uploadQueueDepth?}`. Returns `{serverTime, config}`. `config` is `{chunkSeconds, heartbeatIntervalSeconds, sampleRates, maxUploadBytes, maxClipSeconds}`, so the locket learns the server's limits instead of hard-coding them. `maxClipSeconds` (the longest single clip, 600 by default) is what lets a button-controlled locket cut a long recording into clips the server will take; firmware that predates the field ignores it. |
| `PUT /recordings/{clientId}` | Upload one WAV (below). |
| `GET /recordings/{clientId}` | Ask whether a clip arrived, for example after losing the response. `404` if it did not. |

#### Uploading a clip

`PUT /api/device/v1/recordings/{clientId}` with the raw WAV as the body.

| Header | Required | Meaning |
| --- | --- | --- |
| `Authorization` | yes | `Bearer <device id>.<secret>` |
| `Content-Type` | yes | `audio/wav` (also `audio/x-wav`, `audio/wave`, `application/octet-stream`) |
| `X-Content-SHA256` | yes | Hex SHA-256 of the body |
| `X-Recorded-At` | yes | ISO 8601 start time with time zone |
| `X-Session-Ref`, `X-Sequence` | no | Group and order the parts of one long recording |

`clientId` is chosen by the locket: 8 to 64 letters, digits, `-` or `_`, unique per locket.

A recording longer than `maxClipSeconds` is uploaded as several clips, each with its own `clientId`, the same `X-Session-Ref` (1 to 64 letters, digits, `-`, `_`) and an increasing `X-Sequence` (a whole number below 1,000,000). The button-controlled firmware (`platform/firmware/locket_web_upload`) does this. The website lists each clip as its own recording; the recordings list takes a `sessionRef` filter that returns the clips of one recording, and each clip carries `part` (1, 2, 3 … for a clip that belongs to a longer recording, null otherwise).

The success body is the receipt (`UploadAck`):

```json
{"recordingId": "uuid", "clientRecordingId": "r…", "status": "stored", "sha256": "hex", "bytes": 123, "durationMs": 4560, "duplicate": false, "startedAt": "2026-01-01T00:00:00Z"}
```

The locket does not delete its copy on the status code alone. A gateway or a captive portal can answer `200` to anything. `locket_web_upload` deletes only when the body says `status: "stored"`, names this `clientId`, carries a `recordingId` shaped like a UUID, and repeats the SHA-256 and the byte count it sent (`ask::ack_validate` in its `core.cpp`). Anything else is treated as "not acknowledged": the file is kept and sent again, which is safe because the upload is idempotent.

The server writes the file to a temporary name, flushes it, checks the SHA-256 and the WAV header, renames it into place, and only then commits the database row and answers. So an acknowledgement means the clip is durable.

| Status | Meaning | Locket should |
| --- | --- | --- |
| `201` | Stored | Delete its copy |
| `200` | This exact clip was already stored (`duplicate: true`) | Delete its copy |
| `409 recording_conflict` | Same `clientId`, different content | Use a new `clientId` |
| `422 checksum_mismatch` | Bytes arrived damaged | Retry |
| `422 invalid_audio` | Not a WAV the server accepts (rate, length, header) | Do not retry the same file |
| `413` / `415` / `408` | Too large / wrong type / too slow | Fix or retry later |
| `401 invalid_device_credential` | Credential unknown or revoked | Stop and re-pair |
| `403` | Never sent by the server for a locket; it comes from something in front of it | Keep the file, retry only slowly; re-pairing would not help |
| `429 rate_limited` | Too many requests | Back off |
| anything else | Not acknowledged | **Keep the file and retry** |

### Live updates

`GET /api/events` is a [server-sent events](https://html.spec.whatwg.org/multipage/server-sent-events.html) stream (`text/event-stream`) that tells a signed-in browser what changed, so screens update without reloading. Authenticate with the session cookie, as the browser's `EventSource` does. It is read-only, so there is no CSRF header.

- **Same-origin only.** A request that names an unexpected `Origin` is refused with `403 bad_origin`, like every other browser-facing route.
- **Scoped.** Only events from the signed-in person's company are sent. Managers hear everything in it. Employees hear about the lockets assigned to them (and about being given or losing one) and about recordings that are theirs, the same boundary as the REST API.
- **Resumable.** Every event has an `id`. After a dropped connection, browsers send it back as `Last-Event-ID` and the server replays what was missed. Scripts can pass `?lastEventId=` instead. A fresh connection starts from now and does not replay history. A malformed or future ID is ignored, never an error. Events are kept for `ASK_LIVE_EVENT_RETENTION_HOURS` (default 24); a client that was away longer should refetch everything.
- **Bounded.** A stream ends itself after `ASK_EVENT_STREAM_MAX_SECONDS` (default 600) with a `stream.renew` event and the browser reconnects with no loss. At most `ASK_MAX_EVENT_STREAMS_PER_USER` (default 5) streams per person and `ASK_MAX_EVENT_STREAMS` (default 500) per process are allowed; beyond that the answer is `429 too_many_streams` with `Retry-After: 5`. The server checks every few seconds that the sign-in is still valid, and ends the stream with `session.ended` when it is not (signed out, revoked, expired, idle, or deactivated).
- **Proxy-safe.** The response carries `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`. A comment line is sent every `ASK_EVENT_KEEPALIVE_SECONDS` (default 15) so quiet connections are not closed.

Each message looks like this. `data` is one line of JSON: `{"type", "at", "data"}`.

```text
id: 214
event: device.online
data: {"type":"device.online","at":"2026-10-25T15:03:39.640000+00:00","data":{"deviceId":"…","name":"Front desk"}}
```

| Event | `data` | Sent when |
| --- | --- | --- |
| `device.online` | `deviceId`, `name` | A locket's heartbeat arrives after it was offline or just paired. Announced once per change. |
| `device.offline` | `deviceId`, `name` | Heartbeats stopped for longer than `ASK_HEARTBEAT_EXPIRY_SECONDS`. Announced once. |
| `device.paired` | `deviceId`, `name` | A locket redeemed its pairing code. |
| `device.assigned` | `deviceId`, `name`, `from`, `to` | Who wears the locket changed. `from` and `to` are user IDs or null. |
| `device.revoked` | `deviceId` | A locket was revoked. |
| `device.connected`, `device.disconnected` | `deviceId`, `userId` | Someone connected to or disconnected from a locket. |
| `recording.uploaded` | `recordingId`, `deviceId`, `wornBy` | A clip was stored. `wornBy` is the user the locket was assigned to when it arrived, or null; the feed uses it to decide who hears about this recording. |
| `recording.state` | `recordingId`, `deviceId`, `wornBy`, `state` | Processing state changed (today only a reprocess request; the worker adds the rest in milestone E). |
| `recording.deleted` | `recordingId`, `deviceId`, `wornBy` | A recording was deleted. |
| `stream.renew` | none | The server is about to end this stream on schedule. Reconnect (browsers do it by themselves). |
| `session.ended` | none | The sign-in is no longer valid. Stop and sign in again. |

Events say *that* something changed, not everything about it. The web app refetches the affected lists when one arrives, and polls every 15 seconds while the stream is down.

### Recordings

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/recordings` | Employee | Filters: `q` (transcript text), `deviceId`, `personId`, `state`, `dateFrom`, `dateTo`, `minDurationMs`, `maxDurationMs`, `sessionRef`. Sorting: `sort` (default `startedAt`) and `order` (`asc` or `desc`, default `desc`). Paging: `page`, `pageSize` (default 20, at most 100). |
| `GET /api/recordings/{id}` | Employee | Metadata, processing state and speakers. No file paths are ever returned. `simulated` is true when a simulated locket made it, so synthetic audio is never mistaken for a real recording. `placeholder` is true when its transcript was written by the development stub rather than by speech recognition. `failureDetail` says why processing failed or is being retried (cleaned of paths and secrets); it is set only for managers and administrators and only while the recording is `failed` or `retrying`, and is `null` for everyone else. |
| `GET /api/recordings/{id}/audio` | Employee | Streams audio and supports `Range`. The first request for a clip (no `Range`, or one starting at byte 0) is written to the audit log as `recording.played`; `?download=true` is manager-only, sends it as an attachment and is audited as `recording.downloaded`. Both stream the same bytes, so the restriction is a marker and an audit trail, **not** a way to stop someone who can listen from keeping a copy. |
| `GET /api/recordings/{id}/transcript` | Employee | Segments with timestamps, speaker and corrected text, plus `placeholder` (not real speech recognition; nobody is named), `language` (null if not detected), `diarizationModel` (null when speakers were not separated, so every line is one speaker) and `separationExperimental` (true when who-spoke-when came from the experimental, token-free method rather than the full speaker-separation model: the dashboard then tells people to check the labels by listening). A line's `person` is set only when a voice matched an enrolled, consenting person of the same company above the threshold and clear of the runner-up; `confidence` is that match score (0 to 1) and is not a probability that the name is right. |
| `GET /api/recordings/{id}/transcription.txt` | Employee | The transcript as a plain-text download, one line per segment: `[HH:MM:SS] Speaker 1: text` (a matched person's name instead of the number). Placeholder transcripts start with `PLACEHOLDER: not a real transcript`. `404 transcript_not_ready` until there is one. The phone saves it as its local backup. |
| `GET /api/recordings/{id}/summary.txt` | Employee | The written summary (Gemini, with `ASK_AI_PROVIDER=google`) as a plain-text download. `404 transcript_not_ready` / `404 summary_not_ready` until made. |
| `PATCH /api/recordings/{id}/transcript` | Employee | `{edits: [{segmentId, text}]}`. The original text is kept next to the correction. |
| `DELETE /api/recordings/{id}` | Manager | Soft delete. The audio and transcript are purged after `purgeGraceDays`. |
| `POST /api/recordings/{id}/reprocess` | Manager | Queues a new processing job (`202`). `409 already_processing` if one is running. The new transcript **replaces** the old one, and corrections made to it are lost. |
| `GET /api/jobs/{id}` | Employee | Processing job status. `lastError` (the cleaned cause of the last failure) is returned to managers and administrators only; it is `null` for employees. |

`GET /api/recordings/{id}` also returns `analysis` (null while processing, for placeholder transcripts, and when it could not be made): `engine` (`semantic` when sentences were matched to activities by meaning and key phrases, `lexical` by key phrases and words only), `voices` (each speaker's `label`, `title`, `name`, `talkMs`, `share`, `turns`, `pitch` with base, low and high in hertz, `matchedBy` and, for managers only, `matchScore`), `summary` (`narrative` lines and `sections` of key lines: decisions, action items with `owner` and `due`, commitments, questions with `open`, requirements, issues and risks, approvals and rejections), `items` (every sentence with `what`, `context`, `activity`, `task`, `owner`, `due`, `dated` (an activity with a date or time), `verb` and `keywords`), `counts`, `contexts` (the topics in order, each with `group`, `startMs`, `endMs`, `sentences` and `keywords`) and `contextThreshold`. Each item's `context` also says which `group` it is in, whether it opens a `newContext` and its `similarity` to the closest of the three sentences before it. **`contexts` is empty and those three are null unless the sentence model ran** (or `ASK_CONTEXT_SIMILARITY` was set): the words engine cannot judge topics, so none is claimed. The summary is built by fixed rules and templates, with **no generative AI**: the same transcript always gives the same summary. A speaker is named only while the transcript still names them, so withdrawing consent removes the name from the summary at once. See [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md).

States: `uploaded`, `queued`, `validating`, `transcribing`, `diarizing`, `identifying_speakers`, `aligning`, `completed`, `failed`, `retrying`, `needs_review`. Recordings move through them only while a worker is running (`python -m app.worker`, see [AI.md](AI.md)); with none, they stay `queued`, and `GET /api/processing` says so. A stage appears only if it really runs: without speaker separation a recording goes from `transcribing` straight to `aligning`.

### Processing

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/processing` | Employee | Whether anything is processing recordings now, and what it can do. |

```json
{
  "available": true, "workers": 1, "placeholder": false,
  "transcription": true, "diarization": false, "speakerMatching": false,
  "queued": 2, "running": 1, "enrollmentAvailable": false,
  "notes": ["Speaker separation is unavailable: no Hugging Face token (ASK_HF_TOKEN). Every recording is treated as one speaker."]
}
```

`available` is true only while a worker has reported in within `ASK_WORKER_ONLINE_SECONDS`. `placeholder` means the running worker writes placeholder text. `diarization` and `speakerMatching` say whether speakers are separated and named. `queued` and `running` count this company's recordings. `enrollmentAvailable` says whether voice samples can be taken. `notes` is developer-facing and **can name libraries and settings**: managers and administrators get it, everyone else gets an empty list, and the web app never shows it.

### People and consent

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/people` | Employee | |
| `POST /api/people` | Manager | `{displayName}` (2 to 120 characters). Starts with consent `pending`. |
| `POST /api/people/{id}/consent` | Manager | `{granted, statement?}`. Appends a consent record (who recorded it, when, and the statement agreed to; a standard statement is used if none is given). `granted: false` withdraws consent, which also deletes the voice profile and stops attributing past transcript lines to that person. |
| `POST /api/people/{id}/enroll` | Manager | Adds one voice sample. The body is the WAV audio itself (`Content-Type: audio/wav`), 3 to 30 seconds. Returns the person. The audio is turned into a voice print and **deleted at once**. `409 consent_required` without recorded consent; `409 enough_samples` past the maximum; `501 not_available` (the message says what is missing) when no speaker model is set up; `422 sample_too_short`, `sample_too_long` or `unusable_sample`; `503 speaker_model_error` if the model itself failed; `415` if not WAV. Rate limited to 30 a minute per user. |
| `POST /api/people/{id}/signature` | Manager | Adds one voice sample to the person's **voice signature** (pitch plus voice colour; see [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md)). The body is the WAV audio itself (`Content-Type: audio/wav`), 3 to 30 seconds. Returns the person. Needs no speaker model: it is arithmetic on the audio, so it works whatever the processing provider is. The audio is measured and **deleted at once**. `409 consent_required` without recorded consent (checked again just before saving, so withdrawing consent in between wins); `409 enough_samples` past the maximum; `422 sample_too_short`, `sample_too_long`, `invalid_audio`, `silent_audio` or `not_enough_speech` (nothing voiced to measure); `415` if not WAV. Rate limited to 30 a minute per user. |
| `POST /api/people/signature-matches` | Manager | Upload a WAV (same rules) and see whom it is most like among the company's people **who have consent on record and enough signature samples**. Pitch is compared first (a much higher or lower voice is never a candidate), then voice colour. Returns `outcome` (`matched`, `no_confident_match` or `nobody_enrolled`), the sample's `pitch` (`baseHz`, `lowHz`, `highHz`) and `voicedSeconds`, `candidatesConsidered`, `match` (set only when the best candidate clears `ASK_SIGNATURE_MATCH_THRESHOLD` and beats the next by `ASK_SIGNATURE_MATCH_MARGIN`), `closest` (up to three, each with `score`, `pitchGapOctaves` and `passedPitchGate`; **being listed is not a match**) and a `notice` that a score is not identity verification. The upload is deleted at once; the lookup is audited as `voice_signature.matched` (outcome and candidate count only, never names or measurements). Rate limited to 20 a minute per user. |
| `DELETE /api/people/{id}/voice-profile` | Manager | Deletes the voice profile and its embeddings **and the voice signature**, and stops attributing past transcript lines to that person. |
| `DELETE /api/people/{id}` | Manager | Does the same, then deletes the person. Transcript text stays, with those lines shown as `Unknown speaker`. |

A person carries `enrolled` (ready: enough samples, made by the speaker model now in use), `enrolledAt`, `sampleCount`, `samplesRequired` and `voiceOutdated` (the speaker model changed since they enrolled, so their old voice print is no longer used until they enroll again). The voice print itself is never returned.

A person also carries `signature`: `ready` (enough samples, made by the version of the measurement now in use), `sampleCount`, `samplesRequired`, `outdated` (made by an older version, so they must start again), `updatedAt` and `pitch` (`baseHz`, `lowHz`, `highHz`). **`pitch` is set for managers and administrators only; it is `null` for everyone else.** The signature itself (the numbers that describe the voice's colour) is never returned and never logged, and is deleted with consent like the voice profile.

### Audit log

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/audit` | Admin | Who did what in this company, newest first, paged (`page`, `pageSize` up to 100, default 25). Filters: `category` (`auth`, `company`, `device`, `invitation`, `member`, `person`, `recording`, `settings`, `transcript`), `action` (exact, such as `person.created`), `actorId`, `objectType`, `dateFrom`, `dateTo`. |

```json
{ "id": "…", "at": "2026-10-20T12:00:00Z", "action": "settings.updated", "category": "settings",
  "actor": { "kind": "user", "id": "…", "name": "Dev Admin", "email": "dev-admin@example.com" },
  "objectType": "company", "objectId": "…", "detail": { "retention_days": 90 }, "ip": "203.0.113.7" }
```

The company comes from the sign-in, never from the request. `actor` is a person in this company or a locket, and `null` for an operator command, housekeeping, or an account that has since been removed; an account that belongs to another company is never named. `detail` holds counts and flags only, and is filtered again on the way out: anything whose name suggests a password, token, credential, voice print or file path is dropped, and long values are cut. `ip` is the address the request came from (see "Client address" below).

Recorded actions: sign-ins (`auth.login`, `auth.login_failed`, `auth.logout`, password changes and resets, `auth.session_revoked`), `company.created`, lockets (`device.created`, `.paired`, `.assigned`, `.connected`, `.revoked`), invitations, team members (`member.role_changed`, `.deactivated`, `.reactivated`, `.sessions_ended`), people and consent (`person.created`, `.deleted`, `.consent_granted`, `.consent_revoked`, `.voice_sample_added`, `.voice_profile_deleted`), recordings (`recording.played`, `.downloaded`, `.deleted`, `.purged`, `.retention_deleted`, `.reprocess_requested`, `.processed`, `.processing_failed`), `settings.updated` and `transcript.corrected`. The log is never pruned automatically.

### Workspace

| Method and path | Access | Notes |
| --- | --- | --- |
| `GET /api/summary` | Employee | Counts for the overview page, scoped to what you may see. |
| `GET /api/settings` | Employee | `{retentionDays, purgeGraceDays}`. `retentionDays: null` means keep until deleted. |
| `PATCH /api/settings` | Admin | Retention rules. Audited. Retention counts from when a recording was **stored**, not from the time the locket claims it was made, because that time is chosen by the device. |
| `POST /api/contact` | Public | The marketing contact form. Always `204`. A hidden honeypot field and a minimum fill time quietly discard bots. Rate limited per address; only a salted hash of the address is stored. |

## Error codes

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_pairing_code`, `invalid_token` | Code or emailed link is wrong, used or expired |
| 401 | `unauthenticated`, `invalid_credentials`, `invalid_device_credential` | Not signed in, wrong password, bad device credential |
| 403 | `csrf_failed`, `bad_origin`, `forbidden` | Missing CSRF header, request from another origin, role too low |
| 404 | `not_found`, `invitation_not_found` | Missing, deleted or in another company |
| 408 / 413 / 415 | `upload_timeout`, `upload_too_large`, `body_too_large`, `unsupported_media_type` | Upload problems. `upload_timeout` also applies to the whole upload (`ASK_UPLOAD_TOTAL_SECONDS`, 300), not only to a stall between pieces. `body_too_large` is for ordinary JSON requests (`ASK_MAX_JSON_BYTES`) |
| 409 | `device_offline`, `already_paired`, `device_revoked`, `account_exists`, `recording_conflict`, `already_processing`, `no_transcript`, `consent_required`, `enough_samples`, `cannot_change_self`, `shared_account` | State conflicts. `consent_required` is also answered when consent is withdrawn while a voice sample is being processed |
| 422 | `validation_error`, `checksum_mismatch`, `invalid_audio`, `simulation_not_allowed`, `sample_too_short`, `sample_too_long`, `unusable_sample` | Bad input (field details in `details`), a simulated locket on a server that only accepts real ones, or a voice sample that cannot be used (`message` says why) |
| 429 | `rate_limited`, `too_many_streams` | `Retry-After` header says when to try again. Also invitations (`ASK_INVITES_PER_HOUR`) |
| 501 | `not_available` | Not set up on this server (for example voice enrollment without a speaker model); `message` says what is missing |
| 503 | `database_unavailable`, `storage_unavailable`, `speaker_model_error` | The database is not answering, audio storage is not writable, or the speaker model failed on a sample (try again) |

## Client address

Rate limits and the audit log record the address a request came from. With `ASK_TRUST_FORWARDED_FOR=true` that is taken from `X-Forwarded-For`, counting `ASK_TRUSTED_PROXY_HOPS` entries (default 1) from the **end** of the header, because each proxy you control appends the address it saw while anything a client sent itself stays at the front. Only turn this on when every request really does pass through a proxy you control; without one, any caller can choose their own address.

## Not built yet

Single sign-on. The processing worker, speaker enrollment and speaker matching exist (milestone E) but their real-model path is **unverified**: see [AI.md](AI.md) for what was and was not run.

Audit events (sign-ins, failed sign-ins, device, team and recording actions, setting changes, playback and downloads) are written to the `audit_events` table, tenant-scoped, and never contain secrets, tokens, audio paths or voice data. Administrators read them with `GET /api/audit` and on the Audit log page.
