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
| POST | `/ai/agents/autonomous/runs` | Start. Response is `autonomous.run.v1` and has no composition body |
| GET | `/ai/agents/autonomous/runs/{id}` | Reconcile a stage left `running`, then return the view |
| POST | `/ai/agents/autonomous/runs/{id}/cancel` | Cancel. Completed stages stay completed |
| POST | `/ai/agents/autonomous/runs/{id}/resume` | Continue a recoverable failed stage |
| POST | `/ai/agents/autonomous/runs/{id}/stages/render/approve` | Enqueue the neural job when the stage is `awaiting_approval` |
| POST | `/ai/agents/autonomous/runs/{id}/stages/render/skip` | Skip that render and finish the run |

The client mints `operation_run_id` before the start request. Model calls and an optional render share that id. See [observability](observability.md).

`include_rendering` defaults false, so the render stage is `skipped`. When rendering is on, `render_approval` defaults to `required` and the run pauses at `awaiting_approval` before enqueue.

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

A fingerprint mismatch against the project is `409 project_revision_conflict`. The previous revision stays head.
