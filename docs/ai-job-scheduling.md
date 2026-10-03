# Capability-aware AI job scheduling

## Summary

When `AI_SCHEDULING_ENABLED` is on and a request does not pin an explicit `model_id`, a pure deterministic scheduler picks one registry candidate (controller-local or trusted-LAN `execution_node`) for a LanguageModel-backed AI operation. On node loss, a bounded reschedule may pick another eligible candidate without escalating the trust boundary. The working `composition.v2` is never written by the scheduler. There is no `composition.v5`.

## Terminology

| Term | Meaning |
|------|---------|
| Job | `scheduling.job.v1` — one placement request (never a prompt or PCM stream) |
| Candidate | `scheduling.candidate.v1` — snapshot of one registry model |
| Decision | `scheduling.decision.v1` — pure scheduler output |
| Policy | `scheduling.policy.v1` — mode + public-cloud gate |
| Trust boundary | `controller_local` \| `trusted_lan` \| `public_cloud` |
| Reschedule | Second pure decision after a failed attempt, with prior node/model ids excluded |

## Policies

| Mode | Behavior |
|------|----------|
| `prefer_local` | Prefer `controller_local`, then `trusted_lan`, then (if allowed) `public_cloud`; then lowest latency |
| `fastest_available` | Lowest estimated latency among eligible peers |
| `memory_safe` | Prefer highest memory headroom above `estimated_memory_mb` |
| `fixed_node` | Hard-pin to `fixed_node_id`; unavailable → no silent move |

Job `priority` is accepted on the DTO and unused in v1 ranking (`priority_ignored_v1`).

## Trust precedence

Evaluate in this order; first match wins:

1. `runtime=execution_node` → `trusted_lan` (even when descriptor `locality=remote`)
2. Controller-local runtimes (`local_openai_compatible`, `fake`, …) → `controller_local`
3. Else `locality=remote` → `public_cloud`
4. Else `controller_local`

Private workloads (`privacy_class=private`, default) never select `public_cloud` unless both policy `allow_public_cloud` and job `privacy_class=allow_public` are set. Explicit pinned `model_id` still wins over the scheduler.

## Local resource hints

Optional env keys fill controller-local candidates when descriptor `limits` omit resources:

- `AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB`
- `AI_SCHEDULING_LOCAL_MEMORY_TOTAL_MB`
- `AI_SCHEDULING_LOCAL_DEVICE_CLASS`
- `AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS`

They never override ExecutionNode heartbeat resources.

## Reschedule

When invoke uses `resolution_path=schedule` or `runtime=execution_node` and fails with `model_unavailable` (not `operation_cancelled`), the controller may reschedule up to `max_attempts` (default 2, clamp 1..4), excluding failed node/model ids, without raising trust above the failed attempt. `AI_FALLBACK_*` after schedule exhaustion also honors that trust clamp.

## HTTP

| Method | Path | Notes |
|--------|------|-------|
| GET | `/ai/scheduling/policy` | Flag off → 404; env-materialized defaults when no SQLite row |
| PUT | `/ai/scheduling/policy` | CAS on `document_revision` (send next revision) |
| POST | `/ai/scheduling/preview` | Returns a decision; never invokes a model |

No studio Nodes / Scheduling tab in this milestone.

## Configuration

See `.env.example` (`AI_SCHEDULING_*`). Soft `/ready` block `ai_scheduling`: `{enabled, mode, allow_public_cloud}` — informational only.

## Examples

Prefer local llama.cpp for a private planner:

```bash
AI_SCHEDULING_ENABLED=1
AI_SCHEDULING_DEFAULT_MODE=prefer_local
AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB=4096
AI_SCHEDULING_LOCAL_DEVICE_CLASS=igpu
AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS=40
```

Preview without invoking:

```http
POST /ai/scheduling/preview
{"job":{"operation":"generate_planner","required_capability":"language_planner"}}
```

## Logging

Safe: `policy_mode`, `operation`, `required_capability`, `selected_model_id`, `selected_node_id`, `trust_boundary`, `reason_codes`, `eligible_count`, `attempt_index`, `failure_code`, `privacy_class`. Never: prompts, `input_text`, bearer tokens, event arrays, absolute weight paths.

## See also

- [Execution nodes](execution-nodes.md)
- [AI Runtime](ai-runtime.md)
- [Observability](observability.md)
