# Implementation Plan: Audio-to-Symbolic Musical Input

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **monophonic melody transcription** from microphone recording or audio-file upload into a **session review preview**, then explicit Apply into canonical `composition.v2` — with pitch/onset/duration/confidence, tempo/grid alignment, expressive-vs-quantize choice, low-confidence visibility, local engines where practical, temporary audio cleanup, and **no** coupling to LLM composition generation or polyphonic song transcription

## Roadmap Linkage
Milestone: "Audio-to-symbolic musical input (monophonic)"
Rationale: Live MIDI capture is complete, but users who hum, whistle, or play an acoustic monophonic instrument still lack a path into editable V2 notes. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint). Prior milestone "MIDI keyboard / live MIDI performance input" is already complete.

## Goal

Allow users to capture a simple hummed / whistled / monophonic-instrument melody, obtain a reviewable note list with confidence, choose expressive timing or quantization, Apply trusted notes into a destination track as canonical V2 events, correct mistakes on the piano roll, then use existing AI tools (harmonize, bass, accompaniment, develop, arrange) on that melody — without treating transcription as composition generation and without silently inventing uncertain notes.

```text
Mic (MediaRecorder) or audio file
  → bounded multipart upload (WAV preferred; common codecs where decodable)
  → local monophonic transcription engine (fake | optional DSP / basic-pitch)
  → transcription.preview.v1 (notes + confidence + timing; session-only)
  → Review UI (include/exclude low-confidence; expressive vs quantize)
  → Apply → extend timeline + commitCompositionTransaction (reuse midiTakeApply pattern)
  → piano roll / Tone.js / Harmony / Develop / Arrange / Motifs / save / export
  → audio bytes deleted (never PROJECT_DB_PATH / revisions)
```

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| V2 note events | `CompositionV2NoteEvent`: `pitch`, `start_tick`, `duration_ticks`, `velocity`, `id`, articulations/tie; **`extra=\"forbid\"`** | Sole playable write target; **no confidence field on notes** |
| MIDI file import | `POST /imports/midi`, `import_settings.py` byte/complexity limits, issue codes, session `import_report` | Mirror limits/error mapping for audio upload; **do not** conflate symbolic MIDI import with audio transcription |
| Live MIDI take → V2 | `midiTakeApply.js` (`extendCompositionToTick`, `applyMidiTakeToComposition`), store `midi-record` transaction | Apply transcribed notes through the same extend + batch commit pattern (`action: 'audio-transcribe'`) |
| Quantize | `quantizeNotes` / `quantizeSelection` | Post-preview or post-apply grid snap when user chooses “quantize” |
| Destination track | `pianoRollTrackId` / MIDI destination pattern | Explicit destination on Apply |
| Import UI / API | `ImportControls.jsx`, `importMidi` / `importMusicXml` in `musicApi.js` | Parallel `transcribeAudio` client + dedicated panel (not replace MIDI import) |
| Session reports | `importReport` ephemeral; analysis/arrangement/development previews until Apply | Transcription preview is session-only until Apply |
| Temp files | `tempfile.TemporaryDirectory` in WAV/MusicXML render paths | Ephemeral audio decode/work dirs with guaranteed cleanup |
| AI runtime stub | `AiOperation.TRANSCRIBE` + `AudioTranscriptionModel` protocol (stub only) | Optional discovery registration for local engines; **HTTP path must not call LLM generate** |
| Logging | Backend `LOG_LEVEL`; frontend `appLogger` | Namespaced `audioTranscription` / `audioCapture`; never log audio bytes or full note arrays at INFO |
| Fixture / fake patterns | `fake_llm`, `fake:symbolic-tiny`, import fixtures | `fake:audio-mono` engine + short WAV fixtures under `backend/tests/fixtures/audio/` |
| Post-melody AI | Harmony / Develop / Arrange / Motifs on `editedMusicJson` | No special bridge — valid V2 notes just work |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| No mic / MediaRecorder capture | No browser audio input UX |
| No audio upload / decode path | Import only MIDI/MusicXML |
| No pitch/onset transcription engine | Stub `AudioTranscriptionModel` raises unavailable |
| No `transcription.preview.v1` contract | Confidence cannot live on V2 notes (`extra=forbid`) |
| No review-before-commit for audio | MIDI commits on Stop; audio needs explicit review for confidence |
| No low-confidence piano-roll overlay | Selection overlays exist; no confidence styling |
| No AUDIO_* limits / retention policy | Need duration/sample-rate/format caps + delete-after-response |
| No fixture audio tests | Need synthetic hum WAVs + golden note expectations |
| Polyphonic / full-song ASR | Explicitly out of scope for v1 |

