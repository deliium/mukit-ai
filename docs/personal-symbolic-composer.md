# Personal symbolic composer

A personal model is one trained adapter plus its files. The display name `MyComposer-v1` is the label the user types. The registry id is `personal:pcomp_` plus 16 hex characters. The playable score stays `composition.v2`. This is not a `composer.profile.v1` and it is not a full Music Transformer checkpoint.

Training starts only from `POST /personal-composers`. Project open, process startup, `GET /personal-composers`, and generate do not create a job. Startup marks an orphaned `running` row `failed` with `personal_interrupted`.

## What gets stored

The request lists project ids and one provenance object per id. Rights resolve
via the studio registry first, then request attestation, else refuse (never
default `training_allowed`). `reference_only` / `unknown` / `no_training` hard-fail
train. After a successful snapshot, the service upserts registry rows for those
projects. Opening Profiles / listing adapters never upserts. Details:
[rights-governance.md](rights-governance.md).

`compute_train_eligible` must be true, and the projects must have note events. When collaboration is on, the actor must be the project owner (`train_adapter`). The accepted request copies those scores into `PERSONAL_COMPOSER_ROOT/<adapter id>/snapshot/`. That directory is a frozen snapshot. It is not `DATASET_ROOT` and it is not the live score. Later edits to the projects do not change the job.

The job writes `personal.training_manifest.v1`, `personal.dataset_snapshot.v1`, and `personal.adapter_config.v1`. The method is `lora`. Rank defaults to 4. Targets are `qkv` and `out_proj`. `freeze_base` is true.

## Engines

`PERSONAL_COMPOSER_FAKE=1` writes `personal.adapter.fake.v1` and does not update weights. Inference delegates to `fake:symbolic-tiny` and records the personal registry id on the composer stage. The torch engine freezes the Music Transformer base and stores only the low-rank tensors. A completed torch adapter loads those tensors and samples through the existing token decode path. Evaluate reports next-token loss and token accuracy for that snapshot. A completed adapter appears on `GET /ai/models?capability=symbolic_composer&status=ready`. Incomplete, stopped, and deleted adapters stay off that list.

Hybrid generate can set `options.composer_model_id` to that registry id. Omitting the field keeps the current symbolic composer. The language-model selection is unchanged.

## Routes

| Method | Path | Effect |
|--------|------|--------|
| `POST` | `/personal-composers` | Start one job. Fake mode finishes before the response. |
| `GET` | `/personal-composers` | List non-deleted jobs. |
| `GET` | `/personal-composers/{adapter_id}` | Read one job. |
| `POST` | `/personal-composers/{adapter_id}/stop` | Stop a running job and keep its files. |
| `POST` | `/personal-composers/{adapter_id}/resume` | Continue from `step.json` when the job is stopped or failed. |
| `POST` | `/personal-composers/{adapter_id}/evaluate` | Write `personal.eval.v1`. Fake eval leaves loss null. |
| `DELETE` | `/personal-composers/{adapter_id}` | Mark the row deleted and remove its directory. |

## Environment

| Key | Default | Meaning |
|-----|---------|---------|
| `PERSONAL_COMPOSER_ROOT` | `personal_composers` | Snapshot and adapter files. Refused when it is `DATASET_ROOT` or `PROJECT_DB_PATH`. |
| `PERSONAL_COMPOSER_FAKE` | off | Write the same documents without torch. |
| `PERSONAL_COMPOSER_MAX_PROJECTS` | 8 | Clamped to 1..8. |
| `PERSONAL_COMPOSER_MAX_STEPS` | 50 | Clamped to 1..50. |
