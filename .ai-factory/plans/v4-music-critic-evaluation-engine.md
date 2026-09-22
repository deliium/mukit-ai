# Implementation Plan: V4 Music Critic and Evaluation Engine

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-22
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: deepen the shipped V4 Critic from a thin analysis→approve/revise gate into a **structured Music Evaluation Engine** that combines deterministic musical checks with optional model-based critique — **without** mutating `composition.v2`, inventing `composition.v4`, collapsing subjective taste into objective correctness, or regressing Analysis / workspace / `multi-agent-apply` CAS
- Parent plans:
  - `.ai-factory/plans/v4-multi-agent-music-architecture.md` (spine Critic agent + `agent.critique.v1`)
  - `.ai-factory/plans/v4-shared-musical-workspace-artifact-graph.md` (typed CritiqueReport promote-on-Apply — **already shipped**)

## Roadmap Linkage
Milestone: "V4 multi-agent music architecture"
Rationale: Milestone marked complete for registry/spine/workspace; this follow-on closes the remaining Critic product gap — structured multi-strata findings, requested-intent compliance checks, optional LLM augmentation with concrete evidence, frontend critique view, and the climax-contrast acceptance criterion — while reusing durable `agent.critique.v1` promote path.

## Goal

Evaluate a composition **musically** before AI workflows decide whether revision is needed.

Ship:

1. A deterministic **evaluation engine** that produces structured findings (severity, category/stratum, affected range/tracks, explanation, evidence, suggested_action).
2. Clear **four-strata taxonomy**: hard constraint failures · objective technical warnings · stylistic observations · subjective AI critique.
3. Critique scopes: whole composition · section · track · selected bars.
4. Optional LLM / music-model critic augmentation that must cite concrete regions or musical properties (no generic praise/blame).
5. Critic **never** modifies Composition; only emits typed artifacts + approve/revise recommendation.
6. Frontend critique/analysis view that surfaces structured findings.
7. Critiques remain **project artifacts** when used in autonomous workflows (reuse shipped workspace promote + `artifact_role_map.critique`).

```text
composition.v2 working draft (read-only)
  → optional request constraints / brief / form-plan intent
  → EvaluationEngine (deterministic checks + analysis metrics)
  → optional LLM critic adapter (ai_runtime; mocked in CI)
  → agent.critique.v1 envelope (findings[] + recommendation)
  → [spine] CriticAgent → artifact_log / optional RevisionPlan
  → [optional] POST /critique/evaluate for Analysis/Critic UI
  → Apply (multi-agent-apply) → durable critique row (existing promote)
  → Frontend Critique view (session) + Versions role summary (durable)
```

**Terminology lock:** Evaluation engine ≠ Analysis API alone. `composition.analysis.v1` remains the derived metric sidecar. `agent.critique.v1` is the **evaluation report** (CritiqueReport). Subjective findings never claim objective correctness. Canonical playable schema stays **`composition.v2`**. Critic / evaluation services **must not** write SQLite; durable promote stays at Apply trust boundary.

## Approach Evaluation (locked)

### Part A — Where evaluation logic lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Inflate `CriticAgent._run_impl` only** | Fast | Untestable outside agent; hard for Analysis UI reuse | **Reject** as sole home |
| **B. New `services/composition_critique.py` (engine) + thin CriticAgent + optional router** | Matches analysis/arrangement pattern; agent purity; reusable HTTP | Small new surface | **Accepted** |
| **C. Replace Analysis with Critique** | One panel | Breaks Analysis tab contract; confuses metrics vs judgment | **Reject** |
| **D. Microservice critic** | Isolation | Contradicts monolith / V4 in-process agents | **Reject** |

### Part B — Schema evolution for CritiqueReport

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep flat `reason_codes` + `summary` only** | Zero churn | Cannot meet structured finding AC | **Reject** |
| **B. New `agent.critique.v2` content_type** | Clean break | Dual promote roles; FE/role-map churn | **Reject** for this plan |
| **C. Backward-compatible extend `agent.critique.v1`** with `findings[]` + stratum enums; keep `recommendation` / `reason_codes` / `summary` | Workspace role `critique` unchanged; old readers still parse | Careful validators + size caps | **Accepted** |
| **D. Embed full `composition.analysis.v1` inside critique** | Rich evidence | Payload blow-up; secrets/log risk; duplicates bounded analysis artifact | **Reject** — keep bounded analysis as sibling artifact; findings hold compact evidence refs |

