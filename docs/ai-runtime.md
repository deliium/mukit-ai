# AI Runtime & Model-Provider Architecture

Unified backend surface for discovering, configuring, selecting, and executing AI
models by **capability**. Composition/editor business logic must not couple to a
single provider or chat framework. FluidSynth WAV export stays outside this
runtime.

**V4 multi-agent layer:** Specialized music agents live in `backend/app/ai_agents/`
**above** this runtime. Agents bind to models via `resolve_model_for_operation` /
`AI_AGENT_<ID>_MODEL`, exchange typed artifacts, and never replace the model
registry. See [Multi-agent architecture](./multi-agent.md).

**Operation spans:** chat and local HTTP completions that run inside an
autonomous run open a model span (`model_id`, `runtime`, reported token
counts, `usage_status`). `SystemExit` from that invoke or from an agent `run`
is stored as `failure_code=operation_model_crashed` and does not exit the API
process. `KeyboardInterrupt` still propagates. See
[observability.md](observability.md).

## Capabilities

Each registered model has:

- **`primary_capability`** — one category for discovery grouping
- **`supported_operations[]`** — concrete app operations the model may serve
- Optional **`secondary_capabilities`** — only when a future non-chat model truly spans categories

Env-bootstrapped OpenAI / DeepSeek / Fake chat models use
`primary_capability=language_planner` with a multi-op `supported_operations` list
covering generate, region edit, arrange/development preview, reharmonize AI, and
creative motif — not multiple primary capabilities.

| Capability | Role |
|------------|------|
| `language_planner` | Form/harmony/theme planning, instruction following, JSON repair |
| `symbolic_composer` | Full/partial symbolic composition generation |
| `symbolic_editor` | Region edit, arrangement/development drafts, reharmonize AI, creative motifs |
| `embedding` | Symbolic musical similarity / retrieval (`local:symbolic-features-v1` ready; text stub remains unconfigured) |
| `audio_transcription` | Local monophonic audio → `transcription.preview.v1` (HTTP primary; discovery may list `local:audio-mono-*`) |
| `audio_generation` | Neural audio render (`fake:neural-audio` when fake mode; optional MusicGen sidecar / MIDI-DDSP; stub when unconfigured). Distinct from FluidSynth WAV. |

## Operations (`AiOperation`)

| Operation | Env key | Default capability |
|-----------|---------|-------------------|
| `generate` | `AI_OP_GENERATE` | `language_planner` |
| `generate_planner` | `AI_OP_GENERATE_PLANNER` | `language_planner` (hybrid: uncollapsed via `collapse_reserved_generate=False`; discovery falls back to `AI_OP_GENERATE` when unset) |
| `generate_composer` | `AI_OP_GENERATE_COMPOSER` | `symbolic_composer` (hybrid: uncollapsed; discovery defaults to first ready symbolic composer when unset) |
| `region_edit` | `AI_OP_REGION_EDIT` | `symbolic_editor` |
| `arrange_preview` | `AI_OP_ARRANGE_PREVIEW` | `symbolic_editor` |
| `development_preview` | `AI_OP_DEVELOPMENT_PREVIEW` | `symbolic_editor` |
| `reharmonize_ai` | `AI_OP_REHARMONIZE_AI` | `symbolic_editor` |
| `creative_motif` | `AI_OP_CREATIVE_MOTIF` | `symbolic_editor` |
| `transcribe` | `AI_OP_TRANSCRIBE` | `audio_transcription` (local mono engines or stub; not LLM generate) |
| `embed` | `AI_OP_EMBED` | `embedding` (default: `local:symbolic-features-v1`) |
| `audio_render` | `AI_OP_AUDIO_RENDER` | `audio_generation` (`fake:neural-audio`, `sidecar:musicgen`, optional `local:midi-ddsp`; stub → 503) |

Fallback: `AI_FALLBACK_<OPERATION>=id1,id2` (comma-separated model ids). Never silent.

Canonical **`model_id`**: `provider:model` (e.g. `openai:gpt-4o-mini`, `fake:fake-deterministic`).

## Registry fields

| Field | Notes |
|-------|--------|
| `id` | Canonical `provider:model` |
| `display_name` | UI label |
| `provider` | Vendor/org id |
| `runtime` | `openai_compatible_chat` \| `fake` \| `stub` \| `local_openai_compatible` \| `symbolic_features` \| `fake_neural_audio` \| `sidecar_musicgen` \| `local_midi_ddsp` |
| `primary_capability` | Discovery group |
| `locality` | `local` \| `remote` |
| `model_version` | Optional version string |
| `supported_operations` | Operation allow-list |
| `status` / `health` | Non-secret readiness (`ready` when usable; local probes may set `loading` / `out_of_memory` / `unsupported_device`; `health_detail` carries `loaded` etc.) |
| `limits` | Optional public limits (e.g. local `context_size`, `max_concurrency` — never host weight paths) |

