# Implementation Plan: V4 Autonomous Project Composer

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-26
Planning depth: ultra

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra
- Refined: 2026-09-26 (`/aif-improve`, all findings applied)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request’s mocked end-to-end tests
- Scope: one creative brief schedules the existing specialized agents, writes an explicit project plan, realizes a multi-section `composition.v2` through symbolic generation, critique, targeted revision, arrangement, and expression, optionally renders, and leaves a durable editable project. Stage status and budgets survive restart. The Agents-tab spine preview stays preview-only
- Parent (reuse, do not fork): `.ai-factory/plans/v4-observability-resource-controls.md` — shipped `operation.span.v1`, `operation.summary.v1`, `reserve_model_call`, `mark_run_cancelled`, `OPERATION_*` ceilings, and neural `operation_run_id`. This plan adds an agent-operation counter and a durable stage machine. It keeps span kinds, the rule that currency is never invented, and the rule that the spine does not start renders
- Also reuse: `.ai-factory/plans/v4-multi-agent-music-architecture.md` (agent ids, typed artifacts, `apply_realized_composition`) and `.ai-factory/plans/controlled-critique-revision-loops.md` (stop reasons, preserve-outside-targets, last valid)

## Roadmap Linkage
Milestone: "V4 autonomous project composer"
Rationale: Every current roadmap milestone is checked, including observability. The spine still runs one fixed preview and waits for Apply. A brief that schedules specialized agents and commits an editable project is a new milestone. The docs task adds it unchecked. Check it only after the mocked end-to-end tests pass.

## Goal

A user submits one creative brief. Autonomous Composer builds a multi-section `composition.v2` by calling specialized agents, and the result is a project the editor can open. The user does not invoke harmony, motif, critique, revision, arrangement, and expression as separate tools.

```text
creative.brief.v1
    │  deterministic compiler locks structure
    ▼
creative_director.plan          → project.plan.v1 (goals filled; structure cannot move)
    ├ harmony.plan              → agent.harmony_plan.v1
    ├ melody_motif.plan         → agent.motif_plan.v1
    ▼
generate_symbolic_composition   → composition.v2 + theme bind + final-section mode
    ▼
critic.critique                 → agent.critique.v1
    ▼
targeted revision (existing stop + preserve helpers)
    ▼
arrangement.propose
    ▼
performance_expression          → dynamics / velocity only
    ▼
optional render (skipped unless requested; approval gates it)
```

**Acceptance one-liner:** Given a creative brief, Autonomous Composer builds a multi-section Composition through specialized agent stages and produces a valid editable project without requiring the user to manually invoke every tool.

Example brief used by the compiler unit test and the mocked end-to-end test:

```text
Create a 2:30 instrumental cinematic piece.
Narrative: cold sparse opening; introduce Theme A; increase tension;
           strong climax; quiet transformed ending.
Instrumentation: piano, cello, strings.
Constraints: no drums; Theme A must remain recognizable;
             begin in F# minor; transform final theme toward major.
```

## Terminology lock

| Term | Meaning |
|------|---------|
| **Creative brief** | `creative.brief.v1`. User goal, narrative beats, instruments, and hard constraints. Not a prompt blob and not a score |
| **Project plan** | `project.plan.v1`. Goals, structured constraints, sections, and the stage graph. Non-playable. Immutable after the plan stage |
| **Stage** | One row in `autonomous_stages`. Status is never stored inside the plan artifact |
| **Run** | One autonomous composition. `autonomous_runs.id`. Carries the observability `operation_run_id` so model calls and an optional render share it |
| **Compiler** | Deterministic function from brief to project plan. Not an LLM |
| **Scaffold** | In-memory `composition.v2` passed into the director only so `AgentWorkflowContext` can be built. Discarded. Never committed |
| **Spine** | Existing `POST /ai/agents/workflows/preview` / `run_spine_workflow`. Unchanged order. Does not execute this plan |

## Non-goals

- One model call that emits the finished score, the plan, and the stage list
- Changing `SPINE_AGENT_IDS` or auto-applying Critic approve on the spine
- Executing today’s `agent.workflow_plan.v1` steps (that artifact stays a log of the spine)
- `composition.v4`, `DATASET_ROOT` writes, or a new playable schema
- `ai_agents/` importing `agent_artifact_workspace`, `db`, or `autonomous_composer_store`
- A new `operation.span.v1` kind. Stage state is the SQLite row plus the existing run/agent/model/render spans
- Extending `revision_artifact_links` CHECK roles. New artifacts are durable `agent_artifacts` rows referenced by `autonomous_stages.artifact_ids_json`
- Starting neural renders from the spine. Render stays an autonomous stage and stays off unless the request asks
- Inventing USD when the provider omits cost
- Rebuilding the arrangement, development, critique, or symbolic engines
- Adding `structure_form` or `orchestration` to this graph. Form lives on the project plan. Instrumentation is the brief plus the arrangement agent
- A Playwright journey. The acceptance test is pytest with `LLM_FAKE_MODE=1` and `fake:symbolic-tiny`

## Approach Evaluation (locked)

### Part A — Who schedules

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A single director prompt returns the piece** | Short | Violates the reuse requirement. No stage status. Hard constraints are wishes | **Reject** |
| **B. Teach the spine to execute `agent.workflow_plan.v1`** | Reuses `run_spine_workflow` | Spine order is an acceptance fixture. Harmony `propose` needs notes that do not exist yet. Preview must not commit | **Reject** |
| **C. A service scheduler calls `get_agent().run` per stage** | Agents stay the workers. Service owns SQLite. Spine stays preview-only | New route and two tables | **Accepted** |

