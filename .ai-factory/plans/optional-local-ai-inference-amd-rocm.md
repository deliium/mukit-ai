# Implementation Plan: Optional Local AI Inference (AMD Ryzen / ROCm)

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Target host: Lenovo ThinkBook 14 G7+ AKP, AMD Ryzen AI APU, Radeon iGPU (gfx1152), 32 GB unified memory
- Default local runtime: llama.cpp OpenAI-compatible server (`local-ai` Compose profile)
- Optional throughput runtime: vLLM + ROCm (separate Compose profile)
- App coupling: none to llama.cpp or vLLM — both expose the same OpenAI-compatible LocalLanguageModel surface

## Roadmap Linkage
Milestone: "Optional local AI inference (AMD/ROCm)"
Rationale: Follows completed V3 unified AI runtime; enables selecting a locally hosted language model from the existing frontend selector while keeping remote providers and default Docker startup unchanged. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint).

## Goal

Allow supported language models to run **optionally** on the development machine via Docker profiles, appear in the same frontend model selector as remote providers, and successfully execute a supported AI composition operation — without requiring local AI for normal app startup, without coupling application logic to a specific inference engine, and with memory-safe defaults appropriate for a 32 GB AMD APU laptop.

## Audit Summary (current state)

### Docker / host
| Item | Today |
|------|--------|
| Compose | `docker-compose.yml`: `backend` + `frontend` only; **no profiles** |
| Dev override | `compose.dev.yml`: bind-mount + reload; no GPU devices |
| Backend image | `python:3.14-slim` + FluidSynth; creates empty `models/` dir; no ROCm/NPU |
| Health | Backend `/health` CMD; frontend wget; `/ready` includes LLM provider names + AI registry summary |
| Env | Remote OpenAI/DeepSeek + fake mode + `AI_OP_*` / `AI_FALLBACK_*`; **no LOCAL_*** |
| Secrets | Keys stay backend-only; nginx proxies `/ai/` and `/llm/` |

### AI runtime V3 (foundation — do not regress)
| Layer | Today |
|-------|--------|
| Protocols | `LanguageModel.complete_text` in `ai_runtime/protocols.py` |
| Runtimes | `openai_compatible_chat`, `fake`, `stub` (`ModelRuntimeId` Literal) |
| Transport | Shared `build_chat_openai` / `ainvoke_chat_text` (base_url supported) |
| Registry | Env bootstrap + optional `AI_MODEL_REGISTRY_PATH`; stubs for embed/transcribe/audio |
| Status | `ready` \| `unconfigured` \| `unavailable` \| `degraded` |
| Locality | `local` \| `remote` (fake already `local`; remotes `remote`) |
| Discovery | `GET /ai/models` + compat `GET /llm/models` |
| Frontend | `MusicGenerator` select uses `availableLlmModels` from `/llm/models` (`provider`/`model`/`display_name`) |
| Explicit out-of-scope in V3 plan | Local GGUF / vLLM / llama.cpp runtime |

### Coupling risks to avoid
1. Importing llama.cpp or vLLM Python APIs into `backend/app` services.
2. Loading GGUF/safetensors weights inside the FastAPI process or `/ready`.
3. Making default `docker compose up` pull multi-GB images or require `/dev/kfd` / `/dev/dri`.
4. Exposing absolute weight paths, host usernames, or ROCm device strings to the frontend.
5. Putting NPU (Ryzen AI) on the critical path — document only if discovered stable; do not require it.

## Scope And Decisions

### In scope
- Optional Compose profiles: conceptual **default** (current stack), **`local-ai`** (llama.cpp), **`local-ai-vllm`** (vLLM+ROCm), **`training`** (placeholder / docs stub for PyTorch+ROCm — not a full training loop).
- `LocalLanguageModel` implementing the V3 `LanguageModel` protocol via OpenAI-compatible HTTP to a sidecar (reuse `build_chat_openai`).
- Config for model name/path (server-side only), context size, quantization, device, memory limit, concurrency, timeout.
- Health/status: `loaded`, `loading`, `unavailable`, `out_of_memory`, `unsupported_device` (mapped into registry discovery + `/ready` local-AI subsection).
- Memory-safe defaults for 32 GB UMA (small quantized chat model; concurrency 1; modest context).
- Tests (unit + mocked HTTP local server), structured logging, docs (Linux/ROCm, passthrough, install, troubleshooting).
- Frontend: local models selectable in the **existing** provider/model select; optional locality/status hint without a new picker architecture.

