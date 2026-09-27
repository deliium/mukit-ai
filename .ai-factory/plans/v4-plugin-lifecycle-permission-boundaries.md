# Implementation Plan: Secure Plugin Installation, Lifecycle, and Permission Boundaries

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-25
Planning depth: ultra

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra
- Refined: 2026-09-25 (`/aif-improve`, all findings applied)
- Scope: explicit install / enable / disable for the shipped in-process plugin SDK, durable desired state, manifest resource declarations, a hard refusal to run high-risk plugins inside the API process, health reporting, and a Plugins panel. No marketplace
- Parent (reuse, do not fork): `.ai-factory/plans/v4-plugin-extension-sdk.md` — shipped `plugin.manifest.v1`, `app.plugin_sdk`, `app.plugin_host`, `GET /plugins`, `POST /plugins/reload`, `runtime=plugin`, import allowlist, example plugins. This plan changes the meaning of install and the public `status` strings. It keeps protocols, model id `plugin:{manifest_id}`, host postconditions, and the rule that plugins are not spliced into `KNOWN_AGENT_IDS` or analysis stages

## Roadmap Linkage
Milestone: "Secure plugin lifecycle and permission boundaries"
Rationale: The V4 plugin SDK milestone is already checked, and every current roadmap milestone is complete. Presence on `PLUGIN_PATHS` still executes plugin code at startup, so a new unchecked milestone is required. The docs task adds that milestone; check it only after the security tests pass.

## Goal

A directory on `PLUGIN_PATHS` is only **discovered**. The API process stays up, projects still open, and no plugin module is imported until a user installs and then enables that plugin. Enable checks API compatibility and the exact installed version first. A plugin that declares a high-risk resource is not loaded. A plugin that fails is recorded as `failed` and does not take down AI Composer.

```text
PLUGIN_PATHS
    │  JSON manifest only (no import)
    ▼
discovered ──install──► installed ──enable──► enabled
    │                        │                    │
    │                        └──disable──────────► disabled
    │
    ├── api major mismatch ──► incompatible (no import, cannot enable)
    └── bad manifest / version mismatch / register error
        / undeclared-resource refusal / isolation refusal ──► failed

enabled plugins only
    → runtime=plugin descriptor on GET /ai/models (model categories)
    → PluginHost invoke (existing postconditions)
```

**Acceptance one-liner:** A broken or incompatible optional plugin can fail or stay disabled without preventing AI Composer from starting or opening projects.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Discovered** | Manifest parsed from a confined directory on `PLUGIN_PATHS`. No installation row. No import |
| **Installed** | User acknowledged this id and exact `version` via `POST /plugins/{id}/install`. Code is still not loaded |
| **Enabled** | Desired state `enabled`, version and API checks passed, `register()` succeeded. This replaces public status `active` |
| **Disabled** | Installation row exists and desired state is `disabled`. Module dropped. No invoke |
| **Incompatible** | `api_compatibility` major is not `1`. Derived at discovery. Never imported |
| **Failed** | A public failure: invalid manifest, duplicate id, version mismatch, isolation refusal, entry/import/register failure, dependency failure during enable, or over cap. Code is the existing `plugin_*` string when one already exists |
| **Health** | Separate from lifecycle. `unknown`, `healthy`, or `unhealthy`. Describes the last load or invoke outcome |
| **Resource** | A closed string on the manifest (`resources`). A declaration, not a grant of OS rights |
| **Desired state** | SQLite value `installed`, `enabled`, or `disabled`. Survives restart. The public lifecycle is derived from this plus the live scan |
| **High-risk resource** | Every value in the resource enum for this milestone. The process cannot enforce them, so enable refuses to import |

## Non-goals

- Internet marketplace, remote index, download, ratings, signature checks, or the host running `pip`
- A subprocess, seccomp, or container runner. The isolation section records why those would be required, then refuses high-risk plugins instead of pretending the import hook is a sandbox
- A `PLUGIN_TRUST_PATHS` (or similar) switch that restores silent activation
- Rewriting symbolic-composer, analyzer, transcription, neural-render, or export orchestrators
- New `AiOperation` values, `composition.v4`, or `DATASET_ROOT` writes
- Letting plugins open `PROJECT_DB_PATH`, receive API keys, or choose filesystem paths
- Auto-disable after a single bad invoke (`plugin_output_invalid` still fails the call and leaves lifecycle `enabled`)
- Persisting PCM, note arrays, prompts, or config values in logs or project history

## Approach Evaluation (locked)

