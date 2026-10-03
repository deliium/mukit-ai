# Implementation Plan: V5 Distributed AI Execution Nodes

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: no new workspace tab. Controller surfaces registered nodes on `GET /ai/execution-nodes` and merges ready remote models into `GET /ai/models`. Soft `/ready` block `execution_nodes` never fails overall readiness
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Locked: bearer required for every peer when enabled; drop availability `unauthenticated`; define `execution.node.catalog.v1`; address allowlist (loopback + RFC1918 + optional CIDRs, refuse link-local/metadata); domain error → HTTP map; controller `task_id→node` index for cancel; `_attach_execution_node_descriptors` after every `reload_registry`; `ainvoke_text_for_resolved` seam so generate/edit do not send ChatOpenAI at `/execution/v1/complete_text`; worker-only role mounts `/ready` + `/execution/v1/*` only; model id parse `node:<16hex>:<remainder>`
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request for trusted-LAN ExecutionNode registration, authenticated inference, heartbeat, capability discovery, availability, cancellation, no remote shell, reuse of V3/V4 model runtime interfaces, security tests, `v5-` plan prefix, and roadmap append
- Scope: a ThinkBook controller discovers a second trusted machine (worker ExecutionNode) and runs one supported model inference operation on it through the existing `LanguageModel` / routing surface and the generate invoke seam. The playable score stays `composition.v2`. There is no `composition.v5`

## Roadmap Linkage
Milestone: "V5 Distributed AI execution nodes"
Rationale: Local AI today is one sidecar URL on the same host. A second trusted LAN machine should register as an ExecutionNode so the composer can route a typed inference call without SSH or a second studio. This plan appends that unchecked item because the request asked to add it. Implementation does not edit `ROADMAP.md`.

## Goal

Allow AI Composer to use models running on other trusted machines on a local network.

Ship:

1. Non-playable documents `execution.node.v1`, `execution.node.registration.v1`, `execution.node.heartbeat.v1`, `execution.node.catalog.v1`, `execution.task.v1`, and `execution.task.result.v1`.
2. A deployment flag `AI_EXECUTION_NODES_ENABLED` (default off) and a shared bearer secret `AI_EXECUTION_NODE_TOKEN` required for **every** peer (including loopback) when the feature is on. Empty token while enabled → `execution_node_token_missing`.
3. Dual role in the same codebase: **controller** (ThinkBook studio backend) and **worker** (second machine). Role is `AI_EXECUTION_NODE_ROLE` ∈ `controller` | `worker` | `both` (tests use `both` or fake peer). Role `worker` mounts only `/ready` and `/execution/v1/*`.
4. Worker registration, heartbeat, availability state, capability/model catalog, resource snapshot, and authenticated typed inference only.
5. A new `runtime=execution_node` adapter that implements the existing `LanguageModel` protocol, reattaches on every `reload_registry`, and plugs into `resolve_model_for_operation` / `GET /ai/models`.
6. An `ainvoke_text_for_resolved` (or equivalent) seam so generate/edit call `ExecutionNodeLanguageModel.complete_text` instead of ChatOpenAI when `runtime=execution_node`.
7. Task ids with a controller `task_id→node_id` index, cancel that reaches the worker, and cooperation with `operation_trace.mark_run_cancelled`.
8. Explicit refusal of arbitrary remote shell / process exec on the worker surface, plus address SSRF allowlisting.

Acceptance: with `AI_EXECUTION_NODES_ENABLED=1` and a shared token, a controller process registers a second trusted peer (fake in-process worker or TestClient against a second app) that advertises one ready language model. `GET /ai/execution-nodes` lists that node as `available` with the model id. Selecting `node:<16hex>:<local_model_id>` through `resolve_model_for_operation(AiOperation.GENERATE, …)` and the invoke seam runs remote `complete_text` and returns a non-empty completion (protocol path and one generate-stage `_invoke_chat` path). A cancel before completion yields `operation_cancelled` / task `cancelled`. A request without the bearer returns `401`. The token never appears in captured logs. A forged path that would invoke shell/`subprocess` is not mounted and a security test asserts the allow-listed worker routes only. Heartbeat expiry moves the node to `unavailable` and routing raises `ModelUnavailableError` instead of calling a dead peer. Register with a link-local or metadata address is rejected. `composition.v2` note events are never written by node registration, heartbeat, or cancel.

