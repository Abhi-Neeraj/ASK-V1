# Running the app locally

The platform's frontend (`platform/web`) is the ASK app: the same code runs as a website and as a phone app.
It talks to the ASK server in `platform/api`. Everything below runs **on your own computer**; nothing is
published.

## 1. The server (first time)

From `platform/api`, with Python 3.11 (see `platform/api/README.md`):

```text
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m app.cli generate-env          writes .env with fresh keys
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m app.cli bootstrap --company "Your company" --name "Your name" --email you@example.com
```

Use a real-looking email address: sign-in refuses special names such as `.test` or `.localhost`.

## 2. Run it

```text
(platform/api)  .venv\Scripts\python -m uvicorn app.main:create_app --factory --port 8000
(platform/api)  set ASK_AI_PROVIDER=stub  and  .venv\Scripts\python -m app.worker
(platform/web)  npm ci  then  npm run build:web  then  npm run serve:web
```

Open <http://localhost:3000> and sign in. With `ASK_AI_PROVIDER=stub` the worker writes **placeholder text**
(the app says so on every recording) and no summary. For real transcripts and summaries set
`ASK_AI_PROVIDER=google` with the Google and Gemini keys (`platform/docs/AI.md`).

## 3. Getting a recording in without a locket

A recording normally comes from the locket over Bluetooth to the phone, which uploads it. On a computer you can
upload a WAV the way the phone does: register a locket (Lockets, administrators), put yourself on it, then
`PUT /api/lockets/{productId}/recordings/{clientId}` with the WAV (see `platform/docs/API.md`).

## What it is not

- **Not Bluetooth.** The website cannot reach a locket. Pairing, Start/Stop recording and firmware updates are
  phone-only and are hidden on the website. The phone build has not been run (no phone or locket here).
- **Not real transcripts** with the stub worker: placeholder text, clearly labelled.