### Part C — Hard constraints vs analysis warnings

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only reuse `ANALYSIS_WARNING_CODES`** | Simple | Misses requested structure/instrumentation/key compliance | **Insufficient** |
| **B. Only reuse `validate_generation_constraints`** | Strong for request fidelity | Misses climax/contrast, contour, motif, collisions already in analysis | **Insufficient** |
| **C. Engine composes both + new evaluation checks (climax contrast, etc.) with explicit stratum tags** | Meets full metric list | More code | **Accepted** |
| **D. Promote analysis warning severity `error` and treat all warnings as hard failures** | Aggressive gate | Violates “don’t represent taste as correctness”; breaks approve-on-partial policy | **Reject** |

### Part D — Subjective AI critique

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. No LLM path** | Deterministic only | Fails req #5–6 | **Reject** |
| **B. Freeform LLM essay as `summary`** | Easy | Generic statements; hard to cite regions | **Reject** |
| **C. Optional `ai_runtime` critique adapter returning structured findings with required evidence locators; fake mode for CI** | Concrete; testable | Needs schema + prompt/projection discipline | **Accepted** |
| **D. Auto-revise on any subjective finding** | Strong loop | Collapses taste → correctness | **Reject** — subjective findings default to observation; revise only from hard/technical policy or explicit request |

### Part E — Frontend surface

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only MultiAgentPanel artifact dump** | Cheap | Not a critique view | **Insufficient** |
| **B. Replace Analysis tab** | One place | Regresses metric cards | **Reject** |
| **C. Enrich Analysis tab with Critique findings section + strata filters; optional Agents panel inspector** | Reuses analysis scopes/store patterns | Slight tab growth | **Accepted** |
| **D. Brand-new top-level Critic tab only** | Clear | Another tab without reusing Analysis scope UX | **Defer** — optional later; not required if Analysis hosts critique session |

### Part F — Bar-range scope

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only composition/section/track (status quo analysis)** | No analysis schema change | Fails selected-bars AC | **Reject** |
| **B. Add `bars` scope to `composition.analysis.v1` and evaluation** | Unified | Larger analysis risk surface | **Accept for evaluation request**; analysis scope extension only if needed for shared metrics — prefer evaluation engine to clip notes by `[start_bar, end_bar]` without forcing Analysis API migration in the same commit |
| **C. Fake bar scope via section only** | Easy | Weak for AC | **Reject** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| `CriticAgent` | Thin: `analyze_composition` → approve unless `failed` / exception; emits bounded analysis + `agent.critique.v1` + optional `revision_plan` | Keep as orchestrator; call EvaluationEngine |
| `AgentCritiqueV1` | `recommendation`, `reason_codes`, `summary`, `analysis_warning_count` | Extend with `findings[]`, stratum counts, scope digest |
| `composition.analysis.v1` | Tonality, harmony, melody (contour/cadences), density, roles, repetition/motifs, tension, section_summaries, warnings | Primary metric source for technical + some stylistic checks |
| `AnalysisWarning` | `severity`, `category`, `message`, `locator`, `details` | Map → evaluation finding (technical stratum) |
| `validate_generation_constraints` | Key, meter, tempo, bars, sections, instrumentation | Hard-constraint stratum when request constraints present |
| Arrangement / instrument catalog ranges | Range / duplicate / preservation postconditions | Hard/technical instrument-range + collision inputs |
| Workspace + `artifact_role_map.critique` | Promote-on-Apply, Versions summary | **Already ships** — no new tables for critique persistence |
| `build_llm_analysis_context` | Bounded advisory projection | Seed LLM critic prompt; still never log full report at INFO |
| Analysis UI / store | Scope Whole/Section/Track, warning list | Extend with Critique findings + bar scope + evaluate API |
| Fake agents / `LLM_FAKE_MODE` | Deterministic CI | Extend FakeCritic + fake critique adapter |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Structured finding model | severity, category/stratum, affected_range, affected_tracks, explanation, evidence, suggested_action |
| Four-strata taxonomy + policy | Hard → revise; technical configurable; stylistic/subjective never auto-claim “incorrect” |
| Evaluation engine service | Deterministic check suite covering required metrics |
| Climax / section-contrast check | Compare density/dynamics vs preceding section when brief/form marks climax |
| Bar-range evaluation scope | Selected bars |
| Optional LLM critic adapter | Structured findings + concrete evidence; mocked tests |
| HTTP evaluate endpoint | Session critique for UI without requiring full spine |
| Frontend critique view | Findings list with strata filters; no V2 mutation |
| CriticAgent decision policy | Drive revise from hard failures (+ optional technical thresholds); not analysis status alone |
| Docs | Critic vs Analysis boundary; strata policy; logging |

