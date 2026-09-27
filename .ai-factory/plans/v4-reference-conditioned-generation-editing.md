# Implementation Plan: Reference-Conditioned Generation & AI Editing

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-22
Improved: 2026-09-22 (`/aif-improve` — strict_partition=disjoint-only, multi-ref borrow-dim conflict, strength-on-policy-only, preserve scope derivation, edit `_build_draft_prompt` inject + in_region_events lock, `active_project_id`, shared HTTP mapper, edit `generation_parameters` Apply pipeline, task deps)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **explicit preserve / borrow / regenerate property policy** on top of shipped `reference.features.v1`, with **per-dimension conditioning strength**, **multi-reference dimension assignment**, and **AI region-edit wiring**, so users can generate or edit canonical `composition.v2` using selected abstract properties from references while inventing genuinely new pitch material — **without** mutating reference Compositions, without literal motif/melody copying (unless own-project + explicit reuse), without overriding hard `GenerationConstraints` / edit selection bounds, and without `DATASET_ROOT` ingest
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v4-reference-music-analysis-feature-decomposition.md` (**shipped** — `reference.features.v1` analyzer, dimension masks, generate/develop soft fragments, affinity+compare_to, FE Generate+Develop checkboxes, provenance `reference_features[]`)
  - `.ai-factory/plans/v3-style-semantic-embeddings-conditioning.md` (**shipped** — scopes, `style_reference`, legacy whole summary)
  - `.ai-factory/plans/v4-composer-profiles.md` (**shipped** — durable prefs + `profile_strength`; complementary soft merge; **generate-only** in this plan’s AC path)
  - `.ai-factory/plans/v1-enforce-generation-prompt-constraints.md` (**shipped** — hard vs soft; reference policy stays soft)
  - `.ai-factory/plans/v2-context-aware-composition-development.md` (**shipped** — develop preview; already accepts `style_reference` / `style_references`)
  - AI region editing (milestone shipped) — `LLMCompositionEditRequest` / `replace_region` — **currently unwired** for reference features; **no** `generation_parameters` on edit response today

## Roadmap Linkage
Milestone: "V4 reference-conditioned generation and editing"
Rationale: Embeddings + reference-feature masks shipped selective soft transfer on generate/develop. This follow-on closes the product gap for **operation-level property policy** (preserve vs borrow vs regenerate), **per-dimension strength**, **multi-reference UI assignment** (A→harmony, B→rhythm), and **AI edit** conditioning — matching the AC “texture of A + rhythm of B, new melody and harmony.” It is its own checked V4 milestone, separate from the V3 embeddings line.

## Goal

Allow composition operations to use **selected musical characteristics from references** while generating **genuinely new musical content**, with an explicit three-way property disposition:

| Disposition | Meaning | Soft prompt role |
|-------------|---------|------------------|
| **preserve** | Keep this property family from the **current** working composition (edit/develop scope) | “Do not alter …” instructions + optional current-scope abstract summary |
| **borrow** | Transfer abstract bands/summaries from a **reference** binding | Masked `soft_fragment` lines (existing analyzer) scaled by strength |
| **regenerate** | Invent new material for this property family; do **not** imitate reference or freeze current | Explicit “create new …” instructions; never inject reference fragments for these dims |

Ship:

1. Consume shipped `reference.features.v1` artifacts (analyze remains read-only).
2. Operation coverage:
   - generate new composition from references
   - regenerate selected section using reference texture
   - use reference harmonic rhythm / instrumentation / tension curve
   - create new melody while preserving only rhythmic character
3. Explicit preserve / borrow / regenerate separation (partition + validation).
4. Default anti-melody/motif copy; opt-in reuse only for **own project** + explicit flag.
5. Conditioning **strength per reference dimension** (and optional default per binding).
6. Combine multiple references (A→harmony, B→rhythm) + Composer Profile orchestration preference (**generate** path; Profile fields stay generate-only).
7. Result remains canonical `composition.v2`.
8. Provenance on the generated/edited revision (`reference_features[]` + policy digest), including **AI edit Apply** via `generation_parameters` on the edit response → candidate → history CAS.
9. Tests (incl. mocked/fake LLM), UI, documentation.

```text
Reference A (dims: texture@strong)     ─┐
Reference B (dims: rhythm@normal)      ─┼─→ ReferenceConditioningPolicy
Composer Profile (orchestration prefs) ─┘         │  (generate only)
Current composition scope (preserve dims) ────────┤
Regenerate set (melodic_contour, harmony, …) ─────┤
        ↓
