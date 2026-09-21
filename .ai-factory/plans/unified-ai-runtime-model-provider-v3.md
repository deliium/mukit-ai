# Implementation Plan: Unified AI Runtime & Model-Provider Architecture (V3)

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21
Improved: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "Unified AI runtime and model-provider architecture (V3)"
Rationale: All prior roadmap milestones are complete; this is the foundation milestone for local/symbolic/audio/embedding models without regressing V2 remote LLM workflows.

## Goal

Introduce one extensible backend architecture for discovering, configuring, selecting, and executing AI models by **capability**, so composition/editor business logic does not couple to a single provider or framework. V1/V2 remote LLM generate/edit/arrangement/development/reharmonize/motif flows must keep working through adapters. Concrete local Music Transformer / ACE-Step / Whisper implementations are **stubs + contracts** in this plan unless already present — the acceptance bar is routing and abstraction, not full training/inference stacks.

## Audit Summary (current state)

### What exists today
| Layer | Today |
|-------|--------|
| Config | `backend/app/llm_settings.py` — env → `LLMProviderSettings` (provider + one model + api_key + base_url) |
| Transport | `llm_chat_client.build_chat_openai` + `ainvoke_chat_text` (LangChain `ChatOpenAI`) |
| Orchestration | `llm_music_generator`, `llm_composition_editor`, `llm_composition_arrangement`, `llm_composition_development`, `llm_reharmonizer`, `llm_motif_editor` — each calls `select_llm_provider` / fake branch |
| Discovery | `GET /llm/models` → `{provider, model, display_name, is_default}` only |
| Frontend | Single global `selectedProvider` / `selectedModel`; all creative ops share it |
| Provenance | Project `generation_provider`/`generation_model`; revision `AiProvenance` with `Literal["openai","deepseek","fake"]` provider + model; extended fields can live in revision `summary_json` |
| Fake | `LLM_FAKE_MODE` + `fake_llm.py` deterministic fixtures |
| Docker proxy | `frontend/nginx.conf` proxies `/llm/` but **not** `/ai/` today |

### Coupling problems to fix
1. **Provider ≡ model slot** — one env model string per provider; no multi-model-per-provider registry.
2. **Hardcoded Literals** — `schemas.py`, arrangement/development/motif/project/history DTOs reject any new id without schema edits.
3. **Chat-only runtime** — all AI paths assume OpenAI-compatible chat completions.
4. **No capability taxonomy** — generate/edit/arrange/develop all share one selection.
5. **No health/status** beyond “key present → listed”.
6. **Provenance incomplete** — missing runtime, capability, model version, operation, generation params; Apply path only persists `provider`/`model`.
7. **Silent model override risk** — `select_llm_provider` accepts any model string for a known provider without validating registration.

### Non-goals (this plan)
- Implementing Music Transformer training, ACE-Step inference, Whisper, or embedding indexes end-to-end.
- Changing `composition.v2` playable contract or editor math.
- Exposing API keys, local weights paths, or host filesystem to the frontend.
- Silently auto-swapping models when primary fails (fallback only when **explicitly** configured and disclosed).
- Per-stage LangGraph planner-vs-composer model split inside generate (env keys may be reserved; resolve **one** model per HTTP generate call in this plan).

## Architecture Decisions

### Separation of concerns
```text
Capability  →  what kind of work (language_planner, symbolic_composer, …)
Operation   →  app intent (generate, region_edit, arrange_preview, …) maps to capability + optional preferred model
Model       →  concrete registered artifact (id, version, limits, supported_operations)
Provider    →  vendor/org that supplies credentials/catalog (openai, deepseek, local, fake, …)
Runtime     →  execution backend (openai_compatible_chat, fake, stub, …)
```
One provider may register many models; one runtime may host models from multiple providers.

### Model capability shape (resolved contradiction)
Each registered model has:
- **`primary_capability`** — one primary category for discovery grouping
- **`supported_operations[]`** — concrete app operations the model may serve
- Optional **`secondary_capabilities`** — only when a future non-chat model truly spans categories

Env-bootstrapped OpenAI/DeepSeek/Fake chat models: `primary_capability=language_planner`, with `supported_operations` covering generate, region_edit, arrange_preview, development_preview, reharmonize_ai, creative_motif (so one chat model can still back today’s V2 workflows without claiming multiple primary capabilities).

