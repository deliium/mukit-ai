# Implementation Plan: V4 Autonomous Composer Human Control

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-26
Planning depth: ultra

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra
- Refined: 2026-09-26 (`/aif-improve`, all findings applied)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request’s Guided-mode acceptance path and frontend tests
- Scope: the shipped autonomous scheduler stays the only scheduler. This plan adds a brief editor, a compiler preview before any agent runs, autonomy modes, musical checkpoints, pause/cancel/retry, reject-and-continue with a safe instruction, and revision open/branch actions. `project.plan.v1` structure stays immutable after compile
- Parent (reuse, do not fork): `.ai-factory/plans/v4-autonomous-project-composer.md` — nine-stage graph, `compile_project_plan`, `execute_autonomous_run`, durable revisions, render gate, Agents-tab panel. Default `autonomy_mode=autonomous` must keep that plan’s fake-mode end-to-end test completing with no new approvals
- Also reuse: `POST /projects/{id}/branches` and `POST /projects/{id}/revisions/{revision_id}/restore` (`.ai-factory/plans` history work already shipped). This plan does not add a second history graph

## Roadmap Linkage
Milestone: "V4 autonomous composer co-producer controls"
Rationale: "V4 autonomous project composer" is checked. The shipped run still executes the brief in one request and gates only the optional render. Brief review, mode checkpoints, and continuing after a rejected arrangement are a new milestone. The docs task adds it unchecked. Check it only after the Guided-mode pytest passes.

## Goal

The autonomous system behaves as a co-producer. The user writes a brief, reads the compiled project plan, then starts. Guided mode stops at musical checkpoints. The user can approve the theme, reject the arrangement, give a new instruction, and continue that stage. Earlier stages are not run again.

```text
brief editor
    │  POST /ai/agents/autonomous/plans  (compiler only)
    ▼
project.plan.v1 shown
    │  Start
    ▼
existing nine-stage scheduler
    │  mode gates after plan / harmony / symbolic / critique / arrangement
    ▼
awaiting_approval  — approve, or reject arrangement
    │  instruction on the arrangement stage
    ▼
retry that stage only
```

**Acceptance one-liner:** In Guided mode the user approves the theme, rejects the arrangement, submits “Keep the melody, but use a smaller string arrangement.”, and continues from the arrangement stage on the same run and project.

The visible board uses musical labels. Display order follows the stage graph so the current marker sits on the step the scheduler will do next:

```text
✓ Composition plan
✓ Harmony
✓ Theme A
✓ Critique
→ Arrangement
○ Final performance
```

The request’s sample puts Theme A above Harmony and Arrangement above Critique. That sample shows the kind of label and the check / current / pending marks. The board order in this plan follows execution, so Harmony is already done when Theme A is waiting, and Critique is already done when Arrangement is waiting.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Autonomy mode** | `guided`, `balanced`, or `autonomous`. Stored on `autonomous_runs`. It does not edit `project.plan.v1.stages[].approval` |
| **Checkpoint** | A pause after a finished stage, before the next stage starts. `checkpoint_id` on the run. The finished stage stays `completed` |
| **Pause** | A user stop at the next stage boundary. Run status `paused`. The in-flight stage is allowed to finish. It is not marked `failed` |
| **Cancel** | Existing behavior. The in-flight stage becomes `failed` with `operation_cancelled` and `recoverable=0`. The run becomes `cancelled` |
| **Reject** | Arrangement only. The arrangement revision stays in history. The working head returns to the previous score revision. The arrangement stage becomes retryable |
| **Instruction** | User text on one stage, max 500 characters (`ARRANGEMENT_MAX_INSTRUCTION_CHARS`). Shown back to the user. Never written to logs |
| **Musical step** | One row of the progress board. Several internal stages can feed one row |

## Non-goals

- Reordering `STAGE_IDS` or teaching `CreativeDirectorAgent` to schedule other agents
- Rewriting section bounds, keys, instruments, or completion codes from an instruction
- A checkpoint that rejects the theme, the form, or the critique and rewrites those stages
- Retargeting `autonomous_runs.branch_id` onto a fork
- Showing `operation_summary` spans, prompts, critique `explanation`, critique `evidence`, or any field named reasoning in the panel
- A Playwright journey. Acceptance is pytest with `LLM_FAKE_MODE=1` plus `node:test` helpers
- Changing default `autonomy_mode`. Omitted means `autonomous`
- Calling the director, or any model, from the plan-preview route
- `composition.v4`, `DATASET_ROOT`, or `ai_agents/` importing the store

## Approach Evaluation (locked)

