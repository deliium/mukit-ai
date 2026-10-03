# Implementation Plan: V5 Dataset and Rights-Governance Registry

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Focused rights surfaces inside existing **Profiles → Personal Composer** (extend the existing attestation form in `PersonalComposerPanel.jsx` / `personalComposerForm.js` — hydrate GET registry when present, show resolved `use_policy`, map refuse codes) and **Develop reference** refuse copy — not a new workspace tab. Dataset/MT paths stay CLI. Opening Profiles never starts training, never writes registry rows, never mutates `composition.v2`. No piano-roll redesign
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Rights gates **hard-fail** (unlike content-provenance soft-fail). Resolution order: registry entry → request attestation → refuse; never default `training_allowed`. Successful personal train upserts studio registry rows. Sidecar `use_policy` + hard-locked sibling `rights/index.jsonl` + `rights/manifest.json`. `compute_train_eligible` / `assert_train_split_policy` delegate to shared evaluator (so `reference_only` cannot train even when legacy status looks train-shaped). Acceptance uses **store** upsert (Task 2), not HTTP (Task 7). Ship-1 = train + reference (+ dataset/MT manifests); neural/mix **render egress rights out of ship-1**. Module boundary: policy pure; no `dataset.pipeline` ↔ rights cycle. Status on `/rights-governance/status` only (not `/ready`)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). First unchecked ROADMAP items (performance conductor / spatial) are orthogonal and already planned/shipped; this plan documents a new franchise milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`
- Scope: centralized non-playable rights-governance registry (`rights.registry.entry.v1`) + pure policy evaluator + train/reference gates + `model.data.provenance.manifest.v1` for training/data use; integrate dataset build, personal composer train, Music Transformer train, and reference conditioning. Never invent `composition.v5`. Never store note events / PCM / prompts on rights docs. Never silently put `unknown` / `reference_only` / `no_training` into training manifests. `ai_agents/` must not import the rights store. Content provenance (`content.provenance.*`) remains the derivation DAG — complementary, not replaced. Neural/mix render rights gating and stamping `rights.entry_id` onto provenance parents are **out of ship-1**
- Predecessors: `.ai-factory/plans/v5-content-provenance-tracking.md` (shipped; derivation lineage), offline `dataset.pipeline.v1` provenance gate (shipped), personal composer request-scoped rights (shipped), reference conditioning (shipped, no rights gate), Music Transformer train over dataset splits (shipped, trusts split)

## Roadmap Linkage
Milestone: "V5 Dataset and rights-governance registry"
Rationale: Training and reference paths reuse ad-hoc `DatasetProvenance` sidecars and per-request personal attestations, but there is no durable centralized registry of source / license / attribution / allowed uses / restrictions / verification that every train and reference path must consult — so `reference_only` and `unknown` can still be treated inconsistently. First unchecked ROADMAP items (performance / spatial) are orthogonal. Milestone appended and marked complete in `.ai-factory/ROADMAP.md` after `/aif-verify` (2026-10-04).

## Goal

Make **training and reference usage constraints** explicit and enforceable through one rights-governance registry and a shared policy evaluator — so unknown or restricted material cannot silently enter training datasets or personal/general training manifests, while `reference_only` sources remain usable for permitted reference analysis.

(Product goal language may still say “render-usage”; **ship-1 does not gate neural/mix egress** — working-composition render stays under ordinary project control. A later plan may add render rights.)

Represent rights categories (locked two-axis model; see Terminology):

Ownership / license class:
- `public_domain`
- `user_owned`
- `licensed`
- `unknown`

Use policy (primary disposition; closed):
- `training_allowed`
- `reference_only`
- `no_training`

Store on each registry entry:
- source (artifact kind + id + optional fingerprint prefix / source URL / reference)
- license (free text + optional SPDX)
- attribution
- allowed uses (closed set projection of use policy)
- restrictions (closed codes)
- verification status

Integrate with:
- dataset creation (`dataset.pipeline.v1` build / split / manifest)
- personal model training (`personal.training_manifest.v1`)
- general model training (Music Transformer experiment / checkpoint card)
- reference conditioning (`reference.features.v1` / policy assembly)

Ship:

1. Non-playable **`rights.registry.entry.v1`** (SQLite studio registry + dataset-version rights index) and pure **`evaluate_rights_use`** policy.
2. Hard gates at dataset train split, personal train snapshot, MT train load, and reference analyze/condition — fail closed for train; allow reference for `reference_only`.
3. Exportable **`model.data.provenance.manifest.v1`** listing source entry ids, rights digests, and use disposition used by a training run or dataset train split (never notes/PCM).
4. Compatibility map from today's `DatasetProvenance.status` (`verified_redistributable` → `licensed` + `training_allowed`, `restricted` → ownership preserved + `no_training`, etc.) without deleting sidecar ingest.
5. Policy tests including the acceptance vector below + docs.

Acceptance: A source marked `reference_only` may be used for permitted reference analysis (`POST /reference-features/analyze` and reference-conditioned soft assembly) but is **automatically excluded** from training manifests (dataset `splits/train.*`, personal training snapshot/manifest, MT train input set, and `model.data.provenance.manifest.v1` train source lists). Unknown / `no_training` never enter those train manifests without an explicit unsafe override that logs ERROR (dataset only; personal/MT have no unsafe override in ship-1).

```text
sidecar / attestation / operator upsert / personal-train success write-back
        │ write rights.registry.entry.v1
        ▼
