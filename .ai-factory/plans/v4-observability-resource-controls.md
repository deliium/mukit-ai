# Implementation Plan: V4 Observability and Resource Controls

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-25
Planning depth: full

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full
- Refined: 2026-09-25 (`/aif-improve`, all findings applied)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request’s “include tests and documentation”
- Scope: shared operation IDs and structured spans for autonomous multi-agent runs, agent calls, model calls, and neural render jobs; configurable model-call / token / wall / render-attempt budgets; cancellation that reaches in-flight children; crash containment so one model `SystemExit` leaves the API process up. Diagnosis is the span log plus the response summary
- Parent (reuse, do not fork): `.ai-factory/plans/controlled-critique-revision-loops.md` — shipped `RevisionLoopBudgets`, `usage_status`, `cancel_check`, stop reason `resource_budget_exhausted`, and the rule that currency is never invented. This plan adds an outer operation span and call-count / render-attempt caps. It keeps revision modes, pass caps, and last-valid behavior

## Roadmap Linkage
Milestone: "V4 observability and resource controls"
Rationale: Every current roadmap milestone is checked, including multi-agent architecture and controlled revision loops. Those features log durations and token usage inside one workflow, but they do not share a run id with model calls and render jobs, and they do not enforce a model-call ceiling. The docs task adds this milestone unchecked. Check it only after the acceptance tests pass.

## Goal

A failed autonomous composition run can be diagnosed from structured operation traces and cannot exceed configured model-call or revision budgets.

An autonomous run is `POST /ai/agents/workflows/preview` (the spine, including its revision loop). A single-agent call is `POST /ai/agents/{id}/run`. The client may send `operation_run_id` before the work starts so in-flight renders can share it. When the field is absent the server mints a UUID. Model calls on that request task inherit the run. Neural render and stem-set jobs carry the run id only when the caller passes it. Cancelling the run cancels those children where the process can see them. `SystemExit` from an agent `run` or from a model invoke is recorded as a failed span and does not exit Uvicorn.

```text
POST /ai/agents/workflows/preview
    │  run_id (uuid)
    ▼
operation span: run
    ├── agent span (one per agent.run)
    │     └── model span (chat / local HTTP / fake)
    └── render span (neural job or stem set whose operation_run_id == run_id)

span close → INFO extra= operation fields (redacted)
response  → operation.summary.v1 (small; no composition, no prompt)
```

## Terminology lock

| Term | Meaning |
|------|---------|
| **Run** | One workflow preview or one single-agent HTTP call. Identified by `run_id` (UUID). The client may supply it; otherwise the server mints it. Distinct from the constant `AGENT_SPINE_WORKFLOW_ID` |
| **Agent call** | One `agent.run`. Identified by `agent_call_id`. Parent is the run |
| **Model call** | One completion or local inference invocation. Identified by `model_call_id`. Parent is the agent call when one is open, otherwise the run |
| **Render job** | Existing `neural_audio_renders.id` or `neural_audio_stem_sets.id`. Child of a run only when `operation_run_id` is set |
| **Span** | One `operation.span.v1` record: ids, kind, status, timing, usage, retries, public failure code |
| **Summary** | `operation.summary.v1` returned on the workflow and single-agent responses and shown in the Agents panel |
| **Budget** | An env ceiling checked before the next child starts. The tighter of this ceiling and the existing revision-loop budget wins. A request cannot raise the env ceiling |

## Non-goals

- OpenTelemetry, Jaeger, or a traces table in SQLite
- Inventing USD when the provider omits cost
- Logging prompts, API keys, PCM, or composition payloads at any level
- `composition.v4`, `DATASET_ROOT` writes, or new `AiOperation` values
- A subprocess or container sandbox for in-process torch. Native segfaults stay a documented residual
- Killing a MusicGen / llama.cpp / vLLM sidecar to cancel one job. Other jobs share that process
- Starting neural renders from the spine. Correlation is opt-in via `operation_run_id`
- Changing Fast / Balanced / Thorough pass caps (1 / 2 / 3) or auto-Apply on Critic approve
- Cancelling neural jobs whose `operation_run_id` is null or belongs to another run

## Approach Evaluation (locked)

