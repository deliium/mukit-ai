# Implementation Plan: Style/Semantic Embeddings & Conditioning for Symbolic Composition

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-21

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship a **registry-compatible symbolic embedding subsystem** (handcrafted-first V3), scoped similarity search, reference provenance, style/reference conditioning for generate/develop, fingerprint-keyed cache invalidation, and a lean frontend reference/similarity UI — without training a large embedding network or copying user projects into global datasets

## Roadmap Linkage
Milestone: "Style/semantic embeddings and conditioning for symbolic composition"
Rationale: AI runtime V3 already reserves `embedding` / `AiOperation.EMBED` as stubs; analysis, motifs, dataset near-dup, and tokenizer conditioning provide feature/provenance hooks but no shared musical embedding, similarity index, or reference-conditioned generation. Add as a new unchecked milestone in `.ai-factory/ROADMAP.md` during docs/implement (roadmap owner: `/aif-roadmap` or docs checkpoint). Prior milestone "Reproducible symbolic music training and evaluation" is already complete.

## Goal

Allow models and AI tools to reason about **musical similarity and stylistic character** from Composition V2 note material (not text artist labels alone). Users can select an existing project/section (or motif / bar range) as a musical reference, retrieve similar material, and generate a distinct variation that is **conditioned on the reference embedding** while remaining a separate Composition with preserved reference provenance.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| AI runtime `embedding` capability | `ModelCapability.EMBEDDING`, `AiOperation.EMBED`, `AI_OP_EMBED`, bootstrap stub `local:embedding-stub`, `StubEmbeddingModel.embed(texts)` | Register a **ready** symbolic feature embedder; extend protocol beyond text-only |
| `docs/ai-runtime.md` | Documents embedding as similarity/retrieval stub | Expand with symbolic embedding + operations |
| Analysis pipeline | Tonality, density, melody, harmony, role, repetition, tension over scopes `composition` / `section` / `track` | Pure feature extractors for handcrafted vectors; **do not** persist `composition.analysis.v1` as embedding payload |
| Motif similarity | `composition_motif_similarity.py` rhythm/contour/interval/alignment scores | Scoped motif embeddings + related-motif search; keep identity gates separate from embedding API |
| Composition fingerprint | `composition_source_fingerprint` / analysis projection | Cache keys + invalidation on composition change |
| Dataset near-dup | Onset-pitch sequences + bucket keys under `DATASET_ROOT` | Inspiration only for coarse descriptors; **never** write user projects into dataset root |
| Dataset provenance | `user_owned` / attested status; `style` string optional on items | Pattern for reference provenance + forbid silent corpus ingest |
| Tokenizer conditioning | `TokenizerConditioningV1` genre/key prefix tokens | Optional bridge: map embedding summary → bounded conditioning labels; do not invent artist tokens |
| Music Transformer | Offline train/eval; generate accepts `conditioning` dict | Optional offline consumer of style_ref vectors; HTTP remains generate-opt-in |
| Development preview | `POST /composition/development/preview` continue/add/vary; fingerprint-gated apply | Primary UX path for "make this section closer to reference X" |
| Project APIs | `GET /projects`, revision detail with composition | Reference picker source (user-owned projects only) |
| Frontend patterns | Develop/Arrange preview-apply, Analysis tab, project browser, Zustand session candidates | Mirror for reference selection + similarity results |
| Fake LLM | Deterministic fixtures | Fake embedder + conditioned generate/develop responses for E2E |
| Provenance DTO | `AiProvenance` additive fields (`model_id`, `capability`, `operation`, `generation_parameters`) | Extend `generation_parameters` / revision summary with reference provenance (bounded) |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| Text-only `EmbeddingModel` protocol | No symbolic scope embed; stub always unavailable |
| No feature-vector schema | No versioned embedding card, dims, distance metric, profile id |
| No composition/section/motif/bars scopes for embed | Analysis lacks motif + selected-bars; need unified embed scope |
| No similarity search API | Cannot nearest-neighbor across projects/sections |
| No embedding cache | Recompute every call; no invalidation protocol |
| No reference conditioning | Generate/develop cannot take reference embedding / project scope |
| Artist≡style risk | Freeform genre/artist strings exist in tokenizer/dataset; must not become sole style id |
| User→global corpus risk | No explicit gate; must refuse dataset ingest from project store unless configured |
| Frontend | No reference picker / similar-sections UI |
| Eval examples | No fixture nearest-neighbor rankings for handcrafted profile |
| Docs | No `docs/embeddings.md`; ai-runtime still says stub |