### Part A — Where the plan is shown

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Start the run, then paint `plan_json`** | One route | Agents begin before the user has read the plan | **Reject** |
| **B. Call the director to draft the plan, then wait** | Goal sentences exist early | Spends a model call and writes a run before Start | **Reject** |
| **C. `compile_project_plan` on a preview route; Start runs the existing scheduler** | Structure is the same object the scheduler will lock. No project row until Start | Director goal sentences appear only after the plan stage | **Accepted** |

**Locked:** `POST /ai/agents/autonomous/plans` validates `creative.brief.v1` and returns `project.plan.v1`. It does not insert a run, create a project, or call `get_agent`. The panel keeps Start disabled until this response is on screen and the brief fields still match it. Start calls the existing `POST /ai/agents/autonomous/runs`, which compiles again. After the plan stage, the run view shows the merged goal and narrative strings.

### Part B — Where a checkpoint lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Flip `project.plan.v1` stage `approval` to `required`** | Uses the column the parent plan already has | That artifact is immutable after compile. Mode would fork the plan per user | **Reject** |
| **B. Mark the finished stage `awaiting_approval`** | Reuses the stage enum | The score is already committed. Status would say the stage is unfinished | **Reject** |
| **C. Run status `awaiting_approval` plus `checkpoint_id`; the finished stage stays `completed`** | Matches “approve the work you can see”. Render can use the same run status | One new column | **Accepted** |

**Locked:** The scheduler reads `autonomy_mode` from the run. `maybe_hold_checkpoint` runs inside the stage loop, immediately after a stage reaches `completed` or `skipped`, and before the next stage is set to `running`. When the mode requires that checkpoint, it sets `checkpoint_id`, sets the run to `awaiting_approval`, and returns from `execute_autonomous_run`. The next stage stays `pending`. After `project_plan_valid` passes, persist the merged plan on `plan_json` (secret guard included) before the form hold returns. Approve clears `checkpoint_id` and records `decision=approved` in one store write, then calls `execute_autonomous_run` again.

### Part C — Pause versus cancel

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Pause calls `mark_run_cancelled`** | Already implemented | The stage becomes a non-recoverable failure | **Reject** |
| **B. Abort the worker mid-agent** | Stops faster | Arrangement and expression are not interrupt-safe. A partial revision could commit | **Reject** |
| **C. Set `pause_requested` and honor it before the next stage starts** | The current stage finishes and commits as it does today. Cancel stays the hard stop | Pause is felt after the current stage, not inside it | **Accepted** |

**Locked:** `POST /ai/agents/autonomous/runs/pause` takes the client `operation_run_id` and resolves the row with `get_run_by_operation`. The start request stores that id before the response returns, and `autonomousRun` is still null while the request is in flight. The route sets `pause_requested=1` while status is `running`. Each stage boundary calls `get_run` on a new connection. The stage loop is not one transaction. When the flag is set, the loop clears it, sets status `paused`, leaves the next stage `pending`, and returns. A stage already inside `_run_agent_node` finishes first. Pause while status is `awaiting_approval`, `paused`, `completed`, `failed`, or `cancelled` returns the current view and does not change a checkpoint. Resume of `paused` calls `execute_autonomous_run`. Resume of `awaiting_approval` stays `409 autonomous_run_not_resumable` so Resume is not an implicit approve. Pause does not abort the start request and does not call `mark_run_cancelled`.

### Part D — Reject the arrangement and continue

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Delete the arrangement revision** | Head looks unchanged | History rewrite. Parent plan keeps earlier revisions | **Reject** |
| **B. Start a new run from the symbolic revision** | Isolated | Restarts plan, harmony, motif, and critique. Fails the acceptance line | **Reject** |
| **C. Restore the pre-arrangement revision as a child, mark arrangement retryable, resume the same run** | Same `run_id` and `project_id`. Earlier stage rows stay `completed` | The rejected arrangement remains in history | **Accepted** |

**Locked:** Reject is valid only when `checkpoint_id=arrangement`, the arrangement stage is `completed`, and `expression` is still `pending`. Load the branch with `_load_branch_command_state` and call `restore_revision` with `RestoreRevisionRequest` fields `branch_id`, `expected_active_branch_id`, `expected_working_version`, and `expected_head_revision_id`. The restored snapshot is the revision id on the previous score stage (`revision` if it committed, otherwise `symbolic`). A dirty draft (`working_fingerprint != head_snapshot_fingerprint`) returns 409 `project_revision_conflict`. On success, set `head_revision_id` and `composition_fingerprint` to the new child. Set arrangement `failed`, `failure_code=autonomous_stage_rejected`, `recoverable=1`, copy its old `revision_id` to `rejected_revision_id`, and clear `revision_id`. Clear `checkpoint_id`. Set the run to `paused`. Expression and render stay `pending`. The next retry runs arrangement only.