### Part A — Where traces live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New SQLite traces table** | Survives process restart | `ai_agents/` must not import SQLite; composition-sized payloads become a storage risk | **Reject** |
| **B. ContextVar spans + structured logs + response summary** | Matches today’s logging; works across await on the same task; diagnosis from the response and container logs | Lost if the process dies before the span is logged | **Accepted** |
| **C. Only the existing `usage` object** | Already on the preview response | No shared id for model calls or renders, no model-call count | **Reject** as the whole design. Keep it; the summary sits beside it |

**Locked:** `backend/app/operation_trace.py` owns a `ContextVar` of a mutable span object. It imports nothing from `ai_agents`, `ai_runtime`, `services`, `db`, or FastAPI. Routers set the run. Agent and model code on the same asyncio task open child spans by reading the current parent. Each span is validated against `operation.span.v1` at close and then logged.

`run_neural_audio_job` executes engine work on a `ThreadPoolExecutor`, so that worker does not see the request `ContextVar`. Render spans read `run_id` from the job column `operation_run_id`. A cancelled run is recorded in a process-wide set guarded by a lock (max 256 ids), which the worker can read. `parent_span_id` on a render span may be null when the render starts after the run span has closed. `run_id` is the join key.

Nullable columns `operation_run_id` and `attempt_count` on the neural tables are the only durable correlation. Alembic revision `20260925_0011`, `down_revision = "20260925_0010"`.

```sql
ALTER TABLE neural_audio_renders ADD COLUMN operation_run_id TEXT;
ALTER TABLE neural_audio_renders ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE neural_audio_stem_sets ADD COLUMN operation_run_id TEXT;
ALTER TABLE neural_audio_stem_sets ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
```

Job `status` stays `queued | running | failed | complete`. Cancellation is `status=failed` and `error_code=operation_cancelled`. Do not rebuild the CHECK constraint.

### Part B — Span schema

`operation.span.v1` (Pydantic, `extra=forbid`) in `backend/app/operation_trace_schemas.py`:

| Field | Rule |
|-------|------|
| `run_id`, `span_id` | UUID strings, max 64 |
| `parent_span_id` | Null on the run span |
| `kind` | `run \| agent \| model \| render` |
| `status` | `ok \| failed \| cancelled \| budget_exceeded` |
| `duration_ms` | Wall time of the span |
| `agent_id` | Set on agent spans. Truncate to 64 |
| `model_id`, `runtime` | Public descriptor fields. Runtime is the existing id (`fake`, `local_openai_compatible`, remote chat, and so on) |
| `prompt_tokens`, `completion_tokens` | Null when the adapter does not report them |
| `usage_status` | Reuse `available \| partial \| unavailable`. Do not invent zeros |
| `local_inference_ms` | Set when `runtime` is local or in-process. Null for remote chat |
| `process_rss_kb`, `process_rss_delta_kb` | `resource.getrusage(RUSAGE_SELF).ru_maxrss` when the call succeeds. Linux reports kilobytes. Null on failure. This is process RSS, labeled as such |
| `gpu_allocated_bytes` | Read only when `torch` is already in `sys.modules` and a CUDA or HIP device answers. Otherwise null. Do not import torch to sample |
| `memory_status` | `available` when RSS was read, else `unavailable` |
| `retry_count` | Transport or render retries for this span. Schema-repair retries inside `llm_music_generator` stay on their existing counters |
| `revision_count` | Run span only. Number of revision passes actually executed |
| `failure_code` | Public `operation_*` or existing agent/runtime code. No traceback, no exception message if it could contain prompt text |
| `provider_reported_cost_micros` | Null unless the adapter returns a number. Never estimate |

`operation.summary.v1` on `AgentWorkflowPreviewResponse`. The same object is an optional field on `AgentRunResult` (`operation_summary`, default null) so existing agent constructors keep validating under `extra=forbid`. The single-agent route sets it on the HTTP response after `agent.run` returns. Internal spine results leave it null.

`run_id`, `status`, `duration_ms`, `model_call_count`, `revision_count`, `failure_count`, `retry_count`, `stop_reason`, `budget_code`, `runtimes` (at most 8 strings), `usage_status`, `prompt_tokens`, `completion_tokens`, `local_inference_ms`.

### Part C — What gets redacted

A helper `redact_log_fields(mapping)` runs before every span log:

