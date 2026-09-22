# Implementation Plan: V4 Reference-Music Analysis & Feature Decomposition

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-22
Improved: 2026-09-22 (`/aif-improve` — generate style_reference wire, mask semantics, texture group slices, affinity compare target, provenance dims, router lock, musicApi, task deps)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **user-selected reference feature dimensions** — analyze a musical reference into named abstract property summaries, let the user choose which dimensions condition generate/develop, support multi-reference masks, section/range scopes, reliability warnings, and embedding-backed similarity where slices exist — **without** modifying the reference Composition, without copying melodies/event arrays, without silent `DATASET_ROOT` ingest, and without replacing Composer Profiles or monolithic V3 `style_reference`
- Parent plans / shipped foundations:
  - `.ai-factory/plans/style-semantic-embeddings-conditioning.md` (**shipped** — handcrafted embeddings, scopes, `style_reference`, provenance, privacy vs dataset)
  - `.ai-factory/plans/deterministic-composition-v2-analysis.md` (**shipped** — melody/harmony/density/repetition/tension/role sidecars + `insufficient_*` warnings)
  - `.ai-factory/plans/composer-profiles.md` (**shipped** — durable abstract prefs; complementary, not a substitute for per-request reference masks)
  - `.ai-factory/plans/midi-musicxml-import-composition-v2.md` (**shipped** — MIDI/MusicXML → V2 session material usable as inline reference)
  - `.ai-factory/plans/audio-to-symbolic-musical-input.md` (**shipped** — monophonic preview → Apply into V2; reference only after symbolic V2 exists)
  - `.ai-factory/plans/enforce-generation-prompt-constraints.md` (**shipped** — hard vs soft; reference features stay soft)

## Roadmap Linkage
Milestone: "Style/semantic embeddings and conditioning for symbolic composition"
Rationale: Embeddings shipped whole-reference conditioning; Composer Profiles shipped durable prefs. This follow-on closes the product gap for **selective, multi-reference feature transfer** (“use A’s rhythm+texture, B’s harmonic rhythm — do not copy melodies”), reusing analysis + embedding group slices while keeping the same privacy boundary. Docs/implement may add a dedicated unchecked roadmap line via `/aif-roadmap` if product wants a distinct V4 milestone label.

## Goal

Allow users to specify **exactly which musical properties** should be taken from a reference (or several references) instead of vague “make it similar” prompts.

Ship:

1. Deterministic **reference feature analysis** → derived metadata report (`reference.features.v1`), never mutates the reference Composition.
2. Named **feature dimensions** with human-readable summaries + reliability status.
3. User **dimension selection** (and multi-reference masks).
4. **Section / bar-range / track** scope selection (reuse embed scopes).
5. Soft conditioning that injects **only selected** dimension summaries (never melodies / event arrays) on **both** generate and develop (generate `style_reference` is DTO-present today but **unwired** — this plan wires it).
6. **Similarity metrics** for dimensions that map onto embedding group slices (with explicit compare target).
7. Provenance of reference source + selected dimensions (+ optional similarity scores).
8. Explicit refusal of automatic training-dataset ingest.
9. Tests, UI (Develop + Generate), documentation.

```text
Reference source (project | revision | inline V2 from import/transcription Apply)
  + embed scope (composition | section | motif | bar_range)
  + selected dimensions[]
        ↓
analyze → reference.features.v1 {
  dimensions: { id → summary | bands | reliability },
  unavailable: [...],
  provenance, fingerprints
}   # derived only — never writes back into reference V2
        ↓
generate / development preview {
  style_references?: [{ source, scope, dimensions[], mode }],
  # or singular style_reference.dimensions
}
  → soft fragment = concatenation of selected dimension summaries only
  → NEVER copy pitch sequences / motifs note lists into prompts as “melody to imitate”
  → provenance: reference ids + scope + dimensions[] (+ optional per-dim affinity)
  → never DATASET_ROOT
```

**Terminology lock:**
- **Reference feature report** = derived sidecar `reference.features.v1`. Not playable. Not `composition.analysis.v1` (though it may call analysis helpers in-process). Not a Composer Profile. Not an embedding vector dump.
- **Feature dimension** = named abstract property family (`harmony`, `rhythm`, `texture`, …). Values are bands / histograms / short prose summaries — **never** note event arrays or motif pitch sequences.
- **Dimension mask** = user-selected subset of dimensions for a given reference binding.
- **Reliability** = `ok | degraded | unavailable` per dimension with stable warning codes when evidence is thin (empty harmony, monophonic-only texture, missing velocity, etc.).
- **Musical reference (V3)** = existing `style_reference` whole-embedding path; this plan **extends** it with masks / multi-ref rather than forking a second opaque path.
- **Composer Profile** = durable prefs; may coexist. Assembly order: prompt hard/soft → profile fragment → **selected reference feature fragments** → (optional) residual whole-embedding summary when dimensions omitted (legacy).
- **AC UI labels:** “Rhythmic density” → dimension id `density`; “Orchestration texture” → dimension id `texture`.
- Reference material stays in `PROJECT_DB_PATH` / session only — **never** auto-exported to `DATASET_ROOT`.

