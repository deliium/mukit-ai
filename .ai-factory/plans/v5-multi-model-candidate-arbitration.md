# Implementation Plan: V5 Controlled Multi-Model Candidate Generation and Arbitration

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-04

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Extend **Generate** (`MusicGenerator.jsx`) with an opt-in Ensemble mode — pick 2..N ready `symbolic_composer` model ids, run arbitration preview, show rejected/surviving candidates with honesty labels, support auto-suggest / human radio / top-N list, Apply only on explicit user commit. Opening Generate never starts ensemble fan-out. Lab tab stays Model Lab research only. Develop/Arrange multi-prompt ballots stay unchanged. No piano-roll redesign
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-04 (`/aif-improve`). Fake sparse/dense need `/ai/models` descriptors + `generate_symbolic_composition` `model_id` dispatch (today fake always echoes `fake:symbolic-tiny`); ship-1 preview request is **client-supplied `composition.plan.v1` + `GenerationConstraints`** (no per-request LLM planner); preference ranking is in-process `LinearPairwiseRanker.rank` only (never `score_pending` / ballot stash — `PreferenceSurface` stays `development|arrangement`); Ensemble Apply projects into `applyGenerationCandidate` envelope with ensemble `generation.provenance.v1` fields; critic ship-1 = annotate only (secondary hard-reject optional and constraint-code allowlisted — never `analysis_failed` alone); **no Collab C** / no new `ACTIONS` (flag-gated preview; Apply uses existing generate-apply `write_score`); always `include_router`, never `/ready`; Task 6 depends on Task 4; Task 7 on 5+6; Task 8 on 5–7
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Franchise milestone appended to `ROADMAP.md` via `/aif-roadmap` after verify (implementation itself did not edit the roadmap)
- Scope: opt-in HTTP + SPA that fans out one hybrid symbolic generate brief across multiple explicit `symbolic_composer` models, hard-validates each candidate before any subjective layer, annotates survivors with critic findings, optionally reorders via the existing preference ranker, and returns a session-only arbitration report with full per-candidate provenance. Selection modes: automatic suggest, human pick, top-N preview. Never invents `composition.v5`. Never presents critic/preference scores as objective musical truth. Caps model count and wall cost. Deterministic fake ensemble path for CI. `ai_agents/` must not import arbitration settings or any new arbitration store
- Predecessors: `.ai-factory/plans/v5-explicit-preference-learning.md` (ballot ranker honesty; Apply stays manual), `.ai-factory/plans/v5-model-lab-training-evaluation.md` (registered `lab:{id}` symbolic composers; `musical_quality_claim=false`), `.ai-factory/plans/v3-hybrid-llm-symbolic-pipeline.md` / shipped hybrid generate + `generate_symbolic_composition`, shipped `composition_critique` / `/critique/evaluate`, shipped arrangement/development multi-candidate preview pattern (single-model directions — not multi-model)

## Roadmap Linkage
Milestone: "V5 Controlled multi-model candidate generation and arbitration"
Rationale: Develop/Arrange already return multi-candidate ballots from one model; preference learning ranks those ballots; Model Lab can register multiple `lab:{id}` composers; critique evaluates a single score. There is no controlled ensemble that fans out across models, hard-rejects invalid outputs before subjective layers, and still lets the user pick a non-top candidate. Appended to `.ai-factory/ROADMAP.md` via `/aif-roadmap` (2026-10-04) after verify; marked complete with the shipped ensemble arbitration surface.

## Goal

Allow multiple symbolic models to propose musical alternatives for one generate brief, then select among them through an explicit layered pipeline:

```text
Model A ─┐
Model B ─┼→ candidates → validators → critic → preference ranker → selection
Model C ─┘
```

Ship:

1. Non-playable session documents **`ensemble.policy.v1`**, **`ensemble.candidate.v1`**, and **`ensemble.arbitration.v1`** (preview report — not a second playable score).
2. Opt-in HTTP surface (`ENSEMBLE_ARBITRATION_ENABLED`, default off) that fans out hybrid symbolic generate across an explicit model list, validates, critiques, ranks, and returns ordered survivors + rejected attempts with provenance.
3. Configurable closed **ensemble strategies** and **selection modes** (`auto_suggest` | `human` | `top_n`).
4. Hard constraint / integrity validation **before** critic and preference ranking.
5. Honesty stamps: `musical_quality_claim=false`; critic and preference layers labeled subjective; UI never says “best music”.
6. Resource ceilings (max models, wall ms, sequential default) + deterministic fake three-model CI path.
7. Generate-panel Ensemble UX + docs.

