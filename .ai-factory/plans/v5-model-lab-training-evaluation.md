# Implementation Plan: V5 Model Lab for Training and Evaluating Musical AI Models

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-04

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: New workspace **Lab** tab (`ModelLabPanel.jsx`) with a staged wizard — Dataset → Tokenizer → Architecture → Training config → Run → Evaluation → Model registry — plus experiment list, metrics, resource usage, checkpoints, generated eval examples, listening comparisons, and multi-experiment compare. Opening Lab lists catalog/experiments only; never starts train, never mutates `composition.v2`, never writes `DATASET_ROOT`. Personal Composer stays on Profiles (LoRA on selected projects). No piano-roll redesign
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-04 (`/aif-improve`). **Collab C** studio actor gate (not project `enforce_current`); single id `mtlab_<hex>` / `lab:{id}`; Lab create freezes eval/listening off by default; rights gate always runs with fixture `rights/index.jsonl`; no ship-1 resume (`stopped` terminal); GET catalog/list when disabled; always include router, never `/ready`; fake register never `load_checkpoint`; torch Lab generate uses personal-style path under `MODEL_LAB_ROOT` (no free-form user path); startup orphan sweep; Task 8 depends on Task 4
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). First unchecked ROADMAP items (performance conductor / spatial) are orthogonal and already planned/shipped; this plan documents a new franchise milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`
- Scope: safe research HTTP + SPA that wraps existing offline V3/V4 training infrastructure (dataset versions, tokenizer contracts, Music Transformer experiment train/eval/listen/compare, checkpoint cards, rights gate, `/ai/models` discovery). Durable **`model.lab.experiment.v1`** index records (dataset version, tokenizer, model config, seed, checkpoint, runtime, evaluation version). Permissions + resource limits. Fake CI path. Never invent `composition.v5`. Never expose arbitrary shell / user argv / remote command execution. `ai_agents/` must not import the Model Lab store or settings. Dataset **build** and tokenizer **CLI encode** stay offline; Lab only selects existing corpus versions and typed tokenizer presets. Personal LoRA training is out of Lab (already Profiles)
- Predecessors: `.ai-factory/plans/v3-symbolic-music-transformer.md` + reproducible training/eval (shipped; offline experiment FS), `.ai-factory/plans/v5-personal-symbolic-composer-adaptation.md` (shipped; Profiles LoRA job pattern), `.ai-factory/plans/v5-dataset-rights-governance-registry.md` (shipped; train rights hard-fail + `model.data.provenance.manifest.v1`), offline `dataset.pipeline.v1` / `tokenizer.v1` CLIs (shipped)

## Roadmap Linkage
Milestone: "V5 Model Lab for training and evaluating musical AI models"
Rationale: Corpus Music Transformer train/eval/listen/compare and dataset/tokenizer contracts already exist as offline CLIs, but there is no safe studio surface to launch a small configured experiment, inspect metrics/resources/checkpoints/listening outputs, compare runs, and register a successful checkpoint as a usable `symbolic_composer` — without shell. First unchecked ROADMAP items (performance / spatial) are orthogonal. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns appending it. This plan does not edit `ROADMAP.md`.

## Goal

Expose existing V3/V4 training infrastructure through a **safe research UI** so a user can walk:

```text
Dataset → Tokenizer → Architecture → Training config → Run → Evaluation → Model registry
```

…launch a **small** configured training experiment, inspect loss / validation metrics / resource usage / checkpoints / generated evaluation examples / listening comparisons, compare multiple experiments, and **explicitly register** a successful checkpoint as a usable model on `GET /ai/models`.

Ship:

1. Non-playable **`model.lab.experiment.v1`** (SQLite index + pointer into MT experiment FS) recording dataset version, tokenizer expectation, model/train config digests, seed, checkpoint refs, runtime resource summary, evaluation version, and status.
2. Opt-in HTTP job surface (`MODEL_LAB_ENABLED`, default off) that wraps `music_transformer.experiment.v1` train/eval/listen/compare behind typed DTOs — **no shell**, no user argv, no `subprocess` with a shell string.
3. Read-only dataset-version catalog (under `DATASET_ROOT`) and closed tokenizer/architecture presets with bounded field edits.
4. Permissions + resource limits (concurrent runs, max steps/batch for Lab launches, experiment retention, CPU-default device policy, storage-root refusal).
5. Lab tab UI + fake engine for CI acceptance + docs.

Acceptance: With `MODEL_LAB_ENABLED=1` and `MODEL_LAB_FAKE=1`, the user selects a fixture dataset version (or Lab-provided tiny corpus stub with train-eligible `rights/index.jsonl`), tokenizer preset `core`, architecture preset `tiny_lab`, seed `42`, and a Lab-capped train config (`max_steps` ≤ Lab cap). `POST /model-lab/experiments` completes one experiment (fake joins before response). The experiment record stores `id=mtlab_<16hex>`, dataset_version_id, tokenizer expectation (version + vocab_hash prefix), architecture/train digests, seed `42`, at least one checkpoint ref (card/placeholder — not a loadable `.pt`), runtime summary (wall_ms, device=`fake`), and evaluation version `music_transformer.eval_report.v1` stamped from the fake stub. Metrics endpoint returns loss + val metrics rows. Explicit `POST …/evaluate` returns symbolic metrics with `musical_quality_claim=false` and at least one generated example id. Listening endpoint returns fixed-prompt generation digests. `POST /model-lab/experiments/{id}/register` with a checkpoint step marks the model ready; `GET /ai/models?capability=symbolic_composer&status=ready` includes `lab:mtlab_<16hex>` with the user display name. Hybrid generate with that `options.composer_model_id` returns provenance `model_id` equal to the lab id (fake path — never `load_checkpoint`). A second experiment with a different seed can be compared via `POST /model-lab/compare` returning a multi-run report (no quality winner). With `MODEL_LAB_ENABLED=0`, every **mutating** route returns `model_lab_disabled`; `GET /model-lab/status` is 200 and catalog/list GETs remain readable. Opening the Lab tab never starts training and never mutates `composition.v2`. A request body that embeds `events` / shell / absolute weight paths outside Lab-owned roots is refused. Status lives on `GET /model-lab/status` only (not `/ready`).

```text
existing DATASET_ROOT version (read-only select) + rights/index.jsonl
        + tokenizer preset / bounded config
        + architecture preset / bounded MusicTransformerConfigV1
        + train config (Lab-capped; eval/listening off by default) + seed
        │
        ▼  POST /model-lab/experiments  (enabled + rights gate always)
