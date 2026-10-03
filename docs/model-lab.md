# Model Lab

Opt-in **research control plane** over existing Music Transformer experiment train/eval/listen/compare. Product generation stays V5; playable scores stay `composition.v2`. There is no `composition.v5`.

Model Lab is distinct from:

| Surface | Role |
|---------|------|
| **Personal Composer** (Profiles) | LoRA on selected owned projects |
| **Composer Profiles** | Soft preference conditioning |
| **MT offline CLI** | Full corpus train without HTTP |

## Workflow

```text
Dataset → Tokenizer → Architecture → Training config → Run → Evaluation → Model registry
```

1. Select an existing dataset version under `DATASET_ROOT` (Lab never builds corpora).
2. Choose closed tokenizer / architecture presets with Lab-capped train knobs.
3. `POST /model-lab/experiments` runs a small experiment (fake joins before response).
4. Inspect metrics, resources, checkpoints, eval examples, listening digests.
5. Explicitly `POST …/register` to expose `lab:{id}` on `GET /ai/models`.

Opening the Lab tab only lists catalog/experiments — it never starts training and never mutates the working score.

## Configuration

| Env | Default | Notes |
|-----|---------|-------|
| `MODEL_LAB_ENABLED` | off | Mutating routes |
| `MODEL_LAB_FAKE` | off | CI skeleton without torch |
| `MODEL_LAB_ROOT` | `experiments/music_transformer` | Refuses `DATASET_ROOT` / `PROJECT_DB_PATH` |
| `MODEL_LAB_MAX_STEPS` | 64 | Hard ≤ 512 |
| `MODEL_LAB_MAX_BATCH` | 8 | Hard ≤ 64 |
| `MODEL_LAB_MAX_CONCURRENT` | 1 | |
| `MODEL_LAB_MAX_COMPARE` | 8 | |
| `MODEL_LAB_ALLOW_ACCELERATOR` | off | |
| `MODEL_LAB_RETENTION` | 50 | |

Status lives on `GET /model-lab/status` only — never `/ready` or `/health`.

## Honesty

- Symbolic metrics are **not** musical quality (`musical_quality_claim` always false).
- Compare reports deltas only — no quality winner.
- Fake registered models generate via `fake:symbolic-tiny` with Lab provenance — they never `load_checkpoint`.
- Torch generate loads only `MODEL_LAB_ROOT/<id>/checkpoints/step_*.pt` (server-derived path).
- No shell / argv / free-form weight paths on HTTP bodies.
- Ship-1 has **no resume** after stop (`stopped` is terminal except delete).

## Rights and collaboration

- Create always runs `verify_train_paths_against_rights` on the selected version dir (fail-closed).
- When `COLLABORATION_ENABLED`, mutating Lab routes use Collab C: known actor who owns ≥1 project (or single-user `local`), action `train_model_lab`.
- `ai_agents/` must not import `model_lab_store` or `model_lab_settings`.

## Related

- [Music Transformer](music-transformer.md) — offline CLI remains the primary train path
- [Personal symbolic composer](personal-symbolic-composer.md) — Profiles LoRA
- [Rights governance](rights-governance.md) — train eligibility
- [Collaboration](collaboration.md) — `train_model_lab`
- [Hybrid generation](hybrid-generation.md) — `options.composer_model_id=lab:…`
