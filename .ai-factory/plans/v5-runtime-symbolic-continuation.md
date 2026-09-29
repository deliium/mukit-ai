# Implementation Plan: V5 Runtime Symbolic Continuation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-29

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra (post `/aif-improve` 2026-09-29)
- UI: yes (status, Fill ahead, and ahead-of-playhead scheduling only; the playback clock does not call a model)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request for bounded continuation, deadlines, late discard, deterministic fallbacks, latency tests, and mocked model tests
- Scope: a session that fills a bar window ahead of Adaptive Score playback. The playable result is a session buffer. The stored graph stays `adaptive.score.v1`. Playback stays `adaptive.playback.runtime.v1`. There is no `composition.v5`, no `adaptive.score.v2`, and no write to `projects.composition_json` from this session.

## Roadmap Linkage
Milestone: "V5 Runtime symbolic continuation"
Rationale: The adaptive score graph, playback clock, and musical-context stream are already checked on the roadmap. This plan is the bounded symbolic lookahead that runs beside that clock. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

Playback can keep sounding while a symbolic model prepares the next bars.

Ship:

1. Versioned documents for the continuation snapshot, the long-term musical memory, and the session note buffer.
2. A pure planner that, from the current playback bar `N`, reserves bars `N..N+4` and targets bars `N+5..N+12`.
3. Three deterministic fallbacks that are ready before any model call returns: reuse the state's loop, a motif-preserving repeat, and fake-symbolic accompaniment.
4. A model call through the existing symbolic composer, checked with `validate_composition_integrity`, bound to a wall deadline and a tick deadline. Late, failed, or inapplicable results are discarded.
5. An Adaptive-tab control that asks for the next window and schedules only notes that are still ahead of the playhead. A failed request does not stop Tone.

Acceptance: with playback already running on a 16-bar score whose exploration loop is bars 1–4, a maintain call made while `bar` is 1 returns in under 50 ms even when the model blocks. The response keeps `transport` as `playing`, `instructions.stop` false, and `instructions.loop.enabled` true. A blocked model that finishes after a 0 ms deadline is discarded. The composition row and the adaptive-score row are unchanged. A second run on that same loop, whose mock returns before the deadline, sets `source` to `model` and `audible` to false with warning `continuation_outside_loop`, and still leaves transport playing. A third run with the loop disabled sets `audible` true and places a local bar-1 note on the start tick of bar 6. Two maintains while `bar` stays 1 share one `job_id`. The same seed, mode, and anchor bar produce the same accompaniment pitches.

```text
playback bar N  (already sounding; not blocked)
        ↓
reserved bars N .. N+4          (must already be playable)
        ↓
deadline = start of bar N+5
        ↓
target bars N+5 .. N+12
        ↓
deterministic fallback first
        ↓
symbolic model (optional, discardable)
        ↓
validate local bars, then place_local_events
        ↓
session buffer only if still applicable
        ↓
schedule only when audible
```

**Terminology lock:** Product generation is **V5**. **Anchor bar** `N` is `adaptive.playback.runtime.v1.bar` at arm time (1-based). **Reserved window** is bars `N` through `N+4` inclusive. **Target window** is bars `N+5` through `N+12` inclusive. **Deadline tick** is the start tick of bar `N+5`. **Fallback** is material computed with no model wait. **Model result** is optional. **Applicable** means the playback bar is still strictly less than `N+5`, the state id and document revision match the job, intensity has not moved by 0.25 or more, and the harmony tail is unchanged. **Buffer** is `adaptive.runtime.buffer.v1` in process memory. **Memory** is `adaptive.runtime.context.v1` inside the continuation snapshot. It is not the external musical-context session. **Playback horizon** `horizon_end_tick` stays the end of the current bar inside the existing clock. This plan does not redefine that field.

Predecessors: `.ai-factory/plans/v5-adaptive-score-domain-model.md`, `.ai-factory/plans/v5-adaptive-score-authoring.md`, `.ai-factory/plans/v5-adaptive-score-transition-engine.md`, `.ai-factory/plans/v5-adaptive-score-intensity-layers.md`, `.ai-factory/plans/v5-adaptive-score-playback-runtime.md`, and `.ai-factory/plans/v5-runtime-musical-context.md`. Do not reopen their decisions (reference-only score material, playback is process memory, invalid playback state requests leave transport playing, context samples emit existing playback commands, no note events on the score, no `composition.v5`).

## Approach Evaluation (locked)

### Part A — Where the lookahead lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Append generated bars onto `composition.v2` as soon as the model returns** | The piano roll shows them | Playback then depends on a write, and a late write changes bars the listener may already have passed | **Reject** |
| **B. Store the new bars on `adaptive.score.v1`** | Same row as the graph | The score is references only. A note array breaks the locked graph | **Reject** |
| **C. Session buffer beside the clock. The score and the composition stay unread-for-write** | Failure leaves the loop sounding. Restart drops the buffer | A second process does not see the buffer | **Accepted** |

