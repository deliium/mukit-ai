# Implementation Plan: V5 Adaptive Score Playback Runtime

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-29

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra (post `/aif-improve` 2026-09-29)
- UI: yes
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request for integration tests and telemetry
- Scope: a session playback clock for one `adaptive.score.v1` graph. It advances bar and beat, loops, queues state requests, and applies intensity, phrases, stingers, and layer fades by calling the existing pure scheduler and layer map. The playable notes stay `composition.v2`. The graph stays `adaptive.score.v1`. There is no `composition.v5` and no `adaptive.score.v2`. The clock does not generate notes and does not call an LLM.

## Roadmap Linkage
Milestone: "V5 Adaptive Score playback runtime"
Rationale: Domain model, authoring, the transition scheduler, and intensity layers are already checked on the roadmap. This plan is the session clock that plays that graph continuously. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

Play one adaptive score continuously while state and intensity requests arrive.

Ship:

1. A pure step function and an in-memory session that hold runtime state apart from the stored graph and apart from the authoring selection.
2. A status API, `adaptive.playback.runtime.v1`, including a deterministic simulation mode.
3. An Adaptive-tab controller that seeks, loops, and fades through the existing Tone.js transport and session mixer. It does not add a second source of note events.
4. Support for state changes, intensity changes, loops, transition phrases, stingers, and layer fades.
5. An invalid state request leaves the transport running. Integration tests and structured telemetry cover the locked Exploration → Suspense → Combat → Victory scenario.

Acceptance: with tempo 120, `4/4`, and `ticks_per_quarter` 480, the simulation in decision 10 starts in Exploration, wraps its loop once, rejects a missing state without stopping, then plays Suspense, a one-bar Combat phrase, Combat, and a Victory stinger on bar lines. The same script returns the same ticks. `projects.composition_json`, the score row, and `document_revision` stay unchanged.

```text
Adaptive tab Play / state / intensity
        ↓  session HTTP (read permission; no score write)
GET/POST/DELETE .../adaptive-scores/{score_id}/playback
        ↓
services/adaptive_playback_service.py     (load score + timeline projections; no writes)
        ↓
services/adaptive_playback.py             (pure step; no SQLite, FastAPI, or LLM)
        ↓  calls existing pure functions
services/adaptive_score_transitions.schedule_musical_transition
services/adaptive_score_layers.map_adaptive_layers
        ↓
adaptive.playback.runtime.v1
        ↓
frontend session controller
        ↓  existing engine
tonePlaybackEngine setLoop / seekToTick / start
session mixer mute + track gain ramp
```

**Terminology lock:** Product generation is **V5**. The stored graph stays **`adaptive.score.v1`**. **Authoring current state** stays `adaptiveSelectedStateId` on the Adaptive tab. **Runtime state** is `runtime_state_id` on the playback snapshot. They are different fields. **Pending transition** inside the snapshot is the boundary the clock is waiting on. It is not the in-memory authoring slot from `TransitionPendingRegistry`, and it is not a document revision. **Transition queue** is the FIFO of later destination requests. **Playback horizon** is one bar of look-ahead used to arm a boundary. **Simulation** advances only when a test or client sends `advance_ticks`. **Live** reports a tick taken from `getPlaybackPosition`. **Active layers** still come from `map_adaptive_layers`. **Audible** keeps that function's meaning. This plan does not select variants.

Predecessors: `.ai-factory/plans/v5-adaptive-score-domain-model.md`, `.ai-factory/plans/v5-adaptive-score-authoring.md`, `.ai-factory/plans/v5-adaptive-score-transition-engine.md`, and `.ai-factory/plans/v5-adaptive-score-intensity-layers.md`. Do not reopen their decisions (reference-only material, `mix_hint` is a label, `target_gain` is 0 or 1, commands that already exist keep their payloads, the schedule route stays read-only and keeps one pending slot, authoring current state is not a playback cursor, no note events, no `composition.v5`).

## Approach Evaluation (locked)

### Part A — Where the clock lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. The browser chooses the next bar, the next state, and the active layers** | Audio stays local | A second scheduler drifts from `schedule_musical_transition` and `map_adaptive_layers` | **Reject** |
| **B. One pure Python step. HTTP returns `adaptive.playback.runtime.v1`. Tone.js only applies seek, loop, and gain** | The acceptance ticks are stable. Simulation tests do not need a browser | A live client posts its playhead tick | **Accepted** |

### Part B — What is stored

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Write `runtime_state_id` into `body_json`** | Survives restart | Confuses authoring selection with the playhead and bumps `document_revision` on every bar | **Reject** |
| **B. Invent `adaptive.score.v2` or `composition.v5` for the clock** | A place to store the cursor | Both schemas are forbidden | **Reject** |
| **C. Process memory only, same lifetime as `TransitionPendingRegistry`** | Restart drops the session. The score row never changes | A second process does not see the session | **Accepted** |

### Part C — Queue versus the existing pending slot

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Replace `TransitionPendingRegistry` with a queue** | One object | Reopens the locked one-slot schedule API | **Reject** |
| **B. Playback writes the authoring slot on every state request** | The Scheduled transition label moves by itself | A failed playback request would fight the locked "failed schedule leaves the slot" rule | **Reject** |
| **C. The session has its own pending boundary and a FIFO queue. The authoring slot is untouched** | Schedule POST/GET/DELETE stay as they are. Three queued states can play in order | The panel must label playback separately from Scheduled transition | **Accepted** |

