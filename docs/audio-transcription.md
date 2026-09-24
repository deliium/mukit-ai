# Audio-to-symbolic musical input (monophonic)

Monophonic melody transcription from a microphone recording or audio-file upload into a **session review preview**, then explicit Apply into canonical `composition.v2`.

This path is **not** MIDI file import, **not** Web MIDI live capture, and **not** LLM composition generation.

## Summary

```text
Mic (MediaRecorder) or audio file
  → POST /transcription/audio (multipart; WAV preferred)
  → local monophonic engine (fake | optional librosa_pyin | optional basic_pitch)
  → transcription.preview.v1 (notes + confidence + timing; session-only)
  → Review UI (include/exclude low-confidence; expressive vs quantize)
  → Apply → extend timeline + commitCompositionTransaction (action: audio-transcribe)
  → piano roll / Tone.js / Harmony / Develop / Arrange / Motifs / save / export
  → audio bytes deleted (never PROJECT_DB_PATH / revisions)
```

Confidence lives only on provisional preview notes. Applied V2 events never carry `confidence` (`extra="forbid"`).

## Usage

1. Open a composition with at least one destination track.
2. In **Transport & tracks**, use **Melody transcription (mono)**:
   - **Record mic** (permission requested only on Record), or **Upload audio** (`.wav` preferred; `.flac` / `.ogg` / `.mp3` when a decoder engine is installed).
3. Review the note list: low-confidence notes appear with amber stripes.
4. Optionally enable **Include low-confidence** and/or **Quantize on Apply**.
5. **Apply to track** commits selected notes in one undoable transaction; **Discard** clears the session preview without mutating V2.

Default policy: notes with confidence below `AUDIO_CONFIDENCE_INCLUDE_THRESHOLD` (default **0.5**) are excluded from Apply unless explicitly included.

## Configuration

| Env | Default | Meaning |
|-----|---------|---------|
| `AUDIO_MAX_UPLOAD_BYTES` | 10485760 (10 MiB) | Upload byte cap |
| `AUDIO_MAX_DURATION_SECONDS` | 60 | Max clip length |
| `AUDIO_MAX_SAMPLE_RATE` | 48000 | Reject higher rates |
| `AUDIO_CONFIDENCE_INCLUDE_THRESHOLD` | 0.5 | Preview summary / default exclude |
| `AUDIO_TRANSCRIPTION_ENGINE` | `auto` | `auto` \| `fake:audio-mono` \| `librosa_pyin` \| `basic_pitch` |
| `AUDIO_FAKE_MODE` | `0` | Allow `fake:audio-mono` under `auto` (CI) |

Optional engines:

```bash
pip install -r backend/requirements-audio-transcription.txt
```

`auto` prefers `basic_pitch` → `librosa_pyin` → else **503** `audio_engine_unavailable` unless `AUDIO_FAKE_MODE=1` (never silently invents a melody via fake outside fake mode).

## API

`POST /transcription/audio`

- Multipart form: `file` (required); optional `tempo_bpm`, `ticks_per_quarter`, `origin_tick`.
- Response: `{ preview, engine, retention: { deleted: true } }` — **no** `composition` field.
- Error codes: `audio_payload_too_large` (413), `audio_format_unsupported` (415), `audio_duration_exceeded` / `audio_malformed_source` / `audio_sample_rate_unsupported` / `audio_empty_upload` (422), `audio_engine_unavailable` (503).

Preview schema: `transcription.preview.v1` (non-playable, non-persistent).

## Engines

| Id | Role |
|----|------|
| `fake:audio-mono` | Deterministic CI / fixture mapping; no scientific claim |
| `librosa_pyin` | Local DSP monophonic f0 + segmentation |
| `basic_pitch` | Spotify Basic Pitch when installed |

`/ai/models` may list `local:audio-mono-*` with capability `audio_transcription` for discovery only. Primary UX uses `/transcription/audio`. Transcription is never routed through language-model generate/edit graphs.

## Retention / cleanup

- Decode/transcribe inside `tempfile` directories prefixed `mukit-audio-`.
- Audio is deleted immediately after response assembly (sync request).
- Never written under `DATASET_ROOT`, `PROJECT_DB_PATH`, autosave, or revision snapshots.

## Expressive vs quantize

- **Expressive** (default): Apply provisional tick times as transcribed.
- **Quantize on Apply**: Snap provisional starts to the piano-roll grid **before** the single commit (cleaner undo than apply-then-quantize).

## Logging

- Backend: logger under `app.services.audio_transcription` / `app.routers.transcription` — scalars only (bytes length, duration, engine id, note counts, sha256 prefix). Never PCM, base64, or full note arrays at INFO.
- Frontend: `appLogger('audioCapture' | 'audioTranscription')` — phase transitions, counts, codes only.

## Out of scope (v1)

- Polyphonic / multi-instrument / drum transcription *(see [audio-recovery.md](./audio-recovery.md) for the V4 mixed-audio recovery path)*
- Speech-to-text lyrics / Whisper-as-melody
- Cloud transcription vendors as default
- Browser WASM basic-pitch as the v1 engine (deferred)
- V2 schema confidence fields
- Persisted audio libraries / take lanes

## Manual acceptance checklist

1. Enable mic or upload `backend/tests/fixtures/audio/melody_c_e_g.wav` with `AUDIO_FAKE_MODE=1`.
2. Confirm review shows low-confidence markers (amber / striped).
3. Apply expressive **or** quantized; undo removes the whole apply.
4. Edit notes on the piano roll.
5. Run Harmony reharmonize preview **or** Develop continue on the melody.
6. Save / export MIDI or MusicXML.

## See also

- [Audio recovery (V4 mixed)](audio-recovery.md) — optional stems, scaffolding, Apply→Bind durable assets
- [MIDI live input](midi-live-input.md) — Web MIDI / QWERTY performance capture
- [MIDI / MusicXML import](import.md) — symbolic file import (not audio)
- [Hybrid generation](hybrid-generation.md) — LLM plan + symbolic notes (not transcription)
- [AI runtime](ai-runtime.md) — capability registry / stubs
- [Testing](testing.md) — pytest / frontend unit / Playwright