**Locked:** `backend/app/services/autonomous_composer.py` is the only scheduler. It calls `get_agent(agent_id).run(...)`. Agents return artifacts and, on realize stages, a `working_draft_update` from `apply_realized_composition`. The service commits. `CreativeDirectorAgent` does not loop over other agents.

### Part B — Where the project plan comes from

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. The model invents stages** | Flexible wording | Drops stages, breaks dependencies, bypasses constraints | **Reject** |
| **B. Compiler only, director unused** | Deterministic | The brief’s director step is skipped | **Reject** |
| **C. Compiler locks structure; director fills goals** | Hard fields cannot move. Director still runs | Goal sentences can be ignored when they conflict | **Accepted** |

**Locked:** `compile_project_plan(brief)` in `backend/app/services/autonomous_project_plan.py` builds the stage graph, sections, bar counts, instruments, and constraints. The service passes that object on `selection["compiled_project_plan"]`. The director may replace `goals[].summary` and section `narrative` (max 240). Any change to stage ids, dependencies, section bounds, keys, instruments, or completion codes is discarded. The stored plan is the merge. Fake mode copies the compiled plan and does not call a model. A real planner model, when one is routed, is limited to those text fields. After the call, the service merges again.

### Part C — Notes before a score exists

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Call harmony and melody `propose` on the scaffold** | Uses today’s realize path | Scaffold notes leak into the piece. Reharmonize invents from a dummy | **Reject** |
| **B. New note-writing agents** | Direct | Forks symbolic generation | **Reject** |
| **C. `plan` emits typed plans; symbolic generation writes the first score** | Matches the requested order. Uses `generate_symbolic_composition` | Harmony and melody need a `plan` branch that does not realize | **Accepted** |

**Locked:** On `AgentOperation.PLAN`, harmony emits `agent.harmony_plan.v1` and melody emits `agent.motif_plan.v1`. `working_draft_update` stays null. `AgentOperation.PROPOSE` stays the spine path. The symbolic stage compiles those plans into `composition.plan.v1`, calls `ensure_plan_conforms`, then `generate_symbolic_composition`. No silent LLM-note fallback.

### Part D — Durability

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Session-only, like spine preview** | No migration | Restart loses the piece. Acceptance wants an editable project | **Reject** |
| **B. One commit at the end** | Simple CAS | A failed expression stage throws away the valid arrangement | **Reject** |
| **C. Commit a revision after each stage that changes the score** | Failed later stages leave the previous head. Rows survive restart | More revisions | **Accepted** |

**Locked:** Operation type `autonomous-stage` (`RevisionOperationType`, TEXT column, no CHECK to migrate). Composition-writing stages are symbolic, revision (only when a pass replaces the draft), arrangement, and expression. Plan, harmony plan, motif plan, and critique insert artifacts and do not commit a score. The scaffold is not a revision.

Durable artifacts use a new `insert_durable` in `agent_artifact_workspace.py`: `retention_class=durable`, `expires_at` null, no `revision_artifact_links` row. `insert_temporary` is the wrong call because those rows expire. `promote_and_link_revision` stays limited to its six existing roles. `project.plan.v1` is not one of them.

Before every `commit_durable_revision`, re-read the branch. Pass that `working_version` and `head_revision_id`. A newer `working_version` is acceptable only when `working_fingerprint` still equals the run’s `composition_fingerprint` (an autosave of the same score). A different fingerprint is `project_revision_conflict` for that stage. The previous revision stays head.

### Part E — Revision without rerunning the spine

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `run_revision_loop` / `run_spine_workflow`** | One call | Re-runs the director and replaces the autonomous order | **Reject** |
| **B. A copied revise loop inside the autonomous service** | Isolated | Drifts from preserve and stop policy | **Reject** |
| **C. `run_targeted_revision_passes` beside `run_revision_loop`** | Same stop, preserve, and budget helpers. Starts from the critique already stored | One new function in `revision_loop.py` | **Accepted** |

**Locked:** If the critic recommendation is `approve`, the revision stage is `skipped`. Otherwise `run_targeted_revision_passes` runs only agents in `target_agent_ids` (`harmony`, `melody_motif`, `arrangement`) by calling the existing `_run_agent_node`, so `SystemExit` stays contained there. `preserve_outside_targets` still applies. A failed pass restores the last valid draft and does not commit. The existing preview route still calls `run_revision_loop`.

### Part F — Optional render

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always enqueue audio** | Pipeline picture is literal | Violates the observability non-goal and spends a quota on every brief | **Reject** |
| **B. No render stage** | Smaller | The status `awaiting_approval` has no use, and the requested pipeline includes optional rendering | **Reject** |
| **C. Stage exists; default `skipped`; approval required when rendering is on** | Budgets stay opt-in. Status enum is used | One approve route | **Accepted** |

**Locked:** `include_rendering` defaults false → stage `skipped`, run can `completed`. When true, `render_approval` defaults to `required` → stage `awaiting_approval` and the run pauses before `enqueue_neural_audio_render`. `auto` enqueues immediately. Tests leave rendering off. A second test approves with the fake neural engine.

## Contract

### `creative.brief.v1`

`backend/app/autonomous_composer_schemas.py`, `extra=forbid`.

