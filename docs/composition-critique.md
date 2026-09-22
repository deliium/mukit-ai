# Composition Critique (Music Evaluation Engine)

Deterministic **Music Evaluation Engine** produces structured `agent.critique.v1` reports
with four-strata findings. Canonical playable notes stay on `composition.v2`.
Critique never mutates Composition and never auto-applies Critic approve.

## Critique vs Analysis

| Concern | Contract | Role |
|---------|----------|------|
| Metrics sidecar | `composition.analysis.v1` | Tonality, harmony, density, warnings — derived evidence |
| Judgment report | `agent.critique.v1` | Findings + approve/revise recommendation |

Analysis remains the Analysis tab metric source. Critique findings are judgment /
compliance observations layered on top (Analysis tab Critic section + Agents preview).

## Four strata

| Stratum | Meaning | Revises by default? |
|---------|---------|---------------------|
| `hard_constraint` | Request fidelity / hard failures | Yes (severity `error`) |
| `technical` | Objective technical warnings | No (optional `revise_on_technical`) |
| `stylistic` | Musical observations (incl. climax contrast) | **Never** alone |
| `subjective` | Optional model critique (must cite locus) | **Never** alone |

Subjective findings cannot use severity `error`. Taste is never represented as
objective correctness.

## Scopes

`composition` · `section` · `track` · `bars` (inclusive start/end bars).

Bar scope is evaluation-owned; Analysis API scopes are unchanged.

## Climax contrast (acceptance)

When intent marks a climax (explicit index, section label/`id` containing `climax`,
or brief text — chorus is **not** auto-climax unless `CRITIQUE_TREAT_CHORUS_AS_CLIMAX`),
and climax density/dynamics ≈ preceding section, emit:

- `code`: `climax_lacks_contrast`
- `stratum`: `stylistic`
- affected climax bars + prior/climax metrics
- **no** Composition mutation; recommendation stays `approve` unless hard errors exist

## HTTP

`POST /critique/evaluate` — session-only; read-only; no SQLite writes.

Body: `composition`, optional `scope`, `include_model_critique`, `revise_on_technical`,
`requested_climax_section_index`, `brief_excerpt`.

Spine Critic (`POST /ai/agents/critic/run` / workflow preview) uses the same engine.
Durable promote of role `critique` remains on `multi-agent-apply` (existing workspace).

## Optional model critique

When `include_model_critique=true` and `LLM_FAKE_MODE`, a deterministic subjective
finding with concrete bars is returned. Findings without `affected_range` /
`affected_tracks` / concrete `evidence.refs` are dropped.

## Logging

- INFO: engine_version, scope kind, stratum counts, recommendation, duration_ms,
  model_critique_status, climax code hit/skip
- Never INFO: full explanations, prompts, keys, full analysis reports, event arrays

## Env knobs

| Variable | Default | Meaning |
|----------|---------|---------|
| `CRITIQUE_CLIMAX_NOTE_LOAD_DELTA_MAX` | `0.08` | Relative note_load delta below → contrast finding |
| `CRITIQUE_CLIMAX_VELOCITY_DELTA_MAX` | `0.10` | Relative peak velocity delta |
| `CRITIQUE_TREAT_CHORUS_AS_CLIMAX` | `false` | Opt-in chorus-as-climax heuristic |

## See also

- [Multi-agent (V4)](multi-agent.md) — Critic agent, controlled revision loops, workspace promote
- [Composition Analysis](composition-analysis.md) — metric sidecar
- [Project persistence](project-persistence.md) — revision AI artifact summary

> Note: automatic Critic → revise → re-critique is owned by the multi-agent
> `revision_loop` controller (session preview). The Evaluation Engine and Critic
> agent remain read-only and never mutate `composition.v2`.