### Part A — Where lifecycle state lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. In-memory only** | Matches today's catalog | Restart of a `PLUGIN_PATHS` directory reactivates everything | **Reject** |
| **B. Host writes state into the plugin directory** | Survives restart | Plugin trees must be writable; host would write into untrusted folders | **Reject** |
| **C. SQLite `plugin_installations` in `PROJECT_DB_PATH`** | Same durability as the rest of the app; plugin dirs stay read-only | One migration | **Accepted** |

**Locked:** Table owned by `backend/app/services/plugin_installation_store.py` using `app.db.connection.get_connection` / `get_project_db_path`, same style as `composer_profile_store.py`. `plugin_host` does not import `services` or `db`. `backend/app/services/plugin_lifecycle.py` is the orchestrator the router and lifespan call. Alembic revision `20260925_0010`, `down_revision = "20260925_0009"`.

```sql
CREATE TABLE IF NOT EXISTS plugin_installations (
    plugin_id TEXT PRIMARY KEY,
    installed_version TEXT NOT NULL,
    desired_state TEXT NOT NULL CHECK (desired_state IN ('installed', 'enabled', 'disabled')),
    config_json TEXT NOT NULL DEFAULT '{}',
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
```

`config_json` is the user override object. Before insert, call both `assert_no_secret_fields` and `assert_no_secret_values` from `app.services.persistence_secret_guard`. A hit raises `PersistenceSecretError` with code `persistence_secret_rejected`; the HTTP layer returns 422 with that code and logs the code only. Do not log field values or the payload. Do not store event arrays. This milestone never deletes installation rows. A plugin directory that disappears from `PLUGIN_PATHS` is omitted from `GET /plugins`. When that id is scanned again, the existing row applies.

### Part B — Public status versus the shipped catalog

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep `active` / `rejected` / `unsatisfied` and add a second field** | Old tests keep passing untouched | Two conflicting stories in `GET /plugins` | **Reject** |
| **B. Replace public `status` with the six lifecycle states** | Matches the requirement; one field in the UI | Contract tests and docs that say `active` must be updated in this plan | **Accepted** |

**Locked:** `PluginPublic.status` is only `discovered | installed | enabled | disabled | incompatible | failed`. Internal `prepared` never leaves `loader.py`. Model descriptors still use registry `health.status="ready"` and exist only for lifecycle `enabled`.

Derivation function lives in `plugin_lifecycle.py` and is unit-tested without importing plugin code:

| Condition (first match wins) | Lifecycle | Code | Import? |
|-------------------------------|-----------|------|---------|
| Manifest JSON/schema invalid, unknown resource, over cap | `failed` | existing `plugin_manifest_invalid` or `plugin_limit_exceeded` | No |
| Duplicate id (first confined root wins) | `failed` | `plugin_duplicate_id` | No |
| API major is not 1 | `incompatible` | `plugin_api_incompatible` | No |
| No installation row | `discovered` | `null` | No |
| Row version ≠ manifest `version` | `failed` | `plugin_version_mismatch` | No |
| Desired `disabled` | `disabled` | `null` | No |
| Desired `installed` | `installed` | `null` | No |
| Desired `enabled` and `resources` non-empty | `failed` | `plugin_isolation_unavailable` | No |
| Desired `enabled`, dependency not `enabled` | `failed` | `plugin_dependency_missing` or `plugin_dependency_cycle` | No |
| Desired `enabled`, merged config invalid | `failed` | `plugin_config_invalid` | No |
| Desired `enabled`, `register()` raised or entry/import guard failed | `failed` | existing `plugin_entry_invalid`, `plugin_forbidden_import`, `plugin_register_failed` | Attempted, then module dropped |
| Desired `enabled`, `register()` returned a callable category method | `enabled` | `null` | Yes |

`last_error_code` is cleared only after a successful enable. Startup reconcile applies a desired `enabled` row once per process. A failed attempt stays `failed` and the process continues.

A 409 (`plugin_not_installed`, `plugin_api_incompatible`, `plugin_version_mismatch`, `plugin_isolation_unavailable`, `plugin_dependency_missing`, `plugin_dependency_cycle`) does not change `desired_state`. Persist `desired_state=enabled` plus `last_error_code` only after a real enable attempt: merged-config validation or `register()`. Discovery does not mark a plugin `failed` for a bad `config.json` or for a dependency that is not enabled yet. Those stay `discovered` or `installed` until enable or until reconcile sees an already-enabled row.

### Part C — What “install” means now

