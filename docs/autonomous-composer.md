# Autonomous project composer

One creative brief schedules the existing specialized agents and leaves an editable `composition.v2` project. The user does not invoke harmony, motif, critique, revision, arrangement, and expression as separate tools.

The Agents-tab spine (`POST /ai/agents/workflows/preview`) stays a session preview. It does not commit. Autonomous runs commit a revision after each stage that changes the score.

```text
creative.brief.v1
    │  deterministic compiler locks structure
    ▼
creative_director.plan          → project.plan.v1
    ├ harmony.plan              → agent.harmony_plan.v1
    ├ melody_motif.plan         → agent.motif_plan.v1
    ▼
generate_symbolic_composition   → composition.v2
    ▼
critic.critique
    ▼
targeted revision
    ▼
arrangement.propose
    ▼
performance_expression          → dynamics / velocity only
    ▼
optional render (off unless requested)
```

## Non-goals

- One model call that emits the score, the plan, and the stage list
- A `composition.v4` schema, or writing `DATASET_ROOT`
- Changing the spine order or auto-applying Critic approve on the spine
- Starting neural renders from the spine. Rendering is an autonomous stage and stays off unless the request asks
- `ai_agents/` importing `agent_artifact_workspace`, `db`, or `autonomous_composer_store`. The service owns the store

A failed later stage keeps the previous head revision. The scaffold used to build the director context is discarded and is never a revision.

## Example

The compiler unit test uses a 2:30 instrumental brief: cold sparse opening, Theme A, tension, climax, quiet transformed ending; piano, cello, strings; no drums; Theme A recognizable; F# minor, final section toward F# major.

At 72 BPM and 4/4 that brief is **45 bars**: intro **7**, theme **9**, tension **9**, climax **11**, outro **9**. Weights are `0.15, 0.20, 0.20, 0.25, 0.20` with largest-remainder rounding.

Harmony emits one chord event per section start, not one per bar. Motif identity is checked on the rhythmic copy **before** the outro mode remap. The outro pitches are then snapped onto the final key. `composition.key` stays the opening key.

Durable artifacts use `insert_durable` (`retention_class=durable`, `expires_at` null, no `revision_artifact_links` row). Stage status lives on `autonomous_stages`, not inside the plan artifact.

## Routes

| Method | Path | Effect |
|--------|------|--------|
| POST | `/ai/agents/autonomous/plans` | Compiler preview only. No run, no project, no model call |
| POST | `/ai/agents/autonomous/runs` | Start. Optional `autonomy_mode` (`guided`, `balanced`, `autonomous`). Response is `autonomous.run.v1` and has no composition body |
| GET | `/ai/agents/autonomous/runs/{id}` | Reconcile a stage left `running`, then return the view |
| POST | `/ai/agents/autonomous/runs/pause` | Body `{ "operation_run_id" }`. Sets `pause_requested` while the run is `running`. The in-flight stage finishes; the next stage stays `pending` and the run becomes `paused` |
| POST | `/ai/agents/autonomous/runs/{id}/cancel` | Cancel. The in-flight stage fails with `operation_cancelled` and is not recoverable |
| POST | `/ai/agents/autonomous/runs/{id}/resume` | Continue a `paused` run or a recoverable failure. `awaiting_approval` is `409 autonomous_run_not_resumable` |
| POST | `/ai/agents/autonomous/runs/{id}/checkpoints/{checkpoint_id}/approve` | Clear that checkpoint and continue. The finished stage stays `completed` |
| POST | `/ai/agents/autonomous/runs/{id}/checkpoints/arrangement/reject` | Restore the previous score revision as a child, mark arrangement retryable, and pause the same run |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/retry` | Re-queue one `failed` stage with `recoverable=1`. Not `operation_cancelled`. Later completed stages stay completed |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/instruction` | Store user text on `arrangement` or `expression` (max 500). Refused while the run is `running` |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/open` | Restore that stage’s revision as a child when it is the run head |
| POST | `/ai/agents/autonomous/runs/{id}/stages/{stage_id}/branch` | Create a branch from that stage’s revision. Does not checkout and does not change the run’s branch |
| GET | `/ai/agents/autonomous/runs/{id}/artifacts` | Public artifact projection. No note lists, pitches, prompts, critique `explanation`, or `evidence` |
| POST | `/ai/agents/autonomous/runs/{id}/stages/render/approve` | Enqueue the neural job when the stage is `awaiting_approval` |
| POST | `/ai/agents/autonomous/runs/{id}/stages/render/skip` | Skip that render and finish the run |

The client mints `operation_run_id` before the start request. Pause posts that id and does not abort the start request. Model calls and an optional render share that id. See [observability](observability.md).

Omitted `autonomy_mode` is `autonomous`. The nine-stage graph is unchanged. `project.plan.v1` stays immutable after compile. `autonomy_mode` and `checkpoint_id` live on the run. Guided mode holds after the plan, harmony, theme, critique, and arrangement. In Guided mode the user can approve the theme, reject the arrangement, send “Keep the melody, but use a smaller string arrangement.”, and continue from the arrangement stage on the same run and project. Melody fingerprints and forbidden instrument families still gate the commit.

The panel shows musical labels (Composition plan, Harmony, the brief’s motif label, Critique, Arrangement, Final performance). It does not show `operation_summary`, prompts, critique `explanation`, critique `evidence`, or any reasoning field.

`include_rendering` defaults false, so the render stage is `skipped`. When rendering is on, `render_approval` defaults to `required` and the run waits at `awaiting_approval` with `checkpoint_id=render` before enqueue.

## Operators

| Env | Default | Effect |
|-----|---------|--------|
| `AUTONOMOUS_MAX_AGENT_OPERATIONS` | `24` | Stop before the next agent. `0` disables. A request may only lower it |
| `AUTONOMOUS_MAX_RUNS_PER_PROJECT` | `20` | `409 autonomous_run_limit` |

Codes you will see, without note text:

- `autonomous_constraint_failed` — a completion check refused the stage, so that stage did not commit
- `autonomous_stage_interrupted` — a `running` stage had no revision when the process stopped. Resume can retry it
- `autonomous_agent_operation_budget` — the next agent was not called
- `operation_cancelled` — the in-flight stage failed; earlier completed stages stay completed
- `autonomous_instruction_unsafe` — the instruction was refused before it was stored
- `autonomous_instruction_unparsed` — the text was stored as a warning and did not change the score
- `autonomous_stage_rejected` — the arrangement revision stays in history and the stage can be retried
- `autonomous_stage_not_rejectable` — reject was not on the arrangement checkpoint
- `autonomous_revision_not_head` — open was asked for a revision that is not the run head. Branch that stage instead

A fingerprint mismatch against the project is `409 project_revision_conflict`. The previous revision stays head.
