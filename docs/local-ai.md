# Optional Local AI Inference (AMD / ROCm)

Run supported language models **optionally** on a development machine via Docker Compose profiles. The FastAPI app talks to an OpenAI-compatible HTTP sidecar only — it never loads GGUF/safetensors weights and does not import llama.cpp or vLLM.

Target reference host: Lenovo ThinkBook 14 G7+ AKP (AMD Ryzen AI APU, Radeon iGPU **gfx1152**, 32 GB unified memory). Defaults are memory-safe for that class of laptop.

## Architecture

```text
Frontend → FastAPI (ai_runtime) → LocalLanguageModel → HTTP OpenAI chat API
                                      ↑
                         llama.cpp  OR  vLLM  (Compose sidecar only)
```

| Layer | Responsibility |
|-------|----------------|
| App (`backend/app`) | Registry, routing, `LocalLanguageModel`, soft `/ready` `local_ai` block |
| Sidecar | Load weights, ROCm/Vulkan/CPU inference, expose `/v1/chat/completions` |
| Frontend | Existing provider/model select; backend supplies `Local (…)` display names |

**NPU (Ryzen AI Execution Provider) is not required** and is not on the critical path.

## Runtime choice

| Profile | Engine | When to use |
|---------|--------|-------------|
| *(default — no profile)* | — | Normal app; fake mode or cloud keys |
| `local-ai` | **llama.cpp** OpenAI server | Default local chat (single-user, UMA-friendly GGUF) |
| `local-ai-vllm` | **vLLM + ROCm** | Throughput / multi-request experiments |
| `training` | Stub only | Future PyTorch+ROCm scaffolding — **no training loop** |

Application code does **not** branch on llama.cpp vs vLLM. Profiles differ only in sidecar image, command, port, and `LOCAL_LLM_BASE_URL`.

## Prerequisites (Linux host)

1. Docker Engine + Compose v2 with optional GPU/device access.
2. For ROCm: vendor-supported stack for your kernel; user in `video` / `render` groups as required by your distro.
3. **gfx1152** ROCm support varies by stack version — if ROCm fails, use the Vulkan fallback (below) for llama.cpp. vLLM profile assumes ROCm; if unsupported, Mukit reports `unsupported_device` and does not silently fall back inside the app.
4. Enough free RAM for OS + browser + Mukit + model (defaults leave ~12–16 GB headroom on a 32 GB machine).

## Device passthrough

`compose.local-ai.yml` mounts `/dev/kfd` and `/dev/dri` on sidecar services only (not on the FastAPI backend). Adjust `group_add` / `security_opt` for your host if the ROCm image requires it. CPU-only hosts can still run llama.cpp with `-ngl 0` (set `LOCAL_LLM_N_GPU_LAYERS=0`).

## Model installation

1. Create weights directory (tracked `.gitkeep` only):

   ```bash
   mkdir -p models/llm
   ```

2. Download a **small** instruct GGUF (~7–8B, **Q4_K_M** recommended). Do **not** use 70B as a default on 32 GB UMA.
3. Name the file to match Compose (`LOCAL_LLM_GGUF_FILE`, default `model.gguf`) or set the env var.
4. Large weights are gitignored (`*.gguf`, `models/llm/*`).

Discovery APIs never return absolute weight paths or host usernames.

## Configuration

See `.env.example` (`LOCAL_*`). Important knobs:

| Variable | Default | Notes |
|----------|---------|-------|
| `LOCAL_LLM_ENABLED` | `0` | Must be `1` to register `local:<model>` |
| `LOCAL_LLM_BASE_URL` | `http://local-llm:8080/v1` | vLLM profile: `http://local-llm-vllm:8000/v1` |
| `LOCAL_LLM_MODEL` | _(empty)_ | Served model name (not a host path) |
| `LOCAL_LLM_CONTEXT_SIZE` | `4096` | |
| `LOCAL_LLM_QUANTIZATION` | `Q4_K_M` | Documented hint; sidecar owns real quant |
| `LOCAL_LLM_DEVICE` | `rocm` | `rocm` \| `vulkan` \| `cpu` |
| `LOCAL_LLM_MEMORY_LIMIT_MB` | `12288` | Budget hint for operators |
| `LOCAL_LLM_MAX_CONCURRENCY` | `1` | |
| `LOCAL_LLM_TIMEOUT_SECONDS` | `180` | Align with `LLM_REQUEST_TIMEOUT_SECONDS` |
| `LOCAL_LLM_API_KEY` | `local` | Placeholder for OpenAI-compatible clients |