Acceptance: With `ENSEMBLE_ARBITRATION_ENABLED=1` and fake ensemble composers available, a request names three symbolic model ids (`fake:symbolic-tiny`, `fake:symbolic-sparse`, `fake:symbolic-dense`), strategy `parallel_once`, selection_mode `human`, and a client-supplied tiny `composition.plan.v1` plus hard `GenerationConstraints` (no LLM planner call on the preview path). `GET /ai/models?capability=symbolic_composer` lists all three fake ids as ready under fake mode. `POST /ensemble/arbitration/preview` returns `ensemble.arbitration.v1` with `musical_quality_claim=false`. At least one deliberately invalid model output (or injected invalid fixture path in tests) is rejected at the **validator** stage with a machine code and never reaches preference ranking. Remaining valid candidates each carry provenance (`model_id` echoing the requested id, seed, engine, strategy, attempt ordinal, validation digest). Critic annotations appear on survivors with stratum honesty (subjective findings are not severity `error` alone; ship-1 does not hard-reject on critic alone). When preference ranking gates are on (`PREFERENCE_LEARNING_ENABLED` + `ranking_enabled`), survivors may be reordered in-process with `ranking_applied` true/false; when off, order stays generation/validator order. `suggested_candidate_id` is set for `auto_suggest` / ranked first, but Apply is a separate SPA action via `applyGenerationCandidate`: the user can select a non-suggested survivor and commit that `composition.v2` with ensemble fields in `generation.provenance.v1`. Top-N mode returns at most N survivors in the preview list. With the flag off, preview returns `ensemble_arbitration_disabled`. Opening Generate never starts fan-out and never mutates the working score until Apply. Status lives on `GET /ensemble/arbitration/status` only (not `/ready`).

