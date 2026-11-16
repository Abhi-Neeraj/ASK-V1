# Server: lockets, emailed pairing codes, phone upload, Google + Gemini, firmware hosting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the FastAPI backend serve the Bluetooth locket design: write-once product IDs, a per-locket people limit only administrators can raise, emailed pairing codes, phone uploads, Google Speech-to-Text plus a Gemini summary, the two `.txt` files, and firmware files for Wi-Fi updates.

**Architecture:** Existing patterns are kept: SQLAlchemy models plus an Alembic migration (`0006`, add-only), services under `app/services`, routers under `app/routers`, the provider interface in `app/ai/types.py` extended with an optional `summarizer`. Lockets stay rows in `devices` (`public_id` is the product ID). Membership moves from the single `devices.assigned_user_id` to a `device_members` table, kept in step with `assigned_user_id` (earliest member) so existing code keeps working.

**Tech Stack:** Python 3.11 (tests may run on 3.13 here), FastAPI, SQLAlchemy 2.1, Alembic, pytest; new optional extra `google`: `google-cloud-speech==2.41.0`, `google-genai==3.3.0`.

**Spec:** `docs/superpowers/specs/2026-11-14-ble-locket-cloud-pipeline-design.md` (sections 5.1, 5.2, 5.4, 5.5).

All paths are relative to `platform/api` unless they start with `platform/` or `docs/`. Run commands from `platform/api` with the project's Python (`.venv/Scripts/python`, or the Python 3.13 venv used on this machine).

## Global Constraints

- Product ID format: `ASK-` + 6 characters (existing `public_id` generator). Once a row exists, `public_id` never changes (ORM guard + database trigger).
- Locket secret = `HMAC-SHA256(ASK_LOCKET_MASTER_KEY, product_id)`; never stored, never logged, returned only once by the register call.
- Pairing code = `uint32_be(HMAC-SHA256(secret, b"ASK-PAIR" + nonce)[0:4]) % 1_000_000`, zero-padded to 6 digits. Nonce is exactly 16 bytes, sent as 32 hex characters.
- `devices.max_users` default 1, allowed 1..50, changed only by role `admin`, never below the current number of people.
- Pending pairing request lifetime: 10 minutes (`ASK_LOCKET_CODE_TTL_MINUTES`). Code requests: 5 per user per hour (`ASK_LOCKET_CODE_REQUESTS_PER_HOUR`).
- Firmware download link lifetime: 30 minutes. Firmware file max 4 MiB.
- Google chunks: at most 55 s of audio per `recognize` call. Gemini model from `ASK_GEMINI_MODEL`.
- Migrations: add columns and tables only, never rebuild a referenced table (see `CLAUDE.md`). New migration gets a data-survival test.
- Never return or log: locket secrets (except the one register response), pairing codes, Google/Gemini keys, storage paths.
- Every company-owned query stays tenant-scoped; another company's locket answers 404.

## Review Focus

- A pairing code request for a locket that is full (limit reached, caller not already on it) must be refused with 409 `locket_full`, and no email sent. → Task 4 test `test_full_locket_refuses_a_code_and_sends_no_email`.
- Two people racing for the last place: the second `paired` call must fail 409, never exceed the limit. → Task 4 test `test_paired_respects_the_limit_even_after_the_code_was_sent`.
- A transcript with no speech (Google returns no results) must still complete, with an empty `transcription.txt` and no summary call. → Task 8 test `test_silence_gives_an_empty_transcript_and_no_summary`.
- Gemini failing (exception or empty text) must not fail the recording; the transcript is saved and `summary.txt` answers 404 `summary_not_ready`. → Task 8 test `test_a_failed_summary_keeps_the_transcript`.
- Someone added to a locket later must not see recordings that arrived before they joined. → Task 6 test `test_a_new_member_does_not_see_older_recordings`.

---

### Task 1: Settings and the pairing-code formula

