# Implementation Plan: V4 Plugin and Extension SDK

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-25
Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-25)

INFO [aif-plan] resolved plan file: `.ai-factory/plans/v4-plugin-extension-sdk.md` (format=slug)

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-25)
- Scope: a stable, in-process **plugin SDK** so a developer can add a new symbolic composer (and the other listed categories) **outside** core application code, point the process at that directory, restart or reload, and see it as an available capability. No marketplace, no remote install, no `composition.v4`, no `DATASET_ROOT` writes
- Parent foundations (reuse, do not fork):
  - `.ai-factory/plans` history for **Unified AI runtime and model-provider architecture (V3)** — shipped: `ai_runtime` capability registry, `ModelDescriptor`, `AiOperation`, protocols, `GET /ai/models`, `reload_registry`
  - V4 multi-agent layer — shipped: `MusicAgent` / `AgentRegistry`. Plugin agents must not be spliced into the spine or `KNOWN_AGENT_IDS` in this plan
  - Symbolic composer path — shipped: `generate_symbolic_composition` with closed `SymbolicBackend = Literal["fake", "music_transformer"]`
  - Analyzer, transcription, neural render, and MusicXML/MIDI/WAV export — shipped as closed orchestrators. This plan adds **protocols + host dispatch + discovery**, and rewires only the symbolic-composer selection seam required by the acceptance test

## Roadmap Linkage
Milestone: "V4 plugin and extension SDK"
Rationale: Capabilities are still added by editing `ModelRuntimeId`, `bootstrap.py`, and per-service `if runtime ==` branches, so a third-party symbolic composer cannot appear after install and restart. Every current roadmap milestone is complete; add this as a **new unchecked** milestone in `.ai-factory/ROADMAP.md` during the docs task and check it only after the contract tests pass.

## Goal

A developer writes a plugin package that imports only `app.plugin_sdk`, drops it in a directory, sets `PLUGIN_PATHS`, restarts or calls reload, and:

1. The host reads a stable `plugin.manifest.v1` (id, name, version, API compatibility, capabilities, dependencies, configuration schema).
2. Incompatible API versions, invalid config, forbidden imports, cycles, and missing dependencies **skip that plugin** and leave the process up.
3. A symbolic-composer plugin is registered as a `ModelDescriptor` with capability `symbolic_composer` and shows up on `GET /ai/models` and `GET /plugins`.
4. When that model id is selected for `generate_composer`, the host calls the plugin, then runs the existing composition normalizer and validator. The plugin never persists a project and never writes `DATASET_ROOT`.
5. The other seven categories have Python interfaces, the same manifest and discovery rules, and a host call path covered by contract tests. They do not replace the shipped orchestrators.

```text
PLUGIN_PATHS directories
        │
plugin.manifest.v1  (+ optional config.json)
        │
API major check, dependency order, JSON-schema config, AST import allowlist
        │
Activated plugin instance (public app.plugin_sdk only)
        │
┌───────┴────────────────────────────────────────────────────────┐
│ Model categories                                                │
│ language_model, symbolic_composer, transcription, neural_render│
│   → one new ModelRuntimeId: "plugin"                            │
│   → reload_plugins executes code (lifespan / POST reload)      │
│   → reload_registry reattaches cached descriptors only         │
│   → GET /ai/models                                              │
│ Symbolic composer only: generate_symbolic_composition branch    │
├─────────────────────────────────────────────────────────────────┤
│ Non-model categories                                            │
│ analyzer, music_agent, export_format, postprocess              │
│   → PluginCatalog (not KNOWN_AGENT_IDS, not analysis.v1 stages)│
│   → GET /plugins                                                │
└─────────────────────────────────────────────────────────────────┘
```

**Acceptance one-liner:** A symbolic composer implemented outside core code, installed via `PLUGIN_PATHS`, is listed as capability `symbolic_composer` after reload, and a contract test can invoke it through the existing generate-composer seam.

Shipped examples (loaded only when their directory is on `PLUGIN_PATHS` or a test points at them):

| Example | Category | Behavior |
|---------|----------|----------|
| `deterministic_analyzer` | analyzer | Returns note count and track count for the given composition dict. Does not mutate it |
| `sample_symbolic_generator` | symbolic_composer | Returns a deterministic 2-bar C-major `composition.v2` dict. Host validates it |

Not:

> A store where users browse, download, and hot-swap DAW plugins, or a rewrite of every generator onto a new plugin bus.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Plugin** | A directory outside the core call graph with `plugin.manifest.json` and a Python entry attribute. Not a setuptools distribution requirement |
| **Manifest** | `plugin.manifest.v1`. Discovery metadata. No code, no secrets, no PCM, no note arrays |
| **SDK** | Import package `app.plugin_sdk`. The only `app.*` surface a plugin may import |
| **Host** | `app.plugin_host`. Loads, guards, registers, and invokes. Core code. Plugins must not import it |
| **API compatibility** | Manifest field compared to `PLUGIN_API_VERSION`. v1 accepts major `1` only (`"1"`, `"1.0"`, `"1.0.0"`) |
| **Capability** | A string the host maps to an existing `ModelCapability` or to a non-model catalog kind. Not a new `AiOperation` |
| **Activation** | Instance constructed and registered. Failed plugins stay in the catalog with a status and a stable code |
| **Configuration schema** | JSON Schema subset on the manifest. Host validates `config.json`. Invalid config does not activate |
| **Install** | Put the directory on `PLUGIN_PATHS` and restart or `POST /plugins/reload`. No marketplace, no pip execution by the host |
| **Runtime `plugin`** | The single new `ModelRuntimeId`. One id for every model-category plugin. Dispatch is by `model_id`, not a new literal per plugin |