```text
ensemble.policy.v1  (model_ids[2..MAX], strategy, selection_mode, top_n, budgets)
        + client-supplied composition.plan.v1 + GenerationConstraints
        │
        ▼  POST /ensemble/arbitration/preview  (enabled; no Collab C)
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

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**. **Ensemble** = explicit multi-`symbolic_composer` fan-out for one brief — distinct from Develop/Arrange multi-**prompt-direction** ballots on one language model, distinct from Model Lab multi-experiment **compare** (metrics, not candidate Apply), and distinct from AI job **scheduling** (placement of one job). **Arbitration** = ordered pipeline validators → critic → preference ranker → selection over ensemble candidates. **Validator** = hard integrity + `GenerationConstraints` (objective reject). **Critic** = existing Evaluation Engine findings (advisory / stratified; never objective musical taste). **Preference ranker** = existing `preference.ranker.v1` / `LinearPairwiseRanker` (explicit user preference, not quality). **Selection mode** `auto_suggest` marks a suggestion only; it does **not** write notes. **Human** selection is the radio + Apply. **Top-N** limits how many survivors the preview returns after ranking. **Provenance** = per-candidate `model_id`, seed, engine, strategy, attempt ordinal, validation/critic digests — never full prompts or event arrays in logs. **Fake ensemble** = three deterministic `fake:symbolic-*` composers for CI without torch. **Strategy** = closed enum controlling how model ids map to attempts (ship-1: `parallel_once`).

## Approach Evaluation (locked)

### Part A — Product surface

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Bolt multi-model fan-out into arrangement/development preview loops** | Reuses candidate UI | Those loops are one language model × creative directions; mixing symbolic ensemble semantics would break existing contracts and preference ballot surfaces | **Reject** as sole surface |
| **B. Only Model Lab compare of experiments** | Lab already compares runs | Compare is metrics/listening digests, not Apply-able `composition.v2` candidates; violates “user chooses another candidate” | **Reject** |
| **C. Dedicated `POST /ensemble/arbitration/preview` over hybrid symbolic generate + Generate-panel Ensemble mode; Develop/Arrange/Lab unchanged** | Matches architecture diagram; reuses symbolic generate, critique, preference ranker; clear honesty boundary | New route + UI path | **Accepted** |

### Part B — Where arbitration state lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Durable SQLite table of every candidate composition** | Easy history | Stores note events beside projects; retention/GC; duplicates revision history | **Reject** for ship-1 |
| **B. Persist only suggested winner into working score automatically** | One click | Auto-write violates preview-until-Apply and human override acceptance | **Reject** |
| **C. Session-only `ensemble.arbitration.v1` in the HTTP response (+ SPA store until Apply/dismiss); soft-fail content provenance / generation provenance on Apply; no new score schema** | Matches critique/arrange preview pattern; full provenance on the report | History lost on refresh unless user Applied | **Accepted** |

### Part C — Pipeline order

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Rank / critique first, validate winners only** | Faster subjective path | Invalid scores could top the ranker; violates hard-before-subjective requirement | **Reject** |
| **B. Validate and critique interleaved per model before all models finish** | Early abort | Complicates partial-success semantics and budgets | **Reject** for ship-1 |
| **C. For each attempt: generate → hard validate (reject or survive) → then on the survivor set only: critic annotate → preference rank → selection. Never feed rejected candidates to ranker** | Matches requirement; partial success OK | Critic cost scales with survivors | **Accepted** |

### Part D — Ensemble membership

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Implicitly pick top-N ready composers from `/ai/models`** | Convenient | Surprises operators; may pull `lab:`/`personal:` without consent; scheduling confusion | **Reject** |
| **B. Language-model ensemble (OpenAI + local LLM) producing full scores** | Popular | Bypasses symbolic composer contract; harder fake parity; not the stated “three symbolic models” acceptance | **Reject** for ship-1 |
| **C. Request body lists 2..`ENSEMBLE_MAX_MODELS` explicit ready `symbolic_composer` ids (`fake:*`, MT, `lab:`, `personal:`, plugin). Duplicate ids refused. Unready/unknown → attempt reject code, not silent skip of the whole request when ≥1 other succeeds** | Explicit; Lab-registered models participate; CI uses three fakes | Operator must name ids | **Accepted** |

### Part E — Ensemble strategies

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Free-form strategy scripts / shell hooks** | Flexible | Forbidden RCE surface | **Reject** |
| **B. Only hardcode “always three fakes”** | Tiny | Blocks Lab/MT ensembles and configurable strategies | **Reject** |
| **C. Closed `EnsembleStrategy` enum. Ship-1: `parallel_once` (one attempt per listed model_id, shared plan/constraints, per-model seed = base_seed + ordinal). Reserve optional later `seed_sweep` (same model, N seeds) behind enum but do not implement until needed. Strategy field required on policy** | Configurable without shell; testable | Strategy list must stay small | **Accepted** |

### Part F — Selection modes

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Auto-Apply top candidate into working V2** | Fast | Forbidden by preference/critique axioms and acceptance “user can choose another” | **Reject** |
| **B. Human-only; no suggested id** | Safest | User asked for automatic best-candidate **selection** (suggest) and top-N | **Reject** as sole |
| **C. `selection_mode`: `auto_suggest` (set `suggested_candidate_id` from first after rank), `human` (suggestion optional/null; SPA requires radio), `top_n` (return only first N survivors after rank; suggestion = first). Apply always SPA-explicit** | Covers all three support bullets | SPA must show honesty copy | **Accepted** |

### Part G — Critic role

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Critic severity `error` alone deletes candidates** | Aggressive filter | Subjective/stylistic strata must not become hard rejects; conflates taste with validity; `analysis_failed` is also `hard_constraint`+`error` | **Reject** |
| **B. Skip critic in ship-1** | Smaller | Architecture diagram and “evaluation layers” require it | **Reject** |
| **C. Always run `evaluate_composition` on survivors; attach finding counts / recommendation digest / stratum summary on `ensemble.candidate.v1`. Ship-1 default = **annotate only** (no critic-driven reject). Optional later secondary gate: allowlist only constraint-mapped codes from `findings_from_constraints` — never subjective/stylistic, never bare `analysis_failed`. Stamp `critic_is_subjective_layer=true`** | Layered; honest; avoids false hard rejects | Extra CPU per survivor | **Accepted** |

### Part H — Preference ranker integration

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New neural quality model** | Flashy | Violates honesty; new deps | **Reject** |
| **B. Call `POST /preferences/rank` / `score_pending` (Develop/Arrange ballot path)** | Reuses HTTP | `PreferenceSurface` is only `development\|arrangement`; needs pending ballot stash; couples ensemble to ballot lifecycle | **Reject** for ship-1 |
| **C. Arbitration service loads effective ranking gates + stored ranker weights, projects `project_preference_features` for survivors, and calls pure `LinearPairwiseRanker.rank` in-process when ranking is effectively on; otherwise keep validator order. Report `ranking_applied` + `ranking_is_preference_not_quality=true`. Do not write `preference.choice.v1`, do not stash a pending ballot, do not add `PreferenceSurface="ensemble"`. Ship-1 Apply does not collect a preference choice** | Reuses shipped ranker; clear honesty; no surface enum change | Cold ranker leaves order unchanged | **Accepted** |

### Part I — Resource / cost limits

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Unlimited parallel fan-out** | Max diversity | Host OOM / API contention | **Reject** |
| **B. Flag default on, max 8 models** | Convenient | Dangerous on studio hosts | **Reject** |
| **C. Flag default off; `ENSEMBLE_MAX_MODELS` default 3 (hard ≤ 4); default execution `sequential` (optional `parallel` only when `ENSEMBLE_ALLOW_PARALLEL=1`); wall budget `ENSEMBLE_MAX_WALL_MS`; refuse oversize model list with `ensemble_model_limit`** | Matches Model Lab caution | Slightly slower sequential CI | **Accepted** |

### Part J — Fake / CI without torch

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require three real MT / Lab checkpoints in default pytest** | Realistic | Core deps exclude torch; flaky | **Reject** |
| **B. Docs-only acceptance** | Cheap | Not enforceable | **Reject** |
| **C. Three deterministic fake composers `fake:symbolic-tiny` (existing), `fake:symbolic-sparse`, `fake:symbolic-dense` with distinguishable note material under the same plan/constraints. Test injects one invalid composition path to prove validator rejection. All unit tests mocked/fake; no torch** | Matches acceptance; deterministic | Fakes are not trained weights — document that | **Accepted** |

### Part K — Locked formulas and refuse codes

1. **Enabled:** preview requires `ENSEMBLE_ARBITRATION_ENABLED` truthy (same truthy set as collaboration); else `ensemble_arbitration_disabled` (HTTP 503). `GET /ensemble/arbitration/status` and `GET …/strategies` always 200 (readable when disabled).
2. **Model arity:** `len(model_ids)` in `[2, ENSEMBLE_MAX_MODELS]`; else `ensemble_model_limit`. Duplicate ids → `ensemble_payload_refused` or `ensemble_selection_invalid`.
3. **Hard before subjective:** any candidate failing integrity or hard `GenerationConstraints` is listed under `rejected_attempts` with stage=`validator` and must not appear in `candidates` passed to critic/ranker.
4. **Honesty:** every `ensemble.arbitration.v1` sets `musical_quality_claim=false`, `critic_is_subjective_layer=true`, `ranking_is_preference_not_quality=true`.
5. **Auto never writes:** no route writes `tracks[].events[]`; Apply is SPA `applyGenerationCandidate` only.
6. **Provenance:** each candidate and rejected attempt records `model_id` (echo requested id), `seed`, `engine`, `strategy`, `attempt_ordinal`, `stage`, codes/digests — never full prompts or event arrays in logs. Apply stamps ensemble fields onto `generation.provenance.v1` / `generation_parameters`.
7. **ai_agents forbid:** `from app.ensemble_arbitration_settings` and `from app.services.ensemble_arbitration_store` (if a store module is added later). Prefer no store in ship-1; still add forbid strings for settings.
8. **No `/ready` coupling:** always `include_router(ensemble_arbitration)` in `main.py`; status not on `/health` or `/ready`.
9. **Opening Generate:** status/strategies GET only — no preview until the user runs Ensemble.
10. **Score axiom:** never invent `composition.v5`; survivors are full `composition.v2` documents inside the session report only until Apply.
11. **Lab compare ≠ arbitration:** do not route through `POST /model-lab/compare`.
12. **Payload refuse:** reject body keys `command`, `argv`, `shell`, and embedded secret/note abuse via existing guards where applicable → `ensemble_payload_refused`.
13. **Plan input:** preview body carries client-supplied `composition.plan.v1` + `GenerationConstraints`; ship-1 does **not** run the hybrid LLM planner on this route.
14. **No Collab C:** preview is flag-gated only; do not add `run_ensemble_arbitration` to `collaboration_permissions.ACTIONS` or call `authorize_studio_lab`. Apply authorization stays on existing generate-apply / project `write_score`.
15. **Ranking path:** never call `score_pending` or `POST /preferences/rank` from arbitration; never add `PreferenceSurface="ensemble"` in ship-1.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Symbolic generate | `generate_symbolic_composition` in `symbolic_composition_generate.py`; dispatches `fake` / MT / `personal:` / `lab:` / plugin. Fake path currently always returns `FAKE_SYMBOLIC_MODEL_ID` | Fan-out call per `model_id` with shared plan + seed offset; **must** dispatch sparse/dense and echo requested id |
| Fake tiny | `fake_symbolic_composer.py` / `fake:symbolic-tiny`; sole descriptor via `default_fake_symbolic_descriptor` in `ai_runtime/runtimes/music_transformer.py` | Keep; add sparse/dense note shapes **and** registry descriptors |
| Hybrid generate | `llm_music_generator.py` hybrid_plan_symbolic; single `composer_model_id`; builds plan then calls symbolic generate | Ensemble preview **skips** planner; accepts client plan + constraints |
| Validation | `validate_composition_integrity`, `GenerationConstraints` enforcement helpers | Validator stage |
| Critique | `evaluate_composition`; constraint findings via `findings_from_constraints`; `analysis_failed` is also `hard_constraint`+`error` | Annotate survivors only in ship-1 |
| Preference ranker | `LinearPairwiseRanker` + `project_preference_features`; HTTP `score_pending` needs ballot + surface `development\|arrangement` | In-process `rank` only when gates on |
| Generate Apply | `MusicGenerator.jsx` → `musicStore.applyGenerationCandidate` + `buildGenerationMetaFromCandidate` | Project ensemble survivor into that envelope |
| Multi-candidate UX patterns | Develop/Arrange candidate radio + Apply; preference suggest ≠ Apply | Mirror honesty + Apply gate in Generate Ensemble UI |
| Model discovery | `GET /ai/models?capability=symbolic_composer` incl. `lab:{id}` | Populate model multi-select (must include three fakes) |
| Model Lab | Registered labs usable as ensemble members when ready; Collab C on Lab mutates | Predecessor only; **do not** copy Collab C onto ensemble preview |
| Opt-in flags | Collaboration / preference / Model Lab parsers | Copy truthy parser for `ENSEMBLE_ARBITRATION_ENABLED` |
| Latest migration | `20261004_0031_model_lab` | Ship-1 needs **no** new table if session-only |
| Agent boundary | `test_ai_agents_architecture.py` forbids store/settings imports | Add ensemble settings forbid |
| Generate UI | `MusicGenerator.jsx` pipeline + single composer select | Ensemble mode multi-select + results panel |

### Gaps (must build)

- Ensemble policy / candidate / arbitration DTOs and settings
- Two additional deterministic fake symbolic composers **plus** `/ai/models` descriptors and generate dispatch that echoes `model_id`
- Pure pipeline orchestrator (order lock + honesty stamps; critic annotate-only)
- Fan-out service accepting client-supplied plan + constraints (no LLM planner)
- HTTP router + status (flag-gated; no Collab C; not on `/ready`)
- In-process preference rank wiring (not `/preferences/rank`)
- Generate-panel Ensemble UX: preview, reject list, suggest banner, top-N, stage→`applyGenerationCandidate` with ensemble provenance
- Acceptance tests for three-model fan-out, validator reject-before-rank, human override Apply
- Docs (`docs/ensemble-arbitration.md`) + AGENTS/DESCRIPTION/ARCHITECTURE + `.env.example` pointers

### Coupling risks

- Do not teach arrange/develop previews to call ensemble (keeps preference ballot surfaces stable)
- Do not call `score_pending` or widen `PreferenceSurface` in ship-1
- Do not let critic findings (including `analysis_failed`) hard-reject by default
- Do not auto-Apply suggested candidate
- Do not copy Model Lab Collab C / `authorize_studio_lab` onto preview
- Do not claim Lab compare winners inside arbitration UI copy
- Sequential default avoids starving the API process when MT/Lab torch paths exist

## Scope And Decisions

### In scope
- Session arbitration preview for symbolic/hybrid note generation across explicit models
- Closed strategies (`parallel_once`) and selection modes
- Hard validation before critic/rank
- Provenance on every attempt
- Resource caps + fake three-model CI
- Generate UI Ensemble mode
- Docs + honesty language

### Out of scope (ship-1)
- Language-model (LLM) note ensembles
- Per-request hybrid LLM planner on the arbitration preview route
- Auto-Apply / silent working-score writes
- Durable candidate warehouse / SQLite history browser
- Changing Develop/Arrange single-provider multi-direction loops into multi-model
- Model Lab train/compare changes
- Claiming musical quality winners
- `composition.v5`
- Marketplace of ensemble presets
- Parallel fan-out as default
- Preference choice collection / `PreferenceSurface="ensemble"` / ballot stash on Apply
- Collab C / new studio `ACTIONS` for ensemble preview
- Critic-driven hard-reject (annotate only unless a later allowlisted secondary gate)
- Agents-tab autonomous ensemble stage

### Architecture decisions (locked)

**1. Documents**

| Document | Role |
|----------|------|
| `ensemble.policy.v1` | strategy, model_ids, selection_mode, top_n, base_seed, max_wall_ms (echo), execution (`sequential`\|`parallel`) |
| `ensemble.candidate.v1` | candidate_id, composition.v2, provenance, validation ok, critic summary (counts/digest only), preference_score optional, rank_index |
| `ensemble.rejected_attempt.v1` | model_id, stage (`generate`\|`validator`), error code, provenance subset — no composition body required (no `critic_hard` stage in ship-1) |
| `ensemble.arbitration.v1` | policy echo, candidates[], rejected_attempts[], suggested_candidate_id, ranking_applied, honesty booleans, wall_ms, musical_quality_claim=false |
| `ensemble.arbitration.request.v1` | **required** `composition.plan.v1` + `GenerationConstraints` + policy (no prompt-only / planner-required body in ship-1) |

**2. Settings (`ensemble_arbitration_settings.py`)**

| Env | Default | Notes |
|-----|---------|-------|
| `ENSEMBLE_ARBITRATION_ENABLED` | off | Preview routes |
| `ENSEMBLE_MAX_MODELS` | 3 | Hard ≤ 4 |
| `ENSEMBLE_MAX_WALL_MS` | e.g. 120000 | Soft budget; exceed → `ensemble_budget_exceeded` after in-flight attempt completes |
| `ENSEMBLE_ALLOW_PARALLEL` | off | Parallel fan-out opt-in |
| `ENSEMBLE_FAKE` | off | Force fake composers path in tests when set (optional; tests may set model ids explicitly) |

**3. HTTP map**

| Method | Path | Notes |
|--------|------|-------|
| GET | `/ensemble/arbitration/status` | always 200; enabled/caps; not on `/ready` |
| GET | `/ensemble/arbitration/strategies` | closed strategy/selection catalogs; readable when disabled |
| POST | `/ensemble/arbitration/preview` | main pipeline; requires enabled; **no** Collab C / studio-lab authorize |

Error codes (non-exhaustive): `ensemble_arbitration_disabled`, `ensemble_model_limit`, `ensemble_budget_exceeded`, `ensemble_payload_refused`, `ensemble_model_unready`, `ensemble_no_survivors`, `ensemble_strategy_unknown`, `ensemble_selection_invalid`.

**4. Fake composers + discovery**

| model_id | Behavior |
|----------|----------|
| `fake:symbolic-tiny` | Existing tiny fixture-shaped output |
| `fake:symbolic-sparse` | Same constraints; fewer/longer notes (distinguishable fingerprint) |
| `fake:symbolic-dense` | Same constraints; more/shorter notes |

All three must: (a) satisfy hard constraints for the acceptance happy path; (b) register as ready `symbolic_composer` descriptors on bootstrap under fake mode; (c) make `generate_symbolic_composition(..., model_id=…)` return provenance `model_id` equal to the requested id. A separate test helper injects an invalid composition for one model_id to prove validator rejection.

**5. UI + Apply**

`MusicGenerator.jsx` (+ helpers `ensembleArbitrationForm.js`, `ensembleArbitrationApi.js`, optional `stageEnsembleSurvivorAsGenerationCandidate` in `compositionCandidateLifecycle.js` or sibling). When pipeline is `hybrid_plan_symbolic` and status.enabled, show Ensemble toggle → multi-select models (from `/ai/models`) → supply/attach plan fields as required by request DTO → Run ensemble → results: rejected codes, survivor cards, honesty banner (“Preference/critic scores are not musical truth”), selection mode controls. **Apply:** project selected survivor into the existing `generationCandidate` envelope (operationType `generate-apply`, fingerprints, `generation_parameters` / `generation.provenance.v1` with ensemble pipeline id, selected `model_id`, seed, strategy, attempt ordinal, sibling model ids) then call `applyGenerationCandidate`. Opening Generate = status/strategies GET only.

**6. Docs axiom**

`docs/ensemble-arbitration.md` must state pipeline order, honesty, caps, client-supplied plan contract, in-process ranking (not preference ballot HTTP), no Collab C, and that Model Lab compare is not arbitration. Cross-link preference-learning, composition-critique, hybrid-generation, model-lab. `.env.example` lists ensemble knobs.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: add ensemble arbitration contracts and fake multi-model composers`
- **Commit 2** (after tasks 4–6): `feat: run layered ensemble arbitration preview API`
- **Commit 3** (after tasks 7–8): `feat: Generate panel ensemble selection UX`
- **Commit 4** (after task 9): `docs: document multi-model candidate arbitration`

