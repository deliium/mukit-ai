# AI-assisted mixing and mastering

Non-destructive mix plans over a completed neural stem set. A request such as "make bass less dominant" becomes an inspectable parameter document. Preview compares before and after. Apply writes a **new** mix revision. Source stem WAVs stay byte-identical.

This is a bounded production helper (gain, pan, EQ, compression, reverb send, simple filtering, short automation). It is not a professional mastering suite and it does not replace a DAW. Master targets are presets and goals, not loudness certifications.

The browser Tone.js mixer is a separate, ephemeral control surface and is not this plan.

## Journey

1. Render a stem set (piano, bass, strings, and so on) until members are `complete`.
2. Open the project and the Neural Audio panel. Mix assist sits under Mix Analysis.
3. Type a request or pick a suggestion chip, choose Dynamic, Streaming, Cinematic, or Demo, and press Preview.
4. Read the parameter table (target, before, after, unit, reason). Edit numbers inside the allowed range. When a mix revision is already the head, `before` is that revision's applied value for the same operation.
5. Play the dry and processed preview clips.
6. Apply to store `mix.plan.v1` plus a new `mix.wav`. Reject drops the preview files only. Undo moves the working head back to the previous revision and keeps both mix files.

Supported phrases in v1:

- "make bass less dominant" / "Make the strings less dominant."
- "Give the piano more space."
- "Make the climax wider."
- "Reduce low-frequency masking."

Roles must exist on the active stem heads (`piano`, `bass`, `strings`, `drums`, `vocals`, `other`). Unknown sentences return `422 mix_plan_intent_unrecognized`.

Suggestions from mix analysis use observation **codes** and loci. The English `suggested_action` string is display-only and is never executed. Mix assist can select a saved report or the in-session report. The Section loudness chip sends only `section_loudness_flat` and no phrase.

## Contracts

| Name | Role |
|------|------|
| `mix.intent.v1` | Structured phrase result. The only object an optional language model may propose |
| `mix.plan.v1` | Ops, parameter diff, stem sha256 prefixes, master target, `guarantee: false`, `mutates_stems: false` |
| `mix.master_target.v1` | `dynamic`, `streaming`, `cinematic`, `demo` |

Streaming documents an aim of about −14 LUFS. If integrated loudness was not measured, the response includes `master_target_unverified`. That warning means the goal was not checked, not that the mix failed a standard.

## API

| Method | Path | Behavior |
|--------|------|----------|
| `POST` | `/mix-plan/preview` | Compile a plan. Optional short dry + processed WAVs |
| `DELETE` | `/mix-plan/previews/{id}` | Reject. 409 if that preview was already applied |
| `POST` | `/mix-plan/apply` | New revision when `digest` matches the preview. 409 `mix_plan_stem_changed` if a stem hash moved |
| `POST` | `/mix-plan/revisions/{id}/undo` | Head becomes the parent. 409 `mix_plan_undo_empty` when there is no parent |
| `GET` | `/mix-plan/revisions` | List metadata (`project_id`, optional `stem_set_id`) |
| `GET` | `/mix-plan/revisions/{id}` | Metadata plus plan JSON (no PCM) |
| `GET` | `/mix-plan/revisions/{id}/audio` | Applied mix WAV |
| `GET` | `/mix-plan/previews/{id}/audio?which=dry\|processed` | Preview A/B |

Files live under `MIX_PLAN_ROOT` (`{project_id}/previews/…` and `{project_id}/{revision_id}/mix.wav`). Stem bytes under `NEURAL_AUDIO_RENDER_ROOT` are read-only. Project delete removes the mix-plan directory for that project.

`dsp_backend` is `fake`, `stdlib`, or `numpy_scipy`. Fake mode writes a deterministic tiny WAV from the plan digest so CI can prove a new path and unchanged stem hashes without claiming audio quality.

## Configuration

| Var | Purpose |
|-----|---------|
| `MIX_PLAN_ROOT` | Preview and revision files (default: directory of `PROJECT_DB_PATH` / `mix_plans`) |
| `MIX_PLAN_FAKE_MODE` | Digest WAV and deterministic intent path |
| `MIX_PLAN_MAX_AUDIO_SECONDS` | Apply decode cap |
| `MIX_PLAN_PREVIEW_MAX_SECONDS` | Preview bounce cap (`preview_truncated` when a stem is longer) |
| `MIX_PLAN_MAX_TOTAL_INPUT_BYTES` | Sum of stem bytes |
| `MIX_PLAN_MAX_REVISIONS_PER_PROJECT` | Apply quota |
| `MIX_PLAN_JOB_TIMEOUT_SECONDS` | Sync wall clock |

## Logging

INFO records preview and revision ids, stem-set id, op counts, master target, dsp backend, output byte size, sha256 prefix, and duration. It does not record the phrase text, PCM samples, or the full plan. DEBUG may record field keys and intent codes.

## Honesty limits

- Never rewrites source stems or the original neural mix-job WAV
- Never writes `composition.v2`, `composition.v4`, or `DATASET_ROOT`
- Never hosts plugins or certifies BS.1770 / streaming loudness
- Reverb is a short feedback delay. Send operations record `reverb_id` `algorithmic_room`
- Soft-stale is a banner when the stem-set fingerprint diverges. It does not auto-apply

## Testing

```bash
cd backend && MIX_PLAN_FAKE_MODE=1 MIX_ANALYSIS_FAKE_MODE=1 NEURAL_AUDIO_FAKE_MODE=1 LLM_FAKE_MODE=1 \
  ../.venv/bin/python -m pytest tests/test_mix_plan.py -q
node --test frontend/src/utils/mixPlanUi.test.js
```

Tone fixtures for role hash checks live in `backend/tests/fixtures/audio/mix_plan/`.

## See also

- [mix-analysis.md](mix-analysis.md) — measurements and observations this compiler reads
- [neural-audio-rendering.md](neural-audio-rendering.md) — stem WAVs that stay read-only inputs
- [browser-playback.md](browser-playback.md) — ephemeral Tone mixer (different surface)