resolve → soft assembly:
  prompt hard/soft
  → profile fragment (strength ≠ off; generate only)
  → PRESERVE lines (current abstract summaries; no events)
  → BORROW lines (masked reference soft_fragments × strength)
  → REGENERATE lines (invent-new instructions)
  → anti-melody / optional own-project motif-reuse gate
        ↓
generate | develop preview | replace_region edit
        ↓
canonical composition.v2 + provenance.reference_features[] + policy digest
```

**Terminology lock:**
- **Reference feature report** = shipped `reference.features.v1` (derived; not playable).
- **Reference conditioning policy** = request-scoped disposition of dimensions across preserve / borrow / regenerate (`reference.conditioning.policy.v1` sketch below). Not a Composer Profile. Not analysis. Not an embedding dump.
- **Borrow binding** = one `style_reference` / `style_references[]` entry with non-empty `dimensions`; strengths come **only** from policy `dimension_strengths` / `default_borrow_strength` (do **not** add strength fields to `StyleReferenceRequest`).
- **Preserve** = properties of the **target** composition (current workspace / edit selection / develop source scope) that must stay musically consistent — expressed as abstract summaries from in-process analysis/feature helpers, **never** by pasting event arrays into prompts.
- **Regenerate** = properties the model must invent; references must not supply soft fragments for these dims.
- **Strength** = `off | light | normal | strong` (same enum family as `profile_strength`); scales soft emphasis only — never hard constraints. Owned solely by the policy DTO.
- **`active_project_id`** = optional request field naming the workspace project for motif-reuse gating (compared to each borrow binding’s `project_id`).
- **Own-project motif reuse** = optional `allow_motif_reuse: true` only when every borrow binding’s `project_id` equals `active_project_id` (revision of same project allowed) **and** user explicitly enables; still never injects raw pitch sequences. Default remains anti-copy. Inline-only / foreign / import bindings cannot enable reuse.
- **AC UI labels:** unchanged from parent (`density`, `texture`, …).
- Never auto-export to `DATASET_ROOT`.

## Approach Evaluation (locked)

### Part A — Where the policy lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only checkbox “dimensions” (shipped)** | Already works | Cannot express preserve vs regenerate; no strength; edit unwired | **Insufficient** alone |
| **B. Freeform prompt “keep rhythm, change melody”** | Flexible | Non-testable; model may copy | **Reject** as sole path |
| **C. Versioned request policy DTO partitioning dimensions** | Explicit AC; testable; FE tri-state UI | Schema + merge work | **Accepted** |
| **D. Hard post-validation that rewrites notes to match reference bands** | Strong transfer | Fights “genuinely new”; hard to define | **Reject** for V1 (soft only) |
| **E. New playable `composition.v4` conditioned score** | — | Forbidden | **Reject** |

### Part B — Policy schema shape

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Three disjoint arrays on the operation request** (`preserve_dimensions`, `borrow` via style_refs, `regenerate_dimensions`) | Clear | Borrow still needs multi-ref map | **Accepted** with refinements |
| **B. Per-dimension enum on a flat map** `{ dens: borrow, harmony: regenerate }` | Simple UI | Multi-ref borrow needs source id per dim | **Accepted as FE view**; API uses A+C |
| **C. `borrow_bindings[]` = style_ref + dimensions + strengths`** | Matches multi-ref AC | Slightly more FE | **Accepted** (extends shipped `style_references[]`; strengths on **policy**, not StyleReferenceRequest) |
| **D. Put preserve/regenerate inside `reference.features.v1` report** | — | Wrong lifecycle; report is about the reference | **Reject** |

**Locked policy sketch** (`reference.conditioning.policy.v1` nested on generate / develop / edit):

```text
{
  schema: "reference.conditioning.policy.v1",
  preserve_dimensions: [DimensionId, ...],   # from CURRENT scope
  regenerate_dimensions: [DimensionId, ...], # invent new
  # borrow comes from style_reference(s).dimensions
  dimension_strengths: { <DimensionId>: "off"|"light"|"normal"|"strong" },  # borrow dims only; policy-owned
  default_borrow_strength: "normal",
  allow_motif_reuse: false,  # gated via active_project_id; see Part E
  strict_partition: true     # DISJOINT ONLY — see validation locks (does NOT require covering all registry dims)
}
```

