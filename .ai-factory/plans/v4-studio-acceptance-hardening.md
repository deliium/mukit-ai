# Implementation Plan: V4 Studio Acceptance and Production Hardening

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-27
Planning depth: ultra

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra
- Refined: 2026-09-27 (`/aif-improve`, all findings applied)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request’s tests and documentation
- Scope: one fake-mode acceptance layer that walks V4 as a single studio (autonomous composition, reference conditioning, AI Jam durability, audio-to-mix, plugins, failure recovery), plus the migration ladder, the storage/secret/backup holes that ladder depends on, and the operational docs
- Parent (reuse, do not fork): `POST /projects` and revision CAS in `backend/app/routers/projects.py` / `backend/app/services/project_history.py`; autonomous routes in `backend/app/routers/ai_agents.py` and `backend/app/services/autonomous_composer.py`; `generate_music_json` and `assemble_reference_conditioning`; `applyAiJamTakeToComposition` in `frontend/src/utils/liveTakeApply.js`; audio recovery, stem, mix-analysis, and mix-plan routes; `plugin_lifecycle` and `PluginHost`; `persistence_secret_guard` and `redact_log_fields`; `events_outside_targets_fingerprint`; Alembic chain `20260914_0001` through `20260926_0017`; `scripts/v3_docker_acceptance.sh` as the pattern for an opt-in Compose gate; `workflow_eval` as the musical-metric oracle only
- Product entry points stay the ones already shipped. This plan does not add a studio-acceptance HTTP route, a second Jam engine, a second critique engine, or a playable schema

## Roadmap Linkage
Milestone: "V4 studio acceptance and production hardening"
Rationale: Every milestone in `.ai-factory/ROADMAP.md` is checked. Feature tests and `scripts/v1_docker_acceptance.sh` through `scripts/v3_docker_acceptance.sh` do not walk autonomous composition, reference non-copy, Jam durability, audio-to-mix, plugins, and crash recovery as one project, and they do not prove an Alembic upgrade from baseline still opens a `composition.v1` project. The docs task adds this milestone unchecked. Check it only after the fake-mode studio scenarios and the migration ladder pass.

## Goal

AI Composer V4 can be exercised as one human-directed studio. A later change that breaks migrations, the playable-source invariant, preservation, plugin isolation, upload bounds, or crash recovery fails a fake-mode test. Live models, training, and weight downloads stay opt-in and outside the default gate.

```text
composition.v1 project                         Alembic 0001 / 0011 database
        │                                              │
        └──────────────► upgrade + open ──────────────┘
                              │
                              ▼
                    composition.v2 on the project head
                              │
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
  autonomous run        reference generate      Jam take fixture
  (fake agents)         (melody not copied)     → save → MIDI
        │                     │                      │
        └──────── head revision + reopen ───────────┘
                              │
              audio recovery bind → harmony preview
              → fake stems → mix analysis → mix apply
                              │
              plugin enable/disable, cancel, SystemExit,
              process reopen, sqlite backup/restore
```

**Acceptance one-liner:** With fake LLM, fake audio, fake recovery, and fake neural modes on, studio pytest files each use their own temp SQLite database and the same fake bundle. The autonomous file reopens a `composition.v2`. The reference file rejects a melody pitch list equal to the reference and rejects that reference pitch token inside `generation_parameters`. The Jam file saves a checked-in take and exports MIDI. The audio file keeps the source hash and stem hashes stable through mix apply. The plugin file leaves project create working after disable. The recovery file resumes a failed stage after `reset_database_initialization_cache()` without replacing the head. `scripts/v4_docker_acceptance.sh` starts one `autonomy_mode=autonomous` run, restarts the backend container, and reopens that project. The default `scripts/run_tests.sh` still does not call a live API or download weights.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Studio scenario** | One pytest (or the Docker script) that calls shipped HTTP routes and, for Jam, the shipped client commit helper. It is not a new product feature |
| **Playable source** | `composition.v2` `tracks[].events[]` on the project working copy and on revision snapshots. Harmony, analysis, plans, artifacts, and mix JSON are not playable |
| **Product V3 / V4** | Runtime eras (unified AI runtime, then agents and studio workflows). There is no `composition.v3` or `composition.v4` document |
| **Migration ladder** | A temp database created at an older Alembic revision, given a legal baseline `projects` row, then upgraded to `ScriptDirectory.get_current_head()` and opened with the current store. The chain’s head while this plan was written is `20260926_0017`; the assertion reads the head, it does not hardcode that id |
| **Baseline schema** | Alembic revision `20260914_0001` (`projects` and the history tables only) |
| **Pre-autonomous schema** | Alembic revision `20260925_0011` (neural, artifacts, profiles, recovery, stems, mix, plugins, operation columns; no `autonomous_runs`) |
| **Pitch-sequence guard** | Ordered MIDI pitches of one track. Equality is the copy check. It is not a similarity score and not a musical-quality claim |
| **Jam golden** | The composition JSON produced once from the current `applyAiJamTakeToComposition` result in `liveJam.simulatedMidi.test.js` and checked in. Later runs deep-equal that file. The node test does not rewrite it. Pytest persists that file through the project API; it does not reimplement the jam engine |
| **Storage root** | A filesystem directory the app writes for recovery assets, neural renders, mix reports, mix previews, workflow-eval reports, or a SQLite backup. It must not be `DATASET_ROOT` and must not contain `PROJECT_DB_PATH` |
| **Backup** | An offline `sqlite3` `Connection.backup()` copy of `PROJECT_DB_PATH`. It is not revision restore, which already creates a child revision on the same database |
| **Fake bundle** | `LLM_FAKE_MODE=1`, `AUDIO_FAKE_MODE=1`, `AUDIO_RECOVERY_FAKE_MODE=1`, `NEURAL_AUDIO_FAKE_MODE=1`, `MIX_ANALYSIS_FAKE_MODE=1`, `MIX_PLAN_FAKE_MODE=1`, `DEFAULT_LLM_PROVIDER=fake`. No provider key is required |
| **Real-model smoke** | The existing `RUN_LLM_SMOKE=1` test. The studio suite never sets it |