## Tasks

### Phase 1: Contracts and fake models
- [x] Task 1: Add ensemble arbitration schemas and settings
- [x] Task 2: Add deterministic sparse/dense composers + registry dispatch
- [x] Task 3: Implement pure arbitration pipeline helpers (validate → critic annotate → rank → select)

### Phase 2: Orchestration and HTTP
- [x] Task 4: Implement fan-out orchestration service (client plan + budgets + in-process rank wiring)
- [x] Task 5: HTTP router + status + strategies catalog (no Collab C, not on `/ready`)
- [x] Task 6: Harden preference ranking gates + honesty stamps + no-survivor errors (depends on 4)

### Phase 3: UI, docs, acceptance
- [x] Task 7: Generate-panel Ensemble mode UI + `applyGenerationCandidate` staging (depends on 5, 6)
- [x] Task 8: Deterministic mocked acceptance tests (depends on 5–7)
- [x] Task 9: Docs, AGENTS.md, architecture forbid, DESCRIPTION + `.env.example` pointers

<!-- Commit checkpoint: tasks 1-3 -->
<!-- Commit checkpoint: tasks 4-6 -->
<!-- Commit checkpoint: tasks 7-8 -->
<!-- Commit checkpoint: task 9 -->

### Task 1: Add ensemble arbitration schemas and settings