### Out of scope
- NPU / Ryzen AI Execution Provider as a required backend.
- Training loops, fine-tuning UI, or music-symbolic local models (Music Transformer) — `training` profile is scaffolding/docs only.
- Changing `composition.v2` playable contract.
- Per-operation local vs remote auto-routing (user selects model; existing `AI_OP_*` / fallback remain explicit).
- Shipping model weights in the git repo or default images.

### Architecture decisions (locked)

**1. OpenAI-compatible boundary (mandatory)**  
```text
Frontend → FastAPI (ai_runtime) → LocalLanguageModel → HTTP OpenAI chat API
                                      ↑
                         llama.cpp server  OR  vLLM
                         (Compose sidecar only)
```
Application code must not branch on `llama.cpp` vs `vLLM`. Docker profiles differ only in sidecar image, command, and default `LOCAL_LLM_BASE_URL`.

**2. Runtime id**  
Add `local_openai_compatible` to `ModelRuntimeId` (or treat as documented alias that constructs `LocalLanguageModel`). Prefer a distinct runtime string so discovery/provenance can show `runtime=local_openai_compatible` while still using the shared ChatOpenAI factory. Do **not** invent a second chat transport stack.

**3. Provider id**  
Register local chat models as `local:<model_name>` (canonical `model_id`), `provider=local`, `locality=local`. Placeholder API key (e.g. `local` / `not-needed`) satisfies ChatOpenAI; never treat it as a secret worth redacting beyond normal key hygiene.

**4. Status vocabulary**  
Extend discovery status (additive) so local lifecycle is visible without breaking remote `ready`/`unconfigured`:

| Status | Meaning |
|--------|---------|
| `loading` | Sidecar reachable but model not yet ready / load in progress |
| `loaded` / `ready` | Prefer exposing **`ready`** for UI “usable” filters; also accept **`loaded`** as alias that lists as selectable equivalent to ready, OR map loaded→ready in catalog with `health.detail=loaded` — **choose one in Task 3 and keep tests consistent**. Recommended: catalog `status=ready` when usable; `health.detail` carries `loaded`\|`loading`\|`out_of_memory`\|`unsupported_device` for precision. Public docs list the five user-facing states. |
| `unavailable` | Profile off, sidecar down, or probe failed |
| `out_of_memory` | Probe/inference reported OOM (detail + status) |
| `unsupported_device` | Configured device backend not available in container/host |

Remote models keep existing status semantics. `/ai/models` warnings must mention local profile when only remotes are missing.

**5. Health probing**  
- Backend never loads weights.
- Optional non-blocking probe to sidecar (`GET /v1/models` or engine health) on registry reload / dedicated refresh — timeouts short; failures → `unavailable` with `error_type` only in logs.
- `/ready` and `/health`: app stays healthy if local AI is off or down; report `local_ai` subsection (`enabled`, `reachable`, `status`, `model_id`) without failing overall readiness unless a future strict flag is added (default: soft).

**6. Memory-safe defaults (32 GB UMA)**  
| Knob | Default |
|------|---------|
| Model class | ~7–8B instruct GGUF, Q4_K_M (or equivalent); **not** 70B |
| Context | 4096 (configurable up to documented max) |
| Concurrency | 1 |
| Device | `rocm` preferred on gfx1152; `vulkan` documented fallback; `cpu` last resort |
| Memory limit | ~12288–16384 MiB budget hint for sidecar (leave ~12–16 GB for OS + Mukit + browser) |
| Timeout | ≥180s (composition stages are long); align with `LLM_REQUEST_TIMEOUT_SECONDS` |
| Quantization | `Q4_K_M` default for llama.cpp; vLLM uses documented dtype/quant env |

