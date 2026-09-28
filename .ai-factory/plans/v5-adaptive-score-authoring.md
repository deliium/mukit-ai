# Implementation Plan: V5 Adaptive Score Authoring and Validation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-28

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full (post `/aif-improve` 2026-09-28)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request to include tests and documentation
- Scope: authoring commands and stronger validation for an existing `adaptive.score.v1` document, plus a workspace graph editor. The playable schema stays `composition.v2`. There is no playback runtime and no `composition.v5`.

## Roadmap Linkage
Milestone: "V5 Adaptive Score authoring and validation"
Rationale: The completed milestone "V5 Adaptive Score domain model" stores the sidecar. This plan is the next V5 surface: step commands, actionable graph findings, and a graph editor. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A person, or an agent using the same HTTP API, can build and inspect an adaptive score without replacing the whole document by hand and without copying note events.

Ship:

1. One command endpoint that applies a closed set of graph edits and persists them with the existing compare-and-swap counter.
2. Validation findings a person can act on: dangling references, impossible transitions, invalid loop boundaries, missing fallback states, circular transitions that cannot make musical progress, and incompatible timing metadata.
3. A workspace Adaptive Score tab that draws states, transitions, the selected current state, and musical material references, and shows those findings.
4. Tests and documentation updates.

The acceptance score is three states — Exploration, Combat, Victory — with transitions Exploration → Combat → Victory, saved on the project. A second case shows a validation error with a message that names the entity and the broken rule. Playback that switches music is not this plan.

```text
ComposerWorkspace "Adaptive" tab
        ↓  commands + validate
POST /projects/{id}/adaptive-scores/{score_id}/commands
POST /projects/{id}/adaptive-scores/{score_id}/validate   (unchanged, read-only)
        ↓
services/adaptive_score_commands.py     (pure graph edit)
services/adaptive_score_validation.py   (pure findings)
services/adaptive_score_service.py      (bind, then existing store CAS)
        ↓
adaptive_scores.body_json               (adaptive.score.v1 only)
```

**Terminology lock:** Product generation is **V5**. The document schema stays **`adaptive.score.v1`**. Commands are edits of that document, not a new schema version. **Current state** in the UI is the selected authoring state, seeded from `initial_state_id`. It is not a playback cursor. A cycle such as Victory → Exploration remains valid. A **deadlock** is a reachable cycle whose every eligible exit is statically impossible and whose fallback cannot leave the cycle.

Predecessor: `.ai-factory/plans/v5-adaptive-score-domain-model.md`. Do not reopen its decisions (sidecar table, reference-only material, cycles allowed, warnings for unreachable states, no playback).

## Approach Evaluation (locked)

### Part A — Where edits live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Frontend mutates JSON and PUTs the whole document** | No new route | Agents and the UI can drift; a bad client can drop states; acceptance wants named backend operations | **Reject** |
| **B. One route per operation** | Obvious URLs | Nine handlers, nine CAS sequences, duplicated bind/log | **Reject** |
| **C. `POST .../commands` with a closed `op` and a typed payload** | One CAS path; agents and the UI share it; unknown ops fail closed | New DTO | **Accepted** |

Whole-document `PUT` stays. It remains the escape hatch for `initial_state_id`, variants, layers, and stingers. New commands do not replace `PUT`.

### Part B — What a deadlock is

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Any cycle is an error** | Easy | Contradicts the domain model: Victory may return to Exploration | **Reject** |
| **B. Ignore cycles** | Keeps the domain rule | Misses a cycle that can never advance | **Reject** |
| **C. Flag only a reachable cycle whose eligible edges are all statically impossible and whose fallback cannot leave the cycle** | Circulating scores still save; stuck scores get an error | Needs a written definition of "impossible" | **Accepted** |

### Part C — Graph canvas

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Add React Flow, dagre, or similar** | Pretty edges | New dependency; caps are 32 states | **Reject** |
| **B. SVG/HTML cards laid out by a pure function, no new package** | Matches current frontend deps; unit-testable | Edges are simple, not a polished node editor | **Accepted** |