model.lab.experiment.v1  (SQLite id=mtlab_<hex>)  ──►  MODEL_LAB_ROOT/<id>/
        │ wraps train_experiment / explicit eval / listen (fake or torch)
        │ metrics.jsonl · checkpoints · eval/ · listening/
        │ model.data.provenance.manifest.v1
        ▼
inspect metrics / resources / checkpoints / examples / listening
        │
        ├─ POST /model-lab/compare  (2..N experiment ids)
        └─ POST …/register  →  GET /ai/models  lab:mtlab_<hex>
              fake → fake:symbolic-tiny + lab provenance
              torch → load under MODEL_LAB_ROOT/<id>/checkpoints/… (no free-form path)
```

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**. **Model Lab** = opt-in research control plane over corpus MT experiments — distinct from **Personal Composer** (Profiles LoRA on selected projects) and from **Composer Profiles** (soft prefs). **Experiment record** = durable `model.lab.experiment.v1` index row + FS layout already defined by `music_transformer.experiment.v1` / `ExperimentPaths`. **Dataset version** = existing `dataset_version_id` under `DATASET_ROOT` (Lab selects; does not build). **Tokenizer** = `tokenizer.v1` expectation (`expected_tokenizer_version` + `vocab_hash`) from a closed preset or bounded `tokenizer.config.v1`. **Architecture** = `MusicTransformerConfigV1` from closed presets (`tiny_lab`, optional `small_lab`) with Lab-tightened ceilings. **Training config** = `MusicTransformerTrainConfigV1` with Lab max-steps/batch caps stricter than CLI. **Checkpoint** = step-named artifact (+ card) under experiment `checkpoints/` — fake may be card-only; torch uses a real `.pt`. **Runtime** = device, wall time, peak mem (when available), tokens/sec summary — never a shell transcript. **Evaluation version** = schema id of the symbolic eval report (`music_transformer.eval_report.v1`) plus optional listening set digest. **Register** = explicit promote of one checkpoint into `/ai/models` as runtime `model_lab`. **Fake engine** = `MODEL_LAB_FAKE=1` writes the same documents/metrics skeleton without torch. **Shell refusal** = no HTTP field that becomes a command line; only typed fields mapped to existing Python APIs. Symbolic metrics **≠** musical quality (`musical_quality_claim` always false). **Collab C** = studio actor gate for Lab mutates when collaboration is on (see Part L).

## Approach Evaluation (locked)

### Part A — What Lab trains

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only wrap Personal Composer LoRA jobs** | Already has HTTP/UI | User asked for dataset→tokenizer→architecture→registry research workflow; personal path has no corpus dataset/tokenizer stages | **Reject** as sole surface |
| **B. New neural audio / MusicGen train UI** | Popular | Out of V3/V4 symbolic stack; licenses/weights different; not the existing MT experiment contracts | **Reject** for ship-1 |
| **C. Wrap Music Transformer `music_transformer.experiment.v1` train/eval/listen/compare over an existing dataset version + tokenizer expectation; Personal Composer stays Profiles** | Reuses shipped offline contracts; matches acceptance | Need job API + index + fake | **Accepted** |

### Part B — Where experiment state lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only scan FS under `MUSIC_TRANSFORMER_EXPERIMENT_ROOT` with no SQLite** | Zero migration | No CAS status, hard to list/compare safely, race on register | **Reject** as sole store |
| **B. Store weights/blobs in `PROJECT_DB_PATH`** | One DB | Violates offline corpus separation; huge blobs | **Reject** |
| **C. SQLite `model_lab_experiments` index in `PROJECT_DB_PATH` + artifact FS under `MODEL_LAB_ROOT` (default = `MUSIC_TRANSFORMER_EXPERIMENT_ROOT` layout via `ExperimentPaths`); never write `DATASET_ROOT`; `reject_storage_root` on Lab root** | Same pattern as personal/neural jobs; reuses MT FS contracts | Dual surface | **Accepted** |

### Part C — How train runs (no shell)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. SPA posts a shell string / argv array executed by the backend** | Flexible | Explicitly forbidden; RCE surface | **Reject** |
| **B. Backend `subprocess.run(["python","-m","app.music_transformer.cli", …])` built from free-form user strings** | Reuses CLI entry | Still argv injection risk; harder to fake; couples HTTP to CLI parser | **Reject** |
| **C. Typed service calls into `train_experiment` / `evaluate_checkpoint` / `run_listening_set` / `compare_experiments` (same modules CLI uses). Background thread + status polling like personal composer. Fake engine skips torch. Optional future process isolation is out of ship-1** | No shell; one code path; CI-friendly | In-process torch can contend with API; mitigated by caps + default-off + fake | **Accepted** |

### Part D — Dataset and tokenizer stages in the UI

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Trigger full `dataset.cli build` and arbitrary tokenizer encode from the SPA** | One-stop | Long offline ops; write `DATASET_ROOT` from FastAPI; rights/ingest complexity | **Reject** for ship-1 |
| **B. Skip dataset/tokenizer UI; hardcode fixture paths** | Fast | Fails the stated workflow and acceptance “configured” experiment | **Reject** |
| **C. Read-only catalog of existing dataset versions (`manifest.json` digests/names) + closed tokenizer presets (`core` / `core_harmony`) with bounded config fields; Lab never builds corpora; MT rights gate always runs on the selected version dir** | Matches “select then train”; keeps build CLI | Operator must have a version dir (CI uses fixture stub under temp root) | **Accepted** |

### Part E — Architecture / train config exposure

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Free-form JSON pasted into any Pydantic model without Lab caps** | Flexible | Easy to OOM; bypasses “small experiment” | **Reject** |
| **B. Closed presets only, zero field edits** | Safest | Blocks legitimate tiny tuning (steps/lr/seed) | **Reject** |
| **C. Closed architecture presets (`tiny_lab` = `tiny_test_config` shape; optional `small_lab` with Lab ceilings) + editable train fields clamped by `MODEL_LAB_MAX_STEPS` / `MODEL_LAB_MAX_BATCH` / device policy (default `cpu`)** | Safe research knobs | Preset list must stay small | **Accepted** |

### Part F — Model registry promote

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Auto-register every completed checkpoint** | Convenient | Pollutes `/ai/models`; user asked to register a successful checkpoint explicitly | **Reject** |
| **B. Overwrite the single env `MUSIC_TRANSFORMER` checkpoint path** | Reuses existing descriptor | Breaks CLI default; no multi-registered labs; implicit | **Reject** |
| **C. Explicit `POST …/register` → runtime `model_lab`, id `lab:{id}`, capability `symbolic_composer`; only `complete` + existing checkpoint; implicit composer route skips `model_lab` (same as personal/plugin); hybrid `options.composer_model_id` accepts it. Fake generate never `load_checkpoint`; torch loads only under `MODEL_LAB_ROOT/<id>/checkpoints/`** | Matches personal pattern; opt-in; avoid allowlist RCE | Generate path needs Lab-specific loader | **Accepted** |

### Part G — UI placement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Profiles subsection beside Personal Composer** | No new tab | Conflates personal LoRA with corpus research wizard; workflow stages do not fit Profiles | **Reject** |
| **B. No SPA; HTTP only** | Smaller | Acceptance is a user launching/inspecting/registering in the studio | **Reject** |
| **C. New Lab tab with staged wizard + experiment browser/compare/register; open = list/catalog only** | Matches research product; clear separation from Profiles | Tab count grows | **Accepted** |

### Part H — Multi-experiment compare

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only reuse CLI pairwise once** | Minimal | UI “multiple experiments” needs N-way | **Reject** as sole |
| **B. Claim a musical quality winner** | Flashy | Violates symbolic-metrics honesty | **Reject** |
| **C. `POST /model-lab/compare` with 2..N experiment ids → `model.lab.compare.v1` side-by-side metrics/eval/listening digests + equality flags; build from existing `compare_experiments` pairwise edges; `musical_quality_claim=false`** | Honest; supports multi-select UI | Matrix size capped (e.g. ≤ 8) | **Accepted** |

### Part I — Permissions and resource limits

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always on, unlimited steps** | Simple | Dangerous on a studio host | **Reject** |
| **B. Flag default off; Lab caps; max concurrent 1; retention/GC; Collab C studio actor gate when collab on; storage-root refusal; status not on `/ready`** | Matches ExecutionNode/personal caution | Operators must enable | **Accepted** |

### Part J — Fake / CI without torch

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require torch in default pytest** | Real weights | Core deps exclude torch | **Reject** |
| **B. Docs-only acceptance** | Cheap | Not enforceable | **Reject** |
| **C. `MODEL_LAB_FAKE=1` completes inline (join before response) writing experiment FS skeleton + metrics + fake checkpoint card (no tensors) + eval/listening stubs; registered fake models generate via `fake:symbolic-tiny` with lab id provenance — never `load_checkpoint`. Optional torch test `importorskip` for one real tiny step when installed** | Same as personal | Fake inference does not apply trained weights. Document that | **Accepted** |

### Part K — Locked formulas and refuse codes

1. **Enabled (mutate):** mutating Lab routes require `MODEL_LAB_ENABLED` truthy; else `model_lab_disabled` (HTTP 503).
2. **Enabled (read):** `GET /model-lab/status` always 200. `GET /datasets`, `/presets`, `/experiments`, detail, metrics, checkpoints, listening are allowed when disabled so the Lab tab can show “enable to train”.
3. **Concurrent runs:** at most `MODEL_LAB_MAX_CONCURRENT` (default 1) with status `running`; else `model_lab_busy`.
4. **Step/batch caps:** Lab POST clamps `train.steps` ≤ `MODEL_LAB_MAX_STEPS` (default 64, hard max 512) and `train.batch_size` ≤ `MODEL_LAB_MAX_BATCH` (default 8, hard max 64). CLI remains freer.
5. **Device policy:** default `cpu`; `cuda`/`rocm`/`mps` only when `MODEL_LAB_ALLOW_ACCELERATOR=1` and the device resolves; else fall back or refuse with `model_lab_device_refused`.
6. **Dataset select:** path must resolve under `DATASET_ROOT` (or packaged Lab fixture root in tests/fake) to a dir with `manifest.json` + `dataset_version_id` match; path escape → `model_lab_dataset_refused`. Lab never writes into that tree.
7. **Rights (fail-closed):** Lab create **always** sets `dataset_dir` to the selected version dir and calls `verify_train_paths_against_rights` before any experiment FS write (fake and torch). Fixture **must** include train-eligible `rights/index.jsonl`. Refuse `rights_train_refused`. No Lab unsafe skip / override.
8. **Eval/listen default:** create freezes `eval.enabled=false` and `listening.enabled=false` unless the create body explicitly opts in to train-inline. Fake create stamps `evaluation_version` from the fake stub. `POST …/evaluate` is the explicit re-run (last-write-wins). Avoid silent double-run.
9. **Stop / no resume:** `POST …/stop` cooperatively stops a running job. `stopped` is terminal except delete. **No** Lab resume route in ship-1 (CLI `resume_experiment` / personal resume remain separate). Operators re-create.
10. **Register:** only `status=complete`, checkpoint artifact present, not deleted; sets `registered_checkpoint_step` + `registry_model_id=lab:{id}`; unregister/delete clears discovery.
11. **Shell / payload refuse:** reject body keys `command`, `argv`, `shell`, `subprocess`, and persistence_secret_guard / embedded note material (`events`, etc.) → `model_lab_payload_refused` or existing secret/embedded codes. Never accept free-form absolute weight paths from the client.
12. **ai_agents forbid:** `from app.services.model_lab_store` and `from app.model_lab_settings`.
13. **No `/ready` coupling:** always `include_router(model_lab)`; status on `GET /model-lab/status` only — never `/health` or `/ready` soft block.
14. **Opening Lab:** GET catalog/list only — no INSERT, no train thread.
15. **Score axiom:** Lab never writes `tracks[].events[]` or invents `composition.v5`. Listening/eval examples are files under the experiment dir, not project scores, unless the user explicitly imports elsewhere (out of ship-1).
16. **Orphan sweep:** process startup marks orphaned `running` rows `failed` with `error_code=model_lab_interrupted` when this process has no thread for them. GET list does not sweep.

### Part L — Collaboration gate (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `enforce_current(project_id, train_model_lab)` with no project** | Looks like personal | `enforce_current` no-ops / cannot bind without `project_id`; Lab has no score project | **Reject** |
| **B. Require a `gate_project_id` only for membership** | Reuses per-project matrix | Confuses UI; experiments are not project-owned | **Reject** for ship-1 |
| **C. Collab C: when `COLLABORATION_ENABLED`, mutating Lab routes resolve `X-Mukit-Actor`, require a known actor who owns ≥1 project (or single-user `local`); store `owner_actor_id` on the row; list filters like personal. Add owner-capable action `train_model_lab` to `ACTIONS` + freeze test + `docs/collaboration.md`. When collab off, no actor lookup. Plugins/rights/scheduling stay flag-only and are not the pattern** | Implementable; mirrors personal ownership honesty without inventing project FK | New authorize helper | **Accepted** |

### Part M — Checkpoint load for registered Lab models (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Pass user-supplied absolute path through `resolve_music_transformer_checkpoint`** | Reuses env MT generate | Free-form path / allowlist gap; RCE-adjacent | **Reject** |
| **B. Append all of `MODEL_LAB_ROOT` to `MUSIC_TRANSFORMER_ALLOWED_ROOTS` globally** | Simple | Broadens env MT path surface | **Reject** as sole |
| **C. Personal-style Lab loader: registered torch generate resolves only `MODEL_LAB_ROOT/<id>/checkpoints/<step>.pt` (server-derived); fake never calls `load_checkpoint`. Optionally also add Lab root to allowlist as defense-in-depth, but never take a client path** | Matches personal adapter load; acceptance-safe | Lab-specific branch in symbolic generate | **Accepted** |

### Part N — Identity lock (added by `/aif-improve`)

1. `id == mtlab_<secrets.token_hex(8)>` (16 hex chars after prefix).
2. FS directory name equals `id` (no separate `experiment_fs_id`; drop dual-id column).
3. `registry_model_id` is null until register; then exactly `lab:{id}`.
4. Never use CLI `auto_experiment_id` for Lab-created dirs.
5. Display name unique among non-deleted rows (like personal).

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| MT experiment FS | `ExperimentPaths`, `create_experiment`, `train_experiment`, `resume_experiment` in `music_transformer/` | Lab service calls these; does not reimplement the trainer. Lab does **not** expose resume in ship-1 |
| Experiment contract | `music_transformer.experiment.v1` + eval/listening/metrics/compare schemas | Frozen into Lab job; Lab index stores digests + pointers |
| Dataset versions | `DATASET_ROOT/<name>/<dataset_version_id>/manifest.json` | Read-only catalog for Lab Dataset stage |
| Tokenizer | `tokenizer.v1`, `expected_tokenizer_record`, presets `core` / `core_harmony` | Lab Tokenizer stage |
| Rights | `rights_gate.verify_train_paths_against_rights` + `evaluate_rights_use` + dataset `rights/` | Always called with Lab `dataset_dir` |
| Personal job pattern | `personal_composer_service` thread + fake join + SQLite + orphan sweep + `owner_actor_id` | Mirror lifecycle; Collab C adapts ownership filter |
| Model registry | `ai_runtime/bootstrap.py`, `GET /ai/models`, hybrid `composer_model_id`, `personal:` dispatch in `symbolic_composition_generate` / `fake_llm` | Add runtime `model_lab` + `lab:` dispatch |
| Path policy | `model_path_resolve.default_music_transformer_roots` does **not** include experiment root | Lab loader must not rely on env MT allowlist alone (Part M) |
| Storage policy | `reject_storage_root` via settings helpers (`assert_*_storage_root`) | `assert_model_lab_storage_root` |
| Architecture tests | `test_ai_agents_architecture.py` | Extend forbid list |
| Collab freeze | `test_collaboration_permissions.py` `ACTIONS` | Add `train_model_lab` |
| Gitignore | `experiments/`, `*.pt`, `personal_composers/` already ignored | No new gitignore required for default Lab root |
| Latest migration | `20261003_0030_rights_registry.py` | Next `20261004_0031_model_lab`, `down_revision` `20261003_0030` |

### Gaps

- No HTTP for MT train/eval/listen/compare
- No SQLite experiment index / register promote
- No Lab SPA / wizard
- No Lab-specific resource caps or fake train path
- No N-way compare API
- No `lab:` generate / fake_llm / implicit-route skip wiring
- No Collab C studio actor helper
- MT docs still say training never runs inside FastAPI — must be amended for opt-in Lab

### Coupling risks to avoid

- Do not import FastAPI inside `music_transformer/`
- Do not let `ai_agents/` open the Lab store
- Do not write corpora into `PROJECT_DB_PATH` or train snapshots into `DATASET_ROOT`
- Do not auto-start train on tab open
- Do not claim symbolic metrics are musical quality
- Do not expose CLI argv or shell
- Do not call `load_checkpoint` for fake Lab models
- Do not use project-less `enforce_current` as if it were a studio gate

## Scope And Decisions

### In scope (ship-1)

- Schemas/settings/store/migration for `model.lab.experiment.v1`
- HTTP: status, dataset catalog, tokenizer/architecture presets, create/list/get experiment, metrics, checkpoints, eval, listening, compare, register, stop/delete
- Background fake/torch train wrapping existing MT experiment APIs + startup orphan sweep
- `lab:` symbolic generate path (fake + torch Lab loader)
- Lab tab wizard + compare + register UX
- Collab C + `train_model_lab` ACTIONS freeze + collaboration docs row
- Resource limits, architecture forbid tests, docs (`docs/model-lab.md`), AGENTS.md entry, `.env.example`

### Out of scope (ship-1)

- Dataset build / ingest / split from the SPA
- Arbitrary tokenizer encode of uploaded files
- Personal Composer LoRA (Profiles)
- Neural audio / MusicGen weight training
- Remote ExecutionNode GPU train placement (inference scheduling stays separate)
- Lab **resume** after stop (CLI `resume_experiment` remains offline)
- Editing Ardour / rewriting live `composition.v2` from Lab
- Marketplace of models
- Claiming quality winners from compare
- `/ready` dependency on Lab
- New Compose training profile for Lab-in-API (host torch / fake CI only)

### Architecture decisions (locked)

**1. Documents**

| Document | Role |
|----------|------|
| `model.lab.experiment.v1` | Index + API view: id, status, dataset_version_id, dataset_name, tokenizer_expectation, architecture digest, train digest, seed, checkpoint refs, runtime summary, evaluation_version, listening_set_digest, registry_model_id (optional), owner_actor_id (optional), error_code, timestamps |
| `model.lab.create.v1` | POST body: display_name, dataset_version_id (or lab fixture id), tokenizer_preset, architecture_preset, optional bounded overrides, train knobs, seed, optional eval/listening opt-in toggles (default off) |
| `model.lab.metrics.v1` | Projection of metrics.jsonl + summary (loss, val_loss, token_accuracy, lr, tokens_per_sec, mem_mb) |
| `model.lab.compare.v1` | Multi-run compare report; `musical_quality_claim=false` |
| `model.lab.register.v1` | checkpoint_step + optional display_name override |
| Reuse | `music_transformer.experiment.v1`, eval/listening/compare/checkpoint cards, `model.data.provenance.manifest.v1` |

**2. Identity (Part N)**

`id = mtlab_` + `secrets.token_hex(8)`. FS dir = `id`. `registry_model_id = lab:{id}` only after register. Display name unique among non-deleted Lab rows.

**3. Table `model_lab_experiments`**

Columns: `id` (PK, = FS dir name), `schema_version`, `display_name`, `status` (`queued|running|complete|failed|stopped|deleted`), `dataset_version_id`, `tokenizer_version`, `tokenizer_vocab_hash_prefix`, `architecture_digest_prefix`, `train_digest_prefix`, `seed`, `evaluation_version`, `registered_checkpoint_step` (nullable), `registry_model_id` (nullable), `engine` (`fake|torch`), `runtime_json`, `owner_actor_id` (nullable), `error_code`, `created_at`, `updated_at`. No `experiment_fs_id` column. No pitch/event columns. No FK into projects.

**4. Settings (`model_lab_settings.py`)**

| Env | Default | Notes |
|-----|---------|-------|
| `MODEL_LAB_ENABLED` | off | Mutating routes |
| `MODEL_LAB_FAKE` | off | CI |
| `MODEL_LAB_ROOT` | `experiments/music_transformer` | `assert_model_lab_storage_root` → `reject_storage_root`; may equal MT experiment root; already covered by `.gitignore` `experiments/` |
| `MODEL_LAB_MAX_STEPS` | 64 | Clamp Lab creates (hard ≤ 512) |
| `MODEL_LAB_MAX_BATCH` | 8 | Hard ≤ 64 |
| `MODEL_LAB_MAX_CONCURRENT` | 1 | |
| `MODEL_LAB_MAX_COMPARE` | 8 | |
| `MODEL_LAB_ALLOW_ACCELERATOR` | off | |
| `MODEL_LAB_RETENTION` | cap on retained experiments (e.g. 50) | GC deleted dirs |

**5. HTTP map**

| Method | Path | Notes |
|--------|------|-------|
| GET | `/model-lab/status` | always 200; enabled/fake/caps; not on `/ready` |
| GET | `/model-lab/datasets` | read-only; allowed when disabled |
| GET | `/model-lab/presets` | allowed when disabled |
| POST | `/model-lab/experiments` | create + start; requires enabled + Collab C |
| GET | `/model-lab/experiments` | list (no train); allowed when disabled; owner filter when collab on |
| GET | `/model-lab/experiments/{id}` | detail; allowed when disabled |
| GET | `/model-lab/experiments/{id}/metrics` | allowed when disabled |
| GET | `/model-lab/experiments/{id}/checkpoints` | allowed when disabled |
| POST | `/model-lab/experiments/{id}/evaluate` | requires enabled |
| GET | `/model-lab/experiments/{id}/listening` | allowed when disabled |
| POST | `/model-lab/experiments/{id}/stop` | requires enabled; no resume |
| POST | `/model-lab/experiments/{id}/register` | requires enabled |
| DELETE | `/model-lab/experiments/{id}` | requires enabled |
| POST | `/model-lab/compare` | requires enabled; body: experiment_ids[2..N] |

Error codes (non-exhaustive): `model_lab_disabled`, `model_lab_busy`, `model_lab_dataset_refused`, `model_lab_device_refused`, `model_lab_payload_refused`, `model_lab_not_found`, `model_lab_not_registerable`, `model_lab_name_taken`, `model_lab_interrupted`, `rights_train_refused`, `model_lab_torch_unavailable`, `collaboration_role_denied`, `collaboration_not_member` (or studio-actor equivalents mapped in the router).

**6. Runtime discovery and generate (Parts F, J, M)**

`ai_runtime/runtimes/model_lab.py` lists registered complete rows. `ModelRuntimeId` gains `model_lab`. `default_operation_routes` skips `model_lab`. `generate_symbolic_composition` and `fake_llm` gain `lab:` dispatch (mirror `personal:`). Fake → `generate_fake_symbolic_composition` with provenance lab id. Torch → Lab loader under `MODEL_LAB_ROOT/<id>/checkpoints/<step>.pt` only. Incomplete/unregistered → `ModelUnavailableError`.

**7. UI**

`ModelLabPanel.jsx` on new Lab tab in `ComposerWorkspace.jsx`. Pure helpers in `modelLabForm.js` / `modelLabApi.js`. Stages are client wizard state over the typed API. Metrics as tables (no new chart library). Listening shows report digests + JSON preview — does not auto-apply into the working score. When disabled, show enable hint from status.

**8. Collaboration (Part L — Collab C)**

Add `train_model_lab` to owner-capable `ACTIONS` in `collaboration_permissions.py`. Add `authorize_studio_lab` (or equivalent) used by mutating Lab routes. Freeze test + `docs/collaboration.md` row. Store `owner_actor_id`; list filters when collab on.

**9. Fixtures**

`backend/tests/fixtures/model_lab/` — manifest + `splits/train.jsonl` + **hard-required** `rights/index.jsonl` (train-eligible) + optional rights manifest. Catalog may expose `lab_fixture:tiny` when fake/tests point `DATASET_ROOT` at the fixture parent.

**10. Docs axiom**

`docs/music-transformer.md` must be amended: CLI remains the offline path; **opt-in Model Lab may train in-process when `MODEL_LAB_ENABLED=1`**. No new Compose profile required for ship-1.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: add Model Lab experiment contracts and SQLite index`
- **Commit 2** (after tasks 4–6): `feat: run Model Lab experiments with metrics eval and compare`
- **Commit 3** (after tasks 7–9): `feat: register Lab checkpoints and Lab tab research UI`
- **Commit 4** (after task 10): `docs: document Model Lab training research surface`