### Coupling risks to avoid

1. Equating named artist/composer with a single numeric style label or embedding id.
2. Persisting `composition.analysis.v1` reports or full event arrays as embedding rows.
3. Writing user project compositions into `DATASET_ROOT` / global training corpora without explicit opt-in config.
4. Loading large torch embedding networks in the default web image / blocking uvicorn with train jobs.
5. Silently falling back to text-embedding stubs and claiming musical similarity.
6. Logging full vectors at INFO, full compositions, prompts, or home-absolute paths.
7. Mutating working composition on similarity search (read-only) or auto-applying conditioned generate.
8. Treating cosine similarity as musical quality.
9. Growing unrelated logic in `main.py` — use `routers/` + `services/` / `app/embeddings/`.
10. Breaking existing `GET /ai/models?capability=embedding` stub discovery without a ready replacement path.

## Approach Evaluation (locked for V3)

| Approach | Pros | Cons | V3 verdict |
|----------|------|------|------------|
| **A. Deterministic handcrafted features** | No torch; CPU-fast; explainable dims; fingerprint-cacheable; CI-friendly; aligns with analysis metrics | Limited semantic depth; sensitive to profile design | **Default production path** |
| **B. Learned symbolic embeddings** | Potentially richer similarity | Needs train data, torch, experiment infra; quality ≠ metric trap; heavier ops | **Deferred** — optional offline research adapter only if explicitly enabled; not required for acceptance |
| **C. Hybrid** | Handcrafted base + optional low-rank projection / whitening from tiny offline fit | Complexity; must keep same schema + provenance | **Supported shape**: profile may include `projection: none \| offline_linear.v1` with digest; default `none` |

**Decision:** Ship **handcrafted musical feature embeddings** as `local:symbolic-features-v1` (ready). Protocol + schemas leave room for learned adapters later. Do **not** train a large embedding network for initial V3 acceptance.

### Handcrafted feature groups (initial profile `symbolic.features.v1`)

All derived from `tracks[].events[]` (+ declared meter/key/sections/motifs metadata when present). Fixed-order float vector; L2-normalize for cosine.

| Group | Examples (bounded) |
|-------|-------------------|
| Pitch / tonality | Pitch-class histogram (12), tonal center stats, range (midi min/max/mean) |
| Rhythm | Duration histogram (coarse bins), onset-modulus histogram, density (notes/bar) |
| Contour / intervals | Melodic interval histogram (clamped), contour up/down/same rates |
| Texture | Concurrent-note density bins, track-count / role mix (melody/bass/accomp when inferable) |
| Form (composition/section) | Relative section position, section-type one-hot when known |
| Motif scope | Relative pitch/rhythm descriptors over motif event set (reuse motif relative coords) |
| Bar-range scope | Same as section but over explicit `[start_bar, end_bar]` or tick interval |

Document that vector similarity is **distributional / structural affinity**, not aesthetic quality or artist identity.

## Scope And Decisions

### In scope
- New `backend/app/embeddings/` package: schemas, feature extractors, vector ops, cache, project-scoped index, settings.
- Extend AI runtime: `SymbolicEmbeddingModel` protocol (or broaden `EmbeddingModel`), ready handcrafted runtime, bootstrap registration, routing for new ops.
- Embed scopes: `composition`, `section`, `motif`, `bar_range` (selected bars).
- HTTP: embed, similarity search, related motifs, reference resolve; wire conditioning into generate + development preview.
- Fingerprint-keyed cache + invalidation when composition fingerprint changes.
- Reference provenance DTO (project/revision/scope/fingerprint/model) on conditioned responses + revision summary.
- Explicit config gate: never auto-export user projects into `DATASET_ROOT`.
- Frontend: reference selection + similar sections/motifs where useful (Develop + compact Similarity UI).
- Tests (unit + API + frontend utils), evaluation fixture examples, verbose logging, docs.