### Part D — Agent access

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Import the store from `ai_agents/`** | Direct | Forbidden by the domain model and architecture | **Reject** |
| **B. HTTP commands are the agent surface; no new agent id** | Same API as the UI; `ai_agents/` stays free of SQLite | No in-process tool wrapper | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| `adaptive.score.v1` | States, transitions, layers, stingers, material refs, loop, entry/exit, intensity, fallback | Do not bump `schema_version`. Do not add note events |
| HTTP | `GET/POST/PUT/DELETE /projects/{id}/adaptive-scores` and `POST .../validate` | Command route sits beside these. Validate stays read-only |
| Store | `replace_score` CAS on `document_revision` | Commands call it. No new table and no Alembic revision |
| Graph checks | `dangling_state_ref`, `loop_bounds` (enabled `end_bar >= start_bar`), `transition_eligibility_mismatch`, `default_state_missing` (warning), `fallback_transition_missing`, `material_target_missing`, `state_unreachable` (warning) | Keep. New codes extend `validate_adaptive_score_graph` and binding. Do not weaken existing tests |
| Binding | Read-only composition or revision; asset refs unchecked | Commands bind before write, same as `PUT` |
| Collaboration | `read` vs `write_score` | Validate stays `read`. Commands use `write_score` |
| Frontend | No adaptive UI. Tabs live in `ComposerWorkspace.jsx`. HTTP clients are `musicApi.js` / `projectApi.js`. No graph library in `package.json` | New tab, new API module, no new npm dependency |
| Logging | INFO counts and ids; never `body_json` or pitches | Same redaction |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Named operations | Nothing creates one state or one transition without a full document replace |
| Impossible transition | No code for a condition or quantization the authored state can never satisfy |
| Deadlock cycle | No code. All cycles are currently silent, which is right for circulating music and wrong for an impossible loop |
| Missing named fallback | `default_state_missing` is only a warning. A policy that names `default_state` while `default_state_id` is missing is not yet an error. A reachable state with no outgoing edge is a valid ending, not a missing fallback |
| Loop vs material | `loop_bounds` does not check the loop against the state's bar span |
| Timing agreement | Entry/exit units and order are not checked against each other or the loop |
| Authoring UI | No tab, no current-state selection, no finding list |

### Coupling risks to avoid

1. Embedding `events`, `pitch`, or a nested composition in a command payload. The existing forbidden-key scan runs before accept.
2. Writing `projects.composition_json` or snapshot blobs from a command.
3. Importing `adaptive_score_store` or SQLite from `ai_agents/`.
4. Adding a playback scheduler, Tone.js state machine, or game-flag listener.
5. Treating a circulating cycle (empty or `manual` conditions) as `transition_deadlock`.
6. Logging command bodies, `body_json`, or note pitches at INFO.
7. Adding a graph-layout npm package.
8. A second coordinate system. Bars stay absolute composition bars.
9. Editing `ROADMAP.md` in this plan.

## Scope And Decisions

### In scope
- Closed command DTO and `POST /projects/{project_id}/adaptive-scores/{score_id}/commands`.
- Pure apply function for the operations listed below.
- New finding codes and messages (≤ 200 characters) that name the target id and the concrete values.
- Adaptive tab: state cards, transition list or edges, selected current state, material summary, finding list.
- Tests: graph rules, command apply, HTTP, frontend layout helper, Playwright journey.
- Doc updates listed in Task 9.

### Out of scope
- Runtime playback, stinger scheduling, live intensity meters.
- Variant, layer, and stinger *commands*. Existing documents still round-trip. The panel may show a count. It does not create them.
- New Alembic revision, new table, `composition.v5`.
- Agent registry entries, workspace artifacts, `multi-agent-apply`.
- Resolving WAV paths or `DATASET_ROOT`.
- React Flow or any new frontend dependency.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("write_score"); map domain errors
services/adaptive_score_service.py      (load, bind, CAS)
        ↓
services/adaptive_score_commands.py     (pure; no SQLite, no FastAPI, no Composition)
services/adaptive_score_validation.py   (pure)
        ↓
