# Symbolic Composition Embeddings

Handcrafted-first **musical feature embeddings** for Composition V2 note material.
Users and AI tools can score **distributional / structural affinity**, pick a
**musical reference** (project/section/motif/bars), search similar material, and
condition development/generate on the reference embedding — without equating
artist names with style ids, and without copying user projects into
`DATASET_ROOT`.

**`musical_quality_claim: false`** — cosine similarity is affinity, not aesthetic
quality or copyright-safe “in the style of ⟨Artist⟩”.

Default production model: `local:symbolic-features-v1`  
Profile: `symbolic.features.v1` (81-D L2-normalized vector; no torch required)

## Architecture

```text
routers/embeddings.py
        ↓
services/composition_embedding.py (+ style_conditioning, invalidation)
        ↓
app/embeddings/   # pure engine
  schemas | features | vector | cache | index | settings
        ↓
ai_runtime/runtimes/symbolic_features.py
```

Learned / hybrid adapters (`projection: offline_linear.v1`) are schema-supported
but **deferred** — V3 acceptance uses `projection: none` only.

## Scopes

| Kind | Selects |
|------|---------|
| `composition` | All pitched note onsets in the document |
| `section` | Section by index (+ optional id/bounds checks) |
| `motif` | Motif definition / occurrence via canonical `event_ids` |
| `bar_range` | Inclusive bars (optional `track_id`) |

Playable pitches come **only** from `tracks[].events[]`. Never invent notes from
harmony, markers, sections, or `composition.analysis.v1`. Analysis reports are
never stored as embedding rows.

## HTTP API

| Method | Path | Role |
|--------|------|------|
| `POST` | `/embeddings/compute` | Composition + scope → embedding card |
| `POST` | `/embeddings/similarity` | Nearest-neighbor over projects or in-request sections |
| `POST` | `/embeddings/related-motifs` | Motif-scope neighbors |
| `POST` | `/embeddings/reference/resolve` | Resolve `style_reference` → embedding + provenance + conditioning |

Domain errors map to 4xx/5xx (`embed_empty_scope` → 422, `dataset_export_forbidden` → 403,
`embedding_model_unavailable` → 503, …).

Discovery: `GET /ai/models?capability=embedding` lists ready
`local:symbolic-features-v1` and the unconfigured text stub
`local:embedding-stub`. Text stubs **must not** be used for musical similarity.

## Cache & invalidation

Cache key: `(model_id, profile_id, algorithm_version, source_fingerprint, scope_digest)`.

- Process LRU + optional filesystem under `EMBEDDING_CACHE_DIR` (default:
  `<dir of PROJECT_DB_PATH>/embedding_cache`).
- **Never** under `DATASET_ROOT`.
- Project PATCH / autosave / revision commit invalidate only when the composition
  fingerprint changes.

## Similarity corpus

- Default: **user projects** (working composition), scopes composition + sections.
- Optional: `corpus.kind=in_request` (current composition sections only).
- `EMBEDDING_ALLOW_DATASET_CORPUS=0` (default): searching/exporting `DATASET_ROOT`
  is refused with `dataset_export_forbidden` / `similarity_corpus_forbidden`.

## Style / reference conditioning

1. User selects a musical reference (project + section preferred).
2. Server embeds reference → `composition.embedding.v1` +
   `composition.reference_provenance.v1` (`artist_label_used: false`).
3. Development preview (`vary_section` / continue / add) accepts `style_reference`.
4. Conditioning modes: `prompt_features` (default bounded feature summary),
   optional `tokenizer_labels` (coarse genre/key only when inferred — never artist
   tokens), optional `vector_hint` when dims ≤ `EMBEDDING_PROMPT_VECTOR_MAX_DIMS`.
5. Candidate remains a **distinct** Composition; fingerprint ≠ reference;
   optional advisory warning `reference_similarity_delta` (not a quality gate).
6. On Apply, nest `reference_provenance` under
   `AiProvenance.generation_parameters` (bounded prefixes only).

UI copy: **Musical reference** / **Similar material** — not “in the style of ⟨Artist⟩”.

## Configuration

See `.env.example` (`EMBEDDING_*`). Notable defaults:

| Env | Default | Notes |
|-----|---------|-------|
| `EMBEDDING_ALLOW_DATASET_CORPUS` | `0` | Hard refuse dataset export/search |
| `EMBEDDING_CACHE_DIR` | beside project DB | Never `DATASET_ROOT` |
| `EMBEDDING_DEFAULT_TOP_K` / `MAX_TOP_K` | `10` / `50` | Search caps |
| `EMBEDDING_PROJECTION_ID` | `none` | Hybrid projection off |

## Offline eval CLI

```bash
cd backend
python -m app.embeddings.cli info --json
python -m app.embeddings.cli eval-examples
python -m app.embeddings.cli eval-examples --json
```

Fixtures live under `backend/tests/fixtures/embeddings/` with
`musical_quality_claim: false`. Expected order: same-rhythm transpose nearer than
unrelated texture.

## Logging

Structured extras: `model_id`, `profile_id`, `scope_kind`, fingerprint prefixes,
`dims`, cache hit/miss, `hit_count`, distance metric, reference `project_id`,
conditioning mode, error codes.

Never at INFO: full vectors (DEBUG may log first 8 dims), event arrays, prompts,
or home-absolute paths.

## Non-goals

- Training a large embedding network / requiring ROCm for embeddings
- Audio / spectrogram embeddings
- Public multi-tenant vector DB
- Auto-ingest of all projects into a global corpus
- Replacing motif identity gates or analysis sidecars
- Claiming similarity = musical quality

## See also

- [AI Runtime](ai-runtime.md) — capability registry / `AI_OP_EMBED`
- [Composition Development](composition-development.md) — `style_reference` on preview
- [Symbolic tokenizer](tokenizer.md) — optional coarse conditioning labels
- [Symbolic datasets](datasets.md) — provenance gates; no silent project ingest