## Tasks

### Phase 1: Contracts and index
- [x] Task 1: Add Model Lab schemas and settings
- [x] Task 2: Add `model_lab_experiments` migration and store
- [x] Task 3: Read-only dataset catalog + presets + payload/shell refuse

### Phase 2: Train / eval / compare jobs
- [x] Task 4: Implement experiment create/run service (fake + torch wrap) + orphan sweep
- [x] Task 5: Metrics, checkpoints, evaluate, listening, stop, delete (no resume)
- [x] Task 6: Multi-experiment compare API

### Phase 3: Registry and HTTP
- [x] Task 7: HTTP router + status + Collab C gate
- [x] Task 8: Register checkpoint onto `/ai/models` + `lab:` generate path

### Phase 4: UI, security, docs
- [x] Task 9: Lab tab wizard, inspect, compare, register UX
- [x] Task 10: Docs, AGENTS.md, architecture forbid, acceptance tests

<!-- Commit checkpoint: tasks 1-3 -->
<!-- Commit checkpoint: tasks 4-6 -->
<!-- Commit checkpoint: tasks 7-9 -->
<!-- Commit checkpoint: task 10 -->

### Task 1: Add Model Lab schemas and settings

Create `backend/app/model_lab_schemas.py` and `backend/app/model_lab_settings.py` with the documents and env knobs in Architecture decisions. Include closed enums for status, engine, and preset ids. Implement `assert_model_lab_storage_root` calling `reject_storage_root` (personal pattern). Do not open SQLite in settings. Clamp helpers for steps/batch/compare arity. Create body defaults eval/listening opt-in to false.

