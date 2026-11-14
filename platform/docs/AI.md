# Processing: transcription, speaker separation and speaker matching

How recordings become transcripts, what runs where, and exactly what has and has not been verified. Developer-facing: this page names the libraries, the dashboard deliberately does not.

## Read this first: what is verified

| Part | State |
| --- | --- |
| Transcription (faster-whisper) | **Run on real audio, on CPU.** Real weights were downloaded and loaded; real speech (synthetic text-to-speech voices) was transcribed with word error rates of 0 to 2% on clean audio and 2 to 4% at about 10 dB of added noise (`small` and `tiny.en` respectively); silence and faint noise produced no invented words. Not run on a GPU. Not run on real people, real rooms or real microphones, so those accuracy figures say nothing about your use. |
| Speaker matching (SpeechBrain voice prints) | **Run on real audio.** Voice prints were made, enrolled through the API, compared, and used to name speakers; the safeguards (same company, consent, threshold, margin, minimum speech, enough samples, consent withdrawn mid-recording) were each tested with the real model, and each test was shown to fail when its safeguard is removed. Only two synthetic voices were available, so **the threshold is not calibrated** and these results prove the machinery, not an error rate. |
| Speaker separation, experimental method (`ASK_AI_DIARIZER=clustering`) | **Run on real audio**, and labelled experimental everywhere it appears. It needs no token. It separated two synthetic voices taking turns with 99% purity (100% with noise added) and did not split one voice into two. It does not handle two people talking at once. Not tried on real people. |
| Speaker separation by pitch (`ASK_AI_DIARIZER=pitch`) | **Run on real audio**, and labelled experimental everywhere it appears. It needs no model and no token. It separated two synthetic voices of similar pitch taking turns, and made-up voices that differ in pitch, and kept one voice as one speaker. Not tried on real people; it does not handle two people talking at once. See [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md). |
| Voice signatures (pitch plus voice colour) | **Run on real audio** (two synthetic voices): enrolled from WAV uploads, matched, and used to name every line correctly in a real-recognizer run. **Not calibrated** on real people: the threshold is a starting point, and the result is never identity verification. See [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md). |
| Meeting summary (decisions, actions, owners; no generative AI) | **Run on real speech** with the words engine and with the meaning-based engine (a sentence model, installed and its weights fetched once with the owner's go-ahead, then run offline on CPU): 10 of 12 paraphrases right against 7 of 12 for the words engine; both gave the same summary of the real-speech conversation. A small, single-author test set: an indication, not an accuracy figure. See [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md). |
| Speaker separation, pyannote (`ASK_AI_DIARIZER=pyannote`) | **Installed and importable, never run.** Its model is gated: it needs a Hugging Face account, acceptance of the model's terms and a token, and none were available (an unauthenticated download returns 401). The adapter was updated for the installed version (it hands the library decoded audio so FFmpeg is not needed), but whether the model name in `ASK_AI_DIARIZATION_MODEL` is the right one for pyannote.audio 4.x is **unknown** until a token is available. |
| GPU | Not run (no GPU here). Settings exist; the code path is unproven. |
| PostgreSQL | **Run.** The worker's queue (`FOR UPDATE SKIP LOCKED`), the migrations and the pipeline were run on PostgreSQL 16.15, including two worker processes sharing one queue (with the placeholder provider; the real models were run on SQLite). |
| Docker | Not run. |

What the verification environment was: Windows 11, Intel Core i7-1165G7 (4 cores, 8 threads), 12 GB RAM, no GPU, Python 3.11.9, CPU-only torch. Exact versions are pinned in `pyproject.toml` (extra `ai`).

## What the worker does

`python -m app.worker` is a separate process from the API. It loads its models once at start-up, then claims queued recordings from the database, one at a time, and moves each through:

```text
queued -> validating -> transcribing -> diarizing -> identifying_speakers -> aligning -> completed
                                                                                     \-> needs_review
                                  (any stage) -> retrying -> queued     (temporary error, with a delay)
                                  (any stage) -> failed                 (permanent error, or retries used up)
```

- **Transcribing** turns speech into timed text.
- **Diarizing** works out who spoke when, as anonymous turns (`SPEAKER_00`, `SPEAKER_01`). It does not know names.
- **Identifying speakers** compares each turn's voice with the voice prints of people **in the same company whose consent is on record**, and names a turn only if it clearly matches.
- **Aligning** puts the text and the turns together.

Diarization (grouping speech by voice) and speaker identification (matching a group to a registered person) are separate stages with separate models and separate failure modes. A recording can have the first without the second: that is every recording until someone is enrolled, and every unknown voice afterwards.

A stage is only announced if it actually runs. If speaker separation is not available the recording goes straight from transcribing to aligning, as one speaker, and the transcript says so.

`needs_review` means the result should be checked by a person: uncertain alignment, or speech-recognition confidence below 0.5. Placeholder text is not flagged for review; it is flagged as a placeholder.

### Start-up check (warm-up)

When the worker starts it loads every model before taking a job. So the first recording does not pay for loading, and a model that cannot load is found at start-up, not on someone's recording:

- Speech recognition cannot load (no weights and downloads forbidden, no memory, a bad device setting): the worker **exits with code 2 and says why**. Nothing is claimed; recordings wait in `queued` and the dashboard says no worker is running.
- Speaker separation or speaker matching cannot load: the worker starts **without it**, says so in its log and in `GET /api/processing`, and the transcripts say speakers were not separated. Transcription carries on.

`--no-warm-up` skips this (used by tests); models then load on first use.

### Reliability

| Behaviour | Detail |
| --- | --- |
| Claiming | One atomic conditional update, so two workers never take the same job. On PostgreSQL it also uses `FOR UPDATE SKIP LOCKED`; that path was run on PostgreSQL 16.15 with four racing workers and with two worker processes (each job claimed exactly once). |
| Attempts | Counted when a job is claimed, so a crash mid-run still counts. Three attempts by default. |
| Retries | A temporary error puts the job back in the queue after a delay: `ASK_JOB_RETRY_BASE_SECONDS` (30 s) after the first attempt, 60 s after the second. Permanent errors (a file that is not audio, a missing or damaged recording) fail at once. |
| Crashes | A running job renews its claim three times per `ASK_JOB_LOCK_TIMEOUT_SECONDS` (**180 s** by default; it was 15 minutes, which left a recording stuck for a quarter of an hour after a crash). If the worker is killed the claim lapses and another worker takes the job. A model that runs longer than the timeout is fine as long as the worker is alive. **Tested with a real worker process killed with the real models running**, then a new process (`tests/real/test_real_worker_process.py`). |
| Shutdown | Ctrl+C or SIGTERM is noticed between stages: the stage in progress finishes, then the job goes back to `queued` with its attempt refunded, and the worker exits. |
| Saving | The finished transcript is written in one transaction, only if this worker still holds the claim. No database transaction is held open while a model runs. |
| Same recording twice | One transcript per recording (a database constraint), replaced when a recording is processed again. Tested with real models. |
| Deleted mid-run | A recording deleted while it was being processed is dropped quietly; no transcript is saved. |
| Errors shown | Messages are cleaned of file paths and tokens before they are stored or shown. |
| Reprocess | `POST /api/recordings/{id}/reprocess` replaces the transcript. **Corrections made to the old one are lost**, and the web app says so before asking. |

### What a model failure looks like

The adapters translate a library's failure into a message an operator can act on, and the job is retried unless the audio itself is the problem.

| Cause | What is recorded | Retried? |
| --- | --- | --- |
| Not enough memory | "There is not enough memory to run the speech recognition model (...). Close other programs, use a smaller model (ASK_AI_ASR_MODEL)..." | Yes (it may pass) |
| Disk full | "The disk is full, so the ... model could not be saved or run." | Yes |
| Weights missing and downloads not possible (offline, no network, wrong name, gated and no token) | "The ... model (...) is not on this machine and could not be downloaded. Check the network, the model name, any access token and its terms, or copy the weights into ASK_AI_MODEL_DIR and set ASK_AI_OFFLINE=true." | Yes |
| GPU chosen but unusable | "... could not use the chosen device. Set the device to cpu ..." | Yes |
| Audio that cannot be decoded, or is damaged after upload | "The audio could not be decoded..." / "The stored audio no longer matches its checksum, so it may be damaged." | **No** |
| Anything else | "The ... model (...) failed (ErrorType). The server log has the details." | Yes |

## Providers

`ASK_AI_PROVIDER` chooses what the worker (and enrollment) can do.

| Value | What it is | Where it may run |
| --- | --- | --- |
| `none` (default) | Nothing. The worker refuses to start (exit code 2). Recordings wait in `queued`, and the dashboard says no worker is running. | Anywhere |
| `stub` | A development stand-in. Writes a fixed line of **placeholder** text per recording. Never names anyone, has no speaker separation and no speaker model. | Development and tests only. **Production refuses to start with it.** |
| `local` | Real models on this machine (below). | Anywhere the extra and weights are present. |

### The placeholder stub is labelled everywhere

Stub output is flagged `isPlaceholder` in the database and `placeholder: true` in the API (on the recording and on its transcript). The dashboard shows a Placeholder badge in lists and on the recording page, a warning that the text is not what was said, and "Placeholder only" under processing details. TXT and JSON exports carry the same statement. The stub cannot enroll or identify voices, so nobody is ever named by it. A stub transcript is never counted as real: the real-model tests refuse to run on it, and the benchmark refuses to start against it.

## Real models (`ASK_AI_PROVIDER=local`)

| Capability | Library (pinned) | Weights | Licence | Access | Size on disk |
| --- | --- | --- | --- | --- | --- |
| Transcription | faster-whisper 1.2.1 (CTranslate2 4.8.4) | `Systran/faster-whisper-<size>`; default `small` | MIT | Public | tiny.en 72 MB, small 461 MB (medium and large-v3 are several GB) |
| Speaker matching, and the experimental separator | SpeechBrain 1.1.2, torch 2.14.1, torchaudio 2.11.0 | `speechbrain/spkrec-ecapa-voxceleb` | Apache-2.0 (trained on VoxCeleb; read its terms for commercial use) | Public | about 85 MB of weights used |
| Speaker separation (full) | pyannote.audio 4.0.7 | `pyannote/speaker-diarization-3.1` (default setting) and the segmentation and embedding models it loads | MIT (pipeline), CC-BY-4.0 for at least one of its parts | **Gated**: Hugging Face account, accept the terms on the model page, create a token | about 100 MB. **Never downloaded here** |

Licences were read from each model's page on the hub on the day this was written; they change. This is not legal advice, and the speaker models process biometric data, so read the terms before a commercial deployment.

Libraries on disk (CPU only): about 1.4 GB in the virtual environment, of which torch is about 500 MB. On Linux the default `torch` wheel bundles CUDA and is much larger: install the CPU wheels from `https://download.pytorch.org/whl/cpu` first (the API Dockerfile does).

Recordings are never sent anywhere. Downloading weights contacts the model hub (and sends nothing about your audio). `ASK_AI_OFFLINE=true` forbids even that: only weights already on disk are used, and a missing model is reported at start-up.

### Set it up

```powershell
cd platform\api
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,ai]"       # about 15 minutes and 1.4 GB the first time
# In .env (never in chat or in git):  ASK_AI_PROVIDER=local
#   optional: ASK_AI_ASR_MODEL=small  ASK_AI_MODEL_DIR=..\data\models  ASK_AI_DIARIZER=clustering
.\.venv\Scripts\python -m app.cli ai-check                 # what would work; loads nothing
.\.venv\Scripts\python -m app.cli ai-check --load          # downloads weights if needed, loads each model, runs it once
.\.venv\Scripts\python -m app.worker
```

`ai-check` exits non-zero if transcription is unavailable, or with `--load` if any model fails to run.

### Try it on one recording, with no database

```powershell
.\.venv\Scripts\python tools\make_sample_audio.py          # once: synthetic speech under platform\data\samples\real-audio (Windows only)
.\.venv\Scripts\python -m app.cli process-file ..\data\samples\real-audio\conversation.wav --voices ..\data\samples\real-audio\voices
```

It runs exactly the worker's stages on one file and prints each line with its times, who (if anyone) the voice matched, and how long each stage took. `--voices` enrolls the people in a folder **in memory for that run only**. `--json` gives the result as JSON. It never writes to the database or to storage.

### The real-model tests

```powershell
$env:ASK_REAL_MODELS = "1"          # off by default: they need weights and take minutes
$env:ASK_AI_ASR_MODEL = "tiny.en"   # or small
.\.venv\Scripts\python -m pytest tests/real -q -s
```

They run the real models through the API and the worker: transcription accuracy and timing, silence, corrupt and truncated files, a recording damaged after upload, a crash in inference and its retry, retries running out, a real worker process killed mid-recording, one transcript after processing twice, speaker separation, naming only enrolled people of the same company with consent, consent withdrawn before and during processing, and voice prints never leaving the server. A test that would pass on the stub is a bug in the test; the suite refuses to run on it. What a passing run proves is described at the top of this page.

### Measure how long it takes

```powershell
.\.venv\Scripts\python tools\benchmark_pipeline.py --model small --voices --lengths 5,10,20,30 --repeat 10
```

It starts a throwaway API and a real worker on a temporary database, uploads real speech over HTTP, and reports each stage's median and 95th percentile, queueing for a burst of clips, and the worker's memory and processor use. `--resilience` also stops the API with clips waiting and kills the worker mid-recording. `--cpu-threads N` limits the worker's threads.

What it measured on the machine described at the top (4 cores, no GPU, one clip at a time, models already loaded, 10 clips per length; the full tables, the machine and the other configurations are in [FINAL_COMPLETION_REPORT.md](FINAL_COMPLETION_REPORT.md)):

| Configuration | A 10 s clip is readable after (median / 95th percentile) | A 60 s clip |
| --- | --- | --- |
| `small`, experimental separation, voice matching with two people enrolled | 9.8 s / 10.8 s | 34.4 s / 42.4 s |
| `small`, transcription only | 5.8 s / 6.9 s | (30 s clip: 12.4 s / 14.6 s) |
| `tiny.en`, separation and matching (6 clips) | 5.0 s / 5.1 s | (20 s clip: 8.4 s / 9.0 s) |

So a short clip becomes readable well inside 60 seconds here. Two caveats matter more than the headline: **throughput** (with `small`, separation and matching on, a 10 s clip takes about 10 s to process, so continuous 10-second chunks would just keep pace and any burst queues; six clips uploaded at once were readable after 8, 16, 24, 32, 40 and 48 s), and the **loaded machine** (a worker limited to one thread took 6.5 s for a `tiny.en` clip instead of 5.0 s). Whisper works in 30-second windows, so a 5 s clip costs almost as much as a 10 s one. A crashed worker's recording is picked up again after the job lock timeout (180 s) plus the new worker's start-up: 187 s were measured.

Under a hard memory cap (a Windows job object) the real worker either refuses to start with "not enough memory" and exit code 2 (700 MB), or dies while loading its libraries (at 250 MB the maths library gives up; at 1200 MB Windows refused to load a database DLL with "the paging file is too small", which shows that a cap on committed memory bites well before the resident figures of about 1 GB suggest); it never started up "successfully" without being able to transcribe, and a recording being processed when a worker dies is recovered as above (`tests/real/test_real_memory_limit.py`).

## Choosing who separates speakers

`ASK_AI_DIARIZER`:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | pyannote if it is installed **and** `ASK_HF_TOKEN` is set; otherwise nobody separates speakers and every recording is one speaker (the transcript says so). |
| `pyannote` | The same, but say clearly if it cannot run. |
| `pitch` | **Experimental.** Needs no model, no token and no network. Finds the voiced speech, measures windows of about 3 s (base pitch and voice colour), never groups windows whose pitch is far apart (`ASK_DIARIZATION_PITCH_GATE_OCTAVES`, 0.45) and otherwise groups them by voice colour (`ASK_DIARIZATION_PITCH_CLUSTER_DISTANCE`, 1.2). Separates voices that differ clearly in pitch well, voices of similar pitch only by colour, and not two people talking at once. Only tried on synthetic voices. See [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md). |
| `clustering` | **Experimental.** Finds the speech, takes a voice print of each short window (1.5 s, every 0.75 s) with the speaker model, and groups windows that sound alike (average-linkage clustering at `ASK_DIARIZATION_CLUSTER_DISTANCE`, 0.6). Needs no token. It cannot separate two people talking at once, it needs a window or two of a voice to notice it, and its threshold was only tried on synthetic voices. The dashboard tells people to check who said what by listening. |

Prefer pyannote where you can have it. Whichever is used, nothing is named by separation: it only says "the same voice".

## Speaker matching

For each speaker turn with enough speech (`ASK_SPEAKER_MIN_SPEECH_SECONDS`, default 3), the worker takes a voice print and compares it by cosine similarity with every eligible voice print. A name is given only if **both** hold:

1. the best score is at least `ASK_SPEAKER_MATCH_THRESHOLD`, and
2. it beats the runner-up by at least `ASK_SPEAKER_MATCH_MARGIN` (default 0.05).

Otherwise the line reads **Unknown speaker**. A wrong name is worse than no name, so doubt always loses. The score shown beside a name is that similarity ("Voice match 87%"): **it is not the probability that the name is right, and a match is not proof of who spoke.**

A voice print is eligible only if it belongs to a person **in the same company**, with consent currently **granted**, with at least `ASK_VOICE_ENROLL_MIN_SAMPLES` samples, made by the **same speaker model** now in use. When the model changes, existing voices show "Voice out of date" and stop being used until the person enrolls again. Withdrawing consent or removing a voice deletes the print immediately and unnames the person on past lines. Consent is checked again, under a lock where the database has one, just before a voice print is saved or a name is written to a new transcript, so withdrawing it while a sample or a recording is being processed cannot leave a print or a name behind.

Company and consent are each enforced twice (when candidates are chosen, and again just before saving). That is deliberate, and it is why a test that removes only one layer still passes: the tests were checked by removing both layers together.

### The default threshold is not calibrated

`ASK_SPEAKER_MATCH_THRESHOLD` defaults to 0.5. That is a placeholder, **not a measured value**. The right threshold depends on the model, the microphones and the rooms. On the two synthetic voices used here, samples of the same voice scored 0.71 to 0.87 against each other (12 pairs) and samples of different voices 0.19 to 0.31 (16 pairs), so the default 0.5 sits in a wide gap; `calibrate-speakers` suggested 0.549 and printed three warnings that this is far too little data to trust (6 samples, 9 comparisons between people, 2 people). **That says nothing about real people**, who sound more alike than two text-to-speech voices and vary with a cold, a room or a microphone. Calibrate on your own people, with their consent:

```text
samples/
  priya/   take1.wav  take2.wav  ... (at least 5, on different days or in different rooms)
  marcus/  take1.wav  ...
```

```powershell
.\.venv\Scripts\python -m app.cli calibrate-speakers --samples samples --max-false-accept 0.01
```

It prints the suggested threshold, how often different people would be wrongly matched, how often real matches would be missed, and warnings when there is too little data to trust it (for example fewer than 100 different-person comparisons, where a 1% error rate cannot even be measured). Then set `ASK_SPEAKER_MATCH_THRESHOLD` and restart the worker. It does not change anything by itself. No universal accuracy figure is claimed anywhere.

### Enrollment

`POST /api/people/{id}/enroll` takes one WAV sample (a manager, with the person's consent on record). Between `ASK_VOICE_SAMPLE_MIN_SECONDS` and `ASK_VOICE_SAMPLE_MAX_SECONDS` (3 to 30 s). The audio is turned into a voice print, merged into the person's running average, and **deleted immediately**; it is never kept. A person is ready after `ASK_VOICE_ENROLL_MIN_SAMPLES` (3); more than `ASK_VOICE_ENROLL_MAX_SAMPLES` (10) is refused. The web app can record from a microphone (converted in the browser to 16 kHz mono WAV) or accept a WAV file.

### Voice prints are biometric data

Where they are, who can reach them, how they go, and what to decide: [SECURITY.md](SECURITY.md#voice-prints).

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `ASK_AI_PROVIDER` | `none` | `none`, `stub` or `local`. |
| `ASK_AI_ASR_MODEL` | `small` | faster-whisper model size (`tiny.en`, `base.en`, `small`, `medium`, `large-v3`...), or a path to converted weights. |
| `ASK_AI_ASR_DEVICE`, `ASK_AI_ASR_COMPUTE_TYPE` | `auto`, `int8` | Where and how precisely speech recognition runs. `int8` suits a CPU; use `float16` on a GPU. |
| `ASK_AI_ASR_BEAM_SIZE` | 5 | Candidate sentences kept while decoding. 1 is fastest and a little less accurate. |
| `ASK_AI_ASR_LANGUAGE` | detect | Force a language code such as `en`. |
| `ASK_AI_TORCH_DEVICE` | `auto` | Where speaker separation and matching run: `auto` (a GPU if there is one), `cpu`, `cuda`. |
| `ASK_AI_CPU_THREADS` | 0 | CPU threads for the models. 0 lets each library choose (usually every core). |
| `ASK_AI_MODEL_DIR` | libraries' own caches | Where downloaded weights live. |
| `ASK_AI_OFFLINE` | `false` | Never contact the model hub; fail clearly if weights are missing. |
| `ASK_HF_TOKEN` | none | Hugging Face token for the gated separation model. A secret; never logged or returned. |
| `ASK_AI_DIARIZER` | `auto` | `auto`, `pyannote`, `clustering` or `pitch` (above). |
| `ASK_SIGNATURE_*`, `ASK_DIARIZATION_PITCH_*`, `ASK_ANALYSIS_ENGINE`, `ASK_AI_TEXT_*` | see the file | Voice signatures, separation by pitch and the summary: [VOICE_SIGNATURE.md](VOICE_SIGNATURE.md#settings). |
| `ASK_AI_DIARIZATION_MODEL`, `ASK_AI_SPEAKER_MODEL` | pyannote 3.1 pipeline, SpeechBrain ECAPA | Which models to load. |
| `ASK_DIARIZATION_CLUSTER_DISTANCE` | 0.6 | Experimental separator only: windows closer than this are the same voice. |
| `ASK_SPEAKER_MATCH_THRESHOLD`, `ASK_SPEAKER_MATCH_MARGIN` | 0.5, 0.05 | See above. The threshold is uncalibrated. |
| `ASK_SPEAKER_MIN_SPEECH_SECONDS` | 3 | Speech a speaker needs in a recording before being compared. |
| `ASK_VOICE_ENROLL_MIN_SAMPLES`, `ASK_VOICE_ENROLL_MAX_SAMPLES` | 3, 10 | Samples to be ready, and the most kept. |
| `ASK_VOICE_SAMPLE_MIN_SECONDS`, `ASK_VOICE_SAMPLE_MAX_SECONDS` | 3, 30 | Length of one enrollment sample. |
| `ASK_JOB_RETRY_BASE_SECONDS` | 30 | First retry delay; doubles each attempt. |
| `ASK_JOB_LOCK_TIMEOUT_SECONDS` | 180 | A silent worker's job is taken back after this. |
| `ASK_WORKER_POLL_SECONDS` | 2 | How often an idle worker looks for work. |
| `ASK_WORKER_ONLINE_SECONDS` | 30 | The dashboard says no worker is running after this long without a heartbeat. |

## Troubleshooting

| Symptom | Cause and what to do |
| --- | --- |
| The worker exits at once with code 2 | Read its log line. `No processing provider is configured` means `ASK_AI_PROVIDER=none`. `faster-whisper is not installed` means `pip install -e ".[ai]"`. A "not on this machine and could not be downloaded" message means no network, a typo in `ASK_AI_ASR_MODEL`, or `ASK_AI_OFFLINE=true` with no weights in `ASK_AI_MODEL_DIR`. |
| Recordings stay `queued` | No worker is running (the dashboard says so), or the worker started without speech recognition and exited. |
| Every transcript is one speaker | Speaker separation is not available: no token for pyannote, or `ASK_AI_DIARIZER` is not `clustering`. `GET /api/processing` and `ai-check` say which. |
| Nobody is ever named | Nobody is enrolled with consent and three samples, or `ASK_SPEAKER_MATCH_THRESHOLD` is above what your voices reach, or speakers have less than `ASK_SPEAKER_MIN_SPEECH_SECONDS` of speech, or the speaker model changed ("Voice out of date"). |
| The wrong person is named | The threshold is too low for your people. Calibrate (above); until then treat every name as unverified, and raise the threshold. |
| "not enough memory" | Close other programs, use a smaller `ASK_AI_ASR_MODEL`, or add RAM. This machine has 12 GB and the whole worker (all three models) peaked at about 1 GB with `tiny.en`. |
| SpeechBrain warns about symlinks on Windows | Harmless: on Windows the model files are copied instead. |
| A pyannote download fails with 401 or 403 | The token is missing or wrong, or the model's terms were not accepted on its page (each model the pipeline loads has its own). |
