# Implementation Plan: Explicit User-Controlled Composer Profiles

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-22
Improved: 2026-09-22 (`/aif-improve` — merge contract, migration parent, CAS, promote UX, env caps, task deps)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **durable, explicitly visible Composer Profiles** — named abstract musical preference documents that optionally condition generation at a user-selected strength — **without** hidden behavioral inference as authority, without copying melodies into profiles, without writing private projects into `DATASET_ROOT`, and without letting profile soft prefs override project prompt / hard `GenerationConstraints`
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v3-style-semantic-embeddings-conditioning.md` (**shipped** — handcrafted features, reference conditioning, privacy vs dataset)
  - `.ai-factory/plans/v2-deterministic-composition-v2-analysis.md` (**shipped** — melody/harmony/density/repetition/tension sidecars)
  - `.ai-factory/plans/v1-enforce-generation-prompt-constraints.md` (**shipped** — hard vs soft constraint split)
  - `.ai-factory/plans/v1-feature-local-project-composition-persistence.md` (**shipped** — SQLite `PROJECT_DB_PATH` + Alembic)
  - `.ai-factory/plans/v4-controlled-critique-revision-loops.md` (session preview patterns; not a dependency — profiles are durable prefs, not revision loops)

## Roadmap Linkage
Milestone: "V4 composer profiles"
Rationale: Embeddings shipped **per-request musical reference** conditioning; this follow-on closes the product gap for **persistent, user-authored preference profiles** (multi-project derived + manual) with explicit strength Off/Light/Normal/Strong, while reusing analysis/embedding abstract stats and keeping the same privacy boundary (never silent `DATASET_ROOT` ingest; never artist≡style ids). It is its own checked V4 milestone, separate from the V3 embeddings line.

## Goal

Allow users to develop **persistent musical preferences** that can influence future generation without overriding project-specific instructions.

Ship:

1. Explicit, visible, editable Composer Profiles (multiple named profiles).
2. Manual create + edit + reset/delete + preview + **promote derived → explicit**.
3. Derive profile from **selected projects** (source project ids recorded; abstract stats only).
4. Distinguish **explicit preferences** vs **derived statistics** in the stored document and UI.
5. Generation-time **profile strength**: Off · Light · Normal · Strong.
6. Priority lock: **project prompt / hard constraints always win** over profile soft defaults.
7. Profile comparison + export/import when straightforward.
8. Persistence in `PROJECT_DB_PATH` (local app DB), tests, UI, documentation.

```text
Selected projects (read-only V2)
  → derive abstract musical stats (analysis + embeddings features)
  → composer.profile.v1 { explicit, derived, source_projects[] }
  → user edits / promote derived→explicit / resets
  → optional: export/import JSON envelope
        ↓
Generate request { prompt, profile_id?, profile_strength }
  → resolve profile → strength-scaled soft fragment (additive)
  → NEVER mutate LLMPromptParameters hard/sent fields
  → inject soft fragment into llm_music_generator prompt builders
  → provenance: composer_profile_id + profile_strength only
  → never embed melodies / event arrays / analysis reports as profile body