`backend/tests/test_model_lab_schemas.py` accepts a create body with display name `TinyLab-v1`, seed 42, preset `tiny_lab`, and rejects: embedded `events`, `command`/`argv`/`shell` keys, steps above Lab hard max, empty display name, free-form absolute checkpoint path fields if present. Settings test pointing `MODEL_LAB_ROOT` at `DATASET_ROOT` expects `StorageRootError` reason `dataset_root`.

LOGGING: INFO on settings load (enabled, fake, max_steps, max_batch, max_concurrent, root basename). DEBUG on schema reject with field name + code, not full payload. Levels follow `LOG_LEVEL`.

### Task 2: Add `model_lab_experiments` migration and store

Add `backend/app/db/alembic/versions/20261004_0031_model_lab.py` with `down_revision` `20261003_0030` and columns from Architecture decisions (including `owner_actor_id`; **no** `experiment_fs_id`). Add `backend/app/services/model_lab_store.py` with insert/get/list/update/status transitions/register fields. List omits `deleted`; optional owner filter. Unique display name among non-deleted. Identity: `id` is the only experiment key. Update `backend/tests/test_ai_agents_architecture.py` to forbid `from app.services.model_lab_store` and `from app.model_lab_settings`.

`backend/tests/test_model_lab_store.py` upgrades a temp DB, inserts `TinyLab-v1` with id `mtlab_` + 16 hex, reads digests/seed/owner back, rejects duplicate name, refuses transition out of `deleted`.