| Field | Rule |
|-------|------|
| `schema_version` | `creative.brief.v1` |
| `title` | Optional, max 120. Project name when `project_id` is omitted |
| `duration_seconds` | 30..600 |
| `narrative` | 1..8 beats. `intent`: `sparse_opening`, `establish_theme`, `build`, `climax`, `resolve` |
| `instrumentation` | 1..8 catalog labels |
| `forbidden_instrument_families` | Default `[]`. The example sends `["drums"]` |
| `opening_key` | Key pattern already used by plans (`F# minor`) |
| `final_section_key` | Optional. Example: `F# major` |
| `motif_label` | Default `Theme A` |
| `motif_must_remain_recognizable` | Default true |
| `time_signature` | Default `4/4` |
| `tempo_min`, `tempo_max` | Optional. Default 60..84 when omitted. Opening tempo used for bar math is `tempo_min` when set, else 72 |
| `mood`, `genre` | Optional soft fields, max 64 |

Reject unknown instrument labels with HTTP 422 `autonomous_instrument_unknown` before any agent runs. Resolve labels with the arrangement catalog / `normalize_instrument`. Do not accept freeform drum kits under another name when the family is forbidden.

### Bar math (deterministic)

`duration_bars = clamp(round(duration_seconds * bpm / 60 / beats_per_bar), 1, 512)`.

`beats_per_bar` comes from `time_signature` via the existing meter parser. Do not call `mix_analysis.windows.seconds_to_bars`.

Five example beats use weights `0.15, 0.20, 0.20, 0.25, 0.20`. Largest-remainder rounding. The 2:30 / 72 BPM / 4/4 case is **45 bars**: intro **7**, theme **9**, tension **9**, climax **11**, outro **9**.

| Beat intent | Section type | Density |
|-------------|--------------|---------|
| `sparse_opening` | `intro` | `sparse` |
| `establish_theme` | `verse` | `moderate` |
| `build` | `bridge` | `moderate` |
| `climax` | `chorus` | `dense` |
| `resolve` | `outro` | `sparse` |

Section ids are `sec-1` … in order. Labels come from the beat text, truncated to 64. `establish_theme` is the motif section. `resolve` receives `final_section_key` when set.

### `project.plan.v1`

Non-playable. Reuse the artifact forbidden-key gate (`tracks`, `events`, `notes`, `note_events`, `musicxml`, `midi`, `wav`, `composition`).

| Field | Rule |
|-------|------|
| `goals` | One per narrative beat. `id`, `summary` ≤ 240, `section_id` |
| `constraints` | Opening key, final key, meter, tempo bounds, `duration_bars`, instruments, forbidden families, motif label, recognizability flag |
| `sections` | `id`, `type`, `label`, `start_bar`, `bar_count`, `density`, `narrative` ≤ 240. Contiguous from bar 1. Sum equals `duration_bars` |
| `stages` | The nine rows below, in order |
| `completion_codes` | Per stage, from the closed list |

Stage graph (ids are stable):

| `stage_id` | Agent | Operation | Depends on | Score commit | Approval |
|------------|-------|-----------|------------|--------------|----------|
| `plan` | `creative_director` | `plan` | — | No | `auto` |
| `harmony_plan` | `harmony` | `plan` | `plan` | No | `auto` |
| `motif_plan` | `melody_motif` | `plan` | `harmony_plan` | No | `auto` |
| `symbolic` | none (service) | `realize` | `motif_plan` | Yes | `auto` |
| `critique` | `critic` | `critique` | `symbolic` | No | `auto` |
| `revision` | targeted agents from the revision plan | `propose` | `critique` | Only when a pass changes the draft | `auto` |
| `arrangement` | `arrangement` | `propose` | `revision` | Yes, when the candidate passes constraints | `auto` |
| `expression` | `performance_expression` | `advise` then realize | `arrangement` | Yes, when pitches and timing are unchanged | `auto` |
| `render` | `production` | `advise` | `expression` | No (job row only) | `required` when rendering is on, else the stage is `skipped` |

`agent.workflow_plan.v1` is still emitted by the director for the spine. It is not this graph. The autonomous artifact content type is `project.plan.v1`.

### Stage status

Stored on `autonomous_stages.status`:

`pending` | `running` | `completed` | `failed` | `awaiting_approval` | `skipped`

Run status on `autonomous_runs.status`:

`pending` | `running` | `completed` | `failed` | `awaiting_approval` | `cancelled`

A run is `completed` when every stage is `completed` or `skipped`. It is `awaiting_approval` when a stage is `awaiting_approval` and none is `running`. It is `failed` when a required stage is `failed`. It is `cancelled` when the client cancels. Completed stages stay `completed` after a later failure or cancel.

Cancel maps the in-flight stage to `failed` with `failure_code=operation_cancelled`. It does not mark that stage `skipped`. `skipped` is only the intentional render skip, or revision when the critic says `approve`.

### Completion codes

Closed strings on the stage, evaluated by `assert_stage_completion` after the worker returns:

| Code | Passes when |
|------|-------------|
| `project_plan_valid` | Merged plan validates and matches the compiler’s stage ids and section bounds |
| `harmony_plan_key` | `agent.harmony_plan.v1.key` equals `opening_key`. One `chord_events` row per section start (`PLAN_CHORD_EVENT_MAX` is 64; a brief may be 512 bars) |
| `motif_plan_present` | A motif entry whose label equals `motif_label` lists the theme section |
| `composition_v2_ok` | `validate_composition_integrity(..., profile="canonical")` |
| `hard_constraints_ok` | `validate_generation_constraints` for key, meter, tempo, bar count, sections, requested instruments |
| `no_forbidden_instruments` | No track `is_drum` and no identity in `forbidden_instrument_families` (`analyze_instrumentation` / `normalize_instrument`) |
| `motif_identity_ok` | When the flag is true: `verify_motif_identity` passes on the rhythmic copy **before** the mode remap. Missing motif rows fail `autonomous_motif_missing` |
| `final_section_mode_ok` | After the remap, outro theme rhythm is unchanged and those pitches lie on `final_section_key`. Do not call `verify_motif_identity` again. Omitted when the brief has no final key |
| `critique_stored` | An `agent.critique.v1` artifact id is on the stage |
| `revision_contained` | Either skipped on approve, or `assert_preserve_outside_targets` held for every committed pass |
| `arrangement_preserved` | Arrangement candidate `preserve_melody` assertions hold and forbidden instruments are still absent |
| `expression_pitch_stable` | Pitch, `start_tick`, and `duration_ticks` match the pre-expression fingerprint |
| `render_dispatched` | Job id stored, or the stage was skipped / left awaiting approval |