### Part D — How an invalid state request fails

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. HTTP 422 and the client calls `stop()`** | Simple errors | Music stops because the request was bad | **Reject** |
| **B. Apply `fallback.on_missing_material: silence` to a bad state id** | Reuses a stored policy | Silence is for missing material, and the requirement says the music keeps going | **Reject** |
| **C. A well-formed request that names a missing or unsatisfied state returns 200. The snapshot keeps `transport: playing`, appends a warning, and follows `on_invalid_transition` (`stay` or `default_state`)** | The transport never sees a stop instruction from that request | The client must keep Tone running when `warning` is set | **Accepted** |

### Part E — What the transport plays

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Copy the state's bars into a new composition** | Easy local buffer | Writes or clones playable notes | **Reject** |
| **B. Add a `playbackSource` kind that splits from the working score** | Fits the preview audition pattern | The notes are the working composition. A second source fights the working transport | **Reject** |
| **C. Session control of the working Tone transport: `setLoop`, `seekToTick`, and session gain. Instructions come from the snapshot** | Notes stay where they are. Stop restores loop and mute | Phrase and stinger seeks must happen while `Transport.state` stays `started` | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Graph | `AdaptiveScoreV1` states, loops, entries, exits, transitions, layers, stingers, fallback | Read-only input to the step |
| Scheduler | `schedule_musical_transition` in `adaptive_score_transitions.py` | Called by the step. Quantization, conditions, and realization companions stay there |
| One authoring slot | `TransitionPendingRegistry` keyed by `(project_id, score_id)` | Left unchanged. Playback does not call `put` or `cancel` |
| Layers | `map_adaptive_layers` returns `active`, `audible`, `target_gain` 0 or 1, `fade_end_tick` | Called by the step on intensity and on state commit |
| Timeline | `CompiledTimeline.bar_start_tick`, `bar_end_tick`, `bar_at_tick`, `tick_to_seconds` | Bar, beat, loop end, and horizon use it. The step does not reimplement meter math |
| Tone | `createPlaybackEngine` `start`, `setLoop`, `seekToTick`, `getPlaybackPosition`, `getTransportState` | The client applies instructions. `relocate` already keeps a short gain ramp and does not rebuild note events |
| Mixer | `updateMixerTrackControl` on `PLAYBACK_MIXER_SCOPE_WORKING`; slider mute remembers `adaptiveLayerMuteSnapshot` | Slider behavior stays. Playback mute is a separate session patch |
| Authoring UI | `adaptiveSelectedStateId`, Scheduled transition, intensity slider | Playback adds a sibling block. It does not overwrite the selected card |
| Logging | INFO ids and counts; never `body_json`, pitches, or event arrays | Same redaction |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Runtime snapshot | Nothing holds runtime state, bar, beat, loop, active layers, pending boundary, horizon, and queue together |
| Continuous clock | The scheduler returns a boundary and does not advance a playhead or wrap a loop |
| Phrase and stinger audition | Realization payloads name material. Nothing seeks the transport through them |
| Layer fade in time | The map reports `fade_end_tick`. The slider mutes immediately and only when every row is `track_range` |
| Invalid-request continuity | Schedule HTTP returns 422 and does not keep a transport running |
| Status API | Nothing returns `adaptive.playback.runtime.v1` |
| Simulation | Tests cannot step the scenario without wall-clock time |
| Telemetry | Adaptive routes do not emit a playback event name or counters |

### Coupling risks to avoid

1. Importing `app.ai_runtime`, `app.services.fake_llm`, LangChain, or any chat client from `adaptive_playback.py` or `adaptive_playback_service.py`.
2. Importing SQLite, `adaptive_score_store`, or FastAPI from the pure step.
3. Writing `body_json`, `projects.composition_json`, snapshot blobs, or the authoring pending slot from start, command, advance, or status.
4. Copying `events`, `pitch`, or marker and section label strings into the snapshot or into INFO logs.
5. Using wall-clock time, `random`, or a `mix_hint` gain curve. `target_gain` stays 0 or 1. A linear fade only ramps that integer across the already computed tick span.
6. Treating `adaptiveSelectedStateId` or `AdaptiveScoreStateV1.intensity` as the playhead. Nominal state intensity is used only when a playback intensity has not been set, the same rule as the layer request.
7. Selecting variants inside the step.
8. Muting a track because a section, motif, or asset layer is active. Only `track_range` track ids reach the mixer.
9. Changing `schedule_musical_transition`, `map_adaptive_layers`, quantization tokens, or the one-slot registry.
10. Stopping Tone when the status contains a warning or when the command body names an unknown state.
11. Letting `ai_agents/` import the step, the playback service, or the playback registry. Agents that need a snapshot use HTTP.
12. Adding a `SpanKind` value. Playback telemetry is structured logs plus counters on the snapshot. `operation.span.v1` stays `run | agent | model | render`.
13. Editing `ROADMAP.md`.
14. Bumping `schema_version` or adding `adaptive.score.v2`.

## Scope And Decisions