The shipped SDK defined install as “put the directory on `PLUGIN_PATHS` and restart.” That definition is retired.

| Step | Who | Effect |
|------|-----|--------|
| Copy the directory and set `PLUGIN_PATHS` | Operator / developer | Discovery only |
| `POST /plugins/{id}/install` | Explicit user action | Writes `desired_state=installed` and `installed_version` from the manifest. No import |
| `POST /plugins/{id}/enable` | Explicit user action | Gates below, then the existing `_activate` path |
| `POST /plugins/{id}/disable` | Explicit user action | `desired_state=disabled`, drop `mukit_plugin_{id}` from `sys.modules`, clear the instance |
| `POST /plugins/reload` | Operator | Rescan manifests. Re-apply desired state. Does not install newly discovered ids |

Install of an `incompatible` or `failed` manifest returns 409 with that code and does not write a row. Install when a row already exists with the **same** version returns 200 and leaves desired state unchanged. Install when the manifest version differs updates `installed_version`, sets desired state to `installed` (not `enabled`), clears `last_error_code`, and does not import. That is the explicit accept of a new package version.

There is no env flag that skips install.

### Part D — Resources and silent access

Existing `capabilities` stay the model/catalog capability strings. Add optional manifest field `resources`, default `[]`, so `backend/examples/plugins/*` manifests remain valid.

Closed enum:

| Resource | What a future isolated runner would be allowed to receive |
|----------|-------------------------------------------------------------|
| `filesystem_model_dir` | One host-chosen model directory, not an arbitrary path |
| `network` | Outbound network |
| `gpu` | Accelerator device |
| `project_read` | Read of a project composition snapshot |
| `project_write` | Write of a project |
| `audio_processing` | PCM bytes |

**Locked enforcement in this milestone:**

- Empty `resources` plus explicit enable is the only in-process execution path. `PluginContext` gains no new path, database, key, or socket handle.
- Any non-empty `resources` on enable returns 409 `plugin_isolation_unavailable`, does not change the installation row, and does not import the module. The UI shows the declaration and the refusal. The user cannot tick a grant that the process would then ignore. If desired state is already `enabled` and a later scan sees non-empty `resources`, reconcile sets lifecycle `failed` with that code and skips import.
- Manifests cannot declare a resource outside the enum (`plugin_manifest_invalid`).
- Do not add a request field for grants. A grant that cannot be enforced is a silent privilege.

### Part E — Process and container isolation

| Approach | What it actually stops | Cost | Verdict |
|----------|------------------------|------|---------|
| **A. Import allowlist only** | Forbidden `app.*` imports | Already shipped. Does not stop `socket`, `open`, or `sys.exit` | Necessary and insufficient for declared resources |
| **B. In-process “permission” object the plugin can ignore** | Honest plugins | A malicious plugin imports `os` anyway | **Reject** as an enforcement story |
| **C. Subprocess with scrubbed env and JSON stdio** | Most accidental and many malicious uses of host memory, `PROJECT_DB_PATH`, and API keys | New protocol, lifecycle, and crash supervision | Right design for a later plan. Out of scope here |
| **D. One container per plugin** | Stronger filesystem and network policy | Compose profile, image build, GPU device mapping | Also a later plan. Out of scope here |
| **E. Refuse to load any plugin that declares a high-risk resource** | The API process never executes that code | Plugins that need GPU, network, or project I/O cannot be enabled yet | **Accepted** for this milestone |

**Locked:** Document A–D in `docs/plugin-sdk.md` under Isolation. Implement E. Categories with empty `resources` (the two examples) stay in-process after enable. High-risk categories are the plugins that **declare** those resources, not a hard-coded category list: a `neural_renderer` with `resources: []` may be enabled in-process and still returns metadata only, as the SDK plan already requires. A `neural_renderer` with `gpu` or `filesystem_model_dir` is not imported.

### Part F — Failure isolation

`_activate` and `_invoke` today catch `Exception` only. `SystemExit` is a `BaseException`, so both functions must catch `SystemExit` explicitly. `KeyboardInterrupt` is not swallowed. On register, `SystemExit` sets lifecycle `failed` with `plugin_register_failed` and drops the module from `sys.modules`. On invoke, `SystemExit` becomes `plugin_output_invalid`, sets health `unhealthy`, and leaves lifecycle `enabled`. One plugin’s failure does not skip later ids and does not remove built-in models. Disabling one plugin does not set any other plugin’s `desired_state` to `disabled`. On reconcile, a desired-enabled plugin whose dependency is not `enabled` becomes `failed` and is not imported.

