# Implementation Plan: V5 Content Provenance Tracking

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Focused provenance surfaces inside existing **Neural** (render/stem), **Versions**, and **Mix Assist** panels — not a new workspace tab. Ordered read path: select/expand a completed render/stem/mix revision → show derivation chain → Download provenance JSON (+ optional C2PA sibling when enabled). Opening any panel never auto-signs, never fan-out-fetches every job, never rewrites WAV/V2, and never starts a render. No piano-roll redesign, no Universe-tab replacement, no mandatory Content Credentials at core boot
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Soft-fail capture must not abort primary writers (unlike dependency cycle hard-fail). Honesty formula: `cryptographic = attached ∧ ¬fake_mode ∧ any(record.trust_class == c2pa_signed)`; fake credentials never upgrade provenance records. Import capture via SPA `completeImport` → `commit_revision` (`operation_type=import`); no durable `import_session`. Personal adapter parent from `personal:pcomp_*` / `composer_model_id`. Acceptance: fake neural with pinned `source_revision_id` (no stub AI ops). `.env.example` + `reject_storage_root` for credentials root; status on `/content-provenance/status` only (not `/ready`). UI fetches chain for selected leaf only; Mix Assist needs no new WAV download button. Downloads never insert records
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). First unchecked ROADMAP items (performance conductor / spatial) are orthogonal and already planned/shipped; this plan documents a new franchise milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`
- Scope: durable non-playable `content.provenance.record.v1` nodes + parent links spanning composition revisions, AI regions, imports, reference conditioning, personal-model outputs, renders, stems, and transformations; exportable `content.provenance.manifest.v1`; optional C2PA Content Credentials evaluation for supported egress without making C2PA mandatory; honesty that internal metadata is not cryptographic provenance. Tamper/error tests + docs. Never invent `composition.v5`. Never store note events / PCM / prompts on provenance docs. Never write `DATASET_ROOT`. `ai_agents/` must not import provenance stores
- Predecessors: `.ai-factory/plans/v5-derived-material-dependency-graph.md` (complete), `.ai-factory/plans/v5-soundtrack-asset-pack-generation.md` (complete), V3 `generation.provenance.v1` + V4 `audio.roundtrip.provenance.v1` (shipped), neural renders/stems, personal composer, reference conditioning

## Roadmap Linkage
Milestone: "V5 Content provenance tracking"
Rationale: Fragments (`generation.provenance.v1`, round-trip session fields, dependency `rendered_from` edges) exist in silos, but a completed render cannot yet show one exportable derivation chain back through Composition and major AI/human operations with honest trust labels. First unchecked ROADMAP items (performance / spatial) are orthogonal. This plan documents the new milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`.

## Goal

Make **content provenance** a first-class V5 sidecar so human, AI, imported-reference, and rendering operations leave a durable, inspectable derivation graph — without claiming cryptographic Content Credentials where only internal metadata exists.

Track provenance for:

- composition revisions
- AI-generated regions
- imported material
- reference conditioning
- personal model outputs
- renders
- stems
- transformations (arrangement / development / motif / universe reuse / mix plan)

Represent on each record:

- source artifact (kind + id + fingerprint prefix)
- operation (closed enum)
- model / model version (when AI)
- user action (when human/import)
- timestamp
- parent artifact ids (DAG, not only a linear list)

Ship:

1. Non-playable **`content.provenance.record.v1`** rows (append-only inserts with soft-fail capture) + assemble **`content.provenance.manifest.v1`** / **`content.provenance.chain.v1`** on read.
2. Capture hooks at existing writers (history commit / apply-as-branch, import via `operation_type=import`, recovery bind, generate/edit apply, reference-conditioned commit, personal-composer-tagged generate, neural render/stem complete, stem rerender, mix-plan `apply_mix`, arrangement/development/motif/universe reuse / `commit_durable_revision` pack paths) without becoming a second note writer and without aborting those writers on provenance soft errors.
3. **Exportable manifest** JSON for a leaf asset (render, stem, mix revision, or composition revision) with trust class labels.
4. **C2PA evaluation path** behind `CONTENT_CREDENTIALS_ENABLED` (default off): optional sibling credential for supported WAV egress; failure never blocks unsigned download; never claim crypto when only Mukit-internal records exist or when fake mode is on.
5. Focused UI: derivation chain + Download provenance on Neural / Mix Assist (selected leaf only); Versions row links into revision chain.
6. Tamper/error tests (mutated parent fingerprint, missing parent, secret-field refusal, unsigned-vs-claimed-signed honesty) + `docs/content-provenance.md`.

Acceptance: With `NEURAL_AUDIO_FAKE_MODE=1`, create a project (history bootstrap → `human_edit` with `user_action=project_create` or a follow-up human/AI commit), enqueue a mix render with `project_id` + **`source_revision_id`**, assert sync `complete`, then `GET` chain/manifest for `neural_render/{id}` walks to that revision, exports JSON, and keeps `honesty.cryptographic=false` with credentials off. No stub AI ops. No real LLM/neural weights or signing keys.