**Validation locks:**
- Dimension id must be in closed registry.
- A dimension must not appear in more than one of {preserve, borrow-union, regenerate} → 422 `reference_conditioning_partition_overlap`.
- **`strict_partition` = disjoint-only:** when true (default for edit/develop; generate may use true as well), validate no overlaps; **do not** require every registry dimension to appear. Unspecified dims receive **no** soft guidance (not forced into regenerate).
- Same dimension borrowed from two bindings → 422 `reference_conditioning_borrow_dimension_conflict`.
- `dimensions: []` on a binding still → 422 `reference_feature_mask_empty`.
- Borrow dims that analyze as `unavailable` → drop + warning; if **all** borrow dims unavailable → 422.
- `dimension_strengths` keys must be subset of borrow-union; unknown → 422 `reference_conditioning_unknown_strength`.
- Strength `off` drops the dim from borrow (becomes unspecified — no soft line); does **not** auto-add to regenerate.
- `allow_motif_reuse: true` with any binding whose `project_id` ≠ `active_project_id` (or missing `active_project_id` / missing binding `project_id`) → 422 `reference_conditioning_motif_reuse_forbidden`.
- Preserve on **generate** (no current composition): empty preserve only; non-empty → 422 `reference_conditioning_preserve_without_source`.
- Strengths live **only** on the policy DTO — never on `StyleReferenceRequest`.

### Part C — Strength semantics

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Repeat soft_fragment N times** | Crude | Token waste; silly | **Reject** |
| **B. Prefix weight tags + instruction intensity** (`[strength=strong]`) | Testable; cheap | Model may ignore | **Accepted** |
| **C. Hard constraint bands from reference** | Strong | Violates soft model | **Reject** |
| **D. Per-binding strength only** | Simpler | AC asks per dimension | **Insufficient** alone |

**Locked:** For each borrowed dim, emit tagged line:
`[ref=<fingerprint_prefix> dim=<id> strength=<level>] <soft_fragment>`
Plus a short strength legend in the anti-copy block. Fake LLM: hash(sorted borrow dims + strengths + fingerprint prefixes) → deterministic contour/density marker shift; still honor hard instruments/key.

### Part D — Operation surface

| Operation | Today | This plan |
|-----------|--------|-----------|
| **Generate** (`LLMMusicGenerationRequest`) | Masked borrow soft fragment wired; has `profile_id` | + policy, strengths, multi-ref FE, `active_project_id`, provenance policy digest |
| **Develop** (`CompositionDevelopmentPreviewRequest`) | Masked refs; **no** profile fields | + preserve from current scope; policy; **no** profile wire (follow-on) |
| **AI edit** (`LLMCompositionEditRequest` / `replace_region`) | **No** style_reference; **no** `generation_parameters` on response | **Wire** style_references + policy; soft inject in `_build_draft_prompt`; fake edit markers; **response + FE Apply** provenance |
| Arrangement / reharmonize / multi-agent | Out of scope | Document follow-on only |

**Locked recipes (FE presets → policy):**

| User intent | preserve | borrow | regenerate |
|-------------|----------|--------|------------|
| New piece: texture A + rhythm B | ∅ | A:`texture`, B:`rhythm` (+ optional dens) | `harmony`, `harmonic_rhythm`, `melodic_contour`, … |
| Regenerate section w/ ref texture | `harmony`, `form` (typical) | ref:`texture` | `melodic_contour`, `density` (typical) |
| Use ref harmonic rhythm | (edit) current melody/contour | ref:`harmonic_rhythm` | (optional) local density |
| Use ref instrumentation | melody/harmony as selected | ref:`instrumentation` (+ `texture`?) | — |
| Use ref tension curve | form/harmony as selected | ref:`tension_curve` | local density optional |
| New melody, keep rhythmic character | `rhythm`/`density` from **current** | optional ref rhythm if user wants foreign groove | `melodic_contour` (+ maybe harmony) |

Presets are FE helpers only — API accepts arbitrary valid partitions.

