# Operation traces and resource controls

A failed autonomous composition run is diagnosed from structured span logs and
the `operation.summary.v1` object on the response. There is no traces table and
no OpenTelemetry exporter.

## Runs

| Request | What it is |
|---------|------------|
| `POST /ai/agents/workflows/preview` | One autonomous run (spine, including its revision loop) |
| `POST /ai/agents/{id}/run` | One single-agent run |

The client may send `operation_run_id` (UUID string, at most 64 characters)
before the work starts. Anything else is HTTP 422 `operation_run_id_invalid`.
When the field is omitted the server mints a UUID. That id is `run_id`.

Child ids:

| Kind | Id | Parent |
|------|----|--------|
| Run | `run_id` | none |
| Agent call | `agent_call_id` / agent span | the run |
| Model call | `model_call_id` / model span | the open agent span, otherwise the run |
| Render job | `neural_audio_renders.id` or `neural_audio_stem_sets.id` | the run only when the job column `operation_run_id` is set |

`AGENT_SPINE_WORKFLOW_ID` is a constant workflow name, not a run id.

## What is logged

`backend/app/operation_trace.py` keeps a `ContextVar` span for the request
task. Each closed span is validated as `operation.span.v1` and logged at
**INFO** with `operation_trace: true`. `LOG_LEVEL=INFO` is enough to see those
lines. DEBUG may add span kind and id prefixes while a span opens. ERROR logs
`failure_code` and `error_type` only.

Fields include ids, `kind` (`run` | `agent` | `model` | `render`), `status`
(`ok` | `failed` | `cancelled` | `budget_exceeded`), `duration_ms`, public
`model_id` and `runtime`, reported `prompt_tokens` / `completion_tokens` with
`usage_status` (`available` | `partial` | `unavailable`), `local_inference_ms`
for local or in-process runtimes, process RSS (`process_rss_kb`,
`process_rss_delta_kb`) when the call succeeds, `gpu_allocated_bytes` only when
`torch` is already imported and a CUDA device answers, `retry_count`,
`revision_count` on the run span, `failure_code`, and
`provider_reported_cost_micros`.

Token counts and cost stay null when the provider does not report them. The
logger does not invent zeros or a USD estimate. Missing cost logs
`cost_status=unavailable` and does not fail the run.

Process RSS is `resource.getrusage` for this process, labeled as such. GPU
bytes are not sampled by importing torch. A failed span leaves memory fields
null and `memory_status=unavailable`.

Render workers run off the request task, so they log a render span from the
job's `operation_run_id`. `parent_span_id` may be null. `run_id` is the join
key. The span logs `byte_size` and `instructions_len`, plus a 12-character
`operation_run_id` prefix. It does not log PCM or instruction text.

## Redaction

Before each span log, exact secret field names are dropped (`api_key`,
`openai_api_key`, `authorization`, `access_token`, `secret`, `password`,
`bearer`, `token`, and the other names in `FORBIDDEN_LOG_FIELD_NAMES`). A
substring search is not used, so `prompt_tokens`, `completion_tokens`, and
`max_prompt_tokens` stay. Keys named `prompt`, `messages`, `composition`,
`events`, `audio`, `pcm`, `wav_bytes`, and `api_key` are dropped. Strings
longer than 256 characters become a length marker. Fingerprints are logged as
prefixes.

## Budgets

Env ceilings in `operation_budget_settings.py`. Invalid values fall back to the
defaults and log a warning with `env_key` and `error_type` only. A request
cannot raise an env ceiling.

| Env | Default | Effect |
|-----|---------|--------|
| `OPERATION_MAX_MODEL_CALLS` | `32` | Stop before the next model call on an active run. `0` disables this ceiling |
| `OPERATION_MAX_RUNTIME_MS` | `180000` | Stop before the next agent, model, or render attempt. Request `max_wall_ms` may only lower it |
| `OPERATION_MAX_PROMPT_TOKENS` | empty (off) | Sum of reported prompt tokens. Does not fire when usage is `unavailable` |
| `OPERATION_REMOTE_COST_MICROS` | empty (off) | Fires only after an adapter reports a cost number |
| `OPERATION_MAX_REVISIONS` | `8` | Passed into revision `resolve_max_passes`. Values above 8 are stored as 8 |
| `NEURAL_AUDIO_MAX_ATTEMPTS` | `2` | Attempts per render or stem-set job, including the first. Range 1–5 |

The tighter of this ceiling and the existing revision-loop budget wins. Mode
caps stay 1 / 2 / 3. The runtime ceiling is checked before each spine agent,
each revise agent, the re-critique Critic, each model call, and each render or
stem-set attempt that carries the run id. A render worker reads the run's
start time from a process-wide clock, because it does not see the request
context. Stop reason remains `resource_budget_exhausted` with
`budget_code`:

- `operation_model_call_budget`
- `operation_runtime_budget`
- `operation_token_budget`
- `operation_cost_budget`
- `operation_revision_budget`
- `operation_render_attempt_budget`

## Cancellation

The workflow and single-agent routes watch `request.is_disconnected` and set
the run's cancel event plus a process-wide cancelled-run set (max 256 ids).
Model calls check that event before the provider call and between stream
chunks. Partial text is not logged.

Neural jobs check the same set before each attempt, including selective stem
rerender. A queued attempt with that `operation_run_id` is stored `failed` /
`operation_cancelled` and does not call the engine. An attempt already inside
sidecar HTTP is abandoned: the span is `cancelled`, the late body is discarded,
and the sidecar process is left running. Other jobs share that process, so
cancel does not kill MusicGen, llama.cpp, or vLLM.

Jobs whose `operation_run_id` is null or belongs to another run are not
cancelled by this run. The Agents panel abort control is the user control.
While a preview is in flight, neural enqueue and stem-set enqueue send the
same `operation_run_id`. After the preview settles, later enqueues omit it.

`KeyboardInterrupt` is not swallowed.

## Crash containment

`SystemExit` from an agent `run` or from a chat / structured provider invoke
is recorded as span status `failed` and `failure_code=operation_model_crashed`.
It is not re-raised. The process stays up, so a following `GET /ready` and
project open still succeed. Routing only resolves a model; it is not the crash
boundary.

Native segfaults inside an in-process torch call remain a residual. There is
no subprocess sandbox in this milestone.

## Diagnosis

1. Read `operation_summary` on the preview or single-agent response (`run_id`,
   `status`, `duration_ms`, `model_call_count`, `revision_count`,
   `failure_count`, `budget_code`, `stop_reason`).
2. In container logs at INFO, find `Operation span closed` lines whose
   `run_id` matches. Render spans join on that id even when `parent_span_id`
   is null.
3. Use `failure_code` / `budget_code`. Do not expect prompt text, composition
   JSON, PCM, or secret values in those lines.

The Agents panel shows one session line from the summary
(`data-testid="multi-agent-operation-summary"`). The span tree is not stored
in the browser.