## Non-goals

- A new musical benchmark, listening packet, or quality score. `workflow_eval` keeps that job. Studio tests set no `musical_quality_claim`
- `composition.v4`, a studio HTTP runner, or a route that executes every scenario for a user
- Reimplementing Jam, accompaniment, critique, arrangement, or mix DSP in the test package
- Playwright coverage for autonomous, reference, Jam commit, mix assist, or plugins. Browser panels stay on their existing specs. Jam durability is the node golden plus the API round trip
- CI downloads of MusicGen, Demucs, llama.cpp, vLLM, or training corpora. Optional Compose profiles are named in a contract test and are not started
- An HTTP backup or restore endpoint
- Treating FluidSynth as required. MIDI and MusicXML export are the required exports. `POST /export/wav` may return 503 when the binary is absent
- Rewriting per-feature tests that already pass. This layer adds the cross-scenario gate and the holes listed in the findings
- A subprocess sandbox for plugins. In-process import guards stay as they are. The operations doc states the residuals from Part H: plugin import hooks are not an OS sandbox, cancel does not kill a sidecar process, and a native crash in an in-process library can still take down the API

## Repository findings

Per-feature pytest, frontend unit tests, and Playwright already cover slices of this studio. Nothing walks them as one project, and `scripts/v3_docker_acceptance.sh` stops at fake neural render plus restart. It does not run an autonomous brief, a reference generate, Jam, mix plan, plugins, or recovery bind.

`composition.v2` is the only playable schema (`backend/app/composition_schemas.py`). `backend/tests/test_composition_v2_migration.py` and `backend/tests/test_project_routes.py` (`test_open_migrates_v1_composition_and_preserves_notes`) already open planted V1 JSON. The Alembic chain runs `20260914_0001` through `20260926_0017`, and each revision defines `upgrade` and `downgrade`. `ScriptDirectory.get_current_head()` is the value tests should compare. `backend/tests/test_autonomous_composer_store.py` `test_migration_creates_tables` still expects `20260926_0013`. No migration is named composition V3 or V4. There is no test that inserts a project at `0001` or `0011` and upgrades to head before open. `20260926_0014` inserts an owner membership for every existing `projects` row, so a baseline insert of `id`, `name`, `composition_json`, `created_at`, and `updated_at` (the shape in `test_legacy_0012_rows_upgrade`) is enough.

Autonomous start (`AutonomousRunRequest.project_id` optional in `backend/app/autonomous_composer_schemas.py`) creates or binds a project and commits stages `symbolic`, `arrangement`, and `expression` onto the history graph. `include_rendering` false skips `render`. Guided mode holds at `form`, `harmony`, `motif`, `critique`, and `arrangement`. `approve_checkpoint` continues only when `status` is `awaiting_approval` and `checkpoint_id` matches. The route is `POST /ai/agents/autonomous/runs/{run_id}/checkpoints/{checkpoint_id}/approve`. `GET` of a run whose stage is still `running` reconciles it. Cancel, `SystemExit`, and preserve failures already keep the previous head in unit tests. They do not call `reset_database_initialization_cache()` the way `test_restart_simulation_preserves_branches_and_restore` does, and they do not snapshot the file. `edit_fake_composition_region` already builds a deterministic `replace_region` patch. `POST /llm/edit-composition-region` returns the edited composition and does not write the project.

`POST /llm/generate-music-json` carries `style_references` and `reference.conditioning.policy.v1`. `assemble_reference_conditioning` is documented to omit event arrays and motif pitch lists from soft lines. `backend/tests/test_reference_features_acceptance.py` asserts the soft fragment contains the text “do not copy melodies”. Fake generate returns the canned expressive fixture and does not read the reference, so a pitch-list inequality against a disjoint phrase does not by itself prove the soft fragment stayed free of pitches.

Jam commit is `applyAiJamTakeToComposition` until a later `PATCH /projects` or revision commit. `frontend/src/utils/liveJam.simulatedMidi.test.js` covers the simulated melody and track roles. `frontend/e2e/midi-live-input.spec.js` only mounts the panel.

