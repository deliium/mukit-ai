# Hybrid LLM planner + symbolic note generation

## Summary

Mukit can generate `composition.v2` through a **pipeline-parameterized** LangGraph path:

| Pipeline | Planner | Notes |
|----------|---------|-------|
| `llm_only` (default) | Language model | Language model compose stages |
| `hybrid_plan_symbolic` | Language model → `composition.plan.v1` | Local symbolic composer (Music Transformer or `fake:symbolic-tiny`) |
| `symbolic_continuation` | Prefix V2 (+ light plan fragment) | Symbolic continue |
| `symbolic_variation` | Prefix V2 (+ light plan fragment) | Symbolic vary |

`composition.plan.v1` is **not** a playable score. Playable notes remain only on `composition.v2` `tracks[].events[]`.

## Request

`POST /llm/generate-music-json` accepts additive options:

```json
{
  "options": {
    "pipeline": "hybrid_plan_symbolic",
    "seed": 42,
    "max_retries": 1,
    "allow_llm_composition_repair": false,
    "prefix_composition": null
  }
}
```

- Default `pipeline` is `llm_only` (unchanged behavior).
- Hybrid/symbolic require a ready `symbolic_composer` model (`GET /ai/models?capability=symbolic_composer&status=ready`). There is **no** silent fallback to LLM note writing while claiming symbolic provenance (HTTP 503 + code).
- `seed` drives deterministic symbolic sampling when the backend uses greedy/fake paths.

## Response provenance (additive)

```json
{
  "pipeline_id": "hybrid_plan_symbolic",
  "stages": [
    {"operation": "generate_planner", "model_id": "fake:fake-v1", "capability": "language_planner", "runtime": "fake"},
    {"operation": "generate_composer", "model_id": "fake:symbolic-tiny", "capability": "symbolic_composer", "runtime": "fake_symbolic", "seed": 42}
  ],
  "plan_schema_version": "composition.plan.v1",
  "constraints_digest_prefix": "sha256:…",
  "seed": 42
}
```

## Repair lanes

| Failure | Lane | Default hybrid behavior |
|---------|------|-------------------------|
| Invalid plan | `repair_plan` | Coerce form hard fields to constraints, rebuild plan, re-validate; fail closed with `plan_invalid` after retries |
| Invalid tokens / decode | `symbolic_decode_repair` | Re-sample with `seed + attempt` (`GENERATION_HYBRID_MAX_TOKEN_RETRIES`) |
| V2 validation | `repair_composition` | Prefer symbolic re-sample; optional LLM note rewrite only if `allow_llm_composition_repair=true` |

## Configuration

| Env | Purpose |
|-----|---------|
| `LLM_FAKE_MODE=1` | Fake planner + always-ready `fake:symbolic-tiny` |
| `MUSIC_TRANSFORMER_CHECKPOINT` | Checkpoint basename/path for real MT |
| `MUSIC_TRANSFORMER_API_ENABLED` / `MUSIC_TRANSFORMER_GRAPH_ENABLED` | Opt-in readiness for `local:music-transformer` |
| `GENERATION_HYBRID_MAX_TOKEN_RETRIES` | Token re-sample budget (default `1`) |
| `AI_OP_GENERATE_PLANNER` / `AI_OP_GENERATE_COMPOSER` | Optional per-op model overrides. Hybrid calls resolve with `collapse_reserved_generate=False` so these stay distinct from `AI_OP_GENERATE`. |

## UI

Music Generator **Pipeline**: LLM | Hybrid. Hybrid is disabled when no ready symbolic composer is discovered. Optional seed for Hybrid. Dual-stage provenance appears after generate.

## Evaluation

Pytest A/B (`tests/test_generation_pipeline_ab.py`) compares fake `llm_only` vs `hybrid_plan_symbolic` on validity / constraints / fingerprints. **`musical_quality_claim: false`** — not an aesthetic quality claim.

## See also

- [AI Runtime](ai-runtime.md) — `generate_planner` / `generate_composer`, `symbolic_composer`
- [Music Transformer](music-transformer.md) — engine behind real symbolic notes
- [Composition V2](composition-v2.md) — sole playable contract
