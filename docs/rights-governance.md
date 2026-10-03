# Rights governance

Centralized **permission-to-use** registry for training and reference paths.
Playable scores stay **`composition.v2`**. There is no `composition.v5`.
Rights documents never store note events, PCM, or prompts.

Content provenance (`content.provenance.*`) remains the derivation DAG —
complementary, not replaced. See [content-provenance.md](content-provenance.md).

## Terminology

| Term | Meaning |
|------|---------|
| **Rights entry** | Durable `rights.registry.entry.v1` (source, license, attribution, use policy, restrictions, verification) |
| **Ownership class** | `public_domain` \| `user_owned` \| `licensed` \| `unknown` |
| **Use policy** | Exactly one of `training_allowed` \| `reference_only` \| `no_training` |
| **Allowed uses** | Derived closed set from use policy (`train`, `reference_analyze`, `eval_holdout`) |
| **Verification status** | `unverified` \| `attested` \| `verified` \| `disputed` |
| **Rights digest** | SHA-256 prefix over canonical entry fields used in manifests |
| **Model/data provenance manifest** | `model.data.provenance.manifest.v1` — train sources + rights digests + disposition |
| **Legacy status** | Today's `DatasetProvenance.status` (`verified_redistributable` \| `user_owned` \| `public_domain` \| `restricted` \| `unknown`) |

`content.provenance.manifest.v1` is derivation honesty/C2PA.
`dataset.manifest.v1` is corpus inventory (counts/digests).
Neither is a training-use rights list.

## Two-axis model

Ownership and use policy are independent. Examples:

- `licensed` + `reference_only` — analyze OK, never train
- `user_owned` + `no_training` — not attested / restricted train
- `public_domain` + `training_allowed` — train + reference

## Legacy → registry map (Part H)

| Legacy `DatasetProvenance.status` | ownership_class | use_policy | verification_status |
|-----------------------------------|-----------------|------------|---------------------|
| `public_domain` | `public_domain` | `training_allowed` | `verified` if URL/reference present else `unverified` |
| `user_owned` + attested | `user_owned` | `training_allowed` | `attested` |
| `user_owned` without attested | `user_owned` | `no_training` | `unverified` |
| `verified_redistributable` | `licensed` | `training_allowed` | `verified` |
| `restricted` | `licensed` if license present else `unknown` | `no_training` | `verified` or `unverified` |
| `unknown` | `unknown` | `no_training` | `unverified` |

Sidecar / registry field `use_policy` (optional on `*.meta.json` / `*.meta.yaml`)
overrides the default use policy from `provenance_status`, even when the legacy
status looks train-shaped. Alias strings `training_allowed` / `reference_only` /
`no_training` are accepted on ingest when ownership is supplied or defaulted to
`unknown`.

## Eligibility formulas (Part I)

1. **Train:** `use_policy == training_allowed` ∧ (`ownership_class != user_owned` ∨ verification ∈ {attested, verified}) ∧ `verification_status != disputed` ∧ `"train" ∈ allowed_uses`.
2. **Reference:** `use_policy ∈ {training_allowed, reference_only}` ∧ `verification_status != disputed` ∧ `"reference_analyze" ∈ allowed_uses`.
3. **Allowed uses projection:**
   - `training_allowed` → `{train, reference_analyze, eval_holdout}`
   - `reference_only` → `{reference_analyze}`
   - `no_training` → `{eval_holdout}` only when dataset `include_non_trainable_in_eval` is true; else empty for product gates
4. Any source failing train eligibility **must not** appear in train source lists of `model.data.provenance.manifest.v1`, dataset `splits/train.*`, personal snapshot project ids, or MT train inputs.

## Hard-fail vs content-provenance soft-fail (Part J)

Rights evaluate/gates **hard-fail** with structured codes (`rights_train_refused`,
`rights_reference_refused`, `personal_rights_refused`). Primary train / reference /
dataset-split / MT-train ops abort on refuse.

Content-provenance capture stays **soft-fail** (WARN and continue). Derivation
lineage ≠ permission-to-use. Do not treat a missing provenance parent as a
rights allow.

