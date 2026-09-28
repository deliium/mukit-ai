# Implementation Plan: V5 Adaptive Score Intensity Layers

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-28

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra (post `/aif-improve` 2026-09-28)
- UI: yes
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request to include tests and UI
- Scope: vertical intensity inside one `adaptive.score.v1` state. A normalized runtime intensity in `0..1` selects stored layers. Fade and mutual exclusion are data. Switching is a pure function. The playable schema stays `composition.v2`. There is no `composition.v5` and no `adaptive.score.v2`. The resolver does not generate notes and does not call an LLM.

## Roadmap Linkage
Milestone: "V5 Adaptive Score intensity layers"
Rationale: The domain model stores layers and intensity windows, and the transition engine schedules moves between states. This plan is the vertical surface: one state changes density by enabling and disabling synchronized layers. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

Let one musical state change intensity while the underlying Composition stays the same piece.

Ship:

1. Optional layer fields for role, exclusive group, priority, and fade policy, with defaults so existing scores still parse.
2. A pure map from runtime intensity to an active layer set, including suppression and a fade completion tick.
3. Read-only HTTP for that map, and a read-only preview of an already-formed layer plan. Persisting a plan uses new layer commands on the existing CAS command route.
4. An intensity control on the Adaptive tab. When every row is `track_range`, the tab may set session mute. A track named by any active row stays unmuted. A track the response does not name stays as it was. Session mute can restore the previous muted flags. It must not rewrite note events.
5. Deterministic tests and documentation.

Acceptance: the same score and the same intensity always return the same active layer ids. For the locked fixture below, intensity `0.0` activates pad and piano; `0.5` adds bass and strings; `0.8` adds percussion; `1.0` adds full orchestration and suppresses the lower-priority brass hint in the same exclusive group. With tempo 120, `4/4`, `ticks_per_quarter` 480, and `position_tick` 2400, a `bar` fade completes at tick 3840. A `linear` fade of 400 ms completes at tick 2784. Repeating the call does not change those ids or integers. `projects.composition_json` and the score row stay unchanged on resolve and on preview.

```text
Adaptive tab intensity slider
        ↓  read-only HTTP
POST .../adaptive-scores/{score_id}/layer-intensity
POST .../adaptive-scores/{score_id}/layer-plans/preview
        ↓
services/adaptive_score_layer_service.py     (load score + span projections; no writes)
        ↓
services/adaptive_score_layers.py            (pure; no SQLite, FastAPI, or LLM)
        ↓
adaptive.layer.intensity.v1 response only

Apply a plan (writes the score, still no notes):
POST .../adaptive-scores/{score_id}/commands    op create_layer | edit_layer | delete_layer
```

**Terminology lock:** Product generation is **V5**. The stored graph stays **`adaptive.score.v1`**. The intensity response is a separate DTO, `adaptive.layer.intensity.v1`. It is not stored in `body_json`. **Current state** stays the authoring selection. **Runtime intensity** is the slider value in `0..1` and is a different field from `AdaptiveScoreStateV1.intensity` (the authored nominal intensity of that state). A **layer plan** is a session proposal. It becomes stored layers only after explicit command Apply. **Active** means the intensity window, state scope, and exclusive group selected the layer. **Audible** means active and the playhead is inside the layer's material span. Variants stay alternate material for a state. This plan does not select variants.

Predecessors: `.ai-factory/plans/v5-adaptive-score-domain-model.md`, `.ai-factory/plans/v5-adaptive-score-authoring.md`, and `.ai-factory/plans/v5-adaptive-score-transition-engine.md`. Do not reopen their decisions (reference-only material, `mix_hint` is a label, commands that already exist keep their payloads, schedule stays read-only, current state is not a playback cursor, no note events, no `composition.v5`).

## Approach Evaluation (locked)

### Part A — Where the active set is decided

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. The slider remaps layers in the browser** | Instant drag | A second algorithm drifts from the API | **Reject** |
| **B. One pure function. HTTP returns `adaptive.layer.intensity.v1`. The slider only renders that response** | Acceptance ids and ticks are stable. UI cannot invent a second map | A drag waits on one local POST | **Accepted** |

### Part B — What intensity does to sound

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Regenerate or edit `tracks[].events[]` when the slider moves** | Audible arrangement changes | Violates the acceptance condition. Writes the playable score | **Reject** |
| **B. Continuous gain curve from `mix_hint`** | Smooth loudness | Domain model locked `mix_hint` as a label, not a gain curve | **Reject** |
| **C. Active is on or off (`target_gain` 1 or 0). Fade policy reports when that edge completes. The tab may mute whole tracks only when the layer material is `track_range`** | Layers stay on the shared transport. Partial bars are not rewritten into new notes | Section and motif layers update the panel and do not mute a slice of a track | **Accepted** |