`main.lifespan` calls the lifecycle reconcile (not bare `reload_plugins`) inside the existing try/except so a store or scan bug still lets the process serve. `/ready` and project open stay successful when every plugin is `failed`. Optional plugins must not flip readiness to not-ready.

Invoke path: keep host postconditions. An invoke `Exception` becomes `plugin_output_invalid`, increments the in-memory failure count, sets health `unhealthy`, and leaves lifecycle `enabled`. `SystemExit` during invoke is caught the same way and does **not** exit the process. `ImportError` for a forbidden module still rejects that plugin (lifecycle `failed`, code `plugin_forbidden_import`). Invoke of a non-`enabled` plugin raises `plugin_not_enabled` and does not call the instance.

### Part G — Health

`PluginHealthV1` on every public plugin object:

| Field | Values |
|-------|--------|
| `status` | `unknown` (discovered, installed, disabled), `healthy` (enabled and zero invoke failures since last success), `unhealthy` (incompatible, failed, or an invoke failure since the last success) |
| `code` | Public `plugin_*` code or null. Never a traceback |
| `invocation_failure_count` | In-memory integer on `PluginRecord`, copied by `public_plugin`. Reset on successful enable and on a successful invoke. Not written to SQLite |

`health_status` and `invocation_failure_count` live on `PluginRecord`. The host updates them under the catalog lock via `update_record`. `plugin_host` does not import the lifecycle service to record health.

`GET /plugins` and `GET /plugins/{id}` include `status`, `health`, `resources`, `installed_version` (null when discovered), and `config_present`. They do not include config values, roots, or stack traces. `configuration_schema` stays, with secret-like defaults stripped as today.

### Part H — Configuration

Property names on `configuration_schema` that match `(?i)(secret|password|token|api_key|apikey|authorization)` or `FORBIDDEN_SECRET_FIELD_NAMES` fail `parse_manifest` with `plugin_manifest_invalid`. The package cannot declare a secret-shaped key.

Package `config.json` remains a read-only document next to the manifest. The lifecycle service builds the object passed to `activate_plugin(plugin_id, config)` in this order: schema defaults, then `config.json` when that file parses, then the SQLite `config_json` override. `plugin_host` does not read SQLite. The host does not write the plugin directory.

`validate_config` runs on that merged object at enable time, before import. An invalid merge persists `desired_state=enabled` and `last_error_code=plugin_config_invalid`, returns HTTP 200 with lifecycle `failed`, and does not import. A missing or invalid `config.json` does not by itself change discovery: the plugin stays `discovered` or `installed` until that enable attempt.

`PUT` of a body that fails `validate_config` returns 422 `plugin_config_invalid` and does not change the row. `PUT` that fails either persistence guard returns 422 `persistence_secret_rejected` and does not change the row. Config can be saved in `installed`, `disabled`, or `enabled` state. Saving while `enabled` does not hot-reload the instance; the response tells the client the value applies on the next enable (disable + enable). Do not log values. `GET` does not return the object.

### Part I — UI

A Plugins tab on `ComposerWorkspace`, component `frontend/src/components/PluginsPanel.jsx`, loaded with `React.lazy` so the initial workspace bundle does not include it. Panel-local `useState`, same shape as `NeuralAudioRenderPanel` (catalog, busy, error code). No new Zustand slice and no persistence of plugin config in `musicStore`.

The panel lists id, name, version, category, lifecycle, health, resources, and error code. Actions follow the lifecycle: Install, Enable, Disable. The configuration form is built only from the schema subset (string, number, integer, boolean, enum) and only from properties `parse_manifest` accepted. There is no URL field and no browse-the-web control.

Register the tab as `{ id: 'plugins', label: 'Plugins' }` on the `TABS` array in `ComposerWorkspace.jsx`. Import `lazy` and `Suspense` from `react`. A 409 leaves the previous card in place and shows the error code. An enable response of HTTP 200 with lifecycle `failed` replaces the card with that object.

Proxy `/plugins` in `frontend/vite.config.js` and `frontend/nginx.conf` or the browser hits the SPA fallback.

### Part J — Version gate

Before any import on enable or startup reconcile:

1. Re-parse the manifest from disk (do not trust the stale catalog object alone).
2. `accepts_api_compatibility` must be true, else lifecycle `incompatible` and skip import even if desired state is `enabled`.
3. Manifest `version` must equal `installed_version`, else `plugin_version_mismatch` and skip import.
4. Dependency ids that are required must already be lifecycle `enabled` with the exact pinned version when a pin is present. Use the version comparison that `claim_duplicate_ids` performs today (`dependency.version` against the other manifest version), but run it only at enable and startup reconcile, against plugins whose lifecycle is `enabled`. Otherwise return 409 `plugin_dependency_missing` or `plugin_dependency_cycle` and skip import. Do not set any other plugin’s `desired_state` to `disabled`.

A 409 does not write `desired_state`. Registry: after enable and after disable, call `reload_registry()` with no explicit `descriptors` argument. That replaces `_registry` from built-ins and then `_attach_plugin_descriptors()` with `overwrite=False`, so a disabled id disappears from `GET /ai/models`. Do not add an unregister API. `runtime=plugin` stays excluded from the implicit `generate_composer` default in `backend/app/ai_runtime/routing.py`.

## HTTP

| Method | Path | Success | Failure |
|--------|------|---------|---------|
| `GET` | `/plugins` | 200 list with lifecycle + health | — |
| `GET` | `/plugins/{id}` | 200 | 404 `plugin_not_found` (also ids starting with `plugin:`) |
| `POST` | `/plugins/{id}/install` | 200 public object | 404 unknown; 409 incompatible, invalid manifest, duplicate |
| `POST` | `/plugins/{id}/enable` | 200 lifecycle `enabled` | 409 `plugin_not_installed`, `plugin_api_incompatible`, `plugin_version_mismatch`, `plugin_isolation_unavailable`, `plugin_dependency_missing`, `plugin_dependency_cycle` and the row is unchanged. Merged-config failure and failed `register()` are HTTP 200 with lifecycle `failed` and `health.code` set |
| `POST` | `/plugins/{id}/disable` | 200 lifecycle `disabled` | 409 `plugin_not_installed` when the row is absent |
| `PUT` | `/plugins/{id}/config` | 200 `config_present=true` | 422 `plugin_config_invalid` or `persistence_secret_rejected`; 409 `plugin_not_installed` |
| `POST` | `/plugins/reload` | 200 list | 403 `plugin_reload_disabled` unchanged |

Enable that ends `failed` is HTTP 200 with the failed object so the UI can render the error without treating the app as down. That 200 is only for a real enable attempt (merged config or `register()`). Gate refusals that never attempt import are HTTP 409 and do not change `desired_state`.

No `POST /plugins/{id}/invoke`. No download route.

## Security test matrix

File: `backend/tests/test_plugin_lifecycle_security.py`. Extend `backend/tests/test_plugin_sdk_contracts.py` so it installs and enables before expecting a model descriptor. Do not leave an assertion that a `PLUGIN_PATHS` entry is `active` immediately after reload.

| # | Case | Expected |
|---|------|----------|
| 1 | Example directory on `PLUGIN_PATHS`, no install row | `discovered`. `mukit_plugin_sample_symbolic_generator` not in `sys.modules`. No `plugin:sample_symbolic_generator` model |
| 2 | Enable before install | 409 `plugin_not_installed`. Still no module |
| 3 | API major `2` | `incompatible`. Enable 409. No module. Process still serves `/health` and a project list/open |
| 4 | `register()` raises `SystemExit` | Lifecycle `failed`, code `plugin_register_failed`. Process stays alive. Project open still works |
| 5 | `register()` raises `RuntimeError` | That id `failed`. A second valid plugin can still be enabled |
| 6 | Installed plugin, manifest `resources: ["network"]` | Enable 409 `plugin_isolation_unavailable`. Desired state stays `installed`. No module |
| 7 | Manifest `resources: ["shell"]` | `failed` / `plugin_manifest_invalid` at discovery. No module |
| 8 | Installed version `1.0.0`, on-disk version `1.0.1`, desired enabled | `failed` / `plugin_version_mismatch`. No module. A new install records `1.0.1` as `installed` and still does not import |
| 9 | Forbidden `app.services` import | Existing `plugin_forbidden_import` behavior, lifecycle `failed` |
| 10 | Invoke `SystemExit` on an enabled empty-resource plugin | HTTP/error `plugin_output_invalid`, process alive, lifecycle stays `enabled`, health `unhealthy` |
| 11 | Startup reconcile with a broken enabled plugin | Lifespan logs a warning, `/health` is ready, projects open |
| 12 | Disable | Module gone, model id absent after `reload_registry()`, invoke returns `plugin_not_enabled` |
| 13 | Config body logged | Log capture has no config values. `GET /plugins/{id}` has no config object |
| 14 | Implicit composer route | After enable, `generate_symbolic_composition()` without `model_id` is still `fake` |
| 15 | Empty `PLUGIN_PATHS` | Zero plugins. Built-in models unchanged |

