# Implementation Plan: V5 Adaptive Score Transition Engine

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-28

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra (post `/aif-improve` 2026-09-28)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request to include tests and documentation
- Scope: a deterministic scheduler that decides when and how an authored `adaptive.score.v1` transition may fire. The playable schema stays `composition.v2`. There is no `composition.v5` and no `adaptive.score.v2`. The scheduler does not render audio and does not call an LLM.

## Roadmap Linkage
Milestone: "V5 Adaptive Score transition engine"
Rationale: The domain model stores the graph and authoring validates it. This plan is the next V5 surface: bar-aligned transition timing, realization, and pending requests. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

Determine when and how the system can move between musical states without an obvious timing discontinuity.

Ship:

1. A pure scheduler that reads canonical tempo and meter from `composition.v2` and returns a grid-aligned boundary, an estimated latency, and a transition realization.
2. One pending request per score, with cancel and replace. A failed request does not clear the current pending request.
3. Deterministic unit tests. The timing path has no LLM import.
4. A preview on the existing Adaptive tab, plus documentation.

Acceptance: a request to move from Exploration to Combat on a transition authored as quantization `bar` always returns the same bar-line tick. For tempo 120, `4/4`, `ticks_per_quarter` 480, and `position_tick` 2400 (bar 2, beat 2: bar starts at 1920, one beat is 480), the boundary is tick 3840 (start of bar 3), `latency_ticks` is 1440, and `latency_ms` is 1500. Repeating the call does not change those integers. The score row and `projects.composition_json` stay unchanged.

```text
Adaptive tab "Schedule"
        ↓  read-only HTTP
POST/GET/DELETE .../adaptive-scores/{score_id}/transition-requests
        ↓
services/adaptive_score_transition_service.py   (load score + project timeline, no writes)
        ↓
services/adaptive_score_transitions.py          (pure; no SQLite, FastAPI, or LLM)
services/adaptive_score_transition_pending.py   (in-memory; one pending slot)
        ↓
adaptive.transition.schedule.v1 response only
```

**Terminology lock:** Product generation is **V5**. The stored graph stays **`adaptive.score.v1`**. The schedule response is a separate DTO, `adaptive.transition.schedule.v1`. It is not stored in `body_json`. **Current state** in the Adaptive tab stays the authoring selection from the authoring plan. A pending transition is a different object, labeled **Scheduled transition**. Quantization tokens stay the stored names: `beat` is next beat, `bar` is next bar, `next_exit` is the configured exit, `loop_end` is the end of the authored loop, `cue` is an explicit transition cue, `phrase` is the next section boundary, `immediate` is the only policy that may leave the beat grid.

Predecessors: `.ai-factory/plans/v5-adaptive-score-domain-model.md` and `.ai-factory/plans/v5-adaptive-score-authoring.md`. Do not reopen their decisions (reference-only material, cycles allowed, no note events, commands stay the nine graph edits, current state is not a playback cursor).

## Approach Evaluation (locked)

### Part A — Where the policy lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Client sends a policy that overrides the stored quantization** | Matches the wording "a request … at next bar" as a free parameter | A caller can cut mid-beat against a bar-authored edge | **Reject** |
| **B. Request names the edge; timing is only the stored quantization** | Immediate can fire only when that edge is authored `immediate`. The acceptance edge is stored as `bar` | The client cannot audition a different grid without editing the graph | **Accepted** |

### Part B — New timing tokens

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Replace the quantization enum** | Cleaner names | Breaks stored documents and the domain-model lock | **Reject** |
| **B. Keep `immediate \| beat \| bar \| next_exit \| custom` and invent phrase/loop/cue only in the scheduler** | No document change | Those three policies cannot be authored | **Reject** |
| **C. Add `AdaptiveTransitionQuantization` (`phrase`, `loop_end`, `cue` plus the five existing tokens). Stingers stay on `AdaptiveQuantization`. Old documents still parse. `schema_version` stays `adaptive.score.v1`** | Transitions can author the new grids. A stinger cannot store a token it cannot fire | Two literals instead of one alias | **Accepted** |

### Part C — Phrase and cue sources

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A fixed 4-bar or 8-bar phrase grid** | Always has a boundary | Second coordinate system; ignores the composition | **Reject** |
| **B. New marker kind on `composition.v2`** | Explicit cues | Changes the playable schema | **Reject** |
| **C. Phrase = next section start on the compiled bar map. Cue = next `rehearsal` marker whose label equals `cue_label` and whose tick is already on a beat** | Uses canonical sections, markers, and `CompiledTimeline`. Off-grid cues are skipped | A score with no sections cannot satisfy `phrase` | **Accepted** |