## Resolve and write-back (Part K)

For a studio source:

1. Load registry row by `(source_kind, source_id)` if present → use it (wins over request claims).
2. Else if request carries legacy `DatasetProvenance` / rights attestation → map (Part H), including explicit `use_policy`.
3. Else synthetic `unknown` + `no_training` → refuse the gated use.

**Never** invent `training_allowed` on missing data.

Personal-train write-back: after a successful snapshot (all projects pass train
evaluate), upsert studio registry entries for those projects. List / open /
evaluate-adapter / project create never upsert.

## Persistence

| Surface | Location |
|---------|----------|
| Studio registry | SQLite `rights_registry_entries` in `PROJECT_DB_PATH` (Alembic `20261003_0030`) |
| Dataset version siblings | `$DATASET_ROOT/.../<version>/rights/index.jsonl` + `rights/manifest.json` |

Rights siblings are **not** folded into `dataset.manifest.v1` digests
(`dataset_version_id` stays bit-stable). No separate `RIGHTS_ROOT`.

`compute_train_eligible` / `assert_train_split_policy` delegate to the shared
evaluator on mapped entries — so `reference_only` never enters train even when
legacy status is `verified_redistributable` / `public_domain` / `user_owned`.

## Unsafe train pollution

Only the existing dataset flag `eligibility.allow_unsafe_train_pollution` may
force non-trainable material into a train split (logs ERROR). Personal composer
and Music Transformer ship-1 have **no** equivalent override.

## Integration

| Path | Behavior |
|------|----------|
| Dataset build / split | Map sidecars; write rights siblings; train split excludes non-`training_allowed` |
| Personal composer train | Part K resolve; refuse unknown / `reference_only` / `no_training`; write-back on success |
| Music Transformer train | Gate train inputs; emit `model.data.provenance.manifest.v1` sibling |
| Reference analyze / condition | `reference_only` and `training_allowed` OK; `no_training` / `unknown` refuse |

## HTTP

| Method | Path | Notes |
|--------|------|-------|
| GET | `/rights-governance/status` | Caps / readiness of the feature — **not** on `/ready` |
| GET | `/rights-governance/entries/{kind}/{id}` | Read one row; missing → `rights_not_found` |
| PUT | `/rights-governance/entries/{kind}/{id}` | Upsert with CAS (`expected_version`) |
| POST | `/rights-governance/evaluate` | Pure evaluate for a use + entry / attestation |

## Env

```
# RIGHTS_GOVERNANCE_MAX_ENTRIES_PER_PROJECT=256
# RIGHTS_GOVERNANCE_MAX_ATTRIBUTION_CHARS=500
```

## UI

Focused surfaces inside existing **Profiles → Personal Composer** (hydrate GET
registry when present, show ownership / use_policy / verification, map train
refuse codes) and **Develop** reference refuse copy for
`rights_reference_refused`. No new Rights tab in ship-1. Opening Profiles never
starts training, never writes registry rows, never mutates `composition.v2`.

## Agent boundary

`ai_agents/` must not import `rights_governance_store` or
`rights_governance_settings`.

## Ship-1 non-goals

- Neural / mix **render egress** rights gating
- Stamping `rights.entry_id` onto content-provenance parents
- New top-level Rights tab / corpus browse UI
- Personal or MT unsafe-train override
- Editing `ROADMAP.md` as part of this feature

## Acceptance vector

Store-upsert a `reference_only` entry for project P → reference analyze succeeds →
personal train / dataset train manifest / MT train inputs exclude P.
`unknown` personal train returns `personal_rights_refused` (or mapped
`rights_train_refused`). HTTP router is not required for that store-level bar.

## See also

- [Content provenance](content-provenance.md) — derivation DAG (soft-fail)
- [Symbolic datasets](datasets.md) — offline corpus + rights siblings
- [Personal symbolic composer](personal-symbolic-composer.md)
- [Symbolic Music Transformer](music-transformer.md)
- [Reference features](reference-features.md)