Recovery `create_recovery_job` in `backend/app/routers/audio_recovery.py` does `await file.read()` and only then `enqueue_audio_recovery_job` applies `max_upload_bytes` (`backend/app/services/audio_recovery/pipeline.py`). Transcription already stops at the limit in `_read_upload_bounded`, and that helper raises `AudioTranscriptionError`. A shared reader must not import that error type. Recovery keeps HTTP 413 `audio_payload_too_large`. Recovery, neural, mix-analysis, and mix-plan settings document “never `DATASET_ROOT`” and default the directory next to the project DB. Only `workflow_eval_settings._reject_root` refuses a configured root. An operator can point `AUDIO_RECOVERY_ASSET_ROOT` at `DATASET_ROOT`.

`configured_provider_secrets` reads `OPENAI_API_KEY` and `DEEPSEEK_API_KEY` and ignores the placeholders `fake`, `unused`, and `changeme`. `LOCAL_LLM_API_KEY` is not scanned. Compose defaults that variable to the literal `local`. A substring scan of the word `local` would reject ordinary text, so `local` must stay a placeholder. `refresh_token` is not in `FORBIDDEN_SECRET_FIELD_NAMES`. `operation_trace.FORBIDDEN_LOG_FIELD_NAMES` is kept aligned by `test_secret_field_names_stay_aligned`.

There is no SQLite backup CLI. `POST /projects/{id}/revisions/{revision_id}/restore` writes a child revision. `docs/project-persistence.md` describes `docker compose down -v` as the volume wipe. `python` `sqlite3.Connection.backup` is unused.

`backend/app/workflow_eval/` scores hard musical metrics offline and refuses `DATASET_ROOT` and `PROJECT_DB_PATH`. It does not persist a user project, render audio, export, run Jam, or install plugins. Studio acceptance must not grow that package.

Plugin install, enable, invoke-via-`plugin:sample_symbolic_generator`, disable, and `SystemExit` containment are tested in `backend/tests/test_plugin_lifecycle_security.py` and `backend/tests/test_plugin_sdk_contracts.py`. There is no `POST /plugins/{id}/invoke`. `deterministic_analyzer` is `PluginHost.analyze` only.

`scripts/run_tests.sh` does not start Docker. Live provider smoke is `RUN_LLM_SMOKE=1` (`backend/tests/test_llm_real_provider_smoke.py`). FluidSynth smoke is `RUN_WAV_RENDERER_SMOKE=1`. PyYAML is already in `backend/requirements.txt`.

## Approach Evaluation (locked)

### Part A — Where the studio suite runs

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Product route that runs scenarios A–G** | Visible in the app | Spends time on a user request and writes a project the user did not ask to create | **Reject** |
| **B. Fold the scenarios into `workflow_eval`** | One CLI | That package is a metric oracle. Its non-goals forbid project DB writes, neural render, and plugins | **Reject** |
| **C. Pytest package plus an opt-in Docker script, calling shipped routes** | Same functions as production. Default CI stays on fakes and temp databases | A human starts the Docker script and any real-model smoke | **Accepted** |

**Locked:** Tests live in `backend/tests/studio_acceptance/`. They use FastAPI `TestClient` and a temp `PROJECT_DB_PATH`. `app/` does not import this package. `scripts/v4_docker_acceptance.sh` follows `scripts/v3_docker_acceptance.sh`: exit 0 when `RUN_DOCKER_ACCEPTANCE` is unset, fake bundle when set, `docker compose -f docker-compose.yml` only.

### Part B — What V1 → V2 → V3 → V4 migration means

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Add `composition.v3` and `composition.v4` documents** | Names match the request text | Both are unsupported. Playable truth is already V2 | **Reject** |
| **B. Open V1 JSON after upgrading a database from baseline and from the pre-autonomous revision** | Proves old projects open on today’s schema | Does not replay every intermediate revision’s data | **Accepted** |

**Locked:** Two temp databases. Upgrade with `command.upgrade` and `_alembic_config` from `backend/app/db/connection.py`, one only to `20260914_0001` and the other only to `20260925_0011`. Each gets one `projects` row using the baseline columns from `test_legacy_0012_rows_upgrade` (`id`, `name`, `composition_json`, `created_at`, `updated_at`). `composition_json` is the canonical V1 fixture (pitch id `keep-me`, pitch `C4`, matching `test_open_migrates_v1_composition_and_preserves_notes`). Then upgrade to head. Open returns `composition.v2` with that pitch and id. `alembic_version.version_num` equals `ScriptDirectory.get_current_head()`, not a hardcoded `20260926_0017`. The same assertion replaces the stale `20260926_0013` check in `test_migration_creates_tables`. `0014` seeds the owner membership during that upgrade. The test uses the existing Alembic API in-process. It does not shell out to a second database file the app is serving.

### Part C — Melody was not copied

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep the “do not copy melodies” prompt assertion** | Already shipped | A model can still echo the reference pitches | **Reject** as the studio gate |
| **B. Exact ordered pitch sequence of the generated melody track differs from the reference track** | Deterministic, no extra model | Fake mode returns a canned piece. The reference must be a phrase that canned piece does not repeat | **Accepted** |
| **C. A similarity or copyright score** | Sounds stricter | It is a taste metric. `workflow_eval` already refuses those as gates | **Reject** |

**Locked:** The reference is a short legal `composition.v2` whose melody uses a pitch token that does not occur in `backend/app/fixtures/composition_v2_expressive.json`. Generate uses `reference.conditioning.policy.v1` with melody in `regenerate`. The studio test fails if that token appears in the returned `generation_parameters` JSON. It also fails if the generated melody track’s pitch list equals the reference track’s pitch list. Masked dimensions still follow the existing soft-fragment test; this plan does not delete that test.

