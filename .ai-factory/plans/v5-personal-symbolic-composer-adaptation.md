# Implementation Plan: V5 Personal Symbolic Composer Adaptation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-02

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: a Personal composer section under the existing Profiles tab, plus an optional symbolic-composer choice on the hybrid generate control. Opening the tab lists adapters and does not start training. Generate does not start training
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-02 (`/aif-improve`). Fake `POST` joins the worker before the response. Process startup marks an orphaned `running` row `personal_interrupted`. `GET /personal-composers` only reads. `_symbolic_generate` forwards a `personal_composer` id as well as a plugin id. The implicit composer route skips runtime `personal_composer`. Ownership uses action `train_adapter` and the existing collaboration error codes
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next franchise milestone and does not edit `ROADMAP.md`
- Scope: an explicit selection of user-owned projects becomes one versioned snapshot and one LoRA adapter. The finished adapter is an optional `symbolic_composer` model. Nothing trains unless the user posts that job

## Roadmap Linkage
Milestone: "V5 Personal symbolic composer adaptation"
Rationale: A composer can train a small adapter on compositions they explicitly select and attest, then choose that adapter as an optional symbolic model, while the studio never starts that training by itself. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A user selects musical projects they own, confirms those projects are allowed to be trained on, and trains a parameter-efficient adapter on a frozen symbolic base. The job records a training manifest, a dataset snapshot version, a base-model reference, an adapter configuration, and a personal model id whose display name can be `MyComposer-v1`. The user can stop, resume when a step checkpoint exists, evaluate, and delete the adapter. The finished model appears on `GET /ai/models` and can be chosen for hybrid symbolic generation. Project open, startup, listing, and generate never create a job.

Ship:

1. Non-playable documents `personal.training_manifest.v1`, `personal.dataset_snapshot.v1`, `personal.adapter_config.v1`, `personal.training_job.v1`, and `personal.eval.v1`.
2. SQLite table `personal_composer_adapters` plus files under `PERSONAL_COMPOSER_ROOT`. The snapshot is a copy taken at train time. It is not a `DATASET_ROOT` corpus and it is not the live score.
3. A rights gate that reuses `DatasetProvenance` and `compute_train_eligible` before any snapshot file is written.
4. A LoRA trainer that freezes the Music Transformer base and writes only adapter tensors. A fake engine writes the same documents without torch so CI can train `MyComposer-v1`.
5. Discovery and an explicit `options.composer_model_id` so hybrid generate can select that adapter without changing the language-model selection.
6. Profiles-tab controls and tiny composition fixtures.

Acceptance: at `4/4`, 120 bpm, and 480 ticks per quarter, fixture Etude has attacks `C4 E4 G4` at ticks `0, 480, 960`, and fixture Sketch has attacks `D4 F4 A4` at the same ticks. The user selects both projects, sets each provenance status to `user_owned` with `user_owned_attested=true`, and sets the display name to `MyComposer-v1`. `POST /personal-composers` with `PERSONAL_COMPOSER_FAKE=1` completes one job. The row stores a 64-hex snapshot version, base model id `fake:symbolic-tiny`, adapter method `lora`, and registry id `personal:pcomp_` plus 16 hex chars. `GET /ai/models?capability=symbolic_composer&status=ready` includes that id and the display name `MyComposer-v1`. A hybrid generate whose `options.composer_model_id` is that id returns `composition.v2` whose provenance `model_id` is that same id. A third project whose status is `unknown` returns `personal_rights_refused` and leaves the adapter root without a new snapshot directory. `GET /personal-composers` on a fresh database returns an empty list and creates no row.