## Approach Evaluation (locked)

### Part A — Where the feature report lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Mutate reference Composition with embedded analysis** | Convenient | Violates AC #1; pollutes playable source | **Reject** |
| **B. Session/request-derived report only (fingerprint-keyed cache optional)** | Matches analysis/embeddings; no reference writeback | Not a named durable library of feature cards | **Accepted** as primary |
| **C. New SQLite table of saved feature cards** | Reusable named analyses | Overlaps Composer Profiles; scope creep | **Reject** for V1 of this feature (docs follow-on) |
| **D. Persist full `composition.analysis.v1` on the project row** | Rich | Wrong lifecycle; huge; not dimension-masked | **Reject** |
| **E. Write analyzed refs into `DATASET_ROOT`** | Training convenience | Violates privacy AC | **Reject** |

### Part B — Schema shape

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Freeform LLM prose “what to take from the ref”** | Flexible | Non-deterministic; may invent melodies; hard to test | **Reject** as sole path |
| **B. Versioned `reference.features.v1` with closed dimension enum + per-dim payload** | Selectable; testable; UI checkboxes | Schema work | **Accepted** |
| **C. Raw embedding vector with bit-mask only** | Tiny | Opaque to users; fails AC #4 | **Insufficient** alone |
| **D. Copy motif / melody event lists into the report** | Catchy | Violates “Do NOT copy melodies” | **Reject** |

**Locked schema sketch** (`reference.features.v1`):

```text
{
  schema: "reference.features.v1",
  algorithm_version: "reference.features.v1.0",
  source: {
    kind: "project" | "revision" | "inline",
    project_id?, revision_id?,
    source_fingerprint,
    import_origin?: "midi" | "musicxml" | "transcription" | "generated" | "unknown"
  },
  scope: CompositionEmbedScope,
  scope_digest,
  requested_dimensions: [DimensionId, ...],  # empty on analyze = all extractable
  dimensions: {
    <DimensionId>: {
      status: "ok" | "degraded" | "unavailable",
      summary: string,
      soft_fragment: string,
      bands?: { ... },
      evidence: { note_count, ... },
      warning_codes: [...]
    }
  },
  unavailable: [{ dimension, code, message }],
  embedding_affinity?: {
    model_id, profile_id,
    per_group: { rhythm?: float, contour?: float, texture?: float, form?: float },
    per_dimension?: { density?: float, ... },
    musical_quality_claim: false
  }
}
```

**Closed dimension registry:**

| DimensionId | User label (UI) | Primary extractors | Melody copy risk |
|-------------|-----------------|--------------------|------------------|
| `harmony` | Harmony | analysis harmony + chord vocabulary bands | Low |
| `harmonic_rhythm` | Harmonic rhythm | chord-change rate / span stats | Low |
| `rhythm` | Rhythm | embeddings onset/duration + onset modulus | Low |
| `melodic_contour` | Melodic contour | interval/contour histograms only | **Guard:** no event lists |
| `texture` | Orchestration texture | concurrent-voice / role mix / track-count | Low |
| `instrumentation` | Instrumentation | track instrument/role multiset | Low |
| `density` | Rhythmic density | notes-per-bar / arrangement density bands | Low |
| `dynamics` | Dynamics | velocity/expression aggregates | May be `unavailable` |
| `form` | Form | section-type histogram | Low |
| `tension_curve` | Tension curve | tension analysis coarse shape | Low |
| `motif_characteristics` | Motif characteristics | motif count / transform variety — no note payloads | **Guard:** metadata only |
| `performance_characteristics` | Performance | expression/timing aggregates | Often degraded/unavailable |

### Part C — Multi-reference + mask binding

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Single `style_reference` only; dimensions ignored** | Status quo | Fails AC | **Reject** |
| **B. Extend with `dimensions` + `style_references[]` (cap N)** | Matches AC; V3-compatible | Schema + FE | **Accepted** |
| **C. Only Composer Profile derive** | Reuses profiles | Wrong lifecycle | **Reject** |

