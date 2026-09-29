# Implementation Plan: V5 Runtime Musical Context

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-29

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra (post `/aif-improve` 2026-09-29)
- UI: yes (readout and sample buttons only; the server owns smoothing)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request for a versioned schema, mapping rules, hysteresis, configurable thresholds, and deterministic tests
- Scope: a normalized runtime musical-context model. An external application or game posts a flat sample. Mapping rules project that sample onto closed musical fields. Smoothing and hysteresis then emit existing adaptive playback commands. The stored graph stays `adaptive.score.v1`. Playback stays `adaptive.playback.runtime.v1`. There is no `composition.v5` and no `adaptive.score.v2`. The context session does not copy note events and does not call an LLM.

## Roadmap Linkage
Milestone: "V5 Runtime musical context"
Rationale: The adaptive score graph, authoring, transition scheduler, intensity layers, and playback clock are already checked on the roadmap. This plan is the external stream that drives that clock without a game-specific schema. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

An external context stream can drive stable musical state and intensity changes.

Ship:

1. Versioned, validated documents for the external sample, the mapping rules, and the normalized musical context.
2. A pure step that binds external scalars, smooths noisy numbers, and holds musical state across a hysteresis band and a sample dwell.
3. An in-memory session that, when playback is already running, emits `set_intensity`, `set_flags`, and `request_state` on that existing clock.
4. An Adaptive-tab readout that posts the locked example and shows the held state. It does not select the authoring card and it does not move Tone by itself.
5. Deterministic tests for the flap stream: danger values that keep crossing 0.5 never alternate Exploration and Combat.

Acceptance: with the locked mapping (danger enter `0.65`, exit `0.35`, dwell `3`, baseline `state-exploration`, target `state-combat`), twenty samples that alternate `0.49` and `0.51` leave `musical_state_id` on `state-exploration` and emit zero `request_state` commands. Three samples at `0.7` then move the held state to `state-combat` and, when playback is already running on the locked fixture, the playback clock follows that state without a score write. The same sample list returns the same snapshots.

```text
External app / Adaptive tab sample button
        ↓  flat scalars only
adaptive.context.external.v1
        ↓
services/adaptive_musical_context.py     (pure bind, EMA, Schmitt dwell)
        ↓
adaptive.musical_context.v1
        ↓  only when a playback session exists
existing playback commands
        set_intensity → set_flags → request_state
        ↓
adaptive.playback.runtime.v1
```

**Terminology lock:** Product generation is **V5**. **External sample** is `adaptive.context.external.v1`: a source label plus a flat map of scalars. **Mapping** is `adaptive.context.mapping.v1`: bindings and rules stored on the session, not on the score. **Normalized context** is `adaptive.musical_context.v1`: the closed musical view after mapping and smoothing. **Musical state** is `musical_state_id`, a state id on `adaptive.score.v1`. **Normalized state** is the categorical slot `state` (an application label such as `"combat"`). Those two strings are different fields. A binding may copy an external value into `state`. Only a rule may copy a normalized value into `musical_state_id`. **Baseline** is the state id used until a rule completes its dwell. **Held state** is the last musical state that finished dwell. **Danger** is a normalized `0..1` slot, not a game hit-point field. **Playback** is the existing clock. **Authoring current state** is still `adaptiveSelectedStateId`. This plan does not select variants.

Predecessors: `.ai-factory/plans/v5-adaptive-score-domain-model.md`, `.ai-factory/plans/v5-adaptive-score-authoring.md`, `.ai-factory/plans/v5-adaptive-score-transition-engine.md`, `.ai-factory/plans/v5-adaptive-score-intensity-layers.md`, and `.ai-factory/plans/v5-adaptive-score-playback-runtime.md`. Do not reopen their decisions (reference-only material, playback is process memory, invalid playback state requests leave transport playing, `set_intensity` and `set_flags` already exist, no note events, no `composition.v5`).

## Approach Evaluation (locked)

### Part A — Where the game schema ends

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Accept the game's nested document as the musical model** | One payload | The engine learns one application's fields and a second game needs a schema change | **Reject** |
| **B. JSON-path queries over nested objects** | Handles deep trees | A query language is a second schema, and path strings invite log leakage of scene data | **Reject** |
| **C. Flat scalar envelope plus explicit bindings into a closed normalized model** | A new game writes bindings, not engine code. Bindings use exact key equality | The caller flattens its own objects before the post | **Accepted** |

### Part B — Where mapping and samples live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Store the mapping inside `adaptive.score.v1`** | Survives restart | Bumps `document_revision`, opens the locked graph, and mixes game config with material references | **Reject** |
| **B. New SQLite table and Alembic revision** | Survives restart | The playback clock is already process memory. A migration is a wider change than this stream | **Reject** |
| **C. One in-memory session per `(project_id, score_id)`, same lifetime as playback** | Restart drops the mapping and the samples. The score row never changes | A second process does not see the session | **Accepted** |

### Part C — How a crossing becomes music

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. The context step calls `schedule_musical_transition` itself** | Direct | Duplicates queue, quantization, and the "invalid request stays playing" path | **Reject** |
| **B. Return advice and let the client call playback** | Thin server | Two clients can apply the same sample differently. The flap test would depend on the panel | **Reject** |
| **C. The pure step returns musical decisions. The service emits existing playback commands only when a playback session exists** | Bar alignment, queue limits, and transport continuity stay in the clock. A context session can run before Play and still produce a snapshot | Playback does not start by itself | **Accepted** |

