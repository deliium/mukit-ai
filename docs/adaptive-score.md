# Adaptive Score

An **adaptive score** describes non-linear music for a project: states, variants, transitions, layers, and stingers. It references canonical `composition.v2` material. It does not store note events, and it is not a playable schema.

Product generation for this surface is V5. The document schema is `adaptive.score.v1`. There is no `composition.v5`.

Playback switching is not a Tone.js runtime. The scheduler below returns a bar-aligned boundary and a latency estimate. It does not render audio, does not write the score, and does not call an LLM. The Adaptive tab is an authoring graph: it draws states and transitions and selects a current state. That selection is not a playback cursor. A pending schedule is a different object, labeled **Scheduled transition**.

## Authority

| Document | Role |
|----------|------|
| `composition.v2` | Authoritative notes on `tracks[].events[]`, plus sections, tracks, and motifs |
| `adaptive.score.v1` | Graph of references (`section_id`, bar range, track ids, motif id, revision id, optional snapshot pin, or an asset id) |

Writes go to the `adaptive_scores` table. They do not update `projects.composition_json` or composition snapshots. Binding a stored `composition.v1` project migrates that document in memory only.

`silence` in score-level fallback means “play nothing.” It does not insert a rest into the composition.

## Two axes

**Horizontal resequencing** is the state graph plus transitions. A transition names `from_state_id`, `to_state_id`, a quantization, a priority, closed conditions, an optional realization, and a fallback. Self-transitions and cycles are valid (victory may return to exploration).

Stingers keep the five original quantization tokens: `immediate`, `beat`, `bar`, `next_exit`, `custom`. A stinger JSON body that uses `phrase`, `loop_end`, or `cue` is `adaptive_score_invalid`.

Transitions use `AdaptiveTransitionQuantization`:

| Token | Boundary |
|-------|----------|
| `immediate` | `position_tick`, including a tick inside a beat. `aligned` is true only when that tick is already a beat |
| `beat` | Next beat, or the same tick when it is already a beat. Beat length is the bar duration divided by the meter numerator |
| `bar` | Next bar line, including `duration_ticks` when that tick is a bar boundary |
| `next_exit` | The source state's exit. An off-grid tick exit is `exit_off_grid` and is not snapped |
| `custom` | Material start bar, then every `custom_grid_bars` bars. A playhead before the anchor lands on the anchor |
| `phrase` | Next section start whose bars intersect the source material. The tick is `bar_start_tick(start_bar)` |
| `loop_end` | End of the authored loop when the loop is enabled. The engine schedules that end once and does not wrap |
| `cue` | Next `rehearsal` marker whose label equals `cue_label` and whose tick is already a beat. Off-grid cues are skipped. `text` markers are not cues |

`cue_label` is required for `cue` and forbidden otherwise.

Omitted `realization` means kind `cut`.

| Kind | Companion | Boundary |
|------|-----------|----------|
| `cut` | none | Switch tick is the boundary |
| `crossfade` | `crossfade_ms` 0..4000 | Boundary stays. `fade_start_tick` is the boundary minus the lead at the boundary tempo, clamped so it is not before `position_tick` |
| `phrase` | `phrase_material` | Phrase starts at the boundary. The response includes material kind and ids only. Binding errors reject the schedule |
| `stinger` | `stinger_id` | Bed stays on the boundary. The stinger's own quantization does not move the bed. A missing id is `422 realization_invalid` |
| `overlap` | `overlap_bars` 1..16 | Legal only on a bar line. Off a bar line the schedule is `realization_invalid` and the boundary does not move |

Locked example, tempo 120, `4/4`, `ticks_per_quarter` 480, `position_tick` 2400, quantization `bar`: `boundary_tick` 3840, `latency_ticks` 1440, `latency_ms` 1500. The same inputs return the same integers.

One pending request is held in memory per score. A later successful schedule replaces it and returns `200` with `replaced_request_id`. A failed schedule leaves the current slot. `DELETE .../transition-requests/{request_id}` clears the current id (`204`, no body). A replaced or unknown id is `409 transition_request_not_pending`. `GET .../transition-requests/current` is `200` or `204` with no body when empty. Restart drops the slot. The response schema is `adaptive.transition.schedule.v1`. It is not stored in `body_json`.

The request does not send a quantization. Timing comes from the authored edge. Collaboration permission is `read`.

**Vertical layering** is simultaneous material inside one state, or in any state when `state_id` is null. `mix_hint` is a label (`bed`, `foreground`, `ornament`), not a gain curve and not `mix.plan.v1`.

**Runtime intensity** is a slider value in `0..1`. It is a different field from the state's authored `intensity`. **Current state** stays the selected card. **Active** means the intensity window, the state scope, and any exclusive group selected the layer. **Audible** means active and the playhead sits inside the layer's material span. Resolve does not write `projects.composition_json` or the score row.

Optional layer fields default so older scores still parse: `role` (`other`), `exclusive_group` (null, additive), `priority` (`0`), and `fade` (`cut` / `cut` / `0` / `0`). A non-null exclusive group matches `^[a-z][a-z0-9_]{0,40}$` and keeps one winner: higher `priority`, then higher `intensity_min`, then `id`. `default_active` stays stored and does not change the map. `target_gain` is `1` or `0`. Fade completion is a tick: `cut` stays on `position_tick`, `linear` adds the millisecond lead, and `bar` uses the next bar boundary. The response schema is `adaptive.layer.intensity.v1`. It is not stored in `body_json`. A layer plan is a session proposal list. Apply posts `create_layer` after an explicit command.

Locked example, one state `state-exploration`, tempo 120, `4/4`, `ticks_per_quarter` 480:

| id | role | window | group | priority | active at |
|----|------|--------|-------|----------|-----------|
| `layer-pad` | ambient | 0–1 | — | 0 | 0, 0.5, 0.8, 0.9, 1 |
| `layer-piano` | harmony | 0–1 | — | 0 | 0, 0.5, 0.8, 0.9, 1 |
| `layer-bass` | bass | 0.5–1 | — | 0 | 0.5, 0.8, 0.9, 1 |
| `layer-strings` | strings | 0.5–1 | — | 0 | 0.5, 0.8, 0.9, 1 |
| `layer-perc` | percussion | 0.8–1 | — | 0 | 0.8, 0.9, 1 |
| `layer-brass-hint` | brass | 0.9–1 | `orchestration` | 0 | 0.9 |
| `layer-orch` | brass | 1–1 | `orchestration` | 1 | 1 (suppresses `layer-brass-hint`) |

At `position_tick` 2400, a `bar` fade ends at tick 3840 and a `linear` fade of 400 ms ends at tick 2784. Repeating the call returns the same ids and integers.

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
| `create_layer` | Append one layer. Id is `layer_` plus 8 hex. Unknown `state_id` is `422 dangling_state_ref` and does not write. |
| `edit_layer` | Copy only fields present on the payload. An explicit null `exclusive_group` clears it. An omitted `fade` keeps the stored fade. `intensity_max < intensity_min` is `422 adaptive_score_invalid` and does not write. |
| `delete_layer` | Remove that layer only. An unknown id is `422 adaptive_score_invalid` with `target_id`. |

`assign_intensity` still edits a state's nominal intensity. It does not edit layer windows. Whole-document `PUT` remains the path for `initial_state_id`, variants, and stingers.

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

The workspace tab id is `adaptive`. It loads the default score, or offers **New adaptive score** for an empty draft named `Exploration cue`. State cards, transition edges, a material summary, and the finding list come from the saved document. The header `Current state:` is the selected card, seeded from `initial_state_id`. Selecting a card does not call the server and does not edit `composition.v2`. The initial state has a separate badge.

**Runtime intensity** is a separate control. Releasing the slider, or pressing **Map layers**, posts `layer-intensity` for the selected state. Rows show id, role, window, active, audible, reason, and fade end tick. **Session mute** is enabled only when every row is `track_range`. A named track stays unmuted when any active row names it, and is muted when only inactive rows name it. Tracks the response does not name stay as they were. **Restore mute** writes the remembered flags back. Session mute does not rewrite note events. A plan disclosure can preview proposals and Apply them as `create_layer` commands, threading `document_revision` from each success.

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
| `POST` | `/{score_id}/transition-requests` | Read-only schedule. `201` or `200` when replacing. Does not write the score |
| `GET` | `/{score_id}/transition-requests/current` | Pending schedule, or `204` with no body |
| `DELETE` | `/{score_id}/transition-requests/{request_id}` | Cancel the current id. `204` with no body |
| `POST` | `/{score_id}/layer-intensity` | Read-only `adaptive.layer.intensity.v1`. Does not write the score or the composition |
| `POST` | `/{score_id}/layer-plans/preview` | Same response for a proposal list. Assigns `preview-00` ids and does not store them |

Unknown project: `404 project_not_found`. Unknown score: `404 adaptive_score_not_found`. CAS mismatch: `409 adaptive_score_conflict`. One default score per project; setting `is_default` clears the previous default in the same transaction.

Collaboration uses `read` for GET, validate, transition schedule/current/cancel, layer intensity, and layer-plan preview, and `write_score` for POST, PUT, DELETE, and commands. When `COLLABORATION_ENABLED` is off, those routes stay open.

Deleting a project cascades `adaptive_scores` via the foreign key on `project_id`.

## Caps

Environment overrides live in `adaptive_score_settings.py`. Defaults: 32 states, 8 variants per state, 64 transitions, 32 layers, 32 stingers, 8 conditions per transition, name length 80, body 262144 bytes. Exceeding a cap is `422 adaptive_score_too_large`.

## Logging

Levels follow `LOG_LEVEL`.

INFO may include `schema_version`, `project_id`, `score_id`, `op`, entity counts, `http_status`, `document_revision`, `binding_status`, `is_default`, `duration_ms`, and validate error/warning counts.

DEBUG may include finding codes, `target_id`, `severity`, `component_size` for a deadlock, state ids, material kinds, section ids, revision ids, and a fingerprint **prefix**.

INFO on a transition route may include `method`, `project_id`, `score_id`, `http_status`, `quantization`, `boundary_tick`, `latency_ms`, `latency_ticks`, `realization_kind`, `request_id`, `duration_ms`, and `replaced`.

INFO on an intensity map includes `project_id`, `score_id`, `state_id`, intensity, active count, and warning count. Command INFO also includes `layer_count`. None of these lines include layer names, material, or `body_json`.

DEBUG in the resolver may include `transition_id`, `position_tick`, `boundary_tick`, `latency_ticks`, `latency_ms`, warning codes, `realization_kind`, and `flag_count`.

INFO and DEBUG must not include `body_json`, command payloads, `composition_json`, event arrays, note pitches, prompts, secret values, marker or section labels, `cue_label`, flag values, or the runtime dict.

## Non-goals

- The transition scheduler does not render audio, does not write the score, and does not call an LLM. It does not start Tone.js, FluidSynth, or a neural render.
- No embedded note events and no `composition.v5`.
- No agent authoring and no `multi-agent-apply` of adaptive scores.
- No resolution of neural stems, recovery WAVs, or `DATASET_ROOT`.