### Part D — AI Jam without hardware

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Playwright drives Web MIDI** | Matches the UI | Web MIDI is not available in CI, and the current spec forbids requesting it on startup | **Reject** |
| **B. A Python copy of the jam engine** | One process | A second engine will drift | **Reject** |
| **C. The existing simulated-MIDI commit stays the behavior proof. Its composition is a golden file the API then saves, reopens, and exports as MIDI** | One commit function. Durability is the project API | The golden updates when the helper’s output changes | **Accepted** |

**Locked:** The implementer writes `backend/tests/fixtures/studio/jam_committed_take.json` once from the current `applyAiJamTakeToComposition` result in `frontend/src/utils/liveJam.simulatedMidi.test.js`. After that, the node test reads the file and deep-equals the next commit. It does not rewrite the file. Pytest loads that file, `PATCH`es it onto a project, commits a revision, reopens, and `POST /export/midi` returns bytes starting with `MThd`. Edit is one note velocity change through the project patch, then undo is the existing revision restore of the pre-edit head.

### Part E — Audio compare and master export

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Compare waveforms to the source recording** | Matches “compare with original” literally | Fake renders are not the source performance. A similarity threshold is not a product invariant | **Reject** |
| **B. Source SHA and stem SHA stay stable; the report and the mix revision exist; V2 events change only where the user applied a symbolic edit** | Uses the shipped fake engines. States the real invariant | Does not claim the render sounds like the upload | **Accepted** |

**Locked:** Scenarios D and E share one fresh temp project. They do not reuse the autonomous scenario’s database. Recovery uploads `backend/tests/fixtures/audio/recovery/mixed_melody_bass.wav`. Bind stores `audio.alignment.v1`. Harmony uses `POST /harmony/reharmonize/preview`, then a re-GET proves stored events are unchanged. Fake stem-set render does not change the composition fingerprint. `POST /mix-analysis/analyze` returns `mix.analysis.v1` with `dsp_backend` `fake`. Mix preview uses the phrase `make bass less dominant` and the fake stem set’s `stem_set_id`, matching `test_acceptance_preview_apply_preserves_stems`. Apply downloads a mix revision WAV (`RIFF`), stem hashes stay unchanged, `guarantee` is false, and undo restores the previous mix head. `POST /export/wav` is called only to record 200 or 503. 503 does not fail the scenario.

### Part F — Backup

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. HTTP backup route** | Convenient | Puts the whole database behind the API | **Reject** |
| **B. Document revision restore as backup** | Already shipped | A deleted volume is not a revision | **Reject** |
| **C. Offline CLI using `Connection.backup()`** | Consistent snapshot. No request path | The operator stops the writer before restore | **Accepted** |

**Locked:** `main()` lives in `backend/app/db/backup.py`. Invoke `python -m app.db.backup` from `backend/`, the same way the other CLIs run. Do not add `backend/app/db/__main__.py`. Commands are `backup --dest <file>` and `restore --from <file> --dest <file>`. Restore writes the destination and refuses to overwrite the live `PROJECT_DB_PATH` from inside the process. Destinations go through the storage-root policy. The operations doc says to stop the backend before restore, then start it so lifespan runs `alembic upgrade head`.

### Part G — Docker profiles and model combinations

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. CI starts `local-ai`, `neural-audio`, and `audio-recovery`** | Proves the sidecars boot | Those images are large and the fakes already cover the app | **Reject** |
| **B. Default Compose file only, fake bundle, plus a YAML contract that the optional profiles exist and are not in the default file** | Matches how V3 acceptance works | A broken sidecar image is a manual check | **Accepted** |

**Locked:** `scripts/v4_docker_acceptance.sh` never passes `-f compose.local-ai.yml`, `compose.neural-audio.yml`, or `compose.audio-recovery.yml`. It starts one run with `autonomy_mode` `autonomous` and `include_rendering` false, then restarts the backend and compares the event fingerprint. It does not walk the guided checkpoint loop from scenario A. A pytest loads the Compose files with PyYAML and asserts the profile names `local-ai`, `local-ai-vllm`, `training`, `neural-audio`, and `audio-recovery`, and asserts `docker-compose.yml` has no `profiles` key. Model matrix tests use the registry with fake as default, an explicit missing remote id raising `ModelNotFoundError` or `ModelUnavailableError` without a socket, and `probe_local_llm_health` against a mocked HTTP response. No test opens a real provider.

### Part H — Security review output

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A checklist document only** | Fast | Leaves the unbounded recovery read and the root hole in place | **Reject** |
| **B. Fix the three concrete holes and document residuals** | The suite fails if the holes return | Does not sandbox native code | **Accepted** |

**Locked holes:** chunked recovery reads, storage-root refusal, `LOCAL_LLM_API_KEY` scanned only when it is not a placeholder (`local` joins `fake`, `unused`, `changeme`), and `refresh_token` added to both forbidden field sets. Residuals, written in the operations doc and not coded around: plugin import hooks are not an OS sandbox; cancel does not kill a sidecar process; a native crash in an in-process library can still take down the API.

## Contract

### Fake bundle

