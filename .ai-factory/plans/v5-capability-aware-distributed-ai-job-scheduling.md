# Implementation Plan: V5 Capability-Aware Distributed AI Job Scheduling

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: none in this milestone (env + `GET/PUT /ai/scheduling/policy` only; no Nodes tab). Explicit `model_id` in the studio still wins over the scheduler
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). `supported_operations` eligibility on candidates; acceptance memory field is `estimated_memory_mb`; `prefer_device_class` is a hard filter; `runtime=execution_node` trust wins over descriptor `locality=remote`; `secondary_capabilities` from `node.capabilities` excluding primary; optional `AI_SCHEDULING_LOCAL_*` resource hints at projection; resolve/reschedule task expanded; standalone hardening Task 10 removed (coverage lives in Tasks 3/4/7)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: when scheduling is enabled and the caller did not pin an explicit `model_id`, a pure deterministic scheduler picks one registry candidate (controller-local or trusted-LAN `execution_node`) for a LanguageModel-backed AI operation. On node loss, a bounded reschedule may pick another eligible candidate without escalating the trust boundary. The working `composition.v2` is never written by the scheduler

## Roadmap Linkage
Milestone: "V5 Capability-aware AI job scheduling"
Rationale: ExecutionNodes already advertise capability, hardware, memory, and latency, but routing still requires an explicit `node:*` model id or static `AI_OP_*` defaults. This plan turns those heartbeats into policy-aware placement and safe recovery. The milestone is appended to `.ai-factory/ROADMAP.md` because the request asked to add it. Implementation does not edit `ROADMAP.md`.

## Goal

Select the most appropriate execution node or local runtime for an AI job from declared requirements and live peer snapshots, without silently shipping private prompts to public infrastructure.

Example placements the acceptance matrix must reproduce with fixture candidates:

| Job | Expected placement |
|-----|--------------------|
| LLM planner (`language_planner`, low memory) | ThinkBook / controller-local `local_openai_compatible` (llama.cpp) |
| Large symbolic model (`symbolic_composer`, high memory + `dgpu`) | Desktop GPU ExecutionNode |
| Planner job under `fastest_available` (latency race; job `priority` unused in v1) | Candidate with lowest estimated latency among eligible peers |

Ship:

1. Non-playable documents `scheduling.policy.v1`, `scheduling.job.v1`, `scheduling.candidate.v1`, `scheduling.decision.v1`, and `scheduling.attempt.v1`.
2. A deployment flag `AI_SCHEDULING_ENABLED` (default off). When off, `resolve_model_for_operation` behavior is unchanged.
3. Four locked policies: `prefer_local`, `fastest_available`, `memory_safe`, `fixed_node`.
4. A pure `schedule_ai_job` function (no FastAPI, no SQLite, no LLM) over candidate snapshots.
5. Integration into model resolution when scheduling is on and no explicit `model_id` is pinned.
6. Bounded reschedule on ExecutionNode transport / unavailable / busy failure without trust-boundary escalation.
7. Deterministic scheduler unit tests covering the example matrix and the privacy refusal cases.

Acceptance: With three fixture candidates — (A) controller-local llama.cpp `language_planner`, memory 8192/4096 MB, device `igpu`, latency 40 ms, trust `controller_local`; (B) desktop node `symbolic_composer` + `language_planner`, memory 65536/48000 MB, device `dgpu`, latency 25 ms, trust `trusted_lan`; (C) public OpenAI-compatible `language_planner`, locality `remote`, trust `public_cloud`, latency 120 ms — and default policy `prefer_local` with `allow_public_cloud=false`:

