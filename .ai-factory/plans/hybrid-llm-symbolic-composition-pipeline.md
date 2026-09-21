# Implementation Plan: Hybrid LLM Planner + Symbolic Note Generation Pipeline

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship a **configurable LangGraph generation pipeline** where an LLM produces a typed non-playable `CompositionPlan` and a dedicated local symbolic model (Music Transformer + tokenizer) produces `composition.v2` note events, with separate failure/repair stages, seed control, multi-stage provenance, and A/B eval vs LLM-only — without removing existing V2 LLM workflows or treating the plan as a second score

## Roadmap Linkage
Milestone: "Hybrid LLM planner + symbolic note generation pipeline"
Rationale: AI runtime already reserves `generate_planner` / `generate_composer` and `symbolic_composer` capability; Music Transformer + tokenizer already generate valid V2 offline/opt-in HTTP; embeddings can condition symbolic prompts — but generate still requires the language model to emit every low-level note. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint). Prior milestone "Style/semantic embeddings and conditioning for symbolic composition" is already complete.

## Goal

Stop requiring a language model to directly generate every low-level note when a specialized local symbolic model is available. Users select **Hybrid** generation, enter a normal textual composition prompt, and receive a valid `composition.v2` where planning is LLM-produced and note events come from the symbolic model — with hard constraints, deterministic seeds, and stage provenance preserved.

```text
Text prompt
   →
LLM planner (LangGraph plan nodes)
   →
CompositionPlan (non-playable)
   →
Symbolic Music Model (tokens → decode)
   →
Canonical Composition V2
   →
deterministic validation / repair
```

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| LangGraph generate | `llm_music_generator.py`: `plan_form` → `plan_harmony` → `plan_themes` → `compose_melody` → `compose_bass` → `compose_accompaniment` → `realize_themes` → assemble → normalize → validate → repair | Keep graph shell; branch after plan nodes for hybrid; leave LLM-only path intact |
| Internal plan DTOs | `ComposerFormPlan`, `ComposerHarmonyPlan`, `ComposerThemePlan` in `composition_planner.py` | Lift into versioned `CompositionPlan` envelope; do **not** invent playable events from plan fields |
| Hard constraints | `GenerationConstraints` frozen snapshot; conformance in validate/repair | Carry unchanged through hybrid graph; plan must satisfy hard fields before symbolic stage |
| Theme relative cells | `ComposerThemeRelativeNote` (relative offsets, not playable placeholders) | Motif/theme section of CompositionPlan; optional seed into tokenizer prefix |
| AI ops reserved | `AiOperation.GENERATE_PLANNER`, `GENERATE_COMPOSER`; env `AI_OP_*`; routing collapses both → `GENERATE` today | Uncollapse when pipeline ≠ `llm_only`; route composer to `symbolic_composer` |
| Capabilities | `ModelCapability.SYMBOLIC_COMPOSER` exists; `GENERATE_COMPOSER` still maps to `language_planner` | Fix default capability for composer op; register ready symbolic runtime |
| Music Transformer | `generate_composition(checkpoint, conditioning, prefix, seed, sample_config)` → V2 + report; CLI + optional `POST /music-transformer/generate` | Primary symbolic note engine behind graph nodes / service adapter |
| Tokenizer | `TokenizerConditioningV1`, encode/decode/repair; conditioning prefix tokens | Bridge plan → conditioning + optional structure prefix |
| Fake LLM | `LLM_FAKE_MODE` deterministic staged JSON | Fake planner outputs for hybrid CI; do not require real LLM |
| Tiny MT fixtures | `backend/tests/fixtures/music_transformer/` tiny arch/train/seed | Fixture / tiny deterministic symbolic stub for CI without heavy GPU |
| Provenance | `AiProvenance` + `generation_parameters`; stage routing extras | Multi-stage provenance: planner + symbolic composer per response |
| Style conditioning | Embeddings `style_reference` on generate/develop | Optional: inject reference summary into planner prompt and/or tokenizer labels (never artist≡style id) |
| Frontend generate | `MusicGenerator.jsx` + `POST /llm/generate-music-json`; preview-first apply | Add pipeline selector **Hybrid** without removing LLM-only |
| Eval/compare | MT `compare.py`, listening sets, symbolic eval metrics (≠ quality) | A/B harness: same prompt → llm_only vs hybrid; report validity / constraint / seed digests |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| No versioned `CompositionPlan` contract | Form/harmony/theme are internal graph state only; not a stable API/schema boundary |
| Plan vs playable confusion risk | Must forbid plan fields that look like `tracks[].events[]` or compete with V2 as score |
| No hybrid LangGraph branch | Compose nodes always LLM; no symbolic node after plan |
| `generate_planner` / `generate_composer` unused | Always collapsed to `generate` |
| No symbolic model in AI registry | MT is opt-in HTTP/CLI only; not `GET /ai/models?capability=symbolic_composer` ready |
| No pipeline enum on generate request | Frontend cannot select Hybrid / continuation / variation |
| Repair is composition-centric only | No distinct invalid-plan vs invalid-token vs V2-validation repair lanes |
| Seed not exposed on LLM generate path | MT has `seed`; generate-music-json does not wire hybrid seed |
| Single-stage provenance | Response surfaces one provider/model; hybrid needs both stages |
| No A/B eval vs LLM-only | Compare CLI is experiment-vs-experiment, not pipeline modes |
| Frontend | No Hybrid mode control; generate always assumes LLM emits notes |