### Part D — How 0.5 is prevented from flapping

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One threshold. `danger >= 0.5` selects Combat** | Simple | The example stream alternates Exploration and Combat | **Reject** |
| **B. Reuse `updateHarmonyBelief` from AI Jam** | Dwell already exists | That function is a live-MIDI belief machine. Importing it couples adaptive playback to the jam session | **Reject** |
| **C. Per-rule Schmitt band plus consecutive-sample dwell, then one winning state** | `0.49`/`0.51` sits inside `0.35..0.65` and never starts dwell. Wall clock is not involved | The enter and exit values must be configured per rule | **Accepted** |

### Part E — Intensity beside state

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Intensity is the same boolean as the state switch** | One output | Layers expect `0..1`, and a held state would freeze loudness | **Reject** |
| **B. Every numeric sample calls `set_intensity`** | Tracks the source | Noise around a stable value spams the clock | **Reject** |
| **C. EMA on the bound number, clamp to `0..1`, emit `set_intensity` only when the clamped value moves by at least `emit_epsilon`** | A real rise still changes layers. Chatter inside the epsilon does not | Alpha and epsilon are mapping fields | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Score states | `AdaptiveScoreV1.states[].id`, including the playback fixture ids `state-exploration` and `state-combat` | Mapping targets must be those ids. Context does not create states |
| Playback commands | `request_state`, `set_intensity`, `set_flags` on `AdaptivePlaybackCommand` | The service sends these. It does not add a playback `op` |
| Flags | `AdaptiveTransitionRuntimeV1.flags`: max 64, key `^[a-z][a-z0-9_]{0,40}$` | Flag rules emit this map and nothing wider |
| Invalid state | A well-formed `request_state` that names a missing state returns 200, warning `dangling_state_ref`, transport stays `playing` | Context start rejects a mapping whose baseline or target is missing before a session exists. A running session never sends a state id that was absent at start |
| Intensity layers | `map_adaptive_layers` uses the playback intensity | Context does not call the layer map. `set_intensity` already does |
| Registry pattern | `AdaptivePlaybackRegistry` keyed by `(project_id, score_id)`, process memory | Context registry is a sibling. It does not share the playback dict |
| HTTP shape | Project router, `enforce_current(..., "read")` for playback, 204 when no session | Context routes follow that shape |
| Schema style | `_Strict` (`extra="forbid"`), `_reject_bool`, `log_adaptive_schema_failure` without the body | Context models do the same |
| Logging | INFO ids, ticks, warning codes. No `body_json`, names, flag values, or notes | Context logs codes and musical ids. It does not log the sample body |
| Authoring UI | `adaptiveSelectedStateId` and the playback block are separate | Context panel is a third sibling |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| External envelope | Nothing accepts a flat scalar map without treating it as a score or a playback command |
| Normalized model | No versioned slots for tension, location, health, danger, tags, characters, or custom parameters |
| Mapping | No rules from an external key to those slots, or from those slots to a score state id |
| Anti-flap | Playback applies a state request on the next command. Nothing holds a noisy danger signal |
| Context session | Nothing remembers the previous smoothed value or dwell count |
| Apply path | Nothing turns a stable decision into `set_intensity` / `set_flags` / `request_state` |
| Readout | The Adaptive tab has no sample control that posts this stream |

### Coupling risks to avoid

1. Importing `app.ai_runtime`, `app.services.fake_llm`, LangChain, or any chat client from the context step or the context service.
2. Importing SQLite, `adaptive_score_store`, FastAPI, or `adaptive_playback.py` from `adaptive_musical_context.py`. The service may call `command_adaptive_playback`. The pure step may not.
3. Importing `liveHarmonyBelief`, `liveJamContracts`, or any jam module.
4. Writing `body_json`, `projects.composition_json`, the authoring pending slot, or the playback registry from a sample post.
5. Adding `adaptive.score.v2` or `composition.v5`.
6. Walking nested objects or evaluating a path expression against the sample.
7. Using wall-clock time, `observed_at_ms`, or `random` to decide state or intensity.
8. Logging the external document, narrative tags, location strings, character ids, custom categorical values, or raw numbers from the sample.
9. Calling `selectAdaptiveState`, `requestAdaptivePlaybackState`, or `setAdaptivePlaybackIntensity` from the context panel.
10. Auto-starting playback, stopping Tone, or selecting variants.
11. Letting `ai_agents/` import the new context modules. Agents that need a snapshot use HTTP.
12. Editing `ROADMAP.md`.
13. Changing `schedule_musical_transition`, `map_adaptive_layers`, playback command payloads, or quantization tokens.
14. Adding a `SpanKind` value. Context telemetry is structured logs plus counters on the snapshot.

## Scope And Decisions

### In scope
- Three strict documents, caps, the pure step, one in-memory session, four HTTP routes, playback command emission, the locked flap tests, a readout on the Adaptive tab, and the doc updates in Task 8.

### Out of scope
- A game client, a Unity or Unreal package, or a nested application schema.
- Persisting samples, mappings, or smoothed values in SQLite. No new Alembic revision.
- Starting, stopping, or seeking playback. Creating score states. Editing transitions.
- Variant selection, note generation, and LLM calls.
- Reusing AI Jam hysteresis.
- A mapping-rule authoring form. The panel posts one fixed example mapping.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("read")
services/adaptive_musical_context_service.py
        ↓  read score state ids once at session start; no write
services/adaptive_musical_context.py          (pure)
        ↓  when playback is already running