### Capability categories (enum)
- `language_planner` — form/harmony/theme planning, instruction following, repair JSON
- `symbolic_composer` — full/partial symbolic composition generation
- `symbolic_editor` — region edit, arrangement/development drafts, reharmonize AI, creative motifs
- `embedding` — similarity / retrieval (stub)
- `audio_transcription` — audio → text/MIDI-ish (stub)
- `audio_generation` — neural audio render (stub; distinct from FluidSynth WAV)

### Stable `AiOperation` catalog (env + routing)
| Operation id | Env key | Default capability mapping |
|--------------|---------|----------------------------|
| `generate` | `AI_OP_GENERATE` | language_planner (compose stages use same resolved model this plan) |
| `generate_planner` | `AI_OP_GENERATE_PLANNER` | reserved; unused in LangGraph this plan — defaults to `AI_OP_GENERATE` |
| `generate_composer` | `AI_OP_GENERATE_COMPOSER` | reserved; unused in LangGraph this plan — defaults to `AI_OP_GENERATE` |
| `region_edit` | `AI_OP_REGION_EDIT` | symbolic_editor |
| `arrange_preview` | `AI_OP_ARRANGE_PREVIEW` | symbolic_editor |
| `development_preview` | `AI_OP_DEVELOPMENT_PREVIEW` | symbolic_editor |
| `reharmonize_ai` | `AI_OP_REHARMONIZE_AI` | symbolic_editor |
| `creative_motif` | `AI_OP_CREATIVE_MOTIF` | symbolic_editor |
| `transcribe` | `AI_OP_TRANSCRIBE` | audio_transcription (stub) |
| `embed` | `AI_OP_EMBED` | embedding (stub) |
| `audio_render` | `AI_OP_AUDIO_RENDER` | audio_generation (stub) |

Fallback: `AI_FALLBACK_<OPERATION>=id1,id2` (comma-separated model ids).

Canonical **`model_id`** format: `provider:model` (e.g. `openai:gpt-4o-mini`, `fake:fake-deterministic`) matching today’s env pairing.

### Typed interfaces (Protocols / ABCs)
Avoid one mega-`invoke(any)`. Define separate protocols under `backend/app/ai_runtime/`:
- `LanguageModel` — `complete_text` / structured JSON helpers used by LangGraph stages
- `SymbolicMusicModel` — `compose` / `edit` / `draft_arrangement` / `draft_development` as needed (adapters may delegate to LanguageModel for V2 chat path)
- `EmbeddingModel` — `embed(texts) -> vectors`
- `AudioTranscriptionModel` — `transcribe(audio_ref) -> result`
- `AudioGenerationModel` — `render(composition_or_spec) -> audio_ref`

Existing LangChain chat path implements `LanguageModel` **only** via `llm_chat_client.build_chat_openai` + `ainvoke_chat_text` (never construct `ChatOpenAI` inline — preserves timeout / `max_retries=0` / keepalive fixes).

### Registry + routing
- In-memory registry built from env (+ optional `AI_MODEL_REGISTRY_PATH`); default = derived from current LLM env for zero-config migration.
- **Lifecycle:** `reload_registry(env=...)` / build-on-demand so pytest `monkeypatch` env changes take effect; do not freeze a process-global stale registry across tests.
- Each entry: `id`, `display_name`, `provider`, `runtime`, `primary_capability`, `locality` (`local`|`remote`), `model_version`, limits, `supported_operations[]`, `status`, non-secret health.
- Resolve order: explicit `selection.model_id` → legacy `provider`+`model` → `AI_OP_<OPERATION>` → global default.
- Fallback only when configured; responses include `fallback_applied`, `requested_model_id`, `resolved_model_id`; never silent.

### Discovery API
- Canonical: `GET /ai/models` (+ optional `GET /ai/models/{id}`).
- Compat: `GET /llm/models` shim for language-capable models.
- **Docker:** add `location /ai/` to `frontend/nginx.conf` (mirror `/llm/` timeouts if needed) and pass `AI_OP_*` / `AI_FALLBACK_*` / `AI_MODEL_REGISTRY_PATH` through `docker-compose.yml`.
- Never return api_key, base_url secrets, absolute weight paths, or host usernames.

