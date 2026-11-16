# Running ASK: deployment and operations

For whoever installs and looks after the platform. It assumes you can use a terminal and have read the plan ([PLAN.md](PLAN.md)).

> **Read this first: none of the deployment files have been run.** The machine this was written on had no Docker, no PostgreSQL and no way to start a container, so `docker-compose.yml`, the two Dockerfiles, the Caddyfile and the backup scripts are carefully written drafts. What *was* checked, and what was not, is listed at the end under [What has and has not been verified](#what-has-and-has-not-been-verified). Do the first deployment on a machine you can afford to throw away, follow the steps below, and treat every surprise as a bug in the draft rather than in you.

## What runs

| Piece | What it is | Shown to the internet? |
| --- | --- | --- |
| `proxy` (Caddy) | TLS certificates, and sends `/api/*` to the API and everything else to the web app | **Yes, the only piece** (ports 80 and 443) |
| `web` | The ASK app frontend (static web export, served on port 3000). It does not call the API yet | No |
| `api` (FastAPI) | Accounts, lockets, uploads, library, live updates, audit log | No (through the proxy) |
| `worker` | Turns recordings into transcripts. **Off by default** (`--profile processing`) | No |
| `db` (PostgreSQL 16) | All data except audio | No |
| `migrate` | Runs once at each start to bring the database up to date, then exits | No |

Audio files live on a Docker volume (`audio`), not in the database. Model downloads live on `models`. TLS certificates live on `caddy_data`.

Lockets and browsers use the same address. A locket calls `https://<your domain>/api/device/v1/...` with its own credential.

## Before you start

- A machine with Docker and Docker Compose v2 (Docker Desktop is fine on Windows or macOS).
- A **domain name** pointing at that machine, with ports **80 and 443** reachable from the internet. Caddy gets and renews the certificate by itself, and needs both.
- An **SMTP account** for outgoing mail. Production refuses to start without one: invitations and password resets are sent by email.
- Somewhere to put **backups** that is not the same disk.
- Time to read [SECURITY.md](SECURITY.md), especially "Decisions that are yours". This system records people's voices.

## First deployment

All commands from `platform/deploy`.

1. **Make the settings file.**

   ```text
   cp .env.example .env          (Windows: Copy-Item .env.example .env)
   ```

   Fill in every value; the comments say how. Make the two secrets with:

   ```text
   python -c "import secrets; print(secrets.token_hex(24))"       # POSTGRES_PASSWORD (letters and digits only)
   python -c "import secrets; print(secrets.token_urlsafe(48))"    # ASK_SECRET_KEY
   ```

   `.env` is ignored by git. Never paste it into a chat or a ticket, and keep a copy in a password manager: without it the database cannot be opened again.

2. **Check the file before running anything.**

   ```text
   docker compose config
   ```

   It must print without errors. A message such as `set ASK_SECRET_KEY in .env` means a value is missing.

3. **Start it.**

   ```text
   docker compose up -d --build
   ```

   The first build downloads a lot. `migrate` runs first; the API and web app wait for it and for the database.

4. **Check it is healthy.**

   ```text
   docker compose ps
   curl https://<your domain>/api/ready
   ```

   `/api/ready` answers `200` only when the database responds and audio storage is writable. Anything else says which: `database_unavailable` or `storage_unavailable`.

5. **Create the first administrator.** There is no public sign-up; this is the only way in:

   ```text
   docker compose run --rm api python -m app.cli bootstrap --company "Your Company" --name "Your Name" --email you@yourcompany.com
   ```

   It asks for the password and does not show it.

6. **Sign in** at `https://<your domain>/login`, open **Devices → Pair a locket**, and pair one locket (see the [firmware README](../firmware/README.md)). Check it shows **Online**, then that a recording appears under **Recordings**.

7. **Take the first backup now** and practise restoring it (below) before you rely on any of this.

### Turning on processing (optional)

With `ASK_AI_PROVIDER=none` (the default) recordings are stored and wait in `queued`; the dashboard says so. To transcribe them, read [AI.md](AI.md) first. On a Windows laptop the real models were run and measured (speech recognition and speaker matching, CPU only), but **not inside the worker container, not on a GPU and not on Linux**; the full speaker separation (pyannote) has never run because it needs a Hugging Face token and accepted model terms; the speaker-matching threshold is **uncalibrated** until you measure it on your own recordings; and the libraries and weights are about 2 GB.

```text
# in .env:  ASK_AI_PROVIDER=local   INSTALL_AI=true     (ASK_HF_TOKEN=... only for pyannote; ASK_AI_ASR_MODEL=small by default)
docker compose --profile processing build worker      # installs CPU-only torch; pass --build-arg TORCH_INDEX=... for a GPU
docker compose --profile processing up -d
docker compose --profile processing run --rm worker python -m app.cli ai-check --load      # does each model really load and run?
docker compose --profile processing logs worker                                           # "... model ready in ..s", or why it exited
```

The worker loads its models when it starts, so the first start downloads the weights into the `models` volume (a few hundred MB for `small`, more for larger models) and can take minutes. If speech recognition cannot load, the worker exits with code 2 and says why, instead of idling; with `restart: unless-stopped` Docker will keep restarting it, so look at the log. Set `ASK_AI_OFFLINE=true` once the weights are on the volume if the machine must not contact the model hub.

`ASK_AI_PROVIDER=stub` is refused in production on purpose: it writes placeholder text, not transcripts.

## Every day

| Look at | How | What a problem looks like |
| --- | --- | --- |
| The service is up | `GET /api/ready` (use it for any uptime monitor) | Not `200` |
| Containers | `docker compose ps` | Anything restarting or `unhealthy` |
| Logs | `docker compose logs -f api` (also `worker`, `proxy`) | Repeated `unhandled error` lines. They name the error type and where it happened, never the data |
| Lockets | Dashboard → Devices | Offline for longer than expected; a battery level that keeps falling |
| Processing | Dashboard overview | A banner saying recordings are waiting for a worker |
| Who did what | Dashboard → Audit log (administrators) | Failed sign-ins from an address you do not know; deletions you did not make |

The API keeps the dashboard honest about itself: it says when no worker is running, when processing is in placeholder mode, and when a recording failed and why. Trust those messages over guesswork.

## Updating

```text
git pull           (or copy the new files over)
docker compose up -d --build
```

`migrate` applies new database changes before the API starts. **Take a backup first.** Migrations have been written so they do not delete existing rows (and a test proves it on SQLite), but they have not been run on PostgreSQL.

To go back: stop the stack, restore the backup taken before the update, and start the previous version of the files. Do not rely on `alembic downgrade` for data you care about.

## Backups

What to keep, in the order you would need it back:

| What | Where it is | How |
| --- | --- | --- |
| The settings file | `platform/deploy/.env` | Copy it to a password manager. **Not** included in the backup scripts, on purpose |
| The database | `db` | `pg_dump` (the scripts do it) |
| The recordings | `audio` volume | `tar` (the scripts do it) |
| Certificates | `caddy_data` | Optional: Caddy will get new ones |

```text
./backup.sh [folder]            (Linux, macOS, Git Bash)
.\backup.ps1 [-Destination folder]    (Windows PowerShell)
```

Each run makes a time-stamped folder with `database.dump`, `recordings.tar.gz` and a `SHA256SUMS` file. Run it from a scheduler (Task Scheduler, cron) at least daily, and copy the result off the machine.

**A backup is as sensitive as the live system**: it holds recordings of people's voices and their voice prints. Encrypt it before it leaves the machine, limit who can read it, and delete old ones on a schedule that matches your retention promise (see [Data lifecycle](#data-lifecycle)). Deleting a person or a recording in the dashboard does **not** reach into old backups.

### Restoring

Practise this on a spare machine before you need it.

```text
docker compose down
docker volume rm deploy_pgdata deploy_audio         # only on the machine you are restoring to; check the names with `docker volume ls`
docker compose up -d db
# wait until `docker compose ps` shows db healthy, then:
docker compose exec -T db pg_restore -U ask -d ask --clean --if-exists < backups/<stamp>/database.dump
docker compose run --rm --no-deps --user root -v "$PWD/backups/<stamp>:/backup" --entrypoint tar api xzf /backup/recordings.tar.gz -C /data
docker compose up -d
```

Then check `/api/ready`, sign in, and open a recording and play it. Check the `SHA256SUMS` first (`sha256sum -c SHA256SUMS`).

## Secrets and keys

| Secret | Where | Rotating it |
| --- | --- | --- |
| `ASK_SECRET_KEY` | `.env` | Safe at any time. It keys CSRF tokens and pairing codes only, so lockets stay paired and sessions stay valid. Pairing codes not yet used stop working, and browsers fetch a new CSRF token at their next page load (a reload) |
| `POSTGRES_PASSWORD` | `.env` and the `pgdata` volume | Change it inside PostgreSQL (`ALTER USER ask PASSWORD '...'`) **and** in `.env`, then restart |
| `ASK_SMTP_PASSWORD` | `.env` | Change at the mail provider and in `.env`, restart `api` |
| `ASK_HF_TOKEN` | `.env` | Revoke at Hugging Face and replace |
| A locket's credential | The locket's own storage | **Revoke** the locket (Devices → Revoke), then add the hardware as a new locket. Revoking takes effect at once |

## People and lockets

| Situation | Do this |
| --- | --- |
| Someone leaves | Settings → Team → **Switch off**. It signs them out everywhere and stops them signing in. Then move their locket on the Devices page. Their recordings stay |
| A laptop was left signed in | Settings → Team → **Sign out everywhere** (they can sign in again) |
| Someone's role changes | Settings → Team → the role box. It takes effect at once |
| A locket changes hands | Devices → the **Assigned to** box. What it has already recorded stays with the previous wearer; new recordings go to the new one. If the card says recordings are still waiting to upload, wait until the queue is empty first, or those will go to the new wearer too |
| A locket is lost or stolen | Devices → **Revoke**. Its credential stops working immediately. Recordings it already uploaded stay |
| Someone withdraws consent | People → **Withdraw consent**. It deletes their voice print and unnames them on past transcript lines |
| Someone asks for their voice to be removed | People → **Remove voice** (profile only) or **Remove** (profile, consent and name) |
| Delete a recording | Open it → Delete. It disappears at once and its audio and transcript are purged after the grace period (Settings) |

An administrator cannot switch themselves off or change their own role. If you have only one administrator, make a second before you need one.

## Data lifecycle

| Data | Kept for |
| --- | --- |
| Recordings | Until deleted, or until the retention rule (Settings) removes them. Retention counts from when a recording was **stored**. A deleted recording's file and transcript are purged after the grace period (default 7 days) |
| Voice prints | Until the person's voice or consent is removed, or the speaker model changes (then they must enroll again). Enrollment audio is deleted the moment the print is made |
| Audit log | **For ever.** Nothing prunes it. It holds sign-in addresses (personal data); decide your own rule and prune with a database job if you need one |
| Live-update events | 24 hours |
| Sign-in sessions | 24 hours idle, 7 days at most |
| Backups | Until you delete them |

## When something is wrong

| Symptom | Likely cause and what to do |
| --- | --- |
| `docker compose config` complains about a variable | A value is missing in `.env` |
| `migrate` exits with an error | Read `docker compose logs migrate`. The API will not start until it succeeds. Do not delete the volume to "fix" it: restore a backup |
| The proxy has no certificate / browsers warn | The domain does not point at this machine, or ports 80/443 are blocked. `docker compose logs proxy` says which |
| `/api/ready` says `storage_unavailable` | The `audio` volume is full or read-only. Free space; recordings are not accepted meanwhile (lockets keep them and retry) |
| Lockets show offline but have power | They cannot reach `https://<your domain>`. Check the locket's `status` over its USB cable (see the [firmware README](../firmware/README.md)) |
| Recordings stay `queued` | No worker. Start it (`--profile processing`), or check `ASK_AI_PROVIDER` |
| A recording is stuck mid-processing | Its worker died. Another worker takes it back after 3 minutes (`ASK_JOB_LOCK_TIMEOUT_SECONDS`). Or open the recording → **Process again** |
| The worker keeps restarting | It exits when speech recognition cannot load. `docker compose --profile processing logs worker` shows why (no memory, weights missing with `ASK_AI_OFFLINE=true`, a typo in `ASK_AI_ASR_MODEL`) |
| A failed recording says "What went wrong" | Managers and administrators see the cause (cleaned of paths and secrets) on the recording page; fix it, then **Process again** |
| Sign-ins are refused with "too many attempts" | Five failed attempts a minute per address and email. Wait for the number of seconds the answer gives |
| Everyone shares one rate-limit bucket | Requests reach the API without the proxy's address header. Keep `ASK_TRUST_FORWARDED_FOR=true` and `ASK_TRUSTED_PROXY_HOPS=1` behind the Compose proxy |
| Live updates do not arrive | Something between the browser and the API buffers `text/event-stream`. The Caddyfile has a rule for it; keep it |

## What has and has not been verified

**Checked, on SQLite and on a laptop with no Docker:**

- The production configuration the Compose file builds passes the application's own production rules (https addresses, PostgreSQL, SMTP mail, no placeholder processing), every variable in `.env.example` is a real setting, and every variable Compose reads is in the template (`tests/test_deploy_files.py`).
- The Compose file's structure: only the proxy publishes ports, the schema is migrated before anything uses it, the worker does not start unless asked, no secret is written into the file.
- The Caddyfile puts the live-update rule before the general API rule and does not compress or buffer it.
- The migrations generate valid-looking PostgreSQL SQL for every table and can be taken back down (`tests/test_postgres_sql.py`).
- The two backup scripts parse (`bash -n`, the PowerShell parser).

**Checked on a real PostgreSQL 16.15** (a portable Windows server, local only; not the Docker image):

- Every migration up, down and up again, with no drift from the models; migration 0004 with rows kept and the foreign key enforced.
- The whole ordinary test suite, run with `ASK_TEST_DATABASE_URL`.
- The job queue's `FOR UPDATE SKIP LOCKED`: four racing workers, and two separate worker processes sharing 60 recordings, each claimed exactly once.
- A concurrent duplicate upload, and an upload followed by processing.
- The database half of the backup: `pg_dump --format=custom`, then `pg_restore --clean --if-exists` into a fresh database, equal row counts in every table, and the application working on the copy.

**Not checked, so not known to work:**

- `docker compose config`, `docker build` (both images), `docker compose up`. Nothing was built or started.
- PostgreSQL **in the container**, performance and connection limits under load, TLS to the database, and a major-version upgrade.
- The Caddyfile syntax and TLS certificate issuing.
- The backup and restore **scripts** (their `docker compose` wrappers and the recordings archive), and a restore into a clean machine.
- The web image's start (`node serve.mjs` in the runtime image). `npm run build:web` and `node serve.mjs` themselves were run and pass.
- The worker **image** with the `[ai]` libraries (CPU wheels). The real models themselves were run on a Windows laptop outside any container; see [AI.md](AI.md).
- Behaviour under real load, a real mail server, a real locket, and any failure of the machine, disk or network.
