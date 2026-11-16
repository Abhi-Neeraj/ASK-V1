# Voice signatures, pitch and the summary

This is the voice-to-summary chain added for the client brief (client: Lohit). It lives in the **platform** (`platform/api` and `platform/web`), uses the existing screens, and changes none of their design: it adds sections to the recording page and the People page, in the same components and palette.

```
audio -> pitch (base / low / high) -> WAV upload -> voice signature -> stored per person
      -> matching (pitch gate, then voice colour) -> speaker-labelled transcript
      -> sentence analysis (what / context / activity / task / owner) -> rule-built summary
```

| Step of the brief | Where it is | How it was checked |
| --- | --- | --- |
| Same UI, nothing redesigned | `platform/web`: new sections only (`recording-insights.tsx`, `signature-dialog.tsx`, `signature-check.tsx`) | Web build, 386 web tests, the demo site opened and viewed at desktop and phone width |
| Pitch: high, low and base pitch per speaker, used as the first layer | `app/voiceid/pitch.py`; `app/voiceid/diarize.py` (separation by pitch); the pitch gate in `app/voiceid/matching.py` | Unit tests on made-up voices of known pitch; real Windows speech |
| WAV upload and voice signature | `POST /api/people/{id}/signature` -> `app/voiceid/signature.py` -> table `person_signatures` | API tests; real speech |
| Upload and match against existing signatures | `POST /api/people/signature-matches` | API tests (tenancy, consent, thresholds); real speech |
| Speaker-labelled transcript | the worker (`app/services/pipeline.py`) names speakers by signature after the speaker model | End-to-end tests with the real worker; real speech with the real recognizer |
| Function-based summarizer (what, context, activity, task, owner) | `app/insights/` | 80 unit tests; real speech, with the words engine and with the real sentence model |
| Summary with no AI | `app/insights/summary.py` (templates) | Unit tests; real speech |

## What each part does

**Pitch.** The fundamental frequency is measured every 10 ms with the YIN method on the frames that are clearly voiced, in plain arithmetic (numpy only: no model, no download). A speaker's **base pitch** is the median, **low pitch** the 5th percentile and **high pitch** the 95th, so one wrong frame cannot move them. With less than half a second of voiced speech nothing is reported. Pitch alone never names anyone.

**Voice signature.** The speaker's pitch profile plus 24 numbers describing the colour of the voice (the average and spread of 12 cepstral coefficients over the voiced frames). It is text-independent and independent of loudness. Several samples are averaged, each counting equally.

**Matching.** Two layers, in order. (1) *Pitch gate*: a voice is only compared with people whose base pitch is within `ASK_SIGNATURE_PITCH_GATE_OCTAVES` (0.4 octave, about 5 semitones), so a low voice is never compared with a high one. (2) *Similarity*: among those who pass, the closest is named only if its similarity reaches `ASK_SIGNATURE_MATCH_THRESHOLD` (0.5) **and** beats the next person by `ASK_SIGNATURE_MATCH_MARGIN` (0.1). Otherwise the speaker stays "Unknown speaker": a wrong name is worse than no name. The speaker model keeps priority; the signature only names speakers it left unnamed.

**Separation by pitch** (`ASK_AI_DIARIZER=pitch`, **experimental**, off by default). Needs no model, no token and no network. The voiced speech is cut into windows of about 3 s; windows are never grouped across the pitch gate (`ASK_DIARIZATION_PITCH_GATE_OCTAVES`, 0.45) and are otherwise grouped by voice colour (`ASK_DIARIZATION_PITCH_CLUSTER_DISTANCE`, 1.2). It is labelled experimental on the recording page. It does not handle two people talking at once, and a change of speaker with no pause can be missed.

**Summary (no generative AI): how much is model and how much is function.** Only one small part is a model: the sentence model (below), which turns each sentence into 384 numbers that stand for its meaning. It writes nothing. Everything else is a plain function, so the summary is written by templates and the same transcript always gives the same summary. Weighing it up: **70% functions, 30% meaning** (the score of a sentence for an activity is 0.70 of the cue words and verbs plus 0.30 of closeness in meaning; measured against 65/35 on 100 labelled sentences with both engines, the results were identical). The functions are:

- **What was spoken**: the transcript line, split into sentences.
- **Context** (`app/insights/context.py`): each sentence is compared with the three before it; if the closest is more similar than the threshold it stays in that context, otherwise a new context (topic) starts. The model supplies the vectors; the comparison is a function. **The threshold is 0.30, not the 0.6 that was suggested**: on four hand-labelled meetings (37 steps, 8 real topic changes) the model's similarities between sentences on one subject mostly sit between 0.35 and 0.5, so at 0.6 almost every sentence was called a new topic (24% right), while between 0.25 and 0.35 it was 84 to 92% right. A small, single-author set: an indication, not an accuracy figure. `ASK_CONTEXT_SIMILARITY` changes it. **The words engine cannot do this** (best F1 0.43), so with it no contexts are worked out and no topic is claimed, unless a threshold is chosen on purpose.
- **Key activity**: cue words and verbs ("will", "need to", "please", "finish", "send", ...) matched to the activities the app's closed registry allows (decision, action, commitment, question, requirement, issue, risk, approval, rejection; anything else is a note), plus the meaning. The action **verb** and the sentence's **main words** are kept with each sentence. A promise about someone else ("Rahul will finish the report by Friday") is a commitment too.
- **Task**: the words of the sentence with the request wrapped around it taken off ("finish the report"), the deadline as spoken ("by Friday"), and whether it is **dated**: an activity together with a date or time is a task with a deadline. Activities without a date are still listed as tasks (otherwise real work like "bring the handouts" would vanish), and the overview says how many of the tasks have a deadline.
- **Who it is assigned to**: the speaker's label (from the voice signature) for "I will", the person spoken to for "could you", a name said in the sentence ("Rahul will ...", "Ravi, please ..."), the team for "we" and "let's", nobody for "someone should" or "she will" (never guessed to be the speaker). Speakers are stored by label only; names are looked up when the page is read, so withdrawing consent removes a name at once.

**The sentence model.** The matching of meaning has two interchangeable engines. *lexical* (always available) fingerprints words and letter groups. *semantic* uses a sentence-embedding model (`sentence-transformers/all-MiniLM-L6-v2`, 384 numbers per sentence, safetensors weights, Apache-2.0) run on this machine's CPU. It recognises paraphrases the rules do not: "I'm on it, consider it handled" is a commitment, "Any idea what the cost comes to?" a question.

