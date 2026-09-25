# Plugin and extension SDK

A plugin is a directory outside the core call graph. It contains `plugin.manifest.json` and a Python entry such as `plugin.py`. The host loads that directory only when `PLUGIN_PATHS` points at it, at process startup or `POST /plugins/reload`.

There is no marketplace, no remote install, and the host does not run `pip`. Plugins must not write `DATASET_ROOT`, must not open `PROJECT_DB_PATH`, and must not persist a `composition.v4` score. Playable notes stay on `composition.v2` `tracks[].events[]`. The host validates symbolic-composer output before it can become a project.

Shipped examples under `backend/examples/plugins/` are not loaded until their directory is on `PLUGIN_PATHS`.

## Install

1. Write a plugin directory that imports only `app.plugin_sdk` (plus the standard library and third-party packages).
2. Set `PLUGIN_PATHS` to that directory, or to a parent whose immediate children are plugin directories. The separator is the platform path separator.
3. Restart the API, or `POST /plugins/reload` while `PLUGIN_RELOAD_ENABLED` is on.

A bad plugin is skipped. The process stays up and built-in models stay registered.

## Manifest (`plugin.manifest.v1`)

| Field | Rule |
|-------|------|
| `schema_version` | `plugin.manifest.v1` |
| `id` | `^[a-z][a-z0-9_]{1,63}$` |
| `name` | 1–80 characters |
| `version` | `MAJOR.MINOR.PATCH` |
| `api_compatibility` | Major `1` only: `1`, `1.0`, or `1.0.0`. Compared with `PLUGIN_API_VERSION` (`1.0.0`) |
| `category` | One of the eight names below |
| `capabilities` | Non-empty. Each value must be allowed for that category |
| `dependencies` | `[{ "id", "version"? }]`. `version` is an exact match when present. Activation order only |
| `configuration_schema` | Object schema subset below, or omitted |
| `entry` | `module:attr`, default `plugin:register`. The module is a file stem in the plugin directory, with no dots |

Model categories register as `plugin:{id}` on `GET /ai/models` with runtime `plugin`. Analyzer, music agent, export format, and postprocess plugins appear only on `GET /plugins`.

| Category | Capability | Discovery |
|----------|------------|-----------|
| `language_model` | `language_planner` | `GET /ai/models` |
| `symbolic_composer` | `symbolic_composer` | `GET /ai/models` and the generate-composer seam when that model id is selected |
| `transcription_model` | `audio_transcription` | `GET /ai/models`. Host `transcribe` only |
| `neural_renderer` | `audio_generation` | `GET /ai/models`. Host `render` returns metadata only in v1 |
| `analyzer` | `analyzer` | `GET /plugins` |
| `music_agent` | `music_agent` | `GET /plugins`. Not added to the agent spine |
| `export_format` | `export_format` | `GET /plugins`. Not wired to the export download buttons |
| `postprocess` | `postprocess` | `GET /plugins` |

`GET /plugins/{id}` uses the manifest id (`sample_symbolic_generator`). `plugin:sample_symbolic_generator` is a model id and is not a plugin path id.

## Protocols

All methods are synchronous. The host passes a `PluginContext` (`api_version`, `plugin_id`, `config`, `logger`).

| Protocol | Method |
|----------|--------|
| `LanguageModelPlugin` | `complete_text(ctx, prompt: str) -> str` |
| `SymbolicComposerPlugin` | `compose(ctx, request: Mapping) -> Mapping` |
| `MusicAgentPlugin` | `run(ctx, request: Mapping) -> Mapping` |
| `AnalyzerPlugin` | `analyze(ctx, composition: Mapping) -> Mapping` |
| `TranscriptionModelPlugin` | `transcribe(ctx, audio_ref: Mapping) -> Mapping` |
| `NeuralRendererPlugin` | `render(ctx, spec: Mapping) -> Mapping` |
| `ExportFormatPlugin` | `export(ctx, composition: Mapping) -> tuple[bytes, str]` |
| `PostprocessPlugin` | `process(ctx, document: Mapping) -> Mapping` |

`register(ctx)` returns an instance that implements the method for the manifest category.

The symbolic-composer host deep-copies the mapping, parses `composition.v2` (`extra="forbid"`), and runs integrity validation with profile `canonical`. Notes come from `tracks[].events[]`. Invalid output raises `plugin_output_invalid` and does not write a project.

Analyzer results are wrapped as `plugin.analysis.fragment.v1` with the plugin body under `data`. The composition object must stay unchanged. Agent results must include `mutates_composition: false`.

A loaded plugin is not the implicit `generate_composer` default. Set `AI_OP_GENERATE_COMPOSER=plugin:{id}` (or pass that model id) to select it.

## Imports

Allowed: the standard library, third-party packages, and `app.plugin_sdk`.

Forbidden: `app.services`, `app.ai_runtime`, `app.ai_agents`, `app.db`, `app.routers`, `app.main`, `app.plugin_host`, and any other `app.*` module. Relative imports that leave the plugin directory are rejected. A plugin must not import another plugin's private module.