A composition-writing stage that fails a code does not commit. `failure_code` is `autonomous_constraint_failed` plus the completion code in a separate column `completion_code` (max 64). Do not put note text in the error.

### Symbolic completion detail

`compile_composition_plan(project_plan, harmony_plan, motif_plan)` builds `composition.plan.v1`:

- `form.key` = opening key, sections from the project plan
- `form.instrumentation` order is the brief order. The example stays `piano`, `cello`, `strings` so `generate_fake_symbolic_composition` assigns melody, bass, and harmony by `_ROLE_PRIORITY` and does not append a track whose instrument is `bass`
- `harmony` from `agent.harmony_plan.v1` chord events (one tonic per section start when the agent emits none)
- `motifs_themes` from the motif plan (labels and section ids only)
- `modulation.targets` = one `PlanModulationTarget` for the outro index and `final_section_key`
- `ensure_plan_conforms` then `generate_symbolic_composition` with the request seed (default 0)

`fake:symbolic-tiny` writes `motifs: []` and one mode for the whole piece. After a valid V2 returns, the service (not the model) does two bounded edits, each through existing helpers, then re-validates:

1. `bind_theme_occurrences` — match the theme and outro sections by `start_bar` and `type`. Fake symbolic rewrites ids to `section-1`, `section-2`, and so on, so a lookup of `sec-1` misses. The first melody-role cell in the theme section is the original `Theme A`. Copy that cell into the outro with the same rhythm and the same relative pitches. Call `verify_motif_identity` on that pair. A scale-degree remap changes intervals, and the default threshold at variation strength 0.5 is 0.75, so identity after the remap is the wrong check.
2. `realize_final_section_mode` — remap outro pitches only onto `final_section_key` with `parse_key`. Rhythm and start ticks stay. Other sections stay in the opening key. `final_section_mode_ok` checks the major scale and the unchanged rhythm. It does not call `verify_motif_identity` again.

If either edit fails, the symbolic stage fails and no revision is written. Plans from earlier stages remain.

### Expression realize

`performance_expression` remains plan-only on `advise` (today’s stub behavior): `agent.performance_plan.v1`, `working_draft_update` null.

The autonomous service then calls `realize_expression_marks` in `backend/app/services/composition_expression.py` and `apply_realized_composition` with a new `RealizeService.EXPRESSION_CANDIDATE = "expression_candidate"`. The realize:

- Writes a track `dynamic_marks` event at each section start: `pp` sparse, `mf` moderate, `ff` dense
- Scales note velocity inside that section (sparse 0.70, moderate 1.00, dense 1.15) and clamps to 1..127
- Does not change pitch, start, duration, track id, or instrument

`POST /ai/agents/performance_expression/run` without `selection.apply_expression=true` does not realize. The autonomous service sets the flag.

### Budgets and cancellation

Reuse `load_operation_budget_settings()` and the open run span. Add `backend/app/autonomous_composer_settings.py`:

| Env | Default | Effect |
|-----|---------|--------|
| `AUTONOMOUS_MAX_AGENT_OPERATIONS` | `24` | Count of `agent.run` calls for this autonomous run. `0` disables. Checked before the next agent stage. Fake agents count |
| `AUTONOMOUS_MAX_RUNS_PER_PROJECT` | `20` | New run over the cap returns 409 `autonomous_run_limit`. Do not delete old runs |

A request field may only lower `AUTONOMOUS_MAX_AGENT_OPERATIONS`, `OPERATION_MAX_REVISIONS`, `OPERATION_MAX_RUNTIME_MS`, and the token cap. It cannot raise them.

Persisted counters on the run (restart-safe): `agent_operation_count`, `revision_pass_count`, `prompt_tokens`, `completion_tokens`, `provider_reported_cost_micros` (null when unknown), `active_runtime_ms`. Wall budget uses `active_runtime_ms`, the sum of stage durations, not clock time while the process was down. Token and cost caps fire only when the stored usage is known (`usage_status` not treated as zero). Missing cost does not fail the run.

Stop before the next stage with `budget_code`:

- `autonomous_agent_operation_budget` (new)
- `operation_revision_budget`, `operation_runtime_budget`, `operation_token_budget`, `operation_cost_budget`, `operation_model_call_budget` (existing checkers)

The HTTP start and resume handlers adopt `operation_run_id` the same way as workflow preview (`adopt_operation_run_id`, disconnect watcher, `mark_run_cancelled`). Cancel is also `POST /ai/agents/autonomous/runs/{id}/cancel`, which sets the run row and the in-process cancel set. A resume after restart sees `cancelled` and does not continue.

`SystemExit` stays contained by the existing agent and model handlers. The stage becomes `failed` with `operation_model_crashed`. Prior revisions stay.

### Restart

On `GET` or `resume`, `reconcile_interrupted_stages`:

- `running` and `revision_id` set → `completed` (commit landed, status write did not)
- `running` and `revision_id` null → `failed`, `failure_code=autonomous_stage_interrupted`, `recoverable=1`