**Files:**
- Modify: `app/config.py` (new settings; production checks)
- Create: `app/security/locket_codes.py`
- Test: `tests/test_locket_codes.py`
- Modify: `.env.example` (document new variables, no values)

**Interfaces:**
- Produces: `locket_secret(master_key: str, product_id: str) -> bytes`; `pairing_code(secret: bytes, nonce: bytes) -> str`; `parse_nonce(value: str) -> bytes` (raises `ApiError(422, "validation_error", ...)` unless exactly 32 hex chars).
- Settings produced: `locket_master_key: SecretStr | None = None` (min 32 chars when set), `locket_code_ttl_minutes: int = 10`, `locket_code_requests_per_hour: int = 5`, `firmware_link_minutes: int = 30`, `firmware_max_bytes: int = 4 * 1024 * 1024`, `ai_provider: Literal["none", "stub", "local", "google"]`, `google_application_credentials: Path | None = None`, `google_stt_language: str = "en-US"`, `gemini_api_key: SecretStr | None = None`, `gemini_model: str = "gemini-2.5-flash"`. Production refuses `ai_provider="google"` without both Google settings.

- [ ] **Step 1: Write the failing tests**

```python
MASTER = "test-master-key-0123456789abcdef0123456789"

@pytest.mark.parametrize(("product_id", "nonce", "secret_hex", "code"), [
    ("ASK-7KQ2MX", "00112233445566778899aabbccddeeff",
     "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04", "403836"),
    ("ASK-ABCDEF", "ffffffffffffffffffffffffffffffff",
     "1f2bbb094260a90004e01c966a9c063fd7f81c44354820394347fc68c5fad0fa", "400246"),
    ("ASK-7KQ2MX", "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
     "89fbcaf408d79a1e3879d5f3559f7b38d63d1b45c74450ae3cefea6b2b798b04", "778647"),
])
def test_known_vectors(product_id, nonce, secret_hex, code): ...
    # locket_secret(MASTER, product_id).hex() == secret_hex
    # pairing_code(bytes.fromhex(secret_hex), bytes.fromhex(nonce)) == code

def test_parse_nonce_rejects_wrong_length_and_non_hex(): ...  # "00"*15, "zz"*16 -> ApiError 422
def test_short_master_key_is_refused(): ...  # Settings(locket_master_key="short") -> ValidationError
def test_production_google_needs_both_keys(): ...
```

- [ ] **Step 2: Run** `python -m pytest tests/test_locket_codes.py -q` — expected FAIL (module missing).
- [ ] **Step 3: Implement** the three functions with `hmac.new(..., hashlib.sha256)`; add the settings with `ASK_` env names matching the field names; document them in `.env.example`.
- [ ] **Step 4: Run** the same command — expected PASS. Then `python -m ruff check . && python -m mypy app`.
- [ ] **Step 5: Commit** `feat(api): locket secret and pairing-code formula`.

### Task 2: Migration 0006 and models

**Files:**
- Create: `migrations/versions/0006_bluetooth_lockets.py` (`revision = "0006"`, `down_revision = "0005"`)
- Modify: `app/models/devices.py`, `app/models/recordings.py`, `app/models/__init__.py`
- Test: `tests/test_migration_0006.py`

**Interfaces:**
- Produces models: `Device.max_users: int` (default 1, check `max_users BETWEEN 1 AND 50`); `DeviceMember(id, company_id, device_id, user_id, added_by: uuid|None, added_at)` unique `(device_id, user_id)`; `RecordingAccess(recording_id, user_id)` composite primary key; `LocketPairingRequest(id, company_id, device_id, user_id, nonce_hash: str(64), expires_at, completed_at|None, created_at)`; `FirmwareRelease(id, version: str(40) unique, sha256: str(64), size_bytes, storage_key, uploaded_by|None, created_at)`; `Transcript.summary_text: str | None`, `Transcript.summary_model: str | None`.
- Migration also: copies each non-null `devices.assigned_user_id` into `device_members`, each non-null `recordings.assigned_user_id` into `recording_access`, and creates a trigger that aborts any update changing `devices.public_id` (SQLite: `BEFORE UPDATE OF public_id ... WHEN NEW.public_id IS NOT OLD.public_id BEGIN SELECT RAISE(ABORT, 'product ID is permanent'); END`; PostgreSQL: a plpgsql function + `BEFORE UPDATE` trigger raising the same message). Downgrade drops all of it.