**Edit prompt lock (anti-copy vs patch contract):**
- Soft conditioning anti-copy applies to **reference** material only (no reference pitch/motif event lists).
- Existing `_build_draft_prompt` **keeps** current-selection `in_region_events` / harmony-in-region for the replace_region contract — do **not** strip them in the name of anti-copy.
- Inject policy soft block **after** analysis context in `_build_draft_prompt`.

### Part E — Anti-copy / own-project motif reuse

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always forbid any motif language** | Safe | Blocks intentional self-reuse | Too strict vs AC #4 exception |
| **B. Default anti-copy; `allow_motif_reuse` gated to own `active_project_id`** | Matches AC #4 | Need DTO field | **Accepted** |
| **C. Allow reuse whenever inline composition provided** | Easy | Inline can be imported foreign music | **Reject** |
| **D. Copy motif event arrays into prompt when allowed** | — | Violates “abstract only” / parent guards | **Reject** — even when reuse allowed, only motif **metadata** / characteristics soft text, never pitch lists |

**Locked:** When `allow_motif_reuse` is false (default), keep shipped `ANTI_MELODY_INSTRUCTION`. When true and gate passes, replace with a narrower instruction: “Motif characteristic reuse from the user’s own project is allowed as abstract guidance; still do not paste note sequences.” Never attach `motifs[].` note payloads. Gate input: request `active_project_id` vs each binding `project_id`.

### Part F — Preserve summaries source

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Paste current region events into prompt** | Accurate | Melody copy risk; huge | **Reject** (for preserve soft lines; edit patch context stays) |
| **B. Run analyzer / feature helpers on current scope → soft_fragment style lines for preserve dims** | Consistent with borrow | Extra CPU | **Accepted** |
| **C. Only textual “preserve harmony” without evidence** | Cheap | Weak | Fallback if evidence thin (`degraded`) |

Reuse `analyze_reference_features` against an **inline** binding of the current composition + scope (or thin `summarize_dimensions_for_scope`) — read-only; never mutate.

**Preserve scope derivation (locked):**
| Operation | Scope for preserve summarize |
|-----------|------------------------------|
| **Generate** | N/A — preserve must be empty |
| **Develop** | Existing develop source context scope (section / composition / operation-specific range already used by develop) |
| **AI edit** | `EmbedScopeBarRange` from `edit.selection.start_bar`/`end_bar` (and track filter only if analyzer/embed scope supports track narrowing; otherwise bar_range + soft track guidance in regenerate/preserve instructions) |

### Part G — Provenance

Extend shipped `generation_parameters.reference_features[]` entries and add sibling:

```text
generation_parameters.reference_conditioning_policy: {
  schema: "reference.conditioning.policy.v1",
  preserve_dimensions: [...],
  regenerate_dimensions: [...],
  borrow: [{ project_id?, revision_id?, scope_kind, fingerprint_prefix, dimensions: [{id, strength}], ... }],
  allow_motif_reuse: bool,
  active_project_id?: str,
  policy_digest: "<sha256 prefix of canonical JSON>"
}
```

Never store soft_fragment text, vectors, or event arrays. Edit and develop must merge the same shape so revision commit AI provenance stays uniform.

**Edit Apply pipeline (locked):** `LLMCompositionEditResponse.generation_parameters` → `AiRegionEditPanel` / `completeAiEdit` → candidate envelope → `buildHistoryAiProvenance` on Apply CAS. Today `completeAiEdit` omits `generation_parameters` — Task 7 must fix that.

### Part H — Domain → HTTP errors

Extend shipped `map_reference_feature_error_to_http` (same `ReferenceFeatureError` or sibling codes on the same mapper) for:
`reference_conditioning_partition_overlap`, `reference_conditioning_borrow_dimension_conflict`, `reference_conditioning_preserve_without_source`, `reference_conditioning_motif_reuse_forbidden`, `reference_conditioning_unknown_strength`.