### Part D — Pending storage

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New SQLite table / Alembic revision** | Survives restart | Timing-critical state does not belong in the score document; extra migration | **Reject** |
| **B. Write the pending request into `body_json`** | Visible in GET | Bumps every preview into a document revision and can race the authoring CAS | **Reject** |
| **C. One in-memory slot per `(project_id, score_id)`, injectable in tests** | Cancel and replace are local. Failed schedule leaves the previous slot. No score write | Lost on process restart. One API process; this app already assumes one SQLite process | **Accepted** |

### Part E — Audio

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Tone.js state machine that crossfades samplers** | Audible proof | Playback runtime the authoring plan kept out; timing bugs hide inside the audio thread | **Reject** |
| **B. Scheduler returns the boundary and the realization. The Adaptive tab shows latency. No audio node starts** | Acceptance is an integer tick. Realizations stay inspectable | Nothing is heard | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Quantization | `immediate`, `beat`, `bar`, `next_exit`, `custom` on transitions and stingers. Both fields use `AdaptiveQuantization` | Stingers keep that alias. Transitions use a wider literal. `custom_grid_bars` stays required only for `custom` |
| Timeline | `compile_timeline` / `CompiledTimeline` in `services/composition_timeline.py`: bar boundaries, `active_tempo`, `active_time_signature`, `tick_to_seconds` | The scheduler does not reimplement tempo or meter math |
| Meter math | `bar_duration_ticks` and `round_half_away_from_zero` in `composition_schemas.py` | Beat length is that bar duration divided by the numerator |
| Exit / loop | `AdaptiveBoundaryV1`, `AdaptiveLoopV1`, absolute bars | `next_exit` and `loop_end` read those fields |
| Sections / markers | `CompositionV2Section` (`start_bar`, `bar_count`, `start_tick`); markers `rehearsal` or `text` | Phrase uses sections. Cue uses `rehearsal` only |
| Graph HTTP | CRUD, validate, nine commands, CAS `document_revision` | New routes sit beside these. They do not call `replace_score` |
| Binding | Read-only composition or revision | Schedule binds phrase material the same way and then discards the result unless findings are errors |
| Frontend | Adaptive tab, `adaptiveScoreApi.js`, store slice cleared on project change | Add a schedule panel on that tab. Do not add a graph library |
| Logging | INFO ids and counts; never `body_json` or pitches | Same redaction |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Grid resolver | Nothing maps a tick to the next beat, bar, phrase, exit, loop end, or cue |
| Latency | Nothing reports integer milliseconds from the tempo map |
| Realization | Nothing records cut, crossfade, transition phrase, stinger, or overlap |
| Pending slot | Nothing to cancel or replace an in-flight request |
| Immediate guard | `immediate` is a legal token today, and a future client must not be able to force it onto a bar edge |

### Coupling risks to avoid

1. Importing `app.ai_runtime`, `app.services.fake_llm`, LangChain, or any chat client from `adaptive_score_transitions.py` or `adaptive_score_transition_pending.py`.
2. Importing SQLite, `adaptive_score_store`, or FastAPI from the pure resolver.
3. Writing `body_json`, `projects.composition_json`, or snapshot blobs from a schedule call.
4. Copying `events`, `pitch`, or marker/section label strings into the response or into INFO logs.
5. Snapping an off-grid cue or an off-grid tick exit onto a nearby beat. Those requests fail. The boundary does not move.
6. Using wall-clock time, `random`, or a 4-bar phrase fallback.
7. Starting Tone.js, FluidSynth, or a neural render from this path.
8. Treating the authoring "current state" as a playback cursor, or treating a pending request as a document revision.
9. Editing `ROADMAP.md` in this plan.
10. Letting `ai_agents/` import the pending registry or the store. Agents that need a schedule use HTTP.

## Scope And Decisions

### In scope
- Optional realization and `cue_label` on `adaptive.score.v1` transitions, with defaults so existing rows still parse.
- Pure boundary function and in-memory pending slot.
- Read-only HTTP to schedule, read, and cancel.
- Static findings for `loop_end` with the loop off, and for realization/cue fields that the schema does not already reject.
- Adaptive tab preview of the pending schedule.
- Tests and the doc updates in Task 9.