### Coupling risks to avoid

1. Persisting audio blobs, spectrograms, or transcription previews into `PROJECT_DB_PATH`, autosave, or revision snapshots.
2. Writing confidence (or any non-V2 field) onto committed `tracks[].events[]` — violates `extra=\"forbid\"`.
3. Routing transcription through LLM `generate` / hybrid symbolic composer (requirement: separate from composition generation).
4. Silently including sub-threshold notes as if they were certain (requirement: no silent fabrication).
5. Auto-installing heavy TF/torch into core `requirements.txt` (follow optional-extra pattern like Music Transformer).
6. Treating audio transcription as MIDI file import or Web MIDI live capture (three distinct ingresses).
7. Claiming polyphonic / multi-instrument song transcription in docs or UI copy.
8. Logging raw audio, full PCM, or full provisional note lists at INFO.
9. Leaving temp audio files on disk after request failure/success (retention leak).
10. Blocking app startup or Playwright when optional transcription engines are absent — fake path + clear 503 codes.
11. Inventing notes from `harmony` / analysis when transcription is sparse.
12. Growing orchestration inside `ImportControls` / `MidiInputPanel` — dedicated audio input module + thin UI.

## Approach Evaluation (locked)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Browser-only pitch (Pitchy / Meyda / WASM basic-pitch)** | True local; no upload; privacy | Weak CI fixture story; large frontend model; retention policy requirement implies server temps; decode/format variance | **Deferred** as enhancement — document; do not block v1 |
| **B. Cloud speech/music API** | Quality | Not local; privacy; separates poorly from product LLM | **Reject** |
| **C. Backend multipart → local engine → preview DTO → client Apply** | Matches import architecture; fixture WAV pytest; cleanup/retention enforceable; optional heavy deps; reuses `midiTakeApply` | Needs mic→WAV client prep; optional engine packaging | **Accepted** |
| **D. Immediate V2 install like MIDI import** | Simple | Skips confidence review; conflicts with “review before committing” | **Reject** as sole path |
| **E. Polyphonic / multi-f0 first** | Power-user | Out of scope; quality/UX explosion | **Deferred** |

**Decision:** Implement **backend-local monophonic transcription** behind `POST /transcription/audio` (or `/imports/audio` alias documented as transcription, not symbolic import). Response is **`transcription.preview.v1` only** (no composition mutation). Frontend owns mic/file capture, review (confidence gates, expressive vs quantize), and Apply via existing composition transaction + `extendCompositionToTick` / take-apply helpers. Engines are pluggable: always-on `fake:audio-mono` for CI; optional local DSP/`basic-pitch` when installed. Wire optional engine into `AudioTranscriptionModel` for `/ai/models` discovery **without** using language-model generate paths.

## Scope And Decisions

### In scope
- Microphone record (user-gesture `getUserMedia` + MediaRecorder / PCM→WAV encode) and audio-file picker.
- Safe upload limits: max bytes, max duration, max sample rate, allowed formats (content-detected), reject hostile payloads.
- Monophonic pitch + onset + duration + per-note confidence estimation.
- Conversion of detected notes into provisional tick-timed events aligned to composition (or estimated) tempo/grid.
- User choice: keep expressive timing **or** quantize to grid before/at Apply.
- Transcription review UI before any V2 commit; include/exclude low-confidence notes; visual markers for low confidence.
- Local engines where practical (`fake` always; optional `librosa_pyin` and/or `basic_pitch` extras).
- Temporary audio storage with delete-on-completion and documented retention (no durable audio archive).
- Separation from LLM composition generation; reuse Harmony/Develop/Arrange/Motifs after Apply.
- Fixture audio tests, verbose structured logging, docs, roadmap milestone.

### Out of scope
- Full polyphonic / multi-instrument / drum transcription.
- Speech-to-text lyrics or Whisper-as-melody.
- Cloud transcription vendors as default.
- Persisting audio or preview JSON in projects/revisions.
- Changing FluidSynth export or claiming transcription improves AI musical quality.
- Auto-creating destination tracks without confirmation (v1 requires existing track).
- Loop overdub / punch-in audio recording into an existing take lane.
- Browser WASM basic-pitch as the v1 engine (deferred).
- Extending V2 schema with `confidence` on note events.