- Drop keys whose full name is in `FORBIDDEN_SECRET_FIELD_NAMES` from `persistence_secret_guard`, or whose full name ends with `_api_key`. Match the name exactly. A substring search is forbidden because `token` would drop `prompt_tokens`, `completion_tokens`, and `max_prompt_tokens`. Duplicate the name tuple in `operation_trace.py` if importing `persistence_secret_guard` pulls in services, and test that the two lists stay aligned. Unit-test that the three token field names survive `redact_log_fields`
- Drop keys `prompt`, `messages`, `composition`, `events`, `audio`, `pcm`, `wav_bytes`, `api_key`
- Replace a string value longer than 256 characters with `{"redacted": true, "length": N}`
- Log fingerprint prefixes the way `edit_fingerprint_log_prefix` already does. Log `event_count` and `byte_size` for renders. Do not log instructions text above 64 characters; log `instructions_len`

INFO is the span line. DEBUG may add `span_kind` and id prefixes during open. ERROR logs `failure_code` and `error_type` only. `LOG_LEVEL` still controls verbosity via `configure_logging`. Do not switch the root formatter to JSON.

### Part D — Budgets

New `backend/app/operation_budget_settings.py`, same env-parse style as `revision_loop_settings.py`.

| Env | Default | Effect |
|-----|---------|--------|
| `OPERATION_MAX_MODEL_CALLS` | `32` | Hard stop before the next model span opens on an active run. `0` disables this ceiling |
| `OPERATION_MAX_RUNTIME_MS` | `180000` | Hard stop before the next agent, model, or render attempt. Request `max_wall_ms` may only lower it |
| `OPERATION_MAX_PROMPT_TOKENS` | empty (off) | Additional cap on summed reported prompt tokens. When usage is `unavailable`, this cap does not fire; model-call and runtime caps still do |
| `OPERATION_REMOTE_COST_MICROS` | empty (off) | Fires only after an adapter reports cost. Missing cost logs `cost_status=unavailable` and does not fail the run |
| `OPERATION_MAX_REVISIONS` | `8` | Ceiling passed into `resolve_max_passes`. Clamps downward. Cannot exceed `REVISION_API_ABSOLUTE_MAX_PASSES` |
| `NEURAL_AUDIO_MAX_ATTEMPTS` | `2` | Attempts per render or stem-set job, including the first. Range 1–5 |

Stop details reuse `RevisionStopReason.RESOURCE_BUDGET_EXHAUSTED` with `budget_code`:

- `operation_model_call_budget`
- `operation_runtime_budget`
- `operation_token_budget`
- `operation_cost_budget`
- `operation_revision_budget`
- `operation_render_attempt_budget`

Existing revision-loop `max_wall_ms` and `max_prompt_tokens` stay. `_budget_exhausted` consults the tighter value. `resolve_max_passes` takes `revision_ceiling: int | None = None` and, when that argument is omitted, loads `OPERATION_MAX_REVISIONS`. Tests pass the ceiling in explicitly so they do not depend on the ambient environment. A fake-mode thorough run under the defaults still finishes, because fake agents do not call the model gate. The model-call ceiling is proven by calling `reserve_model_call()` under an open run.

### Part E — Cancellation

The workflow route already passes `request.is_disconnected` into `cancel_check`. That probe runs between agents. A streaming model call does not await it. Extend cancellation as follows:

1. Preview and single-agent requests accept optional `operation_run_id`. Valid value: a UUID string, max 64. Anything else is HTTP 422 `operation_run_id_invalid`. When omitted, the server mints a UUID. The adopted id is the `run_id`.
2. The open run holds an `asyncio.Event`. The route starts a watcher task that polls `request.is_disconnected` until the run finishes, then cancels the watcher in `finally`. When the probe or the watcher reports disconnect, the event is set and `run_id` is added to the cancelled-run set.
3. `reserve_model_call`, `ainvoke_chat_text`, and `llm_composition_arrangement._invoke_structured_draft` check the event before the provider call. `ainvoke_chat_text` also checks between stream chunks. On cancel they stop without logging partial text or chunk bodies and return a typed `operation_cancelled` to the caller. `LocalLanguageModel.complete_text` is covered because it calls `ainvoke_chat_text`.
4. The revision loop’s existing last-valid behavior stays. Cancel between passes still yields stop reason `cancelled`.
5. `POST /ai/agents/{id}/run` takes `Request` and the same watcher. It currently has none.
6. The cancelled-run set is a module-level set with a lock, max 256 ids. It is not a `ContextVar`. The neural runner, before each attempt, checks that set. A queued or not-yet-started attempt with that `operation_run_id` is stored as `failed` / `operation_cancelled` and does not call the engine. An attempt already inside sidecar HTTP is abandoned: the span is `cancelled`, the late body is discarded, the sidecar process is left running.
7. Jobs with a null or different `operation_run_id` run as they do today.
8. The frontend `multiAgentAbortController.abort()` remains the user control. The preview action mints `operation_run_id` before `fetch` and sends it on the workflow body. Neural enqueue and stem-set enqueue send that same id while the preview is in flight. No second cancel button.