services/adaptive_score_store.replace_score
```

`adaptive_score_commands.py` returns a new `AdaptiveScoreV1` or raises `AdaptiveScoreError`. It does not load the project. The service validates, binds, and writes. `ai_agents/` does not import these modules.

**2. Command envelope**

```json
{
  "expected_document_revision": 3,
  "op": "create_state",
  "payload": { "name": "Exploration" }
}
```

- `extra=forbid` on the envelope and every payload.
- `op` is a `Literal` of the nine operations below. Anything else is `422 adaptive_score_invalid`.
- `expected_document_revision` is required. Mismatch is `409 adaptive_score_conflict` and does not write. Same counter as `PUT`.
- One command per request. No batch.
- Success returns `AdaptiveScoreCommandResponse`: the same fields as `AdaptiveScoreGetResponse` plus `findings` (warnings that were saved; errors never persist). Do not add `findings` to `AdaptiveScoreGetResponse`; that model is shared with GET and is `extra=forbid`. `document_revision` increments by 1.
- The request is one Pydantic model per `op`, combined with `Annotated[..., Field(discriminator="op")]`. Each payload model is `extra=forbid`. An untyped `payload` object is not enough.
- A graph or binding error returns `422`. `detail.code` is the first error finding's code. `detail.details.findings` is the combined graph and bind error list (`code`, `severity`, `target_id`, `message` only). Cap the list at 64. The command path gathers both lists before rejecting. It does not call `raise_on_error_findings`, which keeps only the first code and returns before bind. Schema failures stay `422` without a findings list.
- `map_adaptive_score_error_to_http` today keeps only scalars and lists of strings, so a list of finding objects would be dropped. Extend it to pass `details["findings"]` when every item is an object with only those four keys. Pass no other nested objects.
- Collaboration: `write_score`. Flag off stays open.
- Forbidden-key scan runs on the raw payload before the command is applied.

**3. Operations**

| `op` | Payload | Effect |
|------|---------|--------|
| `create_state` | `name`, optional `id`, optional `material` | Append a state. Server id is `state_` plus 8 hex characters when `id` is omitted. Ids must match the existing entity id pattern and be unique. Default material, when omitted, is `bar_range` bars 1–1 so a score whose `bar_count` is 1 still binds. Default intensity `0`. Default entry `material_start`, exit `material_end`, loop disabled, empty `transition_ids`. When `initial_state_id` is null, set it to the new id. When `default_state_id` is null, set it to the new id. Cap: existing 32-state limit → `adaptive_score_too_large`. |
| `delete_state` | `state_id` | Remove that state. Remove every transition whose `from_state_id` or `to_state_id` is that state, and strip those ids from other states' `transition_ids`. Delete variants of that state. Set `layer.state_id` and `stinger.associated_state_id` to null when they pointed at it (schema allows null as "any state"). If `initial_state_id` or `default_state_id` pointed at it, set that field to null. Unknown id is `422 dangling_state_ref` and does not write. |
| `duplicate_state` | `state_id`, optional `name` | New id (`state_` + 8 hex). Copy material, intensity, loop, entry, exit, and duration bounds by value. `transition_ids` is empty. Variants, layers, and stingers are not copied. The copy is not initial and not default. Default name is the source name plus ` copy`, trimmed to the name cap. |
| `assign_material` | `state_id`, `material` (`AdaptiveMaterialRefV1`) | Replace that state's `material`. Then run the existing binding and `mixed_revision_targets` rules. Error findings reject the write. |
| `create_transition` | `from_state_id`, `to_state_id`, `quantization`, optional `id`, `priority`, `conditions`, `fallback_behavior`, `fallback_transition_id`, `custom_grid_bars` | Append a transition (`tran_` + 8 hex when `id` is omitted). Append its id to the source state's `transition_ids` if missing. Self-transitions are allowed. Both states must exist. Quantization and fallback rules stay the existing model validators. |
| `edit_transition` | `transition_id` plus the same mutable fields as create, all optional except `transition_id` | Replace provided fields. `id` never changes. If `from_state_id` changes, remove the id from the old source's `transition_ids` and append it on the new source. Both endpoint states must already exist, same as create. |
| `assign_loop` | `state_id`, `enabled`, `start_bar`, `end_bar` | Replace `state.loop`. |
| `assign_intensity` | `state_id`, `intensity` (0..1, not a boolean) | Replace `state.intensity`. |
| `assign_boundary` | `state_id`, `which` (`entry` or `exit`), `boundary` (`AdaptiveBoundaryV1`) | Replace that boundary. |

Commands that name a missing state or transition return `422 dangling_state_ref` or `422 adaptive_score_invalid` (`target_id` set) and do not write. They do not create placeholder nodes.

**4. Findings (new or tightened)**

Messages are required, ≤ 200 characters, and include the entity id plus the numbers that failed. Examples: `Loop on state-combat ends at bar 2 before start bar 4.`

Conditions on one transition are a conjunction. An empty condition list is always satisfiable. `manual` and `flag_equals` are never statically impossible (a person or a later runtime supplies them).

| Code | Severity | Rule |
|------|----------|------|
| `dangling_state_ref` | error | Unchanged: initial, default, transition endpoints, variant, layer, stinger, eligibility. |
| `impossible_transition` | error | Eligible transition (listed on its source) is statically unsatisfiable. See the four cases below. |
| `loop_bounds` | error | Keep the enabled `end_bar >= start_bar` rule. Also: when the state's material has both `start_bar` and `end_bar`, an enabled loop must lie inside that inclusive span. When binding can see a `section` target, an enabled loop must lie inside that section's absolute bars (`start_bar` .. `start_bar + bar_count - 1`). |
| `missing_fallback_state` | error | A score policy (`on_missing_material`, `on_invalid_transition`, or `on_unresolved_condition`) is `default_state` and `default_state_id` is null or not a state. A transition with `fallback_behavior: default_state` while the score default is missing. When this error is emitted, do not also emit `default_state_missing` for the same gap. A reachable state with no outgoing edge is a valid ending (Victory). It is not this error, so Exploration → Combat → Victory can be saved one command at a time while the default policy stays `stay`. |
| `transition_deadlock` | error | See the algorithm below. |
| `timing_incompatible` | error | See the timing rules below. |

`impossible_transition` cases, all on an eligible transition:

1. Any `min_time_in_state_bars` value is greater than the source state's `max_duration_bars` when that max is not null.
2. Any `intensity_at_least` value is greater than the source `intensity` and greater than every variant `intensity_max` on that state (no variant means only the state intensity counts). Any `intensity_at_most` value is less than the source `intensity` and less than every variant `intensity_min`.
3. `quantization` is `next_exit` and the source material cannot name an end: kind is `asset` or `motif` or `track_range` without `end_bar` and without `end_tick`, and the source exit is `material_end`. Section, `bar_range`, and `revision_region` with a bar span can name an end, so they are not this case.
4. Do not mark a transition impossible only because it participates in a cycle.

`transition_deadlock` algorithm:

1. Eligible edge: transition id is in the source state's `transition_ids` and `from_state_id` matches.
2. Build components on those edges (self-loops included).
3. A component is a deadlock only when all of the following hold:
   - It contains at least one edge.
   - It is reachable from `initial_state_id`, or `initial_state_id` sits inside it.
   - No eligible edge leaves the component.
   - Every eligible edge inside it is `impossible_transition` (no empty condition list, no `manual`, no `flag_equals` that would make the edge possible).
   - Fallback cannot leave: `on_invalid_transition` is `stay`, or it is `default_state` and `default_state_id` is null or inside the same component.
4. Exploration → Combat → Victory with `manual` or empty conditions is not a deadlock, including Victory → Exploration.
5. A Combat state whose only eligible edge is a self-transition with `min_time_in_state_bars` above `max_duration_bars`, and whose score fallback is `stay`, is a deadlock.
6. One finding per component. `target_id` is the lexicographically smallest state id in the component. Message lists up to three state ids.

`timing_incompatible` cases:

- Entry and exit are both `bar` and `exit.bar < entry.bar`.
- Entry and exit are both `tick` and `exit.tick < entry.tick`.
- A `bar` boundary sits outside the material's inclusive `start_bar`/`end_bar` when both are set.
- A `tick` boundary sits outside the material's inclusive `start_tick`/`end_tick` when both are set.
- Entry and exit use different units (`bar` vs `tick`), or a boundary uses `bar` while the material has only ticks (or `tick` while the material has only bars). `material_start` and `material_end` do not count as a unit clash.
- An enabled loop's `start_bar` is greater than an entry `bar`, or an exit `bar` is less than the loop `start_bar`.
- Do not convert ticks to bars.

Unreachable states stay warnings (`state_unreachable`). `strict=true` on validate still promotes warnings to errors. New codes above are already errors, so strict does not change them. Saves still reject error findings and still allow warning-only drafts, same as `PUT`.

**5. Frontend**

- Tab id `adaptive`, label `Adaptive`, in `frontend/src/components/ComposerWorkspace.jsx` `TABS`, after `analysis`. Mount `AdaptiveScorePanel` only while selected, same pattern as other panels. `data-testid` values: `composer-tab-adaptive`, `adaptive-score-panel`, `adaptive-state-<id>`, `adaptive-transition-<id>`, `adaptive-current-state`, `adaptive-finding-<code>`, `adaptive-validate-button`.
- HTTP in `frontend/src/api/adaptiveScoreApi.js` only. Components do not call `fetch` directly.
- Session state on `musicStore` (one store, `initialAdaptiveScoreState`): loaded score id, document revision, binding status, findings, `selectedStateId`, command error. Selecting a card sets `selectedStateId`. It does not call the server and does not write `editedMusicJson`.
- Spread `initialAdaptiveScoreState` at every composition-replace site that already spreads `initialHarmonyUiState` (open, import, generate apply, and the other replace patches in `musicStore.js`). Otherwise the Adaptive tab keeps the previous project's graph.
- Load when the Adaptive panel mounts and `currentProjectId` is set. If a score list exists, load the default score, else the first score. Ignore the response when `currentProjectId` has changed, the same guard generation apply already uses. If none exists, the panel offers "New adaptive score" which calls the existing create route with an empty draft named `Exploration cue`.
- Graph layout is a pure function in `frontend/src/utils/adaptiveScoreGraph.js`: grid coordinates, edge list `{from, to, id}`, material label from `kind` plus section id or bar range. No network, no Tone.js.
- The selected state card is the current state. The header reads `Current state: <name>` and also marks the initial state with a separate badge so selection and start are not confused.
- Material assignment form offers the open composition's sections (`editedMusicJson.sections`) and a bar-range pair. Optional revision id comes from versions already loaded in the store. It does not invent notes.
- Findings render under the graph after every command and after Validate. A 422 body supplies them at `detail.details.findings`. The panel scrolls the matching `adaptive-state-*` into view when `target_id` is a state id.
- Desktop and a narrow viewport: the graph region scrolls horizontally; the form stacks. Follow the 44px control height used by `HarmonyTimelinePanel`.

**6. Acceptance walkthrough (manual and Playwright)**

1. Open a project that already has a `composition.v2` draft.
2. Open the Adaptive tab. Create the score if needed.
3. Add states named Exploration, Combat, and Victory. Exploration is initial and default because it is first.
4. Assign Exploration to section `section-1` (bars 1–2), Combat to section `section-2` (bars 3–4), and Victory to bar range 1–4. The expressive seed used by Playwright is 4 bars (`backend/app/fixtures/composition_v2_expressive.json`). Bars past `bar_count` are `material_range_outside` and the command will not persist.
5. Add transitions Exploration → Combat and Combat → Victory, quantization `bar`, one `manual` condition (or none).
6. Persist succeeds. Reload keeps the three states. Validate reports no errors. `ready` is true when initial and default still point at Exploration.
7. Trigger one known-bad edit (enabled loop whose end bar is before its start, or a min-time condition above max duration) and show the server message. The bad edit does not persist.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: validate adaptive score authoring rules`
- **Commit 2** (after tasks 4–5): `feat: add adaptive score graph commands`
- **Commit 3** (after tasks 6–8): `feat: add adaptive score graph editor`
- **Commit 4** (after task 9): `docs: describe adaptive score authoring`