- [ ] **Step 1: Write the failing tests** (pattern: `tests/test_migration_wearer.py`)
  - `test_upgrade_keeps_devices_recordings_and_backfills_members`: seed a company, user, device with `assigned_user_id`, recording with `assigned_user_id` at revision 0005; upgrade to 0006; assert row counts unchanged, `device_members` has 1 row for that pair, `recording_access` has 1 row, `devices.max_users == 1`.
  - `test_public_id_cannot_change_after_upgrade`: raw `UPDATE devices SET public_id='ASK-ZZZZZZ'` raises `sqlalchemy.exc.DatabaseError` (or `IntegrityError`) containing `product ID is permanent`.
  - `test_down_and_up_again`: downgrade to 0005 then upgrade to 0006 succeeds with data intact.
- [ ] **Step 2: Run** `python -m pytest tests/test_migration_0006.py -q` — FAIL.
- [ ] **Step 3: Implement** the migration with `op.add_column`/`op.create_table` only (no batch rebuild of `devices`, `recordings` or `transcripts`), and the models.
- [ ] **Step 4: Run** the new test plus `tests/test_migrations.py` — PASS.
- [ ] **Step 5: Commit** `feat(api): migration 0006 for Bluetooth lockets`.

### Task 3: Product ID is write-once in the application

**Files:**
- Modify: `app/models/devices.py` (SQLAlchemy `@event.listens_for(Device.public_id, "set")` guard)
- Test: `tests/test_product_id.py`

**Interfaces:**
- Produces: assigning a different `public_id` to a persistent `Device` raises `PermanentProductIdError(ValueError)` (defined in `app/models/devices.py`). Setting it on a new, unsaved row is allowed.

- [ ] **Step 1: Failing tests:** `test_new_device_gets_a_product_id`, `test_changing_a_saved_product_id_raises`, `test_patch_cannot_change_product_id` (PATCH `/api/devices/{id}` with `{"publicId": "ASK-ZZZZZZ"}` leaves it unchanged — the schema ignores or 422s the field; assert unchanged).
- [ ] **Step 2: Run** — FAIL on the guard test.
- [ ] **Step 3: Implement** the listener (`retval=False`; compare with `oldvalue` when it is not `NO_VALUE`/`None`).
- [ ] **Step 4: Run** `python -m pytest tests/test_product_id.py tests/test_devices.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(api): product ID cannot change once saved`.

### Task 4: People per locket and emailed pairing codes

**Files:**
- Create: `app/services/lockets.py`, `app/schemas/lockets.py`, `app/routers/lockets.py`
- Modify: `app/main.py` (include router), `app/services/devices.py` (`visible_devices` uses membership; `PATCH assignedUserId` replaces members with that one person), `app/services/email.py` (`locket_code_email(to: str, product_id: str, code: str, minutes: int) -> OutgoingEmail`), `app/cli.py` (`register-locket --name NAME --company-id UUID`)
- Test: `tests/test_lockets.py`