### Out of scope
- Rendering, crossfade DSP, sampler overlap, and Tone.js transport changes.
- Loop wrap simulation. The engine schedules the authored loop end once. It does not repeat the loop.
- Game-flag listeners. The caller passes `runtime` on the request.
- New Alembic revision, new table, `composition.v5`, `adaptive.score.v2`.
- A tenth graph command. `PUT` and the existing `create_transition` / `edit_transition` payloads gain optional fields.
- Sharing the pending slot across processes.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("read"); map domain errors
services/adaptive_score_transition_service.py
        ↓  load score, check document_revision, project timeline inputs
services/composition_timeline.compile_timeline
services/adaptive_score_transitions.py          (pure)
services/adaptive_score_transition_pending.py   (in-memory)
```

`schedule_musical_transition` accepts the score, a `CompiledTimeline`, a tuple of section projections, a tuple of marker projections, and the request. It does not accept a `CompositionV2`. The service builds those projections and does not pass tracks or events. `ai_agents/` does not import these modules.

**2. Quantization**

Keep `AdaptiveQuantization` as `immediate | beat | bar | next_exit | custom` for stingers. Add `AdaptiveTransitionQuantization`:

`immediate | beat | bar | next_exit | custom | phrase | loop_end | cue`

Use the wider literal on `AdaptiveScoreTransitionV1.quantization` and on the create and edit transition payloads. A stinger JSON body that uses `phrase`, `loop_end`, or `cue` is `adaptive_score_invalid`.

| Token | Boundary |
|-------|----------|
| `bar` | Smallest `bar_boundaries` entry that is `>= position_tick` and `<= duration_ticks` |
| `beat` | Smallest beat in the current and following bars that is `>= position_tick`, including `duration_ticks` when that tick is a beat boundary. Beat length is `(bar_end - bar_start) / numerator` of the meter active at that bar's start tick. Numerator and the tick length must divide evenly; otherwise `422 meter_grid_indivisible`. A valid `7/8` bar at 480 ticks per quarter (beat 240) does not raise that code |
| `phrase` | Smallest section-start tick `>= position_tick` whose section's inclusive bars intersect the source state's material bars. The tick is `timeline.bar_start_tick(section.start_bar)`, not the section's stored `start_tick` when they differ |
| `next_exit` | The source state's exit. See below |
| `loop_end` | `timeline.bar_end_tick(loop.end_bar)` when the source loop is enabled. If `position_tick` is already past that tick, `loop_end_passed` |
| `cue` | Smallest `rehearsal` marker with label equal to `cue_label`, tick `>= position_tick`, and tick equal to a beat in its bar. Markers that miss the beat are skipped |
| `custom` | Candidates are the source material start bar and every `custom_grid_bars` bars after it. The smallest candidate tick that is still `>= position_tick` wins, including the anchor when the playhead is before the material. Same `custom_grid_bars` rule as today |
| `immediate` | `position_tick` itself, including a tick inside a beat. Legal only because this edge's stored quantization is `immediate` |

A tick that already sits on the chosen grid is the boundary (`latency_ticks == 0`). The resolver never returns a tick before `position_tick`. It never rounds a non-grid tick onto a grid except by moving forward to the next real grid point for `beat`, `bar`, `phrase`, `custom`, `loop_end`, and on-grid cues.

`next_exit` by exit kind:

- `bar`: `bar_start_tick(exit.bar)`. If `position_tick` is past it, `exit_passed`.
- `tick`: that tick when it is a beat boundary. Otherwise `exit_off_grid`. Do not snap.
- `material_end`: the exclusive end tick of the resolved material span (section end bar, or `end_bar`). That tick is a compiled bar boundary. If the material cannot name an end (`asset`, `motif`, or `track_range` without bars), `exit_unavailable`.
- `material_start`: the inclusive start tick of that span, with `exit_passed` when the position is already past it.

`phrase` material span: `bar_range` and `revision_region` use their bars; `section` uses that section's `start_bar` and `bar_count`; `track_range` uses bars when both are set. `motif`, `asset`, and a `track_range` without bars return `phrase_unavailable`. A section whose stored `start_tick` disagrees with `bar_start_tick(start_bar)` is skipped. The response `warnings` may include one `section_tick_disagrees` entry (`code`, `target_id` = section id). The schedule still succeeds if another section supplies a boundary.

`cue_label` is required when quantization is `cue`, and forbidden otherwise (schema error `adaptive_score_invalid`). Labels are matched exactly after strip, max 80 characters. `text` markers are not cues.

If the next grid point would pass `duration_ticks`, and `duration_ticks` itself is the next bar boundary, `bar` may land on `duration_ticks`. If no legal point remains, `boundary_unavailable`.

**3. Latency**

- `latency_ticks = boundary_tick - position_tick` (integer, `>= 0`).
- `latency_ms = round_half_away_from_zero((timeline.tick_to_seconds(boundary_tick) - timeline.tick_to_seconds(position_tick)) * 1000)`.
- `tempo_bpm` and `time_signature` are the timeline values at `boundary_tick`.
- `aligned` is true when the boundary is on that quantization's grid. For `immediate`, `aligned` is true only when `position_tick` is already a beat boundary; the request still succeeds.
- No `datetime`. Two calls with the same inputs return the same integers.

Locked acceptance numbers (constant tempo 120, `4/4`, 480 ticks per quarter, bar = 1920 ticks):

| `position_tick` | Quantization | `boundary_tick` | `latency_ticks` | `latency_ms` |
|-----------------|--------------|-----------------|-----------------|--------------|
| 2400 | `bar` | 3840 | 1440 | 1500 |
| 1920 | `bar` | 1920 | 0 | 0 |
| 100 | `beat` | 480 | 380 | 396 |

`396` is `round_half_away_from_zero(380 * 1000 * 60 / 120 / 480)`. A second test with meter `7/8` (bar = 1680 ticks, beat = 240) from tick 100: `beat` → 240, `bar` → 1680. A tempo change exactly at the boundary does not move `boundary_tick`. Latency uses `tick_to_seconds`, so the tempo before the boundary is what counts for the wait.

**4. Realization**

New optional object `realization` on `AdaptiveScoreTransitionV1`. Omitted means kind `cut`. `extra=forbid`.

| `kind` | Required companion | Effect on the boundary |
|--------|--------------------|------------------------|
| `cut` | none | Switch tick is the boundary |
| `crossfade` | `crossfade_ms` integer 0..4000, not a boolean | Switch tick stays the boundary. `fade_start_tick` is the boundary minus the lead, computed from the tempo at the boundary only (`seconds_per_tick = 60 / tempo_bpm / ticks_per_quarter`). Lead ticks use `round_half_away_from_zero`. If that start is before `position_tick`, `fade_start_tick` becomes `position_tick` and the boundary does not move earlier |
| `phrase` | `phrase_material` (`AdaptiveMaterialRefV1`) | Phrase starts at the boundary. The response includes material kind and ids only |
| `stinger` | `stinger_id` of a stinger on this score | Bed switch stays the boundary. The response includes the stinger id and its `interrupt_policy`. The stinger's own quantization does not move the bed |
| `overlap` | `overlap_bars` integer 1..16 | If `boundary_tick` is not a member of `bar_boundaries`, raise `realization_invalid` and do not fill the pending slot. Do not move the boundary to the next bar. When the boundary is a bar line, the destination entry tick is that boundary. Source release tick is `bar_end_tick` of the bar `overlap_bars` later, clamped to `duration_ticks` and to the source exit when that exit is sooner |

Companions that belong to another kind are schema errors. A missing stinger id is `422 realization_invalid` at schedule time and does not fill the pending slot. Phrase material runs through the existing binder; an error finding rejects the schedule the same way. The binder result is not written back.

**5. Choosing the edge**

The request body is `extra=forbid`:

```json
{
  "expected_document_revision": 3,
  "from_state_id": "state-exploration",
  "to_state_id": "state-combat",
  "transition_id": null,
  "position_tick": 2400,
  "runtime": { "intensity": 0, "flags": {}, "bars_in_state": 0 }
}
```

- `position_tick` is an integer `>= 0`. Booleans are rejected.
- `runtime.intensity` is 0..1 and not a boolean. `flags` keys match the existing flag pattern. `bars_in_state` is an integer `>= 0`.
- Eligible edges: id listed on the source state, `from_state_id` matches, `to_state_id` matches.
- Conditions are a conjunction, same as validation. Empty is satisfied. `manual` is satisfied. `flag_equals` reads `runtime.flags` (missing flag is false). `intensity_at_least` / `intensity_at_most` read `runtime.intensity`. `min_time_in_state_bars` reads `runtime.bars_in_state`.
- If `transition_id` is set, only that edge is considered. Unknown id is `422 dangling_state_ref` with `target_id` set (do not invent a new code for a missing transition id; the message says transition). Unsatisfied conditions on that edge are `422 transition_unsatisfied`.
- If `transition_id` is omitted, keep eligible edges whose conditions pass. Sort by `priority` descending, then id ascending. The first edge wins. None left is `422 transition_unsatisfied`.
- There is no policy field. A bar-authored edge cannot be scheduled as `immediate`.

**6. Pending slot**

Module-level dict guarded by a lock. Tests construct `TransitionPendingRegistry()` and the service tests pass that instance. The default registry used by HTTP is module-global. `reset_default_registry()` clears it. The adaptive-score API fixture calls that reset before each test.

- First successful schedule stores status `pending`, id `treq_` plus 8 hex characters, and returns 201.
- A later successful schedule for the same pair replaces that slot and returns 200 with `replaced_request_id` set to the previous id. The previous request is no longer cancelable.
- `DELETE .../transition-requests/{request_id}` clears the slot only when the id is the current one. Response 204. A replaced or unknown id is `409 transition_request_not_pending` and the current slot stays.
- Any error before accept (revision mismatch, unsatisfied, grid failure, binding error) does not replace or clear the slot.
- `GET .../transition-requests/current` returns the slot, or `Response(status_code=204)` with no response model when empty, matching the existing adaptive-score delete handler. `DELETE` success uses the same 204 response.
- Restart drops the slot. Document that.

`expected_document_revision` mismatch is `409 adaptive_score_conflict` and does not touch the slot. The service does not increment `document_revision`.

**7. Response**

`AdaptiveTransitionScheduleV1`, `extra=forbid`, `schema_version` literal `adaptive.transition.schedule.v1`. Fields: `request_id`, `status` (`pending`), `project_id`, `score_id`, `document_revision`, `transition_id`, `from_state_id`, `to_state_id`, `quantization`, `boundary_tick`, `boundary_bar`, `latency_ticks`, `latency_ms`, `tempo_bpm`, `time_signature`, `aligned`, `realization` (`kind` plus only the companions that kind uses, and `fade_start_tick` / `source_release_tick` when those exist), `replaced_request_id` nullable, `warnings` (max 8 objects of `code`, `target_id`, `message`).

`boundary_bar` is `timeline.bar_at_tick(boundary_tick)`. Messages in warnings are ≤ 200 characters.

HTTP detail codes, in addition to existing adaptive-score codes: `transition_unsatisfied`, `phrase_unavailable`, `loop_unavailable`, `loop_end_passed`, `cue_not_found`, `exit_passed`, `exit_off_grid`, `exit_unavailable`, `boundary_unavailable`, `meter_grid_indivisible`, `position_outside`, `realization_invalid`, `transition_request_not_pending`. `position_tick` past `duration_ticks` is `position_outside`. Reuse `map_adaptive_score_error_to_http`. Do not put the schedule object inside `details`.

Collaboration permission is `read` on schedule, get, and cancel. Flag off stays open. These routes do not require `write_score`.

**8. Static validation (save path)**

- Existing `custom` / `custom_grid_bars` rules stay. New tokens do not require a grid.
- `loop_end` on an eligible transition whose source loop is disabled is `impossible_transition`. Message names the state id and says the loop is disabled.
- `cue` without `cue_label` never reaches graph validation; the model rejects it.
- A `phrase` quantization with no intersecting section, when a composition is bound, adds warning `phrase_unaligned`. It does not block save. `strict=true` promotes that warning the way it promotes other warnings.
- Do not mark `immediate` impossible. Do not treat a cycle as impossible.
- Keep every existing validation test.

**9. Command payloads**

`create_transition` and `edit_transition` accept optional `cue_label` and optional `realization`. Omitted fields keep today's defaults (`cue_label` null, realization cut). No new `op`. Unknown keys still 422. `_transition_body` and the explicit field tuple in `_edit_transition` both copy `cue_label` and `realization` into the dict that `AdaptiveScoreTransitionV1.model_validate` accepts. Adding the payload fields alone drops them.

**10. Frontend**

On `AdaptiveScorePanel`, under the graph, a schedule form:

- Destination state select (other states on the score).
- Position tick input, default `0`.
- Button schedules the edge from the selected authoring state to that destination. The client does not send a quantization.
- The panel shows quantization, boundary tick, boundary bar, latency milliseconds, tempo, meter, realization kind, and Cancel.
- Test ids: `adaptive-transition-schedule`, `adaptive-transition-latency`, `adaptive-transition-cancel`, `adaptive-scheduled-status`.
- Empty pending state hides the latency line.
- Project change clears the pending preview with the rest of the adaptive session.
- No Tone.js import in this panel.
- The existing quantization `<select>` lists every `AdaptiveTransitionQuantization` token, including the stored value. Submitting that form must not rewrite a stored `phrase`, `loop_end`, or `cue` to `bar`.

**11. Logging**

INFO at the HTTP boundary: `method`, `project_id`, `score_id`, `http_status`, `quantization`, `boundary_tick`, `latency_ms`, `latency_ticks`, `realization_kind`, `request_id`, `duration_ms`, `replaced` (boolean). DEBUG in the pure function: chosen `transition_id`, `position_tick`, `boundary_tick`, warning codes. ERROR on rejection: `code` and `request_id` when one exists.

Do not log `body_json`, events, pitches, marker labels, `cue_label`, section labels, flag values, or the runtime dict. Flag count is allowed at DEBUG (`flag_count` integer).

Production can lower `LOG_LEVEL` without a code change.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: resolve adaptive transition boundaries`
- **Commit 2** (after tasks 4–6): `feat: schedule pending adaptive transitions`
- **Commit 3** (after tasks 7–8): `feat: preview adaptive transition latency`
- **Commit 4** (after task 9): `docs: describe the adaptive transition scheduler`