### Out of scope
- Training a large embedding network or requiring ROCm for embeddings.
- Audio / spectrogram embeddings; Whisper/ACE-Step.
- Public multi-tenant embedding SaaS or vector DB (pgvector, etc.).
- Claiming similarity = musical quality in UI copy.
- Auto-ingest of all projects into a global similarity corpus.
- Replacing motif identity gates or analysis sidecar with embeddings.
- Artist-style marketplace / copyright-circumvention features.

### Architecture decisions (locked)

**1. Package boundary**

```text
routers/embeddings.py  (+ small hooks in development / generate)
        ↓
services/composition_embedding.py   # orchestration, project load, provenance
        ↓
app/embeddings/                     # pure engine (no FastAPI, no PROJECT_DB writes except via store helper)
  schemas.py | features.py | vector.py | cache.py | index.py | settings.py
        ↓
ai_runtime/  SymbolicEmbeddingModel adapter (handcrafted runtime)
        ↓
composition_schemas / analysis helpers / motif relative notes (read-only)
```

Offline CLI optional (`python -m app.embeddings.cli`) for eval fixtures — never required for web path.

**2. Schema layering**

| Schema | Role |
|--------|------|
| `composition.embedding.v1` | Vector + dims + profile_id + model_id + scope + source_fingerprint + algorithm_version |
| `composition.embed_scope.v1` | Discriminated: composition \| section \| motif \| bar_range |
| `composition.similarity_query.v1` / `similarity_hit.v1` | Query + ranked hits (score, target ref, provenance) |
| `composition.reference_provenance.v1` | Reference project_id, optional revision_id, scope, fingerprints, embedding model_id, **explicit** `artist_label_used: false` policy field or omit artist ids entirely |
| `composition.style_conditioning.v1` | How reference embedding is applied (`prompt_features` \| `tokenizer_labels` \| `vector_hint`); never a free artist enum as sole style |

Strict Pydantic, `extra=forbid`. Closed issue Literals for errors (`embed_scope_invalid`, `embed_empty_scope`, `similarity_index_empty`, `reference_not_found`, `reference_fingerprint_mismatch`, `embedding_model_unavailable`, `dataset_export_forbidden`, …).

**3. AI runtime integration**

- Add ops (or reuse `embed` + add): `embed` (symbolic), `similarity_search` (may be service-level using resolved embed model), keep capability `embedding`.
- Register ready model: `local:symbolic-features-v1` (`runtime=symbolic_features`, `status=ready`, `locality=local`).
- Keep `local:embedding-stub` for text stub or retire from default bootstrap with docs note — prefer **both**: stub remains unavailable for text; symbolic model is default for `AI_OP_EMBED`.
- Broaden protocol:

```text
SymbolicEmbeddingModel:
  embed_composition_scope(composition, scope) -> CompositionEmbeddingV1
```

Do not pretend text `embed(texts)` equals musical similarity.

**4. Cache invalidation**

- Key: `(embedding_model_id, profile_id, algorithm_version, source_fingerprint, scope_digest)`.
- Store: process LRU + optional SQLite table beside project DB **or** filesystem under configurable `EMBEDDING_CACHE_DIR` (default under same data dir as projects, never `DATASET_ROOT`).
- On project PATCH / autosave / revision commit: if fingerprint changed, drop cache entries for that project_id (and optionally reindex).
- Similarity index entries store fingerprint; stale hits filtered or rebuilt.

**5. Similarity search corpus**

- Default corpus = **user's projects** (working composition and/or latest revision), scoped embeddings precomputed lazily.
- Optional: in-request candidates (current composition sections only) without touching other projects.
- Explicit setting `EMBEDDING_ALLOW_DATASET_CORPUS=0` (default): searching `DATASET_ROOT` off. If enabled later, provenance must mark `corpus=dataset` and respect dataset license/provenance gates — **not** required for acceptance.
- Never copy project JSON into dataset pipeline as a side effect of embed/search.

**6. Style / reference conditioning**