### Part C — Mutual exclusion

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Overlapping windows are an error** | Simple | The requested stack at 0.5 and 0.8 is overlapping additive material | **Reject** |
| **B. Optional `exclusive_group`. Null means additive. A shared group keeps one winner** | Pad and bass can stack. Two brass beds cannot | Needs a published sort | **Accepted** |

### Part D — AI mapping plans

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Resolve calls an LLM to invent windows** | Matches "AI-created" literally | Runtime switching would stop being deterministic | **Reject** |
| **B. Store the plan inside `body_json` before Apply** | Visible on GET | Every preview bumps `document_revision` and races authoring CAS | **Reject** |
| **C. Preview accepts an already-formed proposal list, runs the same pure map, and writes nothing. Apply is `create_layer` commands. No model import** | A caller, including a future agent over HTTP, may supply the plan. The map itself stays deterministic | This plan does not add an agent id | **Accepted** |

### Part E — Layer authoring commands

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Whole-document PUT only** | Already works | The Adaptive tab cannot add one layer without resubmitting the graph | **Reject** |
| **B. New `create_layer`, `edit_layer`, and `delete_layer` ops on the existing command route** | Same CAS, findings, and forbidden-key scan as the nine existing ops. Those nine payloads stay as they are | Three new ops | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Layers | `AdaptiveScoreLayerV1`: `id`, `name`, `state_id` (null means any state), `material`, `intensity_min`, `intensity_max`, `mix_hint` (`bed` / `foreground` / `ornament`), `default_active` | Windows stay inclusive and already reject `intensity_max < intensity_min`. `mix_hint` stays a label. `default_active` stays stored and is not an input to the resolver |
| Material | `AdaptiveMaterialRefV1` kinds `section`, `bar_range`, `track_range`, `motif`, `revision_region`, `asset` | Layers keep referencing those kinds. No event copy |
| Caps | `max_layers` 32 in `adaptive_score_settings.py` | Unchanged |
| Timeline | `compile_timeline` / `CompiledTimeline` | Fade completion for `bar` and `linear` uses bar boundaries and `tick_to_seconds`. The layer module does not reimplement meter math |
| Meter helper | `round_half_away_from_zero` in `composition_schemas.py` | Linear fade ticks use it |
| Runtime intensity | `AdaptiveTransitionRuntimeV1.intensity` on schedule requests | Same 0..1, boolean rejected. This plan adds its own request DTO. It does not change schedule selection |
| Commands | Nine ops via `apply_adaptive_score_command` and `POST .../commands` | New ops join that union. Existing ops keep their fields |
| Graph id allocation | `_fresh_id(prefix, taken)` with `secrets.token_hex(4)` | Layer ids use prefix `layer_` |
| Binding | Read-only composition or revision | The intensity service binds span projections and discards note events |
| Frontend | Adaptive tab, `adaptiveScoreApi.js`, `initialAdaptiveScoreState` cleared on project change, `updateMixerTrackControl` for session mute | Slider state lives in that initial object. Mixer mute uses the working playback scope |
| Logging | INFO ids and counts; never `body_json` or pitches | Same redaction |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Role, exclusive group, priority, fade | Stored layers cannot yet say strings versus brass, or that two brass beds are exclusive |
| Active set | Nothing maps intensity `0.0`, `0.5`, `0.8`, and `1.0` to layer ids |
| Audible span | Nothing distinguishes "in the window" from "playhead is inside this layer's bars" |
| Fade completion tick | Nothing reports cut, linear, or next-bar completion |
| Layer commands | The panel can count layers and cannot create one |
| Intensity HTTP | Nothing returns `adaptive.layer.intensity.v1` |
| Plan preview | Nothing evaluates a proposal list without writing |
| Slider | The Adaptive tab has no runtime intensity control |

### Coupling risks to avoid

1. Importing `app.ai_runtime`, `app.services.fake_llm`, LangChain, or any chat client from `adaptive_score_layers.py` or `adaptive_score_layer_service.py`.
2. Importing SQLite, `adaptive_score_store`, or FastAPI from the pure mapper.
3. Writing `body_json`, `projects.composition_json`, or snapshot blobs from intensity resolve or plan preview.
4. Copying `events`, `pitch`, or marker/section label strings into the response or into INFO logs.
5. Using wall-clock time, `random`, or a gain curve. `target_gain` is the integer 0 or 1.
6. Treating `default_active` or the state's nominal `intensity` as a silent override of the request intensity when the request includes `intensity`.
7. Selecting variants inside the layer mapper.
8. Muting a track because a section, motif, or asset layer is active. Only `track_range` track ids may reach `updateMixerTrackControl`.
9. Letting the slider call generate, edit, arrange, or development routes.
10. Letting `ai_agents/` import the layer mapper, the layer service, or the store. Agents that need a map use HTTP.
11. Editing `ROADMAP.md` in this plan.
12. Bumping `schema_version` or adding `adaptive.score.v2`.