## Non-goals (v1)

- Marketplace, remote index, ratings, download, signature infrastructure, or the host running `pip install`
- Making the backend a setuptools package or an entry-point scan (directory manifests are the install path; optional entry points are a later plan)
- Replacing OpenAI / DeepSeek / fake / local chat generation, Music Transformer, FluidSynth, the analysis stage list, transcription engine ids, neural-render runtime switches, the agent spine, or mix-plan compile
- New `AiOperation` values or a new playable score schema (`composition.v4`)
- Hot reload of a single function without a process restart **or** an explicit `POST /plugins/reload` (no watchdog)
- Sandboxed OS processes, seccomp, or a separate interpreter. The guard is an import allowlist plus host-owned validation, not a VM
- Letting plugins open `PROJECT_DB_PATH`, write `DATASET_ROOT`, receive API keys from `llm_settings`, or choose output filesystem paths
- Frontend plugin manager UI (discovery is HTTP; existing model catalog can show `runtime=plugin` rows if it already renders `/ai/models`)
- Executing plugin-supplied shell commands or arbitrary export into the automatic download buttons
- Loading plugins when `PLUGIN_PATHS` is unset (default behavior of the app stays unchanged)
- Persistent plugin config in SQLite

## Approach Evaluation (locked)

### Part A — Where a plugin lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Edit `bootstrap.py` per plugin** | Matches today | Fails the acceptance test; core patch every time | **Reject** |
| **B. setuptools entry points** | Familiar "install" | Repo has no `pyproject.toml`; would force packaging the monolith | **Reject** for v1 |
| **C. Directory + `plugin.manifest.json` on `PLUGIN_PATHS`** | Works without packaging; restart/reload matches the AC; tests pass a tmp path | Developer copies a folder instead of `pip install` | **Accepted** |

**Locked:** `PLUGIN_PATHS` is a path-separator-separated list (`os.pathsep`). Each entry is a plugin directory or a parent whose immediate children are plugin directories. A directory is a plugin iff it contains `plugin.manifest.json`. Examples live in `backend/examples/plugins/` and are **not** on the default path.

### Part B — How model plugins meet V3

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Open `ModelRuntimeId` per plugin** | Typed | Core edit per install; contradicts the goal | **Reject** |
| **B. One runtime id `plugin` + descriptor from the manifest** | Reuses `register_model`, `list_models`, `GET /ai/models`, fallback filters | One more closed literal, once | **Accepted** |
| **C. Only `AI_MODEL_REGISTRY_PATH` JSON** | Already exists | Metadata only; the doc and code both refuse to load adapter classes | **Reject** as the mechanism |

**Locked:** Add `"plugin"` to `ModelRuntimeId` in `backend/app/ai_runtime/types.py`. `reload_plugins()` is the only path that imports and calls plugin `register()`. FastAPI lifespan and `POST /plugins/reload` call it. `GET /ai/models` already calls `reload_registry()` on every request (`backend/app/routers/ai_models.py`); that function replaces `_registry` under its lock and must **not** re-execute plugin code. When `descriptors is None`, the end of `reload_registry` lazy-imports `plugin_model_descriptors()` and `register_model(..., overwrite=False)` from the already-loaded catalog, inside the same lock. When the caller passes an explicit `descriptors=` tuple, do not attach plugins. A bridge exception logs a warning and leaves the built-in registry in place. `plugin_host` must not import `ai_runtime.registry` at module level. Each model descriptor sets `provider="plugin"` and `health=ModelHealth(status="ready", credentials_present=False)`. Inactive plugins produce no descriptor.

Manifest ids match `^[a-z][a-z0-9_]{1,63}$`. The registered model id is `plugin:{manifest_id}` (for example `plugin:sample_symbolic_generator`). That shape matches the existing `provider:model` id used by discovery and by the file-registry colon check. The manifest id itself stays a plain slug.

Category → existing capability (no new enum members):

| Manifest category | `ModelCapability` | `AiOperation` on the descriptor |
|-------------------|-------------------|---------------------------------|
| `language_model` | `language_planner` | `generate_planner` |
| `symbolic_composer` | `symbolic_composer` | `generate_composer` |
| `transcription_model` | `audio_transcription` | `transcribe` |
| `neural_renderer` | `audio_generation` | `audio_render` |

`analyzer`, `music_agent`, `export_format`, and `postprocess` do **not** get a `ModelDescriptor`. They appear only on `GET /plugins`.

### Part C — Symbolic composer invocation (the acceptance path)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Replace `SymbolicBackend` default with plugins** | Always visible | Breaks fake CI and Music Transformer selection | **Reject** |
| **B. Extend `SymbolicBackend` with `"plugin"` and call it only when the resolved model runtime is `plugin`** | Keeps fake/MT defaults; one branch in `generate_symbolic_composition` | Touches one service | **Accepted** |
| **C. HTTP route that calls the plugin and skips validation** | Small | Undermines the composition contract | **Reject** |