Every studio pytest sets the fake bundle on the temp process environment and points `PROJECT_DB_PATH` at a temp file. `PLUGIN_PATHS` is empty except in the plugin scenario, where it is the two example plugin directories and no others.

### Scenario A — Autonomous composition

1. `POST /projects` creates an empty legal project.
2. `POST /ai/agents/autonomous/plans` with a detailed `creative.brief.v1` returns a non-playable plan (no `tracks`, no `events`).
3. `POST /ai/agents/autonomous/runs` with that `project_id`, `include_rendering` false, and `autonomy_mode` `guided`.
4. The run holds with `status` `awaiting_approval`. `POST /ai/agents/autonomous/runs/{run_id}/checkpoints/{checkpoint_id}/approve` repeats while that status holds, capped at the five guided checkpoint ids `form`, `harmony`, `motif`, `critique`, and `arrangement`. Stages `symbolic` and `expression` are `completed`. `render` is `skipped`.
5. `GET /projects/{id}` returns `composition.v2` with at least one note event. `GET /projects/{id}/revisions` lists metadata only (no embedded composition). The head snapshot decodes to the same event fingerprint as the working copy.
6. `POST /composition/development/preview` does not change that fingerprint.
7. One fake `POST /llm/edit-composition-region` sends `edit.selection.start_bar` and `end_bar` inside the composition’s `bar_count`. `events_outside_targets_fingerprint` of the response, with that bar range, matches the request composition outside the range. A re-GET shows the stored project still on the pre-edit fingerprint. The route does not write the project. An explicit `PATCH` is what would.

### Scenario B — Reference composition

`POST /reference-features/analyze` on the reference does not mutate it. `POST /llm/generate-music-json` sends the reference composition, a dimension mask, and a policy that regenerates melody. Response status 200. The reference melody pitch token is absent from `generation_parameters`. The generated melody pitch list differs from the reference. The request key and instrument constraints are unchanged. The persisted reference project, if any, is a different project from the generated working copy.

### Scenario C — AI Jam

The golden file is written once, then read back. The node test does not regenerate it. Save, reopen, MIDI export, one velocity edit, and revision restore behave as Part D. `POST /live/accompaniment/predict` is called once with the jam fields and returns without writing the project.

### Scenario D — Audio round trip

On a fresh temp project, upload the mixed fixture under the recovery byte limit. Job completes in fake mode. Bind returns an alignment asset. Source file SHA is unchanged after a later stem render. `POST /harmony/reharmonize/preview` does not change stored events on re-GET. Stem set completes in fake mode. Composition fingerprint is unchanged by the stem job.

### Scenario E — Production

Analyze the fake stem set. Preview a mix plan with the phrase `make bass less dominant` and that stem set’s `stem_set_id` (`mutates_stems` false, `guarantee` false). Apply. Download returns `RIFF`. Stem hashes match the pre-apply hashes. Undo restores the previous mix revision id. Symbolic MIDI export of the same project still parses as the score, not as the mix WAV.

### Scenario F — Plugin

Install `sample_symbolic_generator`, enable it, `GET /ai/models` includes `plugin:sample_symbolic_generator`, one symbolic generate through that id returns a valid `composition.v2`. `PluginHost.analyze` on `deterministic_analyzer` leaves its input composition byte-identical. Disable. The model id disappears. `POST /projects` still returns 201. `GET /ready` stays 200. A plugin whose manifest `resources` is non-empty still fails enable with `plugin_isolation_unavailable` and is not imported (existing assertion, called from this module so the scenario owns the boundary).

### Scenario G — Autonomous recovery

1. Start a run and cancel it. The cancelled stage is not `completed`. The project head is still valid V2.
2. A `SystemExit` inside one stage returns a failed stage with `operation_model_crashed`, `/ready` stays 200, and the head revision id is unchanged.
3. Call `reset_database_initialization_cache()` the way `test_restart_simulation_preserves_branches_and_restore` does. Leave a stage `running` in the same file. `GET` the run reconciles it. `POST .../resume` or `POST .../stages/{id}/retry` continues. The resulting head is valid V2 and the revision chain has no duplicate snapshot for a stage that did not commit.
4. `python -m app.db.backup backup` copies that file. Mutate the live file. Restore into a new path. Open the restored path and the pre-mutation head fingerprint matches.

### Storage-root policy

`backend/app/storage_root_policy.py` exposes `reject_storage_root(root, *, dataset_root, project_db) -> None`. It resolves paths and raises `StorageRootError` with code `storage_root_rejected` and a reason of `dataset_root`, `inside_dataset_root`, `project_db_path`, or `contains_project_db`. It logs the reason code only.

Callers: `load_audio_recovery_settings`, `load_neural_audio_settings`, `load_mix_analysis_settings`, `load_mix_plan_settings`, and `load_workflow_eval_settings`. Workflow eval keeps its public error code `benchmark_root_rejected` and maps the policy reason into the existing warning log. Default sibling directories (`<db parent>/audio_recovery_assets` and the neural and mix defaults) stay legal.

### Recovery upload

`read_upload_bounded` in `backend/app/audio_upload.py` reads in at most 64 KiB chunks and stops when `total > max_bytes`. It raises a small exception that carries `limit_bytes` and imports neither router. Transcription maps that exception onto its existing error. `create_recovery_job` maps it onto `audio_payload_too_large` (HTTP 413) before enqueue, and does not create a job directory. Services still check `len(payload)` so a direct service call stays bounded.