### Part E — What an instruction may change

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Append the sentence to a model prompt and rerun arrangement preview** | Flexible | Fake mode would grow an LLM path. Hidden prompt text is what the panel must not become | **Reject** |
| **B. Ignore the text and rerun the default reinstrument** | Easy | The example sentence would not change the string texture | **Reject** |
| **C. Closed interpreter plus the existing melody and forbidden-instrument checks** | The example has a visible effect. Unrecognized sentences still continue, with a warning code | Only a few sentences change the score | **Accepted** |

**Locked:** `interpret_arrangement_instruction` in `backend/app/services/autonomous_instruction.py`. Output is `thin_strings` or `unparsed`. `thin_strings` when the casefold text contains one of `smaller`, `thinner`, `reduce`, `less`, `sparse` and also `string`. Unsafe text is refused with HTTP 422 `autonomous_instruction_unsafe` before the column is written. Unsafe means any of: a phrase that adds a forbidden family (`add drums`, `with drums`, `include drums`, and the same shapes for other families on the brief); `new melody`, `replace the melody`, `change the melody`, `different melody`, `new theme`; a key label other than `opening_key` and `final_section_key` in the same sentence as `key` or ` in `. “Keep the melody, but use a smaller string arrangement.” is safe and parses as `thin_strings`.

`thin_strings` runs after `_run_agent_node` returns and before `commit_autonomous_stage`. `fake:symbolic-tiny` writes one note per bar at `bar_index * bar_ticks`, so a “second half of the bar” drop removes nothing. On the realized draft, drop events on non-melody, non-lead tracks whose `normalize_instrument(...).family` is `strings` when the bar index is odd. Keep at least one event on each such track. Cello stays (`family` `cello`). The melody fingerprint is the pre-stage fingerprint. `preserve_melody` and `no_forbidden_instruments` still gate the commit. `autonomous_instruction_unparsed` is a `warning_code` column, not a completion code, and is not passed to `assert_stage_completion`. Do not call `run_composition_arrangement_preview`.

Expression may store an instruction for display. It does not change pitches, `start_tick`, or `duration_ticks`. The existing `expression_pitch_stable` check stays.

## Contract

### Modes and checkpoints

| `checkpoint_id` | Holds after | Guided | Balanced | Autonomous |
|-----------------|-------------|--------|----------|------------|
| `form` | `plan` completed | hold | hold | continue |
| `harmony` | `harmony_plan` completed | hold | continue | continue |
| `motif` | `symbolic` completed | hold | hold | continue |
| `critique` | `critique` completed, before `revision` | hold | continue | continue |
| `arrangement` | `arrangement` completed, before `expression` | hold | hold | continue |
| `render` | existing render branch | follows `include_rendering` and `render_approval` | same | same |

`render` stays inside the render branch of `execute_autonomous_run`. When it waits, also set `checkpoint_id=render`. Existing `POST .../stages/render/approve` and `.../skip` remain. They clear `checkpoint_id`.

`skipped` revision (critic `approve`) does not create a critique hold by itself. The hold is after the critique stage, before revision starts, and only in Guided mode. Approving that checkpoint lets revision skip or run exactly as it does today.

Default on `AutonomousRunStartV1.autonomy_mode` is `autonomous`. Existing tests that omit the field keep a single uninterrupted run when rendering is off.

### Musical board

Pure function `musical_progress(stages, motif_label)` in Python and a twin in `frontend/src/utils/autonomousControl.js`. Labels:

| Step id | Label | Internal stages |
|---------|--------|-----------------|
| `composition_plan` | Composition plan | `plan` |
| `harmony` | Harmony | `harmony_plan` |
| `theme` | the brief’s `motif_label` (example: Theme A) | `motif_plan`, `symbolic` |
| `critique` | Critique | `critique`, `revision` |
| `arrangement` | Arrangement | `arrangement` |
| `performance` | Final performance | `expression`, `render` |

A step is `done` when every listed stage is `completed` or `skipped`. It is `failed` when any listed stage is `failed`. It is `current` when any listed stage is `running` or `awaiting_approval`, or when the run is `paused` or `awaiting_approval` and this is the first step that is not `done`. Otherwise `pending`.

Marks in the panel: done `✓`, current `→`, pending `○`, failed `!`. The list `data-testid="autonomous-stage-list"` renders these lines. Internal stage ids are not the row text.

### `autonomous.run.v1` additions

Keep composition bodies off this object.