services/adaptive_playback_service.command_adaptive_playback
```

`step_musical_context` accepts the mapping, the previous context clock, and one external sample. It does not accept a score, a composition, tracks, or events. `ai_agents/` does not import these modules. `adaptive_musical_context.py` does not import `adaptive_playback.py`, `adaptive_playback_service.py`, or `adaptive_playback_runtime.py`.

**2. External sample**

`AdaptiveContextExternalV1` is `extra="forbid"`. `schema_version` is `adaptive.context.external.v1`. Any other version is HTTP 422 `unsupported_schema_version` and does not modify the session. The context module owns that message: `schema_version must be adaptive.context.external.v1.` The mapping and snapshot parsers use the same code with `adaptive.context.mapping.v1` and `adaptive.musical_context.v1` in their own messages. Do not reuse the score message that names `adaptive.score.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | Required literal |
| `source_id` | Optional string, length 1..64. Not interpreted. Not logged |
| `observed_at_ms` | Optional int `>= 0`, not a boolean. Stored nowhere that affects the step. The step ignores it |
| `values` | Object, max 64 keys. Key matches `^[A-Za-z][A-Za-z0-9_.]{0,63}$`. Value is a number that is not a boolean, a boolean, a string of length 1..80, or a list of 1..32 such strings |

A nested object, null, or a list of numbers fails validation. The HTTP status is 422 `adaptive_score_invalid` with `details.field` set. The session is unchanged. Keys inside `values` that also appear in `FORBIDDEN_NOTE_KEYS` (`events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`) fail with `embedded_note_material` before the step runs.

Absent keys do not clear a normalized slot. A later sample updates only the slots whose bindings name a present key.

**3. Normalized snapshot**

`AdaptiveMusicalContextV1` is `extra="forbid"`. `schema_version` is `adaptive.musical_context.v1`. It is not stored in `body_json`.

| Field | Rule |
|-------|------|
| `context_id` | `^actx_[0-9a-f]{8}$`, minted once at session start |
| `sample_index` | Int `>= 0`. Zero before any accepted sample. Increments by 1 per accepted sample |
| `state` | String token or null. Application label. Pattern `^[a-z][a-z0-9_]{0,40}$` |
| `intensity` | Float `0..1` or null. Not a boolean |
| `tension` | Float `0..1` or null |
| `location` | String token or null, same pattern as `state` |
| `health` | Float `0..1` or null |
| `danger` | Float `0..1` or null |
| `narrative_tags` | List of tokens, max 32, unique, stored in sorted order |
| `characters` | List of `{id, present}` max 32, ids unique, sorted by id. `id` uses the flag pattern |
| `custom_numeric` | Map, max 32, flag-pattern keys, float values (not forced into `0..1`) |
| `custom_boolean` | Map, max 32, flag-pattern keys, bool values |
| `custom_categorical` | Map, max 32, flag-pattern keys, token values |
| `musical_state_id` | Score state id. Pattern `_ID_RE` from `adaptive_score_schemas.py` (`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`), so `state-exploration` is legal. Starts as the mapping baseline |
| `emitted` | Max 3 items, ops only from `set_intensity`, `set_flags`, `request_state`, in that order. Intensity item carries the clamped float. Flags item carries the bool map. State item carries `to_state_id` |
| `dwell_rule_id` | Rule id or null |
| `dwell_count` | Int `>= 0` |
| `warnings` | `AdaptiveTransitionScheduleWarningV1` shape, max 8. Context codes are `context_value_rejected`, `playback_not_running`, and `document_revision_conflict`. Playback command codes are copied through. First-seen order, then truncate |
| `telemetry` | `sample_count`, `state_change_count`, `intensity_emit_count`, `rejected_sample_count` |
| `document_revision` | Echo of the score read at session start |

The public numbers on `danger`, `health`, `tension`, and `intensity` are the smoothed clamped values. Raw sample numbers are not on this document. `emitted` describes what the service attempted. The pure step fills `emitted` from the decision. `step_adaptive_playback` clears `clock.warnings` at the start of every command, so the service unions warning codes from each `command_adaptive_playback` result in call order, keeps the first sighting of each code, then appends context warnings the same way, and keeps at most 8. It does not clear `musical_state_id` if playback queues the request or rejects it. Held musical state and playback `runtime_state_id` can differ until the clock commits. For the locked fixture they match on the sample response: quantization `immediate` sets the boundary to the current tick, and `_consume_due` commits it inside that `request_state`.

**4. Mapping**

`AdaptiveContextMappingV1` is `extra="forbid"`. `schema_version` is `adaptive.context.mapping.v1`.

| Field | Rule |
|-------|------|
| `baseline_state_id` | Required score state id. Same `_ID_RE` as `musical_state_id`. Hyphens are legal |
| `bindings` | Max 32. Discriminator `kind` |
| `state_rules` | Max 32. Discriminator `kind` |
| `intensity` | Optional. One source |
| `flag_rules` | Max 32 |

Binding kinds:

| `kind` | Fields | Effect |
|--------|--------|--------|
| `numeric` | `id`, `external_key`, `slot` (`tension` \| `health` \| `danger` \| `intensity` \| `custom_numeric`), optional `custom_key`, `transform` (`identity` \| `invert` \| `clamp01` \| `scale`), optional `in_min` / `in_max` when transform is `scale`, `smooth_alpha` default `1` | Writes one numeric slot. `custom_numeric` requires `custom_key` |
| `boolean` | `id`, `external_key`, `slot` (`custom_boolean` \| `character`), optional `character_id` | `character` requires `character_id` and sets `present` |
| `categorical` | `id`, `external_key`, `slot` (`state` \| `location` \| `custom_categorical`), optional `custom_key`, optional `allowed` list max 16 | A present string outside `allowed` produces warning `context_value_rejected` and holds the previous slot |
| `tags` | `id`, `external_key` | Replaces `narrative_tags` only when every string is a `_FLAG_RE` token. One bad string adds `context_value_rejected` and holds the previous list |

Rule ids and binding ids match `^[a-z][a-z0-9_]{0,40}$` and are unique within the mapping. Every `target_state_id` uses `_ID_RE`. Categorical slots, tags, character ids, and playback flags stay on `_FLAG_RE`. Two bindings that write the same slot fail start with HTTP 422 `adaptive_score_invalid` and create no session. The same slot includes two `custom_numeric`, `custom_boolean`, or `custom_categorical` bindings that share a `custom_key`. Two bindings that read the same `external_key` into different slots remain legal. `AdaptiveContextStartRequest` is the start body: `expected_document_revision` plus `mapping`, `extra="forbid"`.

Numeric transform, applied to a finite number that is not a boolean:

| Transform | Result before smoothing |
|-----------|-------------------------|
| `identity` | The number |
| `clamp01` | Clamped to `0..1` |
| `invert` | `1 - clamp01(number)` |
| `scale` | `(number - in_min) / (in_max - in_min)`, then clamped to `0..1`. `in_max` must be greater than `in_min` |

`smooth_alpha` is a float in `(0, 1]`, not a boolean. The smoother blends the transformed number, then clamps. For `tension`, `health`, `danger`, and `intensity`, the value stored for the next sample is the clamped result. The first accepted number is stored after that clamp, so a seed of `1.4` stores `1.0` and the next sample blends against `1.0`. `custom_numeric` stores the unclamped blend. A later sample uses `alpha * sample + (1 - alpha) * previous`.

A JSON string, boolean, nested value, or non-finite number on a numeric binding adds warning `context_value_rejected` and holds that slot. The step does not call `float()` on a string. Other bindings on the same sample still run.

State rule kinds, all reading normalized slots only:

| `kind` | Fields | Enter claim / release |
|--------|--------|------------------------|
| `numeric_band` | `slot` (`tension` \| `health` \| `danger` \| `intensity` or `custom_numeric` plus `custom_key`), `polarity` (`high` \| `low`), `enter`, `exit`, `min_dwell_samples`, `target_state_id`, `priority` | High enter claim: value `>= enter`. High release claim: value `<= exit`. Low enter claim: value `<= enter`. Low release claim: value `>= exit`. A null slot is neither claim |
| `category_equals` | `slot` (`state` \| `location` or `custom_categorical` plus `custom_key`), `equals`, dwell, target, priority | Enter claim: slot equals the token. Release claim: it does not |
| `tag_present` | `tag`, dwell, target, priority | Enter claim: tag is in `narrative_tags`. Release claim: it is not |
| `character_present` | `character_id`, dwell, target, priority | Enter claim: that character is `present: true`. Release claim: it is not |

`priority` is an int `0..100`. `min_dwell_samples` defaults to the env default when omitted, and must sit in `1..max_dwell`.

Hysteresis gap, high polarity: `enter >= exit + min_gap`. Low polarity: `exit >= enter + min_gap`. `enter` and `exit` are finite floats, not booleans. For slots that are clamped to `0..1`, both ends must lie in `0..1`. A mapping that fails the gap is HTTP 422 `hysteresis_gap` at session start. No session is created. A single threshold of `0.5` cannot be expressed with the default gap.

Dwell uses two counters per rule, and only one of them runs:

- When `musical_state_id` is not the rule's target, an enter claim increments `enter_count` and sets `exit_count` to 0. A sample with no enter claim sets `enter_count` to 0.
- When `musical_state_id` is the rule's target, a release claim increments `exit_count` and sets `enter_count` to 0. A sample with no release claim sets `exit_count` to 0.

A danger value inside the open band is neither claim, so the active counter goes to 0. Counts cap at the rule's dwell. Stream B sample 1 (`0.2` while held on Exploration) leaves dwell at 0 because the combat rule does not own that state.

A rule becomes an enter candidate when `musical_state_id` is not its target and `enter_count` reaches its dwell. The winner is the enter candidate with the highest priority, then the lexicographic rule id. The held state becomes that target, and every rule counter resets to 0.

The rule that owns the current `musical_state_id` releases when `exit_count` reaches its dwell. If another rule is already an enter candidate on that same sample, the winner replaces it. Otherwise the held state returns to `baseline_state_id`. A value inside the open band does not enter and does not release. The step does not invent a target from an external key named `state`.

The snapshot's `dwell_rule_id` and `dwell_count` report the counter that advanced on this sample: the winning enter rule, or the owning rule while it is releasing. On the sample that changes `musical_state_id`, `dwell_count` is the completed dwell and `dwell_rule_id` is the rule that entered or released. Internal counters are then 0 for the next sample. Both fields are null and 0 when the sample sits in the open band.

Intensity source, when present: `{slot, custom_key or null, emit_epsilon}`. `emit_epsilon` default `0.02`, range `0..1`, not a boolean. After the numeric update, if the source slot is non-null and `abs(new - last_emitted) >= emit_epsilon`, or nothing has been emitted yet, `emitted` includes `set_intensity`. The first emitted intensity is the current clamped slot value even when it equals 0. A later change smaller than epsilon does not emit and does not change `last_emitted`.

