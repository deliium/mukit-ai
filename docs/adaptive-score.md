# Adaptive Score

An **adaptive score** describes non-linear music for a project: states, variants, transitions, layers, and stingers. It references canonical `composition.v2` material. It does not store note events, and it is not a playable schema.

Product generation for this surface is V5. The document schema is `adaptive.score.v1`. There is no `composition.v5`.

Playback that switches music at runtime is out of scope. The Adaptive tab is an authoring graph: it draws states and transitions and selects a current state. That selection is not a playback cursor.

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

Unreachable states are warnings (`state_unreachable`) so a draft can be saved. `POST .../validate?strict=true` promotes warnings to errors. The new error codes below are already errors, so strict does not change them.

A reachable state with no outgoing transition is a valid ending. It is not `missing_fallback_state`. Exploration → Combat → Victory can be saved one command at a time while the score policy stays `stay`.

A score is **ready** when it has states, `initial_state_id` and `default_state_id` point at those states, and validate reports no errors. Readiness is a validate result, not a second schema.

## Commands

`POST /projects/{project_id}/adaptive-scores/{score_id}/commands` applies one closed edit and persists it with the same `document_revision` counter as `PUT`. Collaboration uses `write_score`. The body is:

```json
{
  "expected_document_revision": 3,
  "op": "create_state",
  "payload": { "name": "Exploration" }
}
```

`op` is one of the nine names below. Anything else is `422 adaptive_score_invalid` and does not include a findings list. `expected_document_revision` sits beside the payload, never inside `body_json`. A mismatch is `409 adaptive_score_conflict` and does not write.

Success returns the same fields as GET plus `findings`. Those findings are warnings that were saved. Errors never persist. `document_revision` increments by 1. GET itself stays without a `findings` field.

A graph or binding error is `422`. `detail.code` is the first error finding's code. `detail.details.findings` is the combined graph and bind error list, capped at 64 objects of `code`, `severity`, `target_id`, and `message`. The bad edit is not stored.

| `op` | Effect |
|------|--------|
| `create_state` | Append a state. Omitted id is `state_` plus 8 hex characters. Omitted material is bar range 1–1. Intensity starts at 0. Entry is `material_start`, exit is `material_end`, loop is off, and `transition_ids` is empty. The first state fills a null `initial_state_id` and a null `default_state_id`. |
| `delete_state` | Remove the state, its variants, and every transition that touches it. Layer and stinger state pointers become null. Initial or default pointers that named it become null. |
| `duplicate_state` | Copy material, intensity, loop, entry, exit, and duration bounds. The copy has a new id, empty `transition_ids`, and is not initial or default. |
| `assign_material` | Replace that state's material, then run binding. |
| `create_transition` | Append a transition (`tran_` plus 8 hex when `id` is omitted) and list it on the source state. Both states must already exist. Self-transitions are allowed. |
| `edit_transition` | Replace provided fields. `id` does not change. Moving `from_state_id` relists the transition on the new source. Both endpoint states must exist. |
| `assign_loop` | Replace `state.loop`. |
| `assign_intensity` | Replace intensity with a number from 0 to 1. A boolean is rejected. |
| `assign_boundary` | Replace `entry` or `exit`. |

Whole-document `PUT` remains the path for `initial_state_id`, variants, layers, and stingers. Commands do not create those three collections.

## Finding codes

Existing codes stay. These are the authoring additions:

| Code | Rule |
|------|------|
| `impossible_transition` | An eligible transition can never fire. `min_time_in_state_bars` above `max_duration_bars`, an intensity gate outside the state and every variant window, or `next_exit` when the material cannot name an end (`asset`, `motif`, or `track_range` without an end) and the exit is `material_end`. `manual` and `flag_equals` are never statically impossible. An empty condition list is satisfiable. A cycle alone is not this error. |
| `loop_bounds` | An enabled loop still needs `end_bar >= start_bar`. It must also lie inside the state's bar span when the material has both bars, and inside a bound section's absolute bars (`start_bar` .. `start_bar + bar_count - 1`). |
| `missing_fallback_state` | A score policy or a transition fallback names `default_state` while `default_state_id` is null or not a state. When this error is present, `default_state_missing` is not also reported for that gap. |
| `transition_deadlock` | A reachable strongly connected component with at least one eligible edge, no eligible edge leaving it, every inside edge `impossible_transition`, and a fallback that cannot leave (`on_invalid_transition` is `stay`, or `default_state` whose target is missing or inside the component). A circulating Exploration → Combat → Victory with `manual` or empty conditions is not a deadlock, including Victory → Exploration. `target_id` is the lexicographically smallest state id in the component. |
| `timing_incompatible` | Entry and exit disagree in order or unit (`bar` versus `tick`), a boundary sits outside the material's bar or tick span, a bar boundary sits on tick-only material (or a tick boundary on bar-only material), or an enabled loop starts after an entry bar or after an exit bar. Ticks are not converted to bars. |

Messages are at most 200 characters and name the entity id and the numbers that failed.

## Adaptive tab

The workspace tab id is `adaptive`. It loads the default score, or offers **New adaptive score** for an empty draft named `Exploration cue`. State cards, transition edges, a material summary, and the finding list come from the saved document. The header `Current state:` is the selected card, seeded from `initial_state_id`. Selecting a card does not call the server and does not edit `composition.v2`. The initial state has a separate badge. Variant, layer, and stinger counts are shown; the tab does not create them.

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
| `POST` | `/{score_id}/commands` | One graph edit. `write_score`. See Commands |

Unknown project: `404 project_not_found`. Unknown score: `404 adaptive_score_not_found`. CAS mismatch: `409 adaptive_score_conflict`. One default score per project; setting `is_default` clears the previous default in the same transaction.

Collaboration uses `read` for GET and validate, and `write_score` for POST, PUT, DELETE, and commands. When `COLLABORATION_ENABLED` is off, those routes stay open.

Deleting a project cascades `adaptive_scores` via the foreign key on `project_id`.

## Caps

Environment overrides live in `adaptive_score_settings.py`. Defaults: 32 states, 8 variants per state, 64 transitions, 32 layers, 32 stingers, 8 conditions per transition, name length 80, body 262144 bytes. Exceeding a cap is `422 adaptive_score_too_large`.

## Logging

Levels follow `LOG_LEVEL`.

INFO may include `schema_version`, `project_id`, `score_id`, `op`, entity counts, `http_status`, `document_revision`, `binding_status`, `is_default`, `duration_ms`, and validate error/warning counts.

DEBUG may include finding codes, `target_id`, `severity`, `component_size` for a deadlock, state ids, material kinds, section ids, revision ids, and a fingerprint **prefix**.

INFO and DEBUG must not include `body_json`, command payloads, `composition_json`, event arrays, note pitches, prompts, or secret values.

## Non-goals

- No adaptive playback runtime and no Tone.js state machine.
- No embedded note events and no `composition.v5`.
- No agent authoring and no `multi-agent-apply` of adaptive scores.
- No resolution of neural stems, recovery WAVs, or `DATASET_ROOT`.