### Coupling risks to avoid

1. Treating subjective LLM taste as hard constraint / auto-revise default.
2. Mutating `composition.v2` from Critic, evaluation engine, or critique router.
3. Persisting full unbounded analysis reports or full LLM prompts inside durable critique payloads.
4. Letting `ai_agents/` import `agent_artifact_workspace` / SQLite.
5. Breaking existing `agent.critique.v1` promote / role map / FakeCritic approve path.
6. Growing critique logic into `main.py` — prefer `routers/` + `services/`.
7. Logging full findings prose, prompts, keys, or event arrays at INFO.
8. Replacing Analysis tab metric cards with critique-only UX.
9. Inventing `composition.v4` or playable fields inside critique findings.
10. Making ArrangementPlan / candidate the source of “orchestration density” without reading actual V2 events.

## Scope And Decisions

### In scope
- Pydantic evaluation finding + extended `agent.critique.v1` (CritiqueReport).
- Deterministic evaluation engine with required check families (see Metric Matrix).
- Four-strata taxonomy + recommendation policy.
- Scopes: composition, section, track, bars.
- Optional LLM critic adapter via `ai_runtime` (+ fake).
- `POST` evaluate API (under `routers/` — prefer `critique.py` or extend `ai_agents.py` / analysis adjacency; **not** `main.py`).
- CriticAgent wired to engine; FakeCritic updated for CI + climax fixture.
- Frontend critique/analysis findings view (Analysis tab enrichment).
- Reuse workspace promote for autonomous workflow artifacts (no new Alembic unless schema size forces — prefer not).
- Deterministic tests, mocked AI tests, verbose logging, docs.

### Out of scope
- `composition.v4`.
- Training new music-quality models.
- Replacing progressive realize or inventing new Apply CAS.
- Auto-commit on Critic approve.
- Full nine-agent expanded revise graph (RevisionPlan targets may improve but deep revise orchestration stays parent spine).
- Playwright SPA journey (API + unit tests suffice; optional e2e later).
- Changing FluidSynth / Tone.js / export fidelity.
- Making Analysis warnings become stylistic taste detectors wholesale.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP (critique evaluate / ai_agents run / workflow preview)
        ↓
ai_agents/CriticAgent  (thin; no SQLite)
        ↓
services/composition_critique.py  (EvaluationEngine orchestrator)
        ↓
├─ composition_analysis (+ metric analyzers)
├─ generation_constraints / plan constraints (when request present)
├─ instrument_catalog / arrangement validation helpers (read-only)
└─ optional ai_runtime critique adapter (structured findings only)
        ↓
agent.critique.v1 envelope (mutates_composition: false)
        ↓