```

**Terminology lock:**
- **Composer Profile** = durable named preference document (`composer.profile.v1`), not a playable score, not `composition.analysis.v1`, not an embedding vector dump, not a training dataset row.
- **Explicit preference** = user-set or user-confirmed (including promoted-from-derived) field; authoritative for that field when present inside the profile.
- **Derived statistic** = auto-computed aggregate from source projects; advisory until promoted/edited; never treated as hidden authority over explicit fields or the project prompt.
- **Profile strength** = soft-conditioning gain only (`off|light|normal|strong`); Off = no profile injection.
- **Project prompt** = current generate `LLMPromptParameters` + hard `GenerationConstraints`; **always request-owned** for key, meter, tempo, instruments, sections, duration, instructions, genre, mood, complexity — profile never rewrites these DTO fields.
- Profiles live in local SQLite under `PROJECT_DB_PATH` — **never** auto-exported to `DATASET_ROOT`.

## Approach Evaluation (locked)

### Part A — Where profiles live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Per-project JSON blob on `projects` row** | Tiny | Not multi-named; couples prefs to one score; hard to reuse across projects | **Reject** |
| **B. New SQLite tables in `PROJECT_DB_PATH` + Alembic** | Matches monolith persistence; restart-safe; secret-guardable; independent of active project | New migration | **Accepted** |
| **C. Browser `localStorage` only** | Easy FE | No shared API conditioning; lost across devices/browsers; untrusted merge | **Reject** as sole store |
| **D. `DATASET_ROOT` / corpus item** | Stats tooling exists | Violates privacy AC; conflates training with user prefs | **Reject** |
| **E. Agent artifact workspace rows** | Typed envelopes | Workspace is promote-on-Apply / revision-linked; wrong lifecycle for durable prefs | **Reject** |

### Part B — Preference document shape

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Flat freeform prompt string only** | Simple | Opaque; cannot distinguish derived vs explicit; hard to edit fields | **Insufficient** |
| **B. Versioned `composer.profile.v1` with typed preference keys + provenance** | Editable; inspectable; testable merge | Schema work | **Accepted** |
| **C. Store full `composition.analysis.v1` / embeddings per source** | Rich | Violates “abstract prefs not melodies”; huge payloads; analysis not prefs | **Reject** |
| **D. Copy motif note sequences into profile** | Catchy | Copies melodies; privacy/IP risk | **Reject** |

**Locked schema sketch** (`composer.profile.v1`):

```text
{
  schema: "composer.profile.v1",
  id, name, created_at, updated_at,
  source_projects: [{ project_id, revision_id?, fingerprint_prefix, included_at }],
  explicit: { ...PreferenceFields },   # user-authored / promoted
  derived:  { ...PreferenceFields, stats_meta },  # advisory aggregates
  notes?: string  # optional user note; never secrets
}
```

**Preference field families** (bounded enums / histograms / scalars — no event arrays):

| Family | Examples | Derive from |
|--------|----------|-------------|
| Melodic range | midi_min/max/mean bands, range_semitones | analysis melody + embeddings RANGE dims |
| Interval tendencies | coarse interval hist (-12..+12 bins or collapsed) | embeddings INTERVAL_BINS |
| Harmonic complexity | chord-change rate band, extension density band | analysis harmony |
| Chord vocabulary | top pitch-class / roman-ish buckets (bounded list) | analysis harmony chords |
| Modulation frequency | key-span change rate band | analysis tonality spans |
| Rhythmic density | notes-per-bar band | analysis density + embeddings DENSITY |
| Syncopation preference | offbeat onset fraction band | embeddings ONSET_BINS / density heuristics |
| Preferred instrumentation | GM/family id multiset (bounded) | track roles + instrument names |
| Arrangement density | concurrent-voice / track-count bands | embeddings CONCURRENT + TRACK_COUNT |
| Repetition amount | motif recurrence / self-similarity band | analysis repetition |
| Motif development | transform variety band (when motifs present) | analysis repetition / motifs metadata |
| Preferred forms | section-type hist | embeddings SECTION_TYPE / composition.sections |
| Tension curves | coarse section tension shape code | analysis tension |
| Performance/dynamics | velocity/expression tendency bands when present | V2 expression aggregates (bounded) |

Every numeric preference stores a **band / histogram / enum**, never raw note events. Forbid fields: `events`, `tracks`, `composition`, `analysis_report`, `embedding.vector`, artist/composer ids.

### Part C — Derivation engine

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. LLM summarizes projects into prose prefs** | Flexible | Non-deterministic; opaque; may invent; hard to test | **Reject** as sole path |
| **B. Deterministic aggregate of analysis + symbolic features across selected projects** | CI-friendly; abstract; source-linked | Limited “taste” depth | **Accepted** |
| **C. Single-project embedding as the profile** | Reuses style_reference | Not multi-project; conflates reference with durable prefs; hidden authority | **Reject** as profile body (optional: profile may *also* be used alongside style_reference later) |

**Locked derive flow:**
1. User selects ≥1 projects (default: project working `composition_json` / current head; optional per-source `revision_id` via `get_revision_detail`, same pattern as style-reference loading).
2. Server loads V2 read-only; skip empty/invalid sources with warnings; fail only if **zero** usable sources (`composer_profile_derive_empty`).
3. Runs bounded feature extract + selected analysis helpers (in-process; not HTTP `/analysis`).
4. Aggregates (mean/median/mode of bands; union of top-K chord/instrument labels with caps).
5. Writes `derived` + `source_projects[]`; leaves `explicit` empty unless user promotes (API/UI).
6. Preview endpoint returns resolved view without persisting unless Save.
7. Never write `DATASET_ROOT`.

### Part D — Generation merge / priority

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Profile overwrites prompt fields** | “Always cinematic” | Violates AC (prompt overrides) | **Reject** |
| **B. Additive soft fragment only; never mutate `LLMPromptParameters` / hard constraints; strength scales fragment weight/length** | Matches AC; avoids “always-sent defaults” ambiguity (`genre`/`mood`/`instruments` always present on generate) | Implementer must inject at prompt-builder sites | **Accepted** |
| **C. Profile becomes hard constraints** | Strong control | Breaks generation constraint model; dangerous | **Reject** |
| **D. “Fill unset soft gaps” by rewriting DTO nulls** | Sounds simple | `LLMPromptParameters` defaults mean almost nothing is unset — false merge | **Reject** |

**Locked merge contract (implement must follow):**

1. **Never mutate** request `prompt` fields or hard `GenerationConstraints` (key, meter, tempo, instruments, sections, duration, instructions, genre, mood, complexity).
2. Resolve profile → strength-scaled **bounded soft fragment** (string and/or tokenizer label dict), same spirit as `conditioning_context_fragment` / “SOFT creative preferences” blocks in `llm_music_generator.py`.
3. Inject fragment into form/theme (and hybrid planner) prompt builders for `llm_only` and hybrid pipelines; assembly order: **prompt hard/soft lines → profile fragment → style_reference fragment** (when present).
4. Inside the profile fragment: **explicit** prefs beat **derived** prefs for the same preference key.
5. Provenance: nest `composer_profile_id` + `profile_strength` (+ optional `source_project_count`) under `generation_parameters` only — never dump preference bodies.
6. Fake LLM: deterministic fingerprint / soft-marker shift when `profile_strength != off` and `profile_id` set; still honor prompt instruments/key.

**Strength scaling:**

| Strength | Behavior |
|----------|----------|
| `off` | Ignore `profile_id`; no injection |
| `light` | Short soft fragment; low-weight wording; fewer preference keys |
| `normal` | Standard soft fragment covering resolved preference families |
| `strong` | Fuller soft fragment (still capped); **still cannot** override hard constraints or rewrite prompt DTO fields |

### Part E — UI placement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Buried under Advanced JSON** | Hidden | Violates “explicitly visible” | **Reject** |
| **B. New workspace tab `Profiles` + MusicGenerator strength/profile selectors** | Visible; editable; mirrors Analysis/Develop patterns | Another tab | **Accepted** |
| **C. Only a generate dropdown** | Fast | Cannot edit/derive/compare well | **Insufficient** alone |

### Part F — Compare / export / import

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Skip** | Faster | User asked if straightforward | **Reject** — it is straightforward |
| **B. Deterministic field-diff compare + JSON envelope export/import** | Matches Versions/Develop compare spirit; portable | Need size caps + secret guard | **Accepted** |
| **C. Full binary blob dump including source compositions** | Convenient restore | Copies private material | **Reject** |

Export envelope: `composer.profile.export.v1` = profile document + schema version + exported_at; **no** composition payloads; source_projects keep ids only (import may warn on missing projects).

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Hard/soft constraints | `generation_constraints.py` `GenerationConstraints` | Hard fields stay request-owned; profile never freezes into hard |
| Prompt builders | `llm_music_generator.py` SOFT creative preferences / soft preference lines | Primary injection sites for profile fragment |
| Prompt DTO | `LLMPromptParameters` / `LLMMusicGenerationRequest` | Add optional `profile_id` + `profile_strength` |
| Style conditioning | `composition_style_conditioning.py`, `conditioning_context_fragment` | Pattern for bounded soft fragments + project/revision load |
| Symbolic features | `embeddings/features.py` (`RANGE`, `INTERVAL`, `DENSITY`, `ONSET`, roles, section types) | Primary derive inputs |
| Analysis sidecars | melody/harmony/density/repetition/tension helpers | Derive bands; **do not persist full reports in profile** |
| SQLite + Alembic | head revision `20260922_0003` | Next: `20260922_0004_composer_profiles` with `down_revision = "20260922_0003"` |
| Secret guard | `persistence_secret_guard.py` | Run on profile JSON before write |
| Name normalize | branch `normalize_branch_name` pattern | `normalized_name` UNIQUE for profiles |
| Domain→HTTP | `map_critique_error_to_http` / projects 409 CAS | `map_composer_profile_error_to_http` + `expected_updated_at` |
| Project list | `project_store.list_projects`, FE ProjectBrowser / Develop picker | Multi-select source for derive |
| Fake LLM | `fake_llm.py` style_reference fingerprint shifts | Profile-strength fingerprint / soft-marker shifts |
| Provenance | `generation_provenance.py` `generation_parameters` | Bounded profile id + strength |
| MusicGenerator UI | `MusicGenerator.jsx` + `llmGenerateRequest.js` | Profile select + strength in request builder |
| Workspace tabs | `ComposerWorkspace.jsx` | Add Profiles tab |
| Env template | `.env.example` `EMBEDDING_*` / `AGENT_ARTIFACT_*` | Add `COMPOSER_PROFILE_*` |
| Docs privacy axiom | AGENTS.md / embeddings docs | Restate for profiles |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Profile schema + store | `composer.profile.v1`, SQLite CRUD, Alembic `0004` |
| Settings + `.env.example` | Caps before store enforces them |
| Derive aggregator | Multi-project abstract stats; skip bad sources |
| Resolve + strength fragment | Explicit over derived; Off/Light/Normal/Strong |
| Generate merge | Additive soft fragment; never mutate prompt DTO; provenance |
| CRUD + reset + CAS | Named multi-profile; `expected_updated_at` → 409 |
| Promote derived→explicit | API and/or panel action |
| Compare + export/import | Field diff + JSON envelope |
| FE Profiles panel | Create/edit/derive/preview/promote/compare/export/import/delete |
| FE generate controls | Profile + strength via `buildLlmRequest` |
| Tests + docs | Priority AC, privacy, README/AGENTS/`.env.example` |

### Coupling risks to avoid

1. Treating derived stats or silent behavioral inference as authoritative over explicit prefs or the project prompt.
2. Storing melodies, event arrays, full analysis reports, or embedding vectors in profiles.
3. Writing profiles or source projects into `DATASET_ROOT` / training corpora.
4. Letting Strong strength override hard constraints or **rewrite** prompt instruments/key/instructions/genre/mood.
5. Growing profile logic in `main.py` — use `routers/composer_profiles.py` + `services/`.
6. Logging full profile JSON, prompts, or event arrays at INFO.
7. Conflating Composer Profile with musical `style_reference` (complementary; distinct UX copy).
8. Making profiles required for generate (default Off / no profile).
9. Auto-applying profile changes to existing project compositions.
10. Inventing `composition.v4` or playable alternate schemas.
11. Implementing “fill unset DTO gaps” — prompt defaults make that unsafe.

## Scope And Decisions

### In scope
- `composer.profile.v1` contract + explicit vs derived split + source_projects + promote.
- SQLite persistence + Alembic migration (`down_revision: 20260922_0003`).
- Manual CRUD, derive-from-projects, preview, reset, delete, multi-named profiles, CAS updates.
- Generation merge with strength Off/Light/Normal/Strong; additive soft fragment; prompt/hard win.
- Profile compare + export/import JSON envelope.
- FE Profiles tab (incl. promote) + MusicGenerator selectors + `buildLlmRequest` wiring.
- Deterministic tests + docs (`docs/composer-profiles.md`, README/AGENTS/`.env.example`).

### Out of scope
- Hidden auto-profiling from every edit without user action.
- Cloud sync / multi-user account profiles.
- Training or fine-tuning models from profiles.
- Copying compositions into profiles or datasets.
- Replacing musical reference / embeddings UX.
- Playwright mega-journey (API + unit + store tests suffice; optional e2e later).
- Profile-driven critique/revision loops (orthogonal).
- Wiring `profile_id` / `profile_strength` into Develop / arrangement / multi-agent preview (docs follow-on only; AC is generate path).

### Architecture decisions (locked)

**1. Layering**

```text
HTTP /composer-profiles/*
        ↓
routers/composer_profiles.py  (+ map_composer_profile_error_to_http)
        ↓
services/composer_profile_store.py          # SQLite CRUD + caps + secret guard
services/composer_profile_derive.py         # multi-project abstract aggregate
services/composer_profile_resolve.py        # preview + strength-scaled soft fragment
services/composer_profile_merge.py          # attach fragment + provenance; never mutate prompt
        ↓
llm_music_generator prompt builders / fake_llm   # soft fragment only
```

Schemas: `backend/app/composer_profile_schemas.py`. Settings: `composer_profile_settings.py` (required for caps).

**2. Storage**

```sql
composer_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL UNIQUE,
  schema_version TEXT NOT NULL,  -- composer.profile.v1
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
```

Migration file: `backend/app/db/alembic/versions/20260922_0004_composer_profiles.py` with `down_revision = "20260922_0003"`.

FE session preference: versioned `localStorage` key `composerProfile:v1` (selected id + strength only; non-secret).

**3. API surface**

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/composer-profiles` | List (id, name, updated_at, source_count) |
| `POST` | `/composer-profiles` | Create (manual empty or body) |
| `GET` | `/composer-profiles/{id}` | Full profile |
| `PUT` | `/composer-profiles/{id}` | Replace/edit; **require** `expected_updated_at` CAS → 409 `composer_profile_conflict` |
| `POST` | `/composer-profiles/{id}/reset` | Clear explicit and/or derived / sources per flags |
| `POST` | `/composer-profiles/{id}/promote` | Copy selected or all derived fields → explicit |
| `DELETE` | `/composer-profiles/{id}` | Delete |
| `POST` | `/composer-profiles/derive` | Preview derive from `project_ids[]` (optional `save_as`) |
| `POST` | `/composer-profiles/{id}/preview` | Resolve soft fragment at strength |
| `POST` | `/composer-profiles/compare` | Diff two profile ids or bodies |
| `GET` | `/composer-profiles/{id}/export` | Export envelope |
| `POST` | `/composer-profiles/import` | Import envelope → new or replace |

Error codes: `composer_profile_not_found`, `composer_profile_name_conflict`, `composer_profile_conflict`, `composer_profile_invalid`, `composer_profile_derive_empty`, `composer_profile_source_missing`, `composer_profile_forbidden_payload`, `composer_profile_import_invalid`, `composer_profile_cap_exceeded`.

**4. Generate request extension**

```text
LLMMusicGenerationRequest +=
  profile_id: str | None
  profile_strength: Literal["off","light","normal","strong"] = "off"
```

Develop/arrangement/agents: **out of scope** for this plan (document as follow-on).

**5. Settings / env (locked keys)**

| Env | Role |
|-----|------|
| `COMPOSER_PROFILE_MAX_PROFILES` | Cap stored profiles |
| `COMPOSER_PROFILE_MAX_SOURCE_PROJECTS` | Cap per derive |
| `COMPOSER_PROFILE_MAX_EXPORT_BYTES` | Import/export size |
| `COMPOSER_PROFILE_FRAGMENT_MAX_CHARS` | Soft fragment cap (mirror embedding feature summary) |
| `COMPOSER_PROFILE_MAX_LIST_ITEMS` | Cap chord/instrument preference lists |

Document in `.env.example` next to other subsystem knobs.

**6. Logging**

- INFO: profile_id, name length, strength, source_project_count, derive duration_ms, merge applied_field_count / skip counts, export/import/promote result codes, CAS conflict.
- DEBUG: preference field keys present, band codes, skip reasons (`prompt_owned`, `explicit_over_derived`).
- Never INFO: full body_json, instructions, event arrays, embedding vectors, API keys.

**7. Frontend**

- Tab **Profiles**: list, create, rename, edit explicit, multi-select derive, **promote derived→explicit**, preview, compare, export/import, delete/reset; show explicit vs derived distinctly.
- `MusicGenerator`: profile dropdown + strength; `buildLlmRequest` sends `profile_id` / `profile_strength` (default Off).
- Copy: “Composer profile” / “Musical preferences” — never “in the style of ⟨Artist⟩”.

**8. Acceptance criterion (locked)**

User creates **"My Cinematic Style"** by selecting ≥1 projects → derived profile saved with `source_projects` populated → new generate with `profile_strength=normal` (or Strong) and prompt overriding instrumentation + key → output respects prompt instruments/key (DTO/hard unchanged) while soft fragment / fake marker reflects profile; tests assert merge priority; profile body contains no note events; no `DATASET_ROOT` writes.

## Commit Plan

- **Commit 1** (after tasks 1–3): `feat(profiles): add composer.profile.v1 schemas, settings, and SQLite store`
- **Commit 2** (after tasks 4–6): `feat(profiles): derive, resolve, and additive generate soft-fragment merge`
- **Commit 3** (after tasks 7–9): `feat(api): composer profile CRUD, CAS, promote, compare, export/import`
- **Commit 4** (after tasks 10–12): `feat(frontend): Profiles tab and generate profile strength controls`
- **Commit 5** (after tasks 13–15): `test(docs): composer profile priority tests and documentation`

## Tasks

### Phase 1: Contracts & persistence

- [x] Task 1: Composer profile schemas
  Deliverable: `backend/app/composer_profile_schemas.py` — `ComposerProfileV1`, preference field models (bounded), `explicit` vs `derived`, `source_projects[]`, `ProfileStrength`, export envelope `composer.profile.export.v1`, promote/compare/preview DTOs, error codes (incl. `composer_profile_conflict`, `composer_profile_cap_exceeded`). Forbid extras; reject payloads containing events/tracks/composition/analysis_report/embedding.vector/artist fields. Unit tests for validation + forbid.
  LOGGING: DEBUG schema reject codes only; no body dumps.
  Files: `backend/app/composer_profile_schemas.py`, `backend/tests/test_composer_profile_schemas.py`

- [x] Task 2: Profile settings / caps
  Deliverable: `composer_profile_settings.py` — load `COMPOSER_PROFILE_MAX_PROFILES`, `MAX_SOURCE_PROJECTS`, `MAX_EXPORT_BYTES`, `FRAGMENT_MAX_CHARS`, `MAX_LIST_ITEMS` with safe defaults; helpers used by store/derive/resolve.
  LOGGING: DEBUG loaded caps at first use.
  Files: `backend/app/composer_profile_settings.py`, `backend/tests/test_composer_profile_settings.py`
  Depends on: Task 1 (error code for cap exceeded may live in schemas)

- [x] Task 3: Alembic migration + store
  Deliverable: Migration `20260922_0004_composer_profiles.py` with **`down_revision = "20260922_0003"`**; `services/composer_profile_store.py` CRUD (list/create/get/update/delete) with branch-style `normalized_name` uniqueness, secret-guard before write, enforce max-profiles cap, CAS update requiring `expected_updated_at` (mismatch → domain conflict).
  LOGGING: INFO create/update/delete with id + name_len; WARN secret reject / cap / CAS conflict codes; never log body_json.
  Files: `backend/app/db/alembic/versions/20260922_0004_composer_profiles.py`, `backend/app/services/composer_profile_store.py`, `backend/tests/test_composer_profile_store.py`
  Depends on: Task 2
  <!-- Commit checkpoint: tasks 1–3 -->

### Phase 2: Derive, resolve, merge

- [x] Task 4: Deterministic derive aggregator
  Deliverable: `services/composer_profile_derive.py` — for each selected project_id (+ optional revision_id), load V2 like style-reference (`project_store` composition_json or `get_revision_detail`); skip empty/invalid with warnings; fail if zero usable sources; extract abstract bands via embeddings features + selective analysis helpers; aggregate; emit `derived` + `source_projects` (ids + fingerprint prefixes only). Never write `DATASET_ROOT`. Respect `MAX_SOURCE_PROJECTS` / list caps.
  LOGGING: INFO derive project_count, usable_count, duration_ms, field_keys_count; DEBUG per-source fingerprint prefixes + skip reasons.
  Files: `backend/app/services/composer_profile_derive.py`, `backend/tests/test_composer_profile_derive.py`
  Depends on: Tasks 1–2

- [x] Task 5: Resolve + strength scaling
  Deliverable: `services/composer_profile_resolve.py` — merge explicit over derived into resolved preference view; apply strength → bounded soft fragment (string and/or tokenizer labels) capped by `FRAGMENT_MAX_CHARS`. Preview-safe (no generate). Off → empty fragment.
  LOGGING: INFO profile_id, strength, applied_field_count, fragment_chars; DEBUG skipped derived keys overridden by explicit.
  Files: `backend/app/services/composer_profile_resolve.py`, tests for Off/Light/Normal/Strong fragment size/weight
  Depends on: Tasks 1–2

- [x] Task 6: Generate merge under locked additive contract
  Deliverable: `services/composer_profile_merge.py` + wire into `generate_music_json` / prompt builders in `llm_music_generator.py` so profile injects **additive soft fragment only** — never mutates `LLMPromptParameters` or hard `GenerationConstraints` (esp. instruments + key). Cover `llm_only` and hybrid planner prompt paths. Attach provenance via `generation_parameters` (`composer_profile_id`, `profile_strength`). Fake LLM: deterministic soft-marker / fingerprint shift when strength ≠ off while still respecting prompt instruments/key. Assembly: prompt → profile → style_reference.
  LOGGING: INFO merge outcome (applied_field_count, strength); DEBUG skip reasons `prompt_owned`.
  Files: `backend/app/services/composer_profile_merge.py`, `backend/app/schemas.py`, `backend/app/services/llm_music_generator.py`, `backend/app/services/fake_llm.py`, `backend/app/services/generation_provenance.py` (if needed), `backend/tests/test_composer_profile_merge.py`
  Depends on: Task 5
  <!-- Commit checkpoint: tasks 4–6 -->

### Phase 3: HTTP API

- [x] Task 7: CRUD + reset + promote router
  Deliverable: `routers/composer_profiles.py` — list/create/get/update/reset/promote/delete; `map_composer_profile_error_to_http` (404/409/422); PUT requires `expected_updated_at`; promote copies selected or all derived keys into explicit; include router in `main.py`.
  LOGGING: INFO method + profile_id + status; never request bodies at INFO.
  Files: `backend/app/routers/composer_profiles.py`, `backend/app/main.py`, `backend/tests/test_composer_profiles_routes.py`
  Depends on: Task 3

- [x] Task 8: Derive + preview endpoints
  Deliverable: `POST /composer-profiles/derive` (preview and optional `save_as` name); `POST /composer-profiles/{id}/preview` with strength. AC path: derive → save “My Cinematic Style”.
  LOGGING: INFO derive/preview ids + strength + source counts.
  Files: router wiring + route tests
  Depends on: Tasks 4, 5, 7

- [x] Task 9: Compare + export/import
  Deliverable: Deterministic field-level compare DTO; export envelope GET; import POST with secret-guard + schema validate + size cap; missing source projects → warnings not hard fail.
  LOGGING: INFO compare/export/import result codes; WARN oversized import reject.
  Files: compare/export helpers, router endpoints, `backend/tests/test_composer_profile_export.py`
  Depends on: Tasks 1, 3, 7
  <!-- Commit checkpoint: tasks 7–9 -->

### Phase 4: Frontend

- [x] Task 10: API client + store fields
  Deliverable: `frontend/src/api/composerProfileApi.js` (+ tests); Zustand fields for selected profile id, strength, list cache, preview payload; optional `composerProfile:v1` localStorage for last selection; no profile bodies in logs.
  LOGGING: existing FE debug policy; no full profile dumps.
  Files: `frontend/src/api/composerProfileApi.js`, `frontend/src/api/composerProfileApi.test.js`, `frontend/src/store/musicStore.js` (minimal fields)
  Depends on: Task 7 (API shape)

- [x] Task 11: Profiles panel UI
  Deliverable: `ComposerProfilesPanel.jsx` — create/rename/edit explicit fields (distinct from derived), multi-select derive from project list, **promote derived→explicit** (selected or all), preview, reset/delete, compare two, export download + import file. Wire tab in `ComposerWorkspace.jsx`. Visible and editable (AC #1 / #5).
  LOGGING: debug user actions (create/derive/promote/save/delete) with ids + field-key counts only.
  Files: `frontend/src/components/ComposerProfilesPanel.jsx`, `frontend/src/components/ComposerWorkspace.jsx`, utils as needed
  Depends on: Task 10

- [x] Task 12: MusicGenerator profile + strength controls
  Deliverable: Profile dropdown + strength Off/Light/Normal/Strong; extend `buildLlmRequest` / `llmGenerateRequest.js` to pass `profile_id` + `profile_strength` (default `off`); unit tests for request builder; instruments/key inputs remain request-owned.
  LOGGING: debug generate with profile_id + strength only.
  Files: `frontend/src/components/MusicGenerator.jsx`, `frontend/src/utils/llmGenerateRequest.js`, `frontend/src/utils/llmGenerateRequest.test.js` (or existing test file), store generate helpers
  Depends on: Task 10
  <!-- Commit checkpoint: tasks 10–12 -->

### Phase 5: Tests & docs

- [x] Task 13: Acceptance / integration tests
  Deliverable: End-to-end API test — create profile from fixture projects (“cinematic” sources) → generate with `profile_strength=normal` + conflicting instruments/key in prompt → assert prompt instruments/key unchanged in constraints/output path and soft fragment/provenance includes profile; assert profile JSON has no events; assert no dataset writes. Frontend unit tests: `buildLlmRequest` omits/sets strength Off by default.
  LOGGING: test logs follow service rules.
  Files: `backend/tests/test_composer_profile_acceptance.py`, FE request builder tests
  Depends on: Tasks 6, 8

- [x] Task 14: Documentation + env template
  Deliverable: `docs/composer-profiles.md` (schema, **additive merge contract**, strength, derive privacy, promote, API, UI); README feature bullet; AGENTS.md entry; cross-links from embeddings + generation docs; **`.env.example` `COMPOSER_PROFILE_*` knobs**. Note Develop/agents profile wiring as follow-on.
  LOGGING: n/a
  Files: `docs/composer-profiles.md`, `README.md`, `AGENTS.md`, `.env.example`, light cross-links in `docs/embeddings.md`
  Depends on: Tasks 1–12 conceptually (docs checkpoint at end)

- [x] Task 15: Privacy & logging regression guards
  Deliverable: Tests that derive/store refuse event-bearing payloads; import rejects secret-like fields; architecture smoke that profile services never import dataset CLI / never write `DATASET_ROOT`; assert merge never changes prompt.instruments/key in unit tests.
  LOGGING: assert INFO paths don’t include body keys in test helpers if such helpers exist.
  Files: `backend/tests/test_composer_profile_privacy.py`
  Depends on: Tasks 3, 4, 6
  <!-- Commit checkpoint: tasks 13–15 -->

## Implementation Notes for `/aif-implement`

1. Prefer analysis **metric extractors** and embeddings **feature bands** over calling full `POST /analysis/composition` HTTP from the derive service (in-process helpers).
2. Keep profile soft fragment size capped via `COMPOSER_PROFILE_FRAGMENT_MAX_CHARS`.
3. Default generate remains `profile_strength=off` with no profile — zero behavior change until user opts in.
4. Name uniqueness: case-fold / whitespace-normalize like project branches (`normalized_name`).
5. Do not auto-link profiles to project revisions on every save of a composition.
6. Do **not** rewrite `LLMPromptParameters` to “fill gaps” — defaults are always sent; use additive soft fragment only.
7. Develop / arrangement / multi-agent profile fields are **follow-on** — document only; AC is `/llm/generate-music-json`.
8. PUT without `expected_updated_at` → 422; mismatch → 409 `composer_profile_conflict`.

## Improvement log (`/aif-improve` 2026-09-22)

- Locked additive soft-fragment merge (rejected DTO gap-filling).
- Locked Alembic parent `20260922_0003`; reordered settings before store.
- Locked PUT CAS; added promote API/UI; `.env.example` keys.
- Clarified derive load path + skip/fail rules; generate injection + provenance sites.
- Explicit task dependencies; Develop/agents wiring marked out of scope follow-on.