| Surface | Mapping |
|---------|---------|
| Generate (`main.py`) | Already catches `ReferenceFeatureError` — ensure new codes flow |
| Edit (`main.py`) | **Add** `ReferenceFeatureError` catch (missing today) |
| Develop | Keep wrapping as `CompositionDevelopmentError` but preserve `details.reference_feature_error_code` (incl. new codes) |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Analyzer + registry | `reference_feature_schemas.py`, `reference_feature_analyze.py` | Borrow + preserve evidence |
| Masked merge | `reference_feature_condition.py` | Extend with strengths + policy partition |
| Generate / develop wire | `llm_music_generator.py`, `llm_composition_development.py` | Add policy assembly |
| Fake generate markers | `fake_llm.py` + `reference_features_active_for_fake` | Extend edit + strength hash |
| Profile strength pattern | `composer_profile_merge.py` | Mirror enum; generate-only Profile in AC |
| FE mask UI | `ReferenceFeaturesControls.jsx`, Generate + Develop | Extend to policy + multi-ref + Edit panel |
| Provenance merge | `merge_reference_feature_provenance` | Extend policy digest |
| Edit path | `llm_composition_editor.py` (`_build_draft_prompt`), `AiRegionEditPanel.jsx`, `completeAiEdit` | Soft inject + provenance plumbing |
| Error mapper | `map_reference_feature_error_to_http` | Extend codes; edit route catch |
| Embed scopes | `EmbedScopeBarRange` | Edit preserve scope |
| Tests | `test_reference_features_*.py` | Add policy / edit / strength / AC multi-ref |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Policy DTO + disjoint validation + borrow-dim conflict | Part B |
| Per-dimension strength tagging (policy-owned) | Part C |
| Preserve summary extraction with op-specific scopes | Part F |
| `active_project_id` on generate/develop/edit DTOs | Part E |
| Shared HTTP mapper + edit `main.py` catch | Part H |
| Edit request fields + `_build_draft_prompt` soft inject | Unwired today |
| Edit response `generation_parameters` + FE Apply | Missing today |
| Multi-reference FE assignment | Backend multi-ref exists; UI mostly singular |
| Fake LLM edit conditioning | Markers for CI |
| Docs + AC recipe tests | Texture A + rhythm B + new melody/harmony |

### Coupling risks to avoid

1. Mutating reference or current Composition during analyze/summarize.
2. Copying **reference** melodies / event arrays / motif note lists into prompts (even with reuse flag); do not confuse with legitimate edit `in_region_events`.
3. Overriding hard `GenerationConstraints` or edit bar/track selection from policy.
4. Auto-export to `DATASET_ROOT`.
5. Treating affinity / strength as musical quality.
6. Growing unrelated logic in `main.py` (edit catch for errors + response field only; conditioning stays in services).
7. Logging full prompts, reports, or event arrays at INFO.
8. Conflating Composer Profiles with reference policy; do not add develop `profile_id` in this plan.
9. Dumping full reference summary when a borrow mask is present.
10. Inventing `composition.v4`.
11. Wiring arrangement/reharmonize/multi-agent in this plan (document only).
12. Allowing `allow_motif_reuse` for import/transcription/foreign project refs.
13. Using preserve on generate without a source composition.
14. Breaking V3 singular `style_reference` / dimensions-omitted legacy path.
15. Requiring full-registry cover under `strict_partition`.
16. Adding strength fields onto `StyleReferenceRequest`.

## Scope And Decisions

### In scope
- `reference.conditioning.policy.v1` (+ validation) on generate, develop, edit.
- `active_project_id` + motif-reuse gate.
- Strength-tagged borrow merge; preserve summaries; regenerate instructions.
- Wire `LLMCompositionEditRequest` + editor soft inject + fake edit.
- Edit `generation_parameters` response + FE Apply provenance.
- Shared HTTP error mapping for conditioning codes.
- FE: multi-ref assignment, tri-state/policy presets, Edit panel controls, strengths.
- Provenance policy digest; tests; docs update (`docs/reference-features.md` + edit/development cross-links).

### Out of scope
- Hard note rewriting to match reference histograms.
- Durable SQLite policy templates library.
- Arrangement / reharmonize / multi-agent conditioning.
- **`profile_id` / `profile_strength` on develop** (AC Profile example uses generate; develop profile wire is a follow-on).
- Audio-as-reference without V2.
- Playwright mega-journey (unit + API + fake LLM sufficient).
- Changing analyzer dimension registry (reuse as-is).

### Architecture decisions (locked)

**1. Layering**