## Tasks

### Phase 1: Validation
- [x] Task 1: Extend adaptive-score findings
  - Add codes `impossible_transition`, `missing_fallback_state`, `transition_deadlock`, and `timing_incompatible` to `ADAPTIVE_SCORE_ERROR_CODES` in `backend/app/adaptive_score_schemas.py`.
  - Implement the locked rules in `backend/app/services/adaptive_score_validation.py`. Loop-vs-section checks that need a composition stay in `bind_material_refs`. Loop-vs-material-bar checks that only need the score stay in `validate_adaptive_score_graph`.
  - Messages ≤ 200 characters and include the target id and the failing numbers.
  - Keep existing codes and their tests passing. Circulating Exploration → Combat → Victory is not a deadlock. A reachable state with no outgoing edge still saves (`missing_fallback_state` does not fire). Unreachable states stay warnings. `transition_deadlock` `target_id` is the lexicographically smallest state id in the component.
  - Extend `backend/tests/test_adaptive_score_validation.py` with one test per new case in the lock, plus the circulating-cycle negative test and the terminal-state negative test.
  - Depends on: none.

  LOGGING REQUIREMENTS:
  - Keep the existing INFO line on graph check (`error_count`, `warning_count`, entity counts).
  - DEBUG one line per finding: `code`, `target_id`, `severity`. Do not log the score body, pitches, or `body_json`.
  - Levels follow `LOG_LEVEL`.
  - Tag new deadlock DEBUG lines with `component_size` only (an integer), not the full graph.