## Scope And Decisions

### In scope
- Optional fields on `AdaptiveScoreLayerV1` with defaults.
- Pure intensity map and fade/span rules.
- `create_layer`, `edit_layer`, `delete_layer`.
- Read-only intensity and plan-preview routes.
- Adaptive tab slider, layer rows, and track mute limited to `track_range`.
- Tests and the doc updates in Task 9.

### Out of scope
- Note generation, region rewrite, arrangement preview, or variant selection.
- A continuous loudness curve. `mix_hint` stays `bed | foreground | ornament`.
- Tone.js transport changes, a new gain node, sampler overlap, and FluidSynth.
- Changing transition quantization, realization, or the pending schedule slot.
- An LLM call, a new agent id, or workspace artifacts. A proposal list may arrive from anywhere; this path only evaluates it.
- Stinger playback.
- A new Alembic revision. New fields live in `body_json`.
- Loop wrap, game-flag listeners, and sharing runtime intensity across processes.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("read" for intensity and preview, "write_score" for commands)
services/adaptive_score_layer_service.py
        ↓  load score, check document_revision, project spans, compile timeline
services/composition_timeline.compile_timeline
services/adaptive_score_layers.py          (pure)
```

`map_adaptive_layers` accepts the layer projections, the request, and a small timeline projection (`bar_boundaries`, `duration_ticks`, `ticks_per_quarter`, tempo at `position_tick`). It does not accept a `CompositionV2`. The service builds projections and does not pass tracks or events. `ai_agents/` does not import these modules.

**2. Stored layer fields**

Existing fields stay. New fields are optional on input. Omitted means the defaults below. `extra=forbid`. `schema_version` stays `adaptive.score.v1`.

| Field | Type | Default | Rule |
|-------|------|---------|------|
| `role` | `harmony \| bass \| percussion \| strings \| brass \| ambient \| other` | `other` | Label for the panel and for plans. It does not change who wins |
| `exclusive_group` | string or null | null | Null is additive. A non-null value matches `^[a-z][a-z0-9_]{0,40}$`. The examples in the prompt map onto roles, not onto new groups: pad is `ambient`, piano is `harmony`, bass is `bass`, strings is `strings`, percussion is `percussion`, full orchestration is `brass` |
| `priority` | int | `0` | Not a boolean. Used only inside one exclusive group |
| `fade` | object | cut / cut / 0 / 0 | See below |

`AdaptiveLayerFadeV1`:

| Field | Type | Default |
|-------|------|---------|
| `in_policy` | `cut \| linear \| bar` | `cut` |
| `out_policy` | `cut \| linear \| bar` | `cut` |
| `fade_in_ms` | int 0..4000 | `0` |
| `fade_out_ms` | int 0..4000 | `0` |

Integers reject booleans. A policy of `cut` may still store milliseconds; the completion tick ignores them. A policy of `bar` stores milliseconds and completes on the bar grid. Companions are not cross-forbidden: both policies and both durations are always present so old documents and partial UI forms stay valid.

`default_active` remains a stored authoring flag. The resolver does not read it.

**3. Choosing layers**

Request `AdaptiveLayerIntensityRequest` is `extra=forbid`:

```json
{
  "expected_document_revision": 3,
  "state_id": "state-exploration",
  "intensity": 0.5,
  "position_tick": 2400,
  "previous_intensity": null
}
```

- `intensity` is a float in `0..1` and not a boolean. When it is omitted, the service fills the named state's stored `intensity`. A missing state is `422 dangling_state_ref`.
- `previous_intensity`, when set, is also `0..1` and not a boolean. It only chooses fade-in versus fade-out policy. It does not change `active`.
- `position_tick` is an integer `>= 0` and not a boolean. When it is omitted, the service uses `0`. A tick greater than `duration_ticks` is `422 position_outside` before the pure map runs. A tick equal to `duration_ticks` is legal.
- Candidates: `state_id` is null or equals the requested state. Any other `state_id` is reported with `reason: out_of_state`, `active: false`, `audible: false`, `target_gain: 0`.
- A candidate is inside the window when `intensity_min <= intensity <= intensity_max`. Comparison uses the parsed floats. There is no epsilon. `0.5` and `1.0` are inside a window that starts at that value. `0.79` is outside a window that starts at `0.8`. Tests build both the request intensity and the layer windows from the same JSON literals (`0.8` included) so both sides share one float.
- Exclusive group: among candidates that are in the window and share the same non-null group, keep one winner. A global layer (`state_id` null) and a state layer in that group compete in this single bucket. Sort by `priority` descending, then `intensity_min` descending, then `id` ascending. The rest are `active: false`, `reason: suppressed`, `suppressed_by` the winner id.
- Winners are `active: true`, `reason: in_window`, `target_gain: 1`. Window misses are `reason: out_of_window`, `target_gain: 0`.
- Response layers follow stored order. The set of active ids is what tests lock. Two calls with the same inputs return the same ids, gains, reasons, and ticks.

**4. Synchronization (audible span)**

The service projects each layer's material into optional inclusive `span_start_tick` and exclusive `span_end_tick` before calling the pure function.

| Material | Span |
|----------|------|
| `bar_range` | `bar_start_tick(start_bar)` through `bar_end_tick(end_bar)` |
| `section` | that section's `start_bar` and `start_bar + bar_count - 1`, using `bar_start_tick` / `bar_end_tick`, not a disagreeing stored `start_tick` |
| `track_range` with both bars set | same bar span |
| `track_range` without bars | whole piece: `0` through `duration_ticks` |
| `revision_region` with bars or a section | that span |
| `motif`, `asset`, or a region that cannot name bars | span is unknown |

`audible` is true only when `active` is true and a known span contains `position_tick` (`span_start_tick <= position_tick < span_end_tick`). An unknown span while active yields `audible: true` and one warning `span_unchecked` (`code`, `target_id` = layer id). A known span that misses the playhead yields `audible: false` and `reason` stays the intensity reason (`in_window`) so the panel can show "active, waiting for the region". `active` does not flip off because the playhead is outside the span. Layers keep the composition's clock. They do not restart at bar 1.

A section whose stored `start_tick` disagrees with `bar_start_tick(start_bar)` is still projected from bars. The response `warnings` may include one `section_tick_disagrees` entry. Resolve still succeeds.

If binding fails with an existing error code (`material_target_missing`, `material_range_outside`, `composition_unavailable`, `mixed_revision_targets`), the route returns that code and no intensity body.

**5. Fade completion**

`seconds_per_tick = 60 / tempo_bpm / ticks_per_quarter` using tempo at `position_tick` and the timeline's `ticks_per_quarter`. Lead ticks for `linear` are `round_half_away_from_zero(fade_ms / 1000 / seconds_per_tick)`, where `fade_ms` is the edge selected below. This is the same lead formula as a transition crossfade.

When `previous_intensity` is set, compute the previous active set with the same function, including suppression. Edge selection uses that previous `active` bit and the new `active` bit:

- New `active` true and previous `active` false: `in_policy`, `fade_in_ms`.
- New `active` false and previous `active` true: `out_policy`, `fade_out_ms`. A layer that stays inside its window and becomes `suppressed` takes `out_policy`.
- Otherwise: if `active`, use `in_policy` and `fade_in_ms`; otherwise use `out_policy` and `fade_out_ms`. `previous_intensity` omitted uses this same steady rule.

`fade_end_tick`:

| Policy | Tick |
|--------|------|
| `cut` | `position_tick` |
| `linear` | `min(duration_ticks, position_tick + lead_ticks)` |
| `bar` | Smallest `bar_boundaries` entry that is `>= position_tick` and `<= duration_ticks`. If none remains, `fade_end_tick` is `duration_ticks` and warnings include `fade_clamped` |

`active` is computed from the new intensity immediately. `fade_end_tick` does not delay the boolean. The response also echoes `fade_ms` for the chosen edge.

Locked numbers, constant tempo 120, `4/4`, 480 ticks per quarter, bar = 1920 ticks:

| `position_tick` | Policy | `fade_ms` | `fade_end_tick` |
|-----------------|--------|-----------|-----------------|
| 2400 | `bar` | ignored for the tick | 3840 |
| 2400 | `linear` | 400 | 2784 |
| 2400 | `cut` | any | 2400 |
| 1920 | `bar` | ignored for the tick | 1920 |

`2784` is `2400 + round_half_away_from_zero(0.4 / (60/120/480))` = `2400 + 384`.

**6. Response**

`adaptive.layer.intensity.v1`, `extra=forbid`, not persisted:

```json
{
  "schema_version": "adaptive.layer.intensity.v1",
  "project_id": "proj-1",
  "score_id": "ascore_0123456789abcdef",
  "document_revision": 3,
  "state_id": "state-exploration",
  "intensity": 0.5,
  "position_tick": 2400,
  "layers": [],
  "warnings": []
}
```

Each layer entry: `layer_id`, `role`, `material_kind`, `track_ids` (at most 16 ids; empty when the material has none), `active`, `audible`, `target_gain` (int 0 or 1), `reason` (`in_window` | `out_of_window` | `out_of_state` | `suppressed`), `suppressed_by`, `in_policy`, `out_policy`, `fade_ms`, `fade_end_tick`. No layer names, no pitches, no event arrays. Warnings cap at 32 (`code`, `target_id`, `message`), matching `max_layers`, so one `span_unchecked` per layer can be returned.

`document_revision` is echoed and checked. Mismatch is `409 adaptive_score_conflict`.

**7. Layer plan preview**

`POST .../layer-plans/preview` uses the same response schema. Body adds `proposals` (1..32), each `extra=forbid` with the create-layer fields except `id`. The service assigns preview ids `preview-` plus a zero-based two-digit index (`preview-00`). Those ids are not written. The pure map runs on the proposals alone, as if they were the full layer list, with `state_id` from the request when a proposal omits state. Stored layers are not merged in. Forbidden-key scan runs on the raw body first. The route does not call `replace_score`.

Apply is the client's loop of `create_layer`. This plan does not delete stored layers as part of preview. The panel shows the preview rows and an Apply button that posts one `create_layer` per proposal. The first command sends the score's current `document_revision`. Each later command sends the `document_revision` from the previous success. If a command returns 422 or 409, stop the loop and show the error. Do not continue with later proposals after a failure.

**8. Commands**

| Op | Payload | Effect |
|----|---------|--------|
| `create_layer` | `name`, `material`, optional `state_id`, `intensity_min`, `intensity_max`, `mix_hint`, `default_active`, `role`, `exclusive_group`, `priority`, `fade` | Append one layer. Id is `layer_` + 8 hex from `_fresh_id`. Unknown `state_id` is `422 dangling_state_ref` and does not write. Cap overflow is `adaptive_score_too_large` |
| `edit_layer` | `layer_id` plus any of the create fields | Copy only keys in `payload.model_fields_set`, the same partial-update rule as `_edit_transition`. Omitted fields stay. An explicit null `exclusive_group` clears the group. An omitted `fade` keeps the stored fade object; a present `fade` replaces that object as a whole. Re-validate the merged layer with `AdaptiveScoreLayerV1`. `intensity_max < intensity_min` is `422 adaptive_score_invalid` and does not write. Unknown layer id is `422 adaptive_score_invalid` with `target_id` set to the layer id, matching `_transition_invalid` |
| `delete_layer` | `layer_id` | Remove that layer only. Unknown id is the same `adaptive_score_invalid` with `target_id` |

`assign_intensity` still edits a state's nominal intensity. It does not edit layer windows.

**9. Adaptive tab**

- Block title **Runtime intensity**, separate from **Current state**.
- Range input `0` to `1`, step `0.01`, defaulted from the selected state's nominal intensity when the score loads.
- Releasing the slider, or pressing **Map layers**, sends `layer-intensity` with `state_id` = `adaptiveSelectedStateId`, `expected_document_revision`, and `position_tick` `0` unless the field **Playhead tick** has a non-negative integer.
- Rows show layer id, role, window, active, audible, reason, and fade end tick. Test ids: `adaptive-intensity`, `adaptive-map-layers`, `adaptive-layer-row-{id}`, `adaptive-layer-active-{id}`.
- **Session mute** is enabled only when every row has `material_kind` `track_range`. It calls `updateMixerTrackControl(PLAYBACK_MIXER_SCOPE_WORKING, trackId, { muted })` and does not change `volumeMidi`, solo, or `editedMusicJson`. A track is unmuted when any active row names it. A track is muted when the response names it and no active row names it. Tracks absent from every row stay as they are. Before the first apply, remember the prior `muted` value per touched track. **Restore mute** writes those values back and then clears the memory.
- If any row is not `track_range`, Session mute stays disabled. The panel still shows active and audible.
- Plan proposals are a second disclosure: role, min, max, Apply plan. Applying uses the revision chain in decision 7 and does not call generate.
- Changing the slider clears any previous intensity response in the store before the POST returns, so a stale set cannot linger. Fields live on `initialAdaptiveScoreState`: `adaptiveLayerIntensity`, `adaptiveLayerIntensityError`, `adaptiveLayerPlanPreview`, `adaptiveLayerMuteSnapshot`. Project change clears all four.

**10. Locked fixture**

One state `state-exploration` with nominal intensity `0`. Layers, all `state_id` `state-exploration`, `mix_hint` `bed`, fade cut/cut except `layer-orch` which is `bar` / `cut`:

| id | role | min | max | group | priority |
|----|------|-----|-----|-------|----------|
| `layer-pad` | ambient | 0 | 1 | null | 0 |
| `layer-piano` | harmony | 0 | 1 | null | 0 |
| `layer-bass` | bass | 0.5 | 1 | null | 0 |
| `layer-strings` | strings | 0.5 | 1 | null | 0 |
| `layer-perc` | percussion | 0.8 | 1 | null | 0 |
| `layer-brass-hint` | brass | 0.9 | 1 | `orchestration` | 0 |
| `layer-orch` | brass | 1 | 1 | `orchestration` | 1 |

Active ids:

| intensity | active ids |
|-----------|------------|
| 0 | `layer-pad`, `layer-piano` |
| 0.5 | pad, piano, `layer-bass`, `layer-strings` |
| 0.8 | those four plus `layer-perc` |
| 0.9 | those five plus `layer-brass-hint` (`layer-orch` out of window) |
| 1 | pad, piano, bass, strings, perc, `layer-orch`. `layer-brass-hint` is `suppressed` by `layer-orch` |

`layer-bass` with bars 1–4 (ticks 0 inclusive to 7680 exclusive at 1920 ticks per bar) and `position_tick` 8000 is `active` at intensity 0.5 and `audible` false.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: map adaptive intensity to layers`
- **Commit 2** (after tasks 4–6): `feat: add adaptive layer commands and intensity routes`
- **Commit 3** (after tasks 7–9): `feat: preview adaptive intensity on the Adaptive tab`