### Coupling risks to avoid

1. Making `CompositionPlan` a second playable representation (events, MIDI, or “draft tracks”).
2. Inventing audible notes from `harmony` / form / density alone — V2 `tracks[].events[]` remains sole playable source.
3. Removing or regressing LLM-only LangGraph generate / repair / fake mode.
4. Loading torch / large checkpoints in the default web image path without opt-in settings (mirror MT API gate).
5. Silently falling back hybrid → LLM-only note generation while claiming symbolic provenance.
6. Collapsing planner and composer provenance into one fake model id.
7. Logging full prompts, plan dumps with freeform instructions, event arrays, or checkpoint absolute home paths at INFO.
8. Growing hybrid orchestration in `main.py` — prefer `services/` + small router hooks; keep MT engine in `music_transformer/`.
9. Equating artist/composer names with style conditioning (reuse embeddings policy).
10. Blocking uvicorn with train jobs or requiring ROCm for acceptance (CI uses tiny/fake symbolic).

## Approach Evaluation (locked)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Parallel second endpoint only** (`/hybrid/generate`) | Isolates risk | Duplicates constraints/provenance/UI; drifts from LangGraph | Reject as sole path |
| **B. Replace compose_* nodes with symbolic always** | Simple graph | Breaks LLM-only acceptance; no A/B | Reject |
| **C. Pipeline-parameterized LangGraph** + shared validate/repair | Reuses constraints, fake LLM planner, MT inference; clear modes | Graph complexity; need careful routing | **Accepted** |
| **D. Symbolic-only generate (no LLM plan)** | Useful continuation/variation | Does not meet Hybrid acceptance (needs LLM plan) | **Supported as additional pipelines**, not Hybrid default |

**Decision:** Implement **pipeline-parameterized generation** on the existing generate entry (`POST /llm/generate-music-json` or thin alias), defaulting to `llm_only`. Hybrid uses LLM plan nodes → `CompositionPlan` validate/repair → symbolic generate nodes → shared V2 normalize/validate/repair. Continuation/variation pipelines may skip LLM plan and use prefix composition + plan fragment.

## Scope And Decisions

### In scope
- Versioned typed `CompositionPlan` (schema + validators + error codes).
- LangGraph pipeline modes: `llm_only`, `hybrid_plan_symbolic`, `symbolic_continuation`, `symbolic_variation`.
- Symbolic generation nodes wrapping Music Transformer inference (+ tiny fake symbolic for tests).
- Hard-constraint carry-through; separate repair for plan / tokens / composition.
- Deterministic seed on hybrid/symbolic paths.
- Multi-stage provenance (planner + composer).
- AI runtime: activate `GENERATE_PLANNER` / `GENERATE_COMPOSER`; register symbolic composer model when configured.
- Frontend Hybrid selector + provenance display.
- A/B evaluation support (pytest + optional CLI) between llm_only and hybrid.
- Tests (mocked planner + tiny symbolic), verbose logging, docs.
- Optional bridge from existing `style_reference` into plan conditioning (bounded).

### Out of scope
- Retraining Music Transformer or claiming hybrid quality > LLM-only.
- Replacing arrangement / development / reharmonize pipelines in this plan (may call shared symbolic adapter later).
- Making plan persist as project composition or revision body.
- Auto-downloading weights; requiring GPU for default compose.
- Removing V1 migration paths or LLM-only generate.
- Artist-style marketplace / copyright circumvention features.

### Architecture decisions (locked)

**1. CompositionPlan is not playable**