[Apply only] agent_artifact_workspace promote (existing)
```

**2. Finding model (`CritiqueFindingV1`)**

```json
{
  "finding_id": "…",
  "severity": "info|warning|error",
  "stratum": "hard_constraint|technical|stylistic|subjective",
  "category": "structure|tonality|harmony|cadence|melody|motif|rhythm|density|orchestration|instrumentation|collision|duplication|contrast|dynamics|tension|other",
  "code": "snake_case_stable_code",
  "affected_range": {"start_bar": 9, "end_bar": 12, "start_tick": null, "end_tick": null},
  "affected_tracks": ["track-id-…"],
  "explanation": "Climax section density nearly matches preceding section…",
  "evidence": {
    "metrics": {"climax_note_load": 0.42, "prior_note_load": 0.41, "relative_delta": 0.02},
    "locators": [],
    "refs": ["section:climax", "section:build"]
  },
  "suggested_action": "Increase attack density and peak velocity in bars 9–12 relative to bars 5–8"
}
```

Rules:
- `extra=forbid`; bounded list sizes; explanation ≤500; evidence JSON size-capped (mirror analysis warning details).
- `stratum=subjective` **must not** use severity `error` (cap at `warning`/`info`).
- `stratum=hard_constraint` may use `error` and feeds revise recommendation.
- `code` is stable machine id; UI may show explanation.

**3. Extended `agent.critique.v1`**

Keep existing fields; add:
- `findings: list[CritiqueFindingV1]` (max ~64)
- `scope` digest (kind + ids/bars)
- `stratum_counts: {hard_constraint, technical, stylistic, subjective}`
- `engine_version` / `algorithm_version` string
- Optional `model_critique_status`: `skipped|ok|failed|unavailable`

`reason_codes` remains derived/merged from finding codes (≤16) for backward compatibility with RevisionPlan targeting.

**4. Recommendation policy (locked)**

| Condition | Recommendation |
|-----------|----------------|
| Any `hard_constraint` finding with severity `error` | `revise` |
| Analysis engine could not run / evaluation failed closed | `revise` (existing) |
| Only `technical` warnings | `approve` by default; optional request flag `revise_on_technical=true` may flip |
| Only `stylistic` / `subjective` | **`approve`** (observations recorded; never auto-revise) |
| Empty findings + analysis ok | `approve` |

Document clearly: stylistic climax observation does **not** force revise unless product later opts in — Acceptance Criterion requires the **structured observation**, not mandatory revise.

**5. Metric Matrix (deterministic checks)**

| Required check | Primary inputs | Default stratum | Notes |
|----------------|----------------|-----------------|-------|
| Requested structure compliance | `GenerationConstraints` / brief/form sections vs V2 sections | hard_constraint | When constraints/brief present; else skip |
| Tonal/key consistency | tonality + declared key + constraints | hard / technical | Declared conflict → technical (existing warning); requested key fail → hard |
| Harmonic progression consistency | harmony analysis + declared harmony | technical | Reuse declared_agreement / warnings |
| Cadence quality indicators | melody cadences | stylistic | Observation, not “wrong cadence” as hard |
| Melodic range | melody tessitura + catalog ranges | technical / hard | Catalog out-of-range → hard/technical |
| Melodic contour | melody contour profiles | stylistic | |
| Motif recurrence | repetition motifs/families | stylistic / technical | Missing requested motif recurrence when MotifPlan present → technical |
| Rhythmic diversity | density IOI / attack stats | stylistic | |
| Note density | density metrics + section_summaries | technical / stylistic | Used heavily by climax check |
| Orchestration density | simultaneity / role overlap / track counts | technical / stylistic | |
| Instrument range violations | analysis warning + catalog | hard / technical | |
| Voice collisions | overlapping_same_pitch / dense overlap warnings | technical | |
| Excessive duplication | `excessive_duplicate_notes` | technical | |
| Section contrast | section_summaries deltas | stylistic | Includes climax AC |
| Dynamic/tension curve | tension + velocity aggregates (if present on events) | stylistic | |
| Requested instrumentation compliance | `validate_generation_constraints` instrumentation | hard_constraint | |

**6. Climax contrast acceptance check (locked)**

Detect when intent marks a **climax** (section `type` / label / FormPlan density outline / brief keyword / explicit `requested_climax_section_index`) and the climax section’s density/dynamics are **almost identical** to the preceding section:

- Compare `note_load` and/or attack density from `section_summaries`.
- Compare bounded mean/peak velocity in those tick ranges when events carry velocity.
- If relative delta below threshold (e.g. note_load relative change &lt; ~5–10%, configurable constant), emit finding:
  - `stratum: stylistic` (or `technical` if product later reclassifies — **lock stylistic** so taste≠correctness)
  - `code: climax_lacks_contrast` (stable)
  - `affected_range` = climax bars
  - `evidence.metrics` includes prior vs climax values
  - `suggested_action` names bars and levers (density/dynamics)
- **Must not** mutate Composition.
- Unit test fixture: two sections with near-identical density; climax labeled; assert finding present and V2 fingerprint unchanged.

**7. Optional LLM critic**

- New operation or reuse creative chat under Critic-bound model (`AI_AGENT_CRITIC_MODEL` / runtime resolve).
- Input: bounded analysis projection + finding stubs + scope + brief excerpt — never full event arrays.
- Output: list of findings with **required** `affected_range` or `affected_tracks` or concrete `evidence.refs` (reject generic “needs more emotion” without locus → drop or mark `model_critique_status=failed` partial).
- All LLM findings forced `stratum=subjective`.
- Fake adapter returns deterministic subjective finding citing bars for tests.
- Default: LLM optional / off when no model ready; deterministic engine always runs.

**8. HTTP API**

`POST /critique/evaluate` (preferred new `routers/critique.py`) accepting:
- `composition` (V2)
- optional `scope` (`composition` | `section` | `track` | `bars`)
- optional `constraints` / brief / form-plan intent hooks
- optional `include_model_critique: bool`

Response: session `agent.critique.v1` payload (+ optional bounded analysis sibling). **Read-only**; does not write projects.

Keep spine `POST /ai/agents/critic/run` and workflow preview using the same engine.

**9. Logging**

- INFO: engine_version, scope kind, finding counts by stratum, recommendation, duration_ms, model_critique_status, climax check skipped/hit (code only).
- DEBUG: finding codes, affected bar ranges, metric deltas, constraint stage ids, artifact id prefixes.
- Never INFO: full explanations list, prompts, keys, full analysis report, event arrays, evidence blobs.

**10. Frontend**

- Extend Analysis tab (or adjacent Critic section) to call `/critique/evaluate` with current scope (+ bar selection from editor when available).
- Display findings grouped/filterable by stratum; show severity, bars/tracks, explanation, suggested_action.
- MultiAgentPanel: show critique findings summary from preview `critique` artifact when present.
- Store: session `critiqueResult` / status; never persist into `composition_json`.

## Acceptance criteria mapping

| Criterion | Tasks |
|-----------|-------|
| Distinguish hard / technical / stylistic / subjective | 2–3, 5–6, 14 |
| Do not represent taste as objective correctness | 2, 5, 9, 14 |
| Structured finding fields | 2, 6, 11, 14 |
| Scopes: whole / section / track / bars | 4, 7, 11–12 |
| Optional LLM critic with concrete refs | 8–9, 13 |
| Never modify Composition | 6–7, 10, 13–14 |
| Frontend critique/analysis view | 12 |
| Preserve critiques as project artifacts in workflows | 10 (reuse workspace; verify) |
| Climax nearly identical density → structured observation, no mutation | 6, 13 |
| Tests, mocked AI, logging, docs | 13–15 |

## Commit Plan
- **Commit 1** (tasks 1–5): `feat(critique): finding schema, strata taxonomy, scopes, inventory lock`
- **Commit 2** (tasks 6–9): `feat(critique): evaluation engine, climax check, LLM adapter, CriticAgent wire-up`
- **Commit 3** (tasks 10–12): `feat(critique): evaluate API, workflow artifact verify, frontend critique view`
- **Commit 4** (tasks 13–15): `test(critique): climax AC, mocked AI, architecture gates, docs`

## Tasks

### Phase 0: Inventory

- [x] Task 1: Inventory Critic/Analysis/workspace contracts and freeze non-goals
  Deliverable: Short checklist (module docstring or `services/composition_critique.py` stub) documenting: (a) current thin `CriticAgent` + flat `AgentCritiqueV1`; (b) analysis metrics/warnings to reuse; (c) workspace promote path for `critique` already ships — no new tables unless forced; (d) never mutate V2; (e) subjective ≠ hard; (f) no `composition.v4`. Non-regression test: existing spine FakeCritic still approves and `mutates_composition` stays false.
  LOGGING: DEBUG checklist ids; INFO skip reasons only.
  Files: `backend/app/services/composition_critique.py` (stub), `backend/tests/test_composition_critique_inventory.py` (new); references `ai_agents/agents/critic.py`, `ai_agents/schemas.py`, `analysis_schemas.py`, `services/agent_artifact_workspace.py`.
  Depends on: none.

### Phase 1: Contracts

- [x] Task 2: Define `CritiqueFindingV1` + extend `agent.critique.v1`
  Deliverable: Pydantic models for finding + extended `AgentCritiqueV1` (`findings`, `scope`, `stratum_counts`, `engine_version`, `model_critique_status`). Validators: bounds, forbid playable fields in evidence, subjective cannot be severity `error`, `reason_codes` auto-merge from finding codes when empty. Unit tests accept/reject fixtures (incl. oversized findings list).
  LOGGING: INFO schema field counts; DEBUG reject codes; never finding bodies at INFO.
  Files: `backend/app/ai_agents/schemas.py` and/or `backend/app/critique_schemas.py` (new preferred for DTOs), `artifact_schemas.py` payload validate updates, `backend/tests/test_critique_schemas.py`.
  Depends on: Task 1.

- [x] Task 3: Strata taxonomy + recommendation policy helpers
  Deliverable: Enums/literals for `CritiqueStratum`, category codes, stable finding code registry starter set (incl. `climax_lacks_contrast`). Pure function `recommend_from_findings(findings, *, revise_on_technical=False) -> CritiqueRecommendation`. Tests cover hard→revise, stylistic-only→approve, subjective-only→approve, mixed hard+style→revise.
  LOGGING: DEBUG policy inputs (counts); INFO recommendation + stratum_counts.
  Files: `backend/app/services/composition_critique_policy.py` (new), tests `backend/tests/test_composition_critique_policy.py`.
  Depends on: Task 2.

- [x] Task 4: Evaluation scope model (incl. bars)
  Deliverable: Request/resolved scope DTO for critique: `composition` | `section` | `track` | `bars` with validation (bars requires start/end inclusive, start≤end, within composition). Helper to resolve tick/bar window without mutating V2. Prefer shared locator types with analysis where possible; do not break Analysis API scopes in this task (Analysis `bars` optional follow-up).
  LOGGING: INFO scope kind; DEBUG resolved tick/bar bounds.
  Files: `critique_schemas.py`, helpers in `composition_critique_scope.py` (new), tests.
  Depends on: Task 2.

- [x] Task 5: Error codes + HTTP map for critique evaluate
  Deliverable: Stable codes e.g. `critique_invalid_composition`, `critique_invalid_scope`, `critique_complexity_exceeded`, `critique_model_unavailable` (soft), `critique_payload_rejected` mapped via existing agent/domain error patterns (422/503 as appropriate). Sanitized details only.
  LOGGING: WARN/ERROR with code + fingerprint prefix only.
  Files: `backend/app/critique_schemas.py` or `ai_agents/errors.py` / analysis-style error type; tests for map.
  Depends on: Task 4.

### Phase 2: Engine

- [x] Task 6: Deterministic EvaluationEngine + metric adapters
  Deliverable: `evaluate_composition(...)` orchestrator that: fingerprints input; runs `analyze_composition` (scoped when possible); maps analysis warnings → technical findings; runs constraint checks when constraints/intent provided; runs dedicated adapters for required metric families (structure, tonality, harmony, cadence, melody range/contour, motif, rhythm, densities, orchestration, instrument range, collisions, duplication, section contrast, dynamics/tension, instrumentation). **Climax contrast check** implemented here with configurable thresholds. Assert input composition dump equality after run (no mutation). Returns extended `AgentCritiqueV1` (or intermediate report → critique payload).
  LOGGING: INFO duration_ms + stratum_counts + recommendation; DEBUG per-check skip/hit codes; never full findings at INFO.
  Files: `backend/app/services/composition_critique.py`, optional `composition_critique_checks.py`, tests `backend/tests/test_composition_critique_engine.py` (+ fixtures).
  Depends on: Task 3, Task 4, Task 5.

- [x] Task 7: Wire scopes through engine (section/track/bars)
  Deliverable: Engine honors resolved scopes; bar-range evaluation clips note evidence and affected_range accordingly. Tests for each scope kind producing findings only inside scope (or scope-aware skip). Composition-level section contrast still available on whole-composition scope.
  LOGGING: DEBUG scope clip stats (note counts), not note dumps.
  Files: engine + scope helpers + tests.
  Depends on: Task 6.

- [x] Task 8: Optional LLM / model critic adapter
  Deliverable: Adapter interface + fake implementation returning structured subjective findings with required locus. Real path uses `ai_runtime` resolve (Critic model / creative chat) + strict parse/validate; rejects findings lacking concrete region/property evidence. `include_model_critique=false` or unavailable model → `model_critique_status=skipped|unavailable` without failing deterministic engine.
  LOGGING: INFO model_id prefix + status + subjective_count; DEBUG reject reasons; never prompts/completions at INFO.
  Files: `backend/app/services/llm_composition_critique.py` (new), `ai_runtime` wiring as needed, `backend/tests/test_composition_critique_llm.py` (mocked).
  Depends on: Task 2, Task 6.

- [x] Task 9: Upgrade `CriticAgent` + FakeCritic to EvaluationEngine
  Deliverable: CriticAgent calls engine (deterministic always; model critique per request/descriptor policy). Still emits bounded analysis sibling + critique + optional RevisionPlan on revise. FakeCritic: deterministic approve path preserved for default spine; additional fake path can emit climax finding when fixture requests it without mutating draft. Architecture: no workspace imports.
  LOGGING: INFO agent_id + recommendation + finding counts; DEBUG reason_codes.
  Files: `backend/app/ai_agents/agents/critic.py`, `fake_agents.py`, `typed_emit.py` if needed; tests `test_ai_agents_workflow.py` / critic-focused tests.
  Depends on: Task 6, Task 8.

### Phase 3: API + UI + artifact path

- [x] Task 10: Verify autonomous workflow artifact preservation
  Deliverable: Ensure extended critique payloads still validate on workspace insert/promote (`validate_artifact_payload`); acceptance test that after multi-agent Apply, revision detail still exposes role `critique` with structured findings (when engine emitted them). No new Alembic unless payload caps require column changes (prefer JSON payload only). Confirm `ai_agents/` forbidden-import gate still green.
  LOGGING: INFO promote critique artifact_id prefix + finding_count.
  Files: `artifact_schemas.py` / workspace validate, `backend/tests/test_agent_artifact_acceptance.py` (extend) or `test_critique_apply_binding.py`.
  Depends on: Task 2, Task 9.

- [x] Task 11: `POST /critique/evaluate` router
  Deliverable: New router registered in app; maps domain errors; returns critique report (+ optional analysis summary ref). Does not write SQLite. Unit/API tests with fake composition fixtures including climax case.
  LOGGING: INFO route duration + stratum_counts; DEBUG scope; never composition body.
  Files: `backend/app/routers/critique.py` (new), `main.py` include_router only, tests `backend/tests/test_critique_routes.py`.
  Depends on: Task 5, Task 6, Task 7, Task 8.

- [x] Task 12: Frontend critique/analysis view
  Deliverable: API client `evaluateCritique`; store session fields; Analysis panel (or Critic subsection) renders findings by stratum with range/tracks/explanation/suggested_action; bar scope when selection/loop provides bars; does not write findings into composition JSON. MultiAgentPanel shows compact critique summary from preview when present. Unit tests for normalize/display helpers.
  LOGGING: FE debug under `VITE_LOG_LEVEL`; no full finding dumps at info.
  Files: `frontend/src/api/musicApi.js`, `musicStore.js`, `CompositionAnalysisPanel.jsx` (and/or small `CompositionCritiquePanel.jsx`), `MultiAgentPanel.jsx`, utils + tests.
  Depends on: Task 11.

### Phase 4: Acceptance, hardening, docs

- [x] Task 13: Climax acceptance + mocked AI tests
  Deliverable: Deterministic test: composition with labeled climax section nearly identical density/dynamics to prior section → finding `climax_lacks_contrast` with affected bars; fingerprint before/after equal. Mocked AI test: fake model returns subjective finding with bars; generic no-locus output rejected/dropped. Policy tests ensure stylistic climax does not alone force `revise`.
  LOGGING: caplog assertions for INFO stratum_counts / recommendation where practical.
  Files: `backend/tests/test_composition_critique_climax.py`, extend LLM mock tests.
  Depends on: Task 6, Task 8, Task 9.

- [x] Task 14: Architecture gates + logging safety
  Deliverable: Tests that critique/evaluate paths never mutate composition; critique findings cannot embed tracks/events; subjective≠error; `ai_agents/` still cannot import workspace; INFO logs omit prompts/payloads (pattern assertions where feasible).
  LOGGING: as above.
  Files: extend `test_ai_agents_architecture.py`, critique tests.
  Depends on: Task 9, Task 11, Task 13.

- [x] Task 15: Documentation checkpoint
  Deliverable: New or extended docs: `docs/composition-critique.md` (engine, strata, scopes, policy, climax AC, logging) + update `docs/multi-agent.md` (Critic uses EvaluationEngine; approve≠auto-apply; subjective≠hard) + cross-link `docs/composition-analysis.md` (Analysis = metrics sidecar; Critique = judgment report). Update `AGENTS.md` key entry points if structure changed. `.env.example` only if new env knobs (thresholds / `CRITIQUE_INCLUDE_MODEL_DEFAULT`).
  LOGGING: n/a for prose; keep log policy accurate.
  Files: `docs/composition-critique.md`, `docs/multi-agent.md`, `docs/composition-analysis.md`, `AGENTS.md`, optional `.env.example`.
  Depends on: Task 12, Task 13, Task 14.

## Implementation notes for `/aif-implement`

- Prefer `critique_schemas.py` + `services/composition_critique*.py` over growing `analysis_schemas.py` into a judgment API.
- Reuse `analyze_composition` purity pattern (dump equality) for the evaluation engine.
- Map existing `AnalysisWarning` → finding with `stratum=technical` (or hard when code is clearly constraint-like — default technical; requested-instrument failures come from constraints as hard).
- Keep `FakeCriticAgent` default approve for spine CI unless tests opt into critique fixtures.
- Climax intent resolution order (document in code): explicit request field → FormPlan/brief density climax → section `type`/`id` heuristics (`climax`, `chorus` only if explicitly configured — prefer explicit labels to avoid false positives).
- Threshold constants live in one module (not scattered magic numbers).
- Frontend: follow existing Analysis store patterns; do not add useMemo/useCallback noise unless repo pattern requires.
- Docs policy `yes` → mandatory `/aif-docs` checkpoint at implement completion.

## Non-goals reminder

Do not invent `composition.v4`. Do not auto-apply Critic approve. Do not treat subjective critique as objective failure. Do not persist critique into `composition_json`. Do not let agents write the workspace. Do not replace the Analysis metrics tab. Do not log full prompts or full critique payloads at INFO.

## Appendix — Metric → implementation sketch

| Check | Likely function / source |
|-------|--------------------------|
| Structure / instrumentation / key | `validate_generation_constraints`, plan constraints digest |
| Tonality / harmony / cadence / melody / motif / density / tension | `composition.analysis.v1` groups + warning registry |
| Instrument range / collisions / duplicates | analysis warnings + catalog ranges |
| Section contrast / climax | `section_summaries` + velocity window stats over section ticks |
| Orchestration density | density simultaneity + role overlap heuristics |
| Subjective | LLM adapter only |

## Appendix — Example climax finding (AC shape)

```json
{
  "severity": "info",
  "stratum": "stylistic",
  "category": "contrast",
  "code": "climax_lacks_contrast",
  "affected_range": {"start_bar": 9, "end_bar": 12},
  "affected_tracks": [],
  "explanation": "Requested climax section has nearly identical note density and peak velocity to the preceding section.",
  "evidence": {
    "metrics": {
      "prior_note_load": 0.40,
      "climax_note_load": 0.41,
      "note_load_relative_delta": 0.025,
      "prior_peak_velocity": 84,
      "climax_peak_velocity": 86
    },
    "refs": ["section_index:1", "section_index:2"]
  },
  "suggested_action": "Raise density and peak dynamics in bars 9–12 relative to bars 5–8 so the climax reads as a contrast peak."
}
```