```text
selected projects + per-project provenance
        │  (refused when not train-eligible, or when the actor is not owner)
        ▼
personal.dataset_snapshot.v1     PERSONAL_COMPOSER_ROOT/<id>/snapshot/
personal.training_manifest.v1    manifest.json
personal.adapter_config.v1       rank 4, targets qkv + out_proj, base frozen
        │
        ├─ fake engine → personal.adapter.fake.v1 → generate delegates to fake:symbolic-tiny
        └─ torch engine → personal.adapter.v1 LoRA on MusicTransformerLM
        ▼
GET /ai/models   id personal:pcomp_<hex>   display_name MyComposer-v1
hybrid options.composer_model_id = that id
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. A **personal model** is a trained adapter row plus its files. It is not a `composer.profile.v1` and it is not a full Music Transformer checkpoint. The **display name** `MyComposer-v1` is the label the user types. The **registry id** is `personal:pcomp_<hex>`. The **snapshot** is the frozen copy of the selected scores. The **base model reference** names the frozen weights the adapter attaches to. **LoRA** here means low-rank matrices on the existing attention projections, with every other parameter frozen. **Fake engine** means the job still writes the manifest, snapshot, config, and model id, and inference stays the deterministic `fake:symbolic-tiny` composer. **Train** happens only on `POST /personal-composers`.

Predecessor: `docs/music-transformer.md` and `.ai-factory/plans/v3-symbolic-music-transformer.md` for the frozen base. `backend/app/dataset/provenance.py` for train eligibility. `.ai-factory/plans/v4-composer-profiles.md` stays the soft-preference path and is not a training path.

## Approach Evaluation (locked)

### Part A — What gets trained

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Fine-tune every Music Transformer weight** | One checkpoint format already exists | The user asked for parameter-efficient adaptation rather than a full retrain. A personal copy of the base is large and is a second full model | **Reject** |
| **B. Add the `peft` package** | Common LoRA API | Torch is already an optional extra. A new training library is unused elsewhere, and CI would grow a dependency for one module | **Reject** |
| **C. An in-repo LoRA wrapper on `MultiHeadCausalAttention.qkv` and `out_proj`. The base `requires_grad` stays false. Only adapter tensors are stored** | Matches the modules in `backend/app/music_transformer/blocks.py`. No new package. `blocks.py` stays unchanged; the wrapper replaces those two attributes | The implementation is small and must be tested on `tiny_test_config` | **Accepted** |

### Part B — Where the training scores live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Copy the selected projects into `DATASET_ROOT` and run `python -m app.dataset.cli`** | Reuses the corpus pipeline | Project axioms forbid exporting user projects into `DATASET_ROOT`. A personal job would mix private scores into the offline corpus | **Reject** |
| **B. Tokenize the live SQLite composition on every step** | No copy | There is no snapshot version. An edit during training changes the set. The trainer would import `project_store` | **Reject** |
| **C. On an accepted POST, copy the selected `composition.v2` documents into `PERSONAL_COMPOSER_ROOT/<id>/snapshot/` and record `personal.dataset_snapshot.v1`. The trainer reads that directory only** | The version is the hash of the snapshot index. Later edits to the projects do not change the job. `reject_storage_root` refuses `DATASET_ROOT` and `PROJECT_DB_PATH` | A second copy of the scores exists until the user deletes the adapter | **Accepted** |

### Part C — Which projects may be included

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Train on every project in SQLite** | No form | The user forbade automatic training. Ownership and license are not checked | **Reject** |
| **B. Treat every local project as `user_owned` because the file is on disk** | Few fields | `DatasetProvenance` already requires `user_owned_attested` for that status. Inference would mark restricted or unknown scores as eligible | **Reject** |
| **C. The POST body lists project ids and a provenance object per id. `compute_train_eligible` must be true. An empty list is refused. When collaboration is on, the actor must pass a new owner-only action `train_adapter`. When it is off, `authorize_project` returns before the membership lookup, and the attestation is still required** | Same eligibility rule as the dataset pipeline. Editors cannot train on someone else's project. A missing attestation cannot be implied by the row existing | The user fills a status and an attestation checkbox per selected project | **Accepted** |

### Part D — How train, stop, and resume run

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A CLI under `app.music_transformer` and no HTTP API** | Keeps torch out of the request process | The acceptance is a user selecting projects and then selecting the model in the studio | **Reject** |
| **B. Run every step inside the POST handler** | One request | Stop and resume are not possible. The request holds the worker for the whole job | **Reject** |
| **C. Insert the row, then run a background thread that checks the stored status at each step boundary. Stop writes `stopped` and keeps files. Resume continues from the adapter step file. Fake mode runs the same loop without torch** | The HTTP process stays available. Resume is real for any job that saved a step. `music_transformer/` still does not import FastAPI or open `PROJECT_DB_PATH` | The thread is in-process and is gone after a restart until the user posts resume | **Accepted** |

### Part E — How the user selects the result

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Make the new adapter the default symbolic composer** | Nothing to click | The user asked for an optional model. Fake tiny and the Music Transformer would be displaced | **Reject** |
| **B. Store the adapter as a `composer.profile.v1` strength** | Profiles already have a picker | Profiles are soft preference text. They must not store weights or override the prompt | **Reject** |
| **C. Register a completed adapter on the existing model registry as runtime `personal_composer`, capability `symbolic_composer`, id `personal:pcomp_<hex>`, display name `MyComposer-v1`. Hybrid generation gains `options.composer_model_id`. `selection.model_id` remains the language planner** | `GET /ai/models` already feeds `MusicGenerator`. The composer id cannot be confused with the chat model. Omitting it keeps today's first-ready composer | The generate request and the hybrid resolver learn one optional field | **Accepted** |

### Part F — Tests without a GPU or a downloaded checkpoint

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require torch in the default test run** | Every test trains real weights | Core `requirements.txt` does not include torch | **Reject** |
| **B. Ship only the fake artifact and no LoRA module** | CI is simple | The user asked for a trainable adapter | **Reject** |
| **C. `PERSONAL_COMPOSER_FAKE=1` writes `personal.adapter.fake.v1` and is what the HTTP acceptance uses. A separate test imports torch when it is installed, builds `tiny_test_config`, runs two steps, and asserts base weights are unchanged while adapter weights moved. That test skips when torch is missing** | The product path and the LoRA math are both covered. Default `docker compose up` does not pull torch and does not train | Fake inference does not apply low-rank matrices. The document says so | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Symbolic base | `MusicTransformerLM` in `backend/app/music_transformer/model.py`. Attention projections are `qkv` and `out_proj` in `backend/app/music_transformer/blocks.py`. `tiny_test_config` is a CPU-sized config in `backend/app/music_transformer/schemas.py` | The LoRA wrapper assigns new modules onto those attribute names. It does not edit `blocks.py` |
| Full-model train | `train_experiment` and `resume_experiment` in `backend/app/music_transformer/train.py` write a full checkpoint card under the experiment root | Not called. A personal job must not resume by overwriting a base checkpoint |
| Rights | `DatasetProvenance` and `TRAIN_ELIGIBLE_STATUSES` in `backend/app/dataset/schemas.py`. `compute_train_eligible` in `backend/app/dataset/provenance.py`. `user_owned` requires `user_owned_attested`. `unknown` and `restricted` are not eligible | The POST body validates through `DatasetProvenance` and this function. Do not copy scores into `DATASET_ROOT` |
| Symbolic selection | `generate_symbolic_composition` in `backend/app/services/symbolic_composition_generate.py` accepts `model_id` for plugins, otherwise `resolve_symbolic_backend` chooses fake or the Music Transformer. `_resolve_hybrid_stage_models` calls `resolve_model_for_operation(GENERATE_COMPOSER, ModelSelectionInput())` with an empty selection so the language `selection.model_id` is not reused. `_symbolic_generate` forwards that id only when `_plugin_composer_model_id` sees runtime `plugin` | Pass `options.composer_model_id` only into that composer selection, and forward runtime `personal_composer` through `_symbolic_generate` the same way as `plugin` |
| Registry | `backend/app/ai_runtime/bootstrap.py` registers `fake:symbolic-tiny` and the Music Transformer descriptor from `backend/app/ai_runtime/runtimes/music_transformer.py`. `ModelRuntimeId` is a closed literal in `backend/app/ai_runtime/types.py`. `GET /ai/models` calls `reload_registry()` before `list_models`. `default_operation_routes` skips runtime `plugin` when picking the implicit composer | Add `personal_composer` inside `build_registry_from_env`. A missing table logs a warning and leaves the rest of the catalog. Do not reload inside `list_models`. Skip runtime `personal_composer` in the implicit composer route |
| Generate UI | `MusicGenerator.jsx` calls `fetchAiModels({ capability: 'symbolic_composer', status: 'ready' })` and only toggles whether Hybrid is enabled. It does not send a composer id | Add a select bound to `options.composer_model_id` |
| Profiles UI | Profiles tab renders `ComposerProfilesPanel` from `frontend/src/components/ComposerWorkspace.jsx` | Render `PersonalComposerPanel` under that panel on the same tab |
| Collaboration | `authorize_project` returns `None` when `COLLABORATION_ENABLED` is off, before any lookup. `enforce_current` maps a weak role to HTTP 403 `collaboration_role_denied` and a missing membership to `collaboration_not_member`. The role matrix in `backend/app/services/collaboration_permissions.py` gives `delete_project` to the owner only. `backend/tests/test_collaboration_permissions.py` freezes `ACTIONS` | Add action `train_adapter` to the owner action set only. Call `enforce_current` per selected project. Keep those HTTP codes |
| Storage roots | `reject_storage_root` in `backend/app/storage_root_policy.py` | Call it for `PERSONAL_COMPOSER_ROOT` |
| Latest migration | `20261002_0021` in `backend/app/db/alembic/versions/20261002_0021_musical_dependency.py`, `down_revision` `20261001_0020` | Next revision `20261002_0022`, `down_revision` `20261002_0021` |
| Agent boundary | `backend/tests/test_ai_agents_architecture.py` forbids store imports from `ai_agents/` | Add `from app.services.personal_composer_store` |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Personal adapter documents and table | No row names a user-trained symbolic model |
| Rights check on projects | Composition V2 has no license field. Eligibility has to be supplied on the train request and checked before the copy |
| LoRA | No adapter module exists |
| Job controls | Music Transformer resume is a CLI over a full checkpoint, not a studio stop/resume/delete |
| Model picker | Hybrid generate cannot ask for a specific symbolic composer |

### Coupling risks to avoid

1. Writing `DATASET_ROOT`, or placing `PERSONAL_COMPOSER_ROOT` on the database file.
2. Starting a job from startup, `GET`, project create, autosave, or generate. `GET /personal-composers` also does not change a job status.
3. Updating Music Transformer base checkpoints, `composer.profile.v1` documents, or `tracks[].events[]` from train, stop, resume, evaluate, or delete.
4. Importing FastAPI or `project_store` from `backend/app/personal_composer/lora.py` and `trainer.py`.
5. Importing `personal_composer_store` from `ai_agents/`.
6. Logging prompts, note arrays, composition JSON, or full fingerprints. Log `snapshot_fingerprint_log_prefix` only.
7. Treating the display name as the registry id, or registering a job that is not `complete`.
8. Adding `peft`, a graph library, `composition.v5`, or a new agent id.
9. Editing `ROADMAP.md`.
10. Making an incomplete, stopped, or deleted adapter the selected composer. Those statuses stay off `status=ready`.

## Scope And Decisions

### In scope
- Manifest, snapshot index, adapter config, job, and eval schemas.
- Table `personal_composer_adapters`, filesystem artifacts, and storage-root refusal.
- Rights and owner checks, then the snapshot copy.
- Fake trainer and LoRA trainer, with stop, resume, evaluate, and delete.
- Registry rows and `options.composer_model_id`.
- Profiles-tab panel, hybrid select, fixtures, tests, and docs.

### Out of scope
- Retraining or replacing the base Music Transformer checkpoint.
- Exporting projects into `DATASET_ROOT` or running the dataset CLI.
- Changing `composer.profile.v1` soft conditioning.
- Automatic refresh when a source project later changes. The snapshot stays the trained set until the user deletes the adapter and trains again.
- Multi-GPU, quantization, or adapters other than LoRA. The schema's method literal is `lora` only.
- A new workspace tab.
- `ROADMAP.md`.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/personal_composer_schemas.py`. `extra=forbid`. This module does not import FastAPI, torch, stores, video, film, or agent modules. A payload that contains any of `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, or `composition_json` is `embedded_note_material`.

`PersonalTrainingManifestV1`. Schema version `personal.training_manifest.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `personal.training_manifest.v1` |
| `adapter_id` | `^pcomp_[0-9a-f]{16}$` |
| `display_name` | `^[A-Za-z][A-Za-z0-9._-]{0,63}$`. The acceptance value is `MyComposer-v1` |
| `registry_model_id` | `personal:` plus the adapter id |
| `project_ids` | 1..8 strings, each length 1..64, unique |
| `rights` | one `DatasetProvenance` per project id |
| `snapshot_version` | `^[0-9a-f]{64}$` |
| `base_model_id` | `fake:symbolic-tiny` or `music_transformer.tiny.v1` or `music_transformer` |
| `base_checkpoint_basename` | optional file name only, no path separators |
| `adapter_config` | `PersonalAdapterConfigV1` |
| `engine` | `fake` or `torch` |
| `created_at` | server timestamp |