### In scope
- Session snapshot, pure step, in-memory registry, status and command routes.
- Simulation advance and a live observed tick.
- Loop wrap, FIFO queue, phrase window, stinger window, layer fade instructions.
- Adaptive tab playback block bound to the working Tone transport.
- Integration test, frontend instruction test, telemetry, and the doc updates in Task 9.

### Out of scope
- Note generation, region rewrite, arrangement, or variant selection.
- A continuous loudness curve. `mix_hint` stays `bed | foreground | ornament`.
- Changing transition quantization, realization companion rules, or `TransitionPendingRegistry`.
- FluidSynth, a new `playbackSource` kind, and sampler overlap.
- An LLM call, a new agent id, or workspace artifacts.
- A new Alembic revision. Nothing from this session is written to SQLite.
- Game-flag listeners and cross-process session sharing. An optional `flags` object on a command is passed through to the existing condition check and is not stored on the score.
- Extending `SpanKind`.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("read")
services/adaptive_playback_service.py
        ↓  load score, check document_revision, project spans, compile timeline
services/composition_timeline.compile_timeline
services/adaptive_playback.py                         (pure)
        ↓
schedule_musical_transition
map_adaptive_layers
```

`step_adaptive_playback` accepts the score, a `CompiledTimeline`, section and marker projections, layer span projections, the previous snapshot, and one command. It does not accept tracks or events. The service builds projections and drops note events before the call. `ai_agents/` does not import these modules.

**2. Snapshot**

`AdaptivePlaybackRuntimeV1` is `extra=forbid`. `schema_version` is `adaptive.playback.runtime.v1`. It is not stored in `body_json`.

| Field | Rule |
|-------|------|
| `playback_id` | `^pbr_[0-9a-f]{8}$`, minted once at start |
| `mode` | `simulation` or `live` |
| `transport` | `playing`, `held`, or `stopped` |
| `runtime_state_id` | State the clock is in |
| `position_tick` | Integer `>= 0` |
| `bar` | `CompiledTimeline.bar_at_tick`, 1-based |
| `beat` | `floor((position_tick - bar_start_tick(bar)) / beat_ticks) + 1`. `beat_ticks = bar_duration_ticks / numerator` using the meter at `position_tick`. A tick on a bar line is beat 1 |
| `intensity` | Float `0..1`, not a boolean |
| `loop` | `enabled`, `start_bar`, `end_bar`, `start_tick`, `end_tick` |
| `active_layer_ids` | Ids `map_adaptive_layers` marks `active`, in stored order |
| `pending_transition` | Null, or `transition_id`, `to_state_id`, `boundary_tick`, `quantization`, `realization_kind` |
| `queue` | Up to 4 `{to_state_id, transition_id}` items. `transition_id` may be null |
| `horizon_end_tick` | `min(duration_ticks, position_tick + horizon_ticks)`. Default horizon is one current bar (1920 ticks in the fixture) |
| `arm_boundary` | True when a pending `boundary_tick` is inside the horizon |
| `phase` | `bed`, `phrase`, or `stinger` |
| `phrase` | Null, or `start_tick` and `end_tick` |
| `active_stinger_id` | Null or the stinger id |
| `instructions` | `seek_tick` or null, `loop`, `track_gains`, `stop: false` except after an explicit stop command |
| `warnings` | Existing warning shape, max 8 |
| `telemetry` | `step_count`, `rejected_request_count`, `last_event` |
| `document_revision` | Echo of the score loaded at start |

`last_event` is one of `started`, `advanced`, `loop_wrapped`, `state_committed`, `phrase_started`, `phrase_finished`, `stinger_started`, `stinger_finished`, `intensity_changed`, `request_rejected`, `request_queued`, `held`, `stopped`.

`track_gains` lists `track_range` layer rows and the active stinger's `track_range` tracks: `track_id`, `target_gain` 0 or 1, `fade_end_tick`. Section, motif, and asset rows stay out of this list. A track named by any active `track_range` layer has `target_gain` 1. A track named only by inactive `track_range` layers has `target_gain` 0. Stinger `track_range` tracks stay at `target_gain` 0 until `active_stinger_id` is set, then 1 until that stinger span ends, then 0. A track the response does not name is omitted so the client leaves it alone.

`instructions.loop` follows the current state's authored loop, except when a pending `boundary_tick` equals that loop's `end_tick` and `arm_boundary` is true. Then `instructions.loop.enabled` is false so Tone does not wrap the playhead back to the loop start. The stored score loop stays enabled.

**3. Commands**

`AdaptivePlaybackCommand` is `extra=forbid`, one object, `op` discriminator:

| `op` | Fields | Effect |
|------|--------|--------|
| `advance` | `advance_ticks` int `>= 0`, not a boolean | Simulation only. Live returns the same snapshot with warning `advance_ignored` |
| `observe` | `position_tick` int `>= 0` | Live only. Simulation returns warning `observe_ignored` and does not move |
| `request_state` | `to_state_id`, optional `transition_id` | Queue or reject. Does not stop transport |
| `set_intensity` | `intensity` float `0..1`, not a boolean | Updates intensity and calls the layer map at the current tick. `previous_intensity` is the session intensity before this command |
| `set_flags` | `flags` map, same limits as `AdaptiveTransitionRuntimeV1.flags` | Stored on the session only. Passed as `runtime.flags` and `runtime.bars_in_state` into the scheduler |
| `stop` | none | `transport: stopped`, `instructions.stop: true`, then the registry drops the session |

A body that fails schema validation is HTTP 422 and does not modify the session. A body that validates and names a missing state is HTTP 200.

**4. Clock rules**

Start creates the session at `position_tick` 0 in `initial_state_id`, or HTTP 422 `dangling_state_ref` when that id is missing. That 422 happens before a session exists. It is not a running transport.

`on_invalid_transition` default `stay` keeps `runtime_state_id`. `default_state` queues `default_state_id` when that state exists and makes one attempt. When that attempt also fails, or the default state is missing, the warning stays and the state stays. `on_missing_material: silence` applies only when the current state's material cannot be bound after a commit, and the snapshot becomes `transport: held` with position unchanged. `hold` keeps the current state and stays `playing`. An unknown `to_state_id` never selects `silence`.

`schedule_musical_transition` raises `AdaptiveScoreError` for `transition_unsatisfied`, `exit_passed`, `loop_end_passed`, `realization_invalid`, and the other grid codes. The step catches that error, appends the code as a warning, applies `on_invalid_transition`, leaves `transport` as `playing`, and returns the snapshot. It does not let the exception become HTTP 422 while a session exists. `_eligible` only selects a transition whose id is listed on the source state's `transition_ids`.

One `advance` walks every loop end and pending boundary between the old tick and the new tick, earliest first. The playhead stops on each one, commits or wraps, then continues with the remaining delta. A final position check that skips an earlier boundary is incorrect.

Loop wrap: when the current state's `loop.enabled` is true, no phrase or stinger is open, and the step would land on or pass `loop.end_tick` without a pending boundary at that tick, `position_tick` becomes `loop.start_tick + overflow`. A pending boundary equal to the loop end commits and does not wrap. When that pending boundary is inside the horizon, `instructions.loop.enabled` is already false.

Queue: cap 4. The fifth `request_state` adds warning `playback_queue_full` and leaves the queue. The first item is scheduled immediately through `schedule_musical_transition` when the playhead is already inside the source state. Later items wait until the previous destination commits.

Schedule hold: the scheduler treats a playhead that is already on a bar line as that bar boundary. After a commit, the next queued item is scheduled with `position_tick` equal to the public playhead after that commit, plus one tick. That stays in the future when a phrase ends after the destination entry. The public `position_tick` does not increase for that extra tick. `bars_in_state` passed to the scheduler is the number of bar lines crossed inside the current state.

Commit: when `position_tick` reaches `pending_transition.boundary_tick`:

| Realization | Behavior |
|-------------|----------|
| `cut` | `runtime_state_id` becomes the destination. Seek to the destination entry tick. Phase stays `bed` |
| `crossfade` | Same commit as `cut`. `instructions` echo `fade_start_tick` from the schedule. The client ramps the engine's existing master gain across that span, then restores 1. Layer fades stay on per-track `uiGain`. Export audio is unchanged |
| `phrase` | Phase becomes `phrase`. `runtime_state_id` stays the source until `phrase.end_tick`. The span is the compiled bar span of the stored transition's `phrase_material`, using `CompiledTimeline` bar ticks. `AdaptiveScheduleRealizationPhraseV1` has no bar ticks. If that span cannot be compiled, commit as `cut` and add warning `phrase_span_unchecked`. If the span starts at `boundary_tick`, `seek_tick` stays null. Otherwise `seek_tick` is the phrase start. At phrase end, commit the destination, leave the playhead at phrase end when that tick sits inside the destination material, and seek to the destination entry only when the entry is still ahead |
| `stinger` | Bed commits on the boundary. The stinger's own quantization does not move the bed. `overlay` plays the stinger span and does not mute the bed. The stinger's `track_range` tracks go to `target_gain` 1 only while `active_stinger_id` is set. `duck_bed` sets bed `track_range` gains to 0 until the stinger span ends, then restores the layer map. `wait_for_exit` waits for the source exit tick before `active_stinger_id` is set; the bed still commits on the boundary. When the stinger span ends, `phase` returns to `bed` and those stinger tracks return to `target_gain` 0 |
| `overlap` | Bed commits on the boundary. The source release tick from the schedule stays in `pending_transition` until that tick, and the client does not seek away from the source before it |

Entry default `material_start` is `bar_start_tick` of the destination material. A destination whose material fails to bind uses `on_missing_material` and does not raise out of the step.

Explicit `stop` is the only command that sets `instructions.stop` true.

**5. Live observation**

`observe` moves the snapshot forward only. A tick behind the snapshot, unless this same response contains a `seek_tick`, sets warning `observation_behind` and keeps the server tick. One exception: when the authored loop is enabled, no pending boundary sits on `loop.end_tick`, and the new tick falls inside `[loop.start_tick, loop.end_tick)` after the previous tick was in that loop's last beat, the step accepts it as `loop_wrapped`. A tick farther than `horizon_ticks + one bar` is clamped to `position_tick + horizon_ticks` with warning `observation_clamped`. The step never reads the wall clock.

**6. Failure and stale documents**

`expected_document_revision` is required on start. A mismatch is HTTP 409 `adaptive_score_conflict` and does not create a session. After start, the session keeps the projections from start. A later command with a different revision returns HTTP 200, warning `document_revision_conflict`, and the same playhead. The score is not reloaded and the transport is not stopped.

`GET` with no session is HTTP 204 and an empty body, matching the empty pending-transition GET. `DELETE` of a live session stops it and returns 204. `DELETE` of an unknown id is 204 as well when nothing is running, so a double stop is quiet. `POST` commands with no session are HTTP 404 `playback_not_running` and include no stop instruction.

**7. Frontend**

New store fields live on `initialAdaptiveScoreState` and clear on project change: `adaptivePlayback` and `adaptivePlaybackError`. `adaptiveSelectedStateId` is not written by playback actions.

The Adaptive tab gains Play, Stop, the runtime state name, `bar:beat`, the queue, and four request buttons for the fixture states when those ids exist. Intensity changes during playback call `set_intensity` on the session. They still call the existing layer-intensity route when playback is stopped, so the slider's current behavior remains.

The client applies `instructions` onto the working engine in this order: loop, then seek, then gains. `relocate` clamps a seek that falls outside an enabled loop back to the loop start (`clampSeekSecondsToLoop`). Applying the loop first is what lets a cut at the loop end leave the loop.

- `loop` calls `setLoop`. When `instructions.loop.enabled` is false, that call happens before `seekToTick`.
- `seek_tick` calls `seekToTick` only while `getTransportState()` is `started`.
- `track_gains` ramp the existing per-track `uiGain` from the current value to `target_gain` so the ramp completes at `fade_end_tick`. `cut` snaps at the current tick. `bar` and a completed `linear` fade snap to the integer 0 or 1 at `fade_end_tick`. A `crossfade` realization ramps the existing master gain only.
- `stop: true` calls `stop`. A warning with `stop: false` must not call `stop`.

On Stop or project change, restore the loop and the gain values remembered when Play began. Do not write `editedMusicJson`.

No new `PLAYBACK_SOURCE_*` constant.

**8. Telemetry**

Each successful start, command, and stop logs one INFO line with `adaptive_playback: true`, `project_id`, `score_id`, `playback_id`, `last_event`, `runtime_state_id`, `position_tick`, `bar`, `beat`, `queue_depth`, `warning_codes`, `step_count`, and `rejected_request_count`. DEBUG may add `transition_id`, `boundary_tick`, and `realization_kind`. ERROR logs `code` only. Never log names, flags, material, or note data.

The snapshot `telemetry` object is what HTTP tests assert. The log line is what `caplog` asserts. No new span kind.

**9. HTTP**

Under the existing project adaptive-score router, `enforce_current(..., "read")`:

| Method | Path | Result |
|--------|------|--------|
| `POST` | `/{score_id}/playback` | Start. Body: `expected_document_revision`, `mode`. Response `adaptive.playback.runtime.v1` |
| `GET` | `/{score_id}/playback` | Current snapshot, or 204 |
| `POST` | `/{score_id}/playback/commands` | One command. Response snapshot, except the cases in decision 6 |
| `DELETE` | `/{score_id}/playback` | Stop. 204 |

**10. Locked scenario**

Fixture name `exploration_suspense_combat_victory`. Constant tempo 120, `4/4`, 480 ticks per quarter, 16 bars. Bar length 1920. Duration 30720.

| State id | Bars | Loop | Entry | `transition_ids` |
|----------|------|------|-------|-------------------|
| `state-exploration` | 1–4, ticks `[0, 7680)` | enabled, bars 1–4 | `material_start` → 0 | `tr-explore-suspense` |
| `state-suspense` | 5–8, ticks `[7680, 15360)` | disabled | `material_start` → 7680 | `tr-suspense-combat` |
| `state-combat` | 9–12, ticks `[15360, 23040)` | enabled, bars 9–12 | `material_start` → 15360 | `tr-combat-victory` |
| `state-victory` | 13–16, ticks `[23040, 30720)` | disabled | `material_start` → 23040 | none |

Those ids must be on the source state. `_eligible` ignores a transition that is missing from `transition_ids`, and validate reports `transition_eligibility_mismatch`.

Transitions, empty conditions, priority 0:

| Id | Edge | Quantization | Realization |
|----|------|--------------|-------------|
| `tr-explore-suspense` | exploration → suspense | `loop_end` | `cut` |
| `tr-suspense-combat` | suspense → combat | `next_exit` | `phrase`, `bar_range` bars 9–9, ticks `[15360, 17280)` |
| `tr-combat-victory` | combat → victory | `loop_end` | `stinger` `stinger-victory` |

`stinger-victory` is `track_range` on track `track-stinger`, bars 13–13, ticks `[23040, 24960)`, `interrupt_policy: overlay`, quantization `bar`, retrigger `once`.

Global layers (`state_id` null) reuse the intensity-plan windows: pad and piano at `0..1`, bass and strings at `0.5..1`, percussion at `0.8..1`, `layer-brass-hint` and `layer-orch` in exclusive group `orchestration` as already locked. Their fade for this scenario is `linear` / `linear` / 400 / 400 on bass and strings, and `bar` / `bar` / 0 / 0 on the orchestration pair. Material for those layers is `track_range` so gain instructions exist. Each state's bed is a `section`, so bed material does not enter `track_gains`.

Simulation script and locked results:

| Step | Command | Position, bar, beat | State and extras |
|------|---------|---------------------|------------------|
| Start | `mode: simulation`, intensity omitted so the session starts at `0` | `0`, bar 1, beat 1 | Exploration. Active layers `layer-pad`, `layer-piano`. `transport: playing`. `last_event: started` |
| 1 | `advance` 7680 | `0` after wrap | Still exploration. `last_event: loop_wrapped` |
| 2 | `advance` 2400 | `2400`, bar 2, beat 2 | Still exploration. Horizon end `4320` |
| 3 | `request_state` `state-missing` | unchanged | `transport: playing`. Warning `dangling_state_ref`. `rejected_request_count` 1. Queue empty. `instructions.stop` false |
| 4 | `request_state` suspense, then combat, then victory | `2400` | Pending `tr-explore-suspense`, `boundary_tick` 7680, kind `cut`. Queue holds combat then victory |
| 5 | `advance` 5280 | `7680`, bar 5, beat 1 | State suspense. Pending combat, `boundary_tick` 15360, kind `phrase`. Schedule used public playhead `7680` plus one tick |
| 6 | `advance` 480 | `8160`, bar 5, beat 2 | Still suspense. Intensity still `0`. Bass and strings still inactive |
| 7 | `set_intensity` `0.5` with `previous_intensity` `0` | `8160`, bar 5, beat 2 | Suspense. Bass and strings `target_gain` 1, `fade_end_tick` 8544. Pad and piano stay on |
| 8 | `advance` to `15360` | `15360` | Phase `phrase`, phrase `[15360, 17280)`, state still suspense, `seek_tick` null because phrase start equals the boundary |
| 9 | `advance` to `17280` | `17280`, bar 10, beat 1 | State combat. Phase `bed`. Loop bars 9–12. Pending victory, `boundary_tick` 23040. Schedule used `17281` |
| 10 | `advance` to `18000` | `18000` | Combat. Intensity still `0.5` |
| 11 | `set_intensity` `1` with `previous_intensity` `0.5` | `18000` | Combat. `layer-orch` active, `layer-brass-hint` suppressed, orchestration `fade_end_tick` 19200 |
| 12 | `advance` to `23040` | `23040`, bar 13, beat 1 | State victory. `active_stinger_id` `stinger-victory`. `track-stinger` `target_gain` 1. `transport: playing` |
| 13 | `advance` to `24960` | `24960`, bar 14, beat 1 | Stinger cleared. `track-stinger` `target_gain` 0. Phase `bed`. State victory. `transport: playing` |

`8160` is `7680 + 480`. `8544` is `8160 + 384`, the same 400 ms lead as the intensity plan (`round_half_away_from_zero(0.4 / (60/120/480))`). `19200` is the next bar boundary at or after `18000` (bar 11). Beat 2 at tick `2400` is `(2400 - 1920) / 480 + 1`.

Repeating the script yields the same ids and integers. After the script, `GET` the score and the project and assert `document_revision` and `composition_json` are the start values.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: step adaptive playback in memory`
- **Commit 2** (after tasks 4–6): `feat: expose adaptive playback status`
- **Commit 3** (after tasks 7–9): `feat: play adaptive scores through the Tone transport`