Acceptance path:

1. User selects reference = project P + scope S (section preferred).
2. Server embeds reference → `CompositionEmbeddingV1` + `ReferenceProvenance`.
3. Development `vary_section` (or generate) accepts `style_reference` block.
4. Conditioning application (V3):
   - **Primary:** inject bounded, human-readable feature summary + top-k similar descriptors into LLM context (counts/histograms rounded; no full vector dump in prompts unless compact dim≤N and documented).
   - **Secondary:** optional cosine-distance advisory in postcondition warnings (`reference_similarity_delta`) comparing candidate vs reference embeddings — diagnostic only, not a hard musical-quality gate.
   - **Tokenizer bridge (optional):** map coarse bins → existing `genre`/`key` conditioning only when confidently inferred from features; never invent artist tokens.
5. Result must remain a **distinct** Composition (new events / development candidate); fingerprint ≠ reference fingerprint; provenance records reference ids.

**7. "Do not equate artist with style"**

- Schemas forbid `artist_id` / `composer_name` as embedding dimensions.
- UI copy: "Musical reference" / "Similar material" — not "in the style of \<Artist\>".
- Freeform user instructions may still mention artists (existing prompt field) but system conditioning uses **reference material embeddings**, not a single artist enum.

**8. Provenance**

Every conditioned generate/develop response and applied revision summary includes `reference_provenance` (or nested under `generation_parameters` with size bounds): project_id, scope kind/ids, source_fingerprint prefix, embedding model_id, profile_id. Never store full reference composition in provenance.

**9. Frontend**

- Develop tab: "Use musical reference" → pick project → pick section (list from loaded composition) → show similarity score vs current selection.
- Compact "Similar sections" list (top-k) from current workspace + other projects.
- Motifs tab (light): "Related motifs" via embed search over motif scopes in current project.
- Preview-first apply unchanged.
- No training dashboard.

**10. Logging**

Verbose structured extras: model_id, profile_id, scope_kind, fingerprint prefixes, dims, cache hit/miss, hit_count, distance metric, reference project_id, conditioning mode, error codes. Never full vectors at INFO (DEBUG may log first 8 dims only). Never event arrays / prompts / absolute home paths.

**11. Evaluation examples**

Fixture set under `backend/tests/fixtures/embeddings/`: pairs/groups of V2 snippets with expected nearest-neighbor order under `symbolic.features.v1` (e.g. same rhythm different pitch still nearer than unrelated texture). CLI or pytest marks `eval_example` — documents limits; `musical_quality_claim: false`.

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Embedding subsystem compatible with unified model registry | 1–3 |
| Embed whole composition / section / motif / selected bars | 2, 4 |
| Evaluate handcrafted / learned / hybrid; no large net required | Approach section + Task 2 (handcrafted default) |
| Similarity search between compositions/sections | 5–6 |
| Use cases: similar sections, related motifs, reference select, closer-to-X | 6–8, 11 |
| Style/reference conditioning for symbolic generation | 7–8 |
| Not equate artist/composer with single style label | 1, 7, 12 |
| Preserve provenance for reference material | 7–8, 10 |
| No train/copy user projects into global datasets without config | 5, 9, 12 |
| Embedding cache invalidation on composition change | 4, 9 |
| Frontend similarity/reference UI | 11 |
| User can choose project/section reference and generate distinct conditioned variation | 7–8, 10–12 |
| Tests, evaluation examples, logging, documentation | 10–12 |

## Commit Plan
- **Commit 1** (tasks 1–3): `feat(embeddings): schemas, handcrafted symbolic embedder, and AI runtime registration`
- **Commit 2** (tasks 4–6): `feat(embeddings): scoped embed cache, project similarity index, and search API`
- **Commit 3** (tasks 7–9): `feat(embeddings): reference conditioning, provenance, and cache invalidation hooks`
- **Commit 4** (tasks 10–12): `feat(embeddings): frontend reference UI, acceptance tests, eval fixtures, and docs`

## Tasks

### Phase 1: Contracts, handcrafted embedder, registry

