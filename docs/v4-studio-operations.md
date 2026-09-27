# V4 studio operations

The playable source is `composition.v2` `tracks[].events[]` on the project working copy and on revision snapshots. Harmony, analysis, plans, artifacts, and mix JSON are not playable. There is no `composition.v3` or `composition.v4` document.

## Fake-mode studio scenarios

`backend/tests/studio_acceptance/` walks shipped HTTP routes on a temp SQLite database. Each file uses the fake bundle:

`LLM_FAKE_MODE=1`, `AUDIO_FAKE_MODE=1`, `AUDIO_RECOVERY_FAKE_MODE=1`, `NEURAL_AUDIO_FAKE_MODE=1`, `MIX_ANALYSIS_FAKE_MODE=1`, `MIX_PLAN_FAKE_MODE=1`, `DEFAULT_LLM_PROVIDER=fake`.

| File | What it checks |
|------|----------------|
| `test_migration_ladder.py` | A `composition.v1` row planted at Alembic `20260914_0001` and at `20260925_0011` opens as `composition.v2` after upgrade to the current head |
| `test_scenario_autonomous.py` | Guided autonomous run, checkpoints, preservation |
| `test_scenario_reference.py` | Generated melody pitches are not the reference pitch list, and that pitch token is absent from `generation_parameters` |
| `test_scenario_jam.py` | Checked-in Jam take, MIDI `MThd`, velocity edit, revision restore |
| `test_scenario_audio_mix.py` | Recovery bind, harmony preview, fake stems, mix apply/undo, MIDI; WAV export may be 503 |
| `test_scenario_plugins.py` | Example plugin enable, generate, disable; project create still works |
| `test_scenario_recovery.py` | Cancel, `SystemExit`, reopen/resume, SQLite backup |
| `test_model_matrix.py` | Fake language model; unknown remote id does not open a socket |
| `test_compose_profiles.py` | Optional Compose profile names stay off `docker-compose.yml` |

`./scripts/run_tests.sh` runs these pytest files. It does not start Docker, call a live provider, or download weights.

## Opt-in gates

| Gate | Command |
|------|---------|
| Default tests | `./scripts/run_tests.sh` |
| Docker studio | `RUN_DOCKER_ACCEPTANCE=1 ./scripts/v4_docker_acceptance.sh` |
| Live provider smoke | `RUN_LLM_SMOKE=1` (existing smoke test; the studio suite never sets it) |
| FluidSynth WAV smoke | `RUN_WAV_RENDERER_SMOKE=1` |

The Docker script uses only `docker compose -f docker-compose.yml`, starts one `autonomy_mode=autonomous` run with `include_rendering` false, restarts the backend, and compares the project event fingerprint. It removes its volume unless `KEEP_VOLUME=1`. Compose profiles `local-ai`, `local-ai-vllm`, `training`, `neural-audio`, and `audio-recovery` stay in their own files and are not started.

## Migration ladder

Old projects are a `composition.v1` document in a database created at Alembic baseline `20260914_0001` or the pre-autonomous revision `20260925_0011`. Upgrade to the current Alembic head, then open the project. Open migrates the document to `composition.v2`. The ladder does not invent a V3 or V4 score.

## Backup and restore

Stop the backend first. Revision restore writes a child revision on the same database. An offline copy is separate:

```bash
cd backend
python -m app.db.backup backup --dest ../var/backups/projects.db
python -m app.db.backup restore --from ../var/backups/projects.db --dest ../var/restore/projects.db
```

The destination must not be the live `PROJECT_DB_PATH` and must not sit inside `DATASET_ROOT`. The CLI logs a basename and a byte size. It does not log paths or row contents. There is no HTTP backup route.

## Cancel and model failure

Cancel fails the running autonomous stage with `operation_cancelled` and leaves the previous `composition.v2` head. A `SystemExit` inside a revision stage is recorded as `operation_model_crashed`; `/ready` stays up and that stage does not add a revision. After a process restart, `GET` of a run reconciles a stage left `running`. Resume continues from the last committed head.

Cancel does not kill a sidecar process. A native crash inside an in-process library can still take down the API.

## Upload limits and storage roots

Recovery and transcription read uploads in bounded chunks and return `audio_payload_too_large` (HTTP 413) before a job directory is created.

A storage root for recovery assets, neural renders, mix reports, mix previews, workflow-eval reports, or a SQLite backup is refused when it is `DATASET_ROOT`, inside that directory, equal to `PROJECT_DB_PATH`, or a parent of the project database. The log line is `storage_root_rejected` with a reason code only.

## Plugin residuals

Plugin import hooks are not an OS sandbox. A manifest that declares resources fails enable with `plugin_isolation_unavailable` and is not imported. Disable leaves `POST /projects` and `GET /ready` working.

## Logs

Do not log API keys, prompts, briefs, event arrays, pitches, or audio bytes. Safe fields are basenames, ids, reason codes, byte sizes, and short fingerprints.