```text
composition.plan.v1  →  high-level musical intent only
composition.v2       →  sole playable / persistable score
```

Forbidden on the plan: `tracks`, `events`, absolute MIDI note lists as the score, MusicXML, analysis sidecars as substitutes for notes. Relative motif cells (existing theme relative notes) remain **non-playable** planning aids — realization still happens via symbolic decode or LLM compose (legacy).

**2. Schema shape (`composition.plan.v1`)**

Strict Pydantic, `extra=forbid`. Envelope fields (bounded):

| Block | Contents (examples) |
|-------|---------------------|
| `form` | tempo, key, meter, bar_count, contiguous sections (reuse `ComposerFormPlan` / sections) |
| `modulation` | optional key timeline / section key targets (bounded; soft unless locked by hard constraints) |
| `harmony` | chord/bar events (`ComposerHarmonyPlan`-compatible) |
| `instrumentation` | families / role hints (not catalog IDs as V2 fields) |
| `motifs_themes` | `ComposerThemePlan`-compatible relative cells + deployments |
| `density` | per-section or global density band (`sparse` / `moderate` / `dense`) |
| `target_ranges` | optional MIDI pitch windows per role (soft guidance for conditioning/constraints) |
| `stylistic_instructions` | bounded freeform string (prompt-sized; never logged as content) |
| `constraints_digest` | hash/summary of hard `GenerationConstraints` applied when plan was locked |
| `schema_version` | `composition.plan.v1` |

Document: plan validation ≠ musical quality; plan alone must not pass as composition integrity.

**3. Pipeline enum**

```text
GenerationPipelineId =
  | "llm_only"                 # current LangGraph (default)
  | "hybrid_plan_symbolic"     # acceptance Hybrid path
  | "symbolic_continuation"    # prefix V2 → symbolic continue
  | "symbolic_variation"       # prefix/scope → symbolic vary
```

Request field: `options.pipeline` (default `llm_only`). Frontend Hybrid sets `hybrid_plan_symbolic`.

**4. LangGraph routing**

```text
llm_only:
  plan_* → compose_* → realize → assemble → normalize → validate ↔ repair

hybrid_plan_symbolic:
  plan_* → validate_plan ↔ repair_plan
       → symbolic_condition → symbolic_generate → symbolic_decode_repair
       → assemble_from_symbolic → normalize → validate ↔ repair_composition
  (skip LLM compose_* / realize_themes note emission)

symbolic_continuation / symbolic_variation:
  load_prefix → (optional light plan fragment) → symbolic_* → validate ↔ repair
```

Hard constraints stay on state from request entry. Symbolic sample config + seed on state.

**5. Plan → symbolic conditioning bridge**

Map plan → `TokenizerConditioningV1` (genre/key when confidently present) + bounded structure/density hints. Optional: short feature summary from `style_reference` (existing embeddings path). Never dump full vectors or artist enums as sole style id. Do not invent pitches from harmony spans.

**6. AI runtime**

- When `pipeline == hybrid_plan_symbolic`: resolve planner via `GENERATE_PLANNER` (language), composer via `GENERATE_COMPOSER` (symbolic_composer). Do **not** collapse ops to `GENERATE` for that request.
- Register `local:music-transformer` (or `local:symbolic-composer-v1`) with `runtime=music_transformer`, `primary_capability=symbolic_composer`, `supported_operations=(GENERATE_COMPOSER, …)`, status `ready` only when checkpoint + torch policy allow; otherwise `unavailable` with clear error (no silent LLM note fallback).
- Fake/tiny symbolic runtime for tests: `fake:symbolic-tiny` always ready in fake mode / pytest.
- Fix `OPERATION_DEFAULT_CAPABILITY[GENERATE_COMPOSER]` → `SYMBOLIC_COMPOSER` (docs today say language_planner for reserved collapse — update).

**7. Failure / repair separation**

| Failure class | Detection | Repair |
|---------------|-----------|--------|
| Invalid plan | Pydantic + constraint conformance on plan | `repair_plan` LLM (bounded retries) or fail closed with `plan_invalid` |
| Invalid symbolic tokens | decode `rejected` / constraint mask exhaustion | `repair_tokens` / re-sample with same seed+1 attempt policy; or fail `symbolic_generate_failed` |
| Composition validation | existing integrity + hard constraints | existing `repair_composition` (LLM repair only when pipeline allows; hybrid may prefer re-sample over LLM note rewrite — document policy: **hybrid default = re-sample/plan repair, not LLM rewrite of all notes**; optional bounded LLM repair as last resort flag) |