`POST .../resume` continues `pending` stages and `failed` stages with `recoverable=1`, in graph order, after dependencies are `completed` or `skipped`. `autonomous_constraint_failed` is `recoverable=0` until the client sends `retry=true` for that stage. Resume does not repeat `completed` stages.

### API

Router: `backend/app/routers/ai_agents.py` (do not add a second app). Schemas on the wire use the models above.

| Method | Path | Result |
|--------|------|--------|
| `POST` | `/ai/agents/autonomous/runs` | 200 `autonomous.run.v1`. Creates a project when `project_id` is omitted |
| `GET` | `/ai/agents/autonomous/runs/{id}` | 200. Reconciles interrupted stages |
| `POST` | `/ai/agents/autonomous/runs/{id}/cancel` | 200 |
| `POST` | `/ai/agents/autonomous/runs/{id}/resume` | 200. Body optional `retry_stage_id` |
| `POST` | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/approve` | 200. Only from `awaiting_approval`. Render approve calls `enqueue_neural_audio_render` with the run’s `operation_run_id` and the head revision as source |
| `POST` | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/skip` | 200. Only `render` while `awaiting_approval` |

`autonomous.run.v1` returns `run_id`, `project_id`, `status`, `head_revision_id`, `composition_fingerprint`, `budget_code`, `failure_code`, `operation_summary`, and stages (`stage_id`, `agent_id`, `status`, `revision_id`, `failure_code`, `artifact_ids`). It does not return the composition, the brief text, or prompts. The client loads the project to edit.

Errors: 404 `autonomous_run_not_found`, 409 `project_revision_conflict` (start refuses when the saved working fingerprint does not match `expected_source_fingerprint`), 409 `autonomous_run_limit`, 409 `autonomous_run_not_resumable`, 422 `autonomous_brief_invalid`, 422 `operation_run_id_invalid`, 422 `autonomous_instrument_unknown`.

Same `operation_run_id` while that run is not `failed` or `cancelled` returns the existing row (no second project).

Before stage 1, if `project_id` is set, CAS-check `expected_working_version`, `expected_head_revision_id`, and `expected_source_fingerprint`. Mismatch is 409 and no stage starts. Unsaved editor work is not overwritten.

### Persistence

Alembic `backend/app/db/alembic/versions/20260926_0012_autonomous_runs.py`, `revision = "20260926_0012"`, `down_revision = "20260925_0011"`.

```sql
CREATE TABLE IF NOT EXISTS autonomous_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    operation_run_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    head_revision_id TEXT,
    composition_fingerprint TEXT,
    agent_operation_count INTEGER NOT NULL DEFAULT 0,
    revision_pass_count INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    provider_reported_cost_micros INTEGER,
    active_runtime_ms INTEGER NOT NULL DEFAULT 0,
    failure_code TEXT,
    budget_code TEXT,
    seed INTEGER,
    include_rendering INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    CHECK (status IN (
        'pending', 'running', 'completed', 'failed',
        'awaiting_approval', 'cancelled'
    ))
);

CREATE TABLE IF NOT EXISTS autonomous_stages (
    run_id TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    agent_id TEXT,
    status TEXT NOT NULL,
    depends_on_json TEXT NOT NULL,
    target_section_ids_json TEXT NOT NULL,
    completion_json TEXT NOT NULL,
    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
    revision_id TEXT,
    failure_code TEXT,
    completion_code TEXT,
    recoverable INTEGER NOT NULL DEFAULT 1,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, stage_id),
    FOREIGN KEY (run_id) REFERENCES autonomous_runs(id) ON DELETE CASCADE,
    CHECK (status IN (
        'pending', 'running', 'completed', 'failed',
        'awaiting_approval', 'skipped'
    ))
);
```

`brief_json` max 16384 bytes. `plan_json` max 65536. Both pass `assert_no_secret_fields` and `assert_no_secret_values` before insert. Neither stores event arrays. Artifact payloads go through `insert_durable` (durable, `expires_at` null, no link row) in the same transaction as the stage status update. `ai_agents/` does not import the store. The service does.

Project delete cascades the rows. Do not TTL them.

### UI

On the Agents tab, above the spine controls, `frontend/src/components/AutonomousComposerPanel.jsx`:

- Brief fields for duration, narrative lines, instruments, key, “no drums”, “Theme A stays recognizable”, final key
- Start, Cancel, Resume
- Stage list `data-testid="autonomous-stage-list"` with status text from `frontend/src/utils/autonomousStageText.js`
- Render approve / skip only when a stage is `awaiting_approval`

`musicStore` holds the run id and the last `autonomous.run.v1`. After a composition-writing stage completes, reload the project into the editor (existing project fetch). Spine Apply / Discard stay as they are. Starting an autonomous run does not call `previewMultiAgentWorkflow`.

### Logging

`LOG_LEVEL` controls verbosity. Never log brief text (log `brief_len`), prompts, event arrays, compositions, or secret values.

- INFO at module load: resolved `AUTONOMOUS_MAX_AGENT_OPERATIONS` and `AUTONOMOUS_MAX_RUNS_PER_PROJECT`
- INFO on compile: `duration_bars`, section count, stage count, opening key
- INFO on stage transition: `run_id` prefix (16), `stage_id`, `status`, `failure_code`, `budget_code`
- DEBUG: dependency check pass/fail as stage ids only
- WARNING: budget stop and constraint failure, codes only
- ERROR: unexpected exception type name only

