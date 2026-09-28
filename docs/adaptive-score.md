# Adaptive Score

An **adaptive score** describes non-linear music for a project: states, variants, transitions, layers, and stingers. It references canonical `composition.v2` material. It does not store note events, and it is not a playable schema.

Product generation for this surface is V5. The document schema is `adaptive.score.v1`. There is no `composition.v5`.

Playback that switches music at runtime, and a visual graph editor, are out of scope.

## Authority

| Document | Role |
|----------|------|
| `composition.v2` | Authoritative notes on `tracks[].events[]`, plus sections, tracks, and motifs |
| `adaptive.score.v1` | Graph of references (`section_id`, bar range, track ids, motif id, revision id, optional snapshot pin, or an asset id) |

Writes go to the `adaptive_scores` table. They do not update `projects.composition_json` or composition snapshots. Binding a stored `composition.v1` project migrates that document in memory only.

`silence` in score-level fallback means “play nothing.” It does not insert a rest into the composition.

## Two axes

**Horizontal resequencing** is the state graph plus transitions. A transition names `from_state_id`, `to_state_id`, a closed quantization (`immediate`, `beat`, `bar`, `next_exit`, `custom`), a priority, closed conditions, and a fallback. Self-transitions and cycles are valid (victory may return to exploration).

**Vertical layering** is simultaneous material inside one state, or in any state when `state_id` is null. `mix_hint` is a label (`bed`, `foreground`, `ornament`), not a gain curve and not `mix.plan.v1`.

A **variant** is alternate material for one state, selected by an intensity window. A **stinger** is a short reference with an interrupt policy (`overlay`, `duck_bed`, `wait_for_exit`).

Bar numbers are absolute composition bars, the same coordinate as `CompositionV2Section.start_bar`.

## Material references

| `kind` | Required |
|--------|----------|
| `section` | `section_id` |
| `bar_range` | `start_bar` and `end_bar` (`end_bar >= start_bar`, both ≥ 1) |
| `track_range` | `track_ids` (1–16) and an optional bar range |
| `motif` | `motif_id` |
| `revision_region` | `revision_id` plus a section or a bar range |
| `asset` | `asset_kind` (`neural_stem`, `neural_mix`, `recovery_source`, `alignment`) and `asset_id` |

Every non-asset ref on one score shares one `revision_id`. All null means the working composition. A mix is `422 mixed_revision_targets`.

Forbidden keys anywhere in the tree: `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`. A hit is `422 embedded_note_material`.

Asset ids are shape-checked only. Validate reports `binding_status: unchecked` and does not stat WAV files or read `DATASET_ROOT`.

## Staleness

| Pin | `binding_status` |
|-----|------------------|
| No symbolic refs (empty or asset-only) | `unchecked` |
| Symbolic refs, no `snapshot_fingerprint` | `unpinned` |
| Pin equals `composition_snapshot_fingerprint` of the resolved composition | `fresh` |
| Pin differs | `stale` |

A stale pin still saves. The server does not write a fingerprint into `body_json`. Missing section, track, or motif ids are `422 material_target_missing`. Bars past `bar_count` are `422 material_range_outside`. A section whose `id` is null is not a target.

Unreachable states are warnings (`state_unreachable`) so a draft can be saved. `POST .../validate?strict=true` promotes warnings to errors.

A score is **ready** when it has states, `initial_state_id` and `default_state_id` point at those states, and validate reports no errors. Readiness is a validate result, not a second schema.

## HTTP

Prefix: `/projects/{project_id}/adaptive-scores`.

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `` | Summaries only (no `body_json`) |
| `POST` | `` | `201`. Server assigns `ascore_` plus 16 hex characters |
| `GET` | `/{score_id}` | Document, `document_revision`, `binding_status` |
| `PUT` | `/{score_id}` | Replace body. Requires `expected_document_revision` beside the score, not inside it |
| `DELETE` | `/{score_id}` | `204` |
| `POST` | `/{score_id}/validate` | Read-only findings. Query `strict` defaults to false |

Unknown project: `404 project_not_found`. Unknown score: `404 adaptive_score_not_found`. CAS mismatch: `409 adaptive_score_conflict`. One default score per project; setting `is_default` clears the previous default in the same transaction.

Collaboration uses `read` for GET and validate, and `write_score` for POST, PUT, and DELETE. When `COLLABORATION_ENABLED` is off, those routes stay open.

Deleting a project cascades `adaptive_scores` via the foreign key on `project_id`.

## Caps

Environment overrides live in `adaptive_score_settings.py`. Defaults: 32 states, 8 variants per state, 64 transitions, 32 layers, 32 stingers, 8 conditions per transition, name length 80, body 262144 bytes. Exceeding a cap is `422 adaptive_score_too_large`.

## Logging

Levels follow `LOG_LEVEL`.

INFO may include `schema_version`, `project_id`, `score_id`, entity counts, `document_revision`, `binding_status`, `is_default`, `duration_ms`, and validate error/warning counts.

DEBUG may include finding codes, state ids, material kinds, section ids, revision ids, and a fingerprint **prefix**.

INFO and DEBUG must not include `body_json`, `composition_json`, event arrays, note pitches, prompts, or secret values.

## Non-goals

- No adaptive playback runtime and no Tone.js state machine.
- No embedded note events and no `composition.v5`.
- No agent authoring and no `multi-agent-apply` of adaptive scores.
- No resolution of neural stems, recovery WAVs, or `DATASET_ROOT`.