```text
human edit / import / AI generate / reference / personal adapter
        │ capture content.provenance.record.v1 (soft-fail)
        ▼
composition revision (+ generation.provenance.v1 nest remains)
        │ parent_ids
        ▼
transform ops (arrangement / develop / motif / reuse / mix plan)
        │ parent_ids
        ▼
neural render / stem / mix revision  (source_revision_id pinned)
        │ assemble
        ▼
content.provenance.manifest.v1  (+ optional C2PA sibling when enabled)
musical.dependency.edge.v1 ── complementary musical stale graph (unchanged)
```

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**. **Provenance record** = durable `content.provenance.record.v1` node (ids, operation, models, parents, timestamps, trust class) — never events/PCM/prompts. **Provenance chain** = `content.provenance.chain.v1` assembled walk from a leaf toward roots. **Provenance manifest** = exportable `content.provenance.manifest.v1` (records + honesty block + digests). **Trust class** = closed enum: `mukit_internal` (default for all SQLite provenance records, including under fake credentials), `c2pa_signed` (only on a provenance record after **real** non-fake credential attach for that leaf), `unavailable` (parent missing). **Artifact kind** = closed enum for endpoints (`composition_revision`, `neural_render`, `neural_stem`, `neural_stem_set`, `mix_plan_revision`, `import_session` display-only/optional, `audio_recovery_bind`, `agent_artifact`, `personal_adapter`, `composer_profile`, `reference_project`, `asset_pack`, `external_file`). Ship-1 import nodes use `composition_revision` + `import_*` operation (no durable import session table). **Operation** = closed enum covering human/AI/import/render/transform (see schema table). **C2PA / Content Credentials** = optional egress projection; not required for core studio operation. **Do not claim cryptographic provenance** where only `mukit_internal` metadata exists. Opening Neural/Versions/Mix Assist never auto-signs. Dependency-graph `rendered_from` stays the musical stale edge; provenance records are the richer AI/human lineage.

## Approach Evaluation (locked)

### Part A — Where unified provenance lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only enrich `generation.provenance.v1` inside revision `summary_json`** | Minimal schema | Misses renders/stems/imports/human edits as first-class nodes; no leaf→root export for WAV | **Reject** as sole store |
| **B. Reuse only `musical.dependency.edge.v1`** | Already has `rendered_from` | Edges are musical stale semantics; no model/version/user-action/trust class; wrong module for Content Credentials | **Reject** as sole store (keep complementary) |
| **C. Durable `content_provenance_records` SQLite table + assemble chain/manifest on read** | Cross-artifact DAG; export without contaminating V2 or WAV bytes | Migration + capture at writers | **Accepted** |
| **D. Ephemeral session-only provenance** | Tiny | Fails durable chain after restart; acceptance needs export | **Reject** |

### Part B — Relation to existing fragments

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Delete `generation.provenance.v1` / round-trip fragments** | One schema | Breaks V3 reproduce + Versions UI + Bind hydrate | **Reject** |
| **B. Keep fragments; mirror key fields into `content.provenance.record.v1` at capture; manifest may nest compact `generation.provenance.v1` when leaf is a revision** | Backward compatible; Versions keep working | Dual write at commit | **Accepted** |
| **C. Generate chain only by parsing summary_json + neural columns at read time** | No capture | Human edits/imports/personal adapters incomplete; fragile | **Reject** as sole path |

### Part C — Parent linking model

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Strict linear linked list (`previous_id` only)** | Simple | Multi-parent (reference + profile + base revision) loses fidelity | **Reject** |
| **B. DAG via `parent_record_ids[]` (cap ≤ 8) + artifact refs** | Matches “parent artifacts”; supports multi-source | Need cycle refusal on insert | **Accepted** |
| **C. Full RDF / W3C PROV store** | Standards-aligned | Overkill; conflicts with SQLite/raw-sql style | **Reject** for ship-1 |

### Part D — C2PA / Content Credentials

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require C2PA for every export/download** | Strong claims | Needs keys/toolchain; breaks CI/fake; violates “not mandatory for core” | **Reject** |
| **B. Evaluate + optional attach behind `CONTENT_CREDENTIALS_ENABLED` (default off); JSON manifest always; unsigned WAV stays canonical job bytes; credential as sibling when enabled; honesty block on manifest** | Matches acceptance + honesty | Soft dependency / fake signer for tests | **Accepted** |
| **C. Docs-only C2PA evaluation, no code path** | Cheap | User asked for integration evaluation with supported media path | **Reject** as sole deliverable |

### Part E — UI placement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New top-level Provenance tab** | Strong branding | Tab sprawl; provenance is inspect/export beside assets | **Reject** for ship-1 |
| **B. Compact chain in Neural panel (render/stem) + Mix Assist revision + Versions provenance link; Download JSON** | Beside the leaf asset; reuses DependencyGraph layout ideas | Panels grow | **Accepted** |
| **C. Universe dependency graph only** | Already shipped | Wrong trust/model semantics; no manifest download | **Reject** as sole UI |

### Part F — Capture strategy

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Background scanner reconstructing history** | No writer churn | Incomplete; races; invents ops | **Reject** |
| **B. Synchronous capture beside existing writers; skip+WARN on unusable fingerprint / cycle / cap; never raise into primary txn (unlike `musical_dependency_capture` cycle hard-fail); hard-fail only `PersistenceSecretError` on explicit provenance APIs** | Matches plan honesty; commits survive | Many call sites | **Accepted** |
| **C. Client-only stamps** | Easy FE | Not restart-safe; forgeable; no server export | **Reject** |