### Provenance
- Prefer additive `AiProvenance` / `ProjectGenerationMeta` fields + existing revision **`summary_json`** for extended metadata — **no Alembic** unless a query requirement forces new columns.
- Fields: `model_id`, `model_version`, `provider`, `runtime`, `capability`, `operation`, `generation_parameters` (bounded: temperature, timeout, candidate_count — no prompts/keys).
- Keep legacy `ai_provider` / `ai_model` (and project generation columns) populated for UI/compare compat.
- Frontend candidate Apply path must carry these fields (not only `provider`/`model`).

### Fake short-circuit strategy
Keep deterministic draft helpers (`generate_fake_*`, `edit_fake_*`, arrangement/development/reharm/motif fakes) as network short-circuits keyed by registry identity (`runtime=fake` / `model_id` starting with `fake:`), not by ad-hoc string compares scattered without registry. Prefer `is_fake_provider` to resolve against the registered fake model so services stop inventing parallel availability rules.

## Commit Plan
- **Commit 1** (tasks 1–3): “feat(ai): add capability registry and typed model interfaces”
- **Commit 2** (tasks 4–6): “feat(ai): wrap remote LLM providers and operation routing”
- **Commit 3** (tasks 7–8a): “feat(ai): discovery API, nginx/compose wiring, and fallback rules”
- **Commit 4** (tasks 9–9b): “feat(ai): enrich provenance through Apply path”
- **Commit 5** (tasks 10–12): “test(ai): V2 regression through runtime; docs and migration notes”

## Tasks

### Phase 0: Design freeze & package layout
- [x] Task 1: Document contracts and create `ai_runtime` package skeleton
  - Write `docs/ai-runtime.md` outline: capabilities (`primary_capability` + `supported_operations`), registry fields, `AiOperation` catalog, routing, discovery, fallback, provenance via `summary_json`, secret policy, migration from `/llm/models`, nginx `/ai/` note.
  - Create package:
    - `backend/app/ai_runtime/__init__.py`
    - `backend/app/ai_runtime/capabilities.py` — StrEnum of capabilities + operation→capability defaults
    - `backend/app/ai_runtime/operations.py` — `AiOperation` StrEnum matching the catalog table above
    - `backend/app/ai_runtime/types.py` — `ModelDescriptor` (with `primary_capability`, `supported_operations`), `ModelHealth`, `ResolvedModel`, `GenerationParameters`
    - `backend/app/ai_runtime/protocols.py` — typed interfaces listed above
    - `backend/app/ai_runtime/errors.py` — `ModelNotFoundError`, `ModelUnavailableError`, `CapabilityMismatchError`, `FallbackNotConfiguredError` (+ stable error codes for HTTP mapping)
  - Update `.ai-factory/ARCHITECTURE.md` Composition/LLM module row to mention `ai_runtime/` as the provider boundary; keep FluidSynth outside it.
  - Document capability shape: env chat models are `primary_capability=language_planner` with multi-op `supported_operations` (not multiple primaries).
  - LOGGING: DEBUG package import; INFO capability/operation enum load counts; never log secrets.
  - Files: above + `docs/ai-runtime.md` (skeleton), `.ai-factory/ARCHITECTURE.md`

### Phase 1: Registry, providers, runtimes
- [x] Task 2: Implement model registry and env bootstrap (compat with current LLM env)
  - `backend/app/ai_runtime/registry.py` — register/get/list/filter by capability/operation/status; **`reload_registry(env=...)`** for tests and config reload.
  - `backend/app/ai_runtime/bootstrap.py` — build from `load_llm_settings()`; model ids `provider:model`; chat models get `primary_capability=language_planner` + full creative `supported_operations` list; register unconfigured stubs for embedding/transcription/audio_generation.
  - Preserve `LLM_FAKE_MODE` semantics and default-provider rules; log when fake wins over real keys.
  - Support optional `AI_MODEL_REGISTRY_PATH` for extra entries; if unset, env-only.
  - LOGGING: INFO registered model ids + primary_capability + locality (no keys); WARN duplicate ids; DEBUG skipped unconfigured providers / reload.
  - Files: `registry.py`, `bootstrap.py`, tests `backend/tests/test_ai_runtime_registry.py` (include monkeypatch reload)
  - Depends on: Task 1