## Tasks

### Phase 1: Stored fields and pure map
- [x] Task 1: Extend the layer schema
- [x] Task 2: Map intensity to layers
- [x] Task 3: Attach fade ticks and audible spans
<!-- Commit checkpoint: tasks 1–3 -->

### Phase 2: Commands and HTTP
- [x] Task 4: Add layer commands
- [x] Task 5: Validate new layer fields
- [x] Task 6: Expose read-only intensity routes
<!-- Commit checkpoint: tasks 4–6 -->

### Phase 3: Adaptive tab
- [x] Task 7: Show runtime intensity and layer rows
- [x] Task 8: Cover the slider and session mute
<!-- Commit checkpoint: tasks 7–8 -->

### Phase 4: Documentation
- [x] Task 9: Document intensity layers
<!-- Commit checkpoint: task 9 -->

### Task 1: Extend the layer schema

In `backend/app/adaptive_score_schemas.py`, add `AdaptiveLayerRole`, `AdaptiveLayerFadePolicy`, and `AdaptiveLayerFadeV1`. Add `role`, `exclusive_group`, `priority`, and `fade` to `AdaptiveScoreLayerV1` with the defaults in decision 2. Reject booleans on `priority`, `fade_in_ms`, and `fade_out_ms`. Reject a bad exclusive-group token with `adaptive_score_invalid`.