`PersonalDatasetSnapshotV1`. Schema version `personal.dataset_snapshot.v1`. `items` length 1..8. Each item has `project_id`, `composition_fingerprint` (`^[0-9a-f]{64}$`), and `relative_path` matching `^[a-z0-9][a-z0-9._-]{0,80}\.json$`. No event arrays. The fingerprint is `composition_snapshot_fingerprint` of the copied document.

`PersonalAdapterConfigV1`. Schema version `personal.adapter_config.v1`.

| Field | Rule |
|-------|------|
| `method` | literal `lora` |
| `rank` | int 1..16, default 4 |
| `alpha` | int 1..64, default 8 |
| `dropout` | float 0..0.2, default 0 |
| `target_modules` | frozen list `qkv`, `out_proj` |
| `freeze_base` | literal true |
| `max_steps` | int 1..50. Fake acceptance uses 1. The torch test uses 2 |

`PersonalTrainingJobV1`. Schema version `personal.training_job.v1`. Status is `running`, `stopped`, `complete`, `failed`, or `deleted`. Fields include `step`, `max_steps`, `error_code` (optional, a short token), and the manifest. Status `complete` is the only status the registry marks `ready`.

`PersonalEvalV1`. Schema version `personal.eval.v1`. Fields: `adapter_id`, `engine`, `step`, `loss` (optional float), `token_accuracy` (optional float). Fake eval sets both metrics null and `engine` to `fake`. This report is not a musical-quality score.

