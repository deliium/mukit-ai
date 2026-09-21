# AI Runtime & Model-Provider Architecture

Unified backend surface for discovering, configuring, selecting, and executing AI
models by **capability**. Composition/editor business logic must not couple to a
single provider or chat framework. FluidSynth WAV export stays outside this
runtime.

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
| `embedding` | Similarity / retrieval (stub) |
| `audio_transcription` | Audio → text/MIDI-ish (stub) |
| `audio_generation` | Neural audio render (stub; distinct from FluidSynth) |

## Operations (`AiOperation`)

| Operation | Env key | Default capability |
|-----------|---------|-------------------|
| `generate` | `AI_OP_GENERATE` | `language_planner` |
| `generate_planner` | `AI_OP_GENERATE_PLANNER` | reserved; defaults to `AI_OP_GENERATE` |
| `generate_composer` | `AI_OP_GENERATE_COMPOSER` | reserved; defaults to `AI_OP_GENERATE` |
| `region_edit` | `AI_OP_REGION_EDIT` | `symbolic_editor` |
| `arrange_preview` | `AI_OP_ARRANGE_PREVIEW` | `symbolic_editor` |
| `development_preview` | `AI_OP_DEVELOPMENT_PREVIEW` | `symbolic_editor` |
| `reharmonize_ai` | `AI_OP_REHARMONIZE_AI` | `symbolic_editor` |
| `creative_motif` | `AI_OP_CREATIVE_MOTIF` | `symbolic_editor` |
| `transcribe` | `AI_OP_TRANSCRIBE` | `audio_transcription` (stub) |
| `embed` | `AI_OP_EMBED` | `embedding` (stub) |
| `audio_render` | `AI_OP_AUDIO_RENDER` | `audio_generation` (stub) |

Fallback: `AI_FALLBACK_<OPERATION>=id1,id2` (comma-separated model ids). Never silent.

Canonical **`model_id`**: `provider:model` (e.g. `openai:gpt-4o-mini`, `fake:fake-deterministic`).

## Registry fields

| Field | Notes |
|-------|--------|
| `id` | Canonical `provider:model` |
| `display_name` | UI label |
| `provider` | Vendor/org id |
| `runtime` | `openai_compatible_chat` \| `fake` \| `stub` |
| `primary_capability` | Discovery group |
| `locality` | `local` \| `remote` |
| `model_version` | Optional version string |
| `supported_operations` | Operation allow-list |
| `status` / `health` | Non-secret readiness |
| `limits` | Optional public limits |

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

## Provenance

Additive fields on `AiProvenance` / `ProjectGenerationMeta` and revision
`summary_json` (prefer no Alembic): `model_id`, `model_version`, `provider`,
`runtime`, `capability`, `operation`, `generation_parameters` (bounded:
temperature, timeout, candidate_count — no prompts/keys). Keep legacy
`provider` / `model` populated for UI compat.

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
  runtimes/         # openai_compatible_chat, fake, stub adapters
```

See also: `.ai-factory/ARCHITECTURE.md` (Composition/LLM module), `.env.example`.