## Tasks

### Phase 1: Grid and realization
- [x] Task 1: Extend the adaptive score schema
  - In `backend/app/adaptive_score_schemas.py`, keep `AdaptiveQuantization` as the five current tokens for `AdaptiveScoreStingerV1`. Add `AdaptiveTransitionQuantization` with those tokens plus `phrase`, `loop_end`, and `cue`, and use it on `AdaptiveScoreTransitionV1.quantization` and on `CreateTransitionPayload` / `EditTransitionPayload`. Add `cue_label` and `realization` on `AdaptiveScoreTransitionV1` with the locked companion rules. Default omitted realization to `cut`. A stinger that uses `phrase`, `loop_end`, or `cue` fails schema validation.
  - Add the new error codes and the response model `AdaptiveTransitionScheduleV1` (`schema_version` `adaptive.transition.schedule.v1`). Add the schedule request model, including `runtime`, with `extra=forbid` and boolean rejection on numeric fields.
  - Extend `create_transition` and `edit_transition` payloads with optional `cue_label` and `realization`. Do not add an `op`.
  - Extend `backend/tests/test_adaptive_score_schema.py`: an old transition JSON without the new fields still parses as `cut`; an old stinger still parses; a stinger with quantization `phrase` fails; `cue` without `cue_label` fails; `crossfade` without `crossfade_ms` fails; a boolean latency or intensity fails.
  - Depends on: none.

  LOGGING REQUIREMENTS:
  - Reuse `log_adaptive_schema_failure` with model name, field, and code.
  - Do not log the document, `cue_label`, or realization material.