Create `backend/app/ensemble_arbitration_schemas.py` and `backend/app/ensemble_arbitration_settings.py` with documents and env knobs from Architecture decisions. Closed enums: `EnsembleStrategy` (`parallel_once`), `EnsembleSelectionMode` (`auto_suggest`, `human`, `top_n`), execution (`sequential`, `parallel`). Clamp helpers for model arity and top_n (`1..max_models`). `musical_quality_claim` must be `Literal[False]`. Request body **requires** `composition.plan.v1` + `GenerationConstraints` + policy; forbids free-form shell keys and duplicate model ids. Export shared fake model id string constants used by Task 2 tests.

`backend/tests/test_ensemble_arbitration_schemas.py` accepts a policy with three fake model ids + `human` selection + a minimal plan/constraints; rejects 1 model, 5 models when hard max is 4, unknown strategy, `top_n=0`, missing plan, embedded `command` key, duplicate model ids, and any attempt to set `musical_quality_claim=true`.

LOGGING: INFO on settings load (enabled, max_models, allow_parallel, max_wall_ms). DEBUG schema reject with field + code, not full payload. Levels follow `LOG_LEVEL`.

### Task 2: Add deterministic sparse/dense composers + registry dispatch

Extend `backend/app/services/fake_symbolic_composer.py` so `fake:symbolic-sparse` and `fake:symbolic-dense` produce valid `composition.v2` under the same plan/constraints as tiny, with **distinct** source fingerprints / note counts. Wire `generate_symbolic_composition` so a requested `fake:symbolic-*` id selects the matching generator and returns provenance `model_id` equal to that id (today the fake backend always returns `FAKE_SYMBOLIC_MODEL_ID`). Register ready descriptors for all three ids in `ai_runtime` bootstrap (`default_fake_symbolic_descriptor` pattern in `ai_runtime/runtimes/music_transformer.py` / `bootstrap.py`) so `GET /ai/models?capability=symbolic_composer` lists them under fake mode. Do not require torch. Do not write `DATASET_ROOT`.