### Part B — Who is allowed to wait

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `step_adaptive_playback` calls the symbolic composer** | One function owns time and music | A slow model stalls the clock. The hard rule forbids that | **Reject** |
| **B. The client waits for generate, then sends `advance`** | Thin server | A lost response stops the musical wait the user hears | **Reject** |
| **C. Maintain returns the fallback immediately. The model is a scheduled task the request does not await. Playback commands are never sent by this session** | Transport, loop, and seek stay on the existing clock | The client must poll or press Fill ahead | **Accepted** |

### Part C — What plays if the model is late

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Silence until the model returns** | No wrong notes | Playback depends on generation | **Reject** |
| **B. One fallback algorithm for every score** | Simple | A looping state already has the right material, and a motif score should keep its cell | **Reject** |
| **C. Ordered deterministic fallbacks, chosen before the model starts: loop, motif repeat, fake accompaniment** | Each one reuses an existing function. The last resort is always the loop instruction already published by playback | The loop fallback adds no new Tone notes | **Accepted** |

### Part D — How a result becomes inapplicable

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Apply any result that validates** | Uses the GPU work | The notes belong to an old bar or an old state | **Reject** |
| **B. Wall-clock timeout only** | Easy to test | A fast model can still return after the playhead entered bar `N+5` | **Reject** |
| **C. Job id plus tick deadline plus state, revision, intensity delta, and harmony tail** | Musical identity does not use `random`. Wall time is only the deadline | The service, not the pure planner, reads the clock | **Accepted** |

### Part E — Which generator

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New note algorithm in the continuation module** | Tuned to 8 bars | Duplicates the symbolic composer and skips the validator the user named | **Reject** |
| **B. `POST /llm/generate-music-json` LangGraph path** | Full provenance | Awaits a planner model and can repair notes with an LLM. That couples the session to chat | **Reject** |
| **C. `generate_symbolic_composition` for the model path and `generate_fake_symbolic_composition(..., prefer path)` for accompaniment, then `validate_composition_integrity`** | Both already accept a prefix composition. Fake mode needs no torch | The Music Transformer stays optional. Unavailable checkpoint falls through to fake inside the existing resolver | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Playback bar | `AdaptivePlaybackRuntimeV1.bar`, `position_tick`, `runtime_state_id`, `intensity`, `document_revision`, `instructions.loop` | Read-only inputs to the planner. `horizon_end_tick` is the current bar's end, not this window |
| Symbolic entry | `generate_symbolic_composition(plan, prefix_composition=..., seed=...)` in `services/symbolic_composition_generate.py` | Model path. Do not add a pipeline |
| Fake composer | `generate_fake_symbolic_composition` and `_merge_prefix_tracks` | Accompaniment fallback with `prefer_fake` forced by calling the fake function directly |
| Validator | `validate_composition_integrity(..., profile="generation")` | Every buffer composition, model and fallback, must pass before it is stored |
| Motif repeat | `transform_repeat` in `services/composition_motif_transform.py` | Motif fallback. Do not call `apply_motif_operation` |
| Plan document | `CompositionPlan` with `density.global_band` in `sparse` / `moderate` / `dense` | Intensity adaptation selects that band. The plan is not playable |
| Prefix pipelines | `symbolic_continuation` and `symbolic_variation` on the hybrid generate request | Names stored on the job as provenance. The runtime does not POST that route |
| Registry shape | `AdaptivePlaybackRegistry` and `AdaptiveMusicalContextRegistry`, process memory, `(project_id, score_id)` | Continuation registry is a third sibling |
| HTTP shape | Project adaptive router, `enforce_current(..., "read")` | Same permission. Generation does not require a higher role |
| Loop sound | `applyAdaptivePlaybackInstructions` seeks, loops, and ramps gain | Unchanged. `reuse_loop` does not add notes on top of that loop |
| Jam schedule | `scheduleLiveAt` uses `liveScheduledEventIds` | Do not share that list |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Window planner | Nothing maps playback bar `N` to reserved `N..N+4` and target `N+5..N+12` |
| Deadline | Nothing discards a symbolic result because the playhead reached the target |
| Fallback chain | Playback loops, motif transforms, and the fake composer are separate tools |
| Long-term memory | Nothing in the adaptive session keeps theme ids, a harmony tail, a repetition count, and an energy series together |
| Non-blocking arm | The symbolic adapter is synchronous. Nothing returns fallback before that call finishes |
| Client schedule | Nothing places adaptive lookahead notes on a queue that jam can clear |

### Coupling risks to avoid