The host AST-scans `.py` files before execution and installs an import hook while `register` and protocol methods run. A violation sets status `rejected` and code `plugin_forbidden_import`.

## Configuration

`configuration_schema` is a JSON Schema subset:

- Root: `type: object`, `additionalProperties: false`, `properties`, `required`
- Property keywords: `type` (`string`, `number`, `integer`, `boolean`), `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `default`
- No `$ref` and no nested objects

`config.json` sits next to the manifest. A missing schema and a missing file are valid. A required key with no file or no value is `plugin_config_invalid`. Config is not stored in SQLite.

Property names matching `(?i)(secret|password|token|api_key|apikey|authorization)` have their defaults removed from `GET /plugins`. Config values are never logged.

## Version and dependencies

`PLUGIN_API_VERSION` is `1.0.0`. Any other API major is status `incompatible` / `plugin_api_incompatible` and is not activated. Dependency versions are exact strings when set. A missing dependency is `plugin_dependency_missing`. Every id in a cycle gets `plugin_dependency_cycle`. Load order is a stable topological sort, with the manifest id as the tiebreak.

## Environment

| Variable | Default | Role |
|----------|---------|------|
| `PLUGIN_PATHS` | empty (plugins off) | Path-separator list of plugin directories or parents |
| `PLUGIN_MAX_COUNT` | `32` | Extra directories are `plugin_limit_exceeded` |
| `PLUGIN_MAX_MANIFEST_BYTES` | `65536` | Manifest size cap |
| `PLUGIN_MAX_EXPORT_BYTES` | `2000000` | Export byte cap |
| `PLUGIN_RELOAD_ENABLED` | `1` | Gates `POST /plugins/reload` only. Off returns 403 `plugin_reload_disabled` |

Invalid integers log the variable name and fall back to the default. Booleans accept `1|true|yes|on` and `0|false|no|off`.

A symlink whose target is outside the configured path entry is not loaded.

## HTTP

- `GET /plugins` and `GET /plugins/{plugin_id}` — public manifest fields, status, code, and `model_id` when a descriptor exists
- `POST /plugins/reload` — rescan and the same list
- `GET /ai/models?capability=symbolic_composer` — includes `plugin:{id}` after reload

There is no `POST /plugins/{id}/invoke`.

## Logging

| Logger | When |
|--------|------|
| `app.plugin_sdk.manifest` | Parse start (basename only) and validation warnings with `code` and plugin id |
| `app.plugin_sdk.config` | Config validation warnings with `code` and plugin id. Values and schema defaults are not logged |
| `app.plugin_sdk.plugin.{id}` | The `PluginContext.logger` wrapper |
| `app.plugin_host.*` | Load, skip, reload, and call success or postcondition failure |

`PluginContext.logger` forwards debug, info, warning, and error. It drops keyword arguments named `config`, `prompt`, `composition`, `events`, and `audio`. Mapping keys that match the secret-name pattern are replaced with `"[redacted]"`, and one debug line records the key names. Levels follow `LOG_LEVEL`.

## Examples

| Directory | Behavior |
|-----------|----------|
| `backend/examples/plugins/deterministic_analyzer` | Returns note count and track count. Does not mutate the composition. Optional integer `min_notes` (default `0`) |
| `backend/examples/plugins/sample_symbolic_generator` | Returns one deterministic 2-bar C-major `composition.v2` with a single `C4` note. The host validates it |

```bash
cd backend
PLUGIN_PATHS=examples/plugins/sample_symbolic_generator python -c "from app.plugin_host.bridge import reload_plugins; from app.ai_runtime.registry import reload_registry, list_models; from app.ai_runtime.capabilities import ModelCapability; reload_plugins(); reload_registry(); ids=[m.id for m in list_models(capability=ModelCapability.SYMBOLIC_COMPOSER)]; assert 'plugin:sample_symbolic_generator' in ids, ids"
```

## Failure codes

| Condition | Status | Code |
|-----------|--------|------|
| Manifest invalid | `rejected` | `plugin_manifest_invalid` |
| API major mismatch | `incompatible` | `plugin_api_incompatible` |
| Duplicate id (first wins) | `rejected` | `plugin_duplicate_id` |
| Dependency missing or version mismatch | `unsatisfied` | `plugin_dependency_missing` |
| Cycle | `unsatisfied` | `plugin_dependency_cycle` |
| Config invalid | `rejected` | `plugin_config_invalid` |
| Forbidden import | `rejected` | `plugin_forbidden_import` |
| Entry missing or not callable | `rejected` | `plugin_entry_invalid` |
| `register()` raised | `rejected` | `plugin_register_failed` |
| Output failed host checks | stays `active` | `plugin_output_invalid` |
| Over the count cap | `not_loaded` | `plugin_limit_exceeded` |
| Reload disabled | HTTP 403 | `plugin_reload_disabled` |
| Unknown id | HTTP 404 | `plugin_not_found` |

## See also

- [AI runtime](ai-runtime.md) — capability registry and `runtime=plugin` rows
- [Hybrid generation](hybrid-generation.md) — `AI_OP_GENERATE_COMPOSER`
- [Multi-agent](multi-agent.md) — plugin agents are not part of the spine