### Phase 2: Commands
- [x] Task 2: Add command DTOs
  - In `backend/app/adaptive_score_schemas.py`, add one command model per `op`, combined with `Annotated[..., Field(discriminator="op")]`. Each payload is `extra=forbid`.
  - Add `AdaptiveScoreCommandResponse` with the get-response fields plus `findings` (reuse `AdaptiveScoreFindingV1`). Do not add `findings` to `AdaptiveScoreGetResponse`. Do not put `expected_document_revision` inside `body_json`.
  - Depends on: Task 1.

  LOGGING REQUIREMENTS:
  - On schema failure, reuse `log_adaptive_schema_failure` with model name, field, and code.
  - Do not log the payload dict.

- [x] Task 3: Implement pure command apply
  - Create `backend/app/services/adaptive_score_commands.py` with `apply_adaptive_score_command(score, command) -> AdaptiveScoreV1`.
  - Implement the nine operations exactly as locked, including delete cleanup, duplicate without edges, and first-state initial/default fill. Default material is bars 1–1. `edit_transition` rejects an endpoint state that does not exist.
  - No SQLite, no FastAPI, no composition load. Caps and id rules raise `AdaptiveScoreError`.
  - Unit tests in `backend/tests/test_adaptive_score_commands.py`: each op, unknown state, duplicate id, delete cascade of transitions, first state becomes initial and default, duplicate does not copy `transition_ids`, forbidden note key rejected.
  - Depends on: Task 2.

  LOGGING REQUIREMENTS:
  - INFO: `op`, `project` omitted here (the service logs the project), `state_count`, `transition_count` after apply.
  - DEBUG: created or deleted entity id and `op`. Never the material body.
  - ERROR: command rejected, with `code` and `op` only.