- [x] Task 1: Embedding + scope + provenance schemas and error codes
  Deliverable: Strict Pydantic models (`composition.embedding.v1`, embed scopes, similarity query/hit, reference provenance, style conditioning). Closed Literals for error/warning codes. Document that artist/composer names are **not** embedding ids. Settings module for cache dir, index limits, `EMBEDDING_ALLOW_DATASET_CORPUS` default false, max dims, top-k caps.
  LOGGING: DEBUG field validation; INFO schema/algorithm version constants; never log full vectors.
  Files: `backend/app/embeddings/schemas.py`, `backend/app/embeddings/settings.py`, `backend/app/embeddings/errors.py`, `.env.example` commented keys.

- [x] Task 2: Handcrafted feature extractor + vector utilities (`symbolic.features.v1`)
  Deliverable: Deterministic feature extraction for all four scopes from Composition V2; fixed dim order; L2 normalize; cosine / euclidean distance helpers; empty-scope errors. Unit tests against fixtures (stable digests). Hybrid hook: optional `projection_id=none` only.
  LOGGING: INFO profile_id, dims, scope_kind, note_count; DEBUG feature group norms; never event payloads.
  Files: `backend/app/embeddings/features.py`, `backend/app/embeddings/vector.py`, `backend/tests/test_embeddings_features.py`, fixtures under `backend/tests/fixtures/embeddings/`.

- [x] Task 3: AI runtime symbolic embedding adapter + bootstrap
  Deliverable: `SymbolicEmbeddingModel` protocol (keep text stub separate); `SymbolicFeaturesEmbeddingModel` runtime; register `local:symbolic-features-v1` as ready default for `AI_OP_EMBED`; routing resolves embed ops; `GET /ai/models?capability=embedding` lists ready model; update `docs/ai-runtime.md` capability blurb (full docs in Task 12).
  LOGGING: INFO register model_id/status; WARN if only stub available; ERROR unavailable codes.
  Files: `backend/app/ai_runtime/protocols.py`, `runtimes/symbolic_features.py` (new), `bootstrap.py`, `operations.py` / `capabilities.py` if new ops, `backend/tests/test_ai_models_routes.py` updates.

### Phase 2: Cache, index, search API

- [x] Task 4: Embedding cache keyed by fingerprint + scope digest
  Deliverable: Get-or-compute embed with LRU/SQLite/fs cache; API to invalidate by `project_id` and/or fingerprint; tests proving change of one note changes fingerprint → cache miss.
  LOGGING: INFO cache hit/miss, key prefixes; DEBUG eviction counts.
  Files: `backend/app/embeddings/cache.py`, tests.

- [x] Task 5: Project-scoped similarity index (no global dataset write)
  Deliverable: Index builder over listed projects' working (or tip revision) compositions for scopes composition+section (motif optional lazy); refuse dataset export helper unless `EMBEDDING_ALLOW_DATASET_CORPUS` and explicit call; hard fail closed otherwise with `dataset_export_forbidden`.
  LOGGING: INFO indexed_project_count, scope_counts; ERROR on forbidden dataset export; never log composition JSON.
  Files: `backend/app/embeddings/index.py`, `backend/app/services/composition_embedding.py` (start), tests.

- [x] Task 6: HTTP embed + similarity + related-motifs routes
  Deliverable: Router under `routers/embeddings.py` (prefer over `main.py`):
  - `POST /embeddings/compute` — composition + scope → embedding card
  - `POST /embeddings/similarity` — query scope + corpus selector → ranked hits
  - `POST /embeddings/related-motifs` — motif_id or occurrence → related in-composition / cross-project motif hits
  Domain errors → 4xx/5xx per mukit conventions. Fake mode uses deterministic vectors from fixtures.
  LOGGING: INFO op, model_id, hit_count, latency_ms; WARN empty index; never vectors at INFO.
  Files: `backend/app/routers/embeddings.py`, wire in `main.py` include_router only, `frontend` API client stubs optional until Task 11, `backend/tests/test_embeddings_routes.py`.

### Phase 3: Conditioning, provenance, invalidation hooks