LOGGING: INFO on insert/status/register with experiment id, status, engine — never metrics blobs or absolute home paths when basename suffices.

Depends on Task 1.

### Task 3: Read-only dataset catalog + presets + payload/shell refuse

Add `backend/app/services/model_lab_catalog.py` to list dataset versions under `DATASET_ROOT` (name, version id, item counts from manifest — no item bodies). Expose tokenizer presets and architecture presets (`tiny_lab` from `tiny_test_config` shape; optional `small_lab` with Lab ceilings). Centralize payload refuse for shell/argv/command and secret/note keys. Resolve selected version to a real `dataset_dir` Path under the root (no escape).

`backend/tests/test_model_lab_catalog.py` builds a temp `DATASET_ROOT` with one version manifest + rights index and asserts catalog entry fields; path escape / non-child paths refused. Preset test asserts `tiny_lab` n_layers/d_model match tiny shape. Payload test rejects `{"command":"rm -rf /"}` shaped bodies at schema or catalog boundary.

LOGGING: DEBUG catalog count + dataset_name only; never dump manifests wholesale at INFO.

Depends on Task 1.

### Task 4: Implement experiment create/run service (fake + torch wrap) + orphan sweep

Add `backend/app/services/model_lab_service.py` (orchestration) and keep MT imports one-way into `music_transformer.train` / `experiments` / `rights_gate`. On create: validate enabled + caps + catalog dataset → **always** `verify_train_paths_against_rights(dataset_dir, …)` → insert row → `assert_model_lab_storage_root` → ensure `ExperimentPaths(MODEL_LAB_ROOT/id)` → start thread. Freeze experiment config with `eval.enabled=false` / `listening.enabled=false` unless create opt-in. Fake engine writes config.json, metadata.json, metrics.jsonl (synthetic decreasing loss), metrics_summary.json, fake checkpoint **card** + placeholder basename (no tensors), eval/listening stubs, and provenance when dataset present; joins before HTTP returns when `MODEL_LAB_FAKE=1`. Torch engine calls `train_experiment` with Lab-clamped config and updates status/runtime_json at boundaries. Expose `sweep_orphaned_model_lab_experiments` for lifespan (mark `running` → `failed`/`model_lab_interrupted` when no live thread). GET list does not sweep.