### Part G — Cryptographic honesty

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Label all manifests “signed” / “verified” when JSON exists** | Marketing | Lies; forbidden by goal | **Reject** |
| **B. Every record and manifest carries `trust_class`; UI copy maps `mukit_internal` → “Studio metadata (not cryptographically signed)”; `honesty.cryptographic` only via locked formula (Part K)** | Honest | Slightly more schema | **Accepted** |

### Part H — Soft-fail vs dependency hard-fail (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Mirror dependency: raise on cycle and roll back commit/bind/render** | Uniform | Ordinary edits fail when provenance parents cycle | **Reject** |
| **B. Capture wrapper catches store cycle/cap/`ContentProvenanceError`; WARNING + skip; primary op commits; unit-test forced cycle leaves revision/`complete` job intact** | Matches coupling risk #12 | Diverges from dependency semantics (document in docs) | **Accepted** |

### Part I — Import / transcription durability (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New durable `import_session` table + file sha256 on every import** | Rich | Out of acceptance; gold-plate | **Reject** for ship-1 |
| **B. Import: SPA `completeImport` → `commit_revision` with `RevisionOperationType.import` → map format to `import_midi` / `import_musicxml`. Transcription: stamp `transcription_apply` only when durable `user_action=audio_transcribe` (or equivalent) is present on commit; else skip. Recovery: `bind_audio_recovery_job` → `recovery_bind`** | Matches real writers | Transcription may be sparse until SPA stamps action | **Accepted** |
| **C. Invent backend import-apply route that does not exist** | Cleaner narrative | Wrong site | **Reject** |

### Part J — Acceptance neural pin (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Compose-only enqueue (null `source_revision_id`) + stub revision provenance** | Matches some existing tests | Fails “chain through source Composition”; invents ops | **Reject** |
| **B. Acceptance requires `source_revision_id`; parent via existing revision record or `parent_artifacts` only — never invent stub AI ops** | Honest gate | Tests must create/commit project first | **Accepted** |

### Part K — Locked formulas (added by `/aif-improve`)

1. **Honesty:**  
   `honesty.cryptographic = honesty.c2pa.attached and not honesty.c2pa.fake_mode and any(r.trust_class == "c2pa_signed" for r in records)`.
2. Under `CONTENT_CREDENTIALS_FAKE`, provenance **records** stay `mukit_internal`; only `content.credentials.status.v1` may carry `fake_mode: true` / status `fake`.
3. Real (non-fake) successful attach may upgrade the **leaf** record to `c2pa_signed`.
4. UI “Cryptographically signed” only when `honesty.cryptographic === true`.
5. Idempotent upsert key: `(artifact_kind, artifact_id, operation)` — no unique on `created_at`.
6. Manifest/chain **downloads never insert** provenance records (`user_action` must not include write-on-download).
7. Minimum acceptance bar (Task 6b): project revision → fake neural with `source_revision_id` → chain/manifest ≥2 ops → `honesty.cryptographic is false`.
8. Credentials flags surface only on `GET /content-provenance/status` — never `/health` or `/ready`.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Generation provenance | `generation.provenance.v1` via `generation_provenance.py`; nested in revision `summary_json` / `AiProvenance` | Compact AI stage nest on revision-rooted chains |
| Round-trip | `audio.roundtrip.provenance.v1` session/Bind fragment | Seed recovery→render parent links; still not the durable graph |
| Dependency edges | `musical.dependency.edge.v1` + `rendered_from` / capture at commit/bind/neural/reuse | Complementary stale graph; **hard-fail on cycle** — provenance must soft-fail instead |
| Neural jobs | `source_revision_id`, fingerprint, model, seed, sha256_prefix; capture beside `capture_rendered_edge` | Leaf + parent revision (pin revision for acceptance) |
| Mix plan | `apply_mix` in `mix_plan/pipeline.py`; no dependency capture | Transform / mix revision nodes |
| Import | Ephemeral `ImportReport`; durable via SPA `completeImport` → `operation_type=import` | Map to `import_*` ops on that revision |
| Transcription | Preview API; SPA local apply → often later `manual-checkpoint` | Optional `user_action=audio_transcribe` stamp |
| Recovery bind | `bind_audio_recovery_job` + `capture_transcribed_edge` | `recovery_bind` record |
| Reference / profile / personal | `generation_parameters`; personal as `personal:pcomp_*` in `stages[].model_id` / `composer_model_id` | Parent artifacts (no dedicated personal_adapter key) |
| Asset pack / universe | `commit_durable_revision` / `reuse_theme` bypass `commit_revision` | Must hook those paths too |
| Secret guard | `persistence_secret_guard` | Refuse prompts/keys on records/manifests |
| Storage roots | `reject_storage_root` / mix_plan pattern | `CONTENT_CREDENTIALS_ROOT` |
| Agent boundary | `test_ai_agents_architecture.py` forbid list | Extend for provenance store |
| Alembic | Latest `20261003_0028_asset_packs.py` | Next revision `20261003_0029` |
| UI patterns | Neural per-job Download + `downloadBlob`; Mix Assist nested in Neural (no mix WAV FE download); Versions provenance text | Chain + provenance download |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Unified record / chain / manifest DTOs | No `content.provenance.*.v1` |
| SQLite store + migration | No derivation DAG table |
| Soft-fail capture wrapper | Must not mirror dependency hard-fail |
| Capture at writers | Include `commit_durable_revision` / `reuse_theme` / `apply_mix` / import history path |
| Manifest assemble + HTTP | No `GET …/provenance` / download |
| Trust-class honesty + fake formula | Part K |
| Optional C2PA path + env + root policy | Zero C2PA today; no `.env.example` yet |
| Acceptance vector with `source_revision_id` | Existing fake enqueue often null |
| Tamper/error tests | Mutated fingerprint / missing parent / secret refusal / claim-signed-when-unsigned |
| Docs | New `docs/content-provenance.md` |