- [x] Task 2: Resolve the musical boundary
  - Create `backend/app/services/adaptive_score_transitions.py` with `schedule_musical_transition`. Inputs are the score, `CompiledTimeline`, section projections, marker projections, and the request. No composition object, no SQLite, no FastAPI.
  - Implement the locked grids, exit kinds, loop end, cue skip, and phrase skip. Call `CompiledTimeline.tick_to_seconds` and `round_half_away_from_zero` for `latency_ms`. For `custom`, candidates start at the material start bar. A playhead before that anchor still lands on the anchor. A `beat` request whose `position_tick` is `duration_ticks` returns that tick when it is a beat boundary.
  - Unit tests in `backend/tests/test_adaptive_score_transitions.py` cover the locked tables (120 BPM `4/4` bar and beat, `7/8` beat and bar), a tempo change that keeps `boundary_tick` stable, `custom` from before the material anchor landing on that anchor, `beat` at `duration_ticks` returning that tick, `immediate` allowed only on an `immediate` edge (the function has no override parameter; cover by asserting a `bar` edge returns a bar line from a mid-bar tick), off-grid cue skipped, off-grid tick exit rejected, position past the loop end rejected, and three identical calls returning the same integers.
  - Add a test that `sys.modules` after importing this module does not include `app.ai_runtime` or `app.services.fake_llm`, and that the module source does not import them.
  - Depends on: Task 1.

  LOGGING REQUIREMENTS:
  - DEBUG: `transition_id`, `quantization`, `position_tick`, `boundary_tick`, `latency_ticks`, `latency_ms`.
  - DEBUG warning codes only. Do not log marker labels or `cue_label`.
  - ERROR: rejection `code`, `from_state_id`, `to_state_id`. No runtime flags.