Add response models for `adaptive.layer.intensity.v1` and the intensity request, plan preview request, and per-layer row. The row includes `material_kind` and `track_ids` from decision 6. Warnings use the same shape as `AdaptiveTransitionScheduleWarningV1` with `max_length` 32. Add `span_unchecked` and `fade_clamped` to `ADAPTIVE_SCORE_ERROR_CODES` only if those codes are raised as HTTP errors. They are warning codes when the map succeeds. `position_outside` is already an error code; the request model keeps `position_tick >= 0`, and the service raises that code in Task 6.

Files: `backend/app/adaptive_score_schemas.py`, `backend/tests/test_adaptive_score_schema.py`.

LOGGING REQUIREMENTS:
- DEBUG on schema rejection via the existing `log_adaptive_schema_failure` (`model`, `field`, `code`).
- Do not log layer names, material payloads, or the document body.
- Levels stay behind `LOG_LEVEL`.

Depends on: none.

### Task 2: Map intensity to layers

Create `backend/app/services/adaptive_score_layers.py` with `map_adaptive_layers(...)`. Inputs are tuples of layer projections (id, state id, role, min, max, group, priority, fade policies, fade milliseconds, optional span, track ids, material kind) plus the request scalars. Output is the row tuple plus warnings. Implement decision 3. No import from FastAPI, sqlite3, `adaptive_score_store`, `ai_runtime`, or `fake_llm`.