`backend/tests/test_fake_symbolic_ensemble.py` generates from one shared plan for all three ids and asserts: all validate; fingerprints or event counts differ pairwise; each result `model_id` echoes the request; discovery lists all three when fake-ready.

LOGGING: DEBUG generate with model_id + seed + event_count; never log full event arrays.

Depends on Task 1 (shared model-id literals / constants from schemas or a tiny shared constants module referenced by both).

### Task 3: Implement pure arbitration pipeline helpers (validate → critic → rank → select)

Add `backend/app/services/ensemble_arbitration_pipeline.py` with pure(ish) functions that take already-generated composition attempts and:

1. Run hard validators; partition rejected vs survivors
2. Run critic on survivors; attach summaries only (**no** critic-driven reject in ship-1)
3. When a `PreferenceRankerV1` (or None) + `ranking_enabled` flag is passed in, project preference features and call `LinearPairwiseRanker.rank`; else stable validator order
4. Apply selection_mode / top_n to produce `suggested_candidate_id` and truncated list

No FastAPI. No SQLite in the pure module (ranker model passed in). Never call `score_pending`. Always set honesty booleans on the built report.

`backend/tests/test_ensemble_arbitration_pipeline.py` feeds three compositions (tiny/sparse/dense fixtures) plus one invalid dict; asserts invalid never appears in ranked candidates; with a warm ranker preferring dense features, dense ranks first; `human` mode still returns all survivors; `top_n=2` returns two; `musical_quality_claim is False`; critic findings present but do not drop a valid survivor.