- [x] Task 7: Style/reference conditioning DTO + development/generate wiring
  Deliverable: Accept `style_reference` on development preview (and generate request where natural): `{ project_id?, composition?, scope, mode }`. Resolve reference, embed, build bounded prompt feature summary, attach provenance. Fake LLM path produces distinct candidate that still validates V2 and differs in fingerprint from reference. Postcondition optional similarity delta warning codes.
  LOGGING: INFO conditioning mode, reference project_id, fingerprint prefixes, candidate similarity delta; never prompts/vectors full dump.
  Files: `composition_development_schemas.py`, `llm_composition_development.py` / context builders, `schemas.py` generate request if needed, `fake_llm.py`, tests for conditioned vary.

- [x] Task 8: "Closer to reference X" development path + acceptance scenario
  Deliverable: Documented + tested flow: select section A, reference section X, `vary_section` with `intent`/`style_reference` → preview candidates → apply → working composition ≠ reference fingerprint; provenance persisted on revision summary when applied. Preserve existing material per development preservation assertions.
  LOGGING: same as Task 7 + apply path provenance fields present.
  Files: service tests + store apply path ensuring `reference_provenance` in revision `summary_json` / `AiProvenance.generation_parameters` (bounded).

- [x] Task 9: Cache/index invalidation on project composition change
  Deliverable: Hooks from project update / history commit paths to invalidate embedding cache + mark index stale for `project_id`. Autosave path must not thrash: debounce or invalidate-by-fingerprint only when fingerprint changes.
  LOGGING: INFO invalidations with project_id + fingerprint prefix; DEBUG skip when unchanged.
  Files: `project_store.py` / `project_history.py` thin hooks or service callback, tests.

### Phase 4: Frontend, tests, eval, docs

- [x] Task 10: Backend acceptance tests + evaluation examples
  Deliverable: Pytest coverage for features stability, cache invalidation, similarity ranking on eval fixtures, conditioned develop fake path, registry discovery, dataset-export forbidden. Evaluation examples README or docstring with `musical_quality_claim: false`. Optional CLI `python -m app.embeddings.cli eval-examples`.
  LOGGING: tests assert log extras where useful; no secrets.
  Files: `backend/tests/test_embeddings_*.py`, `backend/tests/fixtures/embeddings/`, optional `backend/app/embeddings/cli.py`.

- [x] Task 11: Frontend reference selection + similarity UI
  Deliverable: Develop panel reference picker (project + section); show top similar sections; wire API clients; store session fields for reference + hits (ephemeral); Motifs light "related" optional. Follow existing preview/apply and fingerprint gates. Unit tests for normalize helpers.
  LOGGING: existing frontend log level; debug reference ids / score counts only.
  Files: `frontend/src/api/musicApi.js`, `musicStore.js` slice, `CompositionDevelopmentPanel.jsx`, small utils + tests; optional MotifPanel hook.

- [x] Task 12: Documentation, AGENTS/ARCHITECTURE/ROADMAP, logging audit
  Deliverable: New `docs/embeddings.md` (approach tradeoffs, scopes, cache, conditioning, provenance, non-goals, eval disclaimer). Update `docs/ai-runtime.md`, `docs/composition-development.md`, README feature bullet, `AGENTS.md` structure entry, `.ai-factory/ARCHITECTURE.md` module row, `.ai-factory/ROADMAP.md` new unchecked milestone → check on complete. Confirm logging policy in docs. Cross-link tokenizer conditioning and datasets provenance (no silent ingest).
  LOGGING: N/A (docs); verify code paths meet verbose requirements.
  Files: `docs/embeddings.md`, linked docs, `AGENTS.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `.env.example`.

## Implementation notes for `/aif-implement`

- Prefer handcrafted path green in CI **without** torch; do not add torch to default backend image for embeddings.
- Reuse analysis math carefully: extract shared pure helpers if needed rather than calling `POST /analysis/composition` from embed hot path.
- Motif scope must use canonical motif event ids / relative notes — do not invent pitches from harmony.
- Keep arrangement/development fingerprint apply gates intact.
- When both fix and exploit-style requests appear in future plans: N/A here (not cyber).
- Roadmap milestone text must match this plan's `Milestone:` string for `/aif-verify` linkage.
