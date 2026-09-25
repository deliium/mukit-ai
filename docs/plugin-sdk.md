# Plugin and extension SDK

A plugin is a directory outside the core call graph. It contains `plugin.manifest.json` and a Python entry such as `plugin.py`. Putting that directory on `PLUGIN_PATHS` only **discovers** the manifest. The API process does not import the plugin until a user installs it and then enables it.

There is no marketplace, no remote install, and the host does not run `pip`. Plugins must not write `DATASET_ROOT`, must not open `PROJECT_DB_PATH`, and must not persist a `composition.v4` score. Playable notes stay on `composition.v2` `tracks[].events[]`. The host validates symbolic-composer output before it can become a project.

Shipped examples under `backend/examples/plugins/` stay undiscovered until their directory is on `PLUGIN_PATHS`. Even then they stay `discovered` until install and enable.

## Lifecycle

| Status | Meaning |
|--------|---------|
| `discovered` | Manifest parsed. No installation row. No import |
| `installed` | User called `POST /plugins/{id}/install` for this exact version. Code is still not loaded |
| `enabled` | Desired state `enabled`, version and API checks passed, `register()` succeeded |
| `disabled` | Installation row exists and desired state is `disabled`. The module is dropped |
| `incompatible` | `api_compatibility` major is not `1`. Never imported |
| `failed` | Invalid manifest, duplicate id, version mismatch, isolation refusal, entry/import/register failure, dependency failure during enable, or over cap |

Desired state (`installed`, `enabled`, or `disabled`) is stored in SQLite table `plugin_installations` inside `PROJECT_DB_PATH`. It survives restart. Plugin directories stay read-only. `POST /plugins/reload` rescans manifests and reapplies that desired state. It does not install newly discovered ids.

A failed or incompatible optional plugin does not stop AI Composer from starting or opening projects. `/ready` stays successful when every plugin is `failed`.

INFO logs for a transition include `plugin_id`, `from_state`, `to_state`, and `code`. Config values and stack traces stay out of INFO. Stack traces are DEBUG only.

## Resources

Optional manifest field `resources`, default `[]`. A declaration, not a grant of operating-system rights. The closed set is:

| Resource | What a future isolated runner would be allowed to receive |
|----------|-------------------------------------------------------------|
| `filesystem_model_dir` | One host-chosen model directory, not an arbitrary path |
| `network` | Outbound network |
| `gpu` | Accelerator device |
| `project_read` | Read of a project composition snapshot |
| `project_write` | Write of a project |
| `audio_processing` | PCM bytes |

Any value outside that set is `plugin_manifest_invalid` at discovery. An unknown string is not imported.

Empty `resources` plus explicit enable is the only in-process execution path. `PluginContext` still receives `api_version`, `plugin_id`, `config`, and a redacting `logger`. It does not receive a project path, dataset root, API key, or socket.

Any non-empty `resources` on enable returns HTTP 409 `plugin_isolation_unavailable`, does not change the installation row, and does not import the module. There is no request field that grants a resource. A grant the process cannot enforce would be a silent privilege.

## Isolation

| Approach | What it actually stops | This milestone |
|----------|------------------------|----------------|
| Import allowlist | Forbidden `app.*` imports | Shipped. It does not stop `socket`, `open`, or `sys.exit` |
| In-process permission object | Honest plugins only | Not used as an enforcement story |
| Subprocess with scrubbed env and JSON stdio | Most accidental and many malicious uses of host memory, `PROJECT_DB_PATH`, and API keys | A later plan |
| One container per plugin | Stronger filesystem and network policy | A later plan |
| Refuse plugins that declare a high-risk resource | The API process never executes that code | Implemented |

High-risk means the plugin **declares** one of the resource strings above. It is not a hard-coded category list. A `neural_renderer` with `resources: []` may be enabled in-process and still returns metadata only. A `neural_renderer` with `gpu` or `filesystem_model_dir` is not imported.

`SystemExit` during `register()` sets lifecycle `failed` / `plugin_register_failed` and drops the module. `SystemExit` during invoke becomes `plugin_output_invalid`, marks health `unhealthy`, and leaves lifecycle `enabled`. `KeyboardInterrupt` is not swallowed. One plugin's failure does not remove built-in models.

## Health

Every public plugin object includes `health`:

| Field | Values |
|-------|--------|
| `status` | `unknown` (discovered, installed, disabled), `healthy` (enabled and no invoke failures since the last success), `unhealthy` (incompatible, failed, or an invoke failure since the last success) |
| `code` | Public `plugin_*` code or null. Never a traceback |
| `invocation_failure_count` | In-memory only. Reset on successful enable and on a successful invoke. Not written to SQLite |

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
| `dependencies` | `[{ "id", "version"? }]`. `version` is an exact match when present. Checked at enable, not at discovery |
| `resources` | Optional. Default `[]`. Closed enum in Resources. Any other string is `plugin_manifest_invalid` |
| `configuration_schema` | Object schema subset below, or omitted. Secret-shaped property names are `plugin_manifest_invalid` |
| `entry` | `module:attr`, default `plugin:register`. The module is a file stem in the plugin directory, with no dots |

Model categories register as `plugin:{id}` on `GET /ai/models` with runtime `plugin` only while lifecycle is `enabled`. Analyzer, music agent, export format, and postprocess plugins appear only on `GET /plugins`.

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

The host AST-scans `.py` files before execution and installs an import hook while `register` and protocol methods run. A violation sets lifecycle `failed` and code `plugin_forbidden_import`.

## Configuration

`configuration_schema` is a JSON Schema subset:

- Root: `type: object`, `additionalProperties: false`, `properties`, `required`
- Property keywords: `type` (`string`, `number`, `integer`, `boolean`), `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `default`
- No `$ref` and no nested objects

`config.json` sits next to the manifest and stays a read-only package document. The object passed to `register()` is schema defaults, then `config.json` when that file parses, then the SQLite `config_json` user override. A missing or invalid `config.json` does not fail discovery. It fails the enable attempt with lifecycle `failed` and code `plugin_config_invalid` (HTTP 200).

Property names on `configuration_schema` that match `(?i)(secret|password|token|api_key|apikey|authorization)` or forbidden secret field names fail `parse_manifest` with `plugin_manifest_invalid`. `PUT /plugins/{id}/config` stores the user override. A body that fails schema validation is HTTP 422 `plugin_config_invalid`. A body that fails the persistence secret guard is HTTP 422 `persistence_secret_rejected`. Neither writes the row. Saving while `enabled` does not hot-reload; the value applies on the next enable. `GET` does not return config values. Config values are never logged.

## Install

1. Write a plugin directory that imports only `app.plugin_sdk` (plus the standard library and third-party packages).
2. Set `PLUGIN_PATHS` to that directory, or to a parent whose immediate children are plugin directories. The separator is the platform path separator. This step discovers the manifest only.
3. `POST /plugins/{id}/install` records the manifest version. It does not import the module.
4. `POST /plugins/{id}/enable` checks API compatibility, the installed version, empty `resources`, and enabled dependencies, then calls `register()`.

There is no environment flag that skips install. Restart and `POST /plugins/reload` rescan and reapply desired state. They do not install new ids. A bad plugin stays `failed` or `disabled`. The process stays up and built-in models stay registered.

## Version and dependencies

`PLUGIN_API_VERSION` is `1.0.0`. Any other API major is status `incompatible` / `plugin_api_incompatible` and is not imported, even when desired state is `enabled`. Enable and startup reconcile compare `dependency.version` to the other manifest version only for plugins whose lifecycle is already `enabled`. A missing dependency is HTTP 409 `plugin_dependency_missing` and does not change desired state. A cycle is HTTP 409 `plugin_dependency_cycle`. Startup reconcile of an already-enabled row marks that plugin `failed` and does not disable its peers.

## Environment

| Variable | Default | Role |
|----------|---------|------|
| `PLUGIN_PATHS` | empty (plugins off) | Discovery only. Path-separator list of plugin directories or parents. Does not install or import |
| `PLUGIN_MAX_COUNT` | `32` | Extra directories are `plugin_limit_exceeded` |
| `PLUGIN_MAX_MANIFEST_BYTES` | `65536` | Manifest size cap |
| `PLUGIN_MAX_EXPORT_BYTES` | `2000000` | Export byte cap |
| `PLUGIN_RELOAD_ENABLED` | `1` | Gates `POST /plugins/reload` only. Off returns 403 `plugin_reload_disabled` |

Invalid integers log the variable name and fall back to the default. Booleans accept `1|true|yes|on` and `0|false|no|off`.

A symlink whose target is outside the configured path entry is not loaded.

## HTTP

- `GET /plugins` and `GET /plugins/{plugin_id}` — lifecycle, health, resources, installed version, and `config_present`. No config object, roots, or stack traces. `model_id` is set only for an enabled model category
- `POST /plugins/{id}/install` — desired `installed` for the manifest version. No import. Same version leaves desired state unchanged. A new version sets desired `installed`
- `POST /plugins/{id}/enable` — gates then `register()`. HTTP 409 leaves the row unchanged (`plugin_not_installed`, `plugin_api_incompatible`, `plugin_version_mismatch`, `plugin_isolation_unavailable`, `plugin_dependency_missing`, `plugin_dependency_cycle`). A real enable attempt that fails config validation or `register()` is HTTP 200 with lifecycle `failed`
- `POST /plugins/{id}/disable` — desired `disabled`, module dropped. HTTP 409 `plugin_not_installed` when no row exists
- `PUT /plugins/{id}/config` — user override. HTTP 422 `plugin_config_invalid` or `persistence_secret_rejected`. HTTP 409 `plugin_not_installed`
- `POST /plugins/reload` — rescan and reapply desired state. HTTP 403 `plugin_reload_disabled` when reload is off
- `GET /ai/models?capability=symbolic_composer` — includes `plugin:{id}` only after enable

There is no `POST /plugins/{id}/invoke` and no download route.

## Logging

| Logger | When |
|--------|------|
| `app.plugin_sdk.manifest` | Parse start (basename only) and validation warnings with `code` and plugin id |
| `app.plugin_sdk.config` | Config validation warnings with `code` and plugin id. Values and schema defaults are not logged |
| `app.plugin_sdk.plugin.{id}` | The `PluginContext.logger` wrapper |
| `app.plugin_host.*` | Discovery, activate start/finish, skip, reload, and call success or postcondition failure |
| `app.services.plugin_lifecycle` | INFO transition (`plugin_id`, `from_state`, `to_state`, `code`). WARNING on isolation refusal, version mismatch, and startup reconcile failure. Config values stay out of INFO. Tracebacks are DEBUG |
| `app.services.plugin_installation_store` | INFO upsert with plugin id, desired state, and version. No `config_json` |

`PluginContext.logger` forwards debug, info, warning, and error. It drops keyword arguments named `config`, `prompt`, `composition`, `events`, and `audio`. Mapping keys that match the secret-name pattern are replaced with `"[redacted]"`, and one debug line records the key names. Levels follow `LOG_LEVEL`.

## Examples

| Directory | Behavior |
|-----------|----------|
| `backend/examples/plugins/deterministic_analyzer` | Returns note count and track count. Does not mutate the composition. Optional integer `min_notes` (default `0`) |
| `backend/examples/plugins/sample_symbolic_generator` | Returns one deterministic 2-bar C-major `composition.v2` with a single `C4` note. The host validates it |

```bash
cd backend
# Discovery only. install + enable are required before plugin:sample_symbolic_generator exists.
PLUGIN_PATHS=examples/plugins/sample_symbolic_generator python -c "from app.plugin_host.bridge import reload_plugins; reload_plugins(); from app.plugin_host.catalog import get_record; rec=get_record('sample_symbolic_generator'); assert rec.status=='discovered', rec.status"
```

## Failure codes

| Condition | Status | Code |
|-----------|--------|------|
| Manifest invalid, unknown resource, or secret-shaped config name | `failed` | `plugin_manifest_invalid` |
| API major mismatch | `incompatible` | `plugin_api_incompatible` |
| Duplicate id (first confined root wins) | `failed` | `plugin_duplicate_id` |
| Installed version differs from the manifest | `failed` | `plugin_version_mismatch` |
| Enable before install, or disable with no row | HTTP 409 | `plugin_not_installed` |
| Non-empty `resources` on enable | HTTP 409; row unchanged | `plugin_isolation_unavailable` |
| Dependency missing or version pin mismatch at enable | HTTP 409; row unchanged | `plugin_dependency_missing` |
| Dependency cycle at enable | HTTP 409; row unchanged | `plugin_dependency_cycle` |
| Config invalid on a real enable attempt | `failed` (HTTP 200) | `plugin_config_invalid` |
| Config contains a secret field or value | HTTP 422; row unchanged | `persistence_secret_rejected` |
| Forbidden import | `failed` | `plugin_forbidden_import` |
| Entry missing or not callable | `failed` | `plugin_entry_invalid` |
| `register()` raised, including `SystemExit` | `failed` | `plugin_register_failed` |
| Invoke failed host checks, including `SystemExit` | stays `enabled`; health `unhealthy` | `plugin_output_invalid` |
| Invoke while not enabled | HTTP error from the host call | `plugin_not_enabled` |
| Over the count cap | `failed` | `plugin_limit_exceeded` |
| Reload disabled | HTTP 403 | `plugin_reload_disabled` |
| Unknown id | HTTP 404 | `plugin_not_found` |

## See also

- [AI runtime](ai-runtime.md) — capability registry and `runtime=plugin` rows
- [Hybrid generation](hybrid-generation.md) — `AI_OP_GENERATE_COMPOSER`
- [Multi-agent](multi-agent.md) — plugin agents are not part of the spine