**7. Device policy**  
- Prefer ROCm for llama.cpp when supported on **gfx1152**.
- Document Vulkan fallback (`LOCAL_LLM_DEVICE=vulkan` or llama.cpp build flags) when ROCm is broken/incomplete for the APU.
- vLLM profile assumes ROCm; if unsupported, status `unsupported_device` and docs troubleshooting — do not silently fall back inside the app.

**8. Optional by construction**  
- No `profiles: [local-ai]` on `backend`/`frontend` → default `docker compose up` unchanged.
- Local models appear in registry **only** when `LOCAL_LLM_ENABLED=1` (or equivalent) **and** profile brings up sidecar (or user points `LOCAL_LLM_BASE_URL` at an already-running host server).

## Capability / runtime evaluation (reference)

| Runtime | Fit for this host | Role in plan |
|---------|-------------------|--------------|
| **llama.cpp** | Best for single-user, latency-oriented, UMA APU; GGUF quant; ROCm or Vulkan | Default `local-ai` profile |
| **vLLM + ROCm** | Better for throughput / multi-user / benchmarking; heavier image & VRAM assumptions | Optional `local-ai-vllm` profile |
| **PyTorch + ROCm** | Training / custom adapters — not needed for chat completion path | `training` profile stub + docs only |

Choose per capability later (e.g. future local embedding vs chat); for **language chat** this plan ships one LocalLanguageModel + two Docker sidecars.

## Config surface (env)

Document in `.env.example` (all optional; ignored when local AI disabled):

```text
LOCAL_LLM_ENABLED=0
LOCAL_LLM_BASE_URL=http://local-llm:8080/v1
LOCAL_LLM_MODEL=   # served model name (not a host absolute path in discovery responses)
LOCAL_LLM_CONTEXT_SIZE=4096
LOCAL_LLM_QUANTIZATION=Q4_K_M
LOCAL_LLM_DEVICE=rocm          # rocm | vulkan | cpu
LOCAL_LLM_MEMORY_LIMIT_MB=12288
LOCAL_LLM_MAX_CONCURRENCY=1
LOCAL_LLM_TIMEOUT_SECONDS=180
LOCAL_LLM_API_KEY=local        # placeholder for OpenAI-compatible clients
# Optional: host bind for weights (Compose only; never returned by API)
# LOCAL_LLM_MODELS_HOST_PATH=./models/llm
```

Pass through `docker-compose.yml` `environment:` for backend when using profiles. Sidecar-specific vars (GGUF filename, `--n-gpu-layers`, vLLM `--gpu-memory-utilization`) live under profile service blocks / `compose.local-ai.yml` (or equivalent), not in application Python.

## Docker profile sketch

```text
# Default (unchanged)
docker compose up --build

# Local AI — llama.cpp (default local profile)
docker compose --profile local-ai up --build

# Optional vLLM
docker compose --profile local-ai-vllm up --build

# Training scaffolding (docs/stub; may be no-op service or documented compose file)
docker compose --profile training up --build
```

Suggested layout (implementer may merge into `docker-compose.yml` or split files):
- `local-llm` service: `profiles: [local-ai]`, OpenAI-compatible port e.g. `8080`, volume `./models/llm:/models:ro`, devices for `/dev/kfd` + `/dev/dri` (documented; may differ by host), healthcheck against `/health` or `/v1/models`.
- `local-llm-vllm` service: `profiles: [local-ai-vllm]`, distinct port, ROCm image pin documented.
- Backend gains `LOCAL_*` env always optional; depends_on local sidecar **only** when using profile compose override that sets `depends_on` with `required: false` if Compose version supports it — otherwise document start order; **never** make default backend wait for local-llm.

## Secret / logging policy

- Log: `model_id`, runtime, status, device enum, context size, concurrency, probe latency_ms, `error_type`, memory_limit_mb — **not** weight paths, prompts, API keys, full `/v1/models` dumps.
- Discovery: no absolute paths, no host usernames, no ROCm ioctl dumps.
- Extend `test_secret_hygiene` for local AI payloads.

## Acceptance criteria mapping