`KeyboardInterrupt` is not swallowed.

### Part F — Crash isolation

Heavy local models already talk HTTP from `LocalLanguageModel` and the MusicGen sidecar. A dead sidecar today leaves FastAPI up. Keep that boundary.

`routing.py` only resolves a model. It is not the crash boundary. Catch `SystemExit` in these four places:

- `_run_agent_node` in `revision_loop.py`, around `agent.run`
- `run_ai_agent` in `routers/ai_agents.py`, around `agent.run`
- `ainvoke_chat_text` in `llm_chat_client.py`
- `_invoke_structured_draft` in `llm_composition_arrangement.py`

Rules:

- Catch `SystemExit`. Record span status `failed` and `failure_code=operation_model_crashed`. Do not re-raise.
- Let `KeyboardInterrupt` propagate.
- Existing `Exception` handlers stay. They record the existing runtime or agent error code.
- The workflow records `failure_count` and returns the last valid candidate with `operation_summary.status=failed` when the spine cannot continue. The process stays up.
- A following `GET /ready` and project open still succeed.
- The crash test raises `SystemExit` from a fake agent `run`, then calls `GET /ready` on the same app. Fake agents do not call `routing.py`.

Do not add a worker process in this milestone.

### Part G — UI

`MultiAgentPanel` already shows stop reason. Under that hint, render one line from `operation.summary.v1`: duration, model calls, revisions, failures, budget code when set. `data-testid="multi-agent-operation-summary"`.

Store the object on the existing multi-agent fields in `musicStore` (`multiAgentOperationSummary`). No new slice and no persistence of the span tree. Apply / Discard behavior is unchanged. The summary is session display only.

`frontend/src/utils/operationSummaryText.js` formats the line so the component stays thin. Cover it with `node:test` in `operationSummaryText.test.js`, the same runner as `frontend/src/utils/pluginLifecycleUi.test.js`.

The preview action in `musicStore` mints `operation_run_id` before the workflow `fetch` and stores it on the existing multi-agent fields. While that preview request is in flight, neural render enqueue and stem-set enqueue include the same id. After the preview settles, later enqueues omit it.

## Commit Plan
- **Commit 1** (after tasks 1–2): "feat: add operation spans and autonomous-run summaries"
- **Commit 2** (after tasks 3–4): "feat: budget and cancel model calls from the active run"
- **Commit 3** (after tasks 5–6): "feat: cap render attempts and contain model crashes"
- **Commit 4** (after tasks 7–8): "docs: describe V4 operation traces and budgets"

## Tasks

### Phase 1: Trace contract
- [x] Task 1: Add the operation span schema, budget settings, and redacting logger
- [x] Task 2: Attach run and agent spans to the spine and the single-agent route

### Phase 2: Model calls
- [x] Task 3: Record model spans, enforce call and token budgets, and honor cancel mid-call
- [x] Task 4: Sample process RSS and optional GPU memory on span close

### Phase 3: Renders and isolation
- [x] Task 5: Correlate neural jobs with a run and enforce attempt and cancel limits
- [x] Task 6: Contain model `SystemExit` at the runtime boundary

### Phase 4: Summary and docs
- [x] Task 7: Show the operation summary on the Agents panel
- [x] Task 8: Document traces, budgets, cancellation, and the roadmap milestone

### Task 1: Add the operation span schema, budget settings, and redacting logger

**Deliverable:** `operation.span.v1`, `operation.summary.v1`, env budgets, and a ContextVar helper that logs a redacted span.

**Files:**
- `backend/app/operation_trace_schemas.py` (new)
- `backend/app/operation_budget_settings.py` (new)
- `backend/app/operation_trace.py` (new)
- `backend/tests/test_operation_trace.py` (new)
- `backend/tests/test_operation_budget_settings.py` (new)