## Commit Plan
- **Commit 1** (after tasks 1–2): "feat: add the autonomous project plan and run store"
- **Commit 2** (after tasks 3–4): "feat: plan autonomous stages and generate the first score"
- **Commit 3** (after tasks 5–6): "feat: revise, arrange, and express within autonomous constraints"
- **Commit 4** (after tasks 7–8): "feat: run and recover an autonomous composition"
- **Commit 5** (after task 9): "docs: describe the autonomous project composer"

## Tasks

### Phase 1: Plan contract and store
- [x] Task 1: Add the brief, project plan, and deterministic compiler
- [x] Task 2: Persist runs and stages

### Phase 2: Agents and the first score
- [x] Task 3: Teach the director, harmony, and motif agents to emit plans without notes
- [x] Task 4: Compile `composition.plan.v1`, generate symbolically, bind Theme A, and commit

### Phase 3: Critique through expression
- [x] Task 5: Critique and targeted revision that keep the last good revision
- [x] Task 6: Arrangement and expression realizes with hard checks

### Phase 4: Loop, UI, docs
- [x] Task 7: HTTP loop, budgets, cancel, resume, and optional render
- [x] Task 8: Agents-tab brief, stage status, and project reload
- [x] Task 9: Document the workflow and add the roadmap milestone

### Task 1: Add the brief, project plan, and deterministic compiler

**Deliverable:** `creative.brief.v1` and `project.plan.v1` validate, and `compile_project_plan` turns the example brief into the 45-bar, nine-stage plan. No SQLite and no agent calls.

**Files:**
- `backend/app/autonomous_composer_schemas.py` (new)
- `backend/app/autonomous_composer_settings.py` (new)
- `backend/app/services/autonomous_project_plan.py` (new)
- `backend/tests/test_autonomous_project_plan.py` (new)

**Behavior:**
- Implement the field rules, bar math, beat→section table, and largest-remainder split. The example brief locks intro 7, theme 9, tension 9, climax 11, outro 9.
- `merge_director_text(compiled, director_plan)` keeps compiled structure and copies only goal and narrative strings. A director plan that drops `symbolic` still yields nine stages.
- Unknown instruments and a forbidden family named in `instrumentation` fail at compile time with `autonomous_instrument_unknown` / `autonomous_constraint_failed`.
- Settings loader matches `operation_budget_settings.py`: bad env falls back to the default and logs the key.

**LOGGING REQUIREMENTS:**
- Log module load at INFO with the two numeric defaults.
- Log compile at INFO with `duration_bars`, `section_count`, `stage_count`, and `opening_key`.
- Log a rejected director structural field at DEBUG as the field name only.
- Log settings parse failure at WARNING with `env_key` and `error_type`.
- Do not log narrative text. Levels follow `LOG_LEVEL`.

**Depends on:** none

### Task 2: Persist runs and stages

**Deliverable:** Migration `20260926_0012` and a store that inserts a run plus nine `pending` stages, reconciles `running`, and cascades on project delete.

**Files:**
- `backend/app/db/alembic/versions/20260926_0012_autonomous_runs.py` (new)
- `backend/app/services/autonomous_composer_store.py` (new)
- `backend/tests/test_autonomous_composer_store.py` (new)

**Behavior:**
- DDL matches the contract. Secret-like brief JSON is rejected with `persistence_secret_rejected` and is not written.
- `reconcile_interrupted_stages` implements the revision-id rule from the Restart section.
- Over cap returns `autonomous_run_limit` without deleting rows.
- The store does not import `ai_agents` and does not write `composition_json`.

**LOGGING REQUIREMENTS:**
- Log insert at INFO with `run_id` prefix, `project_id`, and `stage_count`.
- Log reconcile at INFO with the stage id and the new status.
- Log secret rejection at WARNING with the code only.
- Do not log `brief_json` or `plan_json` bodies.

**Depends on:** Task 1

### Task 3: Teach the director, harmony, and motif agents to emit plans without notes

**Deliverable:** With `compiled_project_plan` on the selection, the three agents return the typed plan artifacts and a null draft update. Spine `propose` behavior and `agent.workflow_plan.v1` stay.

**Files:**
- `backend/app/ai_agents/schemas.py` (`AGENT_CONTENT_TYPES` gains `project.plan.v1`)
- `backend/app/ai_agents/agents/common.py` (`SUPPORTED_OPS` for `harmony` and `melody_motif` gain `AgentOperation.PLAN`)
- `backend/app/ai_agents/agents/creative_director.py`
- `backend/app/ai_agents/agents/harmony.py`
- `backend/app/ai_agents/agents/melody_motif.py`
- `backend/app/ai_agents/fake_agents.py` (the same operation map)
- `backend/app/ai_agents/artifact_schemas.py` (register `project.plan.v1` on `_CONTENT_TYPE_MODELS`)
- `backend/tests/test_autonomous_plan_agents.py` (new)
- Existing spine tests stay green (`backend/tests/test_ai_agents_workflow.py`)

**Behavior:**
- `BaseMusicAgent.run` rejects operations absent from `SUPPORTED_OPS` before `_run_impl`. Add `plan` on both the real map and the fake map, or the autonomous scheduler never reaches the plan body.
- Harmony and motif `PLAN` return before `preview_reharmonization` and `apply_motif_operation`. `working_draft_update` stays null. `PROPOSE` stays the spine path.
- `AgentArtifactV1` rejects `content_type` values outside `AGENT_CONTENT_TYPES`. Add `project.plan.v1` there and on `_CONTENT_TYPE_MODELS`. Kind is `AgentArtifactKind.PLAN`.
- Harmony plan key is the compiled opening key. `chord_events` has one row per section start, not one per bar. `PLAN_CHORD_EVENT_MAX` is 64 and `duration_bars` can be 512.
- Motif plan includes `Theme A` and the theme section label.
- Director without `compiled_project_plan` keeps today’s brief, form plan, and workflow plan.
- Fake and real adapters both honor the selection. Fake mode makes no model call.
- Scaffold construction, if the context requires a composition, lives in the service in Task 7. This task accepts a caller-supplied context and must not persist it.