**2. Identity**

The server assigns `adapter_id` with `pcomp_` + `secrets.token_hex(8)`. The display name is unique among rows whose status is not `deleted`. A duplicate is `personal_name_taken` (HTTP 409). The registry id is never the display name.

**3. Table**

`personal_composer_adapters` in revision `20261002_0022`.

| Column | Rule |
|--------|------|
| `id` | primary key |
| `schema_version` | `personal.training_job.v1` |
| `display_name` | text |
| `status` | the job status enum |
| `registry_model_id` | text |
| `engine` | `fake` or `torch` |
| `base_model_id` | text |
| `snapshot_version` | 64 hex |
| `step` | integer |
| `max_steps` | integer |
| `manifest_json` | the manifest, without composition documents |
| `eval_json` | nullable |
| `owner_actor_id` | nullable. Null when collaboration is off |
| `error_code` | nullable short token |
| `created_at`, `updated_at` | text timestamps |

No foreign key to `projects`. Deleting a source project does not delete the adapter or the snapshot. There is no pitch column.

**4. Files**

`PERSONAL_COMPOSER_ROOT` defaults to `personal_composers` under the process working directory. Settings live in `backend/app/personal_composer_settings.py`. `reject_storage_root` runs before the first write. A rejected root is HTTP 500 `personal_storage_root_rejected` and no row is inserted.

```text
PERSONAL_COMPOSER_ROOT/<adapter_id>/manifest.json
PERSONAL_COMPOSER_ROOT/<adapter_id>/snapshot/index.json
PERSONAL_COMPOSER_ROOT/<adapter_id>/snapshot/<project_file>.json
PERSONAL_COMPOSER_ROOT/<adapter_id>/adapter.fake.json    # fake engine
PERSONAL_COMPOSER_ROOT/<adapter_id>/adapter.pt           # torch engine
PERSONAL_COMPOSER_ROOT/<adapter_id>/step.json
```