### Secrets

`configured_provider_secrets` also reads `LOCAL_LLM_API_KEY`. Placeholder values are `fake`, `unused`, `changeme`, and `local`. `refresh_token` is added to `FORBIDDEN_SECRET_FIELD_NAMES` and to `operation_trace.FORBIDDEN_LOG_FIELD_NAMES`. Tests keep the two sets equal.

### Backup CLI

Run from `backend/`:

```text
python -m app.db.backup backup --dest ./var/backups/projects.db
python -m app.db.backup restore --from ./var/backups/projects.db --dest ./var/restore/projects.db
```

`main()` is in `backend/app/db/backup.py`. Exit code 0 on success. Exit code 2 on `storage_root_rejected`, a destination equal to the live database, or a missing source. Logs the basename and byte size at INFO. Does not log row contents or SQL values.

### Docker script

```text
RUN_DOCKER_ACCEPTANCE=1 ./scripts/v4_docker_acceptance.sh
```

Uses a distinct `COMPOSE_PROJECT_NAME` default `mukit-v4-accept`. Creates a project, starts one run with `autonomy_mode` `autonomous` and `include_rendering` false, restarts the backend service, reopens the project, and checks the event fingerprint. It does not approve guided checkpoints. Unset `RUN_DOCKER_ACCEPTANCE` exits 0 with a SKIP line. The script does not export provider keys and does not set `RUN_LLM_SMOKE`.

### Real-model smoke

Unchanged entry: `RUN_LLM_SMOKE=1`. `scripts/run_tests.sh` and the V4 Docker script do not set it. The operations doc lists it, `RUN_WAV_RENDERER_SMOKE=1`, and the manual Compose profile commands as operator steps.

## Logging

`LOG_LEVEL` controls verbosity. Do not log prompts, briefs, API keys, MIDI or WAV bytes, composition event arrays, reference pitches, or absolute filesystem paths. Basenames, ids, fingerprints clipped to 12 hex characters, reason codes, and byte sizes are safe.

- INFO when a storage root is accepted: settings name and directory basename. WARNING `storage_root_rejected` with `reason` only
- INFO on recovery reject: code `audio_payload_too_large` and `limit_bytes`. No payload prefix
- INFO on backup start and finish: command name, basename, byte size. WARNING on refusal with the reason code
- INFO in studio tests is not a product log. Product log assertions in the new tests: a secret sentinel never appears; a rejected upload log has no fixture bytes
- Frontend golden test does not log the take

## Commit Plan
- **Commit 1** (after tasks 1–4): "fix: bound recovery uploads, refuse unsafe storage roots, and add a SQLite backup CLI"
- **Commit 2** (after tasks 5–7): "test: prove schema upgrades and autonomous reference acceptance"
- **Commit 3** (after tasks 8–11): "test: cover jam durability, audio production, plugins, and run recovery"
- **Commit 4** (after tasks 12–13): "test: add the fake-mode V4 Docker acceptance gate"
- **Commit 5** (after tasks 14–15): "docs: describe V4 studio operations and the acceptance gate"

## Tasks

### Phase 1: Boundaries the suite depends on
- [x] Task 1: Refuse storage roots that collide with the dataset or the project database
  Deliverable: `reject_storage_root` as in the contract. Recovery, neural, mix-analysis, and mix-plan loaders call it when resolving the configured or default root. `load_workflow_eval_settings` delegates and still raises `benchmark_root_rejected`. Default sibling directories still load. A root equal to `DATASET_ROOT`, nested inside it, equal to the db file, or containing the db file is rejected. Existing workflow-eval tests that expected equality rejection still pass.
  Files: `backend/app/storage_root_policy.py`, `backend/app/audio_recovery_settings.py`, `backend/app/neural_audio_settings.py`, `backend/app/mix_analysis_settings.py`, `backend/app/mix_plan_settings.py`, `backend/app/workflow_eval_settings.py`, `backend/tests/test_storage_root_policy.py`
  Logging: WARNING `storage_root_rejected` with `reason`. INFO basename on accept. DEBUG is not required. No absolute paths.

- [x] Task 2: Stop recovery uploads at the configured byte limit while reading
  Deliverable: `read_upload_bounded` in `backend/app/audio_upload.py` raises a small exception that carries `limit_bytes` and imports neither router. Transcription maps it onto the existing transcription error. `create_recovery_job` maps it onto 413 `audio_payload_too_large` and does not create a job directory. A body one byte over `AUDIO_RECOVERY_MAX_UPLOAD_BYTES` returns that 413. A legal WAV fixture under the limit still completes in fake mode. The service-level length check in `enqueue_audio_recovery_job` stays.
  Files: `backend/app/audio_upload.py`, `backend/app/routers/audio_recovery.py`, `backend/app/routers/transcription.py`, `backend/tests/test_audio_recovery_upload_bound.py`
  Depends on task 1 only for settings load order, not for behavior.
  Logging: INFO code and `limit_bytes` on reject. Do not log the body. Match the existing transcription log shape where it already records a basename.