1. Importing `adaptive_playback.py`, `adaptive_playback_service.py`, SQLite, FastAPI, or an LLM client from the pure planner.
2. Calling `command_adaptive_playback`, `put` on the playback registry, or `schedule_musical_transition` from the continuation service.
3. Importing `liveJamContracts`, `liveHarmonyBelief`, or any jam module.
4. Importing `ai_runtime`, `fake_llm`, or LangChain from the planner or the fallback module. The service may call `generate_symbolic_composition` and `generate_fake_symbolic_composition` only.
5. Writing `body_json`, `projects.composition_json`, or the authoring pending slot.
6. Adding `composition.v5` or `adaptive.score.v2`.
7. Using `random` to choose pitches, mode, or fallback kind. The fake seed is a deterministic function of mode, anchor bar, and state id.
8. Logging event arrays, prompts, raw MIDI payloads, or the buffer JSON.
9. Awaiting the model inside `step_adaptive_playback` or inside the playback command route.
10. Clearing `liveScheduledEventIds` from the continuation panel.
11. Letting `ai_agents/` import the new continuation modules.
12. Editing `ROADMAP.md`.
13. Changing playback command payloads, `horizon_end_tick`, quantization tokens, or `map_adaptive_layers`.
14. Calling `selectAdaptiveState` or stopping Tone when continuation fails.
15. Inventing pitches from `harmony[].chord`. Harmony labels condition the plan. Notes come from the symbolic composer, the motif transform, or a copy of existing events.

## Scope And Decisions

### In scope
- Three strict documents, the pure window and memory step, three fallbacks, one in-memory session, five HTTP routes, deadline discard, latency and mock-model tests, a Fill-ahead control, a private Tone queue for ahead notes, and the doc updates in Task 8.

### Out of scope
- Persisting the buffer or the memory in SQLite. No Alembic revision.
- Applying the buffer into `composition.v2` or into a score state. No Apply button.
- Starting, stopping, or seeking playback. Creating score states.
- Calling the LangGraph generate route or an LLM note repair (`allow_llm_composition_repair` stays unused).
- Requiring torch or a Music Transformer checkpoint. Missing checkpoint uses the existing fake resolver inside `generate_symbolic_composition`.
- Sharing the AI Jam live-event list.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("read")
services/adaptive_runtime_continuation_service.py
        ↓  read playback snapshot; never command it
        ↓  read score + composition; never write them
services/adaptive_runtime_continuation.py          (pure window, memory, applicability)
services/adaptive_runtime_continuation_fallback.py (loop, motif, accompaniment)
        ↓  model path only