`adapter.fake.json` is `personal.adapter.fake.v1` and stores the config, base model id, and snapshot version. It does not store tensors. `adapter.pt` stores only the LoRA state dict plus the config card. Log basenames, never absolute paths that include a home directory when a basename is enough.

**5. Request and errors**

`POST /personal-composers` body: `display_name`, `project_ids`, `rights` (map of project id to provenance fields), optional `rank`, `alpha`, `max_steps`.

| Code | When | HTTP |
|------|------|------|
| `personal_projects_required` | `project_ids` is empty or missing | 422 |
| `personal_project_missing` | a project id is not in the store | 404 |
| `collaboration_role_denied` | collaboration is on and the member's role lacks `train_adapter` | 403 |
| `collaboration_not_member` | collaboration is on and the actor has no membership on that project | 403 |
| `personal_rights_refused` | provenance is invalid or `compute_train_eligible` is false | 422 |
| `personal_snapshot_empty` | a selected score has no note events | 422 |
| `personal_name_taken` | display name belongs to a non-deleted row | 409 |
| `personal_name_invalid` | display name fails the pattern | 422 |
| `personal_too_many_projects` | more than 8 ids | 422 |
| `personal_torch_unavailable` | fake mode is off and torch does not import | 503 |
| `personal_not_stoppable` | stop when status is not `running` | 409 |
| `personal_resume_unavailable` | resume when status is not `stopped` or `failed`, or `step.json` is missing | 409 |
| `personal_busy` | evaluate or a second train control while `running` | 409 |
| `personal_not_found` | unknown adapter id | 404 |
| `embedded_note_material` | forbidden keys in the JSON body | 422 |

Order of the start handler: parse the body, check the display name, resolve collaboration, load each project, run the rights gate, refuse an empty score, then resolve the engine. Only after those checks insert the row and copy files. A rights failure must not create `snapshot/`.

**6. Trainer**

`backend/app/personal_composer/lora.py` imports torch lazily through the same pattern as `music_transformer/model.py`. It builds `MusicTransformerLM`, sets `requires_grad=False` on every base parameter, and replaces `qkv` and `out_proj` with a module that computes `base(x) + (alpha / rank) * B(A(x))`. A and B are the only trainable parameters.

`backend/app/personal_composer/trainer.py` does not import FastAPI. `run_personal_training(adapter_id, *, read_status)` loops `step` to `max_steps`. At the start of each step it reads status through a new `get_connection()` opened in that thread. Do not pass the request connection in. `stopped` or `deleted` returns without another step. After each step it writes `step.json` and the adapter file. Fake engine writes `adapter.fake.json` and does not tokenize. Torch engine encodes the snapshot with the existing tokenizer and runs `max_steps` of next-token loss on those ids, on CPU unless `MUSIC_TRANSFORMER_DEVICE` is already `cpu`. If a checkpoint path is configured and resolves, the base reference is `music_transformer` and that file's basename. Otherwise the torch base reference is `music_transformer.tiny.v1` built from `tiny_test_config`. The fake base reference is `fake:symbolic-tiny`.

`backend/app/services/personal_composer_service.py` stores the thread handle before `POST` returns. When `PERSONAL_COMPOSER_FAKE=1`, that call joins the thread and the response status is `complete`. A torch start returns `running` without joining. A process start marks a `running` row `failed` with `error_code=personal_interrupted` when this process has no thread for it. `GET` does not perform that sweep. The user may then resume if `step.json` exists.

**7. Evaluate and delete**

`POST /personal-composers/{id}/evaluate` is allowed for `complete` or `stopped`. Fake eval writes `personal.eval.v1` with null metrics. Torch eval reports loss and token accuracy on the snapshot and does not sample notes into a project. `DELETE` sets status `deleted`, removes the adapter directory, and drops the registry id. A `running` delete writes `stopped` first and joins the thread, then deletes files.

**8. Discovery and generate**

`backend/app/ai_runtime/runtimes/personal_composer.py` lists rows with status `complete` and returns one `ModelDescriptor` each: runtime `personal_composer`, capability `symbolic_composer`, operation `GENERATE_COMPOSER`, locality `local`, display name from the row. `build_registry_from_env` attaches those descriptors. A missing table or a database that cannot be opened logs a warning and registers none of them. `GET /ai/models` already calls `reload_registry()` before `list_models`, so discovery sees a just-finished job. Do not reload inside `list_models`. `default_operation_routes` skips runtime `personal_composer` when choosing the implicit composer, the same way it skips runtime `plugin`.