rights registry (SQLite studio + dataset version index)
        │ resolve: registry → request attestation → refuse
        │ evaluate_rights_use(use)   ← hard-fail (not provenance soft-fail)
        ├─ use=train      → allow only training_allowed (+ attested when user_owned)
        ├─ use=reference  → allow training_allowed | reference_only (not no_training/unknown)
        └─ refuse → hard error (no silent include)
        ▼
dataset train split / personal snapshot / MT train / reference analyze
        │ emit
        ▼
model.data.provenance.manifest.v1  (train sources only; reference_only absent)
        + dataset version siblings: rights/index.jsonl + rights/manifest.json
content.provenance.* ── complementary derivation DAG (unchanged; soft-fail capture)
dataset.manifest.v1 ── corpus inventory (unchanged role; digests stay bit-stable)
```

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**. **Rights entry** = durable `rights.registry.entry.v1` (source, license, attribution, use policy, restrictions, verification) — never events/PCM/prompts. **Ownership class** = closed enum `public_domain` | `user_owned` | `licensed` | `unknown`. **Use policy** = closed enum `training_allowed` | `reference_only` | `no_training` (exactly one). **Allowed uses** = derived closed set from use policy (`train`, `reference_analyze`, `eval_holdout`). **Verification status** = `unverified` | `attested` | `verified` | `disputed`. **Rights digest** = SHA-256 prefix over canonical entry fields used in manifests. **Model/data provenance manifest** = `model.data.provenance.manifest.v1` listing training sources + rights digests + disposition — distinct from `content.provenance.manifest.v1` (derivation honesty/C2PA) and `dataset.manifest.v1` (corpus counts). **Legacy status** = today's `DatasetProvenance.status` (`verified_redistributable` | `user_owned` | `public_domain` | `restricted` | `unknown`); ship-1 keeps accepting sidecars and maps into the two-axis model. **Unsafe train pollution** = existing dataset flag `eligibility.allow_unsafe_train_pollution` only; personal and MT ship-1 have no equivalent. Content provenance remains derivation lineage; rights registry is permission-to-use. **Resolve** = Part K order (registry → request attestation → refuse).

## Approach Evaluation (locked)

### Part A — Where the centralized registry lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only enrich `DatasetProvenance` sidecars under `DATASET_ROOT`** | Minimal | Personal/reference live in studio SQLite; no durable project rights; MT/personal still ad-hoc | **Reject** as sole store |
| **B. Only extend personal composer request `rights` dict** | Already gates personal train | Ephemeral; dataset/MT/reference untouched; no registry | **Reject** as sole store |
| **C. Pure policy module + SQLite `rights_registry_entries` for studio sources + dataset-version `rights/index.jsonl` (same DTO)** | One schema; studio + offline corpus; fail-closed gates | Migration + dual persistence surfaces | **Accepted** |
| **D. Reuse `content_provenance_records` as rights store** | One table | Wrong semantics (derivation ≠ permission); trust_class/C2PA pollution | **Reject** |

### Part B — Category model

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Flat single enum mixing ownership and use (`public_domain`…`reference_only`…)** | Matches user list literally | `licensed`+`reference_only` and `user_owned`+`no_training` inexpressible | **Reject** |
| **B. Two-axis: ownership_class × use_policy; expose category aliases for docs/UI** | Expresses all listed names; clear train vs reference | Slightly more schema | **Accepted** |
| **C. Free-form SPDX + capability bits only** | Flexible | Loses closed fail-closed tests; harder HTTP/CLI contracts | **Reject** for ship-1 |

### Part C — Relation to existing `DatasetProvenance`

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Delete legacy statuses / break sidecars** | One vocabulary | Breaks fixtures, docs, personal schemas | **Reject** |
| **B. Keep sidecar ingest; map at normalize into `rights.registry.entry.v1`; `compute_train_eligible` delegates to shared evaluator; personal schemas accept legacy status **or** registry entry id** | Backward compatible | Dual read path during transition | **Accepted** |
| **C. Leave dataset untouched; only gate personal/reference** | Smaller | Acceptance needs dataset train exclusion | **Reject** |

### Part D — Train gate hardness

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Soft-skip unknown into train with WARNING** | Convenient | Violates “must not silently enter” | **Reject** |
| **B. Hard-refuse train include for `unknown` / `reference_only` / `no_training`; dataset keeps explicit unsafe override (ERROR log); personal + MT have no override** | Matches acceptance | Operators must fix sidecars | **Accepted** |
| **C. Require C2PA / cryptographic proof for train** | Strong | Out of scope; conflicts with fake CI | **Reject** |

### Part E — Reference gate

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reference ignores registry** | Zero FE churn | `no_training`/`unknown` still condition generate; acceptance needs reference_only allowed | **Reject** |
| **B. `evaluate_rights_use(reference_analyze)` allows `training_allowed` and `reference_only`; refuses `no_training` and `unknown`; resolve via Part K (registry → request attestation → refuse); never default `training_allowed`** | Matches acceptance; Develop keeps working with attestation | Analyze request gains optional `rights` | **Accepted** |
| **C. Allow all references; only filter train** | Simpler | Unknown material soft-conditions without disclosure | **Reject** |
| **D. Registry-only refuse when row missing (no request attestation)** | Forces upsert | Breaks every existing Develop reference until operators upsert | **Reject** |

### Part F — Model/data provenance manifests

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reuse `content.provenance.manifest.v1`** | One export | Wrong honesty/C2PA semantics; not train-source lists | **Reject** |
| **B. New `model.data.provenance.manifest.v1` emitted at dataset train-split finalize, personal snapshot, and MT train; dataset also writes hard-locked siblings `rights/index.jsonl` + `rights/manifest.json` (not embedded in `dataset.manifest.v1` digests)** | Clear training-use audit; bit-stable dataset_version_id | Extra files | **Accepted** |
| **C. Docs-only manifests** | Cheap | Not enforceable / not testable | **Reject** |

### Part G — UI placement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New top-level Rights tab** | Visible | Tab sprawl; operators mostly CLI for corpus | **Reject** for ship-1 |
| **B. Extend existing Personal Composer attestation UI + Develop reference refuse code; dataset CLI inspect** | Beside the actions that need it; reuses `personalComposerForm.js` | Limited browse UX | **Accepted** |
| **C. No UI** | Fastest | Personal refuse opaque | **Reject** |
| **D. Replace Personal Composer form with registry-only editor** | Pure registry | Loses attested train path for projects without rows | **Reject** |

### Part H — Locked mapping (legacy → registry)

| Legacy `DatasetProvenance.status` | ownership_class | use_policy | verification_status |
|-----------------------------------|-----------------|------------|---------------------|
| `public_domain` | `public_domain` | `training_allowed` | `verified` if URL/reference present else `unverified` |
| `user_owned` + attested | `user_owned` | `training_allowed` | `attested` |
| `user_owned` without attested | `user_owned` | `no_training` | `unverified` |
| `verified_redistributable` | `licensed` | `training_allowed` | `verified` |
| `restricted` | `licensed` if license present else `unknown` | `no_training` | `verified` or `unverified` |
| `unknown` | `unknown` | `no_training` | `unverified` |

Explicit sidecar/registry fields may set `use_policy=reference_only` (overrides the default train mapping even when legacy `status` is train-shaped). Alias strings `training_allowed` / `reference_only` / `no_training` accepted on ingest as use_policy when ownership is separately supplied or defaulted to `unknown`. Sidecar field name lock: `use_policy` in `*.meta.json` / `*.meta.yaml` alongside `provenance_status`.

### Part I — Locked formulas

1. **Train eligible:**  
   `use_policy == "training_allowed"` ∧ `(ownership_class != "user_owned" ∨ verification_status ∈ {attested, verified})` ∧ `verification_status != "disputed"` ∧ `"train" ∈ allowed_uses`.
2. **Reference eligible:**  
   `use_policy ∈ {training_allowed, reference_only}` ∧ `verification_status != "disputed"` ∧ `"reference_analyze" ∈ allowed_uses`.
3. **Allowed uses projection:**  
   - `training_allowed` → `{train, reference_analyze, eval_holdout}`  
   - `reference_only` → `{reference_analyze}`  
   - `no_training` → `{eval_holdout}` only when dataset `include_non_trainable_in_eval` is true; else `∅` for product gates (eval holdout remains dataset-internal).
4. **Training manifest exclusion:** any source with `use_policy != training_allowed` (or failing formula 1) **must not** appear in train source lists of `model.data.provenance.manifest.v1`, dataset `splits/train.*`, personal snapshot project ids, or MT train path collection.
5. **Fail-closed after resolve:** after Part K resolution yields an entry (or synthetic unknown), evaluate; if still missing both registry and request attestation for a studio project/revision train or reference selection ⇒ treat as `ownership_class=unknown`, `use_policy=no_training`, refuse the gated use. Dataset inventory ingest may still store unknown/non-trainable items without refusing the whole build.
6. **Idempotent registry key (studio):** `(source_kind, source_id)` unique; CAS via `entry_version` / `updated_at`.
7. **ai_agents forbid:** extend architecture test with `from app.services.rights_governance_store` and `from app.rights_governance_settings` (and settings tokens if store-like).
8. **No `/ready` coupling:** registry status on `GET /rights-governance/status` only — never `/health` or `/ready`.
9. **Secrets:** refuse prompt/key/event fields via `persistence_secret_guard` on persist and manifests.
10. **Acceptance bar (Task 6b):** store-upsert `reference_only` entry for project P → reference analyze succeeds → personal train / dataset train manifest / MT train inputs exclude P; `unknown` personal train returns `personal_rights_refused` (or `rights_train_refused` mapped equivalently). HTTP router not required for this bar.

### Part J — Hard-fail vs content-provenance soft-fail (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Soft-skip rights refuse (WARN) and continue train/reference** | Matches provenance capture | Violates “must not silently enter training” | **Reject** |
| **B. Rights evaluate/gates hard-fail with structured codes; primary train/reference/dataset-split/MT-train ops abort on refuse. Content-provenance capture stays soft-fail and unrelated** | Honest permission semantics; documents divergence from `content_provenance_capture` | Callers must handle codes | **Accepted** |

Document in `docs/rights-governance.md` and `docs/content-provenance.md` cross-links: derivation soft-fail ≠ permission hard-fail.

### Part K — Locked resolution and write-back (added by `/aif-improve`)

1. **Resolve entry for a studio source:**  
   (a) load registry row by `(source_kind, source_id)` if present → use it;  
   (b) else if request carries legacy `DatasetProvenance` / rights attestation → `map_legacy_dataset_provenance` (Part H), including explicit `use_policy` override;  
   (c) else synthetic `unknown` + `no_training` → refuse gated use.  
   **Never** invent `training_allowed` on missing data. Registry wins over contradictory request claims for the same source.
2. **Personal-train write-back:** on successful snapshot (after all projects pass evaluate for `train`), upsert studio registry entries for those projects (`training_allowed` + mapped ownership/verification). List/open/evaluate-adapter never upsert. Project create never upserts.
3. **Sidecar:** `use_policy` optional on meta; when set, overrides Part H default use_policy from `provenance_status`.
4. **Dataset siblings (hard-lock):** version dir always writes `rights/index.jsonl` and `rights/manifest.json` (`model.data.provenance.manifest.v1` with `manifest_kind=dataset_train_split`). Do **not** fold rights bytes into `dataset.manifest.v1` digests (keep `dataset_version_id` bit-stable).
5. **Eligibility rewrite:** `compute_train_eligible` and `assert_train_split_policy` must call shared evaluator on mapped entries — not only `status ∈ TRAIN_ELIGIBLE_STATUSES` — so `reference_only` never enters train even if legacy status is `verified_redistributable` / `public_domain` / `user_owned`.
6. **Module boundary:** `rights_governance_policy.py` is pure (no FastAPI/SQLite/`dataset.pipeline`). Mapping may import `DatasetProvenance` from `dataset.schemas`. `dataset.provenance` may import policy + mapping; must not create import cycles with `dataset.pipeline`.
7. **Acceptance upsert:** Task 6b uses `rights_governance_store` helpers against temp `PROJECT_DB_PATH` — not `PUT /rights-governance/...`.
8. **Ship-1 non-goals:** neural/mix render rights; stamping `rights.entry_id` onto `content.provenance` parents; new Rights tab; `/ready` flags.
9. **GC:** `project_store.delete_project` deletes registry rows for that project on the same connection (mirror `delete_records_for_project` pattern).
10. **License required:** `ownership_class=licensed` ∧ `use_policy=training_allowed` requires `license` or `license_spdx`; `licensed` + `reference_only` may omit license.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Dataset provenance | `DatasetProvenance` + `compute_train_eligible` + `assert_train_split_policy` | Map into registry; **rewrite** to delegate eligibility |
| Dataset sidecars | `*.meta.json` license / status | Add optional `use_policy`; ingest → rights index |
| Personal rights | `personal_composer_rights.evaluate_project_rights` + FE `personalComposerForm.js` | Shared evaluator; registry prefer; write-back on success; extend FE |
| Personal schemas | `rights: dict[str, DatasetProvenance]` | Accept legacy **or** registry; analyze gains optional rights |
| Reference features | Analyze any composition; no rights field on `ReferenceFeatureAnalyzeRequest` | Part K resolve + gate before analyze/condition |
| MT train | Loads `splits/train.jsonl` or examples/; checkpoint card has `dataset_version_id` | Verify rights index; refuse poisoned train; emit model/data manifest sibling |
| Content provenance | Soft-fail derivation DAG | Complementary; do not store allowed uses; document hard-fail divergence |
| Secret / storage policy | `persistence_secret_guard`, `reject_storage_root` | Apply to rights JSON; no separate rights root required in ship-1 (SQLite + DATASET_ROOT CLI only) |
| Project delete GC | `delete_records_for_project` for provenance | Same hook for rights rows |
| Alembic | Latest `20261003_0029_content_provenance.py` | Next revision `20261003_0030` |
| Architecture forbid | `test_ai_agents_architecture.py` | Extend for rights store + settings |
| Fixtures | `backend/tests/fixtures/dataset/sources/*` | Add `reference_only` meta + policy tests |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| `rights.registry.entry.v1` + use/ownership enums | No centralized DTO |
| Pure `evaluate_rights_use` + Part K resolve helper | Logic split; no request/registry order |
| SQLite registry + migration + project-delete GC | No durable studio rights table |
| Dataset rights siblings + eligibility rewrite | Status-only eligibility today; no `reference_only` |
| Reference analyze optional `rights` + gate | None; missing row must not break attested path |
| Personal train write-back upsert | Request-only today; registry never fills |
| MT train re-check + model/data manifest | Trusts split blindly if pollution slipped |
| Policy/acceptance tests (store upsert) | Need `reference_only` vector |
| Docs + hard-fail vs provenance soft-fail | New `docs/rights-governance.md` |

### Coupling risks to avoid

1. Inventing `composition.v5` or storing tracks/events/notes/PCM on rights docs.
2. Replacing or deleting `content.provenance.*` or claiming C2PA from rights entries.
3. Silently including `unknown` / `reference_only` / `no_training` in train splits or personal snapshots.
4. Blocking reference analysis for legitimate `reference_only` sources **or** for attested `training_allowed` sources that simply lack a registry row yet.
5. `ai_agents/` importing `rights_governance_store` / settings.
6. Writing registry JSON into `DATASET_ROOT` from FastAPI request handlers (dataset CLI may write dataset-version rights index under `DATASET_ROOT` only).
7. Using `PROJECT_DB_PATH` as a dataset root or rights export root (`reject_storage_root`).
8. Editing `ROADMAP.md` during implementation.
9. Logging full license file bodies, prompts, event arrays, or API keys — SPDX/id + entry id prefixes only.
10. Making rights registry required for ordinary edit/playback/`/ready`.
11. Treating content-provenance trust_class as a training permit.
12. Auto-upserting `training_allowed` on project create or Profiles open (fail closed; operator/user must attest; write-back only after successful personal train).
13. Soft-skipping rights refuse the way provenance capture soft-skips (forbidden for train/reference gates).
14. Leaving `compute_train_eligible` status-only so `use_policy=reference_only` is ignored when legacy status is train-shaped.
15. Import cycles between `dataset.pipeline` and rights modules.
16. Gating neural/mix WAV download on rights in ship-1.

## Schema sketch (normative for implementers)

### `rights.registry.entry.v1`

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | `"rights.registry.entry.v1"` | Required |
| `entry_id` | `rights_[0-9a-f]{16}` | Stable id |
| `source_kind` | closed: `dataset_item`, `dataset_source`, `project`, `composition_revision`, `external_file`, `personal_snapshot_project` | |
| `source_id` | string | Project id / item id / path digest id |
| `source_fingerprint_prefix` | optional hex ≤40 | |
| `ownership_class` | closed enum | Part B |
| `use_policy` | closed enum | Part B |
| `allowed_uses` | list derived/validated | Part I.3 |
| `license` / `license_spdx` | optional strings | Required when `ownership_class=licensed` and `use_policy=training_allowed` (Part K.10) |
| `attribution` | optional string (cap length) | |
| `source_url` / `source_reference` | optional | Required for `public_domain` train-eligible |
| `restrictions` | list of closed codes | e.g. `no_redistribution`, `attribution_required`, `share_alike`, `evaluation_only` |
| `verification_status` | closed enum | |
| `legacy_status` | optional legacy literal | Round-trip aid |
| `rights_digest` | hex | Canonical digest |
| `created_at` / `updated_at` | ISO UTC | |
| `entry_version` | int ≥ 1 | CAS |

### `model.data.provenance.manifest.v1`

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | `"model.data.provenance.manifest.v1"` | |
| `manifest_kind` | `dataset_train_split` \| `personal_adapter_train` \| `music_transformer_train` | |
| `subject_id` | dataset_version_id / adapter_id / experiment_id | |
| `sources` | list of `{entry_id, source_kind, source_id, ownership_class, use_policy, rights_digest_prefix}` | **Train-eligible only** |
| `excluded_source_counts_by_use_policy` | counts | Includes `reference_only` / `unknown` / `no_training` tallies |
| `created_at` | ISO UTC | |
| `manifest_digest_prefix` | hex | |

Forbidden keys: same note/PCM/prompt set as content provenance / personal composer.

### Analyze request addition

`ReferenceFeatureAnalyzeRequest` gains optional `rights: DatasetProvenance | null` (or compact twin) used only when no registry row exists for the project/revision source. Conditioning bindings that name `project_id`s use the same resolve helper.

## HTTP / CLI surface (ship-1)

| Surface | Behavior |
|---------|----------|
| `PUT/GET /rights-governance/entries/{source_kind}/{source_id}` | Upsert/get studio entry (CAS); never writes notes |
| `POST /rights-governance/evaluate` | Preview `{use, entry or legacy}` → allow/refuse + code |
| `GET /rights-governance/status` | Caps only — **not** on `/ready` |
| Dataset CLI `build` | Writes `rights/index.jsonl` + `rights/manifest.json`; enforces train exclusion |
| Personal `POST …/train` | Part K resolve → evaluate train → snapshot → registry write-back |
| MT `train` | Load dataset rights siblings; refuse if train paths include non-eligible; write model/data manifest beside checkpoint |
| Reference analyze / condition | Part K resolve → evaluate `reference_analyze` |

## Commit Plan
- **Commit 1** (after tasks 1–2): `feat: add rights registry schemas store and policy evaluator`
- **Commit 2** (after tasks 3–4b): `feat: gate dataset and personal training through rights registry`
- **Commit 3** (after tasks 5–6b): `feat: gate reference and MT train; emit model data provenance manifests`
- **Commit 4** (after tasks 7–8): `feat: rights governance HTTP status and personal UI refuse copy`
- **Commit 5** (after task 9): `docs: describe dataset rights governance registry`

## Tasks

### Phase 1: Schemas, policy, store
- [x] Task 1: Add rights registry + model/data provenance DTOs and mapping
- [x] Task 2: Pure evaluator, Part K resolve, SQLite registry store + migration + GC

### Phase 2: Dataset + personal gates
- [x] Task 3: Integrate dataset ingest/split with rights siblings + eligibility rewrite
- [x] Task 4: Integrate personal composer train with registry resolution
- [x] Task 4b: Write-back registry upsert after successful personal train

### Phase 3: Reference + MT + manifests
- [x] Task 5: Gate reference features / conditioning (registry or request attestation)
- [x] Task 6: Gate Music Transformer train + emit `model.data.provenance.manifest.v1`
- [x] Task 6b: Acceptance — `reference_only` reference OK, train manifests exclude

### Phase 4: HTTP, UI, docs
- [x] Task 7: HTTP evaluate/upsert/status + env + architecture forbid
- [x] Task 8: Extend Personal Composer / Develop refuse surfacing
- [x] Task 9: Document rights governance and training exclusion

## Tasks (detail)

### Task 1: Add rights registry + model/data provenance DTOs and mapping

Add Pydantic models in `backend/app/rights_governance_schemas.py` for `rights.registry.entry.v1`, evaluate request/result, resolve inputs, `model.data.provenance.manifest.v1`, `RightsGovernanceError` (`code`, `message`, `http_status`) with mapper. Closed enums for ownership_class, use_policy, verification_status, source_kind, restriction codes. Implement `map_legacy_dataset_provenance(DatasetProvenance, *, use_policy_override=...)` (Part H + sidecar override) and `project_allowed_uses(use_policy)`. Reject forbidden note/secret keys. Schema tests: `reference_only` yields allowed_uses=`{reference_analyze}` only; `licensed`+`training_allowed` requires license/SPDX; `licensed`+`reference_only` may omit license; unknown use refused; manifest refuses embedded `events`.

LOGGING: DEBUG schema rejection with model name + code only. Levels follow `LOG_LEVEL`.

Files: `backend/app/rights_governance_schemas.py`, `backend/tests/test_rights_governance_schemas.py`.

### Task 2: Pure evaluator, Part K resolve, SQLite registry store + migration + GC

Implement pure `evaluate_rights_use(entry, use)` and `resolve_rights_entry(source_kind, source_id, *, registry_row, request_rights)` in `backend/app/services/rights_governance_policy.py` (no FastAPI/SQLite/`dataset.pipeline`) encoding Part I + Part K.1. Alembic `20261003_0030_rights_registry.py` revising `20261003_0029`. Store in `backend/app/services/rights_governance_store.py`: upsert by `(source_kind, source_id)`, get, list-by-prefix, `delete_entries_for_project` for GC. Hook `project_store.delete_project` on the same connection beside content-provenance GC. Settings in `backend/app/rights_governance_settings.py` (caps only; no separate FS root in ship-1). Unit tests: train/reference matrix; `reference_only` reference allow / train refuse; disputed refuses both; CAS conflict; project delete GCs rows; resolve order registry-wins / attestation-fallback / missing→refuse. Migration upgrade/downgrade from `20261003_0029`.

LOGGING: INFO upsert with entry_id, source_kind, ownership_class, use_policy (no attribution body at INFO). WARNING refuse with code + source_id prefix. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Integrate dataset ingest/split with rights siblings + eligibility rewrite

During dataset normalize/build, map sidecars via Part H + optional `use_policy` into entries; **always** write `rights/index.jsonl` and `rights/manifest.json` under the dataset version dir (`DATASET_ROOT` only). Rewrite `compute_train_eligible` / `assert_train_split_policy` to call shared evaluator on mapped entries (Part K.5) — `reference_only` items are inventory-eligible but **never** land in `splits/train.*` unless unsafe override (existing flag, ERROR log), even if legacy `status` is train-shaped. Emit `model.data.provenance.manifest.v1` (`manifest_kind=dataset_train_split`) with train-eligible sources only + `excluded_source_counts_by_use_policy`. Keep `dataset.manifest.v1` digests bit-stable (siblings only). Update fixtures with a `reference_only` source. Tests in `backend/tests/test_dataset_rights_governance.py` + extend pipeline/acceptance.

LOGGING: INFO eligibility summary by use_policy; ERROR on unsafe override. Never log raw MIDI/MusicXML. Levels follow `LOG_LEVEL`.

Depends on Task 2. Files: `backend/app/dataset/provenance.py`, `sources.py`, `split.py`, `pipeline.py`, new `dataset/rights_index.py` as needed, fixtures, tests.

### Task 4: Integrate personal composer train with registry resolution

In `personal_composer_rights.py` / service: for each selected project_id, Part K resolve (registry row if present, else request `rights` legacy dict). Refuse train when evaluator fails (`personal_rights_refused` or `rights_train_refused` with stable HTTP mapping). On successful path preparation, stamp personal training manifest with registry entry ids / digests + emit `model.data.provenance.manifest.v1` (`personal_adapter_train`) under `PERSONAL_COMPOSER_ROOT` next to the job (not DATASET_ROOT). Unknown / reference_only / no_training never snapshot. Tests: registry `reference_only` refuses train; request `user_owned` attested still works when no row; registry row wins over contradictory request train claim.

LOGGING: WARNING refuse with project_id + code; INFO accept with ownership_class/use_policy only. Levels follow `LOG_LEVEL`.

Depends on Task 2. Files: `backend/app/services/personal_composer_rights.py`, `personal_composer_service.py`, `personal_composer_schemas.py` (minimal), tests.

### Task 4b: Write-back registry upsert after successful personal train

After a successful personal train snapshot (all selected projects already passed train evaluate), upsert studio `rights.registry.entry.v1` rows for each accepted project (`source_kind=project`, `use_policy=training_allowed`, ownership/verification from the resolved entry). Idempotent CAS-friendly upsert. Never upsert on list/open/stop/resume/evaluate/delete or Profiles panel mount. Unit test: train → restart → GET store entry present with `training_allowed`; failed/refused train leaves no new `training_allowed` row for that project.

LOGGING: INFO write-back with entry_id + project_id + use_policy. Levels follow `LOG_LEVEL`.

Depends on Task 4. Files: `personal_composer_service.py`, store helpers, tests.

### Task 5: Gate reference features / conditioning (registry or request attestation)

Extend `ReferenceFeatureAnalyzeRequest` with optional `rights` attestation. Before `analyze_reference_features` returns for `kind=project|revision`, Part K resolve + `evaluate_rights_use(..., reference_analyze)`. Allow `reference_only` and `training_allowed`. Refuse `unknown` / `no_training` / unresolved missing attestation with `rights_reference_refused` (422). Wire the same resolve helper into reference-conditioning assembly when bindings name project ids. Inline refs: require explicit rights attestation or refuse. Tests: `reference_only` analyze OK (registry); attested `user_owned` analyze OK with **no** registry row; `unknown` / missing attestation refused; train still excluded (cross-assert with Task 4 fixtures).

LOGGING: WARNING refuse with code + project_id; DEBUG allow with use_policy. Never log melodies. Levels follow `LOG_LEVEL`.

Depends on Task 2. Files: `backend/app/reference_feature_schemas.py`, `services/reference_feature_analyze.py`, `reference_conditioning_policy.py` (or thin wrapper), routers error map, tests.

### Task 6: Gate Music Transformer train + emit `model.data.provenance.manifest.v1`

When MT train loads a `dataset_dir`, read `rights/index.jsonl` / `rights/manifest.json`; verify every train path's parent item is train-eligible; hard-fail with `rights_train_refused` / dataset error if not. Write `model.data.provenance.manifest.v1` (`music_transformer_train`) beside checkpoint card (experiment dir) as sibling (do not require checkpoint card schema churn). Tests with temp dataset containing `reference_only` item: train split build excludes it; if a poisoned train jsonl is crafted, MT train refuses.

LOGGING: INFO train rights verification counts; ERROR refuse with code. Levels follow `LOG_LEVEL`.

Depends on Task 3. Files: `backend/app/music_transformer/data.py`, `train.py`, tests.

### Task 6b: Acceptance — `reference_only` reference OK, train manifests exclude

`backend/tests/test_rights_governance_acceptance.py` (store upsert — **not** Task 7 HTTP):

1. Create project P with V2 fixture; **store-upsert** registry entry `use_policy=reference_only`, `ownership_class=licensed` (license optional per Part K.10).
2. Reference analyze (service or HTTP) with project source → **success** (no request rights needed when registry row exists).
3. Personal train including P → **refused**.
4. Dataset build with sidecar `use_policy: reference_only` → item inventory present; `splits/train.*` excludes it; `rights/manifest.json` sources exclude it; `excluded_source_counts_by_use_policy["reference_only"] >= 1`.
5. MT train against that dataset version succeeds on remaining train-eligible only (fake/tiny / CPU fixtures already used in CI).
6. Missing registry + missing attestation → reference refuse; `unknown` / `no_training` → reference refuse + train refuse.
7. Attested request rights without registry → reference allow for `training_allowed` / `user_owned` attested (Part K.1b).

No real LLM/torch weights beyond existing fake personal / CPU MT smoke patterns.

LOGGING: reuse service logs; assertions on codes/counts only.

Depends on Tasks 2–6 (not Task 7).

### Task 7: HTTP evaluate/upsert/status + env + architecture forbid

Add `backend/app/routers/rights_governance.py`, include in `main.py`. Routes per surface table. Update `.env.example` with caps block (no `/ready` keys). Extend `test_ai_agents_architecture.py` forbid list for store + settings. Status **not** on `/ready` or `/health`. API tests: upsert → get → evaluate train/reference; CAS conflict; notes-in-body 400; delete project GCs; reference-refuse code path (may use Task 5 fixtures).

LOGGING: INFO route method, source_kind, status, duration_ms. Levels follow `LOG_LEVEL`.

Depends on Task 2. Reference-refuse API cases additionally need Task 5.

### Task 8: Extend Personal Composer / Develop refuse surfacing

Extend existing `PersonalComposerPanel.jsx` + `personalComposerForm.js` (do not replace attestation UX): hydrate GET registry entry when present; show resolved ownership_class / use_policy / verification; treat `reference_only` as client non-trainable in `selectionEligible`; surface train refuse `code` from API. Develop / reference UI: surface `rights_reference_refused` from API `code`. Opening panels does not upsert. FE unit tests for form eligibility + message mapping helper.

LOGGING: no `console.log` of full entries. Depends on Task 7 (GET client); client eligibility helpers can land with Task 4 fixtures earlier if needed.

### Task 9: Document rights governance and training exclusion

Add `docs/rights-governance.md`: terminology (two-axis), Part H mapping, Part I formulas, Part J hard-fail vs content-provenance soft-fail, Part K resolve/write-back, dataset sibling layout, personal/MT/reference integration, model/data provenance manifests vs content provenance vs dataset manifest, unsafe override honesty, acceptance vector, UI entry points, ship-1 non-goals (render rights, provenance parent stamp). Link from `AGENTS.md`, `README.md`, `docs/CODEBASE_MAP.md`, `docs/datasets.md`, `docs/personal-symbolic-composer.md`, `docs/music-transformer.md`, `docs/reference-features.md`, `docs/content-provenance.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`. State `ai_agents/` does not import the rights store. Do **not** edit `ROADMAP.md`.

LOGGING: none.

Depends on Tasks 7 and 8.

## Implementation Notes

- Prefer extending `routers/` + `services/` + `dataset/` / `music_transformer/` over growing `main.py`.
- Shared evaluator + Part K resolve are the single source of truth; wrappers only adapt I/O.
- Content provenance capture stays soft-fail; rights gates hard-fail — document both ways.
- Stamping `rights.entry_id` onto provenance parents stays **out of ship-1**.
- Dataset version digests stay bit-stable via sibling rights files only.
- Fail closed after resolve; do not auto-mark new projects `training_allowed`.
- No separate `RIGHTS_ROOT`; studio rows live in `PROJECT_DB_PATH`; corpus index under dataset version dirs only.

## Verification Gate (for `/aif-verify`)

1. Schemas reject note/secret fields; Part H mapping + `use_policy` override covered; `reference_only` allowed_uses exclude `train`.
2. Evaluator + Part K resolve matrix: registry wins; attestation fallback; missing→refuse; never default `training_allowed`.
3. Store CAS + project GC + migration 0029→0030.
4. Dataset: `reference_only` excluded from train split and rights/model-data train manifest even when legacy status is train-shaped; siblings written; unsafe override ERROR path still unit-tested; `dataset_version_id` bit-stable.
5. Personal: registry `reference_only` / `unknown` refuse train; attested `user_owned` still trains; Task 4b write-back persists `training_allowed` after success only.
6. Reference: `reference_only` analyze OK; attested without registry OK; `unknown` / missing attestation refused.
7. MT: refuses poisoned train inputs; writes model/data provenance manifest sibling.
8. Task 6b acceptance passes on fake/CPU fixtures using **store** upsert.
9. Architecture forbid includes rights store + settings; status absent from `/ready`.
10. FE extends existing Personal Composer form; `reference_only` client non-trainable.
11. Docs exist; Part J divergence documented; ship-1 non-goals stated.
12. No `ROADMAP.md` edit; no `composition.v5`; FastAPI paths do not write `DATASET_ROOT` registry files; no neural render rights gate.