### Architecture decisions (locked)

**1. Transcription is ingress → preview; V2 remains sole score**

```text
audio bytes (ephemeral)
  → engine → transcription.preview.v1 (session)
  → user Apply → composition.v2 tracks[].events[]
```

Never a parallel “audio clip” document. Never playable notes from preview alone.

**2. Confidence lives only in the preview / session overlay**

- Preview notes carry `confidence ∈ [0,1]`, `provisional_id`, pitch, start/duration ticks, velocity estimate.
- On Apply, selected notes become strict V2 events (no confidence field). Session overlay may keep provisional→committed id map briefly for UI flash, then clear.
- Default policy: notes with `confidence < AUDIO_CONFIDENCE_INCLUDE_THRESHOLD` (env, default **0.5**) are **excluded** from Apply selection unless the user explicitly checks “Include low-confidence”.
- UI must mark low-confidence notes (e.g. striped/amber class on review piano-roll / list) before commit.

**3. Engine interface (`audio.transcription.engine.v1`)**

```text
transcribe_mono(audio_pcm|path, *, settings) → TranscriptionPreviewV1
```

| Engine id | Availability | Role |
|-----------|--------------|------|
| `fake:audio-mono` | Always (core) | Deterministic fixture mapping for pytest/E2E; no scientific claim |
| `librosa_pyin` | Optional extra | Local DSP monophonic f0 + onset/segment heuristics |
| `basic_pitch` | Optional extra | Spotify Basic Pitch when wheels/deps available |

Selection: `AUDIO_TRANSCRIPTION_ENGINE` env (default `auto`: prefer `basic_pitch` → `librosa_pyin` → else `fake` only in `AUDIO_FAKE_MODE=1` / tests; production without extras → **503** `audio_engine_unavailable` with install hint — never silently invent melody via fake outside fake mode).

**4. HTTP contract**

- `POST /transcription/audio` multipart `file` + optional form fields: `destination_hint` unused server-side; `target_tempo_bpm` optional; `ticks_per_quarter` optional (default composition PPQ 480); `quantize` ignored server-side (client decides) OR accepted as preview hint only — **Apply owns final quantize**.
- Response: `{ preview: TranscriptionPreviewV1, engine: {...}, retention: { deleted: true } }` — **no** `composition` field (unlike MIDI import).
- Errors: 413/415/422/503 with bounded codes (`audio_payload_too_large`, `audio_duration_exceeded`, `audio_format_unsupported`, `audio_not_mono_enough` as warning not hard-fail when still usable, `audio_engine_unavailable`, …).

**5. Tempo / grid alignment**

- Prefer client-supplied composition tempo map + `ticks_per_quarter` when transcribing into an open project (query/form: `tempo_bpm`, `ticks_per_quarter`, optional `origin_tick`).
- If absent (empty workspace): estimate tempo from inter-onset intervals; default meter 4/4; issue `tempo_estimated` / `meter_defaulted`.
- Alignment step after f0 segmentation: map seconds → ticks; optional snap preview for UI when user selects quantize (client may call shared quantize helper on provisional notes before Apply).

**6. Apply / commit policy**

- Client builds note list from included provisional notes → `applyMidiTakeToComposition` (generalize naming to shared `applyPerformanceTakeToComposition` **or** thin wrapper `applyAudioTranscriptionToComposition` that calls the same extend/append/validate path).
- One `commitCompositionTransaction` (`action: 'audio-transcribe'`).
- If user chose quantize: either quantize provisional notes pre-Apply (single transaction) **or** Apply raw then second transaction via `quantizeNotes` on new refs — prefer **single transaction** with pre-Apply quantize when toggle on (cleaner undo); document choice.
- Undo removes whole apply.

**7. Audio cleanup / retention**

- Decode/transcribe inside `tempfile.TemporaryDirectory(prefix="mukit-audio-")` (or bounded named temp under `/tmp` with finally-delete).
- Never write audio under `DATASET_ROOT` or project DB paths.
- Retention policy: **delete immediately after response assembly** (sync request). Document: no multi-minute audio queue in v1; max duration cap (default **60s**).
- Log only sha256 prefix of payload + duration/sample_rate/engine id — never bytes.

**8. Frontend capture**