Unit tests in `backend/tests/test_adaptive_score_layers.py` lock the fixture table in decision 10 for intensities `0`, `0.5`, `0.8`, `0.9`, and `1`. Build `0.8` from the JSON literal on both the request and the layer window. A second call with the same inputs returns equal rows. Boolean intensity is covered at the schema layer in Task 1. A layer with another `state_id` is `out_of_state`. Null `state_id` stays a candidate. A global layer and a state layer that share `exclusive_group` `orchestration` and both sit in the window produce one winner.

LOGGING REQUIREMENTS:
- DEBUG: state id, intensity, candidate count, active count. No layer names.
- No INFO on the pure function (the service logs the request in Task 6).

Depends on: Task 1.

### Task 3: Attach fade ticks and audible spans

In the same pure function, implement decisions 4 and 5. Pass bar boundaries and tempo in. Lead ticks use only `round_half_away_from_zero(fade_ms / 1000 / seconds_per_tick)`. Tests lock `fade_end_tick` 3840, 2784, 2400, and 1920 from the table in decision 5. The bass span test uses `position_tick` 8000, `active` true, `audible` false at intensity 0.5. `previous_intensity` 0.4 to 0.5 on bass selects `in_policy`. `previous_intensity` 0.5 to 0.4 selects `out_policy`. On the locked fixture, `previous_intensity` 0.9 and intensity `1` selects `out_policy` for `layer-brass-hint` (it becomes suppressed) and `in_policy` for `layer-orch`. `active` still follows the new intensity only. A `position_tick` greater than `duration_ticks` raises `position_outside` and returns no rows.