**Behavior:**
- `operation_span(kind, **fields)` is an async context manager. It sets the ContextVar to a mutable span object, times the body, and logs on close including failures. The Pydantic model is built at close. A synchronous `with` is not the API, because the first `await` would close the span.
- Nested spans copy `run_id` and set `parent_span_id` from the current span.
- `current_run_id()` returns the open run id or null.
- `note_usage`, `note_retry`, and `note_failure` mutate the open span.
- `reserve_model_call()` checks the model-call ceiling, records the refusal on the run span when the ceiling is hit, and returns whether the caller may start a provider call. Task 3 is the production caller. Task 1 ships the helper and a unit test that trips the ceiling.
- `mark_run_cancelled(run_id)` adds the id to the locked process-wide set (max 256) and sets the run’s cancel event when that run is open.
- `build_summary(root_span, children)` fills `operation.summary.v1`.
- Invalid env values fall back to the defaults and log a warning with the key name only.
- `OPERATION_MAX_REVISIONS` above 8 is stored as 8.
- `redact_log_fields` drops exact secret field names and keeps `prompt_tokens`, `completion_tokens`, and `max_prompt_tokens`.

**LOGGING REQUIREMENTS:**
- Log module load at INFO with the resolved budget defaults (numbers only).
- Log each closed span at INFO through `redact_log_fields` with `operation_trace: true` plus the span fields listed above.
- Log rejected secret-like keys at DEBUG as key names only.
- Log budget parse failure at WARNING with `env_key` and `error_type`.
- Levels follow `LOG_LEVEL`. Do not log prompts, compositions, audio, or secret values.

**Depends on:** none

### Task 2: Attach run and agent spans to the spine and the single-agent route

**Deliverable:** Every workflow preview and single-agent run returns `operation_summary` and logs a run span whose children are agent spans. A scripted agent failure is diagnosable from the log record and the summary. Revision count on the run matches executed passes and cannot exceed `OPERATION_MAX_REVISIONS`.

**Files:**
- `backend/app/routers/ai_agents.py`
- `backend/app/ai_agents/schemas.py` (`AgentRunResult.operation_summary`, default null)
- `backend/app/ai_agents/workflow.py`
- `backend/app/ai_agents/revision_loop.py` (agent span inside `_run_agent_node`)
- `backend/app/ai_agents/revision_loop_schemas.py` (`resolve_max_passes(..., revision_ceiling: int | None = None)`)
- `backend/tests/test_ai_agents_routes.py`
- `backend/tests/test_revision_loop_controller.py`
- `backend/tests/test_operation_run_spans.py` (new)

**Behavior:**
- `AgentWorkflowPreviewRequest` and `AgentRunHttpRequest` accept optional `operation_run_id`. Invalid values return 422 `operation_run_id_invalid`. A missing value is replaced with a new UUID. `workflow_id` stays `AGENT_SPINE_WORKFLOW_ID`.
- `preview_agent_workflow` and `run_ai_agent` open a `run` span with that id and close it in a `finally` so a failure still logs. Each starts a disconnect watcher that polls `request.is_disconnected` and calls `mark_run_cancelled`. The watcher is cancelled in `finally`.
- `_run_agent_node` opens the `agent` span around `agent.run`. `run_ai_agent` opens an `agent` span around the same call. Do not wrap every agent class.
- `AgentWorkflowPreviewResponse` gains `operation_summary`. `AgentRunResult.operation_summary` defaults to null. The single-agent route assigns the summary on the object it returns. Spine-internal results stay null.
- `resolve_max_passes` applies `min(existing_cap, revision_ceiling)`. When `revision_ceiling` is omitted it loads `OPERATION_MAX_REVISIONS`. Existing tests pass an explicit ceiling or keep the default of 8 so current expectations hold.
- When the revision ceiling stops the loop, `stop_reason` is `resource_budget_exhausted` and `budget_code` is `operation_revision_budget` only if the env ceiling was the binding constraint. Mode caps still report `max_passes_reached`.
- The run span `revision_count` equals the number of revision passes executed. Summary `duration_ms` is the outer run span. `result.duration_ms` stays as it is.

**LOGGING REQUIREMENTS:**
- Keep today’s workflow INFO lines. Add `run_id` (full UUID is fine; it is not secret) on start and ready.
- Agent span close at INFO: `agent_id`, `duration_ms`, `status`, `run_id`.
- On failure, ERROR with `failure_code` and `error_type`. No composition dump.
- DEBUG: pass index and span id prefix.

