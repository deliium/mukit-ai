# Audio recovery (V4 mixed audio → symbolic)

Recover a **short mixed musical demo** into an editable `composition.v2` project with estimated tempo/structure/harmony, confidence-gated notes, and durable related assets — **without claiming perfect transcription**.

Monophonic hum/melody capture remains on the V3 path: see [audio-transcription.md](./audio-transcription.md).

## Workflow

```text
Mixed audio upload (WAV preferred; bounded)
        │
Optional separation (fake:stems | sidecar:demucs | skip)
        │
Recovery job (run_inline)
  ├─ scaffolding: tempo, beat grid, structure, key, harmony (metadata)
  └─ per-stem / combined notes + confidence
        │
audio.recovery.preview.v1  (session review)
        │
User include/exclude / stem remap
        │
Apply (client V2 transaction, confidence stripped)
        │
Bind (POST /audio-recovery/jobs/{id}/bind + project_id)
  ├─ durable source WAV under AUDIO_RECOVERY_ASSET_ROOT
  └─ audio.recovery.result.v1 overlay keyed by event_id
```

**Honesty:** estimates are confidence-gated. Low-confidence material is visible and excluded by default — never silently invented to fill gaps. Harmony/sections are V2 **metadata only** (not playable note sources).

## Mono transcription vs recovery

| | Mono (`/transcription/audio`) | Recovery (`/audio-recovery/*`) |
|--|--|--|
| Input | Hum / single-line melody | Short mixed demos |
| Preview | `transcription.preview.v1` | `audio.recovery.preview.v1` |
| Audio retention | Deleted after request | Durable after **Bind** |
| Confidence after Apply | Session-only | Overlay asset `audio.recovery.result.v1` |
| UI | Audio Input panel | Audio recovery panel |

## Stems and Demucs map

Product stems (closed v1): `vocals` | `melody` | `bass` | `drums` | `harmonic` | `other`.

Demucs-style sidecar roles map to product stems:

| Demucs | Product |
|--|--|
| vocals | vocals + melody (`melody_derived_from_vocals`) |
| bass | bass |
| drums | drums |
| other | harmonic + other (`separation_partial` when ambiguous) |

Empty stem WAVs are never invented to satisfy the enum. When separation is unavailable or skipped (mono-enough / user opt-out), the pipeline uses a **combined** path with reduced confidence and clear issue codes.

## Confidence overlay vs V2

- Playable notes live only on `composition.v2` `tracks[].events[]` (`extra=forbid` — **no** `confidence` field).
- After Bind, confidence/stem/status live on `audio.recovery.result.v1` overlay entries keyed by `event_id`.
- Deleting a recovered note prunes its overlay entry; pitch/time edits mark `user_edited`. New user-drawn notes have no recovery overlay.
- Piano roll: pre-Apply multi-stem provisional ghosts (`data-testid="piano-roll-recovery-provisional-note"`); post-Bind dashed confidence borders on applied notes (`piano-roll-recovery-bound-note`). Overlay rides composition undo/redo.

## Assets, quotas, GC

- Files under `AUDIO_RECOVERY_ASSET_ROOT` (default beside `PROJECT_DB_PATH`; **never** `DATASET_ROOT`).
- SQLite metadata tables for jobs/assets; PCM is never stored in project JSON/snapshots.
- Durable writes only via Bind with `project_id`. Discard/cancel without Bind GC’s job temps.
- Project delete runs `cleanup_project_audio_recovery` (filesystem + rows), beside neural-audio cleanup.

## Jobs (`run_inline`)

`POST /audio-recovery/jobs` creates a job and runs the pipeline **inline** by default (same single-worker pattern as neural-audio). Poll `GET /audio-recovery/jobs/{id}`; `DELETE` cancels and GC’s unbound workdirs. No Celery/RQ in v1.

Fake mode: `AUDIO_RECOVERY_FAKE_MODE=1` (CI / Playwright).

## HTMLAudio vs Tone Transport

- **Source audition:** `HTMLAudioElement` / Web Audio for the original (and optional stem solo) — tick mapping from scaffolding.
- **Composition audition:** existing PlaybackControls / `playbackSource` / Tone Transport only.
- Recovery does **not** register a new `playbackSource`.

## Optional Compose profile

```bash
docker compose -f docker-compose.yml -f compose.audio-recovery.yml --profile audio-recovery up --build
```

Weights bind-mount under `models/audio-recovery/` (gitignored). FastAPI talks HTTP only — never imports torch/Demucs.

Soft-readiness / `GET /ai/models` discovery for recovery sidecars is a **follow-on** (not v1 AC).

## Model license table (operators)

| Component | Notes |
|--|--|
| Demucs (sidecar) | Upstream Demucs license; operators must accept before downloading weights |
| Basic Pitch / poly extras | Optional; same policy — not baked into the default Mukit image |
| Fake engines | Deterministic CI only; no third-party weights |

## API surface

| Method | Path | Purpose |
|--|--|--|
| POST | `/audio-recovery/jobs` | Multipart upload; often `status: complete` when inline |
| GET | `/audio-recovery/jobs/{id}` | Poll job + preview |
| DELETE | `/audio-recovery/jobs/{id}` | Cancel / GC unbound |
| POST | `/audio-recovery/jobs/{id}/bind` | Persist source + overlay (`project_id` required) |
| GET | `/audio-recovery/assets/{id}` | Download bound WAV/JSON |

Bind without `project_id` → `audio_recovery_project_required`. Failed Bind after Apply leaves V2 notes without overlay (UI warns; no auto-rollback).

## Logging

Namespaces: backend `audio.recovery` / module loggers under `app.services.audio_recovery*`; FE `audioRecovery` / `audioRecoveryUi` / `musicApi.audioRecovery`.

**INFO:** scalars only (job_id, status, byte_size, sha prefix, stem keys, note counts, duration_ms).  
**Never:** PCM/WAV payloads, full preview JSON, full paths with user filenames, API keys.

## Configuration

See `.env.example` (`AUDIO_RECOVERY_*`). Distinct from mono `AUDIO_*` and neural egress `NEURAL_AUDIO_*`.

## Acceptance checklist

- [ ] Fixture mixed WAV → fake job completes inline with preview notes + scaffolding
- [ ] Low-confidence notes excluded by default; includable explicitly
- [ ] Apply strips confidence from V2 events
- [ ] Bind with `project_id` persists source audio + overlay; without project rejected
- [ ] Discard without Bind leaves no durable audio
- [ ] HTMLAudio plays source beside score; Tone Transport ownership unchanged
- [ ] Project delete removes recovery asset files
- [ ] Playwright smoke (`e2e/audio-recovery.spec.js` with `AUDIO_RECOVERY_FAKE_MODE=1`) covers upload → review → Apply→Bind → overlay + HTMLAudio

## See also

- [audio-transcription.md](./audio-transcription.md) — V3 mono path
- [audio-symbolic-alignment.md](./audio-symbolic-alignment.md) — ticks↔source seek after Bind
- [neural-audio-rendering.md](./neural-audio-rendering.md) — egress jobs / asset pattern contrast
- [browser-playback.md](./browser-playback.md) — Tone Transport ownership
- [import.md](./import.md) — MIDI/MusicXML symbolic ingress
- [testing.md](./testing.md) — `AUDIO_RECOVERY_FAKE_MODE=1` + Playwright `e2e/audio-recovery.spec.js`