| Field | Rule |
|-------|------|
| `autonomy_mode` | `guided` \| `balanced` \| `autonomous` |
| `checkpoint_id` | The six ids, or null |
| `pause_requested` | bool |
| `plan` | Public projection of the stored plan: goals (`id`, `summary`, `section_id`), sections (`id`, `type`, `label`, `start_bar`, `bar_count`, `density`, `key`, `narrative`), constraints (`opening_key`, `final_section_key`, `duration_bars`, `instruments`, `forbidden_instrument_families`, `motif_label`) |
| stage `decision` | `approved`, `rejected`, or null |
| stage `instruction` | The user’s text, or null. Max 500 |
| stage `rejected_revision_id` | Set when arrangement is rejected |
| stage `completion_code` | Already stored. Include it on the view |
| stage `warning_code` | Stored column. `autonomous_instruction_unparsed` or null. Not a completion code |

`operation_summary` may stay on the JSON for operators. The panel does not render it.

### Artifact projection

`GET /ai/agents/autonomous/runs/{run_id}/artifacts`

One row per stage that has an artifact or a `revision_id`. Fields: `stage_id`, `artifact_id`, `content_type`, `revision_id`, `rejected_revision_id`.

Payload projection, and nothing else:

| Content type | Shown |
|--------------|--------|
| `project.plan.v1` | The same public plan projection |
| `agent.harmony_plan.v1` | `key`, chord-event count |
| `agent.motif_plan.v1` | motif label and section ids |
| `agent.critique.v1` | `recommendation`, and for each finding `code`, `severity`, `stratum`, `suggested_action`, bar range. Omit `explanation` and `evidence` |
| other | `content_type` only |

No note lists, no pitches, no prompts.

### Routes

| Method | Path | Effect |
|--------|------|--------|
| POST | `/ai/agents/autonomous/plans` | Compiler preview. 422 `autonomous_brief_invalid` / instrument codes. No SQLite write |
| POST | `/ai/agents/autonomous/runs` | Existing start. Optional `autonomy_mode` |
| POST | `/ai/agents/autonomous/runs/pause` | Body `{ "operation_run_id": "..." }`. Set `pause_requested` via `get_run_by_operation`. Unknown id is 404 |
| POST | `/ai/agents/autonomous/runs/{id}/resume` | Continue `paused` or a recoverable failure. 409 when `awaiting_approval` |
| POST | `/ai/agents/autonomous/runs/{id}/checkpoints/{checkpoint_id}/approve` | Clear the matching checkpoint and continue |
| POST | `/ai/agents/autonomous/runs/{id}/checkpoints/arrangement/reject` | Unwind the arrangement head and pause |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/retry` | `failed` and `recoverable=1` only. Not `operation_cancelled`. Sets that stage to `pending` and continues |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/instruction` | Body `{ "text": "..." }`. Stage id `arrangement` or `expression`. Catch `AutonomousPlanError` and return 422 `autonomous_instruction_unsafe`. 422 when the run is `running` |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/open` | Restore that stage’s revision as a child and adopt it. Allowed when the run is `paused`, `awaiting_approval`, or `completed`, and `stage.revision_id` equals `head_revision_id` |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/branch` | Body `{ "name": "..." }`. `create_branch` from `stage.revision_id`. Does not checkout. Does not change `branch_id` on the run |
| GET | `/ai/agents/autonomous/runs/{id}/artifacts` | Projection above |

Older render approve and skip routes stay.

Open on an older stage whose revision is not the run head returns 409 `autonomous_revision_not_head`. The client uses Branch for that stage.

Branch names use the existing branch-name rules (`BRANCH_NAME_MAX_LENGTH` 80).

### Status

Run status gains `paused`. Stage status enum is unchanged.

A run is `paused` only from the pause flag or from arrangement reject. A checkpoint uses `awaiting_approval`. Completed stages stay `completed` across pause, reject, and a later failure.

### Panel

`AutonomousComposerPanel.jsx` remains on the Agents tab, above the spine.

- Brief editor: title, duration, tempo min/max, meter, opening key, final key, motif label, mood, genre, 1..8 narrative beats (intent plus text, add and remove), instrument list, no-drums, theme-recognizable, include-rendering, autonomy mode
- Review plan, then Start. Start stays disabled until the preview matches the current brief
- Pause, Cancel, Resume, Retry on a recoverable failed stage. Pause posts `operation_run_id` and does not abort. Cancel keeps `globalThis.AbortController`
- Approve on the active checkpoint. Reject only for `arrangement`, with an instruction field
- Musical progress board
- Artifact and revision rows. Open when the revision is the head. Branch from this stage on any stage that has a `revision_id`
- Reload the project whenever a control response changes `composition_fingerprint`, including reject and open
- Spine preview buttons stay. Autonomous actions do not post to `/ai/agents/workflows/preview`

### Logging