## Tasks

### Phase 1: Snapshot and pure step
- [x] Task 1: Add the playback snapshot schema
- [x] Task 2: Step state, loop, queue, phrase, and stinger
- [x] Task 3: Apply intensity and layer fade instructions
<!-- Commit checkpoint: tasks 1–3 -->

### Phase 2: Session service and HTTP
- [x] Task 4: Hold one session per score
- [x] Task 5: Load projections without writing
- [x] Task 6: Expose playback routes and telemetry
<!-- Commit checkpoint: tasks 4–6 -->

### Phase 3: Tone integration
- [x] Task 7: Apply playback instructions on the working transport
- [x] Task 8: Show runtime status on the Adaptive tab
<!-- Commit checkpoint: tasks 7–8 -->

### Phase 4: Documentation
- [x] Task 9: Document the playback runtime
<!-- Commit checkpoint: task 9 -->

### Task 1: Add the playback snapshot schema

Add `ADAPTIVE_PLAYBACK_RUNTIME_SCHEMA = "adaptive.playback.runtime.v1"` and the snapshot, command, start-request, loop, pending, phrase, track-gain, and telemetry models in `backend/app/adaptive_score_schemas.py` (or `backend/app/adaptive_playback_schemas.py` if that keeps the score module from growing further). `extra=forbid`. Reject booleans on every tick, gain, and intensity field via the existing `_reject_bool` helper. Add warning codes `playback_queue_full`, `advance_ignored`, `observe_ignored`, `observation_behind`, `observation_clamped`, `document_revision_conflict`, and `phrase_span_unchecked` as warning codes, not HTTP errors, except where decision 6 already maps a missing session or a start-time revision mismatch. Add `playback_not_running` to `ADAPTIVE_SCORE_ERROR_CODES` with HTTP 404 on the exception.