- [x] Task 3: Scan local provider secrets without treating the placeholder `local` as a credential
  Deliverable: `configured_provider_secrets` includes `LOCAL_LLM_API_KEY` except for the placeholder set in the contract. `refresh_token` is forbidden in persistence and in span redaction, and the alignment test still passes. A payload containing a non-placeholder local key value is rejected with `forbidden_secret_value`. A payload containing the word `local` is accepted. Logs of the rejection contain the code and not the key.
  Files: `backend/app/services/persistence_secret_guard.py`, `backend/app/operation_trace.py`, `backend/tests/test_persistence_secret_guard.py`, `backend/tests/test_operation_trace.py`
  Logging: existing WARNING `forbidden_secret_field` / value path. Count and code only.

- [x] Task 4: Add an offline SQLite backup and restore CLI
  Deliverable: `main()` in `backend/app/db/backup.py`, invoked as `python -m app.db.backup` from `backend/`. Implement `sqlite3.Connection.backup`. Do not add `backend/app/db/__main__.py`. Restore refuses a destination that resolves to the live `PROJECT_DB_PATH` or fails `reject_storage_root`. A round-trip test writes two projects, backups, deletes a row in the source, restores to a new file, and reads both projects from the restored file. No HTTP route.
  Files: `backend/app/db/backup.py`, `backend/tests/test_db_backup.py`
  Depends on task 1.
  Logging: INFO command, basename, byte size. WARNING reason code on refusal. ERROR exception type name only.

### Phase 2: Migrations
- [x] Task 5: Open a V1 project after upgrading from baseline and from the pre-autonomous schema
  Deliverable: The two-database ladder in Part B. Insert only the baseline `projects` columns, with the V1 fixture in `composition_json`. After upgrade to head, `projects` still has the row, open migrates to `composition.v2` with pitch `C4` and id `keep-me`, and `alembic_version.version_num` equals `ScriptDirectory.get_current_head()`. Update `test_migration_creates_tables` so it expects that same head instead of `20260926_0013`. The pre-autonomous database gains `autonomous_runs` only at head. The stored composition bytes are not rewritten except by the existing open migration. Assert no `composition.v3` or `composition.v4` key is introduced.
  Files: `backend/tests/studio_acceptance/test_migration_ladder.py`, `backend/tests/test_autonomous_composer_store.py`
  Logging: test asserts product migration logs do not include the raw composition JSON. No new product logger.

### Phase 3: Studio scenarios
- [x] Task 6: Run scenario A on one project and check preservation
  Deliverable: `backend/tests/studio_acceptance/test_scenario_autonomous.py` implements the scenario A contract under the fake bundle. `conftest` leaves `COLLABORATION_ENABLED` unset. The test loops `POST /ai/agents/autonomous/runs/{run_id}/checkpoints/{checkpoint_id}/approve` while `status` is `awaiting_approval`, capped at `form`, `harmony`, `motif`, `critique`, and `arrangement`. Guided checkpoints do not advance the next committing stage until approve. Reopen fingerprint matches. Development preview is non-durable. The region-edit response preserves events outside `edit.selection` via `events_outside_targets_fingerprint`. A re-GET shows the stored project unchanged, because that route does not write the project.
  Files: `backend/tests/studio_acceptance/conftest.py` (temp db, fake bundle, client, collaboration flag unset), `backend/tests/studio_acceptance/invariants.py` (`assert_playable_v2`, fingerprint helper), `backend/tests/studio_acceptance/test_scenario_autonomous.py`
  Depends on task 5 only as a sibling; it uses a current-schema temp database.
  Logging: if the test reads caplog, assert the brief text is absent. Product logs stay on the existing autonomous logger.

- [x] Task 7: Run scenario B and fail when the melody pitch list is copied
  Deliverable: A fixture phrase whose melody pitch token does not occur in `backend/app/fixtures/composition_v2_expressive.json`. Analyze does not mutate it. Generate with melody in `regenerate` returns 200, the same hard constraints, a melody pitch list different from the reference, and `generation_parameters` JSON that does not contain that pitch token. A unit-level equality helper is what the test calls, so a future canned collision fails this test instead of being waived.
  Files: `backend/tests/fixtures/studio/reference_phrase.json`, `backend/tests/studio_acceptance/invariants.py`, `backend/tests/studio_acceptance/test_scenario_reference.py`
  Depends on task 6 for the shared conftest.
  Logging: do not log pitches. A caplog assertion, if added, checks that the reference event array is absent.

- [x] Task 8: Durably save the Jam golden and export MIDI
  Deliverable: Part D. Write `backend/tests/fixtures/studio/jam_committed_take.json` once from the current `applyAiJamTakeToComposition` result. The node test then reads that file and deep-equals the next commit. It must not rewrite the file. Pytest saves, reopens, exports MIDI (`MThd`), changes one velocity, and restores the previous revision. One predict call does not change the fingerprint.
  Files: `backend/tests/fixtures/studio/jam_committed_take.json`, `frontend/src/utils/liveJam.simulatedMidi.test.js`, `backend/tests/studio_acceptance/test_scenario_jam.py`
  Depends on task 6.
  Logging: node test stays quiet. Backend export logs stay on the existing export logger and must not include note lists.

