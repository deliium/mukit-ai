# Reference Features

Selective **musical reference feature dimensions** (`reference.features.v1`) let users
take abstract properties from a reference (rhythm, texture, density, …) without
copying melodies or mutating the reference Composition.

Complementary to:

- **Musical reference / embeddings** — whole-scope affinity and legacy `feature_summary`
- **Composer Profiles** — durable multi-project preference documents

## Summary

- Analyze a project, revision, or inline V2 → derived report (session/request only).
- Closed dimension registry with human labels (e.g. **Rhythmic density** → `density`,
  **Orchestration texture** → `texture`).
- Masked soft conditioning on **generate** and **develop**.
- Optional group-slice **affinity** only when a compare target is present
  (`musical_quality_claim: false` always).
- Never writes `DATASET_ROOT`. Never stores event arrays / motif note lists /
  embedding vectors in the report.

## User-facing behavior

1. On the **Develop** tab, enable **Use musical reference** and pick a project/section.
2. Optionally enable **Select reference feature dimensions** and check the properties
   to transfer (default suggestion: density + texture).
3. Preview development or generate with the same musical reference: only selected
   dimension soft fragments are injected (plus an anti-melody instruction).
4. Leave the mask off for legacy whole-reference `feature_summary` behavior.

Copy: “Does not copy melodies.” Affinity scores are structural similarity only —
not musical quality or artist style.

## Mask semantics

| Field state | Behavior |
|-------------|----------|
| `style_references[]` non-empty | Authoritative; singular `style_reference` ignored (warning) |
| Only `style_reference` | Single binding (V3-compatible) |
| `dimensions` omitted / null | Legacy whole `feature_summary` |
| `dimensions` non-empty | Soft fragments for those dims only |
| `dimensions: []` | 422 `reference_feature_mask_empty` |
| All requested dims unavailable | 422 `reference_feature_mask_empty` |

## Merge order

```text
prompt hard/soft lines
  → composer-profile soft fragment (when strength ≠ off)
  → masked reference feature fragments (or legacy whole summary when dimensions omitted)
```

Hard `GenerationConstraints` / prompt instruments / key are never rewritten.

## API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/reference-features/analyze` | Analyze → `reference.features.v1` (+ optional `compare_to`) |

Generate (`LLMMusicGenerationRequest`) and develop preview accept:

- `style_reference.dimensions[]`
- `style_references[]` (cap `REFERENCE_FEATURES_MAX_REFERENCES`)

Provenance under `generation_parameters.reference_features[]`:
`project_id?`, `revision_id?`, `scope_kind`, `fingerprint_prefix`, `dimensions[]`,
`unavailable_codes[]` — never summaries or vectors.

## Configuration

```bash
# REFERENCE_FEATURES_MAX_REFERENCES=3
# REFERENCE_FEATURES_SUMMARY_MAX_CHARS=280
# REFERENCE_FEATURES_SOFT_FRAGMENT_MAX_CHARS=1600
# REFERENCE_FEATURES_ANALYZE_LRU_SIZE=0
```

## Privacy

- Reference material stays in `PROJECT_DB_PATH` / session only.
- Analyze never mutates the reference V2 document.
- No automatic training-dataset ingest.

## See also

- [Symbolic embeddings](embeddings.md)
- [Composer profiles](composer-profiles.md)
- [Composition development](composition-development.md)