- [x] Task 3: Attach a realization without moving the boundary
  - In the same pure function, fill `realization` for cut, crossfade, phrase, stinger, and overlap using the locked lead and release ticks.
  - Tests: crossfade does not change `boundary_tick` from the `bar` case; a lead that would start before `position_tick` clamps `fade_start_tick` and leaves the boundary; overlap on a bar line reports a bar-line release; overlap on a `beat` boundary raises `realization_invalid` and does not move `boundary_tick`; a missing stinger raises `realization_invalid` and the caller can assert no schedule object was returned.
  - Depends on: Task 2.

  LOGGING REQUIREMENTS:
  - DEBUG: `realization_kind` and, for crossfade, `crossfade_ms` and `fade_start_tick`.
  - Do not log `phrase_material` contents.

<!-- Commit checkpoint: tasks 1–3 -->

### Phase 2: Pending requests and HTTP
- [x] Task 4: Hold one pending request
  - Create `backend/app/services/adaptive_score_transition_pending.py` with `TransitionPendingRegistry`: `put` (returns the replaced id or null), `get`, `cancel`. A lock guards the dict. Cancel of a non-current id raises `transition_request_not_pending` and leaves the current entry. Add `reset_default_registry()` for the module-global registry.
  - Tests in `backend/tests/test_adaptive_score_transition_pending.py`: replace returns the old id; cancel of the old id does not drop the new one; cancel of the current id leaves `get` empty.
  - Depends on: Task 1. The registry stores the schedule DTO; it does not call the resolver.

  LOGGING REQUIREMENTS:
  - INFO: `project_id`, `score_id`, `request_id`, action `put` or `cancel`, `replaced` boolean.
  - Do not log the schedule body.