`backend/tests/test_model_lab_service.py` with fake+enabled completes acceptance vector fields on the experiment record (id shape, dataset version, tokenizer, digests, seed, checkpoint ref, runtime, evaluation_version from stub). Disabled create → `model_lab_disabled`. Concurrent second create while running → `model_lab_busy`. Rights fixture with `reference_only` → `rights_train_refused` and no experiment dir. Missing rights index on a version with manifest → refuse. Optional torch test `importorskip("torch")` runs 1–2 steps on tiny fixture. Orphan sweep test marks a stuck `running` row interrupted.

LOGGING: INFO start/complete/fail/sweep with experiment id + engine + step; ERROR on rights refuse with code only; never log full config JSON at INFO if it includes paths — use digests/prefixes.

Depends on Tasks 2, 3.

### Task 5: Metrics, checkpoints, evaluate, listening, stop, delete (no resume)

Service methods project metrics.jsonl/summary, list checkpoint cards, run `evaluate_checkpoint` / `run_listening_set` on explicit POST (fake stubs when fake engine; last-write-wins), stop cooperatively, delete soft-deletes row and removes FS dir (after stop) and clears registry fields. **No** `POST …/resume`. Document `stopped` as terminal except delete.

Tests cover metrics shape (loss + val), checkpoint list non-empty after fake train, evaluate sets `musical_quality_claim=false`, listening returns prompt digests, stop from running, delete removes registry eligibility, resume route absent.