**Interfaces:**
- Consumes: Task 1 functions and settings; Task 2 models.
- Produces (service): `register_locket(db, settings, company_id, name, actor) -> tuple[Device, str]` (secret hex); `members(db, device) -> list[User]`; `add_member(db, device, user, added_by) -> None` (409 `locket_full`); `remove_member(db, device, user_id) -> None`; `set_limit(db, device, max_users) -> None` (409 `limit_below_members`, 422 outside 1..50); `request_code(db, settings, sender, ctx, device, nonce) -> str` (returns masked email like `n***@example.com`); `confirm_paired(db, ctx, device, nonce) -> None` (404 `pairing_request_not_found` when none pending/expired). After every membership change `device.assigned_user_id` = earliest member or `None`.
- Produces (HTTP, JSON camelCase, prefix `/api/lockets`, path key is the product ID):
  - `GET /api/lockets` (member) → `[{productId, name, maxUsers, members:[{id,name,email}], firmwareVersion, batteryPercent, lastSeenAt}]` — managers see all company lockets, others only lockets they are on.
  - `POST /api/lockets` (admin) `{name}` → `{productId, secretHex}`; 503 `locket_keys_missing` when `ASK_LOCKET_MASTER_KEY` unset.
  - `POST /api/lockets/{productId}/pairing-code` (member) `{nonce}` → `{sentTo, expiresAt}`.
  - `POST /api/lockets/{productId}/paired` (member) `{nonce}` → 204.
  - `DELETE /api/lockets/{productId}/members/{userId}` (manager/admin, or the user themselves) → 204.
  - `PATCH /api/lockets/{productId}/limit` (admin) `{maxUsers}` → updated locket.

- [ ] **Step 1: Failing tests** (fixtures `web`, `acme`, `outbox`, helpers `add_member`):
  - `test_admin_registers_a_locket_and_gets_the_secret_once` (second GET never contains `secretHex`).
  - `test_code_is_emailed_and_matches_the_formula` (email body contains `pairing_code(locket_secret(MASTER, pid), nonce)`; response has no code).
  - `test_full_locket_refuses_a_code_and_sends_no_email`.
  - `test_existing_member_can_get_a_new_code_when_full`.
  - `test_paired_adds_the_member_once_and_completes_the_request` (second `paired` with same nonce → 404).
  - `test_paired_respects_the_limit_even_after_the_code_was_sent` (two users get codes while 0/1 used; first `paired` 204, second 409).
  - `test_expired_request_cannot_be_confirmed` (move clock 11 minutes).
  - `test_code_requests_are_rate_limited` (6th within an hour → 429).
  - `test_only_admin_changes_the_limit`, `test_limit_cannot_drop_below_members`, `test_limit_bounds`.
  - `test_member_can_forget_a_locket`, `test_employee_cannot_remove_someone_else`.
  - `test_other_company_locket_is_404`.
  - `test_emails_never_contain_the_secret` and audit rows exist for `locket.code_requested`, `locket.paired`, `locket.limit_changed`.
- [ ] **Step 2: Run** `python -m pytest tests/test_lockets.py -q` — FAIL.
- [ ] **Step 3: Implement.** Store only `sha256(nonce)` in the request row. Lock the device row (`with_for_update()`) in `confirm_paired` and `add_member` before counting.
- [ ] **Step 4: Run** `python -m pytest tests/test_lockets.py tests/test_devices.py tests/test_wearer_visibility.py -q` — PASS (update existing device tests only where they asserted the single-holder internals, never weakening a rule).
- [ ] **Step 5: Commit** `feat(api): people per locket and emailed pairing codes`.

### Task 5: CSRF token in the sign-in response

**Files:**
- Modify: `app/schemas/auth.py` (`UserOut.csrf_token: str`), `app/routers/auth.py` (`login`, `me`, invitation accept return it)
- Test: `tests/test_auth.py` (add `test_login_and_me_return_the_csrf_token`)

**Interfaces:**
- Produces: JSON field `csrfToken` equal to the `ask_csrf` cookie value, for native clients that cannot read cookies.

- [ ] **Step 1:** write the test (assert `resp.json()["csrfToken"] == web.cookies["ask_csrf"]` after login and after `GET /api/auth/me`).
- [ ] **Step 2:** run — FAIL. **Step 3:** implement with `csrf_token(raw, secret)`. **Step 4:** `python -m pytest tests/test_auth.py -q` — PASS. **Step 5:** commit `feat(api): return csrf token in body for the phone app`.