Do not change `AdaptiveScoreV1`, transition schedule models, or layer intensity models.

Files: the schema module above, `backend/tests/test_adaptive_score_schema.py` or `backend/tests/test_adaptive_playback_schema.py`.

LOGGING REQUIREMENTS:
- DEBUG on schema rejection through `log_adaptive_schema_failure` with `model`, `field`, and `code`.
- Do not log command payloads or score bodies.
- Levels stay behind `LOG_LEVEL`.

Depends on: none.

### Task 2: Step state, loop, queue, phrase, and stinger

Create `backend/app/services/adaptive_playback.py` with `step_adaptive_playback`. Implement decisions 4, 5, and 10 except the intensity numbers locked in Task 3. Call `schedule_musical_transition` and do not copy its grid math. Catch `AdaptiveScoreError` from that call inside the step. Phrase ticks come from the stored `phrase_material` and `CompiledTimeline`, and a missing span commits as `cut` with warning `phrase_span_unchecked`. Stinger `track_range` gains follow decision 2. The scenario graph includes the `transition_ids` column in decision 10. No import from FastAPI, sqlite3, `adaptive_score_store`, `adaptive_score_transition_pending`, `ai_runtime`, or `fake_llm`.

Unit tests in `backend/tests/test_adaptive_playback.py` drive the script in decision 10 with a compiled 16-bar timeline and the graph projections. Assert each row's `position_tick`, `bar`, `beat`, `runtime_state_id`, `phase`, pending boundary, phrase span, and stinger id. Assert step 3 leaves `transport` as `playing` and `instructions.stop` false. Assert one `advance` that crosses both a loop end and a later pending boundary handles the earlier event first. At tick `5760`, with pending `boundary_tick` `7680` equal to the exploration loop end, `instructions.loop.enabled` is false and `transport` stays `playing`. Assert `transition_unsatisfied` stays `playing`. Assert a second run matches the first. Assert the fifth queued request returns `playback_queue_full` without dropping the playing state. Assert `track-stinger` is `target_gain` 0 before step 12 and 1 on step 12.