LOGGING: INFO evaluate/listen/stop/delete with ids; DEBUG metric row counts.

Depends on Task 4.

### Task 6: Multi-experiment compare API

Add compare builder producing `model.lab.compare.v1` for 2..`MODEL_LAB_MAX_COMPARE` ids using existing pairwise compare under the hood. Refuse quality-winner fields. Cap arity.

`backend/tests/test_model_lab_compare.py` creates two fake experiments (different seeds), compares, asserts both ids present, equality flags for tokenizer/arch when identical, metric deltas present, `musical_quality_claim` is false. Single id or >max → 422.

LOGGING: INFO compare with id list length + digest prefixes.

Depends on Task 5.

### Task 7: HTTP router + status + Collab C gate

Add `backend/app/routers/model_lab.py`, **always** `include_router` in `main.py` (like personal; not conditional like MT generate API). Wire lifespan orphan sweep. Map domain errors to HTTP table. `GET /model-lab/status` always 200. Read GETs work when disabled; mutates require enabled. Implement Collab C authorize helper; add `train_model_lab` to `collaboration_permissions.ACTIONS` (owner-capable); update `backend/tests/test_collaboration_permissions.py` freeze + editor deny list. Do **not** add Lab to `/ready`.

`backend/tests/test_model_lab_router.py` exercises status when disabled, fake create happy path when enabled, disabled mutate refuse, payload refuse, Collab C editor/non-owner denied, owner allowed. Include fixture dataset wiring via temp env.