### Task 6: Phone upload and per-arrival access

**Files:**
- Modify: `app/services/uploads.py` (`finalize_upload(db, settings, storage, device: Device, *, ...)` instead of `ctx`; writes `RecordingAccess` for every member at arrival), `app/routers/device_api.py` (pass `ctx.device`), `app/services/recordings.py` (`visible_recordings`: non-managers see rows with a `RecordingAccess` for them)
- Modify: `app/routers/lockets.py` (`PUT /api/lockets/{productId}/recordings/{clientRecordingId}`)
- Test: `tests/test_phone_upload.py`

**Interfaces:**
- Consumes: Task 4 membership.
- Produces: phone upload with the same headers and answers as the device upload (`X-Content-SHA256`, `X-Recorded-At`; 201 stored / 200 duplicate; 415/413/422 as today), cookie session + CSRF, caller must be on the locket (else 404).

- [ ] **Step 1: Failing tests:** `test_member_uploads_and_a_job_is_queued`, `test_upload_is_idempotent`, `test_non_member_gets_404`, `test_csrf_is_required`, `test_every_member_at_arrival_can_see_it`, `test_a_new_member_does_not_see_older_recordings`, `test_removed_member_keeps_what_arrived_while_they_were_on`.
- [ ] **Step 2:** run — FAIL. **Step 3:** implement (reuse `spool_to_disk`, `parse_recorded_at`, `CLIENT_ID_PATTERN`; same 120/min limit keyed by device). **Step 4:** `python -m pytest tests/test_phone_upload.py tests/test_wearer_visibility.py tests/test_uploads.py -q` — PASS. **Step 5:** commit `feat(api): phone uploads with per-arrival access`.

### Task 7: The two text files

**Files:**
- Create: `app/services/textfiles.py`
- Modify: `app/routers/recordings.py`
- Test: `tests/test_textfiles.py`

**Interfaces:**
- Produces: `transcription_txt(segments: Sequence[TranscriptSegment], names: Mapping[uuid.UUID, str]) -> str` — one line per segment `[HH:MM:SS] <who>: <text>`, `<who>` = person name when `person_id` is set and known, else `Speaker N` numbered by first appearance; trailing newline. `GET /api/recordings/{id}/transcription.txt` and `/summary.txt` → `text/plain; charset=utf-8` with `Content-Disposition: attachment; filename="transcription.txt"` / `"summary.txt"`; 404 `transcript_not_ready` / `summary_not_ready`; placeholder transcripts start with the line `PLACEHOLDER: not a real transcript`.

- [ ] Steps 1–5 as above with tests `test_lines_and_speaker_numbering`, `test_placeholder_is_marked`, `test_not_ready_answers_404`, `test_summary_txt_returns_the_saved_summary`, `test_other_users_cannot_download`. Commit `feat(api): transcription.txt and summary.txt`.

### Task 8: Google Speech-to-Text and Gemini provider

**Files:**
- Create: `app/ai/google.py`
- Modify: `app/ai/types.py` (`class Summarizer(Protocol): model: str; def summarize(self, transcript: str) -> str: ...`; `Provider.summarizer: Summarizer | None = None`), `app/ai/registry.py` (`google` branch in `capabilities` and `build_provider`), `app/services/pipeline.py` (after `_analyse`: `summary = self._summarize(aligned)`; saved in `_save` to `Transcript.summary_text/summary_model`), `pyproject.toml` (extra `google`)
- Test: `tests/test_google_provider.py`, `tests/test_pipeline_summary.py`