Never blur error codes across classes. Closed Literals for pipeline errors.

**8. Seed control**

- Request: `options.seed: int | null` (hybrid/symbolic).
- Call `seed_everything(seed)` before symbolic generate; record seed in provenance `generation_parameters`.
- Same seed + same plan digest + same checkpoint/card → deterministic token path when `greedy` or documented sampling locks.
- LLM planner remains non-deterministic unless fake mode; A/B compares validity under fixed fake planner + fixed symbolic seed.

**9. Provenance**

Response + apply revision summary include:

```text
pipeline_id
stages: [
  { operation, model_id, capability, runtime, ... },
  { operation, model_id, capability, runtime, seed, checkpoint_card_prefix, tokenizer_version, ... }
]
plan_schema_version
constraints hard_summary (existing)
reference_provenance (optional, existing)
```

Bounded size; no full plan JSON in revision if large — store digest + key fields only.

**10. A/B evaluation**

- Pytest: same fixture prompt → `llm_only` (fake) vs `hybrid_plan_symbolic` (fake planner + tiny symbolic); assert both produce valid V2; compare constraint satisfaction + fingerprint inequality; mark `eval_example` / document `musical_quality_claim: false`.
- Optional CLI: `python -m app.music_transformer.cli` extension or `python -m app.services.generation_ab` writing report under experiment/eval dir — never `PROJECT_DB_PATH`.
- Metrics: validity, constraint codes, note counts, latency buckets — not aesthetic quality.

**11. Frontend**

- `MusicGenerator`: pipeline control — **LLM** (default) | **Hybrid**.
- Hybrid disabled/tooltip when no ready `symbolic_composer` model (discovery via `/ai/models`).
- Show dual provenance after generate (planner · symbolic).
- Seed optional advanced field for Hybrid.
- Preview-first apply unchanged; do not persist CompositionPlan.

**12. Logging**

