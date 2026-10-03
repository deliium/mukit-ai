# Distributed AI Execution Nodes

Trusted LAN peers that run typed model inference for Mukit AI Composer.

## Summary

An **ExecutionNode** (`execution.node.v1`) is a second machine (or in-process fake peer) that registers with the studio **controller**, heartbeats its catalog and health, and accepts authenticated `complete_text` tasks. The playable score stays `composition.v2`. There is no `composition.v5`.

Remote shell, operator-supplied argv, and ChatOpenAI aimed at `/execution/v1/*` are out of scope and refused.

## Roles

| Role | Env | Mounts |
|------|-----|--------|
| `controller` | ThinkBook studio backend | Full studio API + `/ai/execution-nodes/*` |
| `worker` | Second machine | `/ready`, `/health`, `/execution/v1/*` only (no `/projects`) |
| `both` | CI / laptop demos | Full studio + worker surface |

## Configuration

Defaults are off. When `AI_EXECUTION_NODES_ENABLED=1`, `AI_EXECUTION_NODE_TOKEN` must be non-empty (including for loopback).

| Env | Purpose |
|-----|---------|
| `AI_EXECUTION_NODES_ENABLED` | Feature flag |
| `AI_EXECUTION_NODE_TOKEN` | Shared bearer (never logged) |
| `AI_EXECUTION_NODE_ROLE` | `controller` \| `worker` \| `both` |
| `AI_EXECUTION_NODE_CONTROLLER_URL` | Worker bootstrap register target |
| `AI_EXECUTION_NODE_HEARTBEAT_*` | Interval / TTL |
| `AI_EXECUTION_NODE_FAKE` | In-process peer at `http://execution-node.fake` |
| `AI_EXECUTION_NODE_MAX_CONCURRENCY` | Worker in-flight `complete_text` cap (1–64; default 2). Heartbeat reports `busy` when `active_tasks ≥ max`, `draining` while the worker loop is stopping |
| `AI_EXECUTION_NODE_ADDRESS_ALLOW_CIDRS` | Extra CIDRs beyond loopback + RFC1918 |
| `AI_EXECUTION_NODE_ALLOW_HOSTNAME` | Allow non-builtin DNS hostnames |

Address allowlist refuses link-local, cloud metadata (`169.254.169.254`), userinfo, and non-http(s) schemes.

## Discovery and routing

1. Worker `POST /ai/execution-nodes/register` (bearer).
2. Worker heartbeat refreshes catalog + availability TTL.
3. Controller `reload_registry` attaches `node:<16hex>:<local_model_id>` with `runtime=execution_node`.
4. Generate/edit call `ainvoke_text_for_resolved`, which uses `ExecutionNodeLanguageModel.complete_text` instead of ChatOpenAI.
5. Cancel: `POST /ai/execution-nodes/tasks/{task_id}/cancel` forwards to the worker and may call `mark_run_cancelled`.

`GET /ai/execution-nodes` lists peers. `GET /ai/models` includes remote rows with optional `execution_node_id`. Soft `/ready` block `execution_nodes` never fails overall readiness.

## Security

- Bearer required for every peer when enabled; query-string tokens never authorize.
- Forbidden document keys include `events`, `shell`, `command`, `argv`, `subprocess`, `prompt`, `api_key`, …
- Worker dispatch must not call `subprocess` / `os.system`.
- `ai_agents/` must not import `execution_node_store`.

## Logging

Safe fields: `node_id`, `task_id`, `role`, `availability`, HTTP status codes, `prompt_length` / `output_length`. Never log the bearer token, Authorization header, or `input_text` / `output_text` bodies.

## Compose

Optional profile: `compose.execution-node.yml` (`--profile execution-node`). The worker publishes its own port; studio nginx does not need to proxy `/execution/v1` for LAN peers.

## See also

- [AI Runtime](ai-runtime.md)
- [Local AI](local-ai.md)
- [Observability](observability.md)