LOGGING: DEBUG partition counts (generated/rejected/survived); INFO selection mode + suggested id prefix; never log finding explanations at INFO.

Depends on Tasks 1, 2.

### Task 4: Implement fan-out orchestration service with budgets and provenance

Add `backend/app/services/ensemble_arbitration_service.py` that: checks enabled + arity; resolves each model_id readiness; **accepts client-supplied `composition.plan.v1` + `GenerationConstraints`** (does not call the hybrid LLM planner); runs attempts sequentially by default (parallel only when allowed); applies seed = base_seed + ordinal; calls `generate_symbolic_composition(plan, model_id=…, seed=…)`; records provenance with echoed model ids; invokes Task 3 pipeline; enforces wall budget between attempts; maps generate failures to rejected_attempts stage=`generate`. Wire effective preference ranking gates here (load settings + ranker weights; pass into pipeline) so honesty/`ranking_applied` are correct before HTTP.

`backend/tests/test_ensemble_arbitration_service.py` with three fake ids + supplied plan completes a preview report; unready id becomes rejected generate attempt while others survive; exceeding model limit raises domain error; disabled raises `ensemble_arbitration_disabled`; assert no planner/LLM invoke on this path (mock or call-count).

LOGGING: INFO start/complete with model count, survivor/reject counts, wall_ms; WARN on budget stop; never log plan JSON wholesale at INFO.

Depends on Task 3.

### Task 5: HTTP router + status + strategies catalog

Add `backend/app/routers/ensemble_arbitration.py`, always `include_router` in `main.py` (Model Lab pattern). Map domain errors to HTTP. Status always 200. Strategies GET allowed when disabled. Preview requires enabled. **Do not** add Collab C / `authorize_studio_lab` / new `ACTIONS`. Do **not** add ensemble to `/ready` or `/health`. Update `test_ai_agents_architecture.py` to forbid `from app.ensemble_arbitration_settings`.

`backend/tests/test_ensemble_arbitration_router.py` covers status when disabled, preview happy path when enabled+fake with plan body, disabled preview refuse, model limit mapping, payload refuse; assert collaboration helpers are not required for preview.

LOGGING: INFO route preview with codes + counts; no bodies.

Depends on Task 4.

### Task 6: Harden preference ranking gates + honesty stamps + no-survivor errors

Finalize service/router behavior: when ranking off or cold, `ranking_applied=false` and order = validator survivor order. When all attempts rejected, return `ensemble_no_survivors` (HTTP 422) with rejected_attempts populated — do not invent a filler score. Response JSON always includes the three honesty fields. Explicitly assert tests never call `POST /preferences/rank` / `score_pending` from this path.

Tests: ranking off keeps generation order; warm in-process ranker reorders; all-invalid → no_survivors; honesty fields frozen as locked.