### Coupling risks to avoid

1. Inventing `composition.v5` or storing `tracks`/`events`/`notes`/PCM on provenance docs.
2. Claiming `c2pa_signed` / “verified Content Credentials” when only `mukit_internal` rows exist or `fake_mode` is true.
3. Making C2PA or signing keys required for `/ready`, generate, or unsigned WAV download.
4. Rewriting canonical neural job WAV bytes as the only downloadable form when credentials attach (prefer sibling / optional wrap; job sha256 remains the unsigned artifact digest).
5. `ai_agents/` importing `content_provenance_store` or settings.
6. Replacing or deleting `generation.provenance.v1` / dependency edges.
7. Auto-signing on panel open or project open; fan-out GET chain for all jobs on panel mount.
8. Logging prompts, event arrays, full manifests at INFO, API keys, or raw audio bytes.
9. Writing `DATASET_ROOT` or using it as a provenance root / credentials root.
10. Editing `ROADMAP.md` during implementation.
11. Remote LLM / real MusicGen / real C2PA private keys in unit tests — use fake modes.
12. Turning provenance soft capture failure into a hard fail of unrelated commits (except explicit provenance write APIs and secret-guard refusals).
13. Cycle creation via parent_ids (refuse at store; soft-skip at capture).
14. Embedding full `summary_json` or composition snapshots into records.
15. Inventing stub AI ops or null-`source_revision_id` acceptance.
16. Write-on-download provenance records.
17. Adding Mix Assist FE WAV download as a provenance dependency.
18. Durable `import_session` table in ship-1.

## Scope And Decisions

### In scope
- `content.provenance.record.v1`, `content.provenance.chain.v1`, `content.provenance.manifest.v1`, error/trust enums.
- Alembic `content_provenance_records` (+ optional `content_provenance_credentials` sidecar meta for C2PA siblings).
- Store + cycle/cap checks + project-delete GC (in-txn like dependency edges preferred).
- Soft-fail capture helpers + hooks at locked writers (Part F/H/I).
- Assemble chain/manifest; HTTP GET for leaf kinds; manifest download (read-only).
- Settings: `CONTENT_PROVENANCE_*` caps; `CONTENT_CREDENTIALS_ENABLED` / `CONTENT_CREDENTIALS_FAKE` / `CONTENT_CREDENTIALS_ROOT` (default off; root via `reject_storage_root`).
- `.env.example` commented block for those vars.
- Optional C2PA evaluation module (fake signer in tests; real library optional soft import).
- Neural + Mix Assist + Versions UI chain + download (selected leaf).
- Acceptance Task 6b + tamper/error tests + architecture forbid updates.
- Docs: `docs/content-provenance.md` + AGENTS/README/DESCRIPTION/ARCHITECTURE pointers.

### Out of scope
- New Provenance workspace tab.
- Mandatory C2PA for core operation or Docker default profile.
- Hardware attestation / TSA / public transparency logs.
- Rewriting MIDI/MusicXML binary metadata as the primary store (JSON manifest is source of truth).
- Deleting dependency-graph or generation.provenance APIs.
- Film-score / Ardour as unique provenance engines (may appear as operation labels if those writers call capture later; not required for acceptance).
- Marketplace license NFTs.
- Editing `ROADMAP.md`.
- Storing playable notes or training corpora under provenance root.
- Durable `import_session` table / mandatory file sha256 on every import revision.
- New Mix Assist FE button for mix revision WAV download (backend route may exist; not required).

### Architecture decisions (locked)

**1. Documents / schemas**

New module `backend/app/content_provenance_schemas.py`. `extra=forbid`. Scan `FORBIDDEN_NOTE_KEYS` (`events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`, `wav`, `pcm`). Secret-guard on persist and manifest export.

| Document | Role |
|----------|------|
| `content.provenance.record.v1` | One DAG node |
| `content.provenance.chain.v1` | Assembled walk for UI (+ optional `diagnostics[]` codes only) |
| `content.provenance.manifest.v1` | Exportable envelope for a leaf |
| `content.credentials.status.v1` | Optional C2PA attempt result (non-playable; may set `fake_mode`) |

**Record fields (locked):**