- [x] Task 5: Validate the new authored tokens
  - In `backend/app/services/adaptive_score_validation.py`, emit `impossible_transition` for eligible `loop_end` when the source loop is disabled. Emit warning `phrase_unaligned` from the bind path when quantization is `phrase` and no section intersects the material. Leave `strict` promotion as it is. Add `realization.phrase_material` to `iter_material_refs` with `target_id` set to the transition id, and make `_has_symbolic_ref` in `adaptive_score_service.py` count that ref so binding and `mixed_revision_targets` run for it.
  - Extend `backend/tests/test_adaptive_score_validation.py`. Existing tests stay. A circulating Exploration → Combat → Victory graph with `bar` quantization still saves. A phrase material on a second `revision_id` is `mixed_revision_targets`.
  - In `backend/app/services/adaptive_score_commands.py`, copy `cue_label` and `realization` inside `_transition_body` and inside the field tuple in `_edit_transition`, then keep the existing `AdaptiveScoreTransitionV1.model_validate` call. Extend `backend/tests/test_adaptive_score_commands.py` with one create that sets `cue` plus `cue_label`, and one edit that sets `realization` and still has that object after a later edit of `priority`.
  - Depends on: Task 1.

  LOGGING REQUIREMENTS:
  - DEBUG per new finding: `code`, `target_id`, `severity`. Reuse the existing graph INFO line.
  - Do not log the score body.