**Interfaces:**
- Produces: `GoogleSpeech(settings, client: Any | None = None)` with `transcribe(path) -> Transcription` and a matching `GoogleDiarizer(speech: GoogleSpeech)` whose `diarize(path)` returns the turns from the last `transcribe` of that same path (Google returns words and speaker tags together; no second API call). `GeminiSummarizer(settings, client: Any | None = None)` with `model` and `summarize(text) -> str`. `build_google(settings) -> Provider` (name `google`, `placeholder=False`, `embedder=None`). Libraries are imported inside `load()`/constructors so tests and other providers never need them.
- Chunking: read PCM with `read_pcm16_mono` (`app/ai/local.py`), split into ≤55 000 ms pieces, send each as `LINEAR16` at the file's rate with `enable_automatic_punctuation=True`, `diarization_config` 1–6 speakers; shift every word time by the chunk offset; one `AsrSegment` per Google result.
- Summary prompt (exact): `"Summarise this meeting transcript in plain text with three headed sections: Summary, Decisions, Action items (with owner when stated). Use only what the transcript says. If a section has nothing, write 'None'.\n\nTranscript:\n"` + transcription text lines.

- [ ] **Step 1: Failing tests** with fake clients (no network):
  - `test_long_audio_is_sent_in_55_second_chunks_with_offsets` (130 s WAV → 3 calls; second chunk's first word start ≥ 55 000 ms).
  - `test_speaker_tags_become_turns`.
  - `test_silence_gives_an_empty_transcript_and_no_summary`.
  - `test_summarizer_sends_the_prompt_and_returns_text`.
  - `test_a_failed_summary_keeps_the_transcript` (pipeline with a summarizer that raises → recording `completed`, `summary_text is None`).
  - `test_summary_is_saved_with_the_transcript`.
  - `test_registry_reports_missing_google_settings` (capabilities notes say which setting is missing).
- [ ] **Step 2:** run — FAIL. **Step 3:** implement. **Step 4:** `python -m pytest tests/test_google_provider.py tests/test_pipeline_summary.py tests/test_pipeline.py -q` — PASS. **Step 5:** commit `feat(api): Google speech-to-text and Gemini summary`.

### Task 9: Firmware files for Wi-Fi updates

**Files:**
- Create: `app/services/firmware.py`, `app/schemas/firmware.py`, `app/routers/firmware.py`
- Modify: `app/main.py`
- Test: `tests/test_firmware_releases.py`

**Interfaces:**
- Produces: `PUT /api/firmware/{version}` (admin; raw `application/octet-stream`; version matches `^\d+\.\d+\.\d+$`; ≤ `firmware_max_bytes`; 409 if the version exists) → `{version, sizeBytes, sha256}`. `GET /api/firmware/latest` (member) → `{version, sizeBytes, sha256, downloadUrl}` or 404 `no_firmware`; highest version by numeric compare. `GET /api/firmware/{version}/download?token=...` (public) → the bytes; token = `<expiryUnix>.<hex HMAC-SHA256(secret_key, "fw|" + version + "|" + expiry)>`; 403 `link_expired` / `link_invalid`. Files stored under `firmware/<version>.bin` in the existing storage root.

- [ ] Steps 1–5 with tests `test_admin_uploads_and_latest_points_to_it`, `test_numeric_version_order` (1.10.0 > 1.9.0), `test_duplicate_version_409`, `test_too_large_413`, `test_download_with_valid_link`, `test_expired_and_tampered_links_403`, `test_non_admin_cannot_upload`. Commit `feat(api): firmware files for over-the-air updates`.

### Task 10: Documentation and full check

**Files:**
- Modify: `platform/docs/API.md` (new endpoints), `platform/docs/AI.md` (google provider, settings, what was not run), `platform/docs/SECURITY.md` (pairing codes, locket secrets, firmware links), `CLAUDE.md` (summary now by Gemini at the owner's request; Bluetooth design pointer), `platform/api/README.md` (settings table)

- [ ] **Step 1:** update the docs; say plainly that Google and Gemini were run only with fakes here, never against the real services, unless keys were provided.
- [ ] **Step 2:** run `python -m ruff check . && python -m ruff format --check . && python -m mypy app && python -m pytest -q` — all pass (report the counts).
- [ ] **Step 3:** commit `docs: Bluetooth lockets and cloud pipeline`.
