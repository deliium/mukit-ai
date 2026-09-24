# Optional neural audio rendering

Neural audio is an **egress / output layer** for finished Composition V2 scores. It never mutates `composition.v2`, autosave, or revision snapshots. Deterministic FluidSynth WAV (`POST /export/wav`), Tone.js preview, MIDI, and MusicXML stay separate paths.

## Summary

| Concern | Behavior |
|---------|----------|
| Authoritative score | `composition.v2` `tracks[].events[]` only |
| Deterministic WAV | FluidSynth — **Export WAV (deterministic)** |
| Neural render | Job under `/neural-audio/renders` → file on `NEURAL_AUDIO_RENDER_ROOT` |
| Fake CI | `NEURAL_AUDIO_FAKE_MODE=1` + `fake:neural-audio` |
| Real generative | Optional Compose profile `neural-audio` (MusicGen-shaped HTTP sidecar) |
| Note fidelity | Always labeled: generative / neural instrument — **not note-perfect** |

```text
Saved Composition V2 (+ optional source_revision_id)
  → adapter (midi_projection | melody_conditioning | text_prompt)
  → AiOperation.AUDIO_RENDER model
  → job queued → running → complete|failed
  → WAV/FLAC on disk + SQLite metadata
```

## Fidelity classes

| `fidelity_class` | UI label | Meaning |
|------------------|----------|---------|
| `deterministic` | Deterministic | FluidSynth / symbolic export only (not neural jobs) |
| `neural_instrument` | Neural instrument (approximate notes) | MIDI-conditioned neural synth (e.g. optional MIDI-DDSP) |
| `generative` | Generative AI (not note-perfect) | MusicGen / text·melody generative models |

UI banners must show the not-note-perfect disclaimer for generative and approximate disclaimer for neural instrument.

## Adapters (`neural_audio.adapter.v1`)

| Adapter | Transform | Typical engine | `preserves_notes` |
|---------|-----------|----------------|-------------------|
| `midi_projection` | V2 → MIDI bytes (`render_midi_with_report`) | MIDI-DDSP | `true` (lossy projection warnings still apply) |
| `melody_conditioning` | Monophonic melody guide + text | MusicGen melody | `false` |
| `text_prompt` | Tempo / instruments / sections + user instructions | MusicGen / Stable Audio Open | `false` |

Engines that cannot consume MIDI must use `melody_conditioning` or `text_prompt`.

## Job API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/neural-audio/renders` | Enqueue (composition and/or `project_id`+`source_revision_id`) |
| `GET` | `/neural-audio/renders/{id}` | Status + metadata (no PCM) |
| `GET` | `/neural-audio/renders/{id}/audio` | Download when `complete` |
| `GET` | `/neural-audio/renders?project_id=` | List multiple renders |
| `DELETE` | `/neural-audio/renders/{id}` | Delete metadata + file |

Statuses: `queued` → `running` → (`complete` | `failed`). Error codes include `neural_audio_engine_unavailable` (503), `neural_audio_quota_exceeded`, `source_revision_not_found`, `render_not_ready`.

Jobs store `model_id`, `model_version`, `adapter_kind`, `fidelity_class`, instructions, optional genre/mood, `source_revision_id`, `source_fingerprint`, audio relpath / sha256 prefix. Responses always include `mutates_composition: false`.

## Stem sets (multi-stem egress)

Independently manageable stems reuse the same neural job architecture. Mix jobs (`/neural-audio/renders`) remain unchanged.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/neural-audio/stem-sets` | Enqueue stem set (heuristic or explicit role→tracks) |
| `GET` | `/neural-audio/stem-sets/{id}` | Set + members (no PCM) |
| `GET` | `/neural-audio/stem-sets?project_id=` | List |
| `POST` | `/neural-audio/stem-sets/{id}/stems/{stem_id}/rerender` | Selective stem rerender (`supersedes_stem_id`) |
| `GET` | `/neural-audio/stems/{id}/audio` | Download when complete |
| `DELETE` | `/neural-audio/stem-sets/{id}` | Delete set + member files |

Stem roles: `piano` \| `bass` \| `strings` \| `drums` \| `vocals` \| `other`.

**Capabilities** (advertised on `GET /ai/models?operation=audio_render` as `stem_capabilities`):

| Capability | Meaning |
|------------|---------|
| `direct_stems` | Engine returns multi-stem map in **one** call (fake CI only in v1); siblings vary by `stem_role` — not identical WAVs stamped `direct_stems` from per-stem `render` |
| `per_track` / `grouped_tracks` | Slice composition tracks then render |
| `section_symbolic_filter` | Optional bar-range **symbolic** filter before render — not audio punch-in |
| `fluidsynth_deterministic` | Explicit `engine=fluidsynth` only — never silent generative fallback |

**Sync honesty:** each stem records `sync_class` (`deterministic_midi` \| `timeline_aligned` \| `generative_independent`). Generative stems are **not** sample-locked to siblings — do not claim sample-accurate unchanged audio.

Every stem pins `source_fingerprint`, `source_track_ids`, `model_id` / `model_version`, render parameters, and timeline anchors. Rendering never mutates `composition.v2`.

## Configuration

| Env | Default | Notes |
|-----|---------|-------|
| `NEURAL_AUDIO_RENDER_ROOT` | `<dir of PROJECT_DB_PATH>/neural_audio_renders` | Never `DATASET_ROOT` |
| `NEURAL_AUDIO_ENGINE` | `auto` | `fake:neural-audio` \| `sidecar:musicgen` \| `local:midi-ddsp` |
| `NEURAL_AUDIO_FAKE_MODE` | `0` | CI / Playwright |
| `NEURAL_AUDIO_SIDECAR_BASE_URL` | `http://neural-audio:8090` | HTTP only |
| `NEURAL_AUDIO_MAX_CONCURRENCY` | `1` | Single-worker assumption |
| `NEURAL_AUDIO_MAX_RENDERS_PER_PROJECT` | `20` | Soft quota |
| `NEURAL_AUDIO_MAX_PROMPT_CHARS` | `2000` | Bounded instructions |
| `AI_OP_AUDIO_RENDER` | unset | Pin model id (e.g. `fake:neural-audio`) |