**Caps:** `REFERENCE_FEATURES_MAX_REFERENCES` (default 3), `REFERENCE_FEATURES_SUMMARY_MAX_CHARS` per dim, `REFERENCE_FEATURES_SOFT_FRAGMENT_MAX_CHARS` total.

**Locked binding / mask semantics (conditioning):**

| Field state | Behavior |
|-------------|----------|
| `style_references` present (non-empty) | **Authoritative**. If singular also set → ignore singular; warn `reference_feature_singular_ignored` |
| Only `style_reference` | Single binding (V3-compatible) |
| `dimensions` **omitted / null** | Legacy whole `feature_summary` (V3) |
| `dimensions` **non-empty** | Soft fragments for those dims only; no full summary dump |
| `dimensions: []` | **422** `reference_feature_mask_empty` |
| All requested dims `unavailable` | **422** `reference_feature_mask_empty` |
| Some dims `degraded` | Proceed + warning codes |

**Analyze endpoint:** omitted/`[]` `requested_dimensions` → extract **all** registry dimensions.

### Part D — Conditioning merge

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Override hard constraints** | Strong | Violates constraint model | **Reject** |
| **B. Additive soft fragments only** | Matches AC + profiles | Must **wire generate** | **Accepted** |
| **C. Always inject full summary with mask** | Simple | Leaks unselected dims | **Reject** |

**Locked merge contract:**

1. Never mutate request `prompt` fields or hard `GenerationConstraints`.
2. Resolve each binding → filtered `reference.features.v1` (`status != unavailable`; include `degraded` with warning).
3. Soft fragment = ordered `soft_fragment` lines tagged with source + dimension id.
4. Instruction line: “Use these abstract properties only; do not copy melodies or note sequences from the reference.”
5. Assembly: **prompt hard/soft → profile fragment → masked reference fragments → legacy whole summary only when `dimensions` omitted**.
6. **Generate path:** `LLMMusicGenerationRequest.style_reference` exists but is **unwired** in `llm_music_generator.py`. Implement **must** resolve + inject beside `_profile_soft_block` / `profile_soft_fragment` (after profile).
7. Provenance: `generation_parameters.reference_features: [{ project_id?, revision_id?, scope_kind, fingerprint_prefix, dimensions[], unavailable_codes[] }]` — never dump summaries/vectors.
8. Fake LLM: soft-marker / contour shift keyed by sorted dim ids + fingerprint prefix; still honor prompt instruments/key.

### Part E — Similarity metrics

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Skip** | Faster | Fails AC #7 | **Reject** |
| **B. Group-slice cosine + compare target** | Reuses vector | Need public helpers | **Accepted** |
| **C. Similarity = quality** | Marketing | Forbidden | **Reject** |

**Group slice helpers** (add in `embeddings/features.py` — today only pitch/rhythm/contour norms are logged):

| Group id | Vector slice |
|----------|--------------|
| `rhythm` | `DUR_BINS + ONSET_BINS + DENSITY_DIMS` |
| `contour` | `INTERVAL_BINS + CONTOUR_DIMS` |
| `texture` | `CONCURRENT_BINS + ROLE_DIMS + TRACK_COUNT_DIMS` |
| `form` | `FORM_POS_DIMS + SECTION_TYPE_DIMS` (optional) |

Helper: `feature_group_vector(raw_features, group_id) -> list[float]`.

**Dimension → group:** `rhythm`/`density` → `rhythm` (**correlated**); `melodic_contour` → `contour`; `texture`/`instrumentation` → `texture`; others analysis-only (+ `reference_feature_affinity_unsupported`).

**Compare target:** analyze optional `compare_to`; Develop scores vs current scope; **omit affinity** when no compare target.

### Part F — UI placement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Advanced JSON only** | Fast | Hidden | **Reject** |
| **B. Develop + MusicGenerator dimension UI** | Visible; AC | UI work | **Accepted** |
| **C. New tab only** | Room | Duplicate | Optional later |

### Part G — Reference source kinds

| Source | How | Notes |
|--------|-----|-------|
| Existing project | `project_id` | Already supported |
| Project revision | `revision_id` | Already supported |
| Imported MIDI/MusicXML | Inline V2 after Apply | No raw bytes retained; `import_origin` |
| Analyzed audio | After transcription Apply → V2 | Audio never stored; many dims may degrade |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Embed scopes | composition/section/motif/bar_range | Range selection |
| Style resolve | `resolve_style_reference` | Load + fingerprint CAS |
| Feature vector | `embeddings/features.py` 81-D | Add public group helpers |
| Profile soft inject | `_profile_soft_block` | Same site for reference fragments |
| Develop resolve | `llm_composition_development` | Extend with dimensions |
| Generate DTO | `style_reference` on request | **Wire resolve+inject** |
| FE API | `musicApi.js` embeddings | Add analyze client |
| Router pattern | `composer_profiles.py` | **New** `reference_features.py` only |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| `reference.features.v1` + registry | Incl. AC label map |
| Analyzer + unavailability codes | Read-only V2 |
| Mask + multi-ref semantics | Part C table |
| **Generate path wire** | Currently unused |
| Texture/form group slices | `feature_group_vector` |
| Affinity + compare_to | Omit when absent |
| Provenance `reference_features[]` | dims + unavailable_codes |
| FE musicApi + Generate request | `buildLlmRequest` style_reference |
| Tests + docs | AC density+texture |