<!-- Commit checkpoint: tasks 1–3 -->

- [x] Task 4: Persist commands over the existing store
  - Add `apply_adaptive_score_command` orchestration in `backend/app/services/adaptive_score_service.py`: load score, apply, parse, forbidden-key scan, run graph validation and binding together, then `replace_score` with `expected_document_revision` only when there are no error findings.
  - On error findings, do not call `replace_score` and do not call `raise_on_error_findings`. Raise `AdaptiveScoreError` whose code is the first error finding and whose `details.findings` is the combined graph and bind error list, capped at 64 objects of `{code, severity, target_id, message}`.
  - Extend `map_adaptive_score_error_to_http` so `detail.details.findings` survives. Keep dropping other nested objects.
  - Add `POST /{score_id}/commands` in `backend/app/routers/adaptive_scores.py`. Response model `AdaptiveScoreCommandResponse`. `write_score`. Map errors with `map_adaptive_score_error_to_http`.
  - Extend `backend/tests/test_adaptive_score_api.py`: Exploration → Combat → Victory via three `create_state` and two `create_transition` calls (the terminal state saves); CAS mismatch does not change the row; a bad loop does not change `document_revision` and the 422 JSON includes `detail.details.findings` with the loop message; `projects.composition_json` is unchanged; viewer denied when collaboration is on.
  - Depends on: Task 3.

  LOGGING REQUIREMENTS:
  - INFO on the route: `method`, `project_id`, `score_id`, `op`, `http_status`, `document_revision`, `duration_ms`, `state_count`.
  - DEBUG: finding codes on rejection.
  - Do not log payload, `body_json`, or composition JSON.

### Phase 3: Graph editor
- [x] Task 5: Add the client API and layout helper
  - `frontend/src/api/adaptiveScoreApi.js`: list, create, get, command, validate. Failures become `Error` with `status` and `findings` copied from `detail.details.findings` when that list is present. Do not log response bodies.
  - `frontend/src/utils/adaptiveScoreGraph.js`: `layoutAdaptiveScoreGraph` and a material one-line label. Node tests in `frontend/src/utils/adaptiveScoreGraph.test.js` (node:test): three states in order, selected id flagged, edge endpoints, bar-range label, empty score.
  - Depends on: Task 4 for the request shape; the helper itself has no HTTP.

  LOGGING REQUIREMENTS:
  - `console.debug` in the API module with method, path template (no raw composition), and status.
  - On failure, log `status` and finding `code` values only.

