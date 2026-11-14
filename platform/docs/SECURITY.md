# Security: how the platform protects people, what was reviewed, and what is still your decision

This system records people's voices, turns them into text, and can attach names to them. Treat the data in it as sensitive personal data, and parts of it (voice prints) as biometric data.

## How the review was done, and what it is not

Phase F included a security review of the backend (`platform/api`). It was done by **a separate read-only reviewer** (an AI agent, briefed on the system and the areas to cover, with no access to the author's conclusions) that read the code, ran small probes against throwaway in-memory databases, and reported what it could confirm. Its findings were then **reproduced or checked independently before any fix**: each fix has a regression test that was run against the unfixed code and failed, then passed after the change. Where the reviewer's description of a failure turned out to need adjusting, that is noted in the tests.

**It is not**: a penetration test, a review by a security professional, a dependency vulnerability scan, a review of TLS or container configuration, or a review of the firmware or the web front end beyond what it touches (the web front end, the dependencies and the deployment files were looked at in the website pass below). Nothing here is a certification. Before holding other people's voice data in production, have someone qualified review the deployment, and have a lawyer review the consent and retention position for your jurisdiction (biometric and recording laws differ a great deal). That is not legal advice.

## What the platform does

| Concern | How |
| --- | --- |
| One company never sees another's data | Every query on company data filters on the company of the signed-in session, never on anything the request says. Another company's object answers `404`, exactly like a missing one. Checked across devices, recordings, people, invitations, sessions, audit, summary, live updates and processing |
| Sign-in | Passwords hashed with Argon2. Wrong email and wrong password look identical. Sessions are random 256-bit tokens in an HTTP-only, SameSite cookie, stored only as a hash, with idle and absolute limits. Changing or resetting a password ends the other sessions |
| Forged requests | Every state-changing browser request needs a per-session CSRF token and an allowed `Origin`. No cross-origin access is configured |
| Roles | Employees see only their locket's recordings. Managers run lockets, people and invitations. Only administrators change settings, other people's roles and access, and read the audit log. Role is read from the database on every request, so a change takes effect at once |
| Lockets | A per-device 256-bit credential, stored hashed, shown once at pairing. Revoking takes effect immediately. A pairing code is single use and short-lived |
| Audio | Stored under server-built keys the client never chooses. Never returned by path. Playback and downloads are recorded in the audit log |
| Voice prints | Created only for a person whose consent is on record; the enrollment audio is deleted at once; never returned or logged; deleted when consent is withdrawn; only compared with people in the same company |
| Uploads | Size, overall time and chunk-gap limits; a hash check; strict WAV validation; the file is made durable before it is acknowledged |
| Accountability | An append-only audit log of sign-ins, failed sign-ins, team changes, people and consent, recording access and deletion, and settings. Visible to administrators; filtered so it cannot show secrets or paths |
| Abuse limits | Rate limits on sign-in, password reset, pairing, enrollment, uploads, invitations and the contact form. A size limit on ordinary requests |

## What the review found, and what was done

**Fixed, each with a regression test:**

| Finding | Severity | Fix |
| --- | --- | --- |
| A slow upload could keep a database connection for as long as the sender liked, so a handful of them used up the pool for every company | High | The connection is given back before the body is awaited. The whole upload now has a deadline, not just the gap between pieces. Same for voice enrollment |
| Reassigning a locket handed its whole recorded history to the next wearer, because employees saw recordings by the locket's *current* assignee | Product decision, resolved | Each recording records who the locket was assigned to **when it arrived** (`recordings.assigned_user_id`, migration 0004). Employees see recordings by that, in the library, search, summary, audio, transcripts, jobs and the live feed. Passing a locket on does not pass its history on |
| Whoever invited a person was handed the acceptance link, so could accept it themselves as an account "verified" under someone else's address, and block that address from joining its real employer | Medium | With real mail the link is only in the email. A per-person limit on invitations |
| Download was restricted and audited only with `?download=true`; the same bytes streamed without it, with no trace | Medium | Starting playback is audited (once per listen, not per byte range). The documentation no longer implies employees cannot keep a copy: anyone who can listen can |
| No way to switch off a person, change a role, or sign someone out | Medium | New administrator controls, audited, never for yourself; takes effect at once |
| Outgoing mail encrypted the connection but did not check the mail server's certificate | Medium | The certificate is verified |
| Consent withdrawn *while* a voice sample was being processed, or a recording was being matched, still produced a voice print or a name | Medium-low | Consent is checked again, under a lock where the database has one, just before anything is written |
| Dates sent without a time zone crashed the request (`/api/recordings?dateFrom=2026-10-01`) | Low (found during this phase) | Read as UTC |
| Retention ran on a timestamp the locket chose, so a wrong clock could make a new clip look years old and have it deleted | Low-medium | Retention counts from when a recording was stored |
| A file of thousands of empty WAV sections cost seconds of processor time to validate | Low-medium | At most 64 sections before the audio |
| Error logs and stored failure messages could carry request data and database values | Low | Only the error type and where it happened are logged; database parameters are hidden; stored messages are cut at the query |
| "Single use" pairing codes and email links could be used twice by two simultaneous requests | Low | An atomic claim; the second request loses |
| An unbounded rate-limiter table that grew with every distinct email tried | Low | Expired keys are swept |
| A header made of characters like superscript two gave an unhandled error | Low | Refused cleanly |
| Employees were sent every colleague's email address and the notes naming settings and software | Low | Addresses and notes go only to managers and administrators |
| Production configuration accepted in-memory mail, `http://` addresses and `localhost` | Low | Refused at start-up |
| The client address was the **first** entry of `X-Forwarded-For`, which a client can choose | Suspected | The entry the trusted proxy added (counted from the end) |
| No size limit on ordinary JSON requests | Suspected | 1 MiB, refused before it is read |
| Leftover partial uploads were removed only at start-up | Suspected | Housekeeping removes old ones |
| Audio and spool files were created world-readable | Suspected | Process umask `077` on POSIX (not testable on the Windows machine this was built on) |
| Invitation links (a secret in the URL) would be kept by the web server's access log | Low | The container turns the API access log off. See below for other places they can appear |

## Website security pass (1 November 2026)

A second pass, asked for as "harden the website": secrets, access control, authentication, forms, XSS, rate limits, the API, CORS, headers, debug mode, dependencies, exposed files, the database, password hashing and git history. It covered `platform/web`, `platform/api`, `platform/deploy` and every file in the project folder. It is **not** a penetration test or a professional audit, and nothing here is a certification.

**How:** the code was read (not just the earlier notes), every API route was listed with what guards it, the working folder was scanned for secrets, both dependency trees were audited, and then the real stack (the API plus the production web build, on a throwaway database) was probed over HTTP and in a browser: 38 live probes, all passing. Each new safeguard has a test that fails without it (checked by removing the safeguard).

**Checked and found sound (no change needed):**

| Area | What was found |
| --- | --- |
| Secrets | No key, token, password or private key in any source, config, doc or test file (539 files scanned; the 9 pattern hits were `user:password@host` placeholders and test fixtures). Real values live only in `.env` files, which are git-ignored and kept out of the container images (`.dockerignore`). `ASK_SECRET_KEY` is 64 characters and is refused if it looks like a placeholder |
| Authentication | Argon2id with the library's current defaults, re-hashed when the parameters change; an unknown email costs the same time as a wrong password; sessions are random 256-bit tokens kept only as a hash, HttpOnly and SameSite=Lax (and Secure in production), ended by logout, a password change or an administrator |
| Access control | All 58 routes were listed. Without a session only these answer: health, sign-in, logout, password reset, email verification, invitations (a 256-bit token), the contact form, and locket pairing and upload (locket credential). The live stream authenticates inside the route. Administrator only: the audit log, settings changes, role changes. Managers: lockets, people, invitations. Employees are refused (403) on all of those, checked live, and cannot make themselves administrators |
| Forged requests | A CSRF token on every browser write; `Origin` checked on writes and sign-in. **CORS is not enabled at all** (the browser talks only to its own site). `ASK_WEB_ORIGINS` is an allow-list for the Origin check, and `*` is refused at start-up |
| Injection | Every query goes through SQLAlchemy with bound values (no string-built SQL anywhere). The web app has no `dangerouslySetInnerHTML`, `innerHTML`, `eval` or user-built links. A SQL-looking name and a script-looking name were stored as plain text and returned as JSON |
| Rate limits | Sign-in (per address and source, and per source), password reset, reset confirmation, email verification, pairing, uploads, enrollment, invitations, invitation lookup and acceptance, the live stream, the contact form. Verified: the sixth wrong password gets 429 |
| Debug mode | There is no debug flag. In production the interactive docs and the schema are off; errors never carry details (a test makes a route raise with a "secret" in the message); start-up refuses SQLite, console mail, `http://` or `localhost` addresses and insecure cookies |
| Exposed files | The web app serves only `public/` (empty) and built assets. `/.env`, `/.git/config`, `/package.json`, `/next.config.ts` and a path-traversal attempt all answer 404 |
| Database | PostgreSQL in production, on the internal network only (no published port), password from `.env`; error text hides stored values; one query layer. The development SQLite file and the dev admin login are in `platform/data/`, which is git-ignored |
| Containers | The API and web app run as a non-root user; only the proxy publishes ports |

**Changed in this pass:**

| Finding | Severity | Fix |
| --- | --- | --- |
| In the production layout the proxy sends `/api/*` straight to the API, so the headers the web app adds never reached an API answer: signed-in data could be cached, framed or sniffed | Low-medium | A middleware (`app/security/headers.py`) adds `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cross-Origin-Resource-Policy` and `Cache-Control: no-store` to every API answer, and a deny-everything Content-Security-Policy to JSON and the live stream. A route's own `Cache-Control` (audio, live stream) is kept. Tests: `tests/test_hardening_web.py` |
| The web pages had no Content-Security-Policy | Medium as depth (the app escapes everything and has no known XSS) | `next.config.ts` now sends one, plus `Cross-Origin-Opener-Policy` and a tighter `Permissions-Policy`: nothing may load from, or send data to, another origin; no plugins, no `<base>`, forms post only here, no framing. **Checked in a browser:** every page, sign-in, the dashboard with live data, the live stream and a transcript export run with no violation, and a foreign script, image, fetch and iframe were each blocked |
| Free-text fields accepted control characters, line breaks and right-to-left override characters | Low | Names, the company field, the contact message, consent statements and transcript corrections are cleaned on the server (`CleanLine`, `CleanText` in `app/schemas/common.py`): control characters and line breaks in a name become spaces, override characters are removed, text is normalised. It was not exploitable (the mail library refuses a line break in a header, so no header could be injected), but a line break silently stopped a contact message being forwarded, and override characters could make one name look like another in the audit log |
| Application containers could gain privileges after start | Low | `no-new-privileges` on the API, worker, web app, migration step and proxy (**never run**: Docker is not available here) |
| `.gitignore` did not name keys or certificates | Low | `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`, `id_rsa*` and `id_ed25519*` added |
| Dependencies | n/a | Updated within their major versions and pinned exactly: `react` and `react-dom` 19.3.1, `@tanstack/react-query` 5.105.0, `lucide-react` 1.66.0, `motion` 14.9.0, `pydantic` 2.14.2, `pydantic-settings` 2.16.2. Web: lint, types, 344 tests and the production build pass. API: 675 pass. `pip-audit`: no known vulnerabilities in the 139 installed Python packages. `npm audit --omit=dev` (the web app as shipped): 0 |

**Not changed, and what is yours to decide:**

* **Git history could not be checked.** This folder is not a git repository (there is no `.git`), so there is no history to scan; only the files present were scanned. If a copy of this project lives in a git repository elsewhere, scan that with a tool such as `gitleaks` or `trufflehog`, and if anything real turns up, **rotate that secret** rather than only deleting it from history.
* **The CSP allows inline scripts and styles.** A strict, nonce-based policy needs every page rendered per request, which Next.js documents as incompatible with this app's pre-rendered shell. Everything else in the policy is strict, and the app has no known way to inject script, so what remains is the case where an attacker has already found one.
* **Rate limits are per process** (in memory). With the shipped single API container that is the whole limit; with more than one instance, add the same limits at the gateway. Authenticated routes other than those listed have no request-rate limit.
* **Dev-tool advisories in the web app** (16 moderate, all `minimatch` inside the ESLint tooling, not shipped): npm's suggested "fix" is a downgrade to ESLint 4, so it was not applied. Wait for the tooling's own update.
* **The mobile app at the repository root (Expo)** is not the website and was not changed. `npm audit` there reports 26 advisories (3 critical, 8 high; mostly build-tool and dev-server packages such as `shell-quote`, `tinypool`, `vitest` and `@expo/cli`), and the fix is an Expo major upgrade, which can break the app. Decide that separately.
* **A demo build skips the dashboard page gate** (`NEXT_PUBLIC_DEMO_MODE=true` in `proxy.ts`) and shows sample data. The API still authorizes everything, and the Dockerfile and Compose file set it to `false`. Never deploy a demo build.
* **Passwords** need 12 to 128 characters and 5 different ones. They are not checked against lists of breached passwords.
* **Nothing was run in production mode, in Docker, behind the proxy with real TLS, or with real mail.** The start-up checks of `ASK_ENV=production`, and its docs-off and Secure-cookie behaviour, are tested, but a real deployment has not been made.

**Before this goes on the internet:** set `ASK_ENV=production`, a PostgreSQL URL, the real `https://` addresses and SMTP in `platform/deploy/.env` (see [RUNBOOK.md](RUNBOOK.md)); keep `NEXT_PUBLIC_DEMO_MODE=false`; run `docker compose config`; then run `npm audit --omit=dev` and `pip-audit` again, because new advisories appear every week.

## Decisions that are yours

These were found or raised and **deliberately not changed**, because they are product or legal decisions, or need a larger change.

1. **Who counts as the wearer, and when.** An employee sees a recording if the locket was assigned to them *when the clip arrived at the server*. That is what the server can know for certain. Three consequences to be aware of:
   - **A clip that arrives after a handover belongs to the new wearer, even if it was recorded earlier.** A locket that was offline keeps its clips and uploads them later. The Devices page warns when clips are still waiting to upload; wait for the queue to be empty before changing who wears a locket if those clips should stay with the previous wearer. (The time a locket says a clip was made is chosen by the locket, so it is not used to decide who may hear it.)
   - **A locket that is unassigned when a clip arrives** produces recordings only managers and administrators can see, and assigning the locket later does not change that.
   - **Recordings that already existed when this was introduced** (migration 0004) were attributed to their locket's assignee *at that moment*, which is exactly who could see them before, so nobody gained or lost access. If a locket had already changed hands before the upgrade, its earlier history had already gone to the later wearer, and stays there.
2. **Consent is a manager's assertion.** The dashboard records that a manager says a person agreed, with the wording used and a time. It does not capture the person's own agreement. Whether that is enough is a legal question.
3. **Who can correct a transcript.** Employees can edit the transcript of recordings they can see and clear its review flags. The original text is kept next to each correction.
4. **Voice recognition is not identity verification.** The speaker threshold is uncalibrated until you run `calibrate-speakers` on real recordings; the real-model path has never been run. A wrong name is worse than "Unknown speaker", so the system prefers the latter, but never use a match as proof of who spoke.
5. **Biometric data.** Voice prints are biometric data in many places. Backups include them. Deleting a person deletes their print, but old backups keep it until you delete them.
6. **How long the audit log is kept.** For ever, by default. It records sign-in addresses (personal data).

## Voice prints

A voice print is a list of numbers (192 of them for the current speaker model) that describes how someone's voice sounds. It is not audio and cannot be played, but anyone holding the speaker model and a recording can use it to ask "is this the same voice?". Treat it as **biometric data**. This section was written from reading the code and testing it with the real speaker model, and says where the limits are.

| Question | Answer |
| --- | --- |
| What is stored | One row per person in `voice_profiles`: the averaged print as raw 32-bit floats, the model's name and version, the number of samples and when they were taken. **No audio.** The enrollment sample is deleted as soon as the print has been made, including when enrollment fails. |
| Where | The application database, in the `embedding` column. It is **not encrypted by the application**: encrypt the disk or the database volume, and treat every backup as holding biometric data. |
| Who can reach it | Three pieces of code only: enrollment (adds a sample), the worker (reads the prints of the recording's own company to compare), and removal. No API response has a field for it (`tests/real/test_real_speakers.py::test_voice_prints_never_leave_the_server`, and the response schemas), it is not logged, and the audit log records only the sample count. Managers and administrators enroll and remove; employees cannot; another company's people answer `404`. |
| Who can be matched | Only a person of the **same company** whose consent is **currently granted**, with at least three samples, made by the speaker model now in use. Each of those conditions is enforced twice, and tested with the real model. |
| How it is removed | Withdrawing consent, "Remove voice", and removing the person delete the print at once and stop past transcript lines naming the person (the text stays). Deleting a company deletes everything. |
| What removal does **not** reach | Database backups and replicas made earlier; anything the database engine has not yet reclaimed (PostgreSQL keeps dead rows until vacuum); and **prints made by a speaker model you have since replaced**, which stay in the table, unused, until that person enrolls again or is removed. |
| Retention | None automatic. A print lives until consent is withdrawn, the voice or person is removed, or the person is re-enrolled. There is no expiry and no periodic re-consent. |

Decisions that are yours, because they are legal or policy choices: how long a print may live without fresh consent; whether to delete prints from a replaced model automatically; encryption at rest; how long backups are kept (they are your longest-lived copy of every print); and whether this processing is lawful for your people in your jurisdiction (biometric rules differ widely).

## Voice signatures and the summary

A **voice signature** (milestone added for the voice-to-summary brief; full description in [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md)) is 24 numbers describing the colour of someone's voice, with a summary of their pitch. It needs no model, so it exists whatever the processing provider is. It is a **coarser** fingerprint than a voice print, but it can still be used to ask "is this the same voice?", so it is handled as biometric data in the same way. This section was written from reading the code and testing it; nothing here was reviewed by a professional.

| Question | Answer |
| --- | --- |
| What is stored | One row per person in `person_signatures`: the 24 timbre numbers (raw 32-bit floats), the version of the measurement, base, low and high pitch, voiced seconds and the sample count. **No audio**: an upload is measured and deleted at once, including when it is refused (`tests/test_voice_signature_api.py`). |
| Who can reach it | Enrollment (adds a sample), the worker (compares signatures of the recording's own company) and the manager-only lookup. **No response has a field for the 24 numbers**, and nothing logs them; only the pitch summary is returned, in the People list to managers and administrators only. The recording page shows a speaker's pitch to anyone who can see that recording, and how alike a voice was only to managers. |
| Who can be matched | Only a person of the **same company** whose consent is **currently granted**, with enough samples, made by the current version of the measurement. The worker checks consent again under a lock just before saving a name, and the lookup excludes everyone else (`test_another_companys_people_are_never_candidates`, `test_people_whose_consent_was_withdrawn_are_never_candidates`). |
| The lookup (`POST /api/people/signature-matches`) | It tells a manager whom an uploaded voice most resembles, so it is a way of asking "who is this?". It is manager-only, rate limited (20 a minute), audited (outcome and candidate count, never names or measurements) and every answer says a score is not identity verification. A name is returned as the match only when it clears the threshold and beats the next person by the margin. Who may use it, and whether to allow it at all, is a policy decision that is yours. |
| How it is removed | Withdrawing consent, "Remove voice" and removing the person delete it (and the voice profile) at once. A signature from an older version of the measurement is replaced, never mixed with a new one. |
| What removal does **not** reach | Backups and replicas made earlier, like voice prints. The **summary** keeps speakers by label only, never a name, and a name is looked up when the recording is read: withdrawing consent therefore removes it from the summary immediately. A spoken name inside a sentence ("Ravi, please book the room") is part of what was said and stays with the transcript text. |
| Accuracy | Not calibrated on real people. Only two synthetic voices were available. A wrong name is worse than "Unknown speaker", so the defaults err towards the latter, but never use a match as proof of who spoke. |

The sentence model for the summary (an optional download) is a third-party file like the speech models above and has the same checkpoint-loading risk; it is **never downloaded unless `ASK_AI_TEXT_ALLOW_DOWNLOAD=true`**, and the summary works without it.

## Model files

The speech and speaker models are third-party files downloaded from the Hugging Face hub on first use. Their checkpoints are loaded with PyTorch, and **a tampered checkpoint can run code when it is loaded**. The model names are fixed by settings, but no revision or checksum is pinned, so a changed upstream file would be used. If that matters to you: download the weights once on a machine you trust, verify them, copy them into `ASK_AI_MODEL_DIR`, and run with `ASK_AI_OFFLINE=true`, which also guarantees nothing is fetched later. The worker should run as an unprivileged account (the container does).

## Known limits

- **No second factor, no single sign-on.** Passwords only.
- **Account existence can leak through timing.** A password-reset request does a little more work (and, with real mail, waits on the mail server) when the address has an account. Rate limits cap how fast it can be used.
- **No per-company quotas** on storage, lockets, invitations or heartbeats, and the live-update connection cap is shared by all companies. One company's administrator could crowd out others' live updates.
- **Rate limits live in memory**, per process. Run one API process, or enforce limits at the proxy too. With `ASK_TRUST_FORWARDED_FOR=false` behind a proxy, every caller shares the proxy's address, so five wrong passwords for one person's email lock that person out for a minute whoever they came from, and enough attempts lock out sign-in for everyone. There is no per-account limit that ignores the address, so guessing spread over many addresses is limited only by the cost of hashing a password.
- **Sending mail blocks the request** that triggered it (up to 15 seconds).
- **Enrollment against a real speaker model** serialises on one lock; many simultaneous enrollments could tie up threads. Enrollment against the real model was run (one at a time); simultaneous enrollments were not tried.
- **A device's `simulated` flag is its own claim** at pairing time. Anyone holding a pairing code can leave it out.
- **Secrets in URLs.** An invitation or password-reset link carries its token in the address. The API's access log is off in the container and Caddy logs nothing unless asked, but a browser's history, a corporate proxy or the Next.js server's own log can record them. Links expire (72 hours, 30 minutes) and work once.
- **Locket credentials and Wi-Fi passwords** are stored unencrypted in the chip's memory; anyone with the device and a USB cable can read them. See the firmware README.
- **A Wi-Fi password built into the button recorder.** At the owner's request `locket_web_upload/config.h` holds a test hotspot, `ESPTEST` / `12345678` (`ASK_DEFAULT_WIFI_SSID`, `ASK_DEFAULT_WIFI_PASSWORD`), so the locket joins it at power-on with no serial command. That is a password in plain text in the source, in anything committed or shared, and in the compiled firmware (readable from the chip or the `.bin`). It is acceptable only for a throwaway hotspot. The firmware never prints it (checked by a test over every simulated run and a scan of the sources), and it is not sent to the server, but it is not protected. **Before a locket goes to anyone else, empty both settings or replace them, then type the real network over serial (`wifi …`).** Flash encryption would protect the typed one; it is not set up.
- **The original recorder's keys are compromised.** The source the button recorder (`locket_web_upload`) replaces carried a Wi-Fi password, Google API keys and a Telegram bot token. Rotate all of them (new Wi-Fi password, delete the Google keys in the Cloud console, revoke the Telegram token with BotFather); the new firmware carries no key or token, and sets the server address and the pairing credential over the USB cable. The one exception is the throwaway test hotspot below.
- **The button recorder's local maintenance page** (`ap on`) is protected only by the locket's own Wi-Fi password, shows no transcript, credential or password, and answers only on that network. A build with `ASK_DASHBOARD_AUDIO_DOWNLOAD=1` lets anyone on that network download queued recordings; it is off, and for support builds only.
- **A button recorder's session reference and part number** are chosen by the locket and only group recordings of the same locket; they grant nothing, are shown to people who can already see the recording, and cannot reach another company's recordings (checked in `tests/test_recording_parts.py`).
- **Dependencies:** `pip-audit` (the 139 installed Python packages) found no known vulnerabilities on 2026-10-27 and again on 2026-11-01. `npm audit --omit=dev` (the web app as shipped) found none on either day; the full `npm audit` now lists 16 moderate advisories, all in the ESLint tooling (see the website pass above). That is a point-in-time result; run your normal audit before and after deployment, and keep them updated.
- **PostgreSQL** was run (version 16.15, a portable build, local only): the migrations, the whole test suite, and row-level locking (`FOR SHARE`, `SKIP LOCKED`) under racing workers. It was **not** run in Docker, behind the proxy, with TLS to the database or under load. On SQLite the locks are a no-op.

## Reporting a problem

There is no public reporting channel yet. Decide one before you deploy (an address, a person) and put it where users can find it.