- [x] Task 9: Run scenarios D and E on a fresh project
  Deliverable: A new temp project, not the autonomous scenario’s database. Recovery uses `backend/tests/fixtures/audio/recovery/mixed_melody_bass.wav`. `POST /harmony/reharmonize/preview` followed by a re-GET leaves stored events unchanged. Fake stem render leaves the composition fingerprint unchanged. Mix preview uses the phrase `make bass less dominant` and that stem set’s `stem_set_id`, as in `test_acceptance_preview_apply_preserves_stems`. Stem hashes stay stable across apply and undo. Mix revision download starts with `RIFF`. WAV export 503 is an accepted status.
  Files: `backend/tests/studio_acceptance/test_scenario_audio_mix.py`
  Depends on tasks 2 and 6.
  Logging: caplog must not contain the WAV fixture’s raw header beyond what existing recovery logs already allow; the test asserts the secret sentinel is absent and the upload basename is the log field.

- [x] Task 10: Run scenario F against the example plugins
  Deliverable: The plugin contract in scenario F, including the non-empty `resources` refusal. Disable leaves `POST /projects` and `/ready` healthy. Analyzer output is not written onto the composition.
  Files: `backend/tests/studio_acceptance/test_scenario_plugins.py`
  Depends on task 6 for the client fixture pattern.
  Logging: assert config values and the composition JSON are absent from caplog, reusing the sentinel style in `backend/tests/test_plugin_lifecycle_security.py`.

- [x] Task 11: Recover a failed autonomous run and a database file
  Deliverable: Scenario G. Cancel, `SystemExit`, and reconcile-after-`reset_database_initialization_cache()` plus resume or retry all keep a valid `composition.v2` head. A stage that failed before commit does not add a revision. Backup and restore open the pre-mutation fingerprint.
  Files: `backend/tests/studio_acceptance/test_scenario_recovery.py`
  Depends on tasks 4 and 6.
  Logging: caplog contains `operation_cancelled` or `operation_model_crashed` as codes and does not contain the brief text.

### Phase 4: Models and Docker
- [x] Task 12: Lock local and remote model selection on fakes and mocks
  Deliverable: Default resolution with the fake bundle selects the fake language model and does not construct a remote client. An explicit unknown remote id fails with the existing not-found or unavailable error and does not open a socket (monkeypatch `socket` or the HTTP client to raise if called). `probe_local_llm_health` against a mocked `httpx` response marks the sidecar ready or unavailable from the status code only. `runtime_is_local` stays the classifier for `local*`, `fake`, `stub`, and `plugin`. No test sets a real key.
  Files: `backend/tests/studio_acceptance/test_model_matrix.py`
  Logging: assert a fake API key sentinel in the env is not present in caplog.

- [x] Task 13: Add the opt-in V4 Docker acceptance script and the profile contract
  Deliverable: `scripts/v4_docker_acceptance.sh` as in the contract. One `autonomy_mode` `autonomous` run with `include_rendering` false, then a backend restart and an event-fingerprint compare. No guided checkpoint loop. Pytest loads the Compose YAML files and asserts the profile names and that `docker-compose.yml` does not declare those profiles. `scripts/run_tests.sh` does not invoke the Docker script. The script removes its volume unless `KEEP_VOLUME=1`, matching V3.
  Files: `scripts/v4_docker_acceptance.sh`, `backend/tests/studio_acceptance/test_compose_profiles.py`
  Depends on the shipped autonomous routes and the fake bundle. It does not depend on task 6’s guided sequence.
  Logging: the script echoes phase names and project id only. It does not echo the brief. On skip, one SKIP line.

### Phase 5: Documentation
- [x] Task 14: Write the operations runbook and the architecture acceptance boundary
  Deliverable: `docs/v4-studio-operations.md` states the playable-source invariant, the scenario list, the fake bundle, the migration ladder (V1 document plus Alembic 0001 and 0011, not a V3/V4 score), backup and restore including “stop the backend first”, cancel and model-failure behavior, upload limits, storage-root refusal, plugin residuals, and the split between default tests, `RUN_DOCKER_ACCEPTANCE=1`, and `RUN_LLM_SMOKE=1`. `.ai-factory/ARCHITECTURE.md` gains a short principle that studio acceptance is a test package and a Docker script, and that sidecars stay optional profiles. `docs/testing.md` links the new doc and the script. `docs/project-persistence.md` links the backup CLI beside revision restore. `AGENTS.md` lists `storage_root_policy.py`, `audio_upload.py`, `app.db.backup`, and the script in the map tables. `README.md` points operators at the runbook. `.gitignore` ignores `var/backups/` and `var/restore/` if those default destinations are used.
  Files: those docs, `AGENTS.md`, `README.md`, `.gitignore`
  Depends on tasks 1–13 so the commands in the doc exist.
  Logging: none in docs. The runbook quotes the redaction rules: no keys, prompts, briefs, event arrays, or audio bytes.

- [x] Task 15: Add the roadmap milestone
  Deliverable: Unchecked milestone **V4 studio acceptance and production hardening** in `.ai-factory/ROADMAP.md` with a one-line description naming the fake-mode studio scenarios, the Alembic upgrade open, and the backup CLI. Leave it unchecked until tasks 5–13 are green, then check it and add the completed row.
  Files: `.ai-factory/ROADMAP.md`
  Depends on tasks 5–13.
  Logging: none.