| Field | Rule |
|-------|------|
| `schema` | `content.provenance.record.v1` |
| `record_id` | ULID/uuid string |
| `project_id` | Owning project when applicable; null only for pack-level/global kinds with explicit allowlist |
| `artifact_kind` | Closed enum (see terminology) |
| `artifact_id` | Stable id (revision_id, render_id, …) |
| `artifact_fingerprint_prefix` | ≤40 hex/chars; never full secret payloads |
| `operation` | Closed: `human_edit`, `ai_generate`, `ai_edit_region`, `ai_arrange`, `ai_develop`, `ai_motif_apply`, `universe_theme_reuse`, `import_midi`, `import_musicxml`, `transcription_apply`, `recovery_bind`, `reference_condition`, `personal_adapter_generate`, `neural_render`, `neural_stem`, `neural_stem_rerender`, `mix_plan_apply`, `asset_pack_generate`, `asset_pack_slot_regenerate` (`performance_realize` / `spatial_compile` optional no-op if no capture in ship-1) |
| `model_id` / `model_version` / `runtime` | Optional; null for pure human/import |
| `user_action` | Optional ≤64: `apply`, `commit`, `import`, `bind`, `project_create`, `audio_transcribe`, … — **not** `download_manifest` |
| `actor_kind` | `human` \| `ai` \| `import` \| `system` |
| `parent_record_ids` | list≤8 existing record ids |
| `parent_artifacts` | list≤8 `{kind,id,fingerprint_prefix}` for display when parent record GC’d |
| `source_generation_provenance` | Optional compact nest only when operation is AI and fragment already secret-safe |
| `trust_class` | `mukit_internal` \| `c2pa_signed` \| `unavailable` (records stay `mukit_internal` under fake credentials) |
| `created_at` | UTC ISO-8601 |

**Operation mapping (locked):**

| History / site signal | Provenance `operation` |
|----------------------|------------------------|
| `RevisionOperationType` human / `project-create` / `manual-checkpoint` (no AI) | `human_edit` (+ `user_action` when known) |
| `import` + MIDI/MusicXML format from import summary | `import_midi` / `import_musicxml` |
| `generate-apply` / generate AI | `ai_generate` |
| `ai-region-edit-apply` | `ai_edit_region` |
| `arrangement-apply` | `ai_arrange` |
| `development-apply` + `ai.operation=vary_section` | `ai_develop` |
| `creative-motif-apply` | `ai_motif_apply` |
| `musical-universe-theme-apply` / `reuse_theme` | `universe_theme_reuse` |
| `asset-pack-generate` / `asset-pack-slot-regenerate` | matching pack ops |
| Reference in `generation_parameters` | parent `reference_project` (+ keep AI op) |
| `personal:pcomp_*` / `composer_model_id` | parent `personal_adapter` (AI op or `personal_adapter_generate`) |
| Neural mix / stem / rerender complete | `neural_render` / `neural_stem` / `neural_stem_rerender` |
| `apply_mix` | `mix_plan_apply` |
| `bind_audio_recovery_job` | `recovery_bind` |

**Manifest honesty (locked — Part K):** see formula above. Schema validator: refuse `honesty.cryptographic=true` when `c2pa.fake_mode` or not `c2pa.attached`.

**2. Persistence**

Alembic `20261003_0029_content_provenance.py` revising `20261003_0028`:

```sql
content_provenance_records (
  record_id TEXT PRIMARY KEY,
  project_id TEXT,
  artifact_kind TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  artifact_fingerprint_prefix TEXT,
  operation TEXT NOT NULL,
  actor_kind TEXT NOT NULL,
  model_id TEXT,
  model_version TEXT,
  runtime TEXT,
  user_action TEXT,
  parent_record_ids_json TEXT NOT NULL,
  parent_artifacts_json TEXT NOT NULL,
  source_generation_provenance_json TEXT,
  trust_class TEXT NOT NULL DEFAULT 'mukit_internal',
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL
)
-- indexes: (project_id, created_at), (artifact_kind, artifact_id)
-- unique upsert helper: (artifact_kind, artifact_id, operation) for idempotent neural/stem complete
```

Optional `content_provenance_credentials` as previously specified. `CONTENT_CREDENTIALS_ROOT` default: directory beside `PROJECT_DB_PATH` / `content_credentials`; `reject_storage_root` refuses `DATASET_ROOT` and `PROJECT_DB_PATH`. Lazy create on first credentials POST when enabled.

**3. Capture sites (locked)**

| Writer | Operation(s) | Parents |
|--------|--------------|---------|
| `commit_revision` / `apply_as_branch_command` | map table above | prior head record if any + reference/profile/personal parents |
| Import via same (`operation_type=import`) | `import_midi` / `import_musicxml` | prior head if any |
| Transcription commit with `user_action=audio_transcribe` | `transcription_apply` | prior head |
| `bind_audio_recovery_job` | `recovery_bind` | source audio artifact |
| Neural mix/stem/rerender complete (beside `capture_rendered_edge`) | neural ops | source revision record or `parent_artifacts` only — **no stub AI** |
| `apply_mix` | `mix_plan_apply` | stem set / prior mix revision |
| `reuse_theme` / motif durable apply | `universe_theme_reuse` / `ai_motif_apply` | theme / occurrence parents |
| Pack `commit_durable_revision` / slot generate | pack ops + resulting revision | pack artifact + revision |

Soft-fail wrapper (Task 2b) around all of the above.

**4. HTTP surface**

