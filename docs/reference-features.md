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
  → composer-profile soft fragment (generate only; when strength ≠ off)
  → PRESERVE abstract lines (current scope; develop/edit)
  → BORROW strength-tagged reference fragments
  → REGENERATE invent-new lines
  → anti-melody / gated own-project motif-reuse instruction
  → legacy whole summary only when no masks and no policy borrow
```

Hard `GenerationConstraints` / prompt instruments / key / edit bar-track selection are never rewritten.

## Conditioning policy (`reference.conditioning.policy.v1`)

Operation-level **preserve / borrow / regenerate** disposition with per-dimension
borrow strength (`off|light|normal|strong`). Strengths live **only** on the policy
DTO — never on `StyleReferenceRequest`.

| Disposition | Meaning |
|-------------|---------|
| preserve | Keep property family from **current** scope (abstract summary only) |
| borrow | Transfer masked reference soft fragments × strength |
| regenerate | Invent new; never inject reference fragments for these dims |

**Partition:** `strict_partition` is **disjoint-only** — overlaps → 422
`reference_conditioning_partition_overlap`. Unspecified dims get no soft guidance
(not forced into regenerate). Same dimension on two borrow bindings → 422
`reference_conditioning_borrow_dimension_conflict`.

**Motif reuse:** `allow_motif_reuse` requires `active_project_id` equal to every
borrow binding `project_id`. Still never pastes note sequences.

**Operations:** generate, develop preview, and AI region edit (`replace_region`).
Edit keeps `in_region_events` for the patch contract; anti-copy applies to
**reference** material only. Edit responses include `generation_parameters` for
Apply CAS provenance.

**AC example:** borrow A:`texture` + B:`rhythm`, regenerate `melodic_contour` +
`harmony` → soft block contains only those tagged borrow lines (not A melody /
B harmony). FE presets and multi-ref A/B assignment live in
`ReferenceFeaturesControls` / `referenceConditioningPolicy.js`.

Composer Profile soft merge remains **generate-only** in this path (develop
`profile_id` is a follow-on).

## API

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/reference-features/analyze` | Analyze → `reference.features.v1` (+ optional `compare_to`) |

Generate / develop / edit accept:

- `style_reference.dimensions[]` / `style_references[]`
- `reference_conditioning_policy`
- `active_project_id` (motif-reuse gate)

Provenance under `generation_parameters`:

- `reference_features[]` — ids, scope, fingerprint prefix, dimensions, optional strengths
- `reference_conditioning_policy` — preserve/regenerate lists, borrow digests, `policy_digest`

Never summaries, vectors, or event arrays.

## Configuration

```bash
# REFERENCE_FEATURES_MAX_REFERENCES=3
# REFERENCE_FEATURES_SUMMARY_MAX_CHARS=280
# REFERENCE_FEATURES_SOFT_FRAGMENT_MAX_CHARS=1600
# REFERENCE_FEATURES_ANALYZE_LRU_SIZE=0
# REFERENCE_FEATURES_POLICY_DIGEST_PREFIX_LEN=16
# REFERENCE_FEATURES_STRENGTH_LEGEND_MAX_CHARS=240
```

## Privacy

- Reference material stays in `PROJECT_DB_PATH` / session only.
- Analyze never mutates the reference V2 document.
- No automatic training-dataset ingest.

## See also

- [Symbolic embeddings](embeddings.md)
- [Composer profiles](composer-profiles.md)
- [Composition development](composition-development.md)