Flag rules: `{id, source: custom_boolean \| character \| tag, source_key, flag}`. `flag` uses the playback flag pattern. The emitted map contains one entry per flag rule whose source is true on this sample. `set_flags` is emitted only when the map differs from the last emitted map. An empty map is a legal emission when the previous map was non-empty.

Command order inside `emitted` is fixed: `set_intensity`, then `set_flags`, then `request_state`. `request_state` is present only when `musical_state_id` changed on this sample. `transition_id` is always omitted so the playback clock chooses the eligible transition.

**5. Session**

`AdaptiveMusicalContextRegistry` is keyed by `(project_id, score_id)`. One session. `put` replaces the previous session for that pair. Another score keeps its own session. Process memory only. `reset_default_context_registry()` replaces the process map and is for tests.

Session start reads the score once through `get_score` in `backend/app/services/adaptive_score_store.py`, the same call playback uses. It checks `expected_document_revision`, and checks that `baseline_state_id` and every rule `target_state_id` exist on that score. A revision mismatch is HTTP 409 `adaptive_score_conflict` and creates no session. A missing state id is HTTP 422 `dangling_state_ref` and creates no session. Do not add a second score query.

A later sample, when the stored score revision differs, returns HTTP 200 with warning `document_revision_conflict`, the previous snapshot, and `rejected_sample_count` increased by 1. It does not step and it does not emit commands.

GET with no session is HTTP 204 and an empty body. DELETE of a session clears it and returns 204. DELETE when nothing is running returns 204. POST samples with no session is HTTP 404 `context_not_running`.

Start does not create a playback session. A sample while playback is absent still steps the context clock, leaves `emitted` as the decisions, and adds warning `playback_not_running`. `command_adaptive_playback` is not called.

When playback is running, the service calls `command_adaptive_playback` once per emitted item, in order. Warning codes are unioned as decision 3 describes. Transport is not stopped. Context does not send `stop`, `advance`, or `observe`.

**6. Threshold defaults**

New module `backend/app/adaptive_musical_context_settings.py`. `load_adaptive_musical_context_settings()` reads integers and one float gap. A non-numeric or out-of-range value logs WARNING `extra={"setting", "invalid": True}` and keeps the default. One DEBUG log per process lists the resolved numbers. An explicit `env` dict skips that once-log.

| Env | Default | Allowed |
|-----|---------|---------|
| `ADAPTIVE_CONTEXT_MAX_BINDINGS` | 32 | 1..128 |
| `ADAPTIVE_CONTEXT_MAX_RULES` | 32 | 1..128 |
| `ADAPTIVE_CONTEXT_MAX_VALUES` | 64 | 1..256 |
| `ADAPTIVE_CONTEXT_MAX_TAGS` | 32 | 1..64 |
| `ADAPTIVE_CONTEXT_MAX_CHARACTERS` | 32 | 1..64 |
| `ADAPTIVE_CONTEXT_MAX_CUSTOM` | 32 | 1..64 |
| `ADAPTIVE_CONTEXT_DEFAULT_DWELL` | 3 | 1..64 |
| `ADAPTIVE_CONTEXT_MAX_DWELL` | 64 | 1..256, and `>=` default dwell |
| `ADAPTIVE_CONTEXT_MIN_HYSTERESIS_GAP` | 0.05 | 0.0..0.5 |

Thresholds used for Combat versus Exploration live on the mapping, not in these env vars. The env gap only rejects a band that is too narrow.

**7. Locked mapping and streams**

These numbers are the acceptance fixture. Tests and the panel example must use them unchanged.

```text
baseline_state_id: state-exploration
binding bind-danger:
  kind numeric
  external_key danger
  slot danger
  transform identity
  smooth_alpha 1
state rule rule-combat:
  kind numeric_band
  slot danger
  polarity high
  enter 0.65
  exit 0.35
  min_dwell_samples 3
  target_state_id state-combat
  priority 10
intensity:
  slot danger
  emit_epsilon 0.02
```

Stream A, twenty samples, danger alternating `0.49`, `0.51`, `0.49`, `0.51`, ... :

- `musical_state_id` is `state-exploration` on every snapshot
- `state_change_count` is 0
- `request_state` never appears in `emitted`
- `dwell_count` stays 0
- Intensity tracks the sample because alpha is 1 and the step is `0.02`, so `set_intensity` may appear. That is not a state flap

Stream B, danger `0.2`, `0.7`, `0.7`, `0.7`:

- After sample 1: Exploration, intensity `0.2`, dwell 0
- After sample 2: Exploration, dwell_count 1, rule `rule-combat`
- After sample 3: Exploration, dwell_count 2
- After sample 4: `state-combat`, `dwell_count` 3, `dwell_rule_id` `rule-combat`, `state_change_count` 1, emitted contains `request_state` with `to_state_id` `state-combat` and `set_intensity` `0.7`

Stream C, continuing from Combat, danger `0.4`, then `0.3`, `0.3`, `0.3`:

- `0.4` stays Combat because `0.4 > 0.35`
- The third `0.3` returns to `state-exploration`

Stream D: running stream A twice from a fresh session yields the same `sample_index`, danger, musical state, dwell, emitted ops, and telemetry. `context_id` is equal within one session and is the only field allowed to differ across two fresh sessions.