Core `requirements.txt` is unchanged — no MusicGen/MIDI-DDSP weights in the FastAPI process by default. **Never** silently fall back to FluidSynth.

Dev Vite (`frontend/vite.config.js`) and production nginx must proxy `/ai/` and `/neural-audio/` to the backend (same pattern as `/llm/`).

## Compose profile

```bash
docker compose -f docker-compose.yml -f compose.neural-audio.yml --profile neural-audio up --build
```

Default `docker compose up` does not pull the neural image. Place operator-accepted weights under `./models/neural-audio/` (gitignored). Sidecar must expose `GET /health` and `POST /v1/audio/render` (JSON + `audio_base64`). VRAM/CPU: prefer CPU for smoke tests; GPU/ROCm is host-specific — FastAPI only probes HTTP health.

## License / redistribution

| Stack | Code | Weights | Mukit default image |
|-------|------|---------|---------------------|
| FluidSynth + GM SF2 | LGPL / typical SF2 attribution | Redistributable with attribution | **Included** (deterministic export) |
| MusicGen (AudioCraft) | MIT-style AudioCraft | Meta model license — operator accepts | **Not baked**; sidecar + host mount |
| Stable Audio Open 1.0 | tools vary | Stability AI Community License (non-commercial / revenue caps) | **Not baked**; docs-only optional pin |
| MIDI-DDSP | Apache-2.0 (code) | Check Magenta weight redistribution | **Not baked**; optional extras |
| `fake:neural-audio` | Mukit | N/A (synthetic short WAV) | Always available when fake mode on |
| Stem sets | Same engines as above | Same licenses per chosen engine / FluidSynth | Stem paths inherit the selected engine license; FluidSynth stems are explicit only |

Do **not** claim Stable Audio Open is freely redistributable in product images. Do not auto-download weights on `docker compose up`. Same install policy as [local-ai.md](local-ai.md) / [music-transformer.md](music-transformer.md). DAW handoff remains SMF/MusicXML — see [daw-interoperability.md](daw-interoperability.md). Stem downloads are still egress WAVs, not a second score.

## Logging

Logger: `app.services.neural_audio_render` (+ store / adapters / runtimes).

**INFO:** render_id, model_id, adapter_kind, fidelity_class, status, duration_ms, audio_bytes, sha256_prefix, source_revision_id.

**Never at INFO:** full prompts, composition JSON, PCM bytes, API keys.

Frontend: `appLogger('neuralAudioRender')` — phase transitions only; DEBUG may include truncated prompt length.

## UI

**Render with AI** panel beside Export. **Export WAV (deterministic)** remains FluidSynth. Job list supports multiple mix renders, status badges, download when complete. **Stems** section: role checklist, neural or explicit FluidSynth engine, render stem set, per-stem download/rerender, sync-class honesty + soft-stale banners.

## Testing

Backend fake + mocked sidecar (no heavy weights):

```bash
cd backend && NEURAL_AUDIO_FAKE_MODE=1 pytest tests/test_neural_audio_*.py -q
```

Frontend unit (disclaimers / download gate / stem sync):

```bash
node --test frontend/src/utils/neuralAudioRenderUi.test.js frontend/src/utils/neuralAudioStemUi.test.js
```

Optional Playwright smoke (running stack with `LLM_FAKE_MODE=1` **and** `NEURAL_AUDIO_FAKE_MODE=1`):

```bash
cd frontend && NEURAL_AUDIO_FAKE_MODE=1 npm run test:e2e -- e2e/neural-audio-render.spec.js
```

The smoke skips when `fake:neural-audio` is absent from `GET /ai/models?operation=audio_render`. It asserts enqueue → `complete` → WAV download and that workspace composition JSON is unchanged.

## See also

- [AI Runtime](ai-runtime.md) — `audio_render` / `audio_generation`
- [Browser playback](browser-playback.md) — Tone.js (unchanged)
- [Audio transcription](audio-transcription.md) — ingress only (distinct from this egress path)
- [Audio↔symbolic alignment](audio-symbolic-alignment.md) — soft-stale renders after symbolic edits
- [Optional local AI](local-ai.md) — sidecar pattern reference