```text
HTTP generate / development/preview / composition edit
        ↓
schemas (+ policy + active_project_id on existing request DTOs;
         + generation_parameters on LLMCompositionEditResponse)
        ↓
services/reference_conditioning_policy.py   # NEW: partition validate, digest, preserve summarize
services/reference_feature_condition.py     # EXTEND: strengths + policy-aware assembly
map_reference_feature_error_to_http         # EXTEND: conditioning codes
services/llm_music_generator.py             # inject policy soft block
services/llm_composition_development.py     # same (+ preserve scope)
services/llm_composition_editor.py          # resolve+inject in _build_draft_prompt
services/fake_llm.py                        # generate + edit markers
main.py                                     # edit: catch ReferenceFeatureError; pass generation_parameters
        ↓
reference_feature_analyze helpers (read-only)
```

**Locked:** Do **not** add a second analyze router. Prefer new `reference_conditioning_policy.py` over bloating analyze. Schemas may live in `reference_feature_schemas.py` or sibling `reference_conditioning_schemas.py` if file size warrants split.

**2. Storage:** no Alembic. Policy is request-scoped only.

**3. Backward compat:**
- Requests with only shipped `style_reference.dimensions` and no policy → imply borrow=those dims, empty preserve, no regenerate list, strengths default `normal`.
- Legacy dimensions-omitted whole summary unchanged when no policy and no mask.
- `style_references[]` authoritative over singular (unchanged).

**4. Merge order (updated):**

```text
prompt hard/soft
  → composer-profile soft fragment (generate only)
  → PRESERVE abstract lines (current scope)
  → BORROW strength-tagged reference fragments
  → REGENERATE invent-new lines
  → anti-melody / gated motif-reuse instruction
  → legacy whole summary only when no masks and no policy borrow
```

## Commit Plan
- **Commit 1** (tasks 1–4): `feat: add reference conditioning policy schemas, assembly, and error mapping`
- **Commit 2** (tasks 5–8): `feat: wire policy on generate, develop, and AI edit with provenance`
- **Commit 3** (tasks 9–11): `feat: reference conditioning UI, tests polish, and docs`

## Tasks

### Phase 1: Policy contract & assembly
- [x] Task 1: Define `reference.conditioning.policy.v1` schemas, strength enum, partition validators, error/warning codes, settings knobs
  - Files: `backend/app/reference_conditioning_schemas.py` (or extend `reference_feature_schemas.py`), `backend/app/reference_feature_settings.py` (strength legend caps / policy digest length), `.env.example` if new env keys
  - Include: `preserve_dimensions`, `regenerate_dimensions`, `dimension_strengths`, `default_borrow_strength`, `allow_motif_reuse`, `strict_partition` (**disjoint-only**), `policy_digest` helper
  - **Locks:** no full-registry cover; duplicate borrow dim across bindings → `reference_conditioning_borrow_dimension_conflict`; strengths **policy-owned only** (never on `StyleReferenceRequest`); `off` → drop borrow line (unspecified)
  - Error codes: `reference_conditioning_partition_overlap`, `reference_conditioning_borrow_dimension_conflict`, `reference_conditioning_preserve_without_source`, `reference_conditioning_motif_reuse_forbidden`, `reference_conditioning_unknown_strength`, reuse mask_empty / cap_exceeded
  - LOGGING: DEBUG field names + dim counts; INFO never full policy bodies with fragment text
  - Depends on: none (consumes shipped DimensionId registry)

- [x] Task 2: Implement policy-aware conditioning assembly (preserve summarize + strength-tagged borrow + regenerate instructions + motif-reuse gate)
  - Files: `backend/app/services/reference_conditioning_policy.py` (**new**), extend `backend/app/services/reference_feature_condition.py`
  - Preserve scope: generate → refuse non-empty; develop → develop source context scope; edit → `EmbedScopeBarRange` from selection bars
  - Preserve: analyze/summarize **current** composition scope inline (read-only); degraded → warning, still emit weak preserve line when possible
  - Borrow: call existing masked resolve; tag strengths from policy; `off` drops dim; enforce borrow-dim uniqueness across bindings
  - Regenerate: stable instruction lines per dim (“Invent new melodic contour; do not imitate reference pitch patterns.”)
  - Gate `allow_motif_reuse` against binding `project_id` vs request `active_project_id`
  - LOGGING: INFO binding_count, preserve/borrow/regenerate counts, strengths present, motif_reuse flag, scope_kind; never soft text at INFO
  - Depends on: Task 1

