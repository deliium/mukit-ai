[← Browser Playback](browser-playback.md) · [Back to README](../README.md) · [MIDI live input →](midi-live-input.md)

# Performance Conductor Layer

Product generation **V5**. The playable score stays **`composition.v2`**. There is no **`composition.v5`**.

A **Performance Plan** (`performance.plan.v1`) is a durable, project-scoped conductor document. A **realization** (`performance.realization.v1`) is a session performed stream keyed by `event_id`. Consumers may ignore it. Canonical notes never change under conduction.

```text
composition.v2  (canonical notes + written expression; authoritative)
        │  read-only bind (fingerprint pin)
        ▼
performance.plan.v1  (durable conductor params / preset id)
        │  deterministic conductor (± optional AI param hint)
        ▼
performance.realization.v1  (session performed stream)
        │
        ▼
Tone schedule via schedule-apply helper (canonical export unchanged)
```

## Task 1 freezes

| Topic | Lock |
|-------|------|
| Rubato / microtiming | Per-note `tick_delta` (+ optional `duration_delta`) only — **never** mutate `Tone.Transport.bpm` |
| Fingerprint | `composition_snapshot_fingerprint` only |
| Microtiming cap | `\|tick_delta\| ≤ min(60, max(0, duration_ticks - 1))` |
| Rubato window | Local tempo factor in `[0.85, 1.15]` projected into tick deltas |
| Velocity | Performed velocity stays in `1..127` |
| Duration floor | `duration_ticks + duration_delta >= 1` |
| Ties | Shared `tick_delta` across tie-chain members (logical collapse + fan-out) |
| Audition locus | Backend `realize` only; frontend schedule-apply helper; **no** JS conductor twin |
| Pedaling | Realization `sustain_spans` only — does not write Composition `sustain_pedals` |
| Capture performance | Ship-1 ignores `tracks[].note_performances[]` (`capture_performance_ignored`) |
| SMF Conductor track | Unrelated naming in `composition_midi.py` — do not overload APIs |
| Default export | SMF / MusicXML / WAV stay mechanical |

`engine_version`: `performance.conductor.v1.0` (see `backend/app/performance_conductor_constants.py`).

## Presets (catalog)

Closed seeds: `mechanical`, `restrained`, `intimate`, `dramatic`.

- `GET /projects/{id}/performance-plans/presets` — static catalog, **no SQLite writes**
- `POST /projects/{id}/performance-plans` with `preset_id` + `composition` (or fingerprint) — clones one row
- Opening the Performance tab / listing plans never auto-INSERTS

## HTTP surface

Under `/projects/{project_id}/performance-plans`:

| Method | Behavior |
|--------|----------|
| `GET /` | list summaries (may be empty) |
| `GET /presets` | static catalog |
| `POST /` | create from preset clone or custom body |
| `GET /{id}` | get |
| `PUT /{id}` | CAS replace (`expected_document_revision`) |
| `DELETE /{id}` | delete |
| `POST /{id}/realize` | **requires** `composition`; returns realization + `stale` |
| `POST /{id}/compare` | **requires** `composition`; mechanical vs performed metrics |

No composition write routes. Soft-stale compares plan pin to `composition_snapshot_fingerprint(request.composition)`.

## Before / after audition

1. Performance tab selects a plan and toggles **Performed**.
2. SPA calls `POST .../realize` with current `editedMusicJson`.
3. `applyRealizationToScheduleComposition` builds an ephemeral schedule clone (tick/velocity/duration/gains/pedals).
4. Playback source kind `performance` uses mixer scope `preview`.
5. **Mechanical** clears that audition and restores the working composition schedule.

Pitch and harmony on the working composition are never rewritten.

## Soft-stale

When the request composition fingerprint ≠ plan `source_composition_fingerprint`, realize/compare set `stale: true` and the UI shows a banner. Audition still uses the request body and does not write `projects.composition_json`.

## AI augmentation

`PERFORMANCE_CONDUCTOR_AI_ENABLED` defaults **false**. When on and `engine=ai_augmented`, ship-1 applies a fake deterministic dimension nudge, then runs the deterministic conductor. Pitch/harmony/event arrays are refused (`ai_augment_refused`). `ai_agents/` does not import conductor modules.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `PERFORMANCE_CONDUCTOR_AI_ENABLED` | `false` | Allow `engine=ai_augmented` dimension patches before deterministic realize |

See `.env.example`.

## Logging redaction

| Level | What |
|-------|------|
| DEBUG | dimension params, engine_version, caps, soft-stale, tie fan-out counts |
| INFO | create/realize/compare with ids, counts, mean deltas, stale |
| WARN | soft-stale, AI refused, CAS conflict, embed refuse |
| ERROR | unexpected failures (sanitized) |

Never log full realization notes, composition event arrays, prompts, or API keys at INFO.

## Orthogonality

- Capture `note_performances[]` (MIDI input) is unrelated metadata — conductor ignores it in ship-1.
- SMF “Conductor” meta track (section markers) is unrelated naming — leave export markers unchanged.
- Default DAW export stays mechanical.

## See Also

- [Composition V2](composition-v2.md) — canonical playable score
- [Browser playback](browser-playback.md) — Tone schedule + `PLAYBACK_SOURCE_KIND_PERFORMANCE`
- [MIDI live input](midi-live-input.md) — capture-side `note_performances` (orthogonal)