**Depends on:** Task 1

<!-- Commit checkpoint: tasks 1–2 -->

### Task 3: Record model spans, enforce call and token budgets, and honor cancel mid-call

**Deliverable:** Provider calls opened under a run become model spans. The next call after `OPERATION_MAX_MODEL_CALLS` does not start. Disconnect stops an in-flight stream without logging chunk text. Last valid candidate behavior is unchanged. The ceiling is proven by `reserve_model_call()`, because fake agents never call a model.

**Files:**
- `backend/app/services/llm_chat_client.py` (`ainvoke_chat_text`)
- `backend/app/services/llm_composition_arrangement.py` (`_invoke_structured_draft`)
- `backend/app/ai_runtime/runtimes/local_language.py` (already calls `ainvoke_chat_text`; assert the span is present, do not add a second gate)
- `backend/app/ai_agents/revision_loop.py` (`_budget_exhausted` reads the run budget code)
- `backend/tests/test_operation_model_spans.py` (new)
- `backend/tests/test_llm_chat_client.py` or the nearest existing chat-client test

**Behavior:**
- If no run is open, `reserve_model_call()` returns allowed and does not count. `/generate` and `/edit` keep their existing retry limits.
- `ainvoke_chat_text` and `_invoke_structured_draft` call `reserve_model_call()` before the provider I/O. When it refuses, they do not call the provider, the run records `budget_code=operation_model_call_budget`, and the loop stops with `resource_budget_exhausted`.
- When a run is open, the allowed call opens a `model` span with `model_id` and `runtime`. `local_inference_ms` is set for `local_openai_compatible` and other local runtimes.
- `ainvoke_chat_text` reads `usage_metadata` on the last chunk (`input_tokens` / `output_tokens`, or `prompt_tokens` / `completion_tokens` when that is what the chunk exposes) and calls `note_usage`. The function still returns the concatenated string. Missing metadata leaves `usage_status=unavailable`.
- `_invoke_structured_draft` reads usage from the structured result when LangChain attaches it. Otherwise `usage_status` stays `unavailable`.
- Transport reconnects increment `retry_count` on that model span. `_STREAM_TRANSPORT_ATTEMPTS` stays 2.
- Summed `prompt_tokens` at or above `OPERATION_MAX_PROMPT_TOKENS` (when the env is set and tokens are known) sets `operation_token_budget`.
- Reported cost at or above `OPERATION_REMOTE_COST_MICROS` sets `operation_cost_budget`.
- `ainvoke_chat_text` checks the run cancel event before the request and between streamed chunks. Cancel closes the span as `cancelled` and does not put chunk text into logs or `error_detail`.
- `test_operation_model_spans.py` opens a run and calls `reserve_model_call` until `OPERATION_MAX_MODEL_CALLS` trips. It does not use a fake thorough preview for that assertion. A second test feeds a fake stream chunk with `usage_metadata` and checks the span token fields.

**LOGGING REQUIREMENTS:**
- INFO on model span close: `run_id`, `model_id`, `runtime`, `duration_ms`, `local_inference_ms`, `usage_status`, token counts, `retry_count`, `status`.
- WARNING when a budget refuses the next call: `budget_code`, `model_call_count`, `run_id`.
- INFO when a call is cancelled: `run_id`, `model_call_id`.
- Do not log prompt text, message bodies, or API keys. Existing client construction logs stay, still without the key.

**Depends on:** Tasks 1, 2

### Task 4: Sample process RSS and optional GPU memory on span close

**Deliverable:** Run and model spans include `process_rss_kb` and `process_rss_delta_kb` when `resource.getrusage` works, and `gpu_allocated_bytes` only if torch is already imported. Failures leave the fields null and `memory_status=unavailable`. Sampling never raises.

**Files:**
- `backend/app/operation_trace.py`
- `backend/tests/test_operation_trace.py`

**Behavior:**
- Sample at span enter and span close. Delta may be negative; store it as an int or null.
- GPU helper checks `sys.modules` for `torch` and then a CUDA or HIP device. Any exception becomes null and a DEBUG line with `error_type`.
- Do not add a `psutil` dependency.

**LOGGING REQUIREMENTS:**
- DEBUG when memory is unavailable, with `memory_status` and `error_type`.
- INFO span line includes the memory fields when present.
- Do not log device serial numbers or driver dumps.