Stream E, category. Binding `kind categorical`, `external_key` `mode`, slot `state`, `allowed` `["explore", "combat"]`. Rule `rule-mode`: `category_equals`, slot `state`, `equals` `combat`, dwell 2, target `state-combat`, priority 5. One sample `mode: "combat"` stays on Exploration with `dwell_count` 1. The second sample switches to `state-combat`.

Stream F, tag. Binding `kind tags`, `external_key` `tags`. Rule `rule-boss`: `tag_present`, tag `boss`, dwell 1, target `state-combat`, priority 5. Sample `["boss"]` switches on that sample. A later sample `["explore", "Bad"]` adds `context_value_rejected` and leaves `narrative_tags` at `["boss"]`.

Stream G, character. Binding `kind boolean`, slot `character`, `external_key` `aria_present`, `character_id` `aria`. Rule `rule-aria`: `character_present`, `character_id` `aria`, dwell 1, target `state-combat`, priority 5. Sample `true` switches. Sample `false` releases on that sample and returns to `state-exploration`.

Stream H, flag. Same character binding as stream G. Flag rule `flag-aria`: source `character`, `source_key` `aria`, flag `aria_here`. The `true` sample emits `set_flags` `{aria_here: true}`. The `false` sample emits `set_flags` `{}` because the previous map was non-empty.

Stream I, low polarity. Binding `kind numeric`, `external_key` `hp`, slot `health`, transform `identity`, `smooth_alpha` 1. Rule `rule-hurt`: `numeric_band`, slot `health`, polarity `low`, enter `0.25`, exit `0.40`, dwell 2, target `state-combat`, priority 5. Health `0.50` stays on Exploration with dwell 0. Health `0.20`, `0.20` switches on the second `0.20`. Health `0.30` stays on Combat because `0.30 < 0.40`. Health `0.50`, `0.50` returns to Exploration on the second `0.50`.

Stream J, EMA. Binding `kind numeric`, `external_key` `danger`, slot `danger`, transform `identity`, `smooth_alpha` `0.5`. No state rules. Sample `0` stores danger `0`. Sample `1` stores `0.5`. A separate session whose first sample is `1.4` stores `1.0`. The next sample `0` stores `0.5`, which is `0.5 * 0 + 0.5 * 1.0`. A sample whose danger value is the JSON string `"0.7"` adds `context_value_rejected` and leaves danger unchanged.

Score fixture for the HTTP test, constant tempo 120, `4/4`, 480 ticks per quarter:

| State id | Material | Transition ids |
|----------|----------|----------------|
| `state-exploration` | bars 1–4 | `tr-to-combat` |
| `state-combat` | bars 5–8 | `tr-to-exploration` |

Both transitions use quantization `immediate`, empty conditions, priority 0, and realization `cut`. They are listed on the source state's `transition_ids`. Playback mode is `simulation`. The HTTP test starts playback first, then the context session, then posts stream A and stream B. After stream A, playback `runtime_state_id` is still `state-exploration`. The fourth sample of stream B returns `musical_state_id` `state-combat` on the context snapshot. A playback GET after that sample returns `runtime_state_id` `state-combat`. No extra `advance` is required. `projects.composition_json`, `body_json`, and `document_revision` are unchanged. An unknown `to_state_id` is not posted by this test; a separate unit post uses a mapping whose target is missing and expects 422 with no session.

Direct-coupling unit: external `values.state` equal to `state-combat`, with no binding and no rule, leaves `musical_state_id` on the baseline.

Ignored-key unit: a sample that also contains `quest_step: 4` and no binding for that key steps the danger rule normally. `ignored_key_count` is available to DEBUG logs and is not a snapshot field.

**8. Frontend**

Add `adaptiveMusicalContext: null` and `adaptiveMusicalContextError: ''` to `initialAdaptiveScoreState` in `frontend/src/store/musicStore.js`. Project open and the other spreads of that object clear them. When `currentProjectId` changes, the playback subscribe already DELETEs the previous playback session. That same change DELETEs `/{score_id}/context` for the previous project and score. A missing context session stays quiet, matching playback.

Actions: `startAdaptiveMusicalContext`, `stopAdaptiveMusicalContext`, `sendAdaptiveContextSample`, `sendAdaptiveContextSeries`. They do not call `selectAdaptiveState`, `requestAdaptivePlaybackState`, `setAdaptivePlaybackIntensity`, or any Tone method.

`frontend/src/utils/adaptiveMusicalContext.js` exports the locked example mapping and streams A and B as data. It does not implement dwell math.

`AdaptiveMusicalContextPanel.jsx` is rendered under the playback block in `AdaptiveScorePanel.jsx`. Buttons:

- Arm example: POST the locked mapping
- Flap: POST stream A in order and show the last snapshot
- Sustain: POST stream B in order

The panel shows `musical_state_id`, `intensity`, `dwell_count`, and warning codes from the server snapshot. It does not show the raw `values` object after the response returns.

**9. Telemetry**

Each successful start, sample, and stop logs one INFO line with `adaptive_musical_context: true`, `project_id`, `score_id`, `context_id`, `sample_index`, `musical_state_id`, `state_changed`, `emitted_ops` (the op names), `warning_codes`, and the four telemetry counters. DEBUG may add `dwell_rule_id`, `dwell_count`, `ignored_key_count`, `binding_count`, and the smoothed `danger`, `health`, `tension`, and `intensity` numbers. ERROR logs `code` only.