- Lazy mic permission on “Enable microphone” / Record (never on boot).
- Prefer encoding upload as **WAV PCM** client-side (decode MediaRecorder chunks via AudioContext) to avoid server ffmpeg dependency.
- File picker: `.wav`, `.flac`, `.ogg`, `.mp3` when backend decoder supports; reject others with clear UI.
- Panel: `AudioInputPanel.jsx` near transport / Import — Record, Stop, Upload, Transcribe, Review, Apply/Discard; destination track; expressive vs quantize; confidence threshold slider (optional, default from constant); do not block MIDI panel.

**9. Logging**

- Backend logger `app.services.composition_audio_transcription` (or similar): INFO request (bytes length, duration, engine, note count, low-confidence count); WARN rejects; ERROR engine failures (sanitized).
- Frontend `appLogger('audioCapture' | 'audioTranscription')`: phase transitions, upload size, preview counts, apply result.
- Never INFO-log PCM, base64 audio, or full provisional arrays.

**10. AI runtime**

- Implement a real `LocalMonoAudioTranscriptionModel` adapter that delegates to the engine registry; register when extras present.
- Keep stub for unavailable.
- Do **not** implement transcription inside LangGraph generate/edit graphs.
- `/ai/models` may list `local:audio-mono-*` with capability `audio_transcription` for discovery only; primary UX uses `/transcription/audio`.

**11. Tests**