**Depends on:** Task 1

<!-- Commit checkpoint: tasks 3–4 -->

### Task 5: Correlate neural jobs with a run and enforce attempt and cancel limits

**Deliverable:** Create-render and create-stem-set accept optional `operation_run_id`. The runner performs at most `NEURAL_AUDIO_MAX_ATTEMPTS` attempts and records a render span per attempt. A cancelled parent run prevents a not-yet-started attempt from calling the engine.

**Files:**
- `backend/app/db/alembic/versions/20260925_0011_operation_run_columns.py` (new)
- `backend/app/neural_audio_schemas.py`
- `backend/app/neural_audio_settings.py`
- `backend/app/services/neural_audio_render.py` (`run_neural_audio_job` and its thread pool)
- `backend/app/services/neural_audio_render_store.py` (render row read/write)
- `backend/app/services/neural_audio_stems.py` (stem-set row read/write)
- `backend/app/routers/neural_audio.py`
- `backend/tests/test_neural_audio_operation_budget.py` (new)
- `.env.example` keys for `NEURAL_AUDIO_MAX_ATTEMPTS` and the `OPERATION_*` variables (settings comments only; Task 8 writes the docs)

**Behavior:**
- Missing `operation_run_id` keeps current render behavior aside from the attempt cap.
- `attempt_count` increments per engine invocation and is returned on the job DTO.
- Attempt failures retry while `attempt_count < NEURAL_AUDIO_MAX_ATTEMPTS`. The next attempt after the cap sets `failed` and `error_code=operation_render_attempt_budget`.
- Each attempt opens a `render` span with `run_id` copied from the job column, `parent_span_id` null when no request span is current, `retry_count` equal to `attempt_count - 1`, `model_id`, `duration_ms`, and `byte_size` on success. No PCM and no instructions text. The worker must not rely on the HTTP task’s ContextVar.
- When the id is in the locked cancelled-run set, the job becomes `failed` / `operation_cancelled` before the engine call. A result that arrives after cancel is discarded and does not overwrite a cancelled row back to `complete`.
- Project delete still cascades these rows.

**LOGGING REQUIREMENTS:**
- INFO per attempt: `render_id` or stem-set id, `operation_run_id` prefix (12 chars), `attempt_count`, `status`.
- WARNING at the attempt cap: `budget_code=operation_render_attempt_budget`.
- INFO on cancel-before-start: `error_code=operation_cancelled`.
- Do not log prompt instructions, PCM, or file paths beyond the existing relative-path policy.

**Depends on:** Tasks 1, 2

### Task 6: Contain `SystemExit` around agent run and provider invoke

**Deliverable:** `SystemExit` from a fake agent `run` or from a provider invoke becomes `failure_code=operation_model_crashed`. The preview or single-agent response returns with `operation_summary.status=failed`. A later readiness request on the same app still succeeds. `KeyboardInterrupt` still propagates.

**Files:**
- `backend/app/ai_agents/revision_loop.py` (`_run_agent_node`)
- `backend/app/routers/ai_agents.py` (`run_ai_agent`)
- `backend/app/services/llm_chat_client.py`
- `backend/app/services/llm_composition_arrangement.py` (`_invoke_structured_draft`)
- `backend/tests/test_operation_model_crash.py` (new)

**Behavior:**
- Do not wrap `backend/app/ai_runtime/routing.py`. It does not invoke models.
- Catch `SystemExit` at the four call sites in Part F. Do not re-raise it. Leave `KeyboardInterrupt` uncaught.
- The test replaces one fake agent `run` so it raises `SystemExit`, calls the workflow preview or single-agent route, asserts `operation_model_crashed` on the summary or the span log, and then calls `GET /ready` on the same app instance.
- Built-in model descriptors remain registered after the failure.
- Sidecar connection errors stay on their existing codes (`local` transport classification). They already must not mark the whole app not-ready.

**LOGGING REQUIREMENTS:**
- ERROR: `failure_code=operation_model_crashed`, `error_type=SystemExit`, `run_id`, `model_id`.
- Do not log `SystemExit` code paths that include user prompt text.
- INFO: readiness after the failure still logs the existing ready line.

**Depends on:** Tasks 2, 3

<!-- Commit checkpoint: tasks 5–6 -->

### Task 7: Show the operation summary on the Agents panel