## Audit (current code this plan must touch)

| Area | Fact |
|------|------|
| `plugin_host/loader.py` | `load_plugins` prepares and `_activate`s every valid root in one pass. Status `active` after `register()` |
| `plugin_host/bridge.py` | Descriptors only for `status == "active"` |
| `plugin_host/invoke.py` | Refuses records that are not `active` |
| `plugin_host/schemas.py` | `PluginPublic.status` is a free string. No health, resources, or installed version |
| `plugin_sdk/manifest.py` | No `resources` field. `capabilities` is the category capability enum |
| `main.py` lifespan | Calls `reload_plugins()` and continues on exception |
| `routers/plugins.py` | List, detail, reload. No install/enable/disable/config |
| `ai_runtime/routing.py` | Implicit `generate_composer` already skips `runtime == "plugin"` |
| Frontend | No `/plugins` client. Vite and nginx do not proxy `/plugins`. No Plugins tab |
| SQLite | No plugin table. Alembic head is `20260925_0009` |
| Examples | `deterministic_analyzer` and `sample_symbolic_generator` have no `resources` field. They must remain valid with the default `[]` |

## Commit Plan

- **Commit 1** (after tasks 1–3): `feat: add plugin lifecycle records and discovery-only scan`
- **Commit 2** (after tasks 4–6): `feat: gate plugin enable on version and resource policy`
- **Commit 3** (after tasks 7–8): `feat: expose plugin install lifecycle and security tests`
- **Commit 4** (after tasks 9–10): `feat: add plugin manager panel`
- **Commit 5** (after task 11): `docs: document plugin lifecycle and permission boundaries`

## Tasks

### Phase 1: Contracts and persistence

- [x] Task 1: Add the lifecycle vocabulary, health DTO, and manifest `resources`
  - Extend `backend/app/plugin_sdk/manifest.py` with optional `resources: list[PluginResource]`, default `[]`. `PluginResource` is the closed enum `filesystem_model_dir | network | gpu | project_read | project_write | audio_processing`. Unknown strings fail `parse_manifest` with `plugin_manifest_invalid`. Property names on `configuration_schema` that match `(?i)(secret|password|token|api_key|apikey|authorization)` or `FORBIDDEN_SECRET_FIELD_NAMES` fail the same way.
  - Add `health_status` (`unknown | healthy | unhealthy`) and `invocation_failure_count` (int, default 0) on `PluginRecord` in `backend/app/plugin_host/catalog.py`. Add `PluginHealthV1` and extend `PluginPublic` in `backend/app/plugin_host/schemas.py` with `resources`, `installed_version`, `config_present`, and `health`. `public_plugin` copies the record health fields and maps lifecycle `enabled` the way it mapped `active` for `model_id` (model categories only).
  - Keep `PLUGIN_API_VERSION` and `accepts_api_compatibility` unchanged.
  - LOGGING: DEBUG on manifest parse with `plugin_id` and `resource_count`. WARNING when a resource string or a secret-shaped property name is rejected, with the code and no file body. Levels follow `LOG_LEVEL`.

- [x] Task 2: Persist desired install state
  - Add `backend/app/db/alembic/versions/20260925_0010_plugin_installations.py` revising `20260925_0009`, DDL from Part A, downgrade drops the table. Move the Alembic head assertions from `20260925_0009` to `20260925_0010` in `backend/tests/test_project_store.py`, `backend/tests/test_project_history_store.py`, and `backend/tests/test_composer_profile_store.py`.
  - Add `backend/app/services/plugin_installation_store.py` with get/list/upsert/clear-error. Use `get_connection` and `get_project_db_path`. Before write, call `assert_no_secret_fields` and `assert_no_secret_values`. A hit raises `PersistenceSecretError` (`persistence_secret_rejected`) and does not write. Do not delete rows in this milestone.
  - LOGGING: INFO on upsert with `plugin_id`, `desired_state`, `version` (the version string is not a secret). WARNING on guard rejection with the code only. DEBUG row counts. No connection strings and no `config_json` in logs; log `db_configured: true/false` only.