<!-- Commit checkpoint: tasks 4–5 -->

- [x] Task 6: Store the open adaptive score
  - Add `initialAdaptiveScoreState` and actions on `frontend/src/store/musicStore.js`: load list, create empty score, run command, validate, select state.
  - Spread `initialAdaptiveScoreState` at every site that already spreads `initialHarmonyUiState`.
  - Command actions send `expected_document_revision` from the store and replace local score only on success. The load action ignores a response when `currentProjectId` changed while it was in flight.
  - 409 `adaptive_score_conflict` refetches the score and surfaces a conflict message. It does not write the composition or change `workingVersion`.
  - Selecting a state updates `selectedStateId` only.
  - Store tests beside existing store tests (`frontend/src/store/musicStore.adaptiveScore.test.js`): command success bumps revision; 422 keeps the previous score and stores findings from `detail.details.findings`; select does not change the score; a project replace clears the adaptive session.
  - Depends on: Task 5.

  LOGGING REQUIREMENTS:
  - `console.info` when a command is sent: `op` and score id.
  - `console.debug` on select: state id.
  - `console.warn` on conflict or 422: code list only.
  - Do not log the score JSON.

- [x] Task 7: Build the Adaptive tab
  - `frontend/src/components/AdaptiveScorePanel.jsx` (styled-components, same control sizing as `HarmonyTimelinePanel.jsx`).
  - Register the tab in `frontend/src/components/ComposerWorkspace.jsx`.
  - UI shows state cards, transitions, current-state header, material labels, and findings, using the test ids in the lock.
  - Forms call store actions for create state, delete state, duplicate state, assign material, create transition, edit transition, assign loop, assign intensity, assign entry, and assign exit.
  - On mount, when `currentProjectId` is set, call the load action.
  - Empty project with no score shows the new-score action. No score means no playback control and no Tone.js import.
  - Depends on: Task 6.

  LOGGING REQUIREMENTS:
  - `console.debug` when the tab mounts and when validate returns: score id, state count, error count, warning count.
  - `console.info` on user-submitted commands: `op` only.
  - Do not log material payloads.

- [x] Task 8: Playwright acceptance journey
  - Add `frontend/e2e/adaptive-score.spec.js`.
  - Seed a V2 project the way `frontend/e2e/motif-panel.spec.js` does.
  - Walk the acceptance steps: create three named states, two transitions, assign Exploration to `section-1`, Combat to `section-2`, and Victory to bar range 1–4 (the expressive seed has `bar_count` 4), reload, assert the three names, current state, and a material label are visible, and validation shows no errors.
  - Then submit an enabled loop with end bar before start bar and assert the finding text is visible and the three state names remain.
  - Check a narrow viewport (the graph region still exposes the state names).
  - Depends on: Task 7.

  LOGGING REQUIREMENTS:
  - No new server logs. The journey uses the existing route logs.
  - If a helper needs a browser log line, use `console.debug` with the score id only.

<!-- Commit checkpoint: tasks 6–8 -->

### Phase 4: Documentation
- [x] Task 9: Document authoring and the new findings
  - Update `docs/adaptive-score.md`: command table, the nine operations, the new finding codes, the deadlock definition (circulating cycles stay valid), and the Adaptive tab. State that a reachable state with no outgoing edge is a valid ending, that a rejected command returns findings at `detail.details.findings`, and that current state is an authoring selection. Remove the sentence that says a visual graph editor is out of scope; playback stays out of scope.
  - Update the Adaptive scores row in `.ai-factory/ARCHITECTURE.md` so the frontend cell names `AdaptiveScorePanel`, `adaptiveScoreApi.js`, and `adaptiveScoreGraph.js`.
  - Update `AGENTS.md` key-entry rows for the router (commands) and the frontend panel.
  - Update the adaptive-score pointer in `docs/CODEBASE_MAP.md`.
  - Do not edit `ROADMAP.md`.
  - Depends on: Tasks 4 and 7.

  LOGGING REQUIREMENTS:
  - Document the INFO and DEBUG fields from Tasks 1 and 4, including the redaction list (no `body_json`, no pitches, no command payloads at INFO).

<!-- Commit checkpoint: task 9 -->