1. A `generate_planner` job with `privacy_class=private` selects A.
2. A `generate_composer` job requiring `symbolic_composer`, `estimated_memory_mb=16000`, `prefer_device_class=dgpu` selects B.
3. The same planner job with policy `fastest_available` and `allow_public_cloud=false` selects B (25 ms over A's 40 ms); C stays ineligible.
4. Policy `fixed_node` with `fixed_node_id` = B's `node_id` selects B's matching model; if B is `unavailable`, the decision is `no_eligible_candidate` (no silent move to A or C).
5. Policy `memory_safe` with estimated `estimated_memory_mb=30000` refuses A (available 4096) and selects B.
6. When the chosen ExecutionNode fails with `model_unavailable` after decision B, one reschedule excluding B's `node_id` under `prefer_local` for a planner job selects A and never C.
7. With `allow_public_cloud=false`, no decision ever returns C. With `allow_public_cloud=true` and policy `fastest_available`, C may win only when its latency is best among eligible candidates and the job `privacy_class` is `allow_public`.
8. With `AI_SCHEDULING_ENABLED` off, resolution ignores the scheduler and keeps today's explicit / op_default / fallback path.
9. The scheduler and reschedule path never write `tracks[].events[]`, never call an LLM to pick a node, and never log `input_text` / prompts / bearer tokens.

```text
AI operation request (no explicit model_id)
        │
        ▼
build scheduling.job.v1  +  candidate snapshots from registry/nodes
        │
        ▼
schedule_ai_job(job, candidates, policy)  →  scheduling.decision.v1
        │
        ▼
resolve → ainvoke_text_for_resolved / ExecutionNodeLanguageModel
        │
   on node loss (bounded)
        ▼
reschedule excluding failed node_ids; never escalate trust_boundary
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. A **job** is a non-playable `scheduling.job.v1` describing one AI operation placement request (not a neural PCM byte stream). A **candidate** is a `scheduling.candidate.v1` snapshot of one registry model (optionally bound to an ExecutionNode). A **decision** is `scheduling.decision.v1`, the pure scheduler output. A **policy** is `scheduling.policy.v1` with mode `prefer_local` | `fastest_available` | `memory_safe` | `fixed_node`. **Trust boundary** is `controller_local` | `trusted_lan` | `public_cloud`. **Private workload** means `privacy_class=private` (the default). **Reschedule** is a second pure decision after a failed attempt, with prior `node_id`s excluded. An **ExecutionNode** remains `execution.node.v1`; this plan does not add remote shell or change the typed `complete_text` worker contract. **Neural render** PCM egress stays on `/neural-audio/*`; this milestone does not stream audio over ExecutionNode workers. The acceptance example “neural render → remote GPU” is represented as an `audio_generation` capability preference against registered descriptors when/if such descriptors exist on the controller — not a new remote PCM protocol.

Predecessor: `.ai-factory/plans/v5-distributed-ai-execution-nodes.md` (peer registration, heartbeat resources, `runtime=execution_node`, invoke seam). Routing today: `backend/app/ai_runtime/routing.py` `resolve_model_for_operation`. Capability taxonomy: `backend/app/ai_runtime/capabilities.py`.

## Approach Evaluation (locked)

### Part A — Where placement decisions are made

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Ad-hoc if/else inside each generate/edit/arrange service** | Fast to hack | Diverges per surface; hard to test; duplicates privacy rules | **Reject** |
| **B. Extend `resolve_model_for_operation` only with more env defaults** | No new docs | Cannot weigh memory/GPU/latency; no reschedule story | **Reject** |
| **C. Pure `schedule_ai_job` over snapshots; wire as a resolution path when scheduling is enabled and `model_id` is unset; keep explicit `model_id` as pin** | Deterministic tests; one privacy gate; reschedule reuses the same function | Callers that already pin a model skip the scheduler | **Accepted** |

### Part B — What may be scheduled in this milestone

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Unified job bus for LLM, Music Transformer train, neural PCM, and Demucs** | One queue | Touches offline CLIs and PCM roots; ExecutionNode has no PCM op | **Reject** |
| **B. Only `runtime=execution_node` peers; ignore controller-local models** | Narrow | Breaks “ThinkBook llama.cpp” example; local is a first-class candidate | **Reject** |
| **C. LanguageModel-backed ops whose candidates are registry `ModelDescriptor`s (local openai-compatible, fake, execution_node, remote openai-compatible). Symbolic composer descriptors included when capability matches. Neural PCM remoting and MT training CLIs stay out of scope** | Matches existing invoke seam; covers planner/composer/edit examples | AUDIO_RENDER stays on current neural enqueue | **Accepted** |

### Part C — Privacy and public cloud

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Prefer public cloud whenever latency is lower** | Fast | Silently moves private prompts off-LAN | **Reject** |
| **B. Hard-ban all `locality=remote` forever** | Safe | Blocks intentional `AI_FALLBACK_*` / user-pinned cloud models | **Reject** |
| **C. Tag candidates with `trust_boundary`. Default job `privacy_class=private`. Scheduler never selects `public_cloud` unless policy `allow_public_cloud=true` **and** job `privacy_class=allow_public`. Explicit pinned `model_id` still allowed (user chose it). Reschedule never raises the max trust boundary above the failed attempt’s boundary** | Matches “never silently move private workloads” | Operators must set two flags to use cloud via scheduler | **Accepted** |

### Part D — Policy modes

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Single opaque score with tunable weights and no named modes** | Flexible | Hard to explain; acceptance matrix unclear | **Reject** |
| **B. Ship exactly four named modes: `prefer_local`, `fastest_available`, `memory_safe`, `fixed_node`. Filter then total-order with locked tie-breaks** | Matches the request; deterministic | Later weighted modes need a new plan | **Accepted** |

### Part E — Failure and reschedule

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Retry the same node until timeout** | Simple | Cannot recover from node loss | **Reject** |
| **B. Fall through to any `AI_FALLBACK_*` including public cloud without re-checking trust** | Reuses env | Silent privacy escalation; forbidden by predecessor plan | **Reject** |
| **C. Record `scheduling.attempt.v1` with failure code; call `schedule_ai_job` again with `exclude_node_ids` / `exclude_model_ids`; clamp attempts by `AI_SCHEDULING_MAX_ATTEMPTS` (default 2, clamp 1..4); refuse if next candidate would exceed the original decision’s trust boundary or policy still has no eligible peer** | Safe recovery; testable | In-flight remote tasks remain lost on controller restart (unchanged from execution-node v1) | **Accepted** |

### Part F — Persistence and UI

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New studio Nodes tab that can start jobs and shells** | Visible | Predecessor forbade shell UI; large frontend scope | **Reject** |
| **B. Env-only policy, no HTTP inspect** | Tiny | Cannot change mode without restart; hard to demo | **Reject** |
| **C. Env defaults + optional SQLite singleton `scheduling_policy` document via `GET/PUT /ai/scheduling/policy` when scheduling enabled. No SPA panel in this milestone. Docs describe the four modes** | Inspectable; matches preference-style settings without a tab | Operators use HTTP or env | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| ExecutionNode document | `execution.node.v1` with `capabilities`, `hardware.device_class`, `resources.memory_*`, `health.latency_ms`, `availability`, `installed_models[].primary_capability` / `locality` / `status` | Project candidates from live nodes + registry attach |
| Address allowlist | Loopback + RFC1918; refuses link-local and cloud metadata | `trusted_lan` peers are already non-public by registration |
| Registry attach | `_attach_execution_node_descriptors` registers `node:<hex>:<local>` with `runtime=execution_node` and limits `execution_node_id` / `execution_node_address` | Candidate builder reads descriptors after `reload_registry` |
| Resolve order | explicit `model_id` → legacy → `AI_OP_*` → global → optional `AI_FALLBACK_*` | Insert schedule path only when flag on and explicit id absent |
| Invoke | `ainvoke_text_for_resolved` → `ExecutionNodeLanguageModel.complete_text` | Reschedule wraps this seam; do not change worker HTTP contract |
| Task index | Process-memory `execution_node_tasks` | Cancel still uses it; scheduler does not persist task text |
| Capability map | `ModelCapability` + `OPERATION_DEFAULT_CAPABILITY` | Job `required_capability` defaults from operation |
| Latest migration | `20261003_0024` execution_nodes, `down_revision` `20261002_0023` | Next revision `20261003_0025`, `down_revision` `20261003_0024` |
| Agent boundary | `test_ai_agents_architecture.py` forbids `execution_node_store` | Add forbid for `from app.services.scheduling_policy_store` |
| Opt-in flag pattern | `AI_EXECUTION_NODES_ENABLED` / `COLLABORATION_ENABLED` truthy set | Copy for `AI_SCHEDULING_ENABLED` |
| Local descriptor limits | `bootstrap` attaches `public_limits(local_settings)` on `local_openai_compatible` | Candidate projection may merge optional `AI_SCHEDULING_LOCAL_*` into resource fields when limits omit them |
| Node attach locality | `_attach_execution_node_descriptors` sets `locality="remote"` for all `node:*` rows | Trust mapping must use `runtime=execution_node` → `trusted_lan`, not `locality` |
| Routing path literals | `test_ai_runtime_routing.py` asserts `explicit` / `legacy` / `op_default` / `global` / `fallback` | Extend with `schedule` when the flag is on and no pin |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Pure scheduler | No ranking of peers by memory/GPU/latency/policy; no `supported_operations` filter at schedule time |
| Trust boundary tags | Attached `node:*` rows use `locality=remote`; must not treat LAN peers as `public_cloud` |
| Policy document | No named modes or `allow_public_cloud` |
| Local resource hints | Controller-local descriptors lack heartbeat memory/GPU/latency for `memory_safe` / `prefer_local` |
| Reschedule | Transport failure raises `ModelUnavailableError`; may hit cloud via `AI_FALLBACK_*` without trust re-check |
| Deterministic fixtures | No acceptance matrix for ThinkBook vs desktop GPU vs public |
| Scheduling provenance | Resolved model provenance lacks decision reason codes |

### Coupling risks to avoid

1. Writing `tracks[].events[]` or importing `project_store` from the scheduler.
2. Calling an LLM or Music Transformer to choose a node.
3. Streaming neural PCM or MusicXML/MIDI bytes through ExecutionNode workers.
4. Silent selection of `public_cloud` for `privacy_class=private`.
5. Reschedule that widens trust beyond the failed attempt (e.g. LAN failure → OpenAI).
6. Importing scheduling store or FastAPI from `ai_agents/`.
7. Logging prompts, `input_text`, bearer tokens, absolute weight paths, or full candidate dumps with text.
8. Inventing `composition.v5`, a new agent id, or remote shell routes.
9. Editing `ROADMAP.md` during implementation.
10. Enabling scheduling by default.
11. Letting `fixed_node` silently fall back when the pinned node is down.
12. Changing `LanguageModel` protocol method signatures.

## Scope And Decisions

### In scope
- Schemas for policy, job, candidate, decision, attempt (candidate includes `supported_operations`).
- Env flag + policy settings (env bootstrap + SQLite singleton) + optional `AI_SCHEDULING_LOCAL_*` resource hints.
- Pure scheduler with four modes and locked filters/tie-breaks (hard `prefer_device_class`, operation support, `available` only).
- Candidate projection from registry descriptors + TTL-projected ExecutionNode snapshots, with trust precedence and `node.capabilities` → secondary.
- Wire into `resolve_model_for_operation` (schedule path) and a thin reschedule helper around `ainvoke_text_for_resolved` when schedule path or `execution_node`.
- Deterministic unit tests for the acceptance matrix and privacy/reschedule cases (no standalone “finish tests” task).
- Docs page `docs/ai-job-scheduling.md` + links from `docs/execution-nodes.md` and `docs/ai-runtime.md`.
- Soft `/ready` block `ai_scheduling` (never fails overall readiness).
- Provenance fragment fields: `schedule_policy`, `schedule_reason_codes`, `schedule_attempt` (no prompts).

### Out of scope
- Frontend Nodes / Scheduling tab.
- Remote neural audio PCM over ExecutionNode.
- Music Transformer training job distribution / `DATASET_ROOT` workers.
- Multi-controller consensus, mDNS, or cloud mesh.
- Weighted ML ranker for placement.
- Changing worker `complete_text` contract or adding shell ops.
- Auto-pinning cloud as `AI_FALLBACK_*` from the scheduler (existing env fallback remains a separate, explicit operator config outside schedule path when scheduling is off; when scheduling is on, fallback after schedule exhaustion must still honor trust clamps — see locked decision 7).
- `ROADMAP.md` during implementation.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/scheduling_schemas.py`. `extra=forbid`. This module does not import FastAPI, torch, stores, video, film, agent, adaptive engine, or embedding modules. Forbidden keys anywhere → `scheduling_forbidden_payload` (exact key match): `events`, `notes`, `pitch`, `composition`, `composition_json`, `api_key`, `authorization`, `shell`, `command`, `argv`, `subprocess`, `prompt`, `input_text`, `output_text`.

Typed errors: `SchedulingError` + `map_scheduling_error_to_http`:

| Code | HTTP |
|------|------|
| `scheduling_disabled` | 404 |
| `scheduling_forbidden_payload` | 422 |
| `scheduling_no_eligible_candidate` | 503 |
| `scheduling_fixed_node_unavailable` | 503 |
| `scheduling_trust_refused` | 403 |
| `scheduling_conflict` | 409 |
| `scheduling_invalid_policy` | 422 |

`SchedulingPolicyV1`. Schema version `scheduling.policy.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `scheduling.policy.v1` |
| `mode` | `prefer_local` \| `fastest_available` \| `memory_safe` \| `fixed_node` |
| `allow_public_cloud` | bool, default false |
| `fixed_node_id` | optional `^node_[0-9a-f]{16}$`; required when mode is `fixed_node` |
| `fixed_model_id` | optional string 1..160; when set with `fixed_node`, must match that node’s catalog id or a controller-local id |
| `max_attempts` | int 1..4, default 2 |
| `updated_at` | optional server timestamp |
| `document_revision` | int ≥ 1 CAS |

`SchedulingJobV1`. Schema version `scheduling.job.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `scheduling.job.v1` |
| `operation` | `AiOperation` string value |
| `required_capability` | `ModelCapability` string |
| `privacy_class` | `private` \| `allow_public` (default `private`) |
| `priority` | int 0..100, default 50; higher is more urgent for tie-break only after policy order |
| `estimated_memory_mb` | optional int ≥ 0 |
| `prefer_device_class` | optional `cpu` \| `igpu` \| `dgpu` |
| `exclude_node_ids` | list max 32 of node ids |
| `exclude_model_ids` | list max 64 of model ids |
| `purpose` | optional string max 64 (never a prompt) |

`SchedulingCandidateV1`. Schema version `scheduling.candidate.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `scheduling.candidate.v1` |
| `model_id` | string 1..160 |
| `runtime` | string 1..64 |
| `primary_capability` | string |
| `secondary_capabilities` | list max 8 |
| `supported_operations` | list 0..32 of `AiOperation` string values; empty → ineligible |
| `trust_boundary` | `controller_local` \| `trusted_lan` \| `public_cloud` |
| `node_id` | optional `node_*` |
| `device_class` | `cpu` \| `igpu` \| `dgpu` \| `unknown` |
| `memory_available_mb` | optional int |
| `memory_total_mb` | optional int |
| `estimated_latency_ms` | optional int ≥ 0 |
| `availability` | `available` \| `busy` \| `draining` \| `unavailable` |
| `status` | model health status string (`ready` required to be eligible) |
| `locality` | `local` \| `remote` (informational; trust mapping does not key off this alone) |

Trust mapping (locked). Evaluate in this order; first match wins:

| Source | `trust_boundary` |
|--------|------------------|
| `runtime == execution_node` (registered peer; address already allowlisted), **even when descriptor `locality` is `remote`** | `trusted_lan` |
| `runtime` in `{local_openai_compatible, fake, stub, fake_symbolic, music_transformer, plugin, personal_composer}` and no `execution_node_id` | `controller_local` |
| else if `locality == remote` (openai-compatible / other public API descriptors) | `public_cloud` |
| else | `controller_local` |

Regression: a `node:*` descriptor with `locality="remote"` must never become `public_cloud`.

`SchedulingDecisionV1`. Schema version `scheduling.decision.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `scheduling.decision.v1` |
| `selected_model_id` | optional; null when none |
| `selected_node_id` | optional |
| `policy_mode` | copy of policy mode |
| `trust_boundary` | of selected candidate; omitted when none |
| `reason_codes` | list 1..16 of short snake_case codes |
| `eligible_count` | int ≥ 0 |
| `attempt_index` | int ≥ 1 |

`SchedulingAttemptV1`. Schema version `scheduling.attempt.v1`. Fields: `attempt_index`, `decision`, `failure_code` optional, `finished_ok` bool.

**2. Pure scheduler** (`backend/app/services/ai_job_scheduler.py`)

```text
schedule_ai_job(job, candidates, policy) -> SchedulingDecisionV1
```

No I/O. Eligibility filters (all must pass):

1. `status == ready` and `availability == available` only (`busy` / `draining` / `unavailable` out). Registry attach may still list busy `node:*` rows; the scheduler uses the candidate’s projected availability.
2. `job.operation` is in `supported_operations`. Empty `supported_operations` → ineligible.
3. `required_capability` equals `primary_capability` or is in `secondary_capabilities`.
4. Trust: if `privacy_class=private` or `allow_public_cloud=false`, drop `public_cloud`.
5. Memory: if `estimated_memory_mb` is set (any mode), require `memory_available_mb is not None` and `memory_available_mb >= estimated_memory_mb` (hard floor). Under `memory_safe`, also prefer higher headroom in ranking.
6. Device: if `prefer_device_class` is set, hard-filter to that exact `device_class`. `unknown` never matches a concrete preference.
7. Exclusions: drop `exclude_node_ids` / `exclude_model_ids`.
8. `fixed_node`: hard-filter to `fixed_node_id` / `fixed_model_id`; if empty → `scheduling_fixed_node_unavailable` / decision with `reason_codes=["fixed_node_unavailable"]`.

Ranking by mode (ascending sort key; first wins):

| Mode | Primary key | Then |
|------|-------------|------|
| `prefer_local` | trust rank (`controller_local=0`, `trusted_lan=1`, `public_cloud=2`) | latency asc (missing=10^9), memory_available desc, priority unused on candidate, `model_id` asc |
| `fastest_available` | latency asc (missing=10^9) | trust rank, `model_id` asc |
| `memory_safe` | `(memory_available - estimated)` desc; missing memory sorts last | trust rank, latency asc, `model_id` asc |
| `fixed_node` | only the filtered set; if multiple models on the node, prefer capability match then latency asc then `model_id` asc | |

Job `priority` breaks ties only when the two preceding keys are equal: higher job priority does not reorder candidates; it is reserved for a future multi-queue. For this milestone, document priority as accepted on the job DTO and unused in ranking (tests assert it does not change order). Reason code `priority_ignored_v1` may be included when priority ≠ 50.

Reason codes (examples): `prefer_local`, `lowest_latency`, `memory_headroom`, `fixed_node`, `capability_match`, `trust_filtered_public`, `excluded_failed_node`, `no_eligible_candidate`.

**3. Settings** (`backend/app/scheduling_settings.py`)

| Env | Rule |
|-----|------|
| `AI_SCHEDULING_ENABLED` | truthy `1/true/yes/on`; default off |
| `AI_SCHEDULING_DEFAULT_MODE` | one of the four modes; default `prefer_local` |
| `AI_SCHEDULING_ALLOW_PUBLIC_CLOUD` | truthy; default off |
| `AI_SCHEDULING_MAX_ATTEMPTS` | default 2, clamp 1..4 |
| `AI_SCHEDULING_FIXED_NODE_ID` | optional; used when default mode is `fixed_node` |
| `AI_SCHEDULING_LOCAL_MEMORY_AVAILABLE_MB` | optional int ≥ 0; controller-local default when descriptor limits omit memory |
| `AI_SCHEDULING_LOCAL_MEMORY_TOTAL_MB` | optional int ≥ 0 |
| `AI_SCHEDULING_LOCAL_DEVICE_CLASS` | optional `cpu` \| `igpu` \| `dgpu` \| `unknown` |
| `AI_SCHEDULING_LOCAL_ESTIMATED_LATENCY_MS` | optional int ≥ 0 |

When `AI_EXECUTION_NODES_ENABLED` is off, scheduling may still rank controller-local vs public_cloud descriptors; `trusted_lan` candidates are simply absent. Local `*_LOCAL_*` hints never apply to `runtime=execution_node` rows (those use heartbeat resources).

**4. Store** (`backend/app/services/scheduling_policy_store.py`)

SQLite singleton table `scheduling_policy(id INTEGER PRIMARY KEY CHECK (id = 1), body_json, document_revision, updated_at)`. CAS on PUT. No Composition. Migration `20261003_0025_ai_scheduling.py`.

**5. Candidate projection** (`backend/app/services/scheduling_candidates.py`)

`build_scheduling_candidates(env=...) -> list[SchedulingCandidateV1]`:

1. Reload/list registry models; for each ready descriptor, build a candidate.
2. For `runtime=execution_node`, load the TTL-projected node via `execution_node_service` (`list_nodes` / get). Copy `hardware.device_class`, `resources.memory_*`, `health.latency_ms` → `estimated_latency_ms`, and live `availability`. Set `secondary_capabilities` to `node.capabilities` excluding the row’s `primary_capability` (operators must advertise accurately). Copy `supported_operations` from the descriptor.
3. For controller-local runtimes, read optional resource ints/strings from `descriptor.limits` keys `memory_available_mb`, `memory_total_mb`, `device_class`, `estimated_latency_ms`; if absent, fill from `AI_SCHEDULING_LOCAL_*` env defaults; if still absent, leave `None` (latency sorts last; memory hard-floor excludes). `availability=available` when status is ready. `supported_operations` from the descriptor.
4. Apply locked trust mapping (execution_node before locality).
5. Never log address strings at INFO; DEBUG may count by trust boundary only.

**6. Resolve integration** (`backend/app/ai_runtime/routing.py`)

When `AI_SCHEDULING_ENABLED` and selection has no explicit `model_id` and no legacy provider+model pin:

1. Build job from operation (+ optional request overrides for memory/device/privacy if present on selection options; default private).
2. Load effective policy (store if present else env defaults).
3. `decision = schedule_ai_job(...)`.
4. If `selected_model_id` set → resolve that descriptor with `resolution_path="schedule"`. Add literal `"schedule"` to **both** `ResolutionPath` aliases (`routing.py` and `types.py` `ResolvedModel.resolution_path`).
5. If none → raise `SchedulingError` / map to existing unavailable. Apply `AI_FALLBACK_*` only when scheduling exhausted **and** the fallback descriptor’s trust_boundary rank is ≤ the max allowed for the job (`private` ⇒ not `public_cloud` unless both allow flags); otherwise do not apply public fallback.

Explicit `model_id` → unchanged (no scheduler). This is how users pin “fixed” without policy mode.

Extend `backend/tests/test_ai_runtime_routing.py` with a schedule-path case (flag on, no pin → `resolution_path == "schedule"`).

**7. Reschedule helper** (`backend/app/services/ai_job_reschedule.py` or functions beside invoke)

Wrap `ainvoke_text_for_resolved` when `resolved.resolution_path == "schedule"` **or** `runtime == execution_node`:

1. On `ModelUnavailableError` with codes `model_unavailable` / transport / busy mapped failures (**not** `operation_cancelled`): if attempts remain, append failed `node_id`/`model_id` to excludes, rebuild candidates, `schedule_ai_job` again with `attempt_index+1`, invoke new resolution.
2. Never select a candidate with `trust_boundary` rank worse than the failed decision’s boundary.
3. Cancel remains cooperative via existing task cancel + `mark_run_cancelled`; cancelled ops do not reschedule.
4. Non-text runtimes (Music Transformer generate, neural PCM) are not wrapped in this milestone.

**8. HTTP** (`backend/app/routers/ai_scheduling.py`)

| Method | Path | Notes |
|--------|------|-------|
| GET | `/ai/scheduling/policy` | Flag off → 404; returns effective policy (store row or env materialization). No bearer required (same local-studio posture as `GET /ai/models`) |
| PUT | `/ai/scheduling/policy` | Body includes `document_revision` CAS; mismatch → `scheduling_conflict` 409; validates mode/fixed_node |
| POST | `/ai/scheduling/preview` | Body: job (+ optional candidate override for tests); returns decision; never invokes a model; never logs job purpose as a prompt |

Mount from `main.py` when role is controller/both. No worker mount needed.

**9. Ready**

Soft block `ai_scheduling`: `{enabled, mode, allow_public_cloud}` — informational only.

**10. Logging**

Safe: `policy_mode`, `operation`, `required_capability`, `selected_model_id`, `selected_node_id`, `trust_boundary`, `reason_codes`, `eligible_count`, `attempt_index`, `failure_code`, `privacy_class`. Never: prompts, `input_text`, tokens, Authorization, event arrays, absolute paths.

**11. Tests** (deterministic; no network)

| File | Covers |
|------|--------|
| `backend/tests/test_ai_job_scheduler.py` | Acceptance matrix rows 1–7; `supported_operations` filter; hard `prefer_device_class`; tie-breaks; fixed_node refusal; trust filter; `priority` does not reorder |
| `backend/tests/test_scheduling_schemas.py` | Forbidden keys; policy validation; candidate `supported_operations` |
| `backend/tests/test_scheduling_settings.py` | Flag parser; clamps; local resource env parse |
| `backend/tests/test_scheduling_policy_store.py` | CAS singleton |
| `backend/tests/test_scheduling_candidates.py` | `runtime=execution_node` + `locality=remote` → `trusted_lan`; secondary from `node.capabilities`; local `AI_SCHEDULING_LOCAL_*` fill |
| `backend/tests/test_scheduling_resolve_integration.py` | Flag off noop; schedule path; reschedule excludes failed node; no public escalation; cancel does not reschedule |
| `backend/tests/test_ai_runtime_routing.py` | `resolution_path == "schedule"` when enabled and unpinned |
| `backend/tests/test_scheduling_routes.py` | GET/PUT/preview; 404 when disabled; CAS 409 |
| Extend `test_ai_agents_architecture.py` | Forbid `from app.services.scheduling_policy_store` (in Task 4) |

Fixture candidates for the pure scheduler are constructed in-test as `SchedulingCandidateV1` models (no SQLite). Acceptance B may set `secondary_capabilities` directly on fixtures; projection tests cover the node.capabilities rule separately.

**12. Docs**

- Add `docs/ai-job-scheduling.md` (modes, trust boundaries, reschedule, env, preview route, examples).
- Link from `docs/execution-nodes.md` and `docs/ai-runtime.md`.
- Update `AGENTS.md` entry points for scheduling modules.
- `.env.example` keys for the new env vars.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat(scheduling): add policy schemas, settings, and pure scheduler`
- **Commit 2** (after tasks 4–6): `feat(scheduling): persist policy and project capability-aware candidates`
- **Commit 3** (after task 7): `feat(scheduling): wire schedule resolve path and safe reschedule`
- **Commit 4** (after tasks 8–10): `feat(scheduling): HTTP policy/preview, ready block, and docs`

## Tasks

### Phase 1: Contracts and pure scheduler
- [x] Task 1: Add `backend/app/scheduling_schemas.py` with policy/job/candidate/decision/attempt models (candidate includes `supported_operations`), forbidden-key scan, `SchedulingError` HTTP map, and trust-boundary literals. Unit-test forbidden keys, `fixed_node` validation, and empty `supported_operations`.

  LOGGING: DEBUG on schema rejection code only (no payload bodies).

  Files: `backend/app/scheduling_schemas.py`, `backend/tests/test_scheduling_schemas.py`

- [x] Task 2: Add `backend/app/scheduling_settings.py` parsing `AI_SCHEDULING_*` (including optional `AI_SCHEDULING_LOCAL_*` resource hints) with the collaboration/execution-node truthy set; defaults off; clamp `max_attempts`.

  LOGGING: INFO once on load with `{enabled, mode, allow_public_cloud, max_attempts, has_local_resource_hints}` — never log secrets; `fixed_node_id` may be logged (public id).

  Files: `backend/app/scheduling_settings.py`, `backend/tests/test_scheduling_settings.py`

- [x] Task 3: Implement pure `schedule_ai_job` in `backend/app/services/ai_job_scheduler.py` with locked filters (including `supported_operations`, hard `prefer_device_class`, `availability==available` only), four mode orderings, reason codes, and the full acceptance matrix (rows 1–7) plus `priority` no-op assertion. No FastAPI/SQLite/httpx imports. (depends on 1)

  LOGGING: DEBUG decision summary `{policy_mode, selected_model_id, selected_node_id, eligible_count, reason_codes, attempt_index}`.

  Files: `backend/app/services/ai_job_scheduler.py`, `backend/tests/test_ai_job_scheduler.py`

<!-- Commit checkpoint: tasks 1-3 -->

### Phase 2: Persistence, candidates, local hints
- [x] Task 4: Alembic `20261003_0025_ai_scheduling.py` + `scheduling_policy_store` singleton CAS get/put. Seed nothing; first GET materializes env defaults without requiring a row. Extend `test_ai_agents_architecture.py` to forbid `from app.services.scheduling_policy_store`.

  LOGGING: INFO on put `{document_revision, mode}`; DEBUG on get hit/miss.

  Files: `backend/app/db/alembic/versions/20261003_0025_ai_scheduling.py`, `backend/app/services/scheduling_policy_store.py`, `backend/tests/test_scheduling_policy_store.py`, `backend/tests/test_scheduling_migration.py`, `backend/tests/test_ai_agents_architecture.py`

- [x] Task 5: `build_scheduling_candidates` projecting registry + TTL-projected ExecutionNodes (`execution_node_service.list_nodes`) into `SchedulingCandidateV1`. Lock trust precedence (`runtime=execution_node` → `trusted_lan` even when `locality=remote`). Set `secondary_capabilities` from `node.capabilities` excluding primary. Copy `supported_operations`. Skip non-ready models. (depends on 1, 3)

  LOGGING: DEBUG `{candidate_count, trusted_lan_count, public_cloud_count}` — no address strings at INFO.

  Files: `backend/app/services/scheduling_candidates.py`, `backend/tests/test_scheduling_candidates.py`

- [x] Task 6: Merge optional `AI_SCHEDULING_LOCAL_*` (and descriptor `limits` resource keys) into controller-local candidates only; never override ExecutionNode heartbeat resources. Document the keys in settings comments. Unit-test fill vs leave-None behavior. (depends on 2, 5)

  LOGGING: DEBUG when local hints applied `{applied_keys}` without numeric dump at INFO.

  Files: `backend/app/services/scheduling_candidates.py`, `backend/app/scheduling_settings.py`, `backend/tests/test_scheduling_candidates.py`

<!-- Commit checkpoint: tasks 4-6 -->

### Phase 3: Resolve and reschedule
- [x] Task 7: Wire schedule path into `resolve_model_for_operation` (`resolution_path="schedule"` on both `ResolutionPath` typedefs); extend `test_ai_runtime_routing.py`. Add reschedule helper around `ainvoke_text_for_resolved` when `resolution_path=="schedule"` or `runtime==execution_node`, with trust clamp, `max_attempts`, and no reschedule on `operation_cancelled`. Explicit `model_id` bypasses scheduling. When scheduling exhausted, do not apply a worse trust_boundary via `AI_FALLBACK_*`. Cover flag-off noop, reschedule-without-escalation, and fixed_node hard fail in integration tests. (depends on 3, 4, 5, 6)

  LOGGING: INFO on schedule resolve and each reschedule attempt; WARN on trust refusal / no eligible candidate.

  Files: `backend/app/ai_runtime/routing.py`, `backend/app/ai_runtime/types.py`, `backend/app/ai_runtime/invoke_text.py`, `backend/app/services/ai_job_reschedule.py`, `backend/tests/test_scheduling_resolve_integration.py`, `backend/tests/test_ai_runtime_routing.py`

<!-- Commit checkpoint: task 7 -->

### Phase 4: HTTP, ready, docs
- [x] Task 8: Router `GET/PUT /ai/scheduling/policy` and `POST /ai/scheduling/preview`; CAS 409 on revision mismatch; mount in `main.py` for controller/both; map domain errors; flag off → 404. (depends on 4, 5)

  LOGGING: INFO on PUT and preview with decision summary fields only.

  Files: `backend/app/routers/ai_scheduling.py`, `backend/app/main.py`, `backend/tests/test_scheduling_routes.py`

- [x] Task 9: Soft `/ready` block `ai_scheduling`; `.env.example` keys (including `AI_SCHEDULING_LOCAL_*`); provenance fields on resolve when schedule path used (`schedule_policy`, `schedule_reason_codes`, `schedule_attempt`). (depends on 2, 7)

  LOGGING: DEBUG ready block assembly.

  Files: `backend/app/ready.py` (or existing ready helpers), provenance builders touched by routing, `.env.example`

- [x] Task 10: Docs `docs/ai-job-scheduling.md` (modes, trust precedence, local hints, reschedule, env, preview route, examples); link from `docs/execution-nodes.md` and `docs/ai-runtime.md`; update `AGENTS.md` entry points for scheduling modules. No SPA. (depends on 7, 8, 9)

  LOGGING: n/a (docs only).

  Files: `docs/ai-job-scheduling.md`, `docs/execution-nodes.md`, `docs/ai-runtime.md`, `AGENTS.md`

<!-- Commit checkpoint: tasks 8-10 -->

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer extending `routers/` + `services/` over growing unrelated logic in `main.py`.
3. Keep `ai_job_scheduler.py` import-clean (pure). Candidate projection and store are separate modules.
4. Predecessor execution-node security rules still apply: no shell, no prompt logging, bearer always when nodes enabled.
5. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
6. Do not invent a universal reschedule bus for Music Transformer or neural PCM in this milestone.
)