LOGGING: DEBUG ranking_applied boolean only (no weights).

Depends on Task 4 (service ranking + honesty). Task 5 may land in parallel for HTTP mapping, but this task’s tests may use the service directly or the router once Task 5 exists — implementer lands service assertions first; router-level honesty checks after Task 5.

### Task 7: Generate-panel Ensemble mode UI (auto / human / top-N + Apply override)

Extend `frontend/src/components/MusicGenerator.jsx` with Ensemble controls when hybrid + status.enabled. Add `frontend/src/api/ensembleArbitrationApi.js` and `frontend/src/utils/ensembleArbitrationForm.js`. Multi-select from `/ai/models` symbolic composers. Show survivors, rejected codes, honesty banner, suggested highlight, allow selecting a non-suggested candidate. **Apply:** project selected survivor into the existing `generationCandidate` envelope (optional helper `stageEnsembleSurvivorAsGenerationCandidate` beside `compositionCandidateLifecycle.js`) including ensemble `generation.provenance.v1` / `generation_parameters` fields, then call `applyGenerationCandidate` — no new durable Apply API. Top-N mode reflects returned list length. Opening Generate fetches status/strategies only.

Frontend unit tests for form clamps (model arity, top_n, selection mode), staging helper provenance fields, and “suggestion does not Apply”.

LOGGING: `console.debug` ensemble run id / suggested id; never dump compositions at info.

Depends on Tasks 5, 6.

### Task 8: Deterministic mocked acceptance tests (three models, reject, rank, human override)

Add `backend/tests/test_ensemble_arbitration_acceptance.py` (and frontend unit coverage as needed) that encodes the Goal acceptance vector: three fake models listed on `/ai/models`; client-supplied plan; one injected invalid; survivors ranked in-process when gates on; suggested id set under `auto_suggest`; staging + `applyGenerationCandidate` of a non-suggested survivor yields that composition’s pitches/fingerprint and ensemble provenance fields; flag off refuses preview; `musical_quality_claim` is false; no Collab C requirement.

LOGGING: tests assert log extras omit event arrays (spy or logger cap where the project already patterns this).

Depends on Tasks 5, 6, 7.

### Task 9: Docs, AGENTS.md, architecture forbid, DESCRIPTION pointers

Add `docs/ensemble-arbitration.md`. Update `README.md` feature bullet, `AGENTS.md` structure/docs tables, `.ai-factory/DESCRIPTION.md` / `ARCHITECTURE.md` module rows, and `.env.example` knobs (`ENSEMBLE_ARBITRATION_ENABLED`, max models, wall ms, allow parallel). Cross-link preference-learning, composition-critique, hybrid-generation, model-lab. State clearly: critic/preference layers are not objective musical truth; ranking is in-process not ballot HTTP; preview skips LLM planner; Lab compare is a different product surface; no Collab C on preview.

LOGGING: n/a (docs). Verify docs checkpoint satisfies `Docs: yes`.

Depends on Task 8.

## Test Plan

| Case | Expect |
|------|--------|
| Three fake models on `/ai/models` | All three ready under fake mode |
| Three fake models happy path | ≥2 survivors (ideally 3), echoed `model_id` provenance, claim false |
| Client-supplied plan | Preview succeeds without LLM planner invoke |
| Injected invalid model output | stage=`validator` reject; absent from rank input |
| Preference warm ranker (in-process) | Dense-like candidate first when gates on; no `/preferences/rank` |
| Ranking off | Generation/validator order preserved; `ranking_applied=false` |
| Critic annotate | Findings present; valid survivor not dropped |
| `auto_suggest` | `suggested_candidate_id` set; no score write |
| Human override Apply | `applyGenerationCandidate` commits non-suggested V2 + ensemble provenance |
| `top_n=2` | At most 2 candidates returned |
| Flag off | `ensemble_arbitration_disabled` |
| Model list length 1 or >max | `ensemble_model_limit` |
| All rejected | `ensemble_no_survivors` |
| No Collab C | Preview works without studio-lab authorize |
| ai_agents import guard | settings import forbidden |
| Parallel default | sequential unless allow flag |

## Verification Gate

- [x] Pipeline order hard-coded in service (validator before critic/rank) with tests
- [x] Honesty fields present on every preview response
- [x] Apply never called by preview route; Apply uses `applyGenerationCandidate` staging
- [x] Fake sparse/dense discovered + provenance echo
- [x] No `score_pending` / no Collab C / not on `/ready`
- [x] Three-model fake acceptance green under pytest paths listed above
- [x] Docs checkpoint complete (incl. `.env.example`)
- [x] No `ROADMAP.md` edit from implementation
- [x] No `composition.v5`

## Open Questions

None blocking ship-1. Deferred: preference ballot collection / `PreferenceSurface="ensemble"` on Apply; LLM planner-before-fan-out; `seed_sweep` strategy; critic allowlisted secondary hard-reject; Collab C for ensemble; parallel defaulting heuristics; Agents-tab autonomous ensemble stage.