- [x] Task 6: Expose read-only schedule routes
  - Add `backend/app/services/adaptive_score_transition_service.py`: load the score, compare `expected_document_revision`, compile a timeline, call the pure function, then `put` on the registry only after success. Phrase material with error findings raises before `put`. Always compile the timeline from the working composition, or from the single shared `revision_id` when every symbolic ref names that revision. Do not use the `_has_symbolic_ref` early return as a reason to skip the clock. An asset-only score still gets that clock and skips material binding unless a phrase realization is symbolic.
  - Routes on `backend/app/routers/adaptive_scores.py`: `POST .../transition-requests` (201 or 200), `GET .../transition-requests/current` (200, or `Response(status_code=204)` with no response model when empty), `DELETE .../transition-requests/{request_id}` (the same 204 response). Permission `read`. The API fixture calls `reset_default_registry()` before each test.
  - Extend `backend/tests/test_adaptive_score_api.py`: the Exploration → Combat `bar` acceptance numbers; a second call replaces and reports `replaced_request_id`; a revision mismatch leaves the first pending request and does not change `document_revision` or `composition_json`; cancel then GET is empty (status 204); a `viewer` still succeeds when collaboration is on because the permission is `read`; an asset-only score still receives a bar-aligned boundary from the working composition.
  - Depends on: Tasks 3, 4, and 5.

  LOGGING REQUIREMENTS:
  - INFO on each route with the fields in decision 11.
  - DEBUG on rejection: `code`.
  - Do not log the composition, flags, or `cue_label`.

<!-- Commit checkpoint: tasks 4–6 -->

### Phase 3: Preview
- [x] Task 7: Show the schedule on the Adaptive tab
  - Add schedule, current, and cancel functions in `frontend/src/api/adaptiveScoreApi.js`. 204 current means no pending request. Failures become `Error` with `status` and `code`.
  - Add a store slice for the pending schedule next to the adaptive session in `frontend/src/store/musicStore.js`, cleared wherever `initialAdaptiveScoreState` is reset. Extend `frontend/src/store/musicStore.adaptiveScore.test.js`: a successful schedule stores `latency_ms`; a 409 does not clear a previous pending object; project replace clears it.
  - Render the form in `frontend/src/components/AdaptiveScorePanel.jsx` with the test ids in decision 10. The header `Current state:` stays the authoring selection. The schedule block is labeled `Scheduled transition`. The existing quantization `<select>` lists every `AdaptiveTransitionQuantization` token and keeps the stored value selected, so editing another field does not rewrite `phrase`, `loop_end`, or `cue` to `bar`.
  - No `tone` import from this panel.
  - Depends on: Task 6.

  LOGGING REQUIREMENTS:
  - `console.debug` on schedule: score id, quantization, `latency_ms`, boundary tick.
  - `console.warn` on failure: HTTP status and `code` only.
  - Do not log the score JSON or the runtime flags.

- [x] Task 8: Playwright preview journey
  - Extend `frontend/e2e/adaptive-score.spec.js` or add `frontend/e2e/adaptive-transition.spec.js`.
  - After Exploration and Combat exist with a `bar` transition, submit position tick `2400` only if the seeded composition's tempo, meter, and `ticks_per_quarter` match the acceptance fixture. The expressive seed is 4 bars; if its tempo or meter differs, set position tick to `1` and assert the shown boundary tick is a compiled bar start greater than or equal to 1, and that the latency text is stable across two clicks.
  - Assert Cancel removes the latency line. Assert the composition is still the seed (the piano roll still shows the original bar count).
  - Verify a desktop viewport and a narrow viewport: the latency text remains reachable.
  - Depends on: Task 7.

  LOGGING REQUIREMENTS:
  - No new server log lines. The journey relies on the route INFO from Task 6.
  - Browser helpers may `console.debug` the score id only.

<!-- Commit checkpoint: tasks 7–8 -->

### Phase 4: Documentation
- [x] Task 9: Document the scheduler
  - Update `docs/adaptive-score.md`: quantization table including `phrase`, `loop_end`, and `cue` on transitions; stingers stay on the five original tokens; realization kinds, including overlap rejected off a bar line; the acceptance bar example; pending replace/cancel; in-memory loss on restart; the route list; empty GET and successful DELETE are HTTP 204 with no body; the statement that this path does not render audio, does not write the score, and does not call an LLM. Replace the blanket sentence that playback timing is out of scope with that narrower statement. Keep the authoring current-state definition.
  - Update the Adaptive scores row in `.ai-factory/ARCHITECTURE.md` and the adaptive entries in `AGENTS.md` and `docs/CODEBASE_MAP.md` so they name the transition service, the pure resolver, and the pending registry.
  - Do not edit `ROADMAP.md`.
  - Depends on: Tasks 6 and 7.

  LOGGING REQUIREMENTS:
  - Document the INFO fields and the redaction list from decision 11 (no labels, no flags, no `body_json`, no pitches).

<!-- Commit checkpoint: task 9 -->