Never log `source_id`, `observed_at_ms`, the `values` object, external keys, narrative tags, location, character ids, custom categorical strings, raw unclamped inputs, `body_json`, or note data.

The snapshot `telemetry` object is what HTTP tests assert. The log line is what `caplog` asserts. No new span kind.

**10. HTTP**

Under the existing project adaptive-score router, `enforce_current(..., "read")`:

| Method | Path | Result |
|--------|------|--------|
| `POST` | `/{score_id}/context` | Start. Body: `expected_document_revision` and `mapping`. Response `adaptive.musical_context.v1` with `sample_index` 0 |
| `GET` | `/{score_id}/context` | Current snapshot, or 204 |
| `POST` | `/{score_id}/context/samples` | One external sample. Response snapshot |
| `DELETE` | `/{score_id}/context` | Clear. 204 |

## Commit Plan
- **Commit 1** (after tasks 1–2): `feat: add versioned adaptive musical context schemas`
- **Commit 2** (after tasks 3–4): `feat: hold musical state across a danger hysteresis band`
- **Commit 3** (after tasks 5–6): `feat: drive adaptive playback from a context sample stream`
- **Commit 4** (after tasks 7–8): `feat: show musical context readout on the Adaptive tab`

## Tasks

### Phase 1: Contracts
- [x] **Task 1: Versioned context schemas and caps.**
  Deliverable: `backend/app/adaptive_musical_context_schemas.py` and `backend/app/adaptive_musical_context_settings.py`. Implement decisions 2, 3, 4, and 6: external sample, `AdaptiveContextStartRequest`, mapping, normalized snapshot, parsers that raise `AdaptiveScoreError` without logging the body, bool rejection on numeric fields, `FORBIDDEN_NOTE_KEYS` on sample keys, hysteresis gap check, duplicate-slot rejection, and env caps. Import `_ID_RE` and `_FLAG_RE` from `adaptive_score_schemas.py`. `baseline_state_id`, `target_state_id`, and `musical_state_id` use `_ID_RE`. Categorical slots, tags, character ids, and flags use `_FLAG_RE`. Reuse `log_adaptive_schema_failure`, `_reject_bool`, and `map_adaptive_score_error_to_http`. Do not import playback, SQLite, FastAPI, or jam modules. Context error messages for `unsupported_schema_version` name `adaptive.context.external.v1`, `adaptive.context.mapping.v1`, or `adaptive.musical_context.v1`. Add codes `hysteresis_gap`, `context_not_running`, and warning code `context_value_rejected`. `context_not_running` uses HTTP 404. `hysteresis_gap` and a duplicate slot use HTTP 422.
  LOG REQUIREMENTS: DEBUG on schema rejection with `model`, `field`, and `code` only. WARNING when an env value is invalid, with `setting` and `invalid: true`, then keep the default. One DEBUG line for resolved caps when env is the process environment. Never log sample bodies, binding external keys, tag strings, or character ids.
  Files: `backend/app/adaptive_musical_context_schemas.py`, `backend/app/adaptive_musical_context_settings.py`
- [x] **Task 2: Schema tests.**
  Deliverable: `backend/tests/test_adaptive_musical_context_schema.py`. Cover unsupported `schema_version` with the three context messages (none of them may say `adaptive.score.v1`), extra fields, boolean-as-number, nested `values`, note-like keys, a band with enter `0.5` and exit `0.5` rejected as `hysteresis_gap`, a legal `0.65`/`0.35` band whose target is `state-combat`, two `danger` bindings rejected as `adaptive_score_invalid` with no session object left behind, tag and character caps, and settings fallback when env is not an integer. No database.
  LOG REQUIREMENTS: Assert the schema DEBUG record has no document body and no raw danger value. Assert the settings WARNING record names the setting and not a sample.
  Depends on Task 1.
  Files: `backend/tests/test_adaptive_musical_context_schema.py`

### Phase 2: Pure step
- [x] **Task 3: Bind, smooth, and hold state.**
  Deliverable: `backend/app/services/adaptive_musical_context.py` with `begin_musical_context` and `step_musical_context`. Implement decisions 4 and 7's decision math: exact-key bindings, ignored unbound keys, post-clamp EMA for `tension`/`health`/`danger`/`intensity`, unclamped EMA for `custom_numeric`, no `float()` coercion of strings, all-or-nothing tags, Schmitt polarity, per-rule dwell, winner by priority then id, intensity epsilon, flag diff, and command order. Same inputs return the same clock. No wall clock. The module must not import playback, SQLite, FastAPI, LLM, or jam code.
  LOG REQUIREMENTS: The pure step logs DEBUG with `dwell_rule_id`, `dwell_count`, `ignored_key_count`, and smoothed `danger`/`health`/`tension`/`intensity` only. Do not log INFO from the pure step (the service owns INFO). Do not log external keys, tags, location, character ids, or the sample dict.
  Depends on Task 1.
  Files: `backend/app/services/adaptive_musical_context.py`
- [x] **Task 4: Deterministic stream tests.**
  Deliverable: `backend/tests/test_adaptive_musical_context.py`. Streams A through J from decision 7, plus the unbound `values.state` case and the ignored `quest_step` case. Assert deep equality of the decision fields. Assert stream A emits no `request_state`. Assert stream J's second step is exactly `0.5` and the `1.4` seed's follow-up is `0.5`, not a blend against `1.4`. Assert the JSON string `"0.7"` does not change danger. No database and no playback import in this test module's target.
  LOG REQUIREMENTS: `caplog` at DEBUG shows smoothed danger for stream B and does not contain the string `quest_step` or a narrative tag.
  Depends on Task 3.
  Files: `backend/tests/test_adaptive_musical_context.py`
  <!-- Commit checkpoint: tasks 1–4 -->