**LOGGING REQUIREMENTS:**
- Log at INFO: `agent_id`, `operation`, and the emitted `content_type` list.
- DEBUG when `PLAN` skips realize.
- Do not log the draft or the brief intent text.

**Depends on:** Task 1

### Task 4: Compile `composition.plan.v1`, generate symbolically, bind Theme A, and commit

**Deliverable:** From a stored plan plus harmony and motif artifacts, the symbolic stage writes one `autonomous-stage` revision whose score passes the symbolic completion codes, or it writes nothing.

**Files:**
- `backend/app/services/agent_artifact_workspace.py` (`insert_durable`)
- `backend/app/services/autonomous_symbolic.py` (new, including `commit_autonomous_stage`)
- `backend/app/services/autonomous_constraints.py` (new)
- `backend/tests/test_autonomous_symbolic.py` (new)

**Behavior:**
- `compile_composition_plan` keeps brief instrument order `piano`, `cello`, `strings`. That assignment hits melody, bass, and harmony inside fake symbolic and does not append an instrument named `bass`.
- Call `ensure_plan_conforms` and `generate_symbolic_composition`. Unavailable backend fails the stage with the existing symbolic code and `recoverable=1` only when the failure is `symbolic_unavailable`. Integrity and constraint failures are `recoverable=0`.
- Match theme and outro by `start_bar` and `type`. Copy the theme cell into the outro with the same rhythm and relative pitches, then `verify_motif_identity`. Remap outro pitches only after that call. `final_section_mode_ok` checks scale membership and unchanged rhythm.
- `insert_durable` writes `retention_class=durable`, `expires_at` null, and no `revision_artifact_links` row. Do not call `insert_temporary`.
- `commit_autonomous_stage` re-reads the branch and passes that `working_version` and `head_revision_id` into `commit_durable_revision` with `operation_type=autonomous-stage`. A newer working version is allowed only when the fingerprint still equals the run head. A different fingerprint fails the stage with `project_revision_conflict` and leaves the previous revision. Agents do not call the store. Later stages (Tasks 5–7) call this helper.
- A second failing call after a successful commit leaves that revision as `head_revision_id`.

**LOGGING REQUIREMENTS:**
- INFO start/end with `model_id`, `seed`, `bar_count`, `note_count`, `failure_code`.
- WARNING on constraint failure with the completion code only.
- Do not log pitches or motif note lists. `LOG_LEVEL` still applies.

**Depends on:** Tasks 2 and 3

### Task 5: Critique and targeted revision that keep the last good revision

**Deliverable:** `run_targeted_revision_passes` revises only the critic’s targets, preserves events outside them, and a crashed or invalid pass does not replace the symbolic revision.

**Files:**
- `backend/app/ai_agents/revision_loop.py` (`run_targeted_revision_passes` only)
- `backend/app/services/autonomous_revision.py` (new)
- `backend/tests/test_autonomous_revision.py` (new)
- `backend/tests/test_revision_loop_controller.py` (existing spine loop behavior unchanged)

**Behavior:**
- Critic `approve` → revision stage `skipped`, no new revision.
- Critic `revise` → `build_revision_plan_from_findings` and `run_targeted_revision_passes`. Each target agent goes through `_run_agent_node`. Do not call `agent.run` directly. Pass cap is the tighter of the request, the revision mode, and `OPERATION_MAX_REVISIONS`.
- `assert_preserve_outside_targets` failure → stop `validation_failed_kept_last_valid`, no commit, symbolic head unchanged.
- `SystemExit` is caught in `_run_agent_node` and becomes `operation_model_crashed` on the stage. The process stays up. Cover with a fake agent that raises `SystemExit`, then `GET /ready` on the same app. The scripted revise case lives here (`fake_revise_hard_finding` / `FAKE_CRITIC_REVISE_PASSES`). The Task 7 happy path leaves that unset.
- Subjective findings do not by themselves schedule a revise (existing rule).

**LOGGING REQUIREMENTS:**
- INFO per pass: `pass_index`, `target_agent_ids`, `stop_reason`, `budget_code`.
- WARNING when preserve fails, fingerprint prefix only.
- Do not log findings text over 64 characters; log `findings_count`.

**Depends on:** Task 4

### Task 6: Arrangement and expression realizes with hard checks

**Deliverable:** Arrangement runs `ArrangementAgent` `propose` with `preserve_melody`. Expression changes dynamics and velocity only. Either failure leaves the previous revision in place.

**Files:**
- `backend/app/services/composition_expression.py` (new)
- `backend/app/ai_agents/progressive_realize.py` (`EXPRESSION_CANDIDATE`)
- `backend/app/ai_agents/agents/stubs.py` and a real `performance_expression` module (replace the stub factory for that id only)
- `backend/app/services/autonomous_expression.py` (new)
- `backend/tests/test_autonomous_arrangement_expression.py` (new)

**Behavior:**
- Arrangement and expression commits call `commit_autonomous_stage` so the CAS fields are re-read.
- Arrangement request sets `preserve_melody` and does not add instruments in `forbidden_instrument_families`. Post-check uses `arrangement_preserved` and `no_forbidden_instruments`.
- Expression realize matches the velocity table and dynamic marks. Pitch fingerprint mismatch fails the stage with `expression_pitch_stable` and does not commit.
- Single-agent `advise` without `apply_expression` still returns only `agent.performance_plan.v1`.
- Fake arrangement that would add drums must fail the check in the test and leave the pre-arrangement fingerprint as head.