A section span uses the projected ticks the service will supply. The pure test passes those ticks in directly. `span_unchecked` is one warning when the span is absent and the layer is active.

LOGGING REQUIREMENTS:
- DEBUG: `position_tick`, `fade_end_tick` for the first active layer only as integers, plus warning codes.
- Do not log tempo maps as raw composition JSON.

Depends on: Task 2.

### Task 4: Add layer commands

Add `CreateLayerCommand`, `EditLayerCommand`, and `DeleteLayerCommand` to the command union in `adaptive_score_schemas.py`. Handle them in `apply_adaptive_score_command` in `backend/app/services/adaptive_score_commands.py` per decision 8. Reuse `_fresh_id("layer_", ...)`, the forbidden-key scan, and the CAS path already in `adaptive_score_service.py`. Do not change the nine existing ops.

Tests in `backend/tests/test_adaptive_score_commands.py`: create appends one layer and the new id matches `^layer_[0-9a-f]{8}$`; edit changes `intensity_min` and leaves material; an explicit null `exclusive_group` clears it, and an omitted `fade` keeps the stored fade; `intensity_max < intensity_min` returns `adaptive_score_invalid` and leaves the stored layer; delete removes only that id; unknown `state_id` returns `dangling_state_ref`; unknown layer id returns `adaptive_score_invalid` with `target_id` and does not write; a payload containing `events` is `embedded_note_material`.

LOGGING REQUIREMENTS:
- Extend the existing command INFO extra with `layer_count`. Keep `state_count` and `transition_count`.
- ERROR: rejection with `code` and `op` only, as `_HANDLERS` already logs.
- Never log material or names at INFO.

Depends on: Task 1.

### Task 5: Validate new layer fields

Extend `backend/app/services/adaptive_score_validation.py` so a layer exclusive group that fails the token pattern is still caught if it bypasses the model, and a layer `state_id` still uses `dangling_state_ref`. Do not add a finding that overlapping additive windows are illegal. Add a unit assertion in `backend/tests/test_adaptive_score_validation.py` that the locked fixture, including the orchestration pair, validates with no errors.

If `priority` is a boolean, the schema rejects it before validate. The validate test uses a parsed score and checks the graph still reports layer count.

LOGGING REQUIREMENTS:
- Keep the existing validation log line of counts (`layer_count` included).
- Do not log layer names or windows at INFO.

Depends on: Task 1.

### Task 6: Expose read-only intensity routes

Add `backend/app/services/adaptive_score_layer_service.py`. Load the clock the way `schedule_adaptive_transition` does: `get_score`, `expected_document_revision` (`409 adaptive_score_conflict` on mismatch), then `get_project` / `normalize_project_composition(..., persist_canonical=False)` or `get_revision_detail` using `_has_symbolic_ref` and `_symbolic_revision_id`. Do not import `_load_clock` and do not call `schedule_adaptive_transition`. If `position_tick` is greater than `timeline.duration_ticks`, raise `position_outside`. Bind material the same way schedule does, build span projections with `compile_timeline`, and call `map_adaptive_layers`. Do not call `replace_score` on these two routes.