LOGGING REQUIREMENTS:
- DEBUG: `last_event`, `position_tick`, `runtime_state_id`, `queue_depth`. No state names and no material.
- The pure function does not log INFO. The service logs INFO in Task 6.

Depends on: Task 1.

### Task 3: Apply intensity and layer fade instructions

Inside the same step, call `map_adaptive_layers` on start, on `set_intensity`, and on each state commit. Pass the session's previous intensity as `previous_intensity`. Copy `active_layer_ids` from rows with `active` true in stored order. Build `track_gains` from decision 2. Fade end ticks come only from the layer map.

Tests extend `backend/tests/test_adaptive_playback.py` for steps 7 and 11: intensity `0.5` at tick `8160` with `previous_intensity` `0` yields `fade_end_tick` `8544` on bass and strings; intensity `1` at tick `18000` with `previous_intensity` `0.5` yields active `layer-orch`, suppressed `layer-brass-hint`, and `fade_end_tick` `19200`. Step 6, still intensity `0` at tick `8160`, leaves bass and strings inactive. A section bed contributes no `track_gains` row. Repeating the call does not change those ids or integers.

LOGGING REQUIREMENTS:
- DEBUG: intensity, active count, and the first `fade_end_tick` integer.
- Do not log layer names or windows.

Depends on: Task 2.