- [x] Task 3: Provenance merge for policy digest + extended `reference_features[]` strength fields
  - Files: `reference_feature_condition.py` (`merge_reference_feature_provenance` or sibling), shared digest helper
  - Store policy digest + borrow strengths + optional `active_project_id`; never summaries/vectors/events
  - LOGGING: DEBUG digest prefix; INFO whether policy attached
  - Depends on: Task 2

- [x] Task 4: Add `active_project_id` to generate/develop/edit request DTOs + extend shared HTTP error mapper for conditioning codes
  - Files: `backend/app/schemas.py` (`LLMMusicGenerationRequest`, `LLMCompositionEditRequest`), `backend/app/composition_development_schemas.py`, `backend/app/reference_feature_schemas.py` (`map_reference_feature_error_to_http`)
  - Optional `active_project_id: str | None` (max length aligned with project ids)
  - Mapper covers all `reference_conditioning_*` codes → 422 (unless explicitly 404/403)
  - LOGGING: WARN mapped client errors with code only
  - Depends on: Task 1

### Phase 2: Operation wires
- [x] Task 5: Wire policy on **generate** and **develop** (backward-compatible defaults)
  - Files: `llm_music_generator.py`, `llm_composition_development.py`, `fake_llm.py` (+ DTO fields from Tasks 1/4 already present)
  - Inject assembled soft block after profile fragment (generate); develop has no profile fragment
  - Pass `active_project_id` + current composition/scope into policy assembly for preserve/motif gate
  - Develop: preserve `details.reference_feature_error_code` when wrapping errors
  - Fake generate: include strength+dim hash in marker; honor instruments/key
  - LOGGING: INFO op name + dim set sizes; WARN domain errors with code only
  - Depends on: Tasks 2, 3, 4

- [x] Task 6: Wire policy on **AI region edit** (`replace_region`) — soft inject + service provenance
  - Files: `backend/app/services/llm_composition_editor.py`, `fake_llm.py` (`edit_fake_composition_region`), `backend/app/main.py` (error catch)
  - Inject policy soft block in `_build_draft_prompt` **after** analysis context; **keep** `in_region_events` / harmony-in-region for patch contract; anti-copy = reference-only
  - Map edit selection → `EmbedScopeBarRange` for preserve summarize; policy must not expand bar/track selection
  - Catch `ReferenceFeatureError` in edit route via shared mapper (Part H)
  - Service returns provenance dict suitable for response `generation_parameters` (Task 7 attaches to response model)
  - Fake edit: deterministic pitch/rhythm nudge when borrow active; leave non-target region untouched
  - LOGGING: INFO selection bars/tracks counts + policy dim counts; never prompt or events
  - Depends on: Tasks 2, 3, 4

- [x] Task 7: Edit response + FE Apply provenance pipeline
  - Files: `backend/app/schemas.py` (`LLMCompositionEditResponse.generation_parameters`), `backend/app/main.py` (populate field), `frontend/src/components/AiRegionEditPanel.jsx`, `frontend/src/store/musicStore.js` (`completeAiEdit`), `frontend/src/api/musicApi.js` if needed, tests for envelope/`buildHistoryAiProvenance`
  - Thread `generation_parameters` (policy digest + `reference_features[]`) from edit response → candidate → Apply CAS
  - LOGGING: INFO whether generation_parameters present; never dump bodies
  - Depends on: Tasks 5, 6

- [x] Task 8: Backend tests — policy validation, strengths, multi-ref AC, edit wire, motif-reuse gate, fake LLM, privacy, edit provenance response
  - Files: `backend/tests/test_reference_conditioning_*.py` (+ extend `test_reference_features_acceptance.py` / edit route tests as needed)
  - **Primary AC:** multi-ref borrow `texture` from A + `rhythm` from B; regenerate `melodic_contour` + `harmony`; soft fragment contains only A-texture + B-rhythm tagged lines (not A melody / B harmony); result validates as `composition.v2`; provenance has both refs + policy digest
  - Cases: partition overlap → 422; borrow-dim conflict → 422; preserve on generate without source → 422; `allow_motif_reuse` foreign / missing `active_project_id` → 422; own-project + flag → instruction changes but still no event arrays; strength `off` drops dim; unspecified dims OK under strict_partition; edit injects + response has `generation_parameters`; dimensions-omitted legacy still works; no `DATASET_ROOT` imports
  - Mocked/fake model tests mandatory (`LLM_FAKE_MODE` / `fake_llm` markers)
  - LOGGING: caplog extras without full payloads
  - Depends on: Tasks 3, 5, 6, 7