`LLMGenerationOptions.composer_model_id` is optional, max length 160. `_resolve_hybrid_stage_models` passes `ModelSelectionInput(model_id=options.composer_model_id)` only when that field is set. When it is unset, the call stays `ModelSelectionInput()`. `_symbolic_generate` forwards the stored composer id when its runtime is `plugin` or `personal_composer`. `generate_symbolic_composition` routes a `personal:` id to the personal runtime. Fake adapters call `generate_fake_symbolic_composition` and set `SymbolicGenerateResult.model_id` to the personal registry id. Torch adapters load the LoRA onto the recorded base and use the existing sample-and-decode path. A personal id that is not `complete` is `ModelUnavailableError`.

**9. UI**

`frontend/src/components/PersonalComposerPanel.jsx` renders under `ComposerProfilesPanel` on the Profiles tab. It lists projects from the existing project list, a provenance status select, and the fields `DatasetProvenance` requires for that status. The train button stays disabled until every selected project is eligible and the display name matches the pattern. Stop, resume, evaluate, and delete call the matching routes. Opening the tab, or an empty selection, does not POST.

`MusicGenerator.jsx` renders a symbolic-composer `<select>` when the pipeline is `hybrid_plan_symbolic`. The blank option omits `composer_model_id`. `MyComposer-v1` is the option label and the registry id is the value. `buildLlmRequest` in `frontend/src/utils/llmGenerateRequest.js` copies that id onto `options.composer_model_id`. `frontend/src/utils/personalComposerForm.js` is the pure helper the node test covers. Eligibility matches `DatasetProvenance`: `user_owned` only with attestation, `verified_redistributable` only with a license and a source, `public_domain` only with a source, and `unknown` or `restricted` never eligible.

**10. Fixtures**

`backend/app/fixtures/personal_composer_etude.v2.json` and `backend/app/fixtures/personal_composer_sketch.v2.json` are canonical `composition.v2` scores: one track, `4/4`, 120 bpm, 480 ticks per quarter, one bar, three notes as named in the acceptance. They contain no provenance object. Provenance arrives only on the train request.

## Tasks

### Phase 1: Contract and storage
- [x] Task 1: Add personal-composer schemas and settings
- [x] Task 2: Add the adapter table and file store
- [x] Task 3: Gate training on explicit rights and write the snapshot

### Phase 2: Training job
- [x] Task 4: Implement LoRA and the fake step loop
- [x] Task 5: Add stop, resume, evaluate, and delete

### Phase 3: Selection
- [x] Task 6: Expose the job HTTP API
- [x] Task 7: Register the adapter and accept it as the hybrid composer

### Phase 4: Studio and docs
- [x] Task 8: Add the Profiles section and the hybrid model select
- [x] Task 9: Document the opt-in training path

## Commit Plan
- **Commit 1** (after tasks 1-3): "feat: record a rights-checked snapshot for a personal composer"
- **Commit 2** (after tasks 4-6): "feat: train, stop, resume, evaluate, and delete a personal adapter"
- **Commit 3** (after tasks 7-9): "feat: select a personal adapter as an optional symbolic composer"

### Task 1: Add personal-composer schemas and settings

Create `backend/app/personal_composer_schemas.py` and `backend/app/personal_composer_settings.py` with the documents and defaults in the architecture decisions. Env keys: `PERSONAL_COMPOSER_ROOT` (default `personal_composers`), `PERSONAL_COMPOSER_FAKE` (default off), `PERSONAL_COMPOSER_MAX_PROJECTS` (default 8), `PERSONAL_COMPOSER_MAX_STEPS` (default 50, clamped to 1..50). Fake mode does not change the schema. Reject a settings root through `reject_storage_root` in a helper the service will call later. Do not open SQLite in the settings module.

`backend/tests/test_personal_composer_schemas.py` accepts a manifest whose display name is `MyComposer-v1`, rank 4, targets `qkv` and `out_proj`, and `freeze_base` true. It rejects a manifest that embeds an `events` key, a display name `my composer`, rank 32, and a `user_owned` provenance with `user_owned_attested` false. The settings test points `PERSONAL_COMPOSER_ROOT` at a `DATASET_ROOT` path and expects `StorageRootError` reason `dataset_root`.

LOGGING: INFO when settings load, with fake flag, max projects, max steps, and the root basename. DEBUG on schema rejection with the field name and the error code, not the payload. Levels follow `LOG_LEVEL`.

### Task 2: Add the adapter table and file store

Add `backend/app/db/alembic/versions/20261002_0022_personal_composers.py` with `down_revision` `20261002_0021` and the columns in the architecture decisions. Add `backend/app/services/personal_composer_store.py` with insert, get, list, and status update. Insert uses one connection and returns the row. List omits `deleted`. Update refuses a transition out of `deleted`. Manifest JSON is stored as text and parsed back through the schema.

`backend/tests/test_personal_composer_store.py` upgrades a temporary database, inserts `MyComposer-v1`, and reads the registry id and snapshot version back. A second insert of the same display name raises a store error the router will map to `personal_name_taken`. Updating a deleted row does not change `status`.

Update `backend/tests/test_ai_agents_architecture.py` so the persistence import ban includes `from app.services.personal_composer_store`. Add `personal_composers/` to `.gitignore`. `*.pt` is already ignored. The snapshot JSON must not be committable.