`LOG_LEVEL` controls verbosity. Do not log brief text, instruction text, goal summaries, narrative, prompts, event arrays, or compositions. Log `brief_len` and `instruction_len`.

- INFO on preview: `duration_bars`, `section_count`, `opening_key`
- INFO when a checkpoint holds or clears: `run_id` prefix (16), `checkpoint_id`, `autonomy_mode`, decision `approved` or `rejected`
- INFO on pause and resume: `run_id` prefix, resulting status
- INFO on thin-strings: `run_id` prefix, `dropped_event_count`, `kept_event_count`
- WARNING on unsafe instruction and on `autonomous_instruction_unparsed`: codes only
- DEBUG: stage ids skipped because a checkpoint returned
- ERROR: exception type name only

## Commit Plan
- **Commit 1** (after tasks 1–2): "feat: add autonomous control fields and musical checkpoints"
- **Commit 2** (after tasks 3–5): "feat: pause, approve, and continue an autonomous run from one stage"
- **Commit 3** (after tasks 6–8): "feat: edit the brief and continue after a rejected arrangement"
- **Commit 4** (after task 9): "docs: describe autonomous co-producer controls"

## Tasks

### Phase 1: Contract and columns
- [x] Task 1: Add mode, checkpoint, and instruction contracts
- [x] Task 2: Persist mode, pause, checkpoint, and stage decisions

### Phase 2: Scheduler controls
- [x] Task 3: Preview the compiled plan and extend the run view
- [x] Task 4: Hold at musical checkpoints
- [x] Task 5: Pause, retry, reject, open, and branch

### Phase 3: Instruction and UI
- [x] Task 6: Apply a safe arrangement instruction
- [x] Task 7: Brief editor, plan review, and stage actions
- [x] Task 8: Frontend tests for the Guided path

### Phase 4: Docs
- [x] Task 9: Document the controls and add the roadmap milestone

### Task 1: Add mode, checkpoint, and instruction contracts

**Deliverable:** Mode, checkpoint ids, the musical-progress helper, and the instruction interpreter validate without SQLite and without agent calls. The example sentence parses as `thin_strings`. “Add drums” and “write a new theme” raise `autonomous_instruction_unsafe`.

**Files:**
- `backend/app/autonomous_composer_schemas.py`
- `backend/app/services/autonomous_instruction.py` (new)
- `backend/app/services/autonomous_progress.py` (new)
- `backend/tests/test_autonomous_instruction.py` (new)
- `backend/tests/test_autonomous_progress.py` (new)

**Behavior:**
- Add `AutonomyMode`, `CheckpointId`, and `autonomy_mode` on `AutonomousRunStartV1` defaulting to `autonomous`.
- Extend `AutonomousRunViewV1` and `AutonomousStageViewV1` with the fields in the contract. `extra=forbid` stays.
- `musical_progress` implements the six-step table. A fixture of nine stages with `plan`, `harmony_plan`, and `symbolic` completed yields Theme A `current` only when the run is waiting on `motif`. When those three are completed and the run is running `critique`, Critique is `current` and Theme A is `done`.
- Interpreter rules match Part E. Do not import `ai_agents` or the store.

**LOGGING REQUIREMENTS:**
- INFO when the interpreter returns `thin_strings`: `instruction_len` and `effect`.
- WARNING on unsafe: `code=autonomous_instruction_unsafe` and `instruction_len`.
- Do not log the instruction text. Levels follow `LOG_LEVEL`.

**Depends on:** none

### Task 2: Persist mode, pause, checkpoint, and stage decisions

**Deliverable:** Migration `20260926_0013` rebuilds the run and stage tables so status accepts `paused`, existing rows still read as `autonomous` with a null checkpoint, and stage rows expose instruction, decision, `rejected_revision_id`, and `warning_code`. `get_run` returns those fields.

**Files:**
- `backend/app/db/alembic/versions/20260926_0013_autonomous_control.py` (new)
- `backend/app/services/autonomous_composer_store.py`
- `backend/tests/test_autonomous_composer_store.py`