### Phase 3: UI & docs
- [x] Task 9: Frontend policy model + multi-reference assignment utils/API
  - Files: `frontend/src/utils/referenceConditioningPolicy.js` (+ tests), extend `referenceFeatures.js` / `compositionEmbeddingReference.js` / `llmGenerateRequest.js` / `compositionCandidates.js` / `musicApi.js` (edit payload), `musicStore.js`
  - Support `style_references[]` builder: per-row project + dimension multiselect; strengths on policy map
  - Tri-state or three lists for preserve / borrow / regenerate with overlap prevention (disjoint-only; no full-registry force)
  - Pass `active_project_id` when motif-reuse enabled / when a project is open
  - Session key optional `referenceConditioning:v1` (policy ids + strengths only — no compositions)
  - LOGGING: dim counts / ref counts; never full compositions
  - Depends on: Tasks 5, 6

- [x] Task 10: UI — Generate, Develop, and AI Edit panels
  - Files: `ReferenceFeaturesControls.jsx` (extend or split `ReferenceConditioningControls.jsx`), `MusicGenerator.jsx`, `CompositionDevelopmentPanel.jsx`, `AiRegionEditPanel.jsx`
  - Presets matching Part D recipes; copy: “Does not copy melodies” + own-project reuse checkbox (disabled unless ref `project_id` === active project)
  - Strength selectors per borrowed dimension (default normal)
  - Multi-ref rows for AC example (A texture / B rhythm)
  - Ensure edit submit includes policy + refs and stages `generation_parameters` (Task 7)
  - LOGGING: existing error surfaces
  - Depends on: Tasks 7, 9

- [x] Task 11: Documentation + cross-links + AGENTS/README touch
  - Files: `docs/reference-features.md` (policy section), `docs/composition-development.md`, `docs/composition-v1.md` / composition-editor notes for edit provenance, `README.md`, `AGENTS.md`, `.env.example` if needed
  - Document partition (disjoint-only), borrow-dim conflict, strengths, `active_project_id`, edit wire + Apply provenance, motif-reuse gate, merge order, AC recipe, privacy; note develop Profile follow-on
  - Depends on: Tasks 8–10

## Acceptance Criteria Mapping

| Criterion | Satisfied by |
|-----------|--------------|
| Consume reference-feature artifacts | Tasks 2, 5–8 (analyzer reuse) |
| Generate / section regenerate / harmonic rhythm / instrumentation / tension / rhythm-only melody recipes | Tasks 5–6, 10 (presets + policy) |
| Explicit preserve / borrow / regenerate | Tasks 1–2, 9–10 |
| No literal motif/melody copy unless own project + explicit | Tasks 2, 4, 6–8, 10 |
| Strength per reference dimension | Tasks 1–2, 5, 9–10 |
| Combine multiple references + profile | Tasks 5, 8–10 (profile = generate only) |
| Result canonical Composition V2 | Tasks 5–8 |
| Provenance on generated revision | Tasks 3, 5–8 (incl. edit Apply) |
| Tests + mocked model + docs | Tasks 8, 11 |

## Implementation Notes for `/aif-implement`

1. Prefer extending shipped mask merge over a parallel undocumented soft path.
2. Keep Composer Profiles complementary — AC Profile + refs is **generate**; do not add develop `profile_id` here.
3. Edit selection bounds remain the hard region gate; policy is soft inside the region; keep `in_region_events` in edit prompts.
4. No Alembic migrations.
5. Generate/develop backward compat: missing policy + existing dimensions ⇒ borrow-only behavior with default strengths.
6. After Task 11, run `/aif-docs` checkpoint (Docs: yes).
7. Do not wire arrangement, reharmonize, or multi-agent in this plan.
8. Privacy: never log or persist soft_fragment bodies in provenance; never write `DATASET_ROOT`.
9. `strict_partition` never means “assign all 12 dimensions.”