## Profile commands

```bash
# Default stack — unchanged; no local AI image pull
docker compose up --build

# llama.cpp (default local)
docker compose -f docker-compose.yml -f compose.local-ai.yml --profile local-ai up --build

# Optional vLLM
docker compose -f docker-compose.yml -f compose.local-ai.yml --profile local-ai-vllm up --build

# Training stub (no-op placeholder)
docker compose -f docker-compose.yml -f compose.local-ai.yml --profile training up --build
```

With hot-reload:

```bash
docker compose -f docker-compose.yml -f compose.dev.yml -f compose.local-ai.yml \
  --profile local-ai up --build
```

**Start order:** bring the sidecar up (or point `LOCAL_LLM_BASE_URL` at an already-running host server) before expecting `local:*` to be `ready`. Default backend never hard-depends on local-llm.

## ROCm vs Vulkan (llama.cpp)

1. Prefer **ROCm** when gfx1152 works with your image (`LOCAL_LLM_DEVICE=rocm`).
2. If ROCm is incomplete: set `LOCAL_LLM_DEVICE=vulkan` and use a Vulkan-capable llama.cpp server image/build (override `LOCAL_LLM_LLAMACPP_IMAGE` if needed). App logic stays the same.
3. Last resort: `LOCAL_LLM_DEVICE=cpu` and `LOCAL_LLM_N_GPU_LAYERS=0`.

## Status vocabulary

| User-facing state | Catalog `status` | `health_detail` |
|-------------------|------------------|-----------------|
| Usable | `ready` | `loaded` |
| Loading | `loading` | `loading` |
| Sidecar down / probe fail | `unavailable` | `unavailable` |
| OOM | `out_of_memory` | `out_of_memory` |
| Bad device backend | `unsupported_device` | `unsupported_device` |

`/llm/models` (SPA select) lists local models only when `status=ready`. `/ai/models` may show non-ready local entries for operators. `/ready` includes a soft `local_ai` object; overall readiness does **not** fail when local AI is off or down.

## Logging vs `/ready`

| Surface | Fields |
|---------|--------|
| Logs | `model_id`, runtime, status, device, context/concurrency/memory_limit, probe `latency_ms`, `error_type` — **not** weight paths, prompts, API keys, full `/v1/models` dumps |
| `/ready` → `local_ai` | `enabled`, `reachable`, `status`, `model_id`, `device`, `runtime_profile_hint`, optional `health_detail` |

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| Local missing from UI select | `LOCAL_LLM_ENABLED=1`, model name set, sidecar healthy, probe → `ready` |
| `unsupported_device` | Check ROCm/gfx1152; switch llama.cpp to Vulkan/CPU; vLLM may be unsupported on this APU |
| OOM / slow first token | Smaller GGUF, lower context, concurrency 1, close browser tabs/IDE |
| Sidecar not reachable | Confirm Compose profile, hostname (`local-llm`), port, firewall |
| Default `compose up` pulls huge images | You included `compose.local-ai.yml` + a profile — omit for default stack |
| Conflict with browser + IDE memory | Keep memory budget ≤12–16 GB for the sidecar on 32 GB hosts |

## Manual acceptance (ThinkBook)

1. `docker compose up --build` — app works with fake or cloud keys; no local profile.
2. Place a small instruct GGUF under `models/llm/`; set `.env` `LOCAL_LLM_ENABLED=1` and `LOCAL_LLM_MODEL`.
3. `docker compose -f docker-compose.yml -f compose.local-ai.yml --profile local-ai up --build` — sidecar healthy; `GET /ai/models` shows `local:…` ready/loaded.
4. In UI model select, choose local model; run Generate (or region edit) successfully.
5. Stop sidecar — local status unavailable; remote/fake still usable; app `/health` ok.
6. Optional: `--profile local-ai-vllm` smoke for `/v1/models` (GPU permitting).

## See also

- [AI Runtime](./ai-runtime.md) — registry, routing, provenance
- [Testing](./testing.md) — backend unit tests (no GPU required in CI)
- `.env.example` — `LOCAL_*` knobs
- `compose.local-ai.yml` — profile service definitions