Router `backend/app/routers/content_provenance.py` registered from `main.py` (non-worker include_router pattern):

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/content-provenance/projects/{project_id}/artifacts/{kind}/{id}/chain` | `content.provenance.chain.v1` |
| `GET` | `/content-provenance/projects/{project_id}/artifacts/{kind}/{id}/manifest` | JSON manifest |
| `GET` | `/content-provenance/projects/{project_id}/artifacts/{kind}/{id}/manifest/download` | attachment; **read-only** |
| `POST` | `/content-provenance/projects/{project_id}/artifacts/{kind}/{id}/credentials` | Attempt C2PA when enabled; disabled → `content_credentials_disabled` |
| `GET` | `/content-provenance/status` | `provenance_enabled: true`, `credentials_enabled`, `credentials_fake` |

Collaboration: GET uses `enforce_current(..., "read")` when collaboration on. No `ai_agents/` imports. Not mounted into `/ready`.

**5. Optional C2PA module**

`backend/app/services/content_credentials.py` + `content_credentials_settings.py` + Part K honesty. Soft import; supported media ship-1: neural WAV/FLAC + mix-plan WAV. MIDI/MusicXML manifest-only.

**6. UI**

- `frontend/src/api/contentProvenanceApi.js` (axios blob + `downloadBlob` / `filenameFromContentDisposition`)
- `contentProvenanceChain.js` helpers + honesty copy
- `NeuralAudioRenderPanel.jsx`: chain + Download provenance on **selected/expanded complete** job/stem row beside existing Download
- `MixAssistPanel.jsx`: beside head revision UI; manifest download only (no new WAV download button)
- `ProjectVersionsPanel.jsx`: per-selected-revision control; no auto-fetch on tab open beyond revision list
- Opening panel never POSTs credentials

**7. Logging**

- INFO: capture recorded (`record_id`, `operation`, `artifact_kind`, id prefixes); soft-skip code; manifest assembled; credentials attempt
- DEBUG: parent id lists, schema rejections
- WARNING: cycle/cap soft-skip, `provenance_fingerprint_unusable`, credentials failure
- Never INFO full `body_json`, prompts, event arrays, private key paths, or audio bytes

**8. Agent boundary**

Extend `test_ai_agents_architecture.py` forbid: `content_provenance_store`, `content_credentials` (store/settings), `CONTENT_CREDENTIALS_ROOT` env reads inside `ai_agents/`.

**9. Env (`.env.example`)**

```
# CONTENT_PROVENANCE_MAX_RECORDS_PER_PROJECT=4000
# CONTENT_PROVENANCE_MANIFEST_MAX_RECORDS=64
# CONTENT_PROVENANCE_CHAIN_MAX_DEPTH=32
# Provenance capture is always on (no CONTENT_PROVENANCE_ENABLED).
# CONTENT_CREDENTIALS_ENABLED=0
# CONTENT_CREDENTIALS_FAKE=0
# CONTENT_CREDENTIALS_ROOT=   # default: <dir of PROJECT_DB_PATH>/content_credentials
```

## Commit Plan
- **Commit 1** (after tasks 1–2b): `feat: add content provenance records and soft-fail store`
- **Commit 2** (after tasks 3–5): `feat: capture provenance across revisions imports and renders`
- **Commit 3** (after tasks 6–7): `feat: export provenance manifests and optional credentials path`
- **Commit 4** (after tasks 8–9): `feat: show provenance chains in neural and versions UI`
- **Commit 5** (after task 10): `docs: describe content provenance and C2PA honesty`

## Tasks

### Phase 1: Schemas, migration, store
- [x] Task 1: Add content provenance documents and trust enums
- [x] Task 2: Persist records, refuse cycles/caps, project-delete GC
- [x] Task 2b: Soft-fail capture wrapper (never abort primary writers)

### Phase 2: Capture at writers
- [x] Task 3: Capture revision / AI / human / reference / personal parents
- [x] Task 4: Capture import, transcription stamp, and recovery bind
- [x] Task 5: Capture neural render, stems, mix-plan, pack, and transform ops

### Phase 3: Manifest, HTTP, optional C2PA
- [x] Task 6: Assemble chain and exportable manifest with honesty block
- [x] Task 6b: Acceptance vector — fake neural with `source_revision_id`
- [x] Task 7: HTTP routes, env, credentials status, storage-root, tamper/error API tests

### Phase 4: UI + docs
- [x] Task 8: Neural / Mix Assist derivation chain + download (selected leaf)
- [x] Task 9: Versions provenance entry point
- [x] Task 10: Document content provenance and C2PA evaluation

## Tasks (detail)

### Task 1: Add content provenance documents and trust enums

Add Pydantic models in `backend/app/content_provenance_schemas.py` for record, chain, manifest, credentials status, and `ContentProvenanceError` (`code`, `message`, `http_status`) with mapper. Closed enums for `artifact_kind`, `operation`, `actor_kind`, `trust_class`. Reject forbidden note keys and secret fields. Enforce Part K honesty validator: refuse `honesty.cryptographic=true` when `c2pa.fake_mode` or not `c2pa.attached`. Accept a minimal AI-generate record with one parent artifact and a manifest whose `honesty.cryptographic` is false. Schema tests: `c2pa_signed` on a **record** under fake-mode assemble must not yield `cryptographic=true`; credential **status** docs may include `fake_mode: true`.

`backend/tests/test_content_provenance_schemas.py` covers accept/reject vectors including: embedded `events`, parent_record_ids length > 8, unknown operation, honesty formula cases.

LOGGING: DEBUG schema rejection with model name + code only. Levels follow `LOG_LEVEL`.

Files: `backend/app/content_provenance_schemas.py`, `backend/tests/test_content_provenance_schemas.py`.

### Task 2: Persist records, refuse cycles/caps, project-delete GC

Alembic `20261003_0029_content_provenance.py` revising `20261003_0028`. Implement `backend/app/services/content_provenance_store.py`: insert, get by id, list by artifact, walk parents for chain (`CONTENT_PROVENANCE_CHAIN_MAX_DEPTH` default 32), cycle detection on insert, per-project cap (default 4000), upsert-by `(artifact_kind, artifact_id, operation)`, delete-by-project for GC from `project_store.delete_project` on the same connection. Settings in `backend/app/content_provenance_settings.py`.

`backend/tests/test_content_provenance_store.py`: insert chain of 3, refuse self-parent and diamond cycle, hit cap, project delete removes rows, idempotent neural upsert keeps one row. `backend/tests/test_content_provenance_migration.py` upgrades from `20261003_0028` and downgrades.

LOGGING: INFO insert with `record_id`, `operation`, `artifact_kind`, fingerprint prefix. WARNING cycle/cap with code only. Do not log `body_json`. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 2b: Soft-fail capture wrapper (never abort primary writers)

Add `record_provenance_safe(...)` (or equivalent) in `content_provenance_capture.py` that catches store cycle/cap/`ContentProvenanceError`, logs WARNING with code only, and returns `None` — never raises into the caller. Only re-raise `PersistenceSecretError` when validating an explicit provenance API body. Unit test: force cycle on insert during a `commit_revision` hook → revision still committed and head advances; forced cycle during fake neural complete → job stays `complete`.

LOGGING: WARNING soft-skip with code + artifact kind/id prefixes. INFO never claims success on skip. Levels follow `LOG_LEVEL`.

Depends on Task 2. Files: `backend/app/services/content_provenance_capture.py`, `backend/tests/test_content_provenance_soft_fail.py`.

### Task 3: Capture revision / AI / human / reference / personal parents

Hook `commit_revision` and `apply_as_branch_command` via Task 2b wrapper. Map ops from `RevisionOperationType` + `ai.operation` (locked table). Nest secret-safe `generation.provenance.v1` when present. Parent `reference_project` / `composer_profile` from `generation_parameters`; parent `personal_adapter` when `composer_model_id` or any stage `model_id` matches `personal:pcomp_*` — never copy melodies.

`backend/tests/test_content_provenance_capture.py`: human commit → `human_edit`; fake generate → `ai_generate`; reference-conditioned → reference parent; personal model id → personal_adapter parent; note keys absent from `body_json`. Update `test_ai_agents_architecture.py` forbid list.

LOGGING: INFO capture with operation + record_id + project_id. WARNING soft skip. Never log prompts. Levels follow `LOG_LEVEL`.

Depends on Task 2b.

### Task 4: Capture import, transcription stamp, and recovery bind

Import: on `commit_revision` / apply-as-branch when `operation_type=import`, map MIDI vs MusicXML from import summary / source format fields into `import_midi` / `import_musicxml` (no backend import-apply invent; no `import_session` table). Transcription: emit `transcription_apply` only when durable commit carries `user_action=audio_transcribe` (or agreed SPA stamp); otherwise skip (do not mislabel every `manual-checkpoint`). Recovery: hook `bind_audio_recovery_job` → `recovery_bind` with source sha256 prefix; soft-fail wrapper.

Tests: MIDI import fixture via history import path → import record; recovery bind → `recovery_bind`; restart still walks chain; transcription without stamp does not invent `transcription_apply`.

LOGGING: INFO per operation with asset id prefixes. Basename at DEBUG only. Levels follow `LOG_LEVEL`.

Depends on Task 3.

### Task 5: Capture neural render, stems, mix-plan, pack, and transform ops

Hook neural mix/stem/rerender completion beside `capture_rendered_edge` (`neural_audio_render.py`, `neural_audio_stems.py`). Parent = provenance record for `source_revision_id` when present; else `parent_artifacts` only — **never invent stub AI/human revision records**. Hook `apply_mix` (`mix_plan/pipeline.py`), arrangement/development durable apply (via history hooks + op map), `reuse_theme`, and pack `commit_durable_revision` / slot generate-regen. Soft-fail wrapper everywhere. Assert dependency `rendered_from` still written.

Tests: fake neural with pinned `source_revision_id` → `neural_render` child; stem rerender with superseded parent; `apply_mix` → `mix_plan_apply`; `arrangement-apply` → `ai_arrange`; pack clear/reuse path produces pack op without aborting slot.

LOGGING: INFO with job/revision ids and operation. WARNING soft skip. Levels follow `LOG_LEVEL`.

Depends on Task 4.

### Task 6: Assemble chain and exportable manifest with honesty block

Implement `backend/app/services/content_provenance_manifest.py`: walk parents → chain; canonicalize → manifest with Part K honesty + `manifest_digest_prefix`. Cap records. Missing parent → stub `trust_class=unavailable`. Optional `diagnostics[]` with codes only (`parent_fingerprint_mismatch`, …). Fake credentials path must keep `honesty.cryptographic is false`.

`backend/tests/test_content_provenance_manifest.py`: assemble from human/AI/render fixtures; digest stability; tamper parent fingerprint → diagnostic; fake_mode cryptographic false.

LOGGING: INFO assemble with leaf + count + cryptographic flag. DEBUG digest prefixes. Levels follow `LOG_LEVEL`.

Depends on Task 5.

### Task 6b: Acceptance vector — fake neural with `source_revision_id`

`backend/tests/test_content_provenance_acceptance.py` (or extend API/capture suite):

1. `create_project` with V2 fixture (bootstrap revision).
2. Optional `commit_revision` (`human_edit` or fake AI) so chain has a clear human/AI op.
3. `enqueue_neural_audio_render(..., project_id=..., source_revision_id=<rev>, model_id=FAKE…)` with `NEURAL_AUDIO_FAKE_MODE=1`.
4. Assert job `complete` and `source_revision_id` set.
5. GET chain + manifest for `neural_render/{id}`: walks to that `composition_revision`, ≥2 records, no forbidden keys, `honesty.cryptographic is false`, credentials off.
6. Manifest download 200. Fail the vector if enqueue used composition-only null revision.

LOGGING: reuse route/capture logs; test asserts codes only. Depends on Tasks 5 and 6.

### Task 7: HTTP routes, env, credentials status, storage-root, tamper/error API tests

Add router + `GET /content-provenance/status`. Manifest download Content-Disposition; downloads insert **zero** records. Credentials POST: disabled → `content_credentials_disabled`; fake mode → status `fake_mode: true`, records stay `mukit_internal`, `honesty.cryptographic` false. Wire `content_credentials_settings.py` with `reject_storage_root` tests (refuse `DATASET_ROOT` / `PROJECT_DB_PATH`). Update `.env.example` with Part 9 block. Do **not** add flags to `/ready` or `/health`. Soft-import failure reason `c2pa_library_unavailable` without installing real c2pa.

`backend/tests/test_content_provenance_api.py` + storage-root tests: chain/manifest/download/status/credentials disabled/fake; unknown artifact 404; notes-in-body 400; claim-signed-when-unsigned refused.

LOGGING: INFO route method, path ids, status, duration_ms. WARNING credentials failures with reason_code. Levels follow `LOG_LEVEL`.

Depends on Tasks 2, 2b, and 6 (6b may share fixtures).

### Task 8: Neural / Mix Assist derivation chain + download (selected leaf)

Add `contentProvenanceApi.js`, pure `contentProvenanceChain.js` (+ `node --test`), mount compact chain + “Download provenance” in `NeuralAudioRenderPanel.jsx` and `MixAssistPanel.jsx`. Fetch chain only for the **selected/expanded** complete job/stem or Mix Assist head revision — not on panel mount for all jobs. Use `downloadBlob`. Do not add Mix Assist WAV download. Honesty copy: “Cryptographically signed” only when `honesty.cryptographic === true`. No credentials POST from panel open.

`frontend/src/utils/contentProvenanceChain.test.js` asserts `mukit_internal` label and refuses “Cryptographically signed” when cryptographic is false even if a string contains “c2pa”.

LOGGING: panel does not `console.log` full manifests. API failures surface server `code`.

Depends on Task 7.

### Task 9: Versions provenance entry point

In `ProjectVersionsPanel.jsx`, add per-**selected** revision control to load chain/manifest for `composition_revision`. Keep existing `formatRevisionProvenanceSummary` one-liner. No auto-fetch on tab open beyond existing revision list.

FE unit test: helper builds artifact path for revision id.

LOGGING: none beyond existing. Depends on Task 8.

### Task 10: Document content provenance and C2PA evaluation

Add `docs/content-provenance.md`: terminology, record/chain/manifest, capture sites (including soft-fail vs dependency hard-fail), Part K honesty, optional C2PA (env, supported media, fake mode), relation to `generation.provenance.v1` and dependency graph, import/transcription honesty, UI entry points, acceptance vector. Link from `AGENTS.md`, `README.md`, `docs/CODEBASE_MAP.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`. State `ai_agents/` does not import provenance stores. Do **not** edit `ROADMAP.md`.

LOGGING: none.

Depends on Tasks 8 and 9.

## Implementation Notes

- Prefer extending `routers/` + `services/`; keep `main.py` to include_router only.
- Reuse `assert_no_secret_fields` / `assert_payload_has_no_secret_values` on every persist and manifest.
- Manifest JSON is the interoperability source of truth; C2PA is an egress projection (same honesty as MusicXML projection vs V2).
- Soft-stale neural UI remains fingerprint-based; provenance diagnostics may mirror mismatch without deleting WAV.
- Provenance soft-fail is intentional divergence from dependency edge cycle rollback — document it.
- Asset-pack acceptance remains symbolic; provenance acceptance uses fake neural with pinned revision.

## Verification Gate (for `/aif-verify`)

1. Schemas reject note keys/secret fields and enforce Part K honesty.
2. Store refuses cycles and enforces caps; project delete GCs rows; upsert-by triplet works.
3. Soft-fail: forced cycle does not abort `commit_revision` or neural `complete`.
4. Capture suite: human, AI, import history path, reference, personal model id, neural, stem, `apply_mix`, reuse/pack paths.
5. Task 6b acceptance: fake render with `source_revision_id` walks to Composition revision; `honesty.cryptographic is false`.
6. Credentials disabled does not block audio download; fake credentials set `fake_mode` and do not advertise real cryptography; records stay `mukit_internal`.
7. Tamper/error tests: missing artifact, parent mismatch diagnostic, notes-in-body refusal, signed-claim-when-unsigned refused; download inserts zero records.
8. `reject_storage_root` for credentials root; `.env.example` updated; flags absent from `/ready`.
9. Architecture forbid includes provenance store.
10. FE honesty copy + selected-leaf fetch tests pass.
11. Docs exist and state C2PA is optional; soft-fail vs dependency documented.
12. No `ROADMAP.md` edit; no `composition.v5`; no `DATASET_ROOT` writes.
