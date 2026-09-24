# Audio↔symbolic alignment and round-trip editing

Versioned **`audio.alignment.v1`** maps composition ticks/bars ↔ bound source-audio timestamps after recovery **Bind**. Round-trip: recover → Bind → edit symbolic → **new** neural render — original WAV never rewritten.

## AC lock

Waveform / bar-seek / alignment durability require **recovery Bind**. V3 mono Apply (ephemeral audio deleted) does **not** satisfy seek AC.

## Map model (v1 parametric)

```text
source_seconds = downbeat_offset_seconds
               + tickToSeconds(compileTimeline(composition), tick − origin_tick)
```

Inverse via `sourceSecondsToTick`. Inputs come from recovery scaffolding (`beat_grid.downbeat_offset_seconds`, tempo) plus the live V2 timeline. When V2 tempo diverges from scaffolding, quality lowers and issue `alignment_tempo_diverged` is set.

**Stem bindings (v1):** `stem` role → `track_id` only — no durable stem WAV assets.

## Quality metadata

`quality` on `audio.alignment.v1` (never on V2 note events):

| Field | Meaning |
|-------|---------|
| `overall_confidence` | 0..1 aggregate |
| `tempo_confidence` / `beat_grid_confidence` | From scaffolding |
| `offset_uncertainty_ms` | Estimated |
| `method` | `scaffolding` \| `timeline_parametric` \| `defaulted` \| `user_adjusted` (reserved) |
| `issues[]` | Codes only |

## Dual clocks (HTMLAudio vs Tone)

| Mode | Owner | Notes |
|------|--------|------|
| **Source audition** | HTMLAudio + waveform in recovery/alignment UI | `timeupdate` → `sourcePlayheadTick`; **not** a `playbackSource` |
| **Composition audition** | PlaybackControls / Tone Transport / `playbackSource` | Unchanged |

Piano-roll shows a distinct **source playhead** (amber) separate from Tone `PlaybackCursor` (red). **OSMD / notation cursor follow is out of v1** (piano-roll-first).

## Bar → seek

Selecting a bar (`gotoBar` / edit cursor / AI bar selection) seeks source audio when alignment is present. Multi-bar selection sets `audioWindowHighlight` on the waveform.

## Reopen / hydrate

`GET /audio-recovery/projects/{project_id}/bound` returns latest bound `source_audio_asset_id`, `result_asset_id`, `alignment_asset_id`. On `hydrateProject`, after clearing recovery state, FE reloads blob URL + overlay + alignment.

## Stale neural renders

Jobs pin `source_fingerprint` (`composition.snapshot.v1`). UI marks **stale** when that fingerprint ≠ live/working snapshot fingerprint. Download remains; **Render again** enqueues a **new** job. Never PATCH/overwrite `source_audio` assets.

## Provenance

`audio.roundtrip.provenance.v1` links id prefixes + fingerprints:

`source_audio → recovery_result → alignment → composition_fp/revision → render_id`

Secret-safe (no PCM, prompts, event arrays).

## Logging namespaces

- Backend: `audio.alignment` / services under `app.services.audio_alignment`
- Frontend: `audioAlignment`, `audioRecovery`

Forbidden: full PCM, event arrays, full overlays at INFO.

## Acceptance checklist

- [ ] After Bind, `alignment_asset_id` returned; sibling `kind=alignment_json` asset on disk
- [ ] Project reopen hydrates source + alignment without re-Bind
- [ ] Bar 10 / `gotoBar` seeks HTMLAudio; waveform playhead tracks
- [ ] Edit composition → prior neural job shows stale; Render again creates new job; source sha unchanged
- [ ] Mono transcription path has no durable seek (documented OOS)
- [ ] Fake modes satisfy AC without Demucs/MusicGen

## See also

- [audio-recovery.md](./audio-recovery.md) — Bind / overlay / HTMLAudio ownership
- [neural-audio-rendering.md](./neural-audio-rendering.md) — egress jobs + fingerprint pin
- [browser-playback.md](./browser-playback.md) — Tone `playbackSource`
- [audio-transcription.md](./audio-transcription.md) — mono ephemeral path
- [testing.md](./testing.md) — `AUDIO_RECOVERY_FAKE_MODE` + `NEURAL_AUDIO_FAKE_MODE`