**Locked:** `generate_symbolic_composition` today has no model-id argument and always follows `resolve_symbolic_backend` (`fake` or `music_transformer`). Add an optional `model_id`. Call `PluginHost.compose` only when that id was selected explicitly and `get_model` reports `runtime == "plugin"` plus capability `symbolic_composer`. On that path do **not** call `resolve_symbolic_backend`. Explicit means `AI_OP_GENERATE_COMPOSER` is set to that id, or the hybrid generation state already holds that `composer_model_id` because `_resolve_hybrid_stage_models` resolved it. `_symbolic_generate` currently ignores the stored id; Task 8 passes it through only in the plugin case.

`default_operation_routes` picks the first ready `symbolic_composer` when `AI_OP_GENERATE_COMPOSER` is unset (`backend/app/ai_runtime/routing.py`). Exclude `runtime == "plugin"` from that implicit pick so a loaded plugin does not become the silent default. With the example on `PLUGIN_PATHS` and the env key unset, `generate_symbolic_composition` without `model_id` still returns `backend == "fake"` (`backend/tests/test_symbolic_composition_generate.py`).

Host deep-copies the plugin mapping, parses it as `CompositionV2` (`extra="forbid"`), then `validate_composition_integrity(..., profile="canonical")`. The `generation` profile enforces density floors a two-bar sample will fail. Invalid output raises a typed error with code `plugin_output_invalid` and does not write a project. `SymbolicGenerateResult.model_id` is `plugin:{manifest_id}` and `backend` is `"plugin"`. Notes come from the returned `tracks[].events[]` only.

### Part D — Non-model categories

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Insert analyzers into `analyze_composition` and agents into `KNOWN_AGENT_IDS`** | Sits in existing tabs | Changes shipped report/workflow shape; `ensure_known_agent` is a closed list on purpose | **Reject** |
| **B. Side catalog + host methods + `GET /plugins`** | Additive; contract-testable | Not in the Analysis tab or spine until a later plan | **Accepted** |

**Locked host methods** on `PluginHost` (called by tests and by the thin router):

| Category | Host method | Host postcondition |
|----------|-------------|--------------------|
| `analyzer` | `analyze(plugin_id, composition) -> dict` | JSON-equal composition before vs after. Fragment schema `plugin.analysis.fragment.v1` |
| `music_agent` | `run_agent(plugin_id, request_dict) -> dict` | Result has `mutates_composition: false`. Not registered in `AgentRegistry` |
| `export_format` | `export(plugin_id, composition) -> (bytes, media_type)` | Composition unchanged. Bytes capped by `PLUGIN_MAX_EXPORT_BYTES` |
| `postprocess` | `postprocess(plugin_id, document) -> dict` | Input object unchanged (deep-compare). Return is a new dict, not applied to any project |
| `language_model` | `complete_text(model_id, prompt) -> str` | Does not replace `llm_music_generator` |
| `transcription_model` | `transcribe(model_id, audio_ref) -> dict` | Does not replace `resolve_engine`. Caller does not receive a filesystem path under the data roots |
| `neural_renderer` | `render(model_id, spec) -> dict` | Return metadata only in v1 examples (no WAV write). Host does not open stem paths for the plugin |

`GET /plugins/{id}` returns manifest public fields plus status. Do not add `POST /plugins/{id}/invoke`.

### Part E — Import boundary

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Docs only** | No code | Requirements 4 fails as soon as someone imports `app.services` | **Reject** |
| **B. AST allowlist before exec, plus a loader that refuses the module if it imports forbidden `app` prefixes** | Matches "must not" | Does not stop a plugin that imports a forbidden module from inside a function after a dynamic string built at runtime beyond a simple detector | **Accepted**, with a runtime `sys.meta_path` hook installed for the plugin module prefix during `register()` and the first invoke |

**Forbidden import prefixes:** `app.services`, `app.ai_runtime`, `app.ai_agents`, `app.db`, `app.routers`, `app.main`, `app.plugin_host`, and any `app.*` not under `app.plugin_sdk`.

**Allowed:** stdlib, third-party, `app.plugin_sdk` (and submodules exported from that package).

Dynamic `importlib.import_module("app.services...")` and `__import__` of those prefixes are violations. On violation the plugin status is `rejected` and code is `plugin_forbidden_import`. The module is dropped from `sys.modules`.

### Part F — Version and config

**Locked:**