- [x] Task 3: Implement runtime adapters for current remote chat path
  - `backend/app/ai_runtime/runtimes/openai_compatible_chat.py` — `LanguageModel` that **must** call only `llm_chat_client.build_chat_openai` + `ainvoke_chat_text` (forbid inline `ChatOpenAI`).
  - `backend/app/ai_runtime/runtimes/fake_language.py` — registry-facing fake identity; orchestrators still call `fake_llm` draft helpers for short-circuit (adapter may expose markers / no-op complete for chat stages that never run under fake).
  - `backend/app/ai_runtime/runtimes/stub.py` — unavailable stubs raising `ModelUnavailableError` with stable codes; `status=unconfigured`.
  - LOGGING: INFO runtime construct (model_id, runtime, timeout_seconds); DEBUG invoke lengths/elapsed; WARN transport failures with error_type only.
  - Files: runtimes/*, tests `backend/tests/test_ai_runtime_language_adapter.py` (assert factory used, not inline ChatOpenAI)
  - Depends on: Task 2

### Phase 2: Operation routing & service integration
- [x] Task 4: Operation resolver + per-operation config + DTO cascade
  - `backend/app/ai_runtime/routing.py` — resolve `(AiOperation, request_selection) → ResolvedModel` using: explicit `model_id` → legacy provider+model → `AI_OP_*` → global default.
  - Document all env keys in `.env.example` (table above + `AI_FALLBACK_*`).
  - Additive `model_id` on `LLMModelSelection`; keep `provider`/`model`.
  - Loosen or extend provider Literals across **all** AI request/response DTOs that currently use `Literal["openai","deepseek","fake"]`:
    - `backend/app/schemas.py`
    - `backend/app/arrangement_schemas.py`
    - `backend/app/composition_development_schemas.py`
    - `backend/app/motif_schemas.py`
    - `backend/app/project_schemas.py` (`ProjectGenerationMeta`)
    - `backend/app/project_history_schemas.py` (`AiProvenance`)
    Prefer registry-validated strings with `model_id` as canonical; keep populating legacy `provider`/`model` for known remotes + fake.
  - LOGGING: INFO resolution path (explicit|legacy|op_default|global); WARN unavailable selection; ERROR capability/operation mismatch.
  - Files: `routing.py`, settings helper, `.env.example`, listed schema files, tests `backend/tests/test_ai_runtime_routing.py`
  - Depends on: Task 2

- [x] Task 5: Refactor LLM orchestrators to obtain models via resolver (no composition logic changes)
  - Touch: `llm_music_generator.py`, `llm_composition_editor.py`, `llm_composition_arrangement.py`, `llm_composition_development.py`, `llm_reharmonizer.py`, `llm_motif_editor.py`, plus thin callers in `main.py` / `routers/arrangement.py` / `composition_development.py` / `harmony.py` / `motifs.py`.
  - Replace direct `select_llm_provider` + inline client build with `resolve_model_for_operation(...)`; keep `select_llm_provider` as thin deprecated wrapper if needed for tests.
  - **Generate:** resolve **one** model for `AiOperation.generate` per request; do **not** switch models per LangGraph node in this plan (`generate_planner` / `generate_composer` env keys reserved only).
  - **Fake:** short-circuit via registry fake identity → existing `fake_llm` draft helpers (no network).
  - Map new domain errors to existing HTTP patterns (400 unsupported, 503 unavailable) with stable codes.
  - Do **not** change staged graph structure, validators, fingerprints, or V2 patch math.
  - LOGGING: keep existing stage logs; add `model_id`, `runtime`, `primary_capability`, `operation` extras; never log prompts/api keys.
  - Files: listed services + routers/`main.py` error mapping; regression via existing pytest modules
  - Depends on: Tasks 3, 4

- [x] Task 6: Ready/health integration
  - Extend `ready.py` with non-secret AI registry summary: counts by capability/status, default op routes (ids only).
  - Remote health = credentials present; stubs = unconfigured; never load weights on `/ready`.
  - LOGGING: DEBUG ready AI summary.
  - Files: `ready.py`, `test_health_and_cors.py` / secret hygiene
  - Depends on: Task 2

### Phase 3: Discovery API, proxy, fallback, provenance
- [ ] Task 7: Add `GET /ai/models` (+ optional `GET /ai/models/{id}`) and compat shim
  - New router `backend/app/routers/ai_models.py`; include from `main.py`.
  - DTOs in `backend/app/ai_runtime_schemas.py`: catalog + per-operation defaults + warnings.
  - Keep `GET /llm/models` behavior for language-capable models; docs mark shim as compat.
  - Secret hygiene: extend `test_secret_hygiene.py` for `/ai/models`.
  - LOGGING: INFO discovery requested; DEBUG filter/capability query params.
  - Files: router, schemas, `main.py`, `test_ai_models_routes.py`, `test_llm_routes.py`
  - Depends on: Tasks 2, 6

- [ ] Task 7b: Docker nginx proxy + Compose env passthrough for AI runtime
  - Add `location /ai/` to `frontend/nginx.conf` (proxy to backend; use LLM-like read timeouts if discovery stays light — still required for future AI routes under `/ai/`).
  - Pass through `AI_OP_*`, `AI_FALLBACK_*`, `AI_MODEL_REGISTRY_PATH` (and document) in `docker-compose.yml` alongside existing `OPENAI_*` / `LLM_*` vars.
  - Confirm `.env.example` documents Compose-visible names.
  - LOGGING: N/A for nginx; backend already logs discovery.
  - Files: `frontend/nginx.conf`, `docker-compose.yml`, `.env.example`; optional smoke asserting `/ai/models` reachable via same-origin proxy in Compose docs/tests
  - Depends on: Task 7

- [ ] Task 8: Graceful explicit fallback
  - Implement fallback chain in resolver; surface `fallback_applied`, `requested_model_id`, `resolved_model_id` on AI **operation** responses:
    - generate, region edit, arrangement preview, development preview, reharmonize preview, creative motif responses (every DTO that already returns `provider`/`model`).
  - If fallback not configured and primary unavailable → 503 with stable code (no silent substitute).
  - Tests: configured fallback succeeds with flag; unconfigured unavailable fails closed.
  - LOGGING: WARN when fallback used (ids only); ERROR when exhausted.
  - Files: `routing.py`, response schemas listed in Task 4, service return enrichment, tests
  - Depends on: Tasks 4, 5 *(not Task 7)*

- [ ] Task 9: Enrich AI provenance on revisions / generation meta (backend)
  - Extend `AiProvenance` and `ProjectGenerationMeta` with additive fields: `model_id`, `model_version`, `runtime`, `capability`, `operation`, `generation_parameters` (bounded object); keep `provider`/`model` filled for compat.
  - Persist extended fields via existing revision **`summary_json`** / provenance DTO — **prefer no Alembic**; document in migration notes. Only add columns if a concrete list/filter query requires them.
  - Secret guard: parameters must not include prompts/keys; reuse `persistence_secret_guard`; keep `warning_codes` bounded (existing sanitization).
  - LOGGING: DEBUG provenance attach; INFO operation + model_id on commit.
  - Files: `project_history_schemas.py`, history store/services, `project_schemas.py`, tests `test_persistence_secret_guard.py`, history tests
  - Depends on: Tasks 5, 8

- [ ] Task 9b: Plumb provenance through frontend candidate Apply path
  - Extend `musicApi.js` response contracts to accept additive provenance/fallback fields from AI endpoints.
  - Extend `compositionCandidateLifecycle.js` envelopes beyond `provider`/`model` (`model_id`, `runtime`, `capability`, `operation`, `generation_parameters`, fallback flags as applicable).
  - Ensure durable Apply / `projectPersistRevision` / history commit payloads include extended `AiProvenance`; keep `toHistoryAiWarningCodes` sanitization.
  - Versions panel may display `model_id` when present (minimal UI).
  - LOGGING: DEBUG envelope fields present (no secrets); existing appLogger levels.
  - Files: `musicApi.js`, `compositionCandidateLifecycle.js`, `projectPersistRevision.js`, `musicStore.js` Apply paths as needed, unit tests for those utils/store slices
  - Depends on: Task 9

### Phase 4: Frontend discovery, tests, docs
- [ ] Task 10: Frontend discovery client without breaking global selector
  - Add `fetchAiModels()` in `musicApi.js` calling `/ai/models`; keep using `/llm/models` for current MusicGenerator selector until a follow-up UX plan.
  - Optionally store catalog in Zustand for future per-operation pickers; **do not** require per-op UI in this plan.
  - Ensure no secrets in client logs.
  - LOGGING: existing appLogger levels; DEBUG catalog counts.
  - Files: `musicApi.js`, tests; light store hookup optional
  - Depends on: Tasks 7, 7b

- [ ] Task 11: V2 regression tests through new abstraction + stub capability registrations
  - Prove generate/edit/arrange/develop/reharmonize/motif fake paths still pass via registry-resolved models.
  - New focused tests: registry bootstrap + reload; routing per operation; fallback flags; `/ai/models` shape + secret hygiene; provenance fields on apply/commit (Task 9b path); stub capabilities listed as unconfigured.
  - Run: backend pytest subset + full backend pytest; existing fake-provider and arrangement/development route tests must stay green.
  - LOGGING: assert key log events in unit tests where project already does so.
  - Files: `backend/tests/test_ai_runtime_*.py`, updates to existing LLM/history/frontend unit tests as needed
  - Depends on: Tasks 5–9b

- [ ] Task 12: Documentation, migration notes, AGENTS/DESCRIPTION touch-ups
  - Complete `docs/ai-runtime.md` (architecture, capabilities shape, `AiOperation` catalog, registry lifecycle, routing env, discovery, nginx `/ai/`, fallback, provenance/`summary_json`, secrets, FluidSynth exclusion, reserved planner/composer keys).
  - Migration notes: old `provider`+`model` → `model_id`; `/llm/models` shim; additive provenance; no silent fallback; Compose/nginx requirements.
  - Update `README.md` API list, `.env.example`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ROADMAP.md` (unchecked milestone), `.ai-factory/ARCHITECTURE.md`.
  - Files list must include `frontend/nginx.conf` and `docker-compose.yml`.
  - LOGGING: N/A for docs; ensure examples never show real keys.
  - Files: docs/*, README, .env.example, AGENTS.md, DESCRIPTION.md, ROADMAP.md, ARCHITECTURE.md, nginx.conf, docker-compose.yml (docs references)
  - Depends on: Tasks 1–11

## Acceptance Criteria Mapping
| Criterion | Tasks |
|-----------|-------|
| Audit before design | Plan audit section + Task 1 docs |
| Capability categories | Task 1 |
| Common model registry + metadata | Tasks 2, 7 |
| Separate provider / runtime / model / capability | Tasks 1–3 |
| No remote LLM regressions | Tasks 5, 11 |
| Typed interfaces (not one mega-method) | Task 1, 3 |
| Per-operation model selection | Task 4 |
| Discovery/status API | Tasks 7, 7b, 10 |
| Explicit fallback only | Task 8 |
| Provenance metadata | Tasks 9, 9b |
| No keys/FS internals to frontend | Tasks 7, 9, 11 secret tests |
| V2 tests through abstraction | Task 11 |
| Tests, logging, migration notes, architecture docs | Settings + Tasks 1, 11, 12 |

## Risks & Mitigations
- **Literal explosion / breaking clients** — additive `model_id` + keep legacy fields; shim `/llm/models`; cascade DTOs listed in Task 4.
- **Big-bang orchestrator rewrite** — only swap client resolution; leave LangGraph stages intact; one model per generate request.
- **Silent quality changes via fallback** — fail closed unless fallback explicitly configured; always surface flags.
- **Heavy local model load on ready** — never load weights in `/ready`; status from config presence only.
- **Docker discovery black hole** — Task 7b nginx `/ai/` + Compose env passthrough.
- **Provenance never reaches DB** — Task 9b Apply-path plumbing.
- **ChatOpenAI transport regressions** — Task 3 mandates shared `llm_chat_client` factory.
- **Scope creep into training/ACE-Step / per-node planner-composer** — stubs + reserved env only; separate future plans.

## Out-of-scope follow-ups (explicit)
- Per-operation model picker UI in Generate/Arrange/Develop/Harmony tabs
- Per-stage LangGraph resolution of distinct `generate_planner` vs `generate_composer` models
- Local GGUF / vLLM / llama.cpp runtime
- Music Transformer / MIDI symbolic model training loop
- Whisper / ACE-Step concrete backends
- Embedding store and similarity search UX

## Improvement Notes (2026-09-21)
Applied `/aif-improve` pass: nginx/Compose wiring (7b); Apply-path provenance plumbing (9b); capability shape clarified; `AiOperation` catalog; registry reload; ChatOpenAI factory mandate; fake/generate resolution rules; Task 8 deps fixed (4+5 only); provenance prefers `summary_json` over Alembic; DTO Literal cascade listed; commit plan expanded to 5 checkpoints.