- [x] Task 3: Split discovery from activation
  - In `backend/app/plugin_host/loader.py`, discovery parses manifests, applies caps, dedupes ids (`plugin_duplicate_id`), and checks API major. Stop before `register()`. Do not call the dependency or cycle half of `claim_duplicate_ids` on that pass. A missing or invalid `config.json` stays `discovered` (or `installed` once a row exists). It does not become `failed` at scan time.
  - `reload_plugins()` in `backend/app/plugin_host/bridge.py` becomes discovery-only. Add `activate_plugin(plugin_id, config)` for the lifecycle service. The `config` argument is the merged document from Part H. The host does not read SQLite. Descriptors in `plugin_model_descriptors()` require lifecycle `enabled`.
  - Update `PluginHost` lookups in `backend/app/plugin_host/invoke.py` from `active` to `enabled`, and return `plugin_not_enabled` otherwise.
  - LOGGING: INFO `plugin discovered` with id, version, and lifecycle. INFO `plugin activate start` / `plugin activate finished` with id and elapsed ms. Do not log source text or config. Existing forbidden-import WARNING stays.

### Phase 2: Enable gates

- [x] Task 4: Orchestrate install, enable, disable, and startup reconcile
  - Add `backend/app/services/plugin_lifecycle.py` with `reconcile_on_startup()`, `install_plugin`, `enable_plugin`, `disable_plugin`, `save_config`, and `public_plugins()`. It reads the catalog and the installation store and is the only writer of desired state.
  - `backend/app/main.py` lifespan calls `reconcile_on_startup()` inside the existing try/except that already continues without plugins.
  - On a 409, leave the installation row unchanged. Persist `desired_state=enabled` and `last_error_code` only after merged-config validation or `register()`.
  - Disable drops the module and instance and does not change any other plugin’s `desired_state`. The router task calls `reload_registry()` after enable and disable.
  - LOGGING: INFO for every transition `{plugin_id, from_state, to_state, code}`. WARNING when startup reconcile marks a plugin `failed`, including `error_type` and code, no traceback at INFO (traceback at DEBUG). ERROR is reserved for an unexpected store failure that is still swallowed at lifespan.

- [x] Task 5: Refuse high-risk resources and pass no host privileges
  - `enable_plugin` applies Part D: non-empty `resources` returns 409 `plugin_isolation_unavailable`, does not change the row, and does not call `activate_plugin`. Reconcile of an already-enabled row with non-empty `resources` sets lifecycle `failed` and skips import.
  - Confirm `PluginContext` still has no project path, dataset root, or API key. Do not add one.
  - LOGGING: WARNING `plugin enable refused` with `plugin_id`, `code=plugin_isolation_unavailable`, and `resource_count`. Do not log the resource names at INFO if a future resource string could be sensitive; resource names are a closed enum, so DEBUG may list them.

- [x] Task 6: Version, dependency, and crash boundaries
  - Implement the Part B and Part F checks inside `enable_plugin` and `reconcile_on_startup`. In `_activate`, catch `SystemExit` explicitly (the handler today catches `Exception` only), set `plugin_register_failed`, drop the module, and do not leave the process.
  - In `_invoke`, catch `SystemExit` the same way: `plugin_output_invalid`, increment `invocation_failure_count`, set health `unhealthy`, lifecycle stays `enabled`, process stays alive. A successful invoke resets the count and sets health `healthy`. Call `update_record` after those writes.
  - Run the existing version-pin comparison (`claim_duplicate_ids` compares `dependency.version` to the other manifest version) only at enable and startup reconcile, and only against plugins whose lifecycle is `enabled`. A miss is 409 during enable (row unchanged) or lifecycle `failed` during reconcile of an already-enabled row. Do not import. Do not set other plugins’ `desired_state` to `disabled`.
  - Merged-config failure before import persists `desired_state=enabled`, lifecycle `failed`, code `plugin_config_invalid`, HTTP 200.
  - LOGGING: WARNING `plugin version mismatch` with `plugin_id`, `installed_version`, `manifest_version`. WARNING `plugin crashed` with `plugin_id`, `error_type`, `code`. DEBUG stack traces with no file contents and no config.

### Phase 3: HTTP and tests

- [x] Task 7: Lifecycle routes
  - Extend `backend/app/routers/plugins.py` with install, enable, disable, and config routes from the HTTP table. The router calls `plugin_lifecycle` only. After enable and after disable, call `reload_registry()` with no `descriptors=` argument so built-ins are rebuilt and `_attach_plugin_descriptors()` reattaches only lifecycle `enabled` plugins. Do not add an unregister API.
  - `POST /plugins/reload` calls reconcile, not unconditional activation. 403 behavior for `PLUGIN_RELOAD_ENABLED` stays.
  - Map gate refusals to 409 without writing `desired_state`. Merged-config failure and register failure return 200 with lifecycle `failed`. `PUT` guard failures return 422 `persistence_secret_rejected`.
  - LOGGING: INFO per route with `plugin_id` and result lifecycle. WARNING on 409/422 with the code. Never log the PUT body.