```text
Worker machine (AI_EXECUTION_NODE_ROLE=worker)
        │  Bearer token
        ▼
POST /ai/execution-nodes/register  →  execution.node.v1 on controller
POST /ai/execution-nodes/{id}/heartbeat  (capabilities, models, health, resources)
        │
Controller reload_registry → _attach_execution_node_descriptors
        │
resolve_model_for_operation → runtime=execution_node
        │
ainvoke_text_for_resolved / _invoke_chat seam
        │  Bearer + task_id + optional operation_run_id
        ▼
POST /execution/v1/complete_text  (worker only)
        │
execution.task.result.v1  →  existing generate/edit orchestrators
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. An **ExecutionNode** is `execution.node.v1`, a trusted peer that can run typed model ops. The **controller** is the studio FastAPI process that owns project SQLite and routes work. The **worker** is a peer process that loads or proxies models and, when `AI_EXECUTION_NODE_ROLE=worker`, does not mount studio project routers. **Registration** is an authenticated write of node metadata on the controller. **Heartbeat** refreshes health, catalog, and resource state and resets the availability TTL. **Availability** is `available` | `busy` | `draining` | `unavailable` — auth failures are HTTP `401`, not an availability enum value. A **task** is `execution.task.v1`, one in-flight inference call with a cancel handle. The **invoke seam** is the single place generate/edit obtain completion text for a `ResolvedModel` so ChatOpenAI is never aimed at `/execution/v1/*`. **Typed inference** means only closed operations that map to V3/V4 protocols (`complete_text` first). **Remote shell** means any endpoint or code path that runs an operator-supplied command string, interactive shell, or unrestricted `subprocess`/`os.system` on the worker — forbidden. **Locality** for catalog models hosted on a worker is `remote` from the controller's point of view even when both machines are on a LAN. Sidecar `local_openai_compatible` on the same host as the worker stays `local` *on that worker*; the controller sees the re-exported model as `runtime=execution_node`.

Predecessors: unified AI runtime (`docs/ai-runtime.md`), optional local AI (`docs/local-ai.md`), observability cancel (`docs/observability.md`), and adaptive engine bearer-or-loopback auth (pattern only; modules stay separate). Do not reopen: FluidSynth outside the runtime, no weight download inside FastAPI, no silent fallback, no secrets in discovery JSON, `ai_agents/` must not import new stores.

## Approach Evaluation (locked)

### Part A — What process runs on the second machine

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. SSH / Ansible remote shell that starts llama.cpp on demand** | Familiar ops | User forbade arbitrary remote shell. Secrets and prompts would ride shell history | **Reject** |
| **B. Only point `LOCAL_LLM_BASE_URL` at another host's OpenAI port** | Almost free | No node identity, heartbeat, capability document, resource state, or cancel contract. Auth is whatever the sidecar happens to use. No `ExecutionNode` model | **Reject** |
| **C. Same Mukit backend image/code with `AI_EXECUTION_NODE_ROLE=worker`, exposing a closed `/execution/v1/*` surface and registering with the controller** | One codebase, typed protocols, shared fake mode, cancel hooks, security tests | Worker must refuse studio project writes and shell helpers | **Accepted** |

### Part B — How the controller learns that a node exists

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. mDNS / UDP broadcast auto-join** | Zero config | Spoofable on open Wi-Fi; hard to auth; not “trusted LAN first” | **Reject** for v1 |
| **B. Static env list `AI_EXECUTION_NODE_URLS=http://…` only** | Simple | No heartbeat document, no capability refresh, no availability TTL without extra poller design anyway | **Reject** as sole mechanism |
| **C. Worker calls authenticated `POST /ai/execution-nodes/register` on the controller; optional bootstrap env `AI_EXECUTION_NODE_CONTROLLER_URL` on the worker. Controller may also accept a one-shot admin register for tests. Heartbeat renews TTL** | Explicit trust. Fits TestClient. Matches “registration/discovery suitable for trusted local networks first” | Worker must know the controller URL once | **Accepted** |

### Part C — Authentication

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reuse collaboration `X-Actor-Id`** | Exists | Actors are studio users, not machines; flag-off would open routes | **Reject** |
| **B. Mutual TLS with per-node certs** | Strong identity | Ops burden for a ThinkBook + spare box LAN; blocks fake CI | **Reject** for v1 |
| **C. Shared bearer `AI_EXECUTION_NODE_TOKEN` compared with SHA-256 + `hmac.compare_digest` (same shape as `adaptive_engine_auth`). Feature off → routes 404. Feature on → token required for every peer including loopback; empty token → `execution_node_token_missing`. Query-string tokens and `X-Forwarded-For` never authorize. Never log the raw token or Authorization header** | Proven pattern. Works for LAN. Testable | Shared secret is coarser than per-node mTLS | **Accepted** |

### Part D — How inference reuses V3/V4 runtimes

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New parallel “remote LLM” stack beside `ai_runtime`** | Isolation | Duplicates routing, provenance, cancel, discovery | **Reject** |
| **B. Controller opens raw HTTP to the worker's llama.cpp port, or points ChatOpenAI `base_url` at `/execution/v1`** | Fewer hops | Bypasses typed task/cancel; OpenAI chat dialect ≠ `execution.task.v1`; generate today uses `build_chat_openai` not `LanguageModel` | **Reject** |
| **C. Add `runtime=execution_node` + `ExecutionNodeLanguageModel` (`LanguageModel`). Reattach descriptors after every `reload_registry`. Add `ainvoke_text_for_resolved` and switch `_invoke_chat` (generate) plus one edit path to it when runtime is `execution_node`. Worker dispatches into local `LocalLanguageModel` / `FakeLanguageModel`** | Reuses registry, routing, provenance, cancel. Matches acceptance and real composer path | First ship is language `complete_text` only | **Accepted** |

### Part E — Task cancellation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Rely on HTTP client timeout only** | Simple | Cannot stop a long generation early; ignores `mark_run_cancelled` | **Reject** |
| **B. New global Redis cancel bus** | Multi-process | New dependency; out of stack | **Reject** |
| **C. Every remote call gets `task_id`. Controller keeps an in-memory `task_id → (node_id, worker_cancel_path)` index. `POST /ai/execution-nodes/tasks/{task_id}/cancel` forwards to the worker and, when `operation_run_id` is present, calls `mark_run_cancelled`. Worker maps cancel to `operation_cancelled`** | Matches observability. Testable with fake slow complete | Best-effort if the sidecar ignores disconnect | **Accepted** |

### Part F — Persistence of node rows

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Process memory only** | No migration | Controller restart forgets trusted nodes until they re-register; harder acceptance after restart | **Reject** as sole store |
| **B. Full project-scoped SQLite rows mixed into `projects`** | Durable | Nodes are deployment topology, not musical projects | **Reject** |
| **C. SQLite table `execution_nodes` in `PROJECT_DB_PATH` (deployment DB already used for plugins) holding last registration JSON + revision; live health/availability stay process memory refreshed by heartbeat. Restart: durable address/capabilities seed; status starts `unavailable` until heartbeat** | Survives controller restart without inventing a second DB. Worker must re-heartbeat to become `available` | Stale addresses possible until TTL | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Protocols | `LanguageModel.complete_text` in `ai_runtime/protocols.py` | Worker and controller adapters implement the same protocol; v1 remote path is `LanguageModel` only |
| Generate/edit invoke | `_invoke_chat` / editors call `build_chat_openai` + `ainvoke_chat_text` — **not** `LanguageModel.complete_text` | New invoke seam must branch on `runtime=execution_node` |
| Registry | `register_model`, `reload_registry`, `_attach_plugin_descriptors` | Mirror plugin attach with `_attach_execution_node_descriptors` after every reload |
| Routing | `resolve_model_for_operation`, ContextVar resolved model | Selection by `model_id`; provenance records `resolved_model_id` / runtime |
| Local sidecar | `LocalLanguageModel` + `probe_local_llm_health` | Worker uses these for on-box models |
| Discovery DTO | `AiModelCatalogItem` / `GET /ai/models` | Optional `execution_node_id` max 64 |
| Cancel | `mark_run_cancelled`, `is_run_cancelled` | Controller cancels task + run; worker checks flags |
| Auth pattern | `adaptive_engine_auth` | New pure `execution_node_auth.py`; do not import adaptive engine |
| Error map | `preference_schemas.map_preference_error_to_http` | Same shape for execution-node codes |
| Opt-in flag | collaboration / preference truthy set | Copy for `AI_EXECUTION_NODES_ENABLED` |
| Soft ready | `local_ai` in `/ready` | Add `execution_nodes` soft block |
| HTTP client | `httpx` in requirements; sidecars often use `urllib` | Prefer `httpx` AsyncClient for controller→worker and fake ASGI transport |
| Lifespan | `main.py` lifespan for plugins / personal composer sweep | Start/stop worker heartbeat loop here |
| Nginx | `frontend/nginx.conf` already proxies `location /ai/` | Controller routes under `/ai/execution-nodes` need no new location; worker compose must not publish studio nginx as the only peer |
| Latest migration | `20261002_0023` preference | Next `20261003_0024`, `down_revision` `20261002_0023` |
| Agent boundary | `test_ai_agents_architecture.py` | Add `from app.services.execution_node_store` |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| ExecutionNode documents + store | Identity, hardware, resources, catalog |
| Registration / heartbeat / TTL | LAN peer lifecycle |
| Address allowlist | SSRF guard on register |
| `runtime=execution_node` + registry reattach | Survive `reload_registry` |
| Invoke seam | Generate/edit must not use ChatOpenAI against worker typed API |
| Worker inference surface + slim mount | Closed `/execution/v1/*`; role=`worker` omits studio routers |
| Task index + cancel forward | Process-local cancel is insufficient across machines |
| Domain error HTTP map | Typed codes |
| Security + acceptance tests | Auth, SSRF, shell absence, cancel, generate seam |
| Docs | `docs/execution-nodes.md` + cross-links |

### Coupling risks to avoid

1. Arbitrary remote shell, `os.system`, or operator-supplied argv on the worker HTTP surface.
2. Worker writing `projects.composition_json` or importing `project_store` for studio CRUD when role is `worker`.
3. Controller calling the worker's raw llama.cpp/vLLM URL, or pointing ChatOpenAI at `/execution/v1`.
4. Logging bearer tokens, Authorization headers, full prompts (`input_text`/`output_text`), event arrays, weight paths, or host usernames.
5. Trusting `X-Forwarded-For` or query-string tokens.
6. Silent fallback from a dead node to cloud without `AI_FALLBACK_*`.
7. Importing `execution_node_store` / worker dispatch from `ai_agents/`.
8. Inventing `composition.v5` or a new agent id.
9. Editing `ROADMAP.md` during implementation.
10. Enabling the feature by default or allowing unauthenticated register when the flag is on.
11. mDNS auto-join in v1.
12. Shipping UI that can start a remote shell against a node.
13. Losing `node:*` models on every `reload_registry` because attach was skipped.
14. Accepting link-local or cloud-metadata addresses as worker `address`.

## Scope And Decisions

### In scope
- Schemas (including catalog), settings, auth, address allowlist, SQLite node table, controller register/list/heartbeat/cancel + task index, worker `/execution/v1/*` typed ops, slim worker mount, `runtime=execution_node` LanguageModel adapter, registry reattach, invoke seam for generate + one edit path, soft `/ready`, fake peer for CI, security + acceptance tests, docs, optional compose profile.
- Resource/health fields, availability TTL, busy/draining states.

### Out of scope
- mDNS / cloud mesh / multi-controller consensus.
- Per-node mTLS or OAuth device flow.
- OpenAI-compatible `/v1/chat/completions` on the worker as a substitute for typed tasks.
- Remote symbolic Music Transformer weight management UI.
- Remote neural audio PCM streaming.
- Worker access to `DATASET_ROOT` training loops.
- Frontend Nodes tab.
- Changing `LanguageModel` protocol method signatures.
- `ROADMAP.md` during implementation.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/execution_node_schemas.py`. `extra=forbid`. No FastAPI, torch, stores, video, film, agent, or adaptive engine imports. Forbidden keys anywhere → `execution_node_forbidden_payload`: `events`, `notes`, `pitch`, `composition`, `composition_json`, `api_key`, `authorization`, `shell`, `command`, `argv`, `subprocess`, `prompt` (text travels only as `input_text` / `output_text` on task documents). Exact key-name match.

Typed errors (same style as preference): `ExecutionNodeError` with codes and `map_execution_node_error_to_http`:

| Code | HTTP |
|------|------|
| `execution_nodes_disabled` | 404 |
| `execution_node_unauthorized` | 401 |
| `execution_node_token_missing` | 503 |
| `execution_node_not_found` | 404 |
| `execution_node_conflict` | 409 |
| `execution_node_unavailable` | 503 |
| `execution_node_forbidden_payload` | 422 |
| `execution_node_address_rejected` | 422 |
| `execution_node_task_not_found` | 404 |
| `execution_node_busy` | 429 |

`ExecutionNodeV1`. Schema version `execution.node.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `execution.node.v1` |
| `node_id` | `^node_[0-9a-f]{16}$` |
| `display_name` | string 1..80 |
| `address` | URI `http://` or `https://` host[:port] only; no userinfo; max 256; must pass address allowlist |
| `role` | `worker` |
| `capabilities` | list 1..16 of `ModelCapability` strings |
| `hardware` | `device_class` (`cpu` \| `igpu` \| `dgpu` \| `unknown`), optional `accelerator_name` max 80 (no paths), optional `cpu_count` ≥ 1 |
| `available_runtimes` | list max 16 |
| `installed_models` | list 0..32 of public model rows — never secrets or weight paths |
| `health` | status + optional detail max 64 + optional `latency_ms` |
| `resources` | memory fields optional; `active_tasks` ≥ 0; `max_concurrency` 1..64 |
| `availability` | `available` \| `busy` \| `draining` \| `unavailable` |
| `last_heartbeat_at` | optional server timestamp |
| `document_revision` | int ≥ 1 CAS |

`ExecutionNodeRegistrationV1` / `ExecutionNodeHeartbeatV1` — as before; heartbeat availability only `available`\|`busy`\|`draining`.

`ExecutionNodeCatalogV1`. Schema version `execution.node.catalog.v1`. Fields: `node_id`, `installed_models` (same row shape as on the node), `generated_at` optional. Used as the body of `GET /execution/v1/models` (and may be embedded in controller list projections). Not a second persistence table.

`ExecutionTaskV1` / `ExecutionTaskResultV1` — as before (`input_text` / `output_text`, never logged).

**2. Address allowlist**

Default allow: loopback (`127.0.0.1`, `::1`, `localhost`) and RFC1918 (`10/8`, `172.16/12`, `192.168/16`). Default refuse: link-local, `169.254.169.254`, non-http(s), userinfo, DNS names that resolve only to refused ranges when a resolve check is cheap in tests (lock: validate literal IP hosts strictly; for hostnames allow only when `AI_EXECUTION_NODE_ALLOW_HOSTNAME=1` **or** hostname is `localhost` / `execution-node.fake`). Optional env `AI_EXECUTION_NODE_ADDRESS_ALLOW_CIDRS` adds CIDRs. Code `execution_node_address_rejected`.

**3. Settings** (`backend/app/execution_node_settings.py`)

| Env | Rule |
|-----|------|
| `AI_EXECUTION_NODES_ENABLED` | truthy `1/true/yes/on`; default off |
| `AI_EXECUTION_NODE_TOKEN` | required non-empty when enabled; never logged |
| `AI_EXECUTION_NODE_ROLE` | `controller` (default when enabled on studio), `worker`, or `both` |
| `AI_EXECUTION_NODE_CONTROLLER_URL` | worker bootstrap register target |
| `AI_EXECUTION_NODE_HEARTBEAT_INTERVAL_SECONDS` | default 10, clamp 5..60 |
| `AI_EXECUTION_NODE_HEARTBEAT_TTL_SECONDS` | default 30, clamp 15..120; must be > interval |
| `AI_EXECUTION_NODE_FAKE` | truthy → in-process fake worker |
| `AI_EXECUTION_NODE_MAX_NODES` | default 8, clamp 1..32 |
| `AI_EXECUTION_NODE_ADDRESS_ALLOW_CIDRS` | optional comma CIDRs |
| `AI_EXECUTION_NODE_ALLOW_HOSTNAME` | truthy to allow non-localhost hostnames in address (default off) |

**4. Auth** (`backend/app/services/execution_node_auth.py`)

Pure module. When feature enabled, missing/mismatched token → unauthorized for all peers. Query token → refuse. Log only `peer_class` and code.

**5. Store** (`backend/app/services/execution_node_store.py`)

SQLite `execution_nodes(node_id PK, body_json, document_revision, updated_at)`. CAS on heartbeat. No Composition. Delete removes the row.

**6. Controller routes** (`backend/app/routers/execution_nodes.py`)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/ai/execution-nodes/register` | Auth; address allowlist; upsert |
| POST | `/ai/execution-nodes/{node_id}/heartbeat` | Auth; CAS; TTL |
| GET | `/ai/execution-nodes` | Auth (bearer always when enabled) |
| GET | `/ai/execution-nodes/{node_id}` | Auth |
| DELETE | `/ai/execution-nodes/{node_id}` | Auth; drop registry models |
| POST | `/ai/execution-nodes/tasks/{task_id}/cancel` | Auth; lookup task index; forward to worker |

Flag off → 404 `execution_nodes_disabled`.

**Controller task index** (process memory): on each remote `complete_text` start, record `task_id → {node_id, cancel_path}`. Cancel uses the index; missing → `execution_node_task_not_found`. Restart clears the index (in-flight tasks are lost — acceptable for v1).

**7. Worker routes** (`backend/app/routers/execution_worker.py`)

Mounted when role is `worker` or `both`.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/execution/v1/health` | Auth |
| GET | `/execution/v1/models` | Auth; returns `execution.node.catalog.v1` |
| POST | `/execution/v1/complete_text` | Auth; task in, result out |
| POST | `/execution/v1/tasks/{task_id}/cancel` | Auth |

When role is **`worker`** (not `both`/`controller`): `main.py` mounts only `/ready` (and existing root health if any), the worker router, and does **not** mount projects/imports/neural/agents/etc. Role `both` keeps the full studio surface for CI. No shell/exec routes.

**8. Runtime adapter + registry** (`backend/app/ai_runtime/runtimes/execution_node.py`)

- `ExecutionNodeLanguageModel` implements `LanguageModel`; HTTP via `httpx` to `{address}/execution/v1/complete_text`; registers task in controller index; checks `is_run_cancelled`; never logs text bodies.
- Extend `ModelRuntimeId` with `execution_node`.
- Canonical catalog id: `node:<node_hex>:<remainder>` where `node_hex` is the 16 hex chars of `node_id` and `remainder` is the worker-local model id verbatim (may contain colons). Parse: strip `node:`, take next 16 hex chars, require following `:`, remainder is local id.
- After every `reload_registry` (alongside plugins), call `_attach_execution_node_descriptors()` that registers ready models from available nodes with `overwrite=True` for `node:*` ids owned by the attach path.

**9. Invoke seam** (`backend/app/ai_runtime/invoke_text.py` or extend `llm_chat_client.py`)

```text
ainvoke_text_for_resolved(resolved, prompt, *, purpose) -> str
  if runtime == execution_node → ExecutionNodeLanguageModel.complete_text
  else → existing ChatOpenAI / local path via provider_settings_for_resolved
```

Wire `llm_music_generator._invoke_chat` and one region-edit invoke path to this helper. Do not invent OpenAI credentials for execution-node descriptors in `provider_settings_for_resolved` (raise `ModelUnavailableError` / route through the seam instead).

**10. Fake mode**

`AI_EXECUTION_NODE_FAKE=1` → in-process peer at `http://execution-node.fake` (hostname allowlisted for fake). Deterministic `fake-execution-node:<purpose>`; slow path honors cancel.

**11. Provenance / logging**

Record `resolved_model_id`, `runtime=execution_node`, optional `limits.execution_node_id`. Never token. VERBOSE: register/heartbeat/task/cancel codes and ids only.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat(ai): add execution node schemas, settings, and auth`
- **Commit 2** (after tasks 4–6): `feat(ai): persist execution nodes and controller/worker routes`
- **Commit 3** (after tasks 7–9): `feat(ai): route LanguageModel inference through execution nodes`
- **Commit 4** (after tasks 10–12): `test(ai): cover execution-node security, cancel, and acceptance`
- **Commit 5** (after tasks 13–14): `docs(ai): document distributed execution nodes`

## Tasks

### Phase 1: Contracts and auth
- [x] Task 1: Define `execution_node_schemas.py` with `ExecutionNodeV1`, registration, heartbeat, **`ExecutionNodeCatalogV1`**, task, result, forbidden-key validator, `ExecutionNodeError` + HTTP map, and address-allowlist helpers (loopback, RFC1918, optional CIDRs; refuse link-local/metadata/userinfo). Unit tests for parse, forbidden keys, catalog, and address rejection.
  - LOGGING: schema module may log rejection codes only (no payloads).
  - Files: `backend/app/execution_node_schemas.py`, `backend/tests/test_execution_node_schemas.py`
  - Depends on: none

- [x] Task 2: Add `execution_node_settings.py` (flag, token, role, TTL, fake, max nodes, address CIDRs, allow_hostname) with collaboration-style truthy parser. Enabled+empty token → `execution_node_token_missing`. Log unknown flag strings without echoing raw values.
  - LOGGING: DEBUG `{enabled, role, heartbeat_ttl, fake, max_nodes, token_present, allow_hostname}` — never token.
  - Files: `backend/app/execution_node_settings.py`, `backend/tests/test_execution_node_settings.py`
  - Depends on: none

- [x] Task 3: Pure `execution_node_auth.py` (bearer compare, query refuse, peer_class). Token required for every peer when enabled. Mirror adaptive-engine tests without importing adaptive modules.
  - LOGGING: INFO refuse with code; DEBUG allow with peer_class.
  - Files: `backend/app/services/execution_node_auth.py`, `backend/tests/test_execution_node_auth.py`
  - Depends on: Task 2
<!-- Commit checkpoint: tasks 1-3 -->

### Phase 2: Store and HTTP surfaces
- [ ] Task 4: Alembic `20261003_0024_execution_nodes.py` + `execution_node_store.py` (upsert, CAS heartbeat, list, get, delete). No Composition imports.
  - LOGGING: INFO upsert/delete with `node_id` + revision; DEBUG CAS conflict.
  - Files: `backend/app/db/alembic/versions/20261003_0024_execution_nodes.py`, `backend/app/services/execution_node_store.py`, `backend/tests/test_execution_node_store.py`
  - Depends on: Task 1

- [ ] Task 5: Controller service + router (register with address allowlist, heartbeat TTL, list/get/delete, disabled→404). In-memory **task index** for cancel forward. Wire into `main.py`. Soft `/ready` `execution_nodes` block. Map domain errors via `map_execution_node_error_to_http`.
  - LOGGING: INFO register/heartbeat/delete/cancel; WARN TTL→unavailable; DEBUG task-index insert/remove.
  - Files: `backend/app/services/execution_node_service.py`, `backend/app/services/execution_node_tasks.py` (index), `backend/app/routers/execution_nodes.py`, `backend/app/main.py`, `backend/app/ready.py`, tests
  - Depends on: Tasks 1, 2, 3, 4

- [ ] Task 6: Worker router `/execution/v1/health|models|complete_text|tasks/{id}/cancel` gated by role. Dispatch `complete_text` into local Fake/Local LanguageModel builders. In-memory worker task table with cancel events. When role is **`worker`**, mount only `/ready` + worker router (not projects/imports/neural/agents). Route/dispatch tests here; defer allow-list AST snapshot to Task 11.
  - LOGGING: INFO task lifecycle with ids only; ERROR with failure_code.
  - Files: `backend/app/routers/execution_worker.py`, `backend/app/services/execution_worker_dispatch.py`, `backend/app/main.py` (conditional includes), `backend/tests/test_execution_worker_routes.py`
  - Depends on: Tasks 1, 2, 3
<!-- Commit checkpoint: tasks 4-6 -->

### Phase 3: Runtime integration
- [ ] Task 7: Add `runtime=execution_node` to `ModelRuntimeId`, `ExecutionNodeLanguageModel` (httpx, task-index registration, cancel checks), `_attach_execution_node_descriptors` after plugin attach on every `reload_registry`, canonical id parse `node:<16hex>:<remainder>`, optional `execution_node_id` on `AiModelCatalogItem`. Do **not** invent ChatOpenAI credentials for these descriptors in `provider_settings_for_resolved`.
  - LOGGING: INFO adapter construct `{model_id, node_id}`; DEBUG attach counts.
  - Files: `backend/app/ai_runtime/types.py`, `backend/app/ai_runtime/runtimes/execution_node.py`, `backend/app/ai_runtime/registry.py`, `backend/app/ai_runtime/routing.py`, `backend/app/ai_runtime_schemas.py`, tests
  - Depends on: Tasks 5, 6

- [ ] Task 8: Add `ainvoke_text_for_resolved` invoke seam; switch `llm_music_generator._invoke_chat` and one region-edit invoke path to it when `runtime=execution_node` (else existing ChatOpenAI path). Unit test: resolved fake execution-node model returns deterministic text without building ChatOpenAI.
  - LOGGING: DEBUG seam branch `{runtime, model_id, purpose}`; never log prompt text.
  - Files: `backend/app/ai_runtime/invoke_text.py` (or `llm_chat_client.py`), `backend/app/services/llm_music_generator.py`, `backend/app/services/llm_composition_editor.py`, tests
  - Depends on: Task 7

- [ ] Task 9: Fake peer (`AI_EXECUTION_NODE_FAKE`) that registers, heartbeats, and serves deterministic `complete_text` via in-process/ASGI transport (`http://execution-node.fake` allowlisted). Slow-complete flag for cancel tests.
  - LOGGING: INFO fake peer start/stop; DEBUG each fake complete.
  - Files: `backend/app/services/execution_node_fake.py`, tests
  - Depends on: Tasks 5, 6, 7
<!-- Commit checkpoint: tasks 7-9 -->

### Phase 4: Acceptance, security, ops, docs
- [ ] Task 10: End-to-end acceptance: enable flag+token+fake → register/discover → `resolve_model_for_operation` + invoke seam / `_invoke_chat` succeeds → cancel via controller task index → heartbeat expiry → unavailable → unauthenticated 401. Assert token absent from caplog. (Depends on Tasks 5, 7, 8, 9.)
  - LOGGING: tests assert absence of secret substrings.
  - Files: `backend/tests/test_execution_node_acceptance.py`
  - Depends on: Tasks 5, 7, 8, 9

- [ ] Task 11: Security tests — wrong/missing/query token; disabled flag; forbidden payload keys; address userinfo/link-local/metadata rejected; worker route allow-list AST snapshot (no shell routes; dispatch must not call `subprocess`/`os.system` for request bodies); `ai_agents` import ban for `execution_node_store`; role=`worker` does not expose `/projects`.
  - LOGGING: capture at INFO+.
  - Files: `backend/tests/test_execution_node_security.py`, `backend/tests/test_ai_agents_architecture.py`
  - Depends on: Tasks 5, 6

- [ ] Task 12: Worker auto-register/heartbeat loop when role is `worker` and controller URL set (async lifespan task, clean shutdown). Bound concurrency from `resources.max_concurrency`; busy/draining when active_tasks ≥ max.
  - LOGGING: INFO loop start/stop; WARN register/heartbeat failure codes; never token.
  - Files: `backend/app/services/execution_node_worker_loop.py`, `backend/app/main.py` lifespan, tests
  - Depends on: Tasks 5, 6

- [ ] Task 13: `.env.example` keys + optional `compose.execution-node.yml` profile (`AI_EXECUTION_NODE_ROLE=worker`, no default enable). Note: worker service should expose its own port; do not require studio nginx to proxy `/execution/v1` for LAN peers. Startup INFO logs role when feature enabled.
  - LOGGING: startup INFO `{enabled, role, fake}`.
  - Files: `.env.example`, `compose.execution-node.yml`, startup path in `main.py` or settings load
  - Depends on: Tasks 2, 6, 12

- [ ] Task 14: Docs — `docs/execution-nodes.md`; cross-links from `docs/ai-runtime.md` and `docs/local-ai.md`; `AGENTS.md` entry points (schemas, settings, routers, services, invoke seam). Mandatory `/aif-docs` checkpoint.
  - LOGGING: document safe log fields.
  - Files: `docs/execution-nodes.md`, `docs/ai-runtime.md`, `docs/local-ai.md`, `AGENTS.md`
  - Depends on: Tasks 10–13
<!-- Commit checkpoint: tasks 10-14 -->

## Test Plan (security emphasis)

1. **Auth matrix:** enabled + correct bearer → 201 register; wrong/missing/query token → 401; flag off → 404; empty token while enabled → token_missing; token ∉ caplog.
2. **Address SSRF:** link-local / metadata / userinfo → 422 `execution_node_address_rejected`; RFC1918 + loopback accepted; fake hostname allowed.
3. **Forbidden surface:** `/execution/v1/shell` → 404; AST guard on worker dispatch; role=`worker` → `/projects` not mounted.
4. **Capability discovery:** heartbeat advertises one model; `GET /ai/models` contains `node:…` with `runtime=execution_node` and `execution_node_id`.
5. **Invoke seam:** generate `_invoke_chat` with resolved execution-node model returns fake text without ChatOpenAI.
6. **Cancel:** slow fake task → controller cancel → `cancelled`; `operation_run_id` marked cancelled.
7. **Availability:** TTL expiry → `unavailable` → `ModelUnavailableError`.
8. **Registry reattach:** `reload_registry` then `node:*` still present while node available.
9. **Agent boundary:** `ai_agents` must not import `execution_node_store`.

## Open Questions (resolved for v1)

| Question | Resolution |
|----------|------------|
| Per-node tokens? | Shared deployment token only |
| Symbolic remote ops? | Language `complete_text` only |
| UI? | No Nodes tab; discovery via API + `/ai/models` |
| Second DB? | `PROJECT_DB_PATH` table, not musical project rows |
| ChatOpenAI to worker? | Rejected; typed task + invoke seam |
| Worker full studio API? | Role `worker` mounts slim surface only; `both` for tests |