### Task 4: Hold one session per score

Create `backend/app/services/adaptive_playback_runtime.py` with a registry keyed by `(project_id, score_id)`, one session, lost on process restart. `put` on start replaces a previous session for that pair and returns the replaced `playback_id` only inside DEBUG, not as a second playing score. `get` and `clear` follow the pending-registry lock style. This module does not import the transition pending registry and does not write SQLite.

Tests in `backend/tests/test_adaptive_playback_runtime.py`: two starts for one score leave one session; a different score id keeps its own session; `clear` makes `get` return none.

LOGGING REQUIREMENTS:
- INFO: `project_id`, `score_id`, `playback_id`, `action` (`start`, `clear`). No snapshot body.
- Logger name `__name__`. `LOG_LEVEL` controls visibility.

Depends on: Task 1.

### Task 5: Load projections without writing

Create `backend/app/services/adaptive_playback_service.py` with `start_adaptive_playback`, `get_adaptive_playback`, `command_adaptive_playback`, and `stop_adaptive_playback`. Load the score and compile the timeline the way `schedule_adaptive_transition` and `resolve_adaptive_layer_intensity` already do, including `expected_document_revision` on start. Build section, marker, and layer span projections, then call `step_adaptive_playback`. Do not call `replace_score`, `schedule_adaptive_transition` (the HTTP service), or `TransitionPendingRegistry.put`.

On `document_revision` mismatch after start, return the current snapshot plus warning `document_revision_conflict`. On a missing session command, raise `playback_not_running` at HTTP 404.

Tests may spy the store: start and the full command script leave `body_json` and `projects.composition_json` byte-identical.

LOGGING REQUIREMENTS:
- DEBUG: projection counts (state count, section count, layer count) without titles.
- ERROR: `code` only when load fails.
- No `body_json` and no pitches.

Depends on: Tasks 3 and 4.

### Task 6: Expose playback routes and telemetry

Add the four routes in decision 9 to `backend/app/routers/adaptive_scores.py`. Permission is `read`. Map domain errors through `map_adaptive_score_error_to_http`. Response model is the playback snapshot. GET and DELETE use 204 with no body when specified in decision 6.