**Behavior:**
- `autonomous_stages` references `autonomous_runs`. Alembic runs inside `begin_transaction()` (`backend/app/db/alembic/env.py`), and SQLite will not change `PRAGMA foreign_keys` there. Do not `DROP TABLE autonomous_runs` while stages still point at it, and do not issue that pragma inside the migration.
- Copy order: create `autonomous_runs_new` and `autonomous_stages_new`, copy every row, `DROP TABLE autonomous_stages`, `DROP TABLE autonomous_runs`, rename the copies. Runs gain `autonomy_mode TEXT NOT NULL DEFAULT 'autonomous'`, `pause_requested INTEGER NOT NULL DEFAULT 0`, and `checkpoint_id TEXT`. Status CHECK adds `paused`. Stages gain nullable `instruction`, `decision`, `rejected_revision_id`, and `warning_code`. Stage status CHECK stays as it is.
- Add `paused` to `RUN_STATUSES`. Extend `AutonomousRunRecord`, `AutonomousStageRecord`, `_run_from_rows`, and `_stage_from_row` so the new columns are readable.
- `insert_run` writes `autonomy_mode`. Secret-like instruction text is rejected with `persistence_secret_rejected` and is not written.
- Store helpers: `set_pause_requested`, `set_checkpoint` (clears `checkpoint_id` and writes `decision` in one transaction), `set_stage_instruction`, `set_stage_warning`, `mark_stage_rejected`, and `replace_plan_json` (secret guard, then write). They do not import `ai_agents`.
- Existing runs from `20260926_0012` upgrade in the store test.

**LOGGING REQUIREMENTS:**
- INFO on migration with the revision id.
- INFO when a checkpoint is stored: `run_id` prefix and `checkpoint_id`.
- WARNING on secret rejection: the code only.
- Do not log `instruction` or `brief_json`.

**Depends on:** Task 1

### Task 3: Preview the compiled plan and extend the run view

**Deliverable:** `POST /ai/agents/autonomous/plans` with the example brief returns the 45-bar plan and does not create a project. `GET` run includes the public plan projection and omits composition bodies.

**Files:**
- `backend/app/services/autonomous_composer.py` (`run_view`, preview helper)
- `backend/app/routers/ai_agents.py`
- `backend/tests/test_autonomous_plan_preview.py` (new)

**Behavior:**
- Preview calls `compile_project_plan` only. Unknown instruments still return `autonomous_instrument_unknown`.
- `run_view` loads `plan_json` and maps the public fields. Until Task 4 persists the director merge, that JSON is the compiler plan. Stage view includes `decision`, `instruction`, `rejected_revision_id`, `completion_code`, and `warning_code`.
- `prepare_run` stores `autonomy_mode` from the start body.
- The existing end-to-end test in `backend/tests/test_autonomous_composer_e2e.py` stays green without sending `autonomy_mode`.

**LOGGING REQUIREMENTS:**
- INFO on preview: `duration_bars`, `section_count`, `opening_key`, `brief_len`.
- Do not log narrative lines or the response body.

**Depends on:** Tasks 1 and 2

### Task 4: Hold at musical checkpoints

**Deliverable:** A Guided run stops after `plan` with `checkpoint_id=form` and `harmony_plan` still `pending`. Approving `form` continues and stops on `harmony`. An Autonomous run with rendering off still finishes `completed` in one start request.

**Files:**
- `backend/app/services/autonomous_composer.py`
- `backend/tests/test_autonomous_checkpoints.py` (new)
- `backend/tests/test_autonomous_composer_e2e.py` (keep the default-mode assertion)

**Behavior:**
- After each completed stage, and before the next stage is set to `running`, `maybe_hold_checkpoint` uses the mode table. On hold, set the run to `awaiting_approval`, set `checkpoint_id`, and return from inside the loop. Do not mark the finished stage `awaiting_approval`.
- After `project_plan_valid` passes, call `replace_plan_json` with the merged plan before the form hold returns. Resume reloads that JSON. Structure stays the compiler’s stage ids and section bounds.
- `POST .../checkpoints/{checkpoint_id}/approve` requires an exact `checkpoint_id` match. One store write records `decision=approved` on the stage that just finished (`plan`, `harmony_plan`, `symbolic`, `critique`, `arrangement`, or `render`) and clears `checkpoint_id`. Then call `execute_autonomous_run`.
- Render wait also sets `checkpoint_id=render`. The existing render approve and skip routes clear it in that same write.
- Approving `motif` happens after `symbolic` has a `revision_id`. Approving `critique` runs the revision stage as it does today, including the skip when the critic says `approve`.
- Mismatched checkpoint id returns 409 `autonomous_run_not_resumable`.

**LOGGING REQUIREMENTS:**
- INFO on hold and on approve: `run_id` prefix, `checkpoint_id`, `autonomy_mode`.
- DEBUG lists the next stage id that was not started.
- Do not log plan text.

**Depends on:** Task 3

### Task 5: Pause, retry, reject, open, and branch

**Deliverable:** Pause stops at the next stage boundary without a failed stage. Rejecting arrangement restores the pre-arrangement head, and a later resume does not rerun `plan`, `harmony_plan`, `motif_plan`, `symbolic`, or `critique`. Open and branch use the existing history APIs.

**Files:**
- `backend/app/services/autonomous_composer.py`
- `backend/app/routers/ai_agents.py`
- `backend/tests/test_autonomous_composer_control.py` (new; the Guided acceptance test finishes in Task 6)