Lifecycle: `reload_registry(env=...)` so tests and config reload are not stuck on a
stale process-global registry. Optional `AI_MODEL_REGISTRY_PATH` adds entries;
default = derived from current LLM env.

## Routing

Resolve order for `(AiOperation, request_selection)`:

1. Explicit `selection.model_id`
2. Legacy `provider` + `model`
3. `AI_OP_<OPERATION>`
4. Global default provider/model

Responses that return provider/model also surface `fallback_applied`,
`requested_model_id`, `resolved_model_id` when applicable.

## Discovery

- Canonical: `GET /ai/models` (+ optional `GET /ai/models/{id}`)
- Compat shim: `GET /llm/models` (language-capable models)
- Docker: nginx must proxy `location /ai/` to the backend (same origin as `/llm/`)

Never return API keys, base URL secrets, absolute weight paths, or host usernames.

### Optional local OpenAI-compatible models

When `LOCAL_LLM_ENABLED=1` and a sidecar is reachable, the registry adds
`local:<model>` with `runtime=local_openai_compatible` and `locality=local`.
Transport reuses the shared ChatOpenAI factory against `LOCAL_LLM_BASE_URL`
(`LocalLanguageModel`). Default Compose does **not** start a sidecar — see
[Optional local AI (AMD/ROCm)](./local-ai.md). `/ready` exposes a soft `local_ai`
subsection that never fails overall readiness when local AI is off or down.

### Plugins (`runtime=plugin`)

Directories on `PLUGIN_PATHS` are discovered only. An explicit install and enable can register a model without editing `bootstrap.py`.
`reload_plugins()` parses manifests and does not import plugin code. `GET /ai/models` calls `reload_registry()`,
which only reattaches descriptors for plugins whose lifecycle is `enabled`. Model ids look like
`plugin:sample_symbolic_generator`. Plugin rows are excluded from the implicit
`generate_composer` default. See [Plugin and extension SDK](plugin-sdk.md).

## Provenance

Additive fields on `AiProvenance` / `ProjectGenerationMeta` and revision
`summary_json` (prefer no Alembic): `model_id`, `model_version`, `provider`,
`runtime`, `capability`, `operation`, `generation_parameters` (bounded
`generation.provenance.v1` fragment: pipeline_id, stages, seed, compact
generation_config — no prompts/keys/absolute paths). Keep legacy
`provider` / `model` populated for UI compat. See [hybrid-generation.md](hybrid-generation.md)
and [daw-interoperability.md](daw-interoperability.md).

## Secret policy

- Logs: ids, counts, statuses, error types only — never keys, prompts, weights paths
- Discovery and `/ready`: credentials-present booleans only; never load local weights
- Persistence: reuse `persistence_secret_guard` for generation parameters

## Migration from `/llm/models`

Old clients may keep calling `/llm/models` and sending `provider`+`model`.
New clients prefer `/ai/models` and `model_id`. Both map through the same registry.

## Package layout

```text
backend/app/ai_runtime/
  capabilities.py   # ModelCapability + op→capability defaults
  operations.py     # AiOperation catalog + env key helpers
  types.py          # ModelDescriptor, ResolvedModel, …
  protocols.py      # LanguageModel, SymbolicMusicModel, Embedding*, Audio*
  errors.py         # stable domain error codes
  registry.py       # in-memory registry + reload
  bootstrap.py      # env → registry
  routing.py        # operation resolution + fallback
  runtimes/         # openai_compatible_chat, fake, stub, local_openai_compatible, symbolic_features
  local_health.py   # Bounded sidecar probe (no weight download)
```

See also: `.ai-factory/ARCHITECTURE.md` (Composition/LLM module), `.env.example`.

## Migration notes

| Before | After |
|--------|--------|
| Select via `provider` + `model` only | Prefer `model_id` (`provider:model`); legacy fields still accepted |
| `GET /llm/models` only | Canonical `GET /ai/models`; `/llm/models` remains a language-capable shim |
| Silent provider/model override | Registry-validated selection; unavailable models fail closed unless `AI_FALLBACK_*` is set |
| Provenance = provider/model | Additive `model_id`, `runtime`, `capability`, `operation`, `generation_parameters` via DTO + revision `summary_json` (no Alembic) |
| No `/ai/` nginx proxy | Compose frontend nginx must proxy `/ai/` (same as `/llm/`); pass `AI_OP_*` / `AI_FALLBACK_*` / `AI_MODEL_REGISTRY_PATH` |

FluidSynth WAV export remains outside `ai_runtime`. For hybrid generation (`options.pipeline=hybrid_plan_symbolic`), planner/composer ops are **not** collapsed to `generate` — see [hybrid-generation.md](hybrid-generation.md). Ready symbolic composers: `fake:symbolic-tiny` (always) and `local:music-transformer` (checkpoint + opt-in env).