- **Installed and run here** (with the owner's go-ahead): `sentence-transformers` 6.3.0 (with `transformers` 5.22.2) was installed next to the `ai` extra (`pip check` clean; torch and numpy untouched), and the weights were fetched once into `platform/data/models` by the application's own loader with `ASK_AI_TEXT_ALLOW_DOWNLOAD` switched on for that one process. Since then it runs offline (`HF_HUB_OFFLINE=1`, `ASK_AI_OFFLINE=true`).
- **How meaning is weighed** differs by engine, because their similarities mean different things. The words engine uses closeness to the nearest example sentence, with the rules doing most of the work. The sentence model uses similarity to the centre of each activity's examples, turned into shares across all ten activities including "note", so a sentence with no cue word can still be recognised. Both then weigh 30% meaning against 70% rules.
- **Measured** on 100 sentences written for the purpose (10 per activity, none among the examples), tuned on one half and checked on the other: **40 of 50 right on the held-out half with the sentence model, against 31 of 50 with the words engine** (the words engine named a wrong activity 5 times, the sentence model 6). On a second, independent set of 12 paraphrases run in `tests/real/test_real_semantic_summary.py`: 10 of 12 against 7 of 12. The sets are small and written by one author, so these are an indication, not an accuracy figure; a wrong activity is possible and the screen tells people to check the summary against the transcript. On the real-speech conversation both engines gave the same summary.
- **To use it elsewhere:** `pip install -e ".[text]"` (from `platform/api`), then either put the weights in `ASK_AI_MODEL_DIR` or set `ASK_AI_TEXT_ALLOW_DOWNLOAD=true` once on purpose (about 90 MB from Hugging Face; `ASK_AI_TEXT_MODEL` names the model). Weights are never downloaded otherwise, and `ASK_AI_OFFLINE=true` always wins. `ASK_ANALYSIS_ENGINE` is `auto` (the sentence model if it can run, otherwise the words engine, with a log line), `lexical` or `semantic` (insist, and fail clearly if it cannot run). Note `auto` only finds the weights if `ASK_AI_MODEL_DIR` points at them. Every analysis records which engine made it and the screen says so, without naming a product.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `ASK_AI_DIARIZER=pitch` | `auto` | Separate speakers by pitch and voice colour (experimental). |
| `ASK_DIARIZATION_PITCH_GATE_OCTAVES`, `ASK_DIARIZATION_PITCH_CLUSTER_DISTANCE` | 0.45, 1.2 | The separator's pitch gate and grouping distance. |
| `ASK_SIGNATURE_MATCH_THRESHOLD`, `ASK_SIGNATURE_MATCH_MARGIN` | 0.5, 0.1 | Similarity a voice must reach, and lead over the next person. **Uncalibrated.** |
| `ASK_SIGNATURE_PITCH_GATE_OCTAVES` | 0.4 | Largest difference in base pitch for two voices to be compared. |
| `ASK_ANALYSIS_ENGINE` | `auto` | `auto`, `lexical` or `semantic`. |
| `ASK_AI_TEXT_MODEL`, `ASK_AI_TEXT_ALLOW_DOWNLOAD` | MiniLM, `false` | The sentence model, and whether its weights may be downloaded. |
| `ASK_CONTEXT_SIMILARITY` | empty | A sentence stays in the context of the closest of the three before it if more similar than this (0 to 1). Empty: 0.30 for the sentence model, none for the words engine. |

Samples per person (`ASK_VOICE_ENROLL_MIN_SAMPLES`, `..._MAX_SAMPLES`), sample length (`ASK_VOICE_SAMPLE_MIN_SECONDS`, `..._MAX_SECONDS`) and the minimum speech per speaker (`ASK_SPEAKER_MIN_SPEECH_SECONDS`) are shared with voice enrollment.

## Privacy and safety

- A signature is a biometric measurement, handled like a voice print: made only for a person with consent on record (checked again just before saving), deleted when consent is withdrawn, when the voice data is removed and when the person is deleted. The uploaded audio is measured and **deleted at once**.
- The 24 numbers describing a voice are **never returned and never logged**. Only the pitch summary (base, low, high) is shown, and only to managers and administrators in the People list; the recording page shows each speaker's pitch to anyone who can see that recording, and how alike a voice was only to managers.
- Matching only ever compares with people of the same company who have consent and enough samples. Another company's people are never candidates; asking about another company's recording or person is a 404.
- The lookup endpoint is manager-only, rate limited, audited (outcome and candidate count only), and says in every answer that a score is not identity verification. Being listed among the closest people is not a match.
- A summary is not made for placeholder transcripts. A summary that fails never fails the recording.
- **The signature method is a coarse fingerprint, not a reliable biometric. Its thresholds were only tried on synthetic voices. Never present a match as identity verification**, and run the calibration on consented recordings of your own people before relying on any name (see below).

## What was and was not verified

Run on this machine (Windows 11, CPU, no GPU):

- 767 API tests passed, 182 skipped (no C++ compiler, PostgreSQL or real models in the ordinary run), none failed; `ruff` and `mypy` are clean for everything added. 386 web tests passed; the web app builds.
- **Real speech**: the two Windows text-to-speech voices (Hazel and Zira, of similar pitch) talking, run through the **real** speech recognizer (`tiny.en`), separation by pitch, signatures enrolled from other sentences and the real worker: 5 transcript lines, both voices told apart, every line named correctly, and the summary found the question, both tasks (with the due phrase "before the end of the month"), the commitment and its owner. (`tests/real/test_real_voice_chain.py`, run with `ASK_REAL_MODELS=1`.)
- The **sentence model** (meaning-based engine): loaded offline, 10 of 12 paraphrases right against 7 of 12 for the words engine, and the real-speech conversation summarised with it (`tests/real/test_real_semantic_summary.py`, and the matching test in `test_real_voice_chain.py`).
- Pitch measured within a semitone on made-up voices from 105 to 265 Hz, with level changes, a constant offset and light noise.

Not verified:

- **Anyone's real voice.** There were only two synthetic voices, so there is no error rate and no calibrated threshold. Real voices vary more, and microphones and rooms add their own colour: expect more "Unknown speaker" than in these tests, and verify before trusting.
- **Voices of similar pitch** are separated only by voice colour, which is weaker. Two people talking at once are not separated.
- A GPU, Docker, PostgreSQL with these tables (the migration was tested on SQLite only), and the locket firmware talking to this chain. The sentence model was run on CPU only, on English text, and its accuracy figures (above) come from a small set written by one author.

## Speed, and figures from the brief that were not adopted

Measured on this machine (Windows 11, 4-core i7-1165G7, CPU only, with another application using about 30% of it, so
the figures are rough). **Speech recognition is not included**: it is the biggest cost and was not timed again here.
The parts added for this feature, on 65 minutes of real speech (the Windows-voice conversation repeated):

| Step | Time for about an hour of audio |
| --- | --- |
| Read and resample the WAV | about 1 s |
| Separate speakers by pitch (only with `ASK_AI_DIARIZER=pitch`) | 63 to 87 s for audio that is nearly all speech; about 22 s for a meeting that is 37% quiet (quiet stretches are skipped, with identical results) |
| Pitch and voice signature per speaker | 2 to 3 s (at most 60 s of each speaker is measured) |
| Summary of a 900-sentence hour, words engine | 1.3 s |
| Summary of the same, sentence model | 3 to 8 s, plus loading the model once per worker (18 to 40 s) |

So the parts added here cost roughly one to two minutes per hour of audio on this CPU, on top of speech recognition. A
figure of "about 2 minutes for an hour" for the whole pipeline was suggested; it was not measured and is not claimed.
Other figures in that suggestion were not adopted: **"85 to 90% accuracy for task extraction"** has no support here. What was
measured is in the sections above (activity recognition 31 of 50 for the words engine and 40 of 50 for the sentence model
on a held-out half of 100 author-written sentences; a wrong activity is possible), and no task-extraction accuracy was
measured on real meetings. The code is also larger than "150 to 200 lines" because of the rules, the tests and the safeguards.

## Calibration

Threshold and margin are starting points. With recordings of several consented people, measure signatures of different sentences of each, compare same-person and different-person similarity, and set `ASK_SIGNATURE_MATCH_THRESHOLD` above the highest different-person score you can accept. The comparison is `exp(-(d / 0.8)^2)` of the root-mean-square difference `d` between two 24-number timbre vectors; `app/voiceid/signature.py` documents the numbers measured on the synthetic voices (same voice at most about 0.6 apart, different voices at least about 0.8).