**Behavior:**
- `POST /ai/agents/autonomous/runs/pause` looks up `operation_run_id`. A missing row is 404. While status is `running`, set `pause_requested`. At the top of each stage, `get_run` on a new connection. Do not wrap the stage loop in one transaction. When the flag is set, clear it, set status `paused`, leave the next stage `pending`, and return. Resume allows status `paused` and a recoverable failure. Resume of `awaiting_approval` is 409. Add `paused` to the resume allow-list in `resume_autonomous_run`.
- Retry requires that stage `failed` and `recoverable=1`, and the run is not `cancelled`. `operation_cancelled` stays non-recoverable. Retry sets the stage to `pending` and calls `execute_autonomous_run`. Later completed stages are not reset.
- Reject matches Part D. Build `RestoreRevisionRequest` from `_load_branch_command_state`. A dirty draft returns 409 `project_revision_conflict`. Expression must still be `pending`.
- Open uses that same restore only when `stage.revision_id == head_revision_id` and the run is `paused`, `awaiting_approval`, or `completed`. Then set `head_revision_id` and `composition_fingerprint` to the new child. Other stages return 409 `autonomous_revision_not_head`.
- Branch calls `create_branch` with `from_revision_id=stage.revision_id`. Response is the branch id and name. `autonomous_runs.branch_id` is unchanged. Do not checkout inside this route.
- Cancel behavior stays: in-flight stage `failed`, `recoverable=0`, `operation_cancelled`. Pause does not abort the start request.

**LOGGING REQUIREMENTS:**
- INFO on pause, reject, retry, open, and branch: `run_id` prefix, `stage_id`, resulting run status. For reject, log the old and new revision id prefixes (16).
- WARNING when reject is refused because expression is not `pending`: code `autonomous_stage_not_rejectable`.
- Do not log composition JSON.

**Depends on:** Task 4

### Task 6: Apply a safe arrangement instruction

**Deliverable:** The Guided acceptance test passes. After reject, the instruction is stored, retry runs arrangement only, the melody fingerprint matches the pre-arrangement score, string accompaniment is thinner, and `plan` through `critique` stay `completed` with the same revision ids.

**Files:**
- `backend/app/services/autonomous_instruction.py`
- `backend/app/services/autonomous_expression.py` (`apply_arrangement_stage` accepts the stored instruction)
- `backend/tests/test_autonomous_composer_control.py`
- `backend/tests/test_autonomous_arrangement_expression.py`

**Behavior:**
- Instruction route validates length 1..500, stage id `arrangement` or `expression`. Catch `AutonomousPlanError` and return 422 `autonomous_instruction_unsafe` before write. Allowed while the run is `paused` or `awaiting_approval`. A `running` run is 422.
- On arrangement retry, read the stage instruction. Call the existing arrangement agent with `preserve_melody` true. After `_run_agent_node` returns, `thin_strings` drops odd-bar events on non-melody, non-lead tracks whose family is `strings`, keeping at least one event per such track. Commit that realized draft. Melody fingerprint is the pre-stage fingerprint. A regression that moves melody fails `arrangement_preserved` and does not commit.
- `unparsed` takes the current reinstrument path and sets `warning_code=autonomous_instruction_unparsed` through `set_stage_warning`. Do not pass that string to `assert_stage_completion`.
- Expression stores the text and still passes `expression_pitch_stable`.
- The acceptance test uses the example brief, `autonomy_mode=guided`, `LLM_FAKE_MODE=1`, rendering off. It approves `form`, `harmony`, `motif`, and `critique`, rejects `arrangement`, posts the example sentence, retries, and asserts one `run_id`, one `project_id`, unchanged earlier revision ids, a new arrangement `revision_id`, and a strictly smaller non-melody string-family event count than the rejected arrangement. Cello events are unchanged.

**LOGGING REQUIREMENTS:**
- INFO: `dropped_event_count`, `kept_event_count`, `instruction_len`, `effect`.
- WARNING when the effect is `unparsed`, code only.
- Do not log pitches or the sentence.

**Depends on:** Tasks 1 and 5

### Task 7: Brief editor, plan review, and stage actions

**Deliverable:** The Agents tab can edit a full brief, review the compiled plan, start in Guided mode, and show musical progress plus approve, reject, instruction, pause, retry, open, and branch.

**Files:**
- `frontend/src/components/AutonomousComposerPanel.jsx`
- `frontend/src/utils/autonomousControl.js` (new)
- `frontend/src/api/musicApi.js`
- `frontend/src/store/musicStore.js`
- `frontend/src/utils/autonomousStageText.js` (keep the helper; the board does not use it as the row label)