- [x] Task 8: Security and contract tests
  - Add `backend/tests/test_plugin_lifecycle_security.py` covering the matrix above, using tmp plugin dirs and a tmp `PROJECT_DB_PATH`.
  - In `backend/tests/test_plugin_sdk_contracts.py`, point the autouse fixture `_isolate_plugins` at a temporary `PROJECT_DB_PATH` and initialize it with `ensure_database` so `TestClient` lifespan cannot read `backend/data/projects.db`.
  - Update every test in that file that expects `active`, `rejected`, or `unsatisfied` from `_load()` or `reload_plugins()` alone. Discovery yields `discovered` or `incompatible`. Enable is what reaches `enabled` or `failed`. That includes config, dependency, cycle, forbidden-import, host invoke, `test_http_discovery_and_reload`, and `test_example_generator_is_listed_and_not_the_silent_default`. An explicit install+enable still lists `plugin:sample_symbolic_generator` and does not change the silent fake backend.
  - LOGGING: tests assert the WARNING codes exist in captured logs for the `SystemExit` and isolation-refusal cases, and assert config values are absent. No new log calls inside the tests beyond `pytest` `caplog`.

### Phase 4: Plugins panel

- [x] Task 9: Proxy and API client
  - Add `/plugins` to the dev proxy in `frontend/vite.config.js` and to `frontend/nginx.conf` (same upstream as `/ai/`).
  - Add `frontend/src/api/pluginApi.js` with list, detail, install, enable, disable, and saveConfig. Parse FastAPI `{detail: {code, message}}` onto an error object that keeps `code`, following `composerProfileApi.js`.
  - LOGGING: the client does not log config bodies. Debug logging is not required in the browser client.

- [x] Task 10: Plugins tab
  - Add `frontend/src/utils/pluginLifecycleUi.js` with a pure helper that maps lifecycle to which of Install / Enable / Disable are available, and the health label. Unit-test that helper (frontend unit test next to the other util tests).
  - Add `frontend/src/components/PluginsPanel.jsx`. In `frontend/src/components/ComposerWorkspace.jsx`, import `lazy` and `Suspense` from `react`, add `{ id: 'plugins', label: 'Plugins' }` to `TABS`, and render the panel through `React.lazy`. Empty catalog, 409 codes, and HTTP 200 lifecycle `failed` render as visible error text, not a thrown render. A 409 keeps the previous card. A 200 with lifecycle `failed` replaces the card.
  - The configuration form submits only schema-shaped values. Saving shows that the new config applies on the next enable.
  - Verify in the browser: open the tab, see a discovered example when the dev server has `PLUGIN_PATHS` set, install, enable, disable, and confirm a forced failure code stays on the card while the piano-roll workspace still loads a project.
  - LOGGING: no `console.log` of plugin config. Surface the API `code` in the panel error line.

### Phase 5: Documentation

- [x] Task 11: Document the lifecycle and record the milestone
  - Update `docs/plugin-sdk.md` with Lifecycle, Resources, Isolation (Part E table and the refusal), Health, the new routes, and the retired “PLUGIN_PATHS means activated” sentence. State that there is no marketplace.
  - Update `.env.example` comments so `PLUGIN_PATHS` is described as discovery only.
  - Update `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, and the plugin-host row in `.ai-factory/ARCHITECTURE.md` so they mention the installation table, the lifecycle service, and the Plugins panel.
  - Add an unchecked milestone **Secure plugin lifecycle and permission boundaries** to `.ai-factory/ROADMAP.md`. Do not check it in this task; `/aif-implement` checks it only after the tests in Task 8 pass.
  - LOGGING: docs name the INFO transition fields and the rule that config values and stack traces stay out of INFO logs.

## Implementer notes

- Empty `PLUGIN_PATHS` remains “load nothing.” Default `docker compose up` behavior does not gain plugins.
- Example manifests do not need a `resources` key.
- Do not add a trust-all environment variable to make old tests pass. Update the tests to call install and enable.
- `plugin_host` stays free of SQLite. Plugins stay free of `plugin_host`.