**LOGGING REQUIREMENTS:**
- INFO: stage id, `changed_note_count` for velocity edits, `dynamic_mark_count`.
- WARNING on pitch drift: fingerprint prefixes, no event JSON.
- DEBUG: section id to density band.

**Depends on:** Task 5

### Task 7: HTTP loop, budgets, cancel, resume, and optional render

**Deliverable:** `POST /ai/agents/autonomous/runs` with the example brief, `LLM_FAKE_MODE=1`, and rendering off finishes `completed`, creates a project, and leaves an editable head revision. A forced expression failure keeps the arrangement revision. Resume continues an interrupted stage. The agent-op cap stops before the next agent.

**Files:**
- `backend/app/services/autonomous_composer.py` (new)
- `backend/app/routers/ai_agents.py`
- `backend/tests/test_autonomous_composer_e2e.py` (new)
- `backend/tests/test_autonomous_composer_limits.py` (new)

**Behavior:**
- Order is the project-plan graph. Do not call `run_spine_workflow`.
- Every score commit goes through `commit_autonomous_stage` from Task 4.
- Scaffold for the director is not committed. First `head_revision_id` appears at `symbolic`.
- The happy-path test leaves `FAKE_CRITIC_REVISE_PASSES` unset. `FakeCriticAgent` then approves, the revision stage is `skipped`, and the run is still `completed`. The forced-revise coverage stays in Task 5.
- Default `include_rendering=false` skips `render`. The approval test sets it true, observes `awaiting_approval`, then approve with the fake neural adapter stores a job id and does not change the composition fingerprint.
- Cancel sets the run and the observability cancel set. Completed stages stay completed.
- Counters persist. A new process can `resume` a `pending` stage (test by marking `symbolic` `running` with a null `revision_id`, reconciling, and resuming).
- 409 when the caller’s fingerprint does not match the project. 409 on the run cap.
- Response is `autonomous.run.v1` without composition bodies. `operation_summary` is attached from the open run span.

**LOGGING REQUIREMENTS:**
- INFO at run start: `run_id` prefix, `project_id`, `include_rendering`, `stage_count`.
- INFO at run end: `status`, `failure_code`, `budget_code`, `active_runtime_ms`, `agent_operation_count`.
- WARNING when a budget refuses the next stage.
- Do not log the request body.

**Depends on:** Tasks 2, 4, 5, and 6

### Task 8: Agents-tab brief, stage status, and project reload

**Deliverable:** The Agents tab can start a run, show stage status, cancel, resume, and approve or skip render. A completed composition-writing stage reloads the project into the editor.

**Files:**
- `frontend/src/components/AutonomousComposerPanel.jsx` (new)
- `frontend/src/components/MultiAgentPanel.jsx` (mount the panel)
- `frontend/src/utils/autonomousStageText.js` (new)
- `frontend/src/utils/autonomousStageText.test.js` (new, `node:test`, same runner as `operationSummaryText.test.js`)
- `frontend/src/api/musicApi.js`
- `frontend/src/store/musicStore.js`

**Behavior:**
- Mint `operation_run_id` before `fetch`, same as spine preview.
- Status line includes stage id and status. `data-testid="autonomous-stage-list"`.
- Spine preview buttons and Apply stay. Autonomous start does not post to `/ai/agents/workflows/preview`.
- On fingerprint change, fetch the project and replace the editor composition. Do not drop undo history from an unrelated manual edit that arrives after start; the 409 path shows the conflict and does not start.

**LOGGING REQUIREMENTS:**
- Client debug log: run id prefix, status, stage count. No brief text.
- Log API errors with the public `code` only.

**Depends on:** Task 7

**Verification:** Exercise Start against the local fake-mode API until the stage list reaches completed and the piano roll shows the new project. Then cancel a second run mid-flight and confirm the first project’s notes are still editable. If browser tools are unavailable, say so and rely on the pytest plus the node test.

### Task 9: Document the workflow and add the roadmap milestone

**Deliverable:** `docs/autonomous-composer.md` and the maps point at the brief → plan → agents → symbolic → critique → revision → arrangement → expression → optional render path. Roadmap gains an unchecked milestone. Check it only after Task 7’s tests pass.

**Files:**
- `docs/autonomous-composer.md` (new)
- `docs/multi-agent.md` (short pointer: spine preview does not commit; autonomous runs do, per stage)
- `docs/observability.md` (agent-op budget and the same `operation_run_id`)
- `AGENTS.md`
- `.ai-factory/ARCHITECTURE.md` (service owns the store; `ai_agents/` still must not)
- `.ai-factory/ROADMAP.md`
- `.env.example` (`AUTONOMOUS_MAX_AGENT_OPERATIONS`, `AUTONOMOUS_MAX_RUNS_PER_PROJECT`)

**Behavior:**
- State the non-goals: no giant prompt, no `composition.v4`, spine Apply unchanged, render opt-in, failed stages keep earlier revisions.
- Document the example bar split so the numbers are not rediscovered.
- Document one harmony chord per section, identity checked before the outro mode remap, and `insert_durable` as the artifact write.
- Do not mark the milestone checked in the same edit that introduces it unless Task 7 is already green.

**LOGGING REQUIREMENTS:**
- Docs only. No new log calls. Mention the codes operators will see: `autonomous_constraint_failed`, `autonomous_stage_interrupted`, `autonomous_agent_operation_budget`, `operation_cancelled`.

**Depends on:** Tasks 7 and 8