| Criterion | Tasks |
|-----------|-------|
| Inspect Docker + host assumptions | Task 1 |
| Optional profiles/services | Tasks 2, 8 |
| AMD/ROCm-compatible architecture; evaluate runtimes | Tasks 1–2, docs |
| LocalLanguageModel in V3 runtime | Tasks 3–5 |
| OpenAI-compatible reuse | Tasks 3–4 |
| Config knobs | Task 4 |
| Health/status reporting | Tasks 5–6 |
| Optional; default startup works | Tasks 2, 8, 11 |
| Profiles default / local-ai / training (+ vLLM) | Task 2 |
| 32 GB memory-safe defaults | Tasks 2, 4, docs |
| Docs Linux/ROCm/passthrough/install/troubleshoot | Task 12 |
| NPU out of critical path | Tasks 1, 12 |
| Select local model in UI + run composition op | Tasks 7, 9–11 |
| Tests, health checks, logging, docs | Tasks 5–6, 10–12 |

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat(ai): add local LLM settings and LocalLanguageModel adapter`
- **Commit 2** (after tasks 4–6): `feat(ai): register local models with health and ready reporting`
- **Commit 3** (after tasks 7–9): `feat(docker): optional local-ai and vLLM Compose profiles`
- **Commit 4** (after tasks 10–11): `test(ai): cover local LLM discovery, routing, and secret hygiene`
- **Commit 5** (after task 12): `docs: add local AI AMD/ROCm inference guide`

## Tasks

### Phase 1: Audit freeze & Docker contracts

- [x] Task 1: Capture host/Docker assumptions and runtime choice matrix
  Deliverable: Short design note section in the plan’s implementer checklist (or `docs/local-ai.md` draft skeleton) confirming: default Compose has no GPU requirement; gfx1152 ROCm preferred for llama.cpp; Vulkan fallback; vLLM separate; NPU non-critical; PyTorch+ROCm only under `training` stub. Verify `backend/Dockerfile` `models/` directory intent vs new `./models/llm` host mount naming.
  LOGGING: N/A (docs/design). If a probe script is added for host detection later, log only device enum and support booleans.
  Files: `.ai-factory/plans/optional-local-ai-inference-amd-rocm.md` (reference), start `docs/local-ai.md` skeleton optional here or in Task 12.

- [x] Task 2: Add optional Compose profiles for llama.cpp, vLLM, and training stub
  Deliverable: Compose changes such that:
  - Default `docker compose up` unchanged (no local AI image pull required).
  - `--profile local-ai` starts OpenAI-compatible **llama.cpp** server with memory-safe defaults, read-only model volume, documented device mounts for ROCm (`/dev/kfd`, `/dev/dri` as applicable), healthcheck, and network alias reachable from backend as `LOCAL_LLM_BASE_URL`.
  - `--profile local-ai-vllm` starts **vLLM + ROCm** sidecar (separate service/port); same OpenAI-compatible URL shape for the app.
  - `--profile training` is a documented stub (commented service or minimal placeholder) pointing at future PyTorch+ROCm work — must not break default up.
  - Backend `environment` passthrough for all `LOCAL_*` vars; `.env.example` documented.
  Prefer ROCm for llama.cpp on gfx1152; document Vulkan fallback flags in comments/docs, not hardcoded app logic.
  LOGGING: Compose healthcheck only; app logs remain in later tasks.
  Files: `docker-compose.yml` and/or `compose.local-ai.yml`, `compose.dev.yml` (optional GPU notes), `.env.example`, maybe `models/llm/.gitkeep` + gitignore for large weights.

### Phase 2: Runtime adapter & configuration

- [x] Task 3: Implement `LocalLanguageModel` on the V3 LanguageModel protocol
  Deliverable: New runtime module constructing a `LanguageModel` that:
  - Uses **only** `build_chat_openai` / `ainvoke_chat_text` against `LOCAL_LLM_BASE_URL`.
  - Does not import llama.cpp or vLLM.
  - Validates `runtime` / locality; maps transport failures to existing domain errors with stable codes where useful (`model_unavailable`, and local-specific detail codes for OOM/unsupported device when detectable from HTTP body/status without logging payloads).
  - Registers builder in the same place remote chat models are constructed (routing / provider_settings_for_resolved path).
  Extend `ModelRuntimeId` Literal and bootstrap wiring.
  LOGGING REQUIREMENTS:
  - INFO on construct: `model_id`, `runtime`, `timeout_seconds`, `has_base_url` (boolean).
  - DEBUG on `complete_text`: purpose, prompt_length, elapsed_ms, response_length.
  - WARN on transport failure: `model_id`, `error_type`, status mapping — never prompt/base_url with embedded credentials.
  - Format: structured `extra={...}`; controllable via `LOG_LEVEL`.
  Files: `backend/app/ai_runtime/runtimes/local_language.py` (or similarly named), `backend/app/ai_runtime/types.py`, `backend/app/ai_runtime/runtimes/__init__.py`, `backend/app/ai_runtime/routing.py` (resolve path), tests scaffold in Task 10.

- [x] Task 4: Add local LLM settings loader and memory-safe defaults
  Deliverable: Dataclass + `load_local_llm_settings(env)` (new module or extension of `llm_settings.py`) covering enabled flag, base_url, model name, context size, quantization, device, memory_limit_mb, max_concurrency, timeout_seconds, placeholder api_key. Validate ranges (concurrency ≥1, context >0, memory limit sane). When disabled, registry skips local chat model. When enabled, contribute `LLMProviderSettings`-compatible or parallel settings so `provider_settings_for_resolved` works for `local:*` ids. Wire `SUPPORTED_PROVIDERS` / bootstrap so `local` is accepted without requiring cloud keys.
  Defaults must match the 32 GB table above.
  LOGGING: DEBUG key presence booleans only; INFO when local LLM enabled with model_id/device/context/concurrency/memory_limit (no paths); WARN on invalid env values with fallback to safe defaults.
  Files: `backend/app/llm_settings.py` and/or `backend/app/local_llm_settings.py`, `.env.example`, `docker-compose.yml` env passthrough.

- [x] Task 5: Registry bootstrap + health probe for local models
  Deliverable: When local LLM enabled, register `local:<model>` with `locality=local`, creative chat `supported_operations`, limits dict (public: context_size, max_concurrency — not host paths). Implement health probe helper that sets status/`health.detail` to the user-facing states (`loading`, `loaded`/`ready`, `unavailable`, `out_of_memory`, `unsupported_device`). Probe must be timeout-bounded and must not download weights. Refresh on `reload_registry`; document that `/ready` does not block on cold load beyond a short probe.
  LOGGING: INFO probe result status + latency_ms; WARN on OOM/unsupported_device with `error_type`; never log weight paths.
  Files: `backend/app/ai_runtime/bootstrap.py`, new `backend/app/ai_runtime/local_health.py` (or similar), `backend/app/ai_runtime/types.py` / schemas if status union expands, `backend/app/ai_runtime_schemas.py` if needed.

- [x] Task 6: Ready/health reporting for local AI (soft dependency)
  Deliverable: Extend `build_readiness_report()` with a `local_ai` object: `{enabled, reachable, status, model_id, device, runtime_profile_hint}` — non-secret. Overall `/ready` remains successful when local AI is disabled or unreachable (soft). `/health` stays liveness-only. Update discovery warnings to mention `--profile local-ai` when appropriate. Ensure Compose service healthchecks cover sidecars independently.
  LOGGING: INFO readiness local_ai summary fields; DEBUG skip reasons.
  Files: `backend/app/ready.py`, `backend/app/main.py` (if needed), `backend/tests/test_health_and_cors.py` (extend in Task 10), Compose healthcheck blocks from Task 2.

### Phase 3: API + frontend selection

- [x] Task 7: Surface local models through `/ai/models` and `/llm/models`
  Deliverable: Local ready/loaded models appear in both discovery endpoints used by the SPA. Compat `/llm/models` must include `provider=local` entries so `MusicGenerator` select works without requiring a full `/ai/models` migration. Filter policy: only list selectable local models when status is usable (`ready`/`loaded`); still allow `/ai/models` to show non-ready local entries with status for operators if useful (document). Update empty-state copy in UI lightly to mention optional local profile (Task 9).
  LOGGING: existing discovery logs + local model_id/status counts.
  Files: `backend/app/main.py` (`/llm/models`), `backend/app/routers/ai_models.py`, bootstrap/registry.

- [x] Task 8: Ensure default Compose path never requires local AI
  Deliverable: Automated or scripted verification notes + regression test that with `LOCAL_LLM_ENABLED` unset/0, registry has no dependency on sidecar; backend tests pass without profile. Document operator commands for profile bring-up. Confirm `depends_on` does not hard-fail default stack.
  LOGGING: N/A beyond existing startup.
  Files: Compose files, `docs/local-ai.md`, maybe `scripts/` note in docs only (avoid new mandatory scripts unless useful).

- [x] Task 9: Frontend selector UX for local models
  Deliverable: Existing `data-testid="llm-model-select"` lists local models when API returns them. Optional: show locality or status in `display_name` (e.g. `Local (llama-q4)`) from backend display_name — prefer backend-provided label over frontend heuristics. Setup empty-state text mentions fake mode, cloud keys, **or** local-ai profile. No new global architecture; keep `selectedProvider`/`selectedModel`. Provenance already carries provider/model/runtime once backend returns them — verify generate path sends `local` + model string / `model_id` if already supported.
  LOGGING: existing `console.debug` generation request — ensure no secrets; optional locality field.
  Files: `frontend/src/components/MusicGenerator.jsx`, possibly `WorkspaceChrome.jsx` status string, `frontend/src/api/musicApi.js` / store if `/llm/models` shape gains fields (keep backward compatible).

### Phase 4: Tests & hardening

- [x] Task 10: Backend tests for local settings, adapter, health, routing, secrets
  Deliverable: Pytest coverage including:
  - Settings parse + defaults + disabled path.
  - `LocalLanguageModel` complete_text with mocked `build_chat_openai` / HTTP.
  - Registry lists `local:*` when enabled; absent when disabled.
  - Health probe maps to status details (mock responses for loading/ready/OOM/unsupported).
  - `/ai/models` + `/llm/models` include local; `/ready` soft-local section.
  - `test_secret_hygiene` — no weight paths/keys in discovery/ready.
  - Routing: resolve `local:…` for a creative `AiOperation` with fake sidecar settings.
  Do **not** require real GPU in CI.
  LOGGING: tests may assert log extras via caplog where valuable (status transitions).
  Files: `backend/tests/test_local_llm_settings.py`, `test_local_language_model.py`, `test_local_llm_health.py`, extend `test_ai_models_routes.py`, `test_ai_runtime_routing.py`, `test_secret_hygiene.py`, `test_health_and_cors.py`.

- [x] Task 11: Integration / acceptance path for “select local + generate”
  Deliverable: With `LLM_FAKE_MODE` pattern as inspiration, add a **deterministic local OpenAI-compatible mock** (httpx/ASGI mock or tiny stub server in tests) proving generate (or region edit) routing uses LocalLanguageModel when `local:*` selected. Optional Playwright note in docs for manual GPU acceptance on the ThinkBook (not mandatory flaky GPU E2E in CI). Confirm remote providers still work when local disabled.
  LOGGING: assert orchestrator resolution logs include `model_id` / `runtime` without prompts.
  Files: backend route/orchestrator tests; docs manual checklist in `docs/local-ai.md`.

### Phase 5: Documentation

- [x] Task 12: Write local AI docs and wire README / ai-runtime / roadmap / AGENTS
  Deliverable: New `docs/local-ai.md` covering:
  - Linux host prerequisites (kernel, ROCm user groups, gfx1152 notes).
  - Device passthrough for Docker (`/dev/kfd`, `/dev/dri`, security opts if any).
  - Model installation (download GGUF into `models/llm`, naming, `.gitignore`).
  - Profile commands: default vs `local-ai` vs `local-ai-vllm` vs `training`.
  - ROCm vs Vulkan fallback for llama.cpp.
  - Memory-safe defaults and how to raise them safely.
  - Troubleshooting: OOM, unsupported device, sidecar not reachable, slow first token, conflict with browser+IDE memory.
  - Explicit “NPU not required / not on critical path”.
  - App architecture reminder: OpenAI-compatible only.
  Update: `README.md` (short link), `docs/ai-runtime.md` (local runtime + status), `.ai-factory/ROADMAP.md` (new unchecked milestone), `AGENTS.md` / DESCRIPTION if structure changes significantly, `.env.example` comments.
  LOGGING: document which fields appear in logs vs `/ready`.
  Files: `docs/local-ai.md`, `docs/ai-runtime.md`, `README.md`, `.ai-factory/ROADMAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md` (brief), `.env.example`.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| ROCm incomplete on gfx1152 consumer APU | Default docs emphasize probing; Vulkan fallback for llama.cpp; status `unsupported_device` |
| Accidental huge default model | Pin small GGUF name in Compose/docs; refuse to document 70B as default |
| Default compose pulls 10GB+ images | Profiles only; pin optional images behind profile |
| `/ready` blocks on cold model load | Short probe timeout; soft local_ai section; never load weights in API process |
| Frontend breaks on new provider | Keep `/llm/models` shape; use `local` as provider string; extend Literal unions if any remain |
| Secret leakage of model paths | Hygiene tests; basename-only logging; discovery limits dict public fields only |
| Coupling to one engine | Single LocalLanguageModel + BASE_URL; two Compose sidecars |
| NPU scope creep | Docs-only mention; no dependency |

## Manual acceptance (ThinkBook)

1. `docker compose up --build` — app works with fake or cloud keys; no local profile.
2. Place a small instruct GGUF under `models/llm/`; set `.env` `LOCAL_LLM_ENABLED=1` and model name.
3. `docker compose --profile local-ai up --build` — sidecar healthy; `GET /ai/models` shows `local:…` ready/loaded.
4. In UI model select, choose local model; run Generate (or region edit) successfully.
5. Stop sidecar — local status unavailable; remote/fake still usable; app `/health` ok.
6. Optional: `--profile local-ai-vllm` smoke for `/v1/models` reachability (GPU permitting).

## Out-of-scope follow-ups
- Ryzen AI NPU Execution Provider integration
- Full PyTorch training loop / music-symbolic local models
- Multi-model local catalog UI beyond single configured `LOCAL_LLM_MODEL`
- Automatic engine selection (llama.cpp vs vLLM) inside the backend

## Implementer Checklist (Task 1 freeze)

Confirmed 2026-09-21 against current tree (`docker-compose.yml`, `backend/Dockerfile`, AI runtime V3):

| Assumption | Decision |
|------------|----------|
| Default Compose GPU requirement | **None** — `backend` + `frontend` only; no `/dev/kfd` / `/dev/dri`; no multi-GB inference image pull |
| Preferred chat runtime on gfx1152 | **llama.cpp** OpenAI-compatible server (`--profile local-ai`) |
| ROCm vs Vulkan | Prefer **ROCm** for llama.cpp when host supports gfx1152; document **Vulkan** fallback via `LOCAL_LLM_DEVICE=vulkan` / server flags — app never branches on engine |
| Throughput / benchmark path | **vLLM + ROCm** under `--profile local-ai-vllm` only |
| Training / PyTorch+ROCm | **`--profile training` stub + docs only** — no training loop |
| NPU (Ryzen AI) | **Non-critical** — docs mention only; never required for ready/local chat |
| Backend `models/` vs host weights | Dockerfile `mkdir models/` is legacy empty app dir; local weights live on host at **`./models/llm`** bind-mounted read-only into the sidecar as `/models` — never into FastAPI weight-loading |
| App coupling | FastAPI talks **only** OpenAI-compatible HTTP (`LOCAL_LLM_BASE_URL`); no llama.cpp/vLLM Python imports |
| Status mapping (Task 3+) | Catalog `status=ready` when usable; `health.detail` ∈ `loaded` \| `loading` \| `out_of_memory` \| `unsupported_device`; non-usable → `unavailable` / local status codes as implemented |
| Memory-safe defaults | ~7–8B Q4_K_M class, context 4096, concurrency 1, memory budget ~12288 MiB, timeout ≥180s |

Docs skeleton: `docs/local-ai.md` (filled in Task 12).
)