LOGGING: INFO on insert and status change with `adapter_id`, `status`, and `engine`. Do not log `manifest_json` or project titles. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Gate training on explicit rights and write the snapshot

Add `backend/app/services/personal_composer_rights.py`. It builds `DatasetProvenance` per project and calls `compute_train_eligible`. Any false result is `personal_rights_refused` and includes the project id in the error details, not the score. Add `backend/app/services/personal_composer_snapshot.py`. It loads each composition from the project store, rejects a score with zero events, copies canonical JSON into the snapshot directory, and writes `personal.dataset_snapshot.v1` whose version is the sha256 of the canonical index. Call `reject_storage_root` before `mkdir`. This module does not start a thread and does not import torch.

`backend/tests/test_personal_composer_rights.py` loads the Etude and Sketch fixtures as two projects, accepts `user_owned` plus attestation, and asserts both snapshot files exist and the index contains no `pitch` key. A third project with status `unknown` raises `personal_rights_refused` and the adapter directory is absent. An empty `project_ids` list raises `personal_projects_required` before any project load. The start path calls `enforce_current(project_id, "train_adapter")` for each selected project. Add `train_adapter` only to the owner action set in `backend/app/services/collaboration_permissions.py`. Update `backend/tests/test_collaboration_permissions.py` so `ACTIONS` includes `train_adapter`, the owner still allows every action, and the editor deny list includes `train_adapter`. When collaboration is enabled, an editor receives `collaboration_role_denied` and a non-member receives `collaboration_not_member`. An owner passes. When collaboration is disabled, `enforce_current` returns before the membership lookup, and the attestation is still required.

LOGGING: INFO when a snapshot is written, with `adapter_id`, project count, and `snapshot_fingerprint_log_prefix`. WARNING on rights refusal with the code and project id. Do not log note text or the full fingerprint. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 4: Implement LoRA and the fake step loop

Add `backend/app/personal_composer/lora.py` and `backend/app/personal_composer/trainer.py` as locked in the trainer decision. Add `backend/app/services/personal_composer_service.py` with `start_personal_composer`. It runs the rights and snapshot steps, inserts `running`, stores the thread handle, and then starts the thread. Each step opens its own `get_connection()`. The thread sets `complete` on the last step or `failed` with a short `error_code` on an exception. When `PERSONAL_COMPOSER_FAKE=1`, `start_personal_composer` joins the thread before it returns, so the caller sees `complete`. A torch start returns `running` without joining. Fake mode never imports torch. If fake mode is off and torch is missing, return `personal_torch_unavailable` before the snapshot copy.

`backend/tests/test_personal_composer_fake_train.py` starts from the two fixtures in fake mode and asserts status `complete`, `step == 1`, `adapter.fake.json` schema `personal.adapter.fake.v1`, base model id `fake:symbolic-tiny`, and that `app.music_transformer.train.train_experiment` was not called. `backend/tests/test_personal_composer_lora.py` skips unless torch imports. It builds `tiny_test_config`, runs two steps on the fixture snapshot, and asserts every base parameter tensor is equal to a clone taken before the steps and at least one LoRA tensor is not all zeros. It also asserts `freeze_base` left the base parameter set with `requires_grad` false.

LOGGING: INFO at each step with `adapter_id`, `step`, `max_steps`, and `engine`. INFO on complete. ERROR on failure with the error code and exception class, not the composition. Do not log tensor values. Levels follow `LOG_LEVEL`.

Depends on Task 3.

### Task 5: Add stop, resume, evaluate, and delete

Extend the service with `stop`, `resume`, `evaluate`, and `delete` using the status rules in the architecture decisions. The trainer reads status from the store at each step boundary, so stop does not need a sleep. Resume loads `step.json` and continues the remaining steps with the same snapshot. Evaluate writes `eval_json` and does not insert notes into a project. Delete removes the directory after the status write. Process startup marks an orphaned `running` row `failed` with `personal_interrupted` when this process has no thread for that id. `GET` does not write that status.

`backend/tests/test_personal_composer_control.py` uses fake mode and `max_steps` 2. It calls the trainer function once with a status reader that returns `stopped` after step 1, asserts `step == 1` and the snapshot files remain, then resumes and asserts `step == 2` and status `complete`. Evaluate then returns `engine=fake` and null loss. Delete removes the directory and a following get raises `personal_not_found`. Resume of a `complete` row raises `personal_resume_unavailable`. A startup sweep on a `running` row with no live thread sets `personal_interrupted`. A list call on that row does not change `status`.

LOGGING: INFO on stop, resume, evaluate, and delete with `adapter_id` and the resulting status. WARNING on an illegal transition with the code only. Levels follow `LOG_LEVEL`.

Depends on Task 4.

### Task 6: Expose the job HTTP API

Add `backend/app/routers/personal_composer.py` and register it in `backend/app/main.py` next to the other routers. Routes:

| Method | Path | Effect |
|--------|------|--------|
| POST | `/personal-composers` | start |
| GET | `/personal-composers` | list non-deleted jobs |
| GET | `/personal-composers/{adapter_id}` | one job |
| POST | `/personal-composers/{adapter_id}/stop` | stop |
| POST | `/personal-composers/{adapter_id}/resume` | resume |
| POST | `/personal-composers/{adapter_id}/evaluate` | eval report |
| DELETE | `/personal-composers/{adapter_id}` | delete |