- `PLUGIN_API_VERSION = "1.0.0"` in `app.plugin_sdk.version`
- Manifest `api_compatibility` must parse as major `1`. Anything else → status `incompatible`, code `plugin_api_incompatible`, not activated
- Plugin `version` is semver `MAJOR.MINOR.PATCH`. Stored and returned. Host does not range-match plugin versions except dependencies that specify an exact version string
- `dependencies`: list of `{ "id": "<manifest id>", "version": "1.2.3" }` with optional `version` (exact match when present). Unknown id → `plugin_dependency_missing`. Cycle → `plugin_dependency_cycle` for every id in the cycle. Load order is a stable topological sort (manifest id as tiebreak). Dependencies control activation order only. They do not put another plugin directory on `sys.path`, and plugin A must not import plugin B's private module
- `configuration_schema` is an object schema with `type: object`, `additionalProperties: false`, and property keywords limited to `type` (`string|number|integer|boolean`), `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `default`. No `$ref`, no nested objects in v1. Host validates with a small local validator (do not add a jsonschema dependency)
- Config file: `config.json` next to the manifest. Absent schema + absent file is valid. Schema with `required` and missing file or missing key → `plugin_config_invalid`
- Property names matching `(?i)(secret|password|token|api_key|apikey|authorization)` are redacted in every log line. Config values are never logged even when not secret
- Config is not written to SQLite or project history

### Part G — Failure policy

A bad plugin never aborts startup and never removes built-in models.

| Condition | Status | Code |
|-----------|--------|------|
| Manifest JSON / schema invalid | `rejected` | `plugin_manifest_invalid` |
| API major mismatch | `incompatible` | `plugin_api_incompatible` |
| Duplicate id (first wins) | `rejected` | `plugin_duplicate_id` |
| Dependency missing or version mismatch | `unsatisfied` | `plugin_dependency_missing` |
| Cycle | `unsatisfied` | `plugin_dependency_cycle` |
| Config invalid | `rejected` | `plugin_config_invalid` |
| Forbidden import | `rejected` | `plugin_forbidden_import` |
| Entry attribute missing or not callable | `rejected` | `plugin_entry_invalid` |
| `register()` raised | `rejected` | `plugin_register_failed` |
| Output failed host validation | activation stays `active`; call returns error | `plugin_output_invalid` |
| Over cap | not loaded | `plugin_limit_exceeded` |

Catalog entries for failures include `id` (when the file parsed enough to know it), `status`, `code`, and `message` that names the code, not a stack trace. Stack traces stay in DEBUG logs without file contents.

Caps (env, invalid values log the **name** and fall back, same style as `llm_settings.py`):

| Env | Default |
|-----|---------|
| `PLUGIN_PATHS` | empty (disabled) |
| `PLUGIN_MAX_COUNT` | 32 |
| `PLUGIN_MAX_MANIFEST_BYTES` | 65536 |
| `PLUGIN_MAX_EXPORT_BYTES` | 2000000 |
| `PLUGIN_RELOAD_ENABLED` | `1` (gates `POST /plugins/reload` only) |

Path rule: `Path.resolve()` the candidate and require it to be a real directory. Reject a plugin root that resolves outside the path entry that declared it (symlink escape). Do not follow a symlink to a directory that is not a child of the configured entry.

## Audit Summary (current state)

| Area | Path | Fact that shapes this plan |
|------|------|----------------------------|
| Protocols | `backend/app/ai_runtime/protocols.py` | `LanguageModel.complete_text`, unused `SymbolicMusicModel`, `AudioTranscriptionModel.transcribe`, `AudioGenerationModel.render` |
| Runtime id closed set | `backend/app/ai_runtime/types.py` `ModelRuntimeId` | Must add one literal `"plugin"` |
| Registry rebuild | `ai_runtime/registry.py` `reload_registry` | Replaces `_registry` under the lock. `descriptors is None` rebuilds from env. An explicit `descriptors=` tuple must stay plugin-free. Reattach cached descriptors only; do not re-exec `register()` |
| Bootstrap | `ai_runtime/bootstrap.py` | Static imports only. `AI_MODEL_REGISTRY_PATH` is metadata, not code. Do not overload it |
| Capabilities | `ai_runtime/capabilities.py` | Six capabilities. Map four plugin categories onto four of them. Do not add an enum value |
| Symbolic branch | `services/symbolic_composition_generate.py`, `llm_music_generator.py` `_symbolic_generate` | No `model_id` argument today. Hybrid stores `composer_model_id` from `_resolve_hybrid_stage_models` and then ignores it. `default_operation_routes` uses the first ready composer when `AI_OP_GENERATE_COMPOSER` is unset |
| Agents | `ai_agents/bootstrap.py`, `ai_agents/schemas.py` `AgentRunResult` | Closed `KNOWN_AGENT_IDS`. `mutates_composition` is a result field that must be false. Plugin agents stay outside that registry and the SDK must not import `app.ai_agents` |
| Analysis | `services/composition_analysis.py` | Fixed stage list. Do not insert plugins there |
| Transcription | `services/audio_transcription/engines.py` | Closed `AudioEngineId` |
| Neural | `services/neural_audio_render.py` `_build_model_instance` | Closed runtime switch. Do not add plugin ids there in v1; host `render()` is the contract path |
| Export | `main.py` `POST /export/musicxml|midi|wav` | No exporter protocol. Keep those routes. Plugin export is `PluginHost.export` |
| Packaging | no `pyproject.toml` | No entry points |
| Discovery route | `routers/ai_models.py` | Calls `reload_registry()` per request, so plugin code must not run there. Cached descriptors are what make `plugin:{id}` visible. Model ids use `{model_id:path}` because they contain `:` |
| Logging | `ready.py` `LOG_LEVEL` | `DEBUG|INFO|WARNING|ERROR`, logger name = module, never secrets or payloads |
| Tests | `backend/tests/test_ai_runtime_registry.py` | `clear_registry_for_tests` autouse pattern to copy |

## Public SDK surface (locked)

Package `backend/app/plugin_sdk/`:

| Module | Exports |
|--------|---------|
| `version.py` | `PLUGIN_API_VERSION` |
| `manifest.py` | `PluginManifestV1`, `parse_manifest`, category enum, capability ids allowed per category |
| `config.py` | `validate_config(schema, document) -> dict` |
| `protocols.py` | Eight protocols below |
| `context.py` | `PluginContext` (`api_version`, `plugin_id`, `config`, `logger`) |
| `errors.py` | `PluginError` with `.code` |
| `__init__.py` | `__all__` of the above only |

`PluginContext.logger` is a wrapper that drops `config` kwargs and redacts secret-like mapping keys.

Protocols (all sync in v1 so examples stay free of an event-loop policy; the host may call them from async routes with a direct call):

```text
LanguageModelPlugin.complete_text(ctx, prompt: str) -> str
SymbolicComposerPlugin.compose(ctx, request: Mapping) -> Mapping
MusicAgentPlugin.run(ctx, request: Mapping) -> Mapping
AnalyzerPlugin.analyze(ctx, composition: Mapping) -> Mapping
TranscriptionModelPlugin.transcribe(ctx, audio_ref: Mapping) -> Mapping
NeuralRendererPlugin.render(ctx, spec: Mapping) -> Mapping
ExportFormatPlugin.export(ctx, composition: Mapping) -> tuple[bytes, str]
PostprocessPlugin.process(ctx, document: Mapping) -> Mapping
```

Entry convention: `plugin.py` attribute `register(ctx) -> instance` where `instance` matches the protocol for `manifest.category`. Manifest field `entry` defaults to `"plugin:register"` (`module:attr`, module is a file stem inside the plugin directory, no dots that climb out).

The SDK does **not** re-export Pydantic composition models, `ai_runtime`, or services. Request and response bodies are mappings. The host interprets composer output as a composition document.

## Tasks

### Phase 1: Contracts

- [x] Task 1: Manifest, API version, and configuration schema
  - Add `backend/app/plugin_sdk/version.py`, `manifest.py`, `config.py`, `errors.py`, and package `__init__.py` with the locked exports
  - `PluginManifestV1` fields: `schema_version` literal `plugin.manifest.v1`, `id`, `name` (1–80 chars), `version` (semver), `api_compatibility`, `category` (the eight names), `capabilities` (non-empty, each allowed for that category), `dependencies` (id + optional exact version), `configuration_schema` (subset or null), `entry` default `plugin:register`
  - Category capability allow-lists exactly as Part B for model categories; non-model categories allow only their own kind string (`analyzer`, `music_agent`, `export_format`, `postprocess`)
  - `validate_config` implements the Part F keyword subset and `additionalProperties: false`
  - LOGGING: `app.plugin_sdk.manifest` and `app.plugin_sdk.config`. DEBUG on parse start with manifest path **basename** only. WARNING on validation failure with `code` and plugin `id` when known. Never log raw JSON, config values, or schema defaults that might be secrets. Levels follow `LOG_LEVEL`
  - Files: `backend/app/plugin_sdk/`

- [x] Task 2: Category protocols and `PluginContext`
  - Add `protocols.py` and `context.py` as locked above
  - `@runtime_checkable` protocols. Host checks the method for the manifest category only (extra methods ignored)
  - Context logger wrapper: INFO/DEBUG/WARNING/ERROR forward to `logging.getLogger("app.plugin_sdk.plugin." + plugin_id)`; strip kwargs named `config`, `prompt`, `composition`, `events`, `audio`; redact mapping keys that match the secret regex by replacing values with `"[redacted]"`
  - LOGGING: document the wrapper behavior in the module docstring. DEBUG once when a redaction happens, with the key name and not the value
  - Depends on Task 1
  - Files: `backend/app/plugin_sdk/protocols.py`, `context.py`

- [x] Task 3: Host settings
  - Add `backend/app/plugin_host/settings.py` with `load_plugin_settings(env=None) -> PluginSettings` frozen dataclass
  - Parse the caps in Part G. Booleans accept `1|true|yes|on` like `llm_settings.py`. Bad integers log the env **name** and fall back to the default
  - Empty `PLUGIN_PATHS` means the host loads nothing
  - LOGGING: INFO one line at load with `enabled` (paths non-empty), `path_count`, and the four caps. Never log path contents if a segment contains `key`, `token`, or `secret`. DEBUG the resolved directory count
  - Files: `backend/app/plugin_host/settings.py`

### Phase 2: Loader and guards

- [x] Task 4: Directory loader, dependency order, activation records
  - Add `backend/app/plugin_host/loader.py` and `catalog.py`
  - Scan rules and symlink confinement from Part A and Part G
  - Parse manifests, apply version check, sort dependencies, enforce `PLUGIN_MAX_COUNT` in stable path order (skip the rest with `plugin_limit_exceeded`)
  - Catalog is an in-memory map guarded by a lock, with `clear_plugins_for_tests()`
  - Duplicate ids: first discovered path wins
  - Do not import plugin code in this task beyond recording the entry spec; Task 5 performs the import
  - LOGGING: INFO `plugin load start` with path count; INFO per accepted candidate `id`, `version`, `category`, `api_compatibility`; WARNING per skip with `code` and `id`. DEBUG dependency edge list as id strings only
  - Depends on Tasks 1 and 3
  - Files: `backend/app/plugin_host/loader.py`, `catalog.py`

- [x] Task 5: Import allowlist and entry call
  - Add `backend/app/plugin_host/import_guard.py`
  - AST-scan every `.py` file under the plugin directory before execution. Reject forbidden prefixes, relative imports that escape the directory, and `importlib`/`__import__` string literals that match a forbidden prefix
  - Load the entry module with `importlib.util.spec_from_file_location` under a private module name `mukit_plugin_<id>`. Do not insert the plugin directory on `sys.path`
  - Dependencies from Part F only decide activation order and exact version match. Do not expose another plugin's directory or module to this importer. Plugin A must not import plugin B's private module
  - Install the meta-path hook for that module prefix while calling `register(ctx)` and while invoking protocol methods
  - On success, store the instance on the catalog record and set status `active`
  - On failure, remove the private module from `sys.modules` and record the Part G code
  - LOGGING: DEBUG `register start/end` with plugin id and elapsed ms. WARNING `plugin_forbidden_import` with the forbidden module name (not the source line text). ERROR `plugin_register_failed` with exception **type** only
  - Depends on Tasks 2 and 4
  - Files: `backend/app/plugin_host/import_guard.py`, call from `loader.py`

- [x] Task 6: Reload lifecycle beside the model registry
  - Add `backend/app/plugin_host/bridge.py` with `plugin_model_descriptors() -> tuple[ModelDescriptor, ...]` (read the catalog; do not import or call `register`) and `reload_plugins(env) -> catalog snapshot` (the only function that executes plugin code)
  - `plugin_host` must not import `app.ai_runtime.registry` at module level. The registry lazy-imports the bridge inside the function
  - Call `reload_plugins()` from FastAPI lifespan (`backend/app/main.py`) before serving, and from `POST /plugins/reload` (route arrives in Task 10)
  - At the end of `reload_registry`, when `descriptors is None`, reattach `plugin_model_descriptors()` with `register_model(..., overwrite=False)` inside the existing lock. When `descriptors is not None`, do not attach. `GET /ai/models` already calls `reload_registry()` per request, so this path must not re-enter `reload_plugins()`
  - A second `reload_registry()` without `reload_plugins()` must not call `register()` again. `reload_plugins()` replaces the catalog (no duplicate actives)
  - Exceptions in the bridge must not prevent the built-in registry from returning
  - LOGGING: INFO `plugins loaded` with active count when `reload_plugins` runs. INFO `plugins attached` with model-descriptor count on reattach, at DEBUG if the count is unchanged. WARNING if the bridge raises, with exception type, after the built-in registry is intact. Never log prompts or config
  - Depends on Tasks 4 and 5
  - Files: `bridge.py`, `ai_runtime/registry.py`, `main.py` lifespan

### Phase 3: Dispatch

- [x] Task 7: Model descriptors for the four model categories
  - Build `ModelDescriptor` in the bridge: `id=plugin:{manifest_id}`, `provider="plugin"`, `runtime="plugin"`, `health=ModelHealth(status="ready", credentials_present=False)`, capability and `supported_operations` as `AiOperation` values from Part B, `locality="local"`, `status="ready"`, `display_name` from manifest name, `model_version` from manifest version, no secrets in `limits`
  - Add `"plugin"` to `ModelRuntimeId`
  - Inactive plugins produce no descriptor
  - Language, transcription, and neural rows are discoverable and reachable through `PluginHost.complete_text` / `transcribe` / `render` only. Do not edit `llm_music_generator.py`, `audio_transcription/engines.py`, or `neural_audio_render._build_model_instance`
  - LOGGING: DEBUG each attached model id and capability. No prompts
  - Depends on Tasks 2 and 6
  - Files: `backend/app/ai_runtime/types.py`, `backend/app/plugin_host/bridge.py`, `backend/app/plugin_host/invoke.py`

- [x] Task 8: Symbolic composer seam
  - Add optional `model_id` to `generate_symbolic_composition` in `backend/app/services/symbolic_composition_generate.py`. The function currently always uses `resolve_symbolic_backend` and has no model-id argument
  - Call `PluginHost.compose` only when `model_id` is present, `get_model` reports `runtime == "plugin"`, and capability is `symbolic_composer`. On that path do not call `resolve_symbolic_backend`. Extend `SymbolicBackend` with `"plugin"`. `SymbolicGenerateResult.model_id` is `plugin:{manifest_id}`
  - Host deep-copies the mapping, parses `CompositionV2` (`extra="forbid"`), then `validate_composition_integrity(..., profile="canonical")`. Use `canonical` because `generation` enforces density floors a two-bar sample will fail. On failure raise a typed error with code `plugin_output_invalid` and do not write a project
  - In `default_operation_routes` (`backend/app/ai_runtime/routing.py`), skip descriptors with `runtime == "plugin"` when choosing the implicit first ready `generate_composer`. An explicit `AI_OP_GENERATE_COMPOSER=plugin:{id}` still selects that model
  - Hybrid already stores `composer_model_id` from `_resolve_hybrid_stage_models` and `_symbolic_generate` ignores it. Pass that stored id into `generate_symbolic_composition` only when its runtime is `plugin`. With `AI_OP_GENERATE_COMPOSER` unset, `backend/tests/test_symbolic_composition_generate.py` must still see `backend == "fake"`
  - LOGGING: INFO `symbolic plugin compose` with model id and `valid=true/false`. DEBUG note count after validation. Never log pitches as a full event array and never log the prompt
  - Depends on Task 7
  - Files: `symbolic_composition_generate.py`, `ai_runtime/routing.py`, `llm_music_generator.py`, `plugin_host/invoke.py`

- [x] Task 9: Non-model host methods
  - Implement `analyze`, `run_agent`, `export`, and `postprocess` with the postconditions in Part D
  - Analyzer fragment must include `schema_version: plugin.analysis.fragment.v1` plus whatever the plugin returned under `data` after the host checks the composition snapshot is unchanged
  - Agent result rejected unless the returned mapping has `mutates_composition` false. Check that key on the mapping. Do not import `app.ai_agents` or `AgentRunResult` (`backend/app/ai_agents/schemas.py` is the field this copies, not a dependency)
  - Export rejects oversize bytes with `plugin_output_invalid`
  - Postprocess returns the new dict only
  - Do not call `register_agent`, `analyze_composition`, or the export routes
  - LOGGING: INFO category, plugin id, and success boolean. WARNING on postcondition failure with code. DEBUG byte length for export, not the payload
  - Depends on Tasks 2 and 6
  - Files: `backend/app/plugin_host/invoke.py`

### Phase 4: Discovery HTTP

- [x] Task 10: `GET /plugins`, `GET /plugins/{plugin_id:path}`, `POST /plugins/reload`
  - New `backend/app/routers/plugins.py` included from `main.py` next to `ai_models_router`
  - List and detail DTOs in `backend/app/plugin_host/schemas.py` (or `plugin_sdk` only if they are manifest re-exports; prefer host schemas so SDK stays free of FastAPI)
  - Public fields: id, name, version, api_compatibility, category, capabilities, dependencies (ids only), status, code, model_id when a descriptor exists. Omit `configuration_schema` defaults' values if any default key is secret-like; include the schema itself because it is developer metadata (types and enums, not live secrets)
  - Path id is the manifest id (`sample_symbolic_generator`), not the model id `plugin:sample_symbolic_generator`. Use `{plugin_id:path}` the same way `GET /ai/models/{model_id:path}` does, and `unquote` the segment
  - HTTP errors use `HTTPException(detail={"code": ..., "message": ...})`, matching `backend/app/routers/mix_plan.py` and `neural_audio.py`
  - `POST /plugins/reload` calls `reload_plugins()` then returns the same list. When `PLUGIN_RELOAD_ENABLED` is off, 403 with code `plugin_reload_disabled`. It must not be documented as 404
  - Unknown manifest id on GET → 404 with code `plugin_not_found`
  - Confirm `GET /ai/models?capability=symbolic_composer` includes `plugin:{id}` after reload. Do not change catalog item shape
  - LOGGING: INFO reload requested and result counts. DEBUG query filters. Never log config documents
  - Depends on Tasks 6, 7, and 9
  - Files: `backend/app/routers/plugins.py`, `backend/app/plugin_host/schemas.py`, `main.py`

### Phase 5: Examples

- [x] Task 11: Deterministic analyzer example
  - `backend/examples/plugins/deterministic_analyzer/plugin.manifest.json` and `plugin.py`
  - `register` returns an object implementing `AnalyzerPlugin`
  - `analyze` counts notes in `tracks[].events[]` and track rows; returns the fragment body the host wraps
  - Imports only `app.plugin_sdk` (and stdlib). Include a `configuration_schema` with an integer `min_notes` default `0` so schema-driven config is exercised; if the count is below `min_notes`, still return the counts (do not mutate, do not raise)
  - No network, no files written
  - LOGGING: the plugin uses `ctx.logger` at DEBUG with note_count and track_count only
  - Depends on Tasks 5 and 9
  - Files: `backend/examples/plugins/deterministic_analyzer/`

- [x] Task 12: Sample symbolic generator example
  - `backend/examples/plugins/sample_symbolic_generator/plugin.manifest.json` and `plugin.py`
  - `compose` ignores prompt contents and returns one deterministic `composition.v2` dict. Required shape: `schema_version` `composition.v2`, `tempo` 120, `key` `C major`, `time_signature` `4/4`, `ticks_per_quarter` 480, `duration_ticks` 3840, `bar_count` 2, `harmony` `[]`, one section (`type` `verse`, `start_bar` 1, `bar_count` 2, `start_tick` 0, `duration_ticks` 3840), one track (`id`, `name`, `instrument`, `role` `melody`, `midi_program` 0, `channel` 1, `events` with one note). The note is `type` `note`, pitch `C4` (scientific spelling, not MIDI 60), `start_tick` 0, `duration_ticks` 480, `velocity` 80. `CompositionV2` uses `extra="forbid"`. Do not import fixtures, `app.services`, or `app.composition_schemas`
  - Manifest capabilities `["symbolic_composer"]`, category `symbolic_composer`, id `sample_symbolic_generator`
  - Optional dependency on nothing
  - LOGGING: DEBUG `sample_symbolic_generator compose` with no request body
  - Depends on Task 8
  - Files: `backend/examples/plugins/sample_symbolic_generator/`

### Phase 6: Contract tests

- [x] Task 13: Contract tests
  - `backend/tests/test_plugin_sdk_contracts.py` covering:
    - Manifest accept and reject (missing field, bad semver, bad category, capability not allowed for category)
    - API `2.0.0` → catalog status `incompatible`, process still lists built-in models
    - Config missing required key → `plugin_config_invalid`; valid `config.json` reaches `ctx.config`
    - Secret-like config value absent from `caplog` text
    - Fixture plugin that imports `app.services` → `plugin_forbidden_import` and not active
    - Duplicate id, missing dependency, exact version mismatch, and a two-plugin cycle
    - Symlink escape not loaded
    - Empty `PLUGIN_PATHS` → zero plugins, and `resolve_symbolic_backend` unchanged
    - Example analyzer: composition object equal before and after; counts match a tiny inline composition
    - Example generator: with `PLUGIN_PATHS` pointed at `backend/examples/plugins/sample_symbolic_generator` (or a parent), after `reload_plugins()` plus `reload_registry()`, `list_models(capability=symbolic_composer)` and `GET /ai/models` contain `plugin:sample_symbolic_generator`
    - A second `reload_registry()` does not call the example `register()` again. `reload_registry(descriptors=(...))` does not include plugin models
    - With the example loaded and `AI_OP_GENERATE_COMPOSER` unset, `generate_symbolic_composition` without `model_id` still returns `backend == "fake"`
    - With `AI_OP_GENERATE_COMPOSER=plugin:sample_symbolic_generator` (or an explicit `model_id`), the result is a validated composition whose only note pitch is `C4`, and a second call with a different prompt returns the same notes
    - `GET /plugins/sample_symbolic_generator` returns the manifest id. `GET /plugins/plugin:sample_symbolic_generator` is 404 `plugin_not_found`
    - A plugin that returns `{}` yields `plugin_output_invalid`
    - `GET /plugins` lists both examples when both directories are configured; `POST /plugins/reload` picks up a newly written tmp plugin
    - Agent fixture with `mutates_composition: true` is rejected by the host
    - Export over the byte cap is rejected
  - Use `monkeypatch` for env and `clear_registry_for_tests` / `clear_plugins_for_tests`
  - LOGGING: assertions on `caplog` for one WARNING code and one secret redaction. No new application logs in the test file beyond pytest
  - Depends on Tasks 10, 11, and 12
  - Files: `backend/tests/test_plugin_sdk_contracts.py` (split a second module only if the file exceeds the project's usual test size)

### Phase 7: Documentation

- [x] Task 14: Developer documentation, map, roadmap
  - Add `docs/plugin-sdk.md`: what a plugin is, manifest field table, the eight protocols with signatures, allowed and forbidden imports, configuration schema subset, version rules, `PLUGIN_*` env, install steps (directory + restart or reload), how `plugin:{id}` appears on `GET /ai/models`, how non-model plugins appear on `GET /plugins`, the two examples, and an explicit statement that there is no marketplace and that plugins must not write `DATASET_ROOT` or persist `composition.v4`
  - Cross-link from `docs/ai-runtime.md` (one short subsection, not a rewrite)
  - `.env.example` block with commented `PLUGIN_PATHS` and the caps
  - Update `AGENTS.md` structure tree and key-entry table with `plugin_sdk`, `plugin_host`, `routers/plugins.py`, `docs/plugin-sdk.md`
  - Update `.ai-factory/ARCHITECTURE.md` with one logical-module row for the plugin host
  - Add roadmap milestone **V4 plugin and extension SDK** and check it once Task 13 tests have been added and pass
  - LOGGING: no new runtime logs. Docs must state the logger names and the redaction rule
  - Depends on Task 13
  - Files: `docs/plugin-sdk.md`, `docs/ai-runtime.md`, `.env.example`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, `.ai-factory/ROADMAP.md`

## Commit Plan

- **Commit 1** (after tasks 1–3): `feat: add plugin manifest and SDK protocols`
- **Commit 2** (after tasks 4–6): `feat: load plugins from PLUGIN_PATHS with import guards`
- **Commit 3** (after tasks 7–9): `feat: register plugin models and host category dispatch`
- **Commit 4** (after tasks 10–12): `feat: expose plugin discovery and example plugins`
- **Commit 5** (after tasks 13–14): `test: cover plugin SDK contracts and document the SDK`

Suggested `git commit` commands are for `/aif-implement`. Do not commit during planning.

## Verification

From `backend/` with the example path exported only inside tests (the suite sets env itself):

```bash
python -m pytest tests/test_plugin_sdk_contracts.py tests/test_ai_runtime_registry.py -q
```

Full gate when the docs task lands: `scripts/run_tests.sh --backend-only`.

Manual check the acceptance path once from `backend/` (the process must see `app`):

```bash
PLUGIN_PATHS=examples/plugins/sample_symbolic_generator python -c "from app.plugin_host.bridge import reload_plugins; from app.ai_runtime.registry import reload_registry, list_models; from app.ai_runtime.capabilities import ModelCapability; reload_plugins(); reload_registry(); ids=[m.id for m in list_models(capability=ModelCapability.SYMBOLIC_COMPOSER)]; assert 'plugin:sample_symbolic_generator' in ids, ids"
```

Then run the same snippet with `PLUGIN_PATHS` unset and confirm that id is absent. A second `reload_registry()` in the first process must not be required for the id to remain.

Frontend: no UI in this plan. Do not block on a browser pass. If a later change adds a visible catalog, verify that screen before calling it done.