### Phase 3: Session and playback
- [x] **Task 5: In-memory session that emits playback commands.**
  Deliverable: `backend/app/services/adaptive_musical_context_runtime.py` and `backend/app/services/adaptive_musical_context_service.py`. Implement decision 5. Start calls `get_score` from `adaptive_score_store.py` and validates revision and state ids. Samples step the pure clock. When `get` on the playback registry shows a running session, call `command_adaptive_playback` for each emitted item in order. Union warning codes in first-seen order and cap the list at 8. Never call `put` on the playback registry and never write the score. A second context start replaces only that score's context session. The runtime test sends `set_intensity` and `request_state` in one sample and asserts both warning codes survive when the playback command results carry different codes.
  LOG REQUIREMENTS: INFO on start, sample, and stop using decision 9's field list. DEBUG may add dwell fields. ERROR logs `code` only. `caplog` in the runtime test must not contain `body_json` or external key names.
  Depends on Task 3.
  Files: `backend/app/services/adaptive_musical_context_runtime.py`, `backend/app/services/adaptive_musical_context_service.py`, `backend/tests/test_adaptive_musical_context_runtime.py`
- [x] **Task 6: HTTP routes and score-stability tests.**
  Deliverable: four routes on `backend/app/routers/adaptive_scores.py` from decision 10. Handlers parse, call the service, and map `AdaptiveScoreError`. Permission `read`. Integration test in `backend/tests/test_adaptive_musical_context_api.py` calls `reset_default_playback_registry()` and `reset_default_context_registry()` before and after the module. It builds the decision 7 score fixture, starts simulation playback, posts stream A and stream B, and asserts playback `runtime_state_id` from the playback GET after the fourth stream B sample, with no extra `advance`. `document_revision` and the composition stay unchanged. Also assert 422 for a missing target with no session, 404 `context_not_running` with no session, 204 DELETE, and 200 warning `playback_not_running` when playback was not started. A mapping with a missing target must not call playback.
  LOG REQUIREMENTS: One INFO record per sample with `adaptive_musical_context: true`, `musical_state_id`, and `emitted_ops`. The record must not include the sample JSON.
  Depends on Task 5.
  Files: `backend/app/routers/adaptive_scores.py`, `backend/tests/test_adaptive_musical_context_api.py`
  <!-- Commit checkpoint: tasks 5–6 -->

### Phase 4: Readout and docs
- [x] **Task 7: Adaptive tab context readout.**
  Deliverable: decision 8. Extend `frontend/src/api/adaptiveScoreApi.js`, add store actions that only talk to the context routes, add `frontend/src/utils/adaptiveMusicalContext.js` with the locked mapping and streams A and B, and render `frontend/src/components/AdaptiveMusicalContextPanel.jsx` from `AdaptiveScorePanel.jsx`. Buttons arm the example, post the flap series, and post the sustain series. The visible state comes from the response. Add the two fields to `initialAdaptiveScoreState`. On the project-id change that already DELETEs playback, DELETE the previous context route as well.
  LOG REQUIREMENTS: No `console.log` of sample values. Failed posts set `adaptiveMusicalContextError` to the server `code` string.
  Tests: `frontend/src/utils/adaptiveMusicalContext.test.js` asserts the example enter/exit/dwell numbers and that the helper module does not export a step function. Extend `frontend/src/store/musicStore.adaptiveScore.test.js` so a context sample action does not call `selectAdaptiveState` and does not POST `/playback/commands` itself.
  Depends on Task 6.
  Files: `frontend/src/api/adaptiveScoreApi.js`, `frontend/src/store/musicStore.js`, `frontend/src/utils/adaptiveMusicalContext.js`, `frontend/src/utils/adaptiveMusicalContext.test.js`, `frontend/src/components/AdaptiveMusicalContextPanel.jsx`, `frontend/src/components/AdaptiveScorePanel.jsx`, `frontend/src/store/musicStore.adaptiveScore.test.js`
- [x] **Task 8: Docs and import boundary.**
  Deliverable: `docs/adaptive-musical-context.md` describing the three schema versions, the flat-sample rule, the locked flap numbers, HTTP, logging redaction, and non-goals. Link it from `docs/adaptive-score.md` and `README.md`. In `AGENTS.md`, extend the adaptive overview sentence and add a Key Entry Points row for `adaptive_musical_context_schemas.py`, `services/adaptive_musical_context.py`, and `services/adaptive_musical_context_service.py`. Extend the `ai_agents/` forbidden-import sentence in `.ai-factory/ARCHITECTURE.md` and the matching sentence in `docs/adaptive-score.md` with `adaptive_musical_context_schemas`, `adaptive_musical_context_settings`, `adaptive_musical_context`, `adaptive_musical_context_service`, and `adaptive_musical_context_runtime`. Do not edit `ROADMAP.md`. Do not document a nested game schema.
  LOG REQUIREMENTS: The logging section must list the INFO fields and the forbidden payload fields from decision 9. No sample of a real external document belongs in the doc beyond the numeric danger fixture.
  Depends on Tasks 6 and 7.
  Files: `docs/adaptive-musical-context.md`, `docs/adaptive-score.md`, `README.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`
  <!-- Commit checkpoint: tasks 7–8 -->