### Coupling risks to avoid

1. Mutating reference Composition with feature reports.
2. Copying melodies / event arrays into reports or prompts.
3. Overriding hard constraints from reference features.
4. Auto-export to `DATASET_ROOT`.
5. Claiming affinity = musical quality / artist style.
6. Growing logic in `main.py`.
7. Logging full reports/events/prompts at INFO.
8. Conflating with Composer Profiles.
9. Dumping full summary when a mask is present.
10. Inventing `composition.v4`.
11. Raw audio as reference without V2.
12. Fake harmony from empty `harmony: []`.
13. Extending embeddings router instead of dedicated router.
14. Inventing affinity without a compare target.

## Scope And Decisions

### In scope
- `reference.features.v1` + analyzer + HTTP analyze.
- Masked conditioning on **generate and** develop; fake LLM markers.
- Provenance with dimensions; optional affinity with compare target.
- FE: checkboxes, summaries, unreliable badges, musicApi, Generate wire.
- Tests + docs (`docs/reference-features.md`, README/AGENTS/`.env.example`).

### Out of scope
- Durable SQLite feature-card library; required analyze LRU cache.
- Audio embeddings; melody quotation; training from references.
- Playwright mega-journey; multi-agent wiring beyond generate/develop.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP  POST /reference-features/analyze
        ↓
routers/reference_features.py  (+ map_reference_feature_error_to_http)
        ↓
services/reference_feature_analyze.py
services/reference_feature_condition.py
services/composition_style_conditioning.py
services/llm_music_generator.py         # MUST resolve+inject
services/llm_composition_development.py # extend
        ↓