**Behavior:**
- Brief fields match the panel contract. Beat count stays within 1..8.
- Review plan calls the preview route. Start stays disabled until `briefFingerprint(brief)` equals the previewed brief. Changing a field clears the match.
- Musical board uses `musical_progress` and the marks in the contract. `data-testid="autonomous-stage-list"`.
- Pause posts `{ operation_run_id }` to `/ai/agents/autonomous/runs/pause` and does not abort the start request. Cancel still uses `globalThis.AbortController` and the cancel route once a run id exists. `startAutonomousComposer` sends `autonomy_mode` with the brief.
- Reject is shown only when `checkpoint_id` is `arrangement`. The instruction box is shown for arrangement and expression when the run is `paused` or `awaiting_approval`.
- Open calls the open route, then reloads the project. Reject and any other response that changes `composition_fingerprint` use that same reload. Branch calls the branch route and shows the returned name. It does not checkout by itself. A second control can checkout through the existing `checkoutBranch` helper; the panel then shows that the editor is on the fork while the run’s branch is unchanged.
- The panel does not render `operation_summary`, finding explanations, or evidence.
- Spine controls in `MultiAgentPanel.jsx` stay mounted.

**LOGGING REQUIREMENTS:**
- Client debug log: run id prefix, status, `autonomy_mode`, `checkpoint_id`, stage count. No brief text and no instruction text.
- Log API errors with the public `code` only.

**Depends on:** Tasks 3, 5, and 6

**Verification:** With the local API in fake mode, Review plan shows 45 bars before Start. Start Guided, approve through the theme checkpoint, reject arrangement, submit the example sentence, and retry. The board stays on the same run, Arrangement becomes current again, and the piano roll still has the earlier sections. Pause a Balanced run and confirm the in-flight stage is not `failed`. If browser tools are unavailable, say so and rely on the pytest plus the node tests.

### Task 8: Frontend tests for the Guided path

**Deliverable:** `node:test` covers the board, the preview gate, the instruction examples, and the action set for reject-then-continue. `npm test` in `frontend/` runs these files through the existing glob.

**Files:**
- `frontend/src/utils/autonomousControl.test.js` (new)
- `frontend/src/utils/autonomousStageText.test.js` (keep current cases)
- `frontend/src/api/autonomousApi.test.js` (new)

**Behavior:**
- Board fixture: plan, harmony, motif, and symbolic completed, arrangement `running` → Harmony and Theme A are `done`, Arrangement is `current`, Critique is `done` only if critique and revision are completed or skipped. Match the Python labels, including motif label `Theme A`.
- `briefFingerprint` changes when the instruction-relevant brief fields change, and Start’s pure `canStart(preview, brief)` is false until they match.
- Example sentence is allowed. `add drums` and `new theme` are refused by the client checker. The server test in Task 6 remains the authority.
- Given a view with `checkpoint_id=arrangement` and earlier stages `completed`, the enabled actions include reject and instruction, and they do not include a control that restarts `plan`.
- API test follows `frontend/src/api/projectApi.test.js`: preview, pause by `operation_run_id`, checkpoint approve, arrangement reject, instruction, and retry hit the paths in the contract.

**LOGGING REQUIREMENTS:**
- No new loggers. If a test spies on the client logger, it asserts the instruction string is absent.

**Depends on:** Task 7

### Task 9: Document the controls and add the roadmap milestone

**Deliverable:** `docs/autonomous-composer.md` describes modes, the preview route, pause versus cancel, reject-and-continue, and the fields the panel withholds. Roadmap gains an unchecked milestone. Check it only after Task 6’s pytest passes.

**Files:**
- `docs/autonomous-composer.md`
- `docs/project-persistence.md` (one paragraph: branch-from-stage calls the existing branch route and does not move the run)
- `AGENTS.md`
- `.ai-factory/ARCHITECTURE.md` (mode and checkpoint live on the run; `ai_agents/` still does not import the store; arrangement receives the instruction through the service)
- `.ai-factory/ROADMAP.md`

**Behavior:**
- State that default mode is `autonomous` and that the nine-stage graph is unchanged.
- Document the Guided acceptance sentence and that melody and forbidden families still hold.
- Name the withheld fields: prompts, span lists, critique explanation, critique evidence.
- Do not mark the milestone checked in the same edit that introduces it unless Task 6 is already green.

**LOGGING REQUIREMENTS:**
- Docs only. No new log calls. Mention codes operators will see: `autonomous_instruction_unsafe`, `autonomous_instruction_unparsed`, `autonomous_stage_rejected`, `autonomous_stage_not_rejectable`, `autonomous_revision_not_head`.

**Depends on:** Tasks 6, 7, and 8