- Backend: fixture WAVs (synthetic sine melody + quiet noise); fake engine golden notes; limit/reject tests; cleanup asserted (temp dir gone); optional engine tests skipped without extras.
- Frontend: store review include/exclude; apply + undo; quantize path; mic support probe without calling getUserMedia on init.
- Optional Playwright: upload fixture WAV → review → apply → Develop tab smoke (no real mic in CI).

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Mic / audio-file input | 3, 8–9 |
| Common formats + safe limits | 1–2 |
| Pitch/onset model/algorithm | 4–5 |
| Canonical V2 events | 6–7 |
| Pitch, onset, duration, confidence | 4–5, 8 |
| Tempo/grid alignment | 5–6 |
| Expressive vs quantize choice | 6–8 |
| Review before commit | 7–9 |
| Low-confidence visually identifiable | 8–9 |
| No silent fabrication of uncertain notes | 5–7, 8 |
| Local execution where practical | 4, 10 |
| Separate from composition generation | 2, 4, 11 |
| Post-Apply AI tools work | 7, 11–12 |
| Temp audio cleanup / retention | 2–3, 12 |
| Fixture audio tests + logging + docs | 10–12 |
| Hum → edit piano roll → AI harmonize | 11–12 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(audio): transcription settings, preview schemas, and upload route shell`
- **Commit 2** (tasks 4–6): `feat(audio): monophonic engines, tick alignment, and Apply-to-V2 path`
- **Commit 3** (tasks 7–9): `feat(audio): mic/file capture, review UI, confidence visuals, quantize choice`
- **Commit 4** (tasks 10–12): `feat(audio): fixture tests, fake engine, docs, and roadmap milestone`

## Tasks

### Phase 1: Contracts, limits, upload shell

- [x] Task 1: Audio transcription settings + format policy
  Deliverable: `audio_transcription_settings.py` (or extend a dedicated module beside `import_settings.py`) with env-backed limits: `AUDIO_MAX_UPLOAD_BYTES` (default 10 MiB), `AUDIO_MAX_DURATION_SECONDS` (60), `AUDIO_MAX_SAMPLE_RATE` (48000), `AUDIO_CONFIDENCE_INCLUDE_THRESHOLD` (0.5), `AUDIO_TRANSCRIPTION_ENGINE` (`auto`|`fake:audio-mono`|`librosa_pyin`|`basic_pitch`), `AUDIO_FAKE_MODE`. Allowed extensions/content signatures for wav/flac/ogg/mp3; document WAV-preferred client encoding. Wire `.env.example` + compose comments. Do not overload `IMPORT_*` for audio without clear `AUDIO_*` names.
  LOGGING: INFO resolved settings at load (scalar limits only); WARN invalid env clamped.
  Files: `backend/app/audio_transcription_settings.py` (new), `.env.example`, optionally `docker-compose.yml` env pass-through.

- [x] Task 2: `transcription.preview.v1` schemas + error codes
  Deliverable: Pydantic DTOs for preview notes (`provisional_id`, `pitch`, `start_tick`, `duration_ticks`, `velocity`, `confidence`), preview document (`schema_version`, `notes`, `issues[]`, `summary` with counts, `timing` with tempo/ppq/origin, `engine`), HTTP response/error models. Issue codes for defaults, estimation, monophonic ambiguity, omitted low-energy segments. Explicitly document that preview is non-playable and non-persistent.
  LOGGING: N/A at schema layer; validation failures use existing log_validation helpers without dumping note lists.
  Files: `backend/app/audio_transcription_schemas.py` (new), tests for schema reject/accept.

- [x] Task 3: Multipart route + bounded read + temp cleanup shell
  Deliverable: `POST /transcription/audio` in new `routers/transcription.py` (prefer routers/ over growing `main.py`): bounded chunked read (mirror imports), content sniff, write to TemporaryDirectory, call service stub, **always** cleanup in `finally`, return preview-only JSON. Map domain errors to HTTP like imports. Register router in app factory.
  LOGGING: INFO duration_ms, upload_bytes, engine_id, note_count, low_confidence_count, sha256_prefix; never log filename paths beyond basename; ERROR cleanup failures.
  Files: `backend/app/routers/transcription.py`, `main.py` include, service stub `composition_audio_transcription.py`, tests with tiny WAV upload.

### Phase 2: Engines, alignment, Apply path

- [x] Task 4: Pluggable monophonic engines (`fake` + optional locals)
  Deliverable: Engine protocol + registry. Implement `fake:audio-mono` that maps fixture digests / deterministic PCM heuristics to golden notes with confidence. Optional `librosa_pyin` (requirements-audio-transcription.txt: librosa, soundfile, numpy) segmenting voiced f0 into notes with confidence from voicing periodicity / amplitude. Optional `basic_pitch` adapter when importable. Unavailable extras → clear error; never silently swap to fabricating fake melody when `AUDIO_FAKE_MODE` is off. Optional thin `LocalMonoAudioTranscriptionModel` implementing `AudioTranscriptionModel.transcribe` for registry discovery.
  LOGGING: INFO engine selected; DEBUG segment counts; WARN fallback/skip reasons; ERROR import failures sanitized.
  Files: `backend/app/services/audio_transcription/` (package) or `services/composition_audio_transcription.py` + engines modules; `requirements-audio-transcription.txt`; ai_runtime adapter hook in bootstrap (optional).

- [x] Task 5: Seconds→ticks tempo/grid alignment + confidence policy
  Deliverable: Pure helpers: map note onsets/durations in seconds to ticks given tempo_bpm + ticks_per_quarter (+ optional origin_tick); estimate tempo when missing; attach issue codes; apply default exclude policy for low confidence in preview `summary.excluded_low_confidence_count` while still returning those notes marked for UI. Monophonic policy: when overlapping f0 candidates exist, keep highest-confidence stream and issue `polyphony_collapsed` / `secondary_pitch_omitted` — do not invent harmony notes.
  LOGGING: INFO alignment tempo source (`provided`|`estimated`); WARN estimation low confidence; DEBUG omitted secondary pitch count.
  Files: alignment helpers under services; unit tests with fixed second→tick vectors.

- [x] Task 6: Shared Apply-to-V2 + store transaction
  Deliverable: Frontend (and optionally backend dry-run unused) path: normalize included preview notes → performance take shape → `extendCompositionToTick` + append events + validate + `commitCompositionTransaction` (`action: 'audio-transcribe'`). Pre-Apply quantize when user selected quantize (reuse `quantizeNotes` on a temporary composition or shared snap helper). Discard clears session preview without mutation. Reject locked destination tracks with closed codes.
  LOGGING: Frontend INFO apply note counts / excluded counts / quantize flag; WARN validation; backend N/A on Apply (client-side). Prefer generalizing `midiTakeApply.js` exports for reuse without breaking MIDI tests.
  Files: `frontend/src/utils/audioTranscriptionApply.js` (new) and/or extend `midiTakeApply.js`; `musicStore.js` session slice + actions; tests.

### Phase 3: Capture UX + review visuals

- [x] Task 7: Session store slice for audio transcription
  Deliverable: Ephemeral fields: `audioSupport`, `audioPhase` (`idle`|`requesting_mic`|`recording`|`uploading`|`transcribing`|`review`|`applying`|`error`), `audioPreview`, `audioSelectedProvisionalIds`, `audioIncludeLowConfidence`, `audioQuantizeOnApply`, `audioDestinationTrackId`, `audioErrorCode`, recording level meters optional. No persistence into autosave payload. Clear preview on project switch / composition replace.
  LOGGING: INFO phase transitions; DEBUG destination id; WARN invalid transitions.
  Files: `musicStore.js`, `musicStore.audioTranscription.test.js`.

- [x] Task 8: Mic/file capture utilities + API client
  Deliverable: `audioInputSupport.js` (secure context / getUserMedia feature detect, no permission on import); `audioRecorder.js` (record → WAV blob, stop/cancel, max duration enforce client-side); `transcribeAudio(file|blob)` in `musicApi.js` posting FormData to `/transcription/audio`. Focus/permission UX codes: `unsupported`, `insecure_context`, `permission_denied`, `available`.
  LOGGING: INFO support probe + record start/stop durations; WARN permission denied; never log blob contents.
  Files: `frontend/src/utils/audioInputSupport.js`, `audioRecorder.js`, `musicApi.js`, colocated tests with mocked MediaRecorder/AudioContext.

- [x] Task 9: Review UI + low-confidence visuals + panel
  Deliverable: `AudioInputPanel.jsx` (+ optional lightweight review strip): list/piano-roll overlay of provisional notes; low-confidence styled distinctly; toggles for include-low-confidence and expressive vs quantize; Transcribe / Apply / Discard; destination track select. Wire into `ComposerWorkspace` near Import/MIDI without requiring mic at startup. Piano-roll overlay reads session provisional notes only while `audioPhase === 'review'` (do not write V2 until Apply).
  LOGGING: UI actions DEBUG/INFO via store; no console dumps of preview JSON.
  Files: `AudioInputPanel.jsx`, workspace integration, piano-roll overlay hook/CSS, styled-components consistent with MIDI/import panels.

### Phase 4: Fixtures, docs, acceptance

- [x] Task 10: Fixture audio + backend/frontend test suite
  Deliverable: Short synthetic WAV fixtures (e.g. C4–E4–G4 sine steps) under `backend/tests/fixtures/audio/` with expected fake-engine note table; pytest for limits, cleanup, fake golden path, schema; skip optional engine tests if deps missing; frontend unit tests for support probe, store review selection defaults (low-confidence excluded), apply+undo, quantize-on-apply. Ensure `AUDIO_FAKE_MODE` path used in CI without extras.
  LOGGING: Tests may assert log calls lightly; production paths must still log scalars.
  Files: fixtures + `test_audio_transcription_*.py`, frontend `*.test.js`.

- [x] Task 11: Acceptance path (manual + optional Playwright)
  Deliverable: Checklist: enable mic or upload fixture → transcribe → review low-confidence markers → Apply expressive or quantized → edit on piano roll → Harmony reharmonize **or** Develop continue → save/export. Optional `frontend/e2e/audio-transcription.spec.js` using file upload only (no mic). Do not fail CI when optional engines absent.
  LOGGING: E2E quiet; document manual log observation points.
  Files: e2e optional; checklist in docs.

- [x] Task 12: Documentation + roadmap + AGENTS touchpoints
  Deliverable: New `docs/audio-transcription.md` (formats/limits, engines, fake mode, confidence policy, expressive vs quantize, retention/cleanup, separation from MIDI import & Web MIDI & LLM generate, deferred polyphonic/browser-WASM). Link from README, `docs/import.md`, `docs/midi-live-input.md`, `docs/testing.md`, hybrid/AI runtime docs as “not generate”. Add ROADMAP unchecked milestone. Update `AGENTS.md` / DESCRIPTION one bullet. Note optional `requirements-audio-transcription.txt` install.
  LOGGING: Document log namespaces and forbidden payloads.
  Files: `docs/audio-transcription.md`, README, cross-links, `.ai-factory/ROADMAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`.

## Implementation Notes For Agents

- Prefer `routers/transcription.py` + `services/` engines over new logic in `main.py`.
- Reuse `midiTakeApply` timeline extend; do not fork a second extend implementation.
- V2 `extra=forbid` makes session preview mandatory for confidence — do not “temporarily” smuggle fields into events.
- `AiOperation.TRANSCRIBE` stub exists — extend carefully; never call language models for pitch.
- Keep monophonic copy honest in UI (“Melody transcription (mono)”).
- Related prior plan: `.ai-factory/plans/midi-keyboard-live-midi-input.md` (capture/commit UX patterns; different ingress).

## Out-of-Scope Reminders (do not implement in this plan)

- Polyphonic full-song transcription
- Cloud vendor APIs as default
- Persisted audio libraries / take lanes
- Browser WASM engine as v1 requirement
- V2 schema confidence fields
- Transcription-as-LLM-generate
