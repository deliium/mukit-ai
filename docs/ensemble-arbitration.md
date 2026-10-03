# Controlled multi-model candidate arbitration

Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`.

**Ensemble** means an explicit multi-`symbolic_composer` fan-out for one generate brief. It is distinct from Develop/Arrange multi-prompt-direction ballots on one language model, distinct from Model Lab multi-experiment compare (metrics, not Apply-able candidates), and distinct from AI job scheduling (placement of one job).

**Arbitration** is the ordered pipeline: hard validators → critic annotate → optional preference ranker → selection. Critic and preference layers are subjective; they are never objective musical taste. Every report stamps `musical_quality_claim=false`.

`ai_agents/` must not import `ensemble_arbitration_settings` or any ensemble arbitration store.

## Pipeline

```text
ensemble.policy.v1  (model_ids[2..MAX], strategy, selection_mode, top_n)
        + client-supplied composition.plan.v1 + GenerationConstraints
        │
        ▼  POST /ensemble/arbitration/preview  (ENSEMBLE_ARBITRATION_ENABLED)
fan-out generate_symbolic_composition(plan, model_id=…) per model
        │
        ▼  hard validators (integrity + constraints)  ← reject here first
survivors
        │
        ▼  critic annotate only (evaluate_composition; subjective ≠ truth)
        │
        ▼  optional in-process LinearPairwiseRanker.rank (not /preferences/rank)
        │
        ▼  selection mode → suggested_id + ordered candidates
ensemble.arbitration.v1  (session-only; musical_quality_claim=false)
        │
        └─ SPA stage survivor → applyGenerationCandidate (human may override)
```

Ship-1 preview **does not** run the hybrid LLM planner. The client supplies `composition.plan.v1` and hard `GenerationConstraints`. Preview never writes `tracks[].events[]`. Apply is SPA-only via the existing generate-apply envelope.

## Honesty

| Field | Value | Meaning |
| --- | --- | --- |
| `musical_quality_claim` | `false` | No “best music” claim |
| `critic_is_subjective_layer` | `true` | Critic findings are advisory |
| `ranking_is_preference_not_quality` | `true` | Ranker reflects explicit user preference |

UI copy must not say “best music”. Suggested candidate id never Applies.

## Selection modes

| Mode | Behavior |
| --- | --- |
| `auto_suggest` | Sets `suggested_candidate_id` to the first after rank; Apply is still explicit |
| `human` | Suggestion optional/null; SPA radio required |
| `top_n` | Returns at most N survivors after rank; suggestion = first |

## Configuration

| Env | Default | Notes |
| --- | --- | --- |
| `ENSEMBLE_ARBITRATION_ENABLED` | off | Preview routes |
| `ENSEMBLE_MAX_MODELS` | 3 | Hard ≤ 4 |
| `ENSEMBLE_MAX_WALL_MS` | 120000 | Soft wall budget between attempts |
| `ENSEMBLE_ALLOW_PARALLEL` | off | Parallel fan-out opt-in |
| `ENSEMBLE_FAKE` | off | Optional fake-path hint for tests |

Status lives on `GET /ensemble/arbitration/status` only (not `/ready` or `/health`). Strategies catalog is readable when disabled. Preview requires the enabled flag. There is **no Collab C** / `authorize_studio_lab` gate on preview.

## Ranking path

When `PREFERENCE_LEARNING_ENABLED` and user `ranking_enabled` are on, arbitration loads stored ranker weights and calls in-process `LinearPairwiseRanker.rank`. It never calls `score_pending`, never posts `POST /preferences/rank`, and never adds `PreferenceSurface="ensemble"`. Ship-1 Apply does not collect a preference choice.

## Fake ensemble (CI)

Three deterministic composers without torch:

| model_id | Shape |
| --- | --- |
| `fake:symbolic-tiny` | One half-bar note per bar |
| `fake:symbolic-sparse` | Fewer/longer notes |
| `fake:symbolic-dense` | More/shorter notes |

All three register as ready `symbolic_composer` descriptors under fake mode. Provenance `model_id` echoes the requested id.

## HTTP

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/ensemble/arbitration/status` | Always 200 |
| GET | `/ensemble/arbitration/strategies` | Closed catalogs |
| POST | `/ensemble/arbitration/preview` | Requires enabled; session report only |

Error codes include `ensemble_arbitration_disabled`, `ensemble_model_limit`, `ensemble_budget_exceeded`, `ensemble_payload_refused`, `ensemble_model_unready`, `ensemble_no_survivors`, `ensemble_strategy_unknown`, `ensemble_selection_invalid`.

## UI

Generate panel Ensemble mode (Hybrid pipeline + status.enabled): multi-select ready symbolic composers, run preview, inspect rejected codes and survivors, honesty banner, stage/Apply selected survivor through `applyGenerationCandidate` with ensemble fields on `generation.provenance.v1`. Opening Generate fetches status/strategies only — never starts fan-out.

Lab tab stays Model Lab research. Develop/Arrange ballots stay unchanged.

## See also

- [Preference learning](preference-learning.md) — ballot ranker honesty; ensemble uses in-process rank only
- [Composition critique](composition-critique.md) — Evaluation Engine (annotate only in ensemble)
- [Hybrid generation](hybrid-generation.md) — single-composer hybrid plan + symbolic notes
- [Model Lab](model-lab.md) — experiment compare is not arbitration