Routes in `backend/app/routers/adaptive_scores.py`, both `enforce_current` read:

- `POST /{score_id}/layer-intensity`
- `POST /{score_id}/layer-plans/preview`

Response model is the intensity DTO. Errors use `map_adaptive_score_error_to_http`. Preview assigns `preview-00` ids and does not merge stored layers.

HTTP tests in `backend/tests/test_adaptive_score_api.py`: the fixture intensities; each row includes `material_kind` and `track_ids`; `fade_end_tick` 3840 for `layer-orch` at position 2400 when that layer is active at intensity 1; a `position_tick` past `duration_ticks` is 422 `position_outside` and does not write; a second POST returns the same body aside from no new revision; `projects.composition_json` and `document_revision` are unchanged; preview of two proposals returns those preview ids and does not increase the stored layer count; revision mismatch is 409.

LOGGING REQUIREMENTS:
- INFO: `project_id`, `score_id`, `state_id`, intensity, active count, warning count.
- ERROR: `code` only.
- Never log proposals, material, or `body_json`.

Depends on: Tasks 3 and 4.

### Task 7: Show runtime intensity and layer rows

In `frontend/src/api/adaptiveScoreApi.js`, add `mapAdaptiveLayerIntensity` and `previewAdaptiveLayerPlan`. Failures become `Error` with `status` and `findings` when present. Do not log response bodies.

Add the four session fields from decision 9 to `initialAdaptiveScoreState` in `frontend/src/store/musicStore.js`, plus actions that POST and store the response. Project change must keep clearing them via the existing `...initialAdaptiveScoreState` resets.

In `frontend/src/components/AdaptiveScorePanel.jsx`, add the slider, playhead tick, Map layers, rows, Session mute, and Restore mute per decision 9. Apply plan posts `create_layer` one proposal at a time and threads `document_revision` from each success into the next command. Session mute uses `updateMixerTrackControl` on `PLAYBACK_MIXER_SCOPE_WORKING`. Do not write `editedMusicJson`. Do not import the store from `frontend/src/utils/`.

A small pure helper `frontend/src/utils/adaptiveLayerIntensity.js` may format a row label from the response. It must not recompute `active`.

LOGGING REQUIREMENTS:
- The panel stays free of `console.log` of score bodies.
- Backend logs from Task 6 cover the request.

Depends on: Task 6.

### Task 8: Cover the slider and session mute

Frontend unit tests:

- `frontend/src/store/musicStore.adaptiveScore.test.js`: a successful map stores `adaptiveLayerIntensity`; a project switch clears it and clears any remembered mute snapshot. Session mute leaves a track unmuted when any active row names it, mutes a track that only inactive rows name, and leaves an unmentioned track unchanged. Two rows that share one track id follow that union. Restore mute writes the remembered `muted` flags. Composition JSON in the store stays unchanged. Apply of two proposals sends the second command with the `document_revision` returned by the first.
- Render test next to the panel's existing coverage, or a store-level assertion if the panel has no render harness: response rows for intensity `0.5` expose bass as active. Prefer asserting the store payload over a new renderer if the project has no panel unit test yet. Add `frontend/src/components/AdaptiveScorePanel.intensity.test.jsx` only if the current test setup already renders components with the store.

Playwright `frontend/e2e/adaptive-intensity.spec.js`, following `adaptive-transition.spec.js`: seed a project, PUT or command the locked layers (API seed is allowed; the spec does not need to click seven create forms), open the Adaptive tab, set the intensity field to `0.5`, click Map layers, and expect `adaptive-layer-active-layer-bass` to show active and `adaptive-layer-active-layer-perc` to show inactive. Assert the composition text in the JSON editor is unchanged if that surface is already on the page; otherwise assert the piano roll remains mounted and the panel did not show a generate error.

LOGGING REQUIREMENTS:
- No new client logs.
- Playwright must not print the score JSON.

Depends on: Task 7.

### Task 9: Document intensity layers

Update `docs/adaptive-score.md` with runtime intensity, the active/audible split, exclusive groups, fade completion, the two read-only routes, the three commands, and the statement that resolve does not write the composition. State the locked example in one short table.

Update the adaptive-score row in `.ai-factory/ARCHITECTURE.md` and the adaptive paragraph in `docs/CODEBASE_MAP.md` and `AGENTS.md` so they name `adaptive_score_layers.py`, `adaptive_score_layer_service.py`, and `adaptive.layer.intensity.v1`.

Do not edit `ROADMAP.md`.

LOGGING REQUIREMENTS:
- Docs only. No new log statements.

Depends on: Tasks 6 and 7.