Verbose structured extras: `pipeline_id`, stage name, model_id, capability, operation, seed, plan digest prefix, constraint hard_summary, token counts, decode repair result, validation codes, fallback_applied=false for hybrid composer. Never full instructions/plan dumps/event arrays/checkpoint home paths at INFO. DEBUG may log plan field counts and first-N token ids.

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Reuse LangGraph where appropriate | 3–5 |
| Typed CompositionPlan (form, sections, key/modulation, harmony, instrumentation, motifs, density, ranges, style) | 1–2 |
| Plan is not a second playable composition | 1, 12 |
| Symbolic generation nodes | 4–6 |
| Configurable pipelines (llm_only, hybrid, continuation, variation) | 3, 7, 11 |
| Hard constraints through graph | 2–5, 8 |
| Separate failure/repair: plan / tokens / composition | 5, 8 |
| Deterministic seed for symbolic | 6–7, 10 |
| Provenance per stage | 7, 9 |
| A/B eval llm_only vs hybrid | 10 |
| Do not remove existing V2 workflows | 3, 11–12 |
| User selects Hybrid → valid V2 (LLM plan, symbolic notes) | 7, 11–12 |
| Tests (mocked planner + tiny symbolic), logging, docs | 10–12 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(generation): composition.plan.v1 schema and pipeline-aware LangGraph skeleton`
- **Commit 2** (tasks 4–6): `feat(generation): symbolic composer nodes, registry runtime, and seed control`
- **Commit 3** (tasks 7–9): `feat(generation): hybrid request path, multi-stage provenance, and repair lanes`
- **Commit 4** (tasks 10–12): `feat(generation): Hybrid UI, A/B eval fixtures, acceptance tests, and docs`

## Tasks

### Phase 1: Plan contract + pipeline skeleton

- [x] Task 1: Define `composition.plan.v1` schemas and error codes
  Deliverable: Strict Pydantic `CompositionPlan` envelope composing form / modulation / harmony / instrumentation / motifs_themes / density / target_ranges / stylistic_instructions / constraints_digest. Reuse or wrap existing `ComposerFormPlan` / `ComposerHarmonyPlan` / `ComposerThemePlan` without duplicating divergent validators. Explicit validators rejecting playable event payloads. Closed Literals: `plan_invalid`, `plan_constraint_mismatch`, `plan_empty_sections`, `plan_forbidden_playable_fields`, …. Unit tests for valid/invalid fixtures.
  LOGGING: INFO schema version; DEBUG field presence counts; never log stylistic_instructions text or relative note payloads at INFO.
  Files: `backend/app/composition_plan_schemas.py` (new) or `backend/app/services/composition_plan.py` + schemas; `backend/tests/test_composition_plan_schemas.py`; fixtures under `backend/tests/fixtures/composition_plan/`.

- [x] Task 2: Plan ↔ hard constraints conformance helpers
  Deliverable: Functions to build/validate plan against frozen `GenerationConstraints` (key/meter/tempo/bars/sections/instruments). Produce `constraints_digest` for provenance. Shared by LLM plan parse path and hybrid validate_plan node.
  LOGGING: INFO hard_summary + digest prefix; WARN on soft mismatches; ERROR codes on hard failures.
  Files: `backend/app/services/composition_plan_constraints.py` (new), updates to `generation_constraints.py` only if needed; tests.

- [x] Task 3: Pipeline id on generate request + LangGraph mode skeleton
  Deliverable: Add `LLMGenerationOptions.pipeline` (`GenerationPipelineId`, default `llm_only`). Thread into `_GenerationState`. Compile graph with conditional edges selecting llm_only vs hybrid vs symbolic_* entry paths **without** removing current node implementations. Hybrid path may stub symbolic nodes with NotImplemented/unavailable until Task 4 — but llm_only must keep passing existing tests.
  LOGGING: INFO pipeline_id at generate start; DEBUG edge routing decisions.
  Files: `backend/app/schemas.py`, `backend/app/services/llm_music_generator.py`, tests updating generate options; `.env.example` comments if new env knobs appear later.

### Phase 2: Symbolic nodes + registry

- [x] Task 4: Symbolic generate / decode / token-repair service adapter
  Deliverable: Service layer that maps `CompositionPlan` (+ optional prefix composition) → conditioning + `generate_composition` / decode repair. Tiny **fake symbolic composer** for pytest (`fake:symbolic-tiny`) producing deterministic valid V2 from plan form metadata without requiring GPU torch weights (may synthesize minimal events via tokenizer fixture path or deterministic fixture composer — still events on tracks, not from harmony invention heuristics that invent pitches outside documented fixture rules). Real path uses existing MT inference when checkpoint configured.
  LOGGING: INFO model_id, seed, prompt_tokens, generated_tokens, decode result; ERROR token/integrity codes; never checkpoint absolute home paths (basename / configured id only).
  Files: `backend/app/services/symbolic_composition_generate.py` (new), `backend/app/services/fake_symbolic_composer.py` (new), reuse `music_transformer/inference.py`; tests with tiny fixtures.

- [x] Task 5: LangGraph nodes: validate_plan, repair_plan, symbolic_*, assemble_from_symbolic
  Deliverable: Implement hybrid branch nodes; wire separate routing after plan validation failures → repair_plan; after symbolic decode failures → token repair/re-sample; after V2 validation → existing repair_composition with hybrid policy (prefer re-sample; optional last-resort LLM repair behind flag). Skip LLM `compose_*` on hybrid success path.
  LOGGING: Per-node INFO stage/attempt/codes; WARN retry; ERROR terminal failure class.
  Files: `backend/app/services/llm_music_generator.py` (or extracted `llm_hybrid_generation.py` if file size warrants — prefer extract if >maintainable), tests for routing with mocks.

- [x] Task 6: AI runtime symbolic composer registration + op uncollapse
  Deliverable: Register music-transformer / fake-symbolic descriptors; set `GENERATE_COMPOSER` default capability to `symbolic_composer`; stop collapsing planner/composer ops when pipeline is hybrid/symbolic; `GET /ai/models?capability=symbolic_composer` lists ready models; unavailable when API/checkpoint/torch missing (explicit status). Update `docs/ai-runtime.md` in Task 12.
  LOGGING: INFO register model_id/status; WARN unavailable reasons (codes only); never secrets/paths.
  Files: `backend/app/ai_runtime/bootstrap.py`, `operations.py`/`capabilities.py`/`routing.py`/`types.py` (runtime id `music_transformer`), `runtimes/music_transformer.py` or thin adapter; `backend/tests/test_ai_models_routes.py`.

### Phase 3: API provenance + repair policy + continuation/variation

- [x] Task 7: Wire hybrid into generate HTTP path + multi-stage provenance
  Deliverable: `POST /llm/generate-music-json` accepts pipeline + seed; returns V2 + validation + stage provenance (planner + composer). Fake mode supports hybrid end-to-end. No silent fallback to LLM notes when symbolic unavailable — return 503/422 with clear code. Preserve llm_only response shape (additive fields).
  LOGGING: INFO pipeline, both model_ids, seed, fingerprint prefixes; ERROR unavailable/fallback-forbidden.
  Files: `backend/app/main.py` or generate handler module, `schemas.py` response extensions, `fake_llm.py` planner fixtures for hybrid; API tests.

- [x] Task 8: Distinct repair policies and error mapping
  Deliverable: Documented + implemented separation of plan / token / composition repair with closed HTTP/domain codes. Hybrid default: do not rewrite all notes via LLM on first composition failure — re-sample symbolic or repair plan. Max attempts configurable via env (`GENERATION_HYBRID_MAX_*`).
  LOGGING: INFO repair lane + attempt; DEBUG diagnostic codes; never full composition.
  Files: repair helpers in generator service; `generation_constraints` / validation report extensions if needed; tests for each failure class.

- [x] Task 9: Symbolic continuation + variation pipelines
  Deliverable: `symbolic_continuation` and `symbolic_variation` accept prefix composition (and optional scope) without requiring full LLM plan; still emit provenance + constraints where applicable. Development preview may optionally call shared adapter later — **not required** for acceptance if generate API covers modes; at minimum generate options + tests.
  LOGGING: INFO pipeline + prefix fingerprint prefix + seed.
  Files: generator graph branches; schema options; tests.

### Phase 4: Frontend, A/B, docs, acceptance

- [x] Task 10: A/B evaluation support (llm_only vs hybrid)
  Deliverable: Pytest module comparing fake llm_only vs hybrid_plan_symbolic on shared prompts/fixtures; report validity, constraint satisfaction, note_count, stage model_ids; `musical_quality_claim: false`. Optional small CLI or script writing JSON report. Fixtures under `backend/tests/fixtures/generation_ab/`.
  LOGGING: INFO comparison summary digests; never prompts at INFO.
  Files: `backend/tests/test_generation_pipeline_ab.py`, optional `backend/app/services/generation_ab_eval.py`.

- [x] Task 11: Frontend Hybrid generation UX
  Deliverable: Pipeline selector on `MusicGenerator` (LLM | Hybrid); optional seed field; disable Hybrid when no ready symbolic composer; show dual-stage provenance on candidate; pass `options.pipeline` / `options.seed` via `musicApi.js`; store fields as needed. Keep preview-first apply. Unit tests for request builder.
  LOGGING: console debug pipeline/seed/model ids only (match existing MusicGenerator logging style); no instruction dump.
  Files: `frontend/src/components/MusicGenerator.jsx`, `frontend/src/api/musicApi.js`, `frontend/src/store/musicStore.js` (minimal), frontend tests.

- [x] Task 12: Documentation, roadmap milestone, AGENTS/DESCRIPTION touch-ups
  Deliverable: New `docs/hybrid-generation.md` (pipelines, plan vs V2, seeds, provenance, repair lanes, unavailable behavior). Update `docs/ai-runtime.md` (ops uncollapse, symbolic_composer ready path), `docs/music-transformer.md` (graph integration note), README link, `.env.example` knobs, `.ai-factory/ROADMAP.md` unchecked milestone + DESCRIPTION/AGENTS one-liners if structure changes. Mandatory docs checkpoint.
  LOGGING: N/A (docs); ensure code log policy documented.
  Files: `docs/hybrid-generation.md`, `docs/ai-runtime.md`, `docs/music-transformer.md`, `README.md`, `.env.example`, `.ai-factory/ROADMAP.md`, `AGENTS.md` / `.ai-factory/DESCRIPTION.md` as needed.

## Dependency notes

- Tasks 2–3 depend on Task 1.
- Tasks 5–7 depend on Tasks 3–4 and 6.
- Task 8 depends on Task 5.
- Task 9 depends on Tasks 4–7.
- Tasks 10–11 depend on Task 7.
- Task 12 depends on Features being stable (after 7–11); can draft stubs earlier but finalize last.

## Related plans

- `.ai-factory/plans/style-semantic-embeddings-conditioning.md` — optional style_reference conditioning into planner/tokenizer bridge (reuse; do not re-implement embeddings).
- `.ai-factory/plans/symbolic-music-transformer.md` / `reproducible-symbolic-music-training.md` — engine + seed + eval metrics (consume, do not retrain).
- `.ai-factory/plans/unified-ai-runtime-model-provider-v3.md` — registry/ops reserved fields now activated.
- `.ai-factory/plans/enforce-generation-prompt-constraints.md` — hard constraints must remain authoritative.