Map the error codes to the HTTP statuses in the request table. Collaboration denials stay `collaboration_role_denied` and `collaboration_not_member` from `enforce_current`. Responses are `personal.training_job.v1` or `personal.eval.v1`. They must not include composition documents. GET list filters by `owner_actor_id` when collaboration is on and does not update any row. With `PERSONAL_COMPOSER_FAKE=1`, POST joins the worker and the body status is `complete`.

`backend/tests/test_personal_composer_api.py` posts the acceptance body for Etude and Sketch and reads `complete` from that response, plus the display name. A GET list before any POST returns `[]` and leaves the row count at 0. A post whose third project is `unknown` returns 422 `personal_rights_refused`. Stop on a completed job returns 409 `personal_not_stoppable`.

LOGGING: INFO per route with method, `adapter_id` when present, and status code. Do not log the request body. Levels follow `LOG_LEVEL`.

Depends on Task 5.

### Task 7: Register the adapter and accept it as the hybrid composer

Add `personal_composer` to `ModelRuntimeId`. Add `backend/app/ai_runtime/runtimes/personal_composer.py` and attach it from `build_registry_from_env` in `backend/app/ai_runtime/bootstrap.py`. A missing `personal_composer_adapters` table, or a database that cannot be opened, logs a warning and leaves the other descriptors in place. Do not reload inside `list_models`. `GET /ai/models` already calls `reload_registry()` first. In `default_operation_routes`, skip runtime `personal_composer` the same way the loop skips runtime `plugin`. Extend `LLMGenerationOptions` with `composer_model_id`. Change `_resolve_hybrid_stage_models` only as the architecture decision describes. In `_symbolic_generate`, forward `state["composer_model_id"]` when `get_model` reports runtime `plugin` or `personal_composer`. Extend `generate_symbolic_composition` so a `personal:` id uses the adapter engine and records that id on the result. An omitted composer id still uses the empty `ModelSelectionInput` path.

`backend/tests/test_personal_composer_generate.py` completes the fake acceptance job, then asserts `GET /ai/models` includes the registry id, display name `MyComposer-v1`, and capability `symbolic_composer`. A hybrid request with that `options.composer_model_id` returns a composition and provenance `model_id` equal to the registry id. A request that omits `composer_model_id` still resolves through the empty `ModelSelectionInput` path, and the implicit route is not the personal id. A stopped adapter is not in the ready list. Reloading the registry with no personal table still returns `fake:symbolic-tiny`. `backend/tests/test_ai_agents_architecture.py` stays green with the store import ban.

LOGGING: INFO when a personal descriptor is registered, with registry id and status. INFO on generate with the composer model id and engine. Do not log the prompt. Levels follow `LOG_LEVEL`.

Depends on Task 6.

### Task 8: Add the Profiles section and the hybrid model select

Add `frontend/src/utils/personalComposerForm.js`, `frontend/src/api/personalComposerApi.js`, and `frontend/src/components/PersonalComposerPanel.jsx`. Mount the panel under `ComposerProfilesPanel` on the Profiles tab in `ComposerWorkspace.jsx`. The form matches the UI decision: train stays disabled until the helper says the selection is eligible and the name matches. Eligibility follows `DatasetProvenance`: `user_owned` only with attestation, `verified_redistributable` only with a license and a source, `public_domain` only with a source, and `unknown` or `restricted` never eligible. In `MusicGenerator.jsx`, add the symbolic select for `hybrid_plan_symbolic`. `buildLlmRequest` in `frontend/src/utils/llmGenerateRequest.js` sets `options.composer_model_id` from that selection and omits the field when the selection is blank. Extend `frontend/src/utils/llmGenerateRequest.test.js` for that field. Add a node test `frontend/src/utils/personalComposerForm.test.js` that rejects an empty selection, rejects `unknown`, rejects `verified_redistributable` without a license, accepts both fixtures with `user_owned` attested and the name `MyComposer-v1`, and maps a complete job to an option whose label is the display name and whose value is the registry id.

LOGGING: `console.debug` on list load with the adapter count, and on train click with the project count and whether the form was eligible. Do not log composition JSON. The browser has no `LOG_LEVEL`; keep these on the debug channel.

Depends on Task 7.

### Task 9: Document the opt-in training path

Add `docs/personal-symbolic-composer.md`. State that training starts only from the explicit POST, that fake mode does not update weights, that LoRA freezes the base, that the snapshot is not `DATASET_ROOT`, and that `MyComposer-v1` is a display name. Include the env keys and the stop, resume, evaluate, and delete routes. Update `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, and `.env.example` with the new modules and env keys. Add a `train_adapter` row to the action table in `docs/collaboration.md`. The owner is allowed. Editor, commenter, and viewer are not. Do not edit `ROADMAP.md`.

The doc page is the mandatory docs checkpoint for `/aif-implement`.

LOGGING: none. This task edits documentation only.

Depends on Task 8.