services/symbolic_composition_generate.generate_symbolic_composition
services/composition_validator.validate_composition_integrity
services/composition_timeline.compile_timeline
```

`plan_runtime_window` accepts bar, bar count, loop start/end, intensity, state id, and the previous memory. It does not accept a playback clock object. `ai_agents/` does not import these modules. `adaptive_runtime_continuation.py` does not import playback, SQLite, FastAPI, the symbolic composer, or the validator. The fallback module may import `transform_repeat`, `generate_fake_symbolic_composition`, and the validator. It may not import the playback modules. The service calls public `compile_timeline` for `bar_start_tick` and `bar_range_ticks`. It does not call `_material_span_bars` or `command_adaptive_playback`.

**2. Windows**

Defaults live in `adaptive_runtime_continuation_settings.py`. Env overrides use positive ints, and an invalid value logs a warning and keeps the default.

| Setting | Default | Meaning |
|---------|---------|---------|
| `ADAPTIVE_CONTINUATION_PLAY_BARS` | 5 | Reserved length. Bars `N` through `N+4` |
| `ADAPTIVE_CONTINUATION_GENERATE_BARS` | 8 | Target length. Bars `N+5` through `N+12` |
| `ADAPTIVE_CONTINUATION_PREFIX_BARS` | 8 | Recent material ending at bar `N` |
| `ADAPTIVE_CONTINUATION_DEADLINE_MS` | 250 | Wall budget from arm |
| `ADAPTIVE_CONTINUATION_LATENCY_BUDGET_MS` | 50 | Test ceiling for the non-awaiting arm |
| `ADAPTIVE_CONTINUATION_INTENSITY_EPSILON` | 0.25 | Larger move marks the job stale |
| `ADAPTIVE_CONTINUATION_MAX_BUFFER_EVENTS` | 512 | Extra events above this are dropped and warned |

`N` is the playback `bar`. Both windows are clipped to `1..bar_count`. If `target_start_bar` is greater than `bar_count`, the snapshot uses `job_status: idle`, `fallback_kind: reuse_loop`, warning `continuation_window_past_end`, and the service does not schedule a model.

Deadline tick is `timeline.bar_start_tick(target_start_bar)` when the target exists. The service reads that tick from the same compiled timeline playback already uses. The pure planner receives the tick as an integer argument. It does not compile the timeline itself.

**3. Snapshot**

`AdaptiveRuntimeContinuationV1` is `extra="forbid"`. `schema_version` is `adaptive.runtime.continuation.v1`. It is not stored in `body_json`.

| Field | Rule |
|-------|------|
| `continuation_id` | `^arcn_[0-9a-f]{8}$`, minted once at session start |
| `job_id` | `^arcj_[0-9a-f]{8}$` or null. A later maintain keeps this id while decision 7's identity key matches |
| `mode` | `continuation` \| `variation` \| `intensity_adaptation` \| `harmonic_continuation` \| `state_specific` |
| `anchor_bar` | Int `>= 1` |
| `reserved_start_bar` / `reserved_end_bar` | Inclusive, `end >= start` |
| `target_start_bar` / `target_end_bar` | Inclusive, or both null when the window is past the end |
| `deadline_tick` | Int `>= 0` |
| `fallback_kind` | `reuse_loop` \| `motif_variation` \| `accompaniment` |
| `source` | `none` \| `fallback` \| `model` |
| `job_status` | `idle` \| `pending` \| `applied` \| `discarded` \| `failed` |
| `applicable` | Bool. False after discard, failure, or a past-end window |
| `audible` | Bool. False when the published loop is enabled and `target_start_bar` is outside `[loop.start_bar, loop.end_bar]`. Also false for `reuse_loop` |
| `runtime_state_id` | Score state id copied from playback at arm |
| `intensity` | Float `0..1` copied from playback at arm. Not a boolean |
| `context` | `AdaptiveRuntimeContextV1` |
| `warnings` | Existing warning shape, max 8, first-seen order |
| `telemetry` | `arm_count`, `model_apply_count`, `late_discard_count`, `failure_count`, `fallback_count` |
| `document_revision` | Echo of the score read at session start |

Warning codes owned by this feature: `playback_not_running`, `continuation_window_past_end`, `continuation_late`, `continuation_stale`, `continuation_invalid`, `continuation_model_failed`, `continuation_truncated`, `continuation_outside_loop`, `continuation_material_unbounded`, `continuation_meter_assumed_constant`.

The snapshot has no `events` field. Note events belong to the buffer document.

**4. Long-term memory**

`AdaptiveRuntimeContextV1` is `extra="forbid"`. `schema_version` is `adaptive.runtime.context.v1`.

| Field | Rule |
|-------|------|
| `theme_ids` | Motif ids whose occurrences overlap the prefix, max 16, sorted, unique |
| `harmony_tail` | Last `CompositionV2HarmonyItem.chord` whose span overlaps the prefix end, or null. Max 32 characters. Not a pitch list |
| `harmony_chord_count` | Int `>= 0`, count of harmony items overlapping the prefix, max reported 32 |
| `recent_start_bar` / `recent_end_bar` | Prefix window, or null when the composition has no bars |
| `repetition_count` | Int `0..8`. How many times the pitch-class tuple of the last 2 prefix bars already occurred in that prefix |
| `energy` | List of floats `0..1`, max 8, oldest dropped. Each maintain appends the playback intensity |

Absent motifs leave `theme_ids` empty and skip the motif fallback. The memory document stores no event arrays. The in-memory job holds the prefix slice separately, and that slice is not on the HTTP snapshot.

The service resolves material to integers before the planner runs. A `section` uses that section's start bar and bar count. A `bar_range`, `track_range`, or `revision_region` uses `start_bar` and `end_bar` when both are set. `state_specific` clips the prefix to that span. Bars from an earlier state are not the prefix. A `motif` or `asset` material has no bar span: the service adds `continuation_material_unbounded` and uses the recent prefix instead. Other modes use the last `PREFIX_BARS` ending at `N`, clipped to the composition. The job stores `prefix_digest`, the first 16 hex characters of a sha256 over each prefix event's onset, pitch, and duration, in track order. The digest is not a snapshot field and is not logged.

**5. Modes**

All five modes share the same windows, deadlines, and fallback order. The mode changes the plan fragment and the prefix.

| Mode | Prefix | Plan fragment |
|------|--------|----------------|
| `continuation` | Recent bars | Key, meter, tempo, and track instruments copied from the composition. Provenance pipeline id `symbolic_continuation` |
| `variation` | Same prefix | Same plan. Provenance pipeline id `symbolic_variation`. The symbolic call still receives the prefix |
| `intensity_adaptation` | Same prefix | `density.global_band` is `sparse` below 0.34, `moderate` below 0.67, otherwise `dense` |
| `harmonic_continuation` | Same prefix | The plan key stays the composition key. The buffer may carry copied harmony labels for the target bars by repeating `harmony_tail` once per target bar. Those labels are not note events |
| `state_specific` | State material only | Same plan as continuation. Armed `runtime_state_id` is part of applicability |

The service calls `generate_symbolic_composition` with that plan, the prefix composition, and the deterministic seed. It does not pass `allow_llm_composition_repair`. The composer returns a short piece whose bar 1 is local tick 0. `place_local_events` validates that local piece with `validate_composition_integrity` and `profile="generation"`, then adds `deadline_tick` to every kept `start_tick`. When the result still contains the prefix span, events with `start_tick` below the prefix end are dropped first, and the first remaining bar is shifted so that it lands on `deadline_tick`. Filtering for events that are already inside `[deadline_tick, …)` before that shift is not the placement rule: those events do not exist yet. The local grid uses the meter at `deadline_tick` from `compile_timeline`. A later meter change inside the target window adds `continuation_meter_assumed_constant` and does not rebuild the grid.

**6. Fallback order**

Chosen by the pure planner, then realized by the fallback module. The realization runs to completion before the model task is scheduled.

| Condition | Order |
|-----------|-------|
| `repetition_count < 3` | `reuse_loop`, then `motif_variation`, then `accompaniment` |
| `repetition_count >= 3` | `motif_variation`, then `accompaniment`, then `reuse_loop` |

| Kind | Behavior |
|------|----------|
| `reuse_loop` | If `instructions.loop.enabled` and the loop has a start and end, the buffer event list is empty. The sounding material is the loop playback already published. `source` is `fallback` |
| `motif_variation` | Load the first motif in `theme_ids` that has a relative cell. Call `transform_repeat` on a copy of the destination track with `allow_overlap=True`, `duration_limit` equal to the target span in ticks, and id seed `arc-{job_id}`. Keep `result.events` only. Place them with `place_local_events`. Do not call `apply_motif_operation` |
| `accompaniment` | Call `generate_fake_symbolic_composition` with `prefix_composition=None` and `bar_count` equal to the target length. `_merge_prefix_tracks` returns the prefix unchanged when the plan is not longer than the prefix, so a prefix must not be passed on this path. Seed is `(anchor_bar * 10007 + mode_code * 97 + state_code) & 0x7FFFFFFF`, where `mode_code` and `state_code` are the sums of the code points. No `random`. Place the local bars with `place_local_events` |

If a kind throws or fails validation, the planner walks to the next kind. If all three fail, the snapshot still uses `reuse_loop` with an empty buffer and warning `continuation_invalid`. That path does not raise to the router. Playback is untouched.

**7. Deadlines and discard**

The service records `armed_monotonic_ms`, `deadline_tick`, and `prefix_digest` on the job. The service function stays synchronous. It accepts a scheduler and `now_ms`. The async router passes `lambda coro: asyncio.get_running_loop().create_task(coro)`. The service does not call `asyncio.get_running_loop()` itself. A task exception logs `code` and sets `job_status` to `failed` with warning `continuation_model_failed`. It does not send a playback command.

Maintain keeps the active job when all of these match the armed job: `anchor_bar`, `mode`, `runtime_state_id`, `document_revision`, and `prefix_digest`, and `job_status` is `pending` or `applied`. It returns the current snapshot and does not schedule a second model call. It mints a new job only when the playhead is on a new anchor bar, the mode or state id changes, the digest or revision changes, or the previous job is `failed`, `discarded`, or `idle`.

A finished model result is applied only when every check passes:

1. `job_id` is still the active job.
2. `now_ms() - armed_monotonic_ms` is strictly less than `ADAPTIVE_CONTINUATION_DEADLINE_MS`.
3. Playback `bar` is strictly less than `target_start_bar`, and `position_tick` is strictly less than `deadline_tick`.
4. `runtime_state_id` equals the armed state id.
5. `document_revision` equals the armed revision.
6. `abs(playback.intensity - armed intensity) < 0.25`.
7. The harmony tail string is equal to the armed tail, and `prefix_digest` equals the digest of the current prefix.
8. `place_local_events` has already validated the local composition. A validation failure is check 8, not a second absolute-tick slice.

Failure of 2 or 3 sets `continuation_late` and increments `late_discard_count`. Failure of 4–7 sets `continuation_stale` and increments `late_discard_count`. Failure of 8 or a raised `SymbolicCompositionGenerateError` sets `continuation_invalid` or `continuation_model_failed` and increments `failure_count`. In every failure the previous fallback buffer stays, `source` stays `fallback`, and `job_status` is `discarded` or `failed`. Transport is not read back for mutation.

`arm` does not await the scheduled task. Tests pass a scheduler that stores the coroutine. A latency test leaves it un-awaited until the assertion. A cancel on session stop closes a still-pending task.

`now_ms` is injectable. Musical pitches do not read it. `audible` is false, with warning `continuation_outside_loop`, when `instructions.loop.enabled` and `target_start_bar` falls outside `[loop.start_bar, loop.end_bar]`. The buffer may still hold the model events. The client does not schedule them.

**8. Buffer**

`AdaptiveRuntimeBufferV1` is `extra="forbid"`. `schema_version` is `adaptive.runtime.buffer.v1`.

| Field | Rule |
|-------|------|
| `continuation_id` / `job_id` | Match the snapshot |
| `source` / `fallback_kind` | Same tokens as the snapshot |
| `events` | Composition V2 note events, max 512, each `start_tick` inside the target span. Empty for `reuse_loop` |
| `harmony` | Optional copied chord labels for harmonic continuation, max 8, no pitches |

GET buffer returns this document. The continuation snapshot does not embed it. Logs do not include it.

**9. HTTP**

Under the existing project adaptive-score router, `enforce_current(..., "read")`:

| Method | Path | Result |
|--------|------|--------|
| `POST` | `/{score_id}/continuation` | Start. Body: `expected_document_revision` and `mode`. Requires a running playback session or the response is 200 with `playback_not_running` and no model task |
| `GET` | `/{score_id}/continuation` | Snapshot, or 204 |
| `POST` | `/{score_id}/continuation/maintain` | Recompute the window. Keep the active job when decision 7's identity key matches. Otherwise publish a new fallback and schedule the model. Response is the snapshot |
| `GET` | `/{score_id}/continuation/buffer` | Buffer, or 204 |
| `DELETE` | `/{score_id}/continuation` | Cancel the task, clear the session. 204 |

Maintain without a session is 404 `continuation_not_running`. A second start replaces only that score's continuation session. Unsupported `schema_version` on a future body is 422 and does not modify the session. This start body has no `schema_version` field; `mode` outside the five tokens is 422 `adaptive_score_invalid`.

**10. Client**

`AdaptiveScorePanel` renders a continuation block beside the playback block. It shows `anchor_bar`, the target bar range, `fallback_kind`, `source`, `job_status`, `audible`, and warning codes. It does not show pitches or the buffer JSON.

Fill ahead POSTs start when no `continuation_id` exists, then POSTs maintain. While `adaptivePlayback.transport === 'playing'` and a session exists, the panel POSTs maintain on a 1 second interval. While the identity key is unchanged the server returns the same `job_id`. A failed maintain sets `adaptiveContinuationError` to the server `code` and does not call stop, seek, or `selectAdaptiveState`.

`scheduleContinuationAt` and `clearContinuationScheduled` are new methods on the Tone engine. They keep a separate id list. They do not read or write `liveScheduledEventIds`. The panel schedules buffer events only when `audible` is true and (`source` is `model` or `fallback_kind` is `motif_variation` or `accompaniment`), and only when the event start tick is still at or after the playback `position_tick`. `reuse_loop` and `continuation_outside_loop` schedule nothing. Discard, stop, project change, and state-id change clear that list. A throw from schedule leaves the playback loop in place.

**11. Telemetry**

Each start, maintain, apply, discard, and stop logs one INFO line with `adaptive_runtime_continuation: true`, `project_id`, `score_id`, `continuation_id`, `job_id`, `mode`, `anchor_bar`, `target_start_bar`, `fallback_kind`, `source`, `job_status`, `audible`, `warning_codes`, and the five counters. DEBUG may add `deadline_tick`, `repetition_count`, `theme_count`, and `harmony_chord_count`. ERROR logs `code` only.

Never log events, pitches, `harmony_tail`, prompts, prefix JSON, buffer JSON, or `body_json`. No new span kind.

## Commit Plan
- **Commit 1** (after tasks 1–2): `feat: add versioned adaptive continuation snapshots`
- **Commit 2** (after tasks 3–4): `feat: plan a playback bar window and deterministic fallbacks`
- **Commit 3** (after tasks 5–6): `feat: discard late symbolic continuation without stopping playback`
- **Commit 4** (after tasks 7–8): `feat: show adaptive continuation status while playback continues`

## Tasks

### Phase 1: Contracts
- [x] **Task 1: Versioned continuation schemas and caps.**
  Deliverable: `backend/app/adaptive_runtime_continuation_schemas.py` and `backend/app/adaptive_runtime_continuation_settings.py`. Implement decisions 2, 3, 4, and 8: continuation snapshot including `audible`, embedded `adaptive.runtime.context.v1`, buffer document, start request with the five modes, parsers that raise `AdaptiveScoreError` without logging the body, and bool rejection on numeric fields. Import `_ID_RE` from `adaptive_score_schemas.py` for state ids. Reuse `log_adaptive_schema_failure`, `_reject_bool`, and `map_adaptive_score_error_to_http`. Add `continuation_not_running` as HTTP 404. Do not import playback, SQLite, FastAPI, the symbolic composer, or jam modules.
  LOG REQUIREMENTS: DEBUG on schema rejection with `model`, `field`, and `code` only. WARNING when an env value is invalid, with `setting` and `invalid: true`, then keep the default. One DEBUG line for resolved caps when settings load from the process environment. Never log events or harmony strings.
  Files: `backend/app/adaptive_runtime_continuation_schemas.py`, `backend/app/adaptive_runtime_continuation_settings.py`
- [x] **Task 2: Schema tests.**
  Deliverable: `backend/tests/test_adaptive_runtime_continuation_schema.py`. Cover an extra field, a boolean intensity, a sixth mode, event lists on the snapshot (forbidden), buffer events over 512, context energy over 8 items, theme id duplicates rejected, and settings fallback when deadline env is not an integer. Assert the default play length is 5 and the default generate length is 8. No database.
  LOG REQUIREMENTS: Assert the schema DEBUG record has no event array and no harmony tail string. Assert the settings WARNING names the setting.
  Depends on Task 1.
  Files: `backend/tests/test_adaptive_runtime_continuation_schema.py`

### Phase 2: Pure planner and fallbacks
- [x] **Task 3: Window planner, memory, applicability, and fallback realization.**
  Deliverable: `backend/app/services/adaptive_runtime_continuation.py` with `plan_runtime_window`, `update_runtime_context`, and `result_applicable`. Deliverable: `backend/app/services/adaptive_runtime_continuation_fallback.py` with `realize_fallback` and `place_local_events`. For anchor bar 1 and a 16-bar score, the window is reserved 1–5 and target 6–13. A prefix shorter than 8 bars is clipped to bar 1. `repetition_count >= 3` starts at `motif_variation`. Applicability is false when `bar >= target_start_bar`, when intensity delta is 0.25, when the harmony tail differs, or when `prefix_digest` differs. `place_local_events` validates a local composition of `GENERATE_BARS` at tick 0, then adds `deadline_tick` to each kept `start_tick`. Accompaniment calls `generate_fake_symbolic_composition` with `prefix_composition=None`. Motif fallback calls `transform_repeat` on a track copy with `allow_overlap=True` and `duration_limit` equal to the target span, and keeps `result.events` only. The pure module imports neither playback nor the symbolic composer nor the validator. The fallback module calls `transform_repeat`, `generate_fake_symbolic_composition`, and `validate_composition_integrity`. The service, not these modules, calls `compile_timeline`.
  LOG REQUIREMENTS: DEBUG in the planner with `anchor_bar`, `target_start_bar`, `fallback_kind`, `repetition_count`, and `theme_count`. Do not log INFO from the pure module. Do not log pitches, events, `harmony_tail`, or `prefix_digest`.
  Depends on Task 1.
  Files: `backend/app/services/adaptive_runtime_continuation.py`, `backend/app/services/adaptive_runtime_continuation_fallback.py`
- [x] **Task 4: Deterministic window and fallback tests.**
  Deliverable: `backend/tests/test_adaptive_runtime_continuation.py`. Assert the locked 1→(1–5, 6–13) window, clipping when the anchor is bar 14 on a 16-bar score (`continuation_window_past_end`), the repetition order switch at 3, an empty motif list skipping to accompaniment, the same seed producing the same accompaniment pitches, and applicability rejecting bar 6, a 0.25 intensity jump, a changed harmony tail, and a changed `prefix_digest`. A validator failure walks to `reuse_loop` and does not raise. `place_local_events` puts a local bar-1 note on the start tick of bar 6 for anchor bar 1. An accompaniment call passes `prefix_composition=None`. No playback registry and no sleeping model.
  LOG REQUIREMENTS: `caplog` at DEBUG contains `fallback_kind` and does not contain a pitch name from the buffer.
  Depends on Task 3.
  Files: `backend/tests/test_adaptive_runtime_continuation.py`
  <!-- Commit checkpoint: tasks 1–4 -->

### Phase 3: Session, deadline, and HTTP
- [x] **Task 5: In-memory session that never awaits the model on the arm path.**
  Deliverable: `backend/app/services/adaptive_runtime_continuation_runtime.py` and `backend/app/services/adaptive_runtime_continuation_service.py`. Implement decisions 5 and 7. Start and maintain read the playback registry with `get` and read the score plus composition. They call public `compile_timeline` for deadline ticks. They resolve section and bar-range material to integers and pass those integers to the planner. A motif or asset material warns `continuation_material_unbounded` and uses the recent prefix. They never call `command_adaptive_playback`, `put`, `_material_span_bars`, or `asyncio.get_running_loop`. The router supplies the scheduler. Maintain realizes the fallback, stores it, schedules the model coroutine, and returns, unless decision 7's identity key matches an active `pending` or `applied` job. Two maintains at the same bar then share one `job_id`. Completing the coroutine applies `place_local_events` only when `result_applicable` passes, including an unchanged `prefix_digest`. A blocking mock is not awaited by maintain. A task exception sets `continuation_model_failed` and does not command playback. Session stop cancels a pending task. An older job id cannot apply. Seed is `(anchor_bar * 10007 + mode_code * 97 + state_code) & 0x7FFFFFFF`.
  LOG REQUIREMENTS: INFO on start, maintain, apply, discard, and stop using decision 11. ERROR logs `code` only. The runtime test's `caplog` must not contain `events`, a chord spelling, or `prefix_digest`.
  Depends on Task 3.
  Files: `backend/app/services/adaptive_runtime_continuation_runtime.py`, `backend/app/services/adaptive_runtime_continuation_service.py`, `backend/tests/test_adaptive_runtime_continuation_runtime.py`
- [x] **Task 6: HTTP routes, latency test, and mocked model test.**
  Deliverable: the five routes in decision 9 on `backend/app/routers/adaptive_scores.py`. The async handlers pass `lambda coro: asyncio.get_running_loop().create_task(coro)` into the synchronous service. Integration tests in `backend/tests/test_adaptive_runtime_continuation_api.py`. Build a 16-bar composition and an exploration state whose loop is bars 1–4. Start simulation playback so `bar` is 1 and transport is `playing`. Reset the playback registry and the continuation registry around the test.
  Latency: install a model callable that waits on a `threading.Event`. POST maintain. Assert elapsed time is under `ADAPTIVE_CONTINUATION_LATENCY_BUDGET_MS` (50). Assert the snapshot `source` is `fallback`, playback GET `transport` is `playing`, `instructions.stop` is false, and `instructions.loop.enabled` is true. Release the event in `finally`. With deadline 0 ms, the finished model is `continuation_late`, `late_discard_count` is 1, and the composition and score revisions are unchanged.
  Idempotent poll: two maintains while `bar` stays 1 return the same `job_id`.
  Mocked model on the loop fixture: a stub returns a valid local continuation immediately. Deadline is 10 seconds and playback bar stays 1. After the test awaits the scheduled coroutine, `source` is `model`, `job_status` is `applied`, `audible` is false, and the warning is `continuation_outside_loop`. Buffer events, after `place_local_events`, lie inside bars 6–13. Transport stays `playing`. A raised model error leaves `source` as `fallback` and warning `continuation_model_failed`. A maintain after `request_state` moves playback to another state discards the in-flight job as `continuation_stale`.
  Linear fixture: the same score with the exploration loop disabled. The mock apply sets `audible` true. A local bar-1 note is stored at the start tick of bar 6, the same shift Task 4 locks.
  Also assert 404 `continuation_not_running` when maintain has no session, and 204 on DELETE.
  LOG REQUIREMENTS: One INFO record per maintain with `adaptive_runtime_continuation: true` and no buffer JSON.
  Depends on Tasks 4 and 5.
  Files: `backend/app/routers/adaptive_scores.py`, `backend/tests/test_adaptive_runtime_continuation_api.py`
  <!-- Commit checkpoint: tasks 5–6 -->

### Phase 4: Readout and docs
- [x] **Task 7: Adaptive tab continuation status and a private ahead-note queue.**
  Deliverable: decision 10. Extend `frontend/src/api/adaptiveScoreApi.js` and the music store with continuation actions. Add `frontend/src/components/AdaptiveContinuationPanel.jsx` and render it from `AdaptiveScorePanel.jsx`. Fill ahead starts and maintains. The 1 second poll runs only while playback transport is `playing` and a continuation id exists. A second poll at the same bar keeps the same `job_id`. Failures do not call `stopAdaptivePlayback` or `selectAdaptiveState`. Add `scheduleContinuationAt` and `clearContinuationScheduled` on `frontend/src/utils/tonePlaybackEngine.js` using a new id list. `reuse_loop` and `audible: false` do not schedule. The panel schedules only when `audible` is true, and only ticks at or after `position_tick`. Project change and playback stop clear the continuation ids and DELETE the continuation route.
  LOG REQUIREMENTS: No `console.log` of events or pitches. Failed posts set `adaptiveContinuationError` to the server `code`.
  Tests: `frontend/src/utils/adaptiveContinuation.test.js` asserts a reuse-loop buffer and a model buffer with `audible: false` produce an empty schedule list, and an audible model buffer keeps only ticks at or after the playhead. Extend `frontend/src/store/musicStore.adaptiveScore.test.js` so a rejected maintain leaves `adaptivePlayback.transport` as `playing` and a repeated successful maintain at the same bar does not replace `job_id`.
  Depends on Task 6.
  Files: `frontend/src/api/adaptiveScoreApi.js`, `frontend/src/store/musicStore.js`, `frontend/src/store/musicStore.adaptiveScore.test.js`, `frontend/src/utils/adaptiveContinuation.js`, `frontend/src/utils/adaptiveContinuation.test.js`, `frontend/src/utils/tonePlaybackEngine.js`, `frontend/src/components/AdaptiveContinuationPanel.jsx`, `frontend/src/components/AdaptiveScorePanel.jsx`
- [x] **Task 8: Docs and import boundary.**
  Deliverable: `docs/adaptive-runtime-continuation.md` describing the three schema versions, the `N..N+4` / `N+5..N+12` window, local-bar placement, `audible` versus the exploration loop, idempotent maintain, the three fallbacks, the deadline checks, HTTP, logging redaction, and the rule that playback does not await generation. Link it from `docs/adaptive-score.md` and `README.md`. In `AGENTS.md`, extend the adaptive overview sentence and add Key Entry Points rows for the continuation schemas, the pure planner, and the continuation service. Extend the `ai_agents/` forbidden-import sentence in `.ai-factory/ARCHITECTURE.md` and the matching sentence in `docs/adaptive-score.md` with `adaptive_runtime_continuation_schemas`, `adaptive_runtime_continuation_settings`, `adaptive_runtime_continuation`, `adaptive_runtime_continuation_fallback`, `adaptive_runtime_continuation_service`, and `adaptive_runtime_continuation_runtime`. After the edit, confirm each of those module names is present in that ARCHITECTURE sentence and in `docs/adaptive-score.md`. Do not edit `ROADMAP.md`.
  LOG REQUIREMENTS: The logging section lists the INFO fields and the forbidden payload fields from decision 11.
  Depends on Tasks 6 and 7.
  Files: `docs/adaptive-runtime-continuation.md`, `docs/adaptive-score.md`, `README.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`
  <!-- Commit checkpoint: tasks 7–8 -->