LOGGING: INFO route-level create/register/compare with codes; no bodies.

Depends on Tasks 4–6.

### Task 8: Register checkpoint onto `/ai/models` + `lab:` generate path

Add `backend/app/ai_runtime/runtimes/model_lab.py`, extend `ModelRuntimeId`, bootstrap registration of registered rows, skip in implicit composer route. Wire `lab:` in `symbolic_composition_generate.py` and `fake_llm` selected_composer (mirror `personal:`). Fake path: never `load_checkpoint`; provenance model_id = lab id. Torch path: Part M Lab loader under `MODEL_LAB_ROOT/<id>/checkpoints/<step>.pt` only (no client path). Unregister on delete. Acceptance: torch generate of registered id must not return `model_path_rejected`.

`backend/tests/test_model_lab_registry.py`: after fake train+register, `GET /ai/models?capability=symbolic_composer&status=ready` includes the id and display name; hybrid generate with that `composer_model_id` returns provenance model_id equal to the lab id (fake path). Incomplete/unregistered id → unavailable. Optional torch register+generate when torch installed.

LOGGING: INFO register/unregister with registry id; WARN if table missing during bootstrap (catalog continues).

Depends on Tasks 4 and 7.

### Task 9: Lab tab wizard, inspect, compare, register UX

Add `frontend/src/components/ModelLabPanel.jsx`, `frontend/src/api/modelLabApi.js`, `frontend/src/utils/modelLabForm.js`, wire Lab tab in `ComposerWorkspace.jsx` (`data-testid=composer-tab-lab`, `model-lab-panel`). Wizard stages bind to catalog/presets/create; detail view shows metrics table, resource summary, checkpoints, eval examples, listening; multi-select compare; register button only when complete + checkpoint selected. Open tab → list/catalog only. When status.enabled is false, show enable hint (GETs still work).

Frontend unit tests for form clamps/eligibility; optional Playwright smoke when fake stack is available (or unit-only if e2e env lacks Lab flag — document in task).

LOGGING: `console.debug` stage transitions and experiment id; never dump full metrics arrays at info.

Depends on Task 8.

### Task 10: Docs, AGENTS.md, architecture forbid, acceptance tests

Write `docs/model-lab.md` (workflow, flags, limits, honesty that metrics ≠ quality, no shell, Collab C, no resume, relation to Personal Composer / rights / MT CLI). Amend `docs/music-transformer.md`: CLI remains offline path; opt-in Model Lab may train in-process when enabled. Update `docs/collaboration.md` with `train_model_lab` row. Update `AGENTS.md` structure + key entry points. Ensure architecture forbid tests cover store/settings. Add `backend/tests/test_model_lab_acceptance.py` encoding the Goal acceptance vector under fake+enabled. Update `.env.example` with `MODEL_LAB_*` next to personal/MT. Note gitignore already covers `experiments/`. Do not edit `ROADMAP.md`. No new Compose profile required for ship-1.

LOGGING: N/A for docs; acceptance test uses existing log assertions sparingly (no secret leakage).

Depends on Tasks 7–9.

## Test Plan

- Schema/settings/store/catalog unit tests (Task 1–3)
- Fake service acceptance fields + rights refuse + busy/disabled + orphan sweep (Task 4)
- Metrics/eval/listen/stop/delete; no resume (Task 5)
- N-way compare honesty (Task 6)
- Router + Collab C + GET-when-disabled (Task 7)
- Registry + `lab:` hybrid composer_model_id + no fake load_checkpoint (Task 8)
- Frontend form unit tests (Task 9)
- Full fake acceptance module + ai_agents import ban + collaboration docs/ACTIONS (Task 10)
- Optional torch `importorskip` tiny train + register generate

## Verification Gate

Before `/aif-verify`: all tasks checked; `scripts/run_tests.sh` green for touched surfaces; fake acceptance vector passes; Lab tab open does not create rows; `MODEL_LAB_ENABLED=0` refuses mutate but status/list GETs work; no shell endpoint; no `composition.v5`; no resume route; docs checkpoint satisfied (`Docs: yes`).

## Open Questions

None blocking ship-1. Future plans may add Lab resume, ExecutionNode-placed GPU train workers, dataset-build orchestration, or neural weight labs — explicitly out of this plan.