analysis helpers + embeddings/features.py (+ group helpers)
```

**Locked:** new `routers/reference_features.py` only. Schemas: `reference_feature_schemas.py`. Settings: `reference_feature_settings.py`.

**2. Storage:** no Alembic migration. Optional LRU only.

**3. API:** `POST /reference-features/analyze` (+ optional `compare_to`); generate/develop accept `style_reference.dimensions` / `style_references[]`.

**4–5.** Backward compat + anti-melody-copy as in Part C/D.

## Commit Plan
- **Commit 1** (tasks 1–3): `feat: add reference.features.v1 schemas and analyzer`
- **Commit 2** (tasks 4–6): `feat: wire dimension-masked reference conditioning on generate and develop`
- **Commit 3** (tasks 7–9): `feat: reference feature UI, tests, and docs`

## Tasks

### Phase 1: Contract & analyzer
- [x] Task 1: Define `reference.features.v1` schemas, closed dimension registry, error/warning codes, and `REFERENCE_FEATURES_*` settings
  - Files: `backend/app/reference_feature_schemas.py`, `backend/app/reference_feature_settings.py`, `.env.example`
  - Include: `DimensionId`, per-dim payload, optional `compare_to`, affinity block, forbid event/vector/artist fields
  - UI label map: “Rhythmic density”→`density`, “Orchestration texture”→`texture`
  - LOGGING: DEBUG validation field names only; INFO never full report bodies
  - Depends on: none

- [x] Task 2: Implement deterministic analyzer service (read-only V2 → feature report)
  - Files: `backend/app/services/reference_feature_analyze.py`
  - Load via style-reference loader patterns; per-dim `ok|degraded|unavailable`
  - Never mutate composition; never write `DATASET_ROOT`; LRU optional only
  - LOGGING: INFO counts + fingerprint_prefix + scope_kind; DEBUG per-dim status; never events
  - Depends on: Task 1

- [x] Task 3: HTTP `POST /reference-features/analyze` + domain→HTTP mapping
  - Files: `backend/app/routers/reference_features.py`, `backend/app/main.py` (`include_router` only)
  - **New router only** — do not add routes to `embeddings.py`
  - Map empty scope / unknown dim / mask_empty → 422; missing project → 404; dataset → 403
  - LOGGING: INFO counts; WARN mapped client errors with code only
  - Depends on: Task 2

### Phase 2: Conditioning & similarity
- [x] Task 4: Extend style-reference DTOs + masked soft-fragment merge; **wire generate and develop**
  - Files: embeddings/reference schemas, `schemas.py`, `composition_development_schemas.py`, `reference_feature_condition.py`, `composition_style_conditioning.py`, `llm_music_generator.py` (**new resolve+inject** beside `_profile_soft_block`), `llm_composition_development.py`, `fake_llm.py`, provenance helpers
  - Locked Part C semantics + Part D merge contract (incl. singular ignored warning)
  - Provenance: `reference_features[]` with `dimensions[]` + `unavailable_codes[]`
  - LOGGING: INFO ref count, dim lists, fragment char length; never fragment text at INFO
  - Depends on: Tasks 1, 2

- [x] Task 5: Public embedding group slices + dimension affinity with compare target
  - Files: `embeddings/features.py` (`feature_group_vector`), analyzer sibling
  - Affinity only when `compare_to` / Develop current scope present; omit otherwise
  - Document correlated `density`/`rhythm` scores; `musical_quality_claim: false`
  - LOGGING: DEBUG rounded scores; INFO whether affinity present
  - Depends on: Tasks 1, 2

- [x] Task 6: Backend tests — analyzer, mask merge, **generate wire**, unavailability, anti-melody, privacy, fake LLM, AC
  - Files: `backend/tests/test_reference_features_*.py`
  - AC: only `density` + `texture` in soft fragment; not harmony/`melodic_contour`; V2 unchanged; no `DATASET_ROOT`
  - Assert generate injects when `style_reference.dimensions` set (regression for prior unwired DTO)
  - Cases: `dimensions: []`→422; all unavailable→422; multi-ref; singular ignored; no affinity without compare_to
  - LOGGING: caplog extras without full payloads
  - Depends on: Tasks 3, 4, 5

### Phase 3: UI & docs
- [x] Task 7: Frontend API client + session model + utils for dimension masks and multi-reference
  - Files: `frontend/src/api/musicApi.js` (`analyzeReferenceFeatures`), `compositionEmbeddingReference.js` (+ tests), `compositionCandidates.js`, `llmGenerateRequest.js` (+ tests with style_reference/dimensions), `musicStore.js`
  - Util test: `density`+`texture` mask serializes correctly
  - Optional `localStorage` key `referenceFeatures:v1` (mask only)
  - LOGGING: status codes, dim counts; never full compositions
  - Depends on: Tasks 3, 4

- [x] Task 8: UI — dimension checkboxes, summaries, unreliable badges, section/range, multi-ref
  - Files: `CompositionDevelopmentPanel.jsx`, `MusicGenerator.jsx` (or shared `ReferenceFeaturesControls.jsx`)
  - Labels per Task 1 map; affinity readout only when compare available; “Does not copy melodies”
  - LOGGING: existing error surfaces
  - Depends on: Task 7

- [x] Task 9: Documentation + AGENTS/README/`.env.example` + cross-links
  - Files: `docs/reference-features.md`, `docs/embeddings.md`, `docs/composer-profiles.md`, `docs/composition-development.md`, `README.md`, `AGENTS.md`, `.env.example`
  - Document registry, mask semantics, generate wire, affinity compare target, privacy, profile merge order
  - Depends on: Tasks 6–8

## Acceptance Criteria Mapping

| Criterion | Satisfied by |
|-----------|--------------|
| Derived metadata only; no reference mutation | Tasks 2–3, 6 |
| Provenance preserved | Tasks 4, 6 |
| Select dimensions; multi-ref; no melody copy | Tasks 1, 4, 6–8 |
| User-understandable summaries | Tasks 2, 8–9 |
| Unreliable extraction detected | Tasks 2, 6, 8 |
| Section/range selection | Tasks 3, 7–8 |
| Similarity where embeddings support | Tasks 5, 8 |
| No automatic training dataset entry | Tasks 2–3, 6, 9 |
| AC: composition → `density` + `texture` only | Tasks 6–8 |
| Generate path conditions on masked reference | Tasks 4, 6 |
| Tests, UI, documentation | Tasks 6–9 |

## Implementation Notes for `/aif-implement`

1. Prefer extending `StyleReferenceRequest` over a parallel undocumented field.
2. Keep Composer Profiles and reference features complementary in UI copy.
3. Share pure band helpers with profile derive only if cycle-free.
4. MIDI/transcription refs require validated V2 inline; `import_origin` best-effort.
5. No Alembic migrations.
6. Generate wire is mandatory — DTO field alone is not acceptance.
7. After Task 9, run `/aif-docs` checkpoint (Docs: yes).