Integration test `backend/tests/test_adaptive_playback_api.py` creates a project and the decision 10 score, runs the simulation script over HTTP, and asserts the locked ticks, the rejected missing state, `instructions.stop == false` after that rejection, and unchanged `document_revision` plus `composition_json`. A second identical script matches. `caplog` contains one INFO record with `adaptive_playback` true and `last_event` `state_committed`, and the record message and extras contain no `pitch` and no `events`.

Logging helper: one function used by the service so every route emits decision 8.

LOGGING REQUIREMENTS:
- INFO extras listed in decision 8.
- DEBUG: `op` and `mode` on command entry.
- ERROR: `code` only.
- Never log the command's raw JSON.

Depends on: Task 5.

### Task 7: Apply playback instructions on the working transport

Add `frontend/src/utils/adaptivePlayback.js` with a pure `applyAdaptivePlaybackInstructions(engine, mixer, previous, snapshot)` that returns the next remembered loop and gains. It reads `instructions` only. It does not recompute boundaries or active layer ids. `stop: false` must not call `engine.stop`. Apply `setLoop` before `seekToTick`. `seekToTick` runs only when `engine.getTransportState()` is `started`. Layer fades ramp the existing per-track `uiGain`. A crossfade ramps the existing master gain only.

Add a session ramp on `frontend/src/utils/tonePlaybackEngine.js` only if no public method can move an existing `uiGain` to 0 or 1 across a supplied duration. The ramp is session state. Stop and dispose restore the pre-playback gains. Do not change `RELOCATION_FADE_SECONDS`, FluidSynth export, or `playbackSource.js` constants.

Store actions in `frontend/src/store/musicStore.js` call the API module added in `frontend/src/api/adaptiveScoreApi.js`. Project switch keeps using `...initialAdaptiveScoreState`. Live mode polls `getPlaybackPosition().tick` with `observe` while the session `mode` is `live`, one request in flight at a time. After a `seek_tick`, the next observe uses the post-seek tick. Simulation mode is what tests call. The Adaptive tab Play button uses `live`.

Unit tests in `frontend/src/utils/adaptivePlayback.test.js`: a warning snapshot does not call `stop`; a snapshot with `instructions.loop.enabled` false and `seek_tick` 7680 calls `setLoop` before `seekToTick`; a `seek_tick` while started calls `seekToTick`; a `track_gains` entry for an unnamed track is not touched; section rows are absent so they cannot mute a bed; restore returns the remembered mute map.

LOGGING REQUIREMENTS:
- Client logger `createAppLogger('adaptivePlayback')` at info for `last_event`, `position_tick`, and `runtime_state_id` only.
- Do not log the snapshot JSON or note lists.
- Backend logging from Task 6 stays the source of HTTP telemetry.

Depends on: Task 6.

### Task 8: Show runtime status on the Adaptive tab

In `frontend/src/components/AdaptiveScorePanel.jsx`, add a Playback block: Play, Stop, runtime state, `bar:beat`, transport, queue depth, pending boundary tick, and buttons that `request_state` for each state on the loaded score. Disable Play when no score is loaded. Stop calls the DELETE route and restores mixer state from Task 7.

Keep the authoring selected card and the Scheduled transition block bound to their current fields. Playback must not call `set` on `adaptiveSelectedStateId`.

Extend `frontend/src/store/musicStore.adaptiveScore.test.js` so a playback snapshot stores on `adaptivePlayback`, a project change clears it, and a rejected-state snapshot with `stop: false` leaves the composition JSON untouched.

Playwright `frontend/e2e/adaptive-playback.spec.js`, following `adaptive-transition.spec.js`: seed the decision 10 score through the API, open the Adaptive tab, start playback, request a missing state id if the panel exposes a way to send it, otherwise call the command API and reload status, and expect the play button to stay in the playing state. Then request Suspense and expect the status text to show the pending boundary `7680` while the piano roll stays mounted. The spec does not need to run the whole 30720-tick simulation in the browser. Backend Task 6 owns the full tick script.

Verify in the browser that Play starts Tone, a queued Suspense request leaves audio running, Stop silences the transport, and the authoring selected card stays on the state the user had selected before Play.

LOGGING REQUIREMENTS:
- The panel does not `console.log` score bodies.
- Playwright must not print composition JSON.

Depends on: Task 7.

### Task 9: Document the playback runtime

Update `docs/adaptive-score.md`: the playback runtime is a session clock; authoring current state and the Scheduled transition slot stay what they are; the status schema is `adaptive.playback.runtime.v1` and is not stored; invalid state requests do not stop playback; the route table; the locked scenario's boundary ticks `7680`, `15360`, `17280`, `23040`, and `24960`; restart drops the session.

Update `docs/browser-playback.md` with one short section: adaptive playback is session control of the working transport (seek, loop, session gain), the loop instruction is applied before the seek so a cut at the loop end is not clamped back into the loop, and it does not add a playback source kind or change FluidSynth.

Update the adaptive-score row in `.ai-factory/ARCHITECTURE.md`, the adaptive paragraph in `docs/CODEBASE_MAP.md`, and `AGENTS.md` so they name `adaptive_playback.py`, `adaptive_playback_service.py`, `adaptive_playback_runtime.py`, and `adaptive.playback.runtime.v1`, and state that `ai_agents/` does not import them.

Do not edit `ROADMAP.md`.

LOGGING REQUIREMENTS:
- Docs only. No new log statements in this task.

Depends on: Task 8.