**Deliverable:** After a workflow preview, the Agents panel shows duration, model-call count, revision count, and failure count from `operation_summary`. A budget code is visible when present. Apply and Discard still work.

**Files:**
- `frontend/src/utils/operationSummaryText.js` (new)
- `frontend/src/utils/operationSummaryText.test.js` (new, `node:test`, same style as `pluginLifecycleUi.test.js`)
- `frontend/src/api/musicApi.js` (send `operation_run_id` on workflow preview, neural enqueue, and stem-set enqueue; pass `operation_summary` through)
- `frontend/src/store/musicStore.js` (mint `operation_run_id` before the preview fetch; `multiAgentOperationSummary` next to the existing stop-reason field)
- `frontend/src/components/MultiAgentPanel.jsx`

**Behavior:**
- The preview action creates a UUID, sends it as `operation_run_id`, and keeps it while the request is in flight.
- Neural enqueue and stem-set enqueue include that id only while the preview is in flight.
- Empty or missing summary renders nothing.
- Cancelled runs show the status word `cancelled` on the same line.
- The formatter accepts the DTO and returns one string. No network inside the util. The test file uses `node:test`.
- Do not store the span tree or the candidate inside the summary object.

**LOGGING REQUIREMENTS:**
- DEBUG in the panel when a summary is shown: `run_id` prefix (12), `status`, `model_call_count`, `revision_count`. Reuse the panel’s existing logger if it has one; otherwise a `console.debug` behind the same pattern as neighboring panels.
- Do not log the candidate composition.

**Depends on:** Task 2

### Task 8: Document traces, budgets, cancellation, and the roadmap milestone

**Deliverable:** Operators can configure the ceilings and can diagnose a failed run from the span fields. The roadmap lists the new milestone unchecked.

**Files:**
- `docs/observability.md` (new) — ids, span fields, redaction list, budgets, cancel propagation, crash residual for native faults, diagnosis steps for a failed preview
- `docs/multi-agent.md` — link and the summary field
- `docs/ai-runtime.md` — model span and `SystemExit` containment
- `docs/neural-audio-rendering.md` — `operation_run_id`, attempt cap, `operation_cancelled`
- `docs/CODEBASE_MAP.md` and `AGENTS.md` — one entry for `operation_trace.py` and the new doc
- `.env.example` — `OPERATION_MAX_MODEL_CALLS`, `OPERATION_MAX_RUNTIME_MS`, `OPERATION_MAX_PROMPT_TOKENS`, `OPERATION_REMOTE_COST_MICROS`, `OPERATION_MAX_REVISIONS`, `NEURAL_AUDIO_MAX_ATTEMPTS`
- `.ai-factory/ROADMAP.md` — unchecked milestone **V4 observability and resource controls**
- `.ai-factory/DESCRIPTION.md` — one line that autonomous runs emit a redacted operation summary and honor the env ceilings

**Behavior:**
- Docs state that token and cost figures appear only when the provider reports them.
- Docs state that sidecar processes are not killed on cancel.
- Docs state that `LOG_LEVEL=INFO` is enough to see span close lines.

**LOGGING REQUIREMENTS:**
- Documentation describes the INFO/WARNING/ERROR fields from tasks 1–6.
- No sample log line may contain a fake API key, a prompt, or a composition body.

**Depends on:** Tasks 1–7

<!-- Commit checkpoint: tasks 7–8 -->

## Acceptance check

1. Run a fake-mode workflow preview that forces an agent failure. The response `operation_summary.run_id` matches `run_id` on the INFO span lines for the run, the agent, and any model call. The log record has no `composition` object and no secret key.
2. With an open run and `OPERATION_MAX_MODEL_CALLS=1`, the second `reserve_model_call()` is refused with `operation_model_call_budget` and does not perform provider I/O. Separately, `OPERATION_MAX_REVISIONS=1` keeps a thorough preview at one pass (`max_passes_reached` or `operation_revision_budget` as specified in Task 2). A fake thorough preview’s `model_call_count` of 0 is not the proof of the call ceiling.
3. The client sends `operation_run_id` and aborts the preview. A child model call in progress closes `cancelled`. A queued neural job created with that same id ends `failed` / `operation_cancelled` and does not call the engine. A job with another run id still runs.
4. A fake agent `run` that raises `SystemExit` returns a failed summary with `operation_model_crashed`. `GET /ready` on the same app still succeeds.
