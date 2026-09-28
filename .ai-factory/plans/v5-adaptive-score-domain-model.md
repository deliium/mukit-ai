# Implementation Plan: V5 Adaptive Score Domain Model

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-28
Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-28)

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-28)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: introduce a durable, versioned **Adaptive Score** document that describes non-linear music (states, variants, transitions, layers, stingers) by **referencing** canonical `composition.v2` material. It must not become a second note-event representation, must not invent `composition.v5`, and must not change playback, export, or agent Apply.

## Roadmap Linkage
Milestone: "V5 Adaptive Score domain model"
Rationale: Every V1–V4 roadmap milestone is checked complete. This plan opens the first V5 surface: a project-scoped adaptive graph over existing Composition revisions, regions, and assets. `ROADMAP.md` does not yet list this heading; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A project can define multiple musical states that point at existing Composition material without copying canonical note events.

Ship:

1. Versioned document `adaptive.score.v1` (`extra=forbid`) with states, variants, transitions, layers, stingers, material references, duration bounds, quantization, conditions, and fallback behavior.
2. Explicit **horizontal resequencing** (state graph + transitions) and **vertical layering** (layers active inside a state or globally).
3. Validation that rejects embedded note events and dangling graph edges, and that checks section / track / motif / revision targets against the project’s Composition.
4. SQLite persistence in `PROJECT_DB_PATH` with Alembic upgrade/downgrade, per-project documents, compare-and-swap, and project-delete cascade.
5. HTTP CRUD plus a read-only validate call. Writes do not mutate `projects.composition_json` or composition snapshots.
6. Tests (schema, graph rules, store, API, migration) and architecture documentation.

```text
composition.v2 working draft / project_revisions snapshot  (authoritative notes)
        ↑ referenced by id, section, bar range, motif, optional fingerprint pin
adaptive.score.v1  (states, variants, transitions, layers, stingers)
        ↓
services/adaptive_score_store.py  →  table adaptive_scores
        ↓
GET/POST/PUT/DELETE /projects/{id}/adaptive-scores
POST .../validate   (read-only findings; no composition write)
```

**Terminology lock:** Product generation is **V5**. The document schema is **`adaptive.score.v1`**. The playable schema stays **`composition.v2`**. There is no `composition.v5`. Horizontal resequencing is the state/transition graph. Vertical layering is simultaneous material references inside a state. A runtime that actually switches music during playback is **not** this plan.

Example states the document must be able to name: exploration, suspense, combat, victory.

## Approach Evaluation (locked)

### Part A — Where the model lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New fields on `CompositionV2`** | One document | Mixes playable notes with game-state graph; every export/playback/migration parser must ignore it; invites a second event list | **Reject** |
| **B. `composition.v5` playable schema** | Clean version number | Replaces the canonical contract the product just stabilized; user forbade a second note representation | **Reject** |
| **C. Sidecar `adaptive.score.v1` in its own table, referencing V2** | Matches composer-profile / mix-plan sidecar style; Composition stays authoritative | New schema, migration, and API | **Accepted** |
| **D. Session-only Zustand graph** | No migration | Fails “a project can define” across restart | **Reject** |

### Part B — How material is referenced

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Copy `events[]` into each state** | Self-contained playback later | Duplicates canonical notes; drifts from revisions | **Reject** |
| **B. Reference `section_id` / bar range / `track_ids` / `motif_id` / `revision_id` / optional `snapshot_fingerprint` / optional asset id** | Zero note copies; revisions stay the source | Bindings can go stale when the score changes | **Accepted** |
| **C. Reference only the whole project revision** | Simple | Cannot point a state at exploration bars vs combat bars | **Insufficient** |

Stale pins are reported, not repaired by rewriting Composition.

### Part C — Shape of horizontal vs vertical

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One flat cue list** | Small | Cannot say “combat percussion layers on while the combat bed loops” | **Reject** |
| **B. States + transitions for time, layers for simultaneity, variants for alternate material of one state** | Matches the requested tree; both concepts are named types | More models | **Accepted** |
| **C. Layers as the only axis, states as intensity labels** | Short schema | Hides resequencing | **Reject** |

### Part D — Conditions and quantization

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Freeform expression / script strings** | Flexible | Becomes a hidden language; untestable; logging risk | **Reject** |
| **B. Closed condition kinds and a closed quantization enum** | Validatable; safe to log codes | New behavior needs a schema bump | **Accepted** |
| **C. Defer conditions (boolean only)** | Smaller | Fails “transition conditions” | **Reject** |

### Part E — Persistence row shape

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Normalized tables per state/transition/layer** | Queryable edges | High migration cost; graph edits become many writes; document versioning is harder | **Reject** for v1 |
| **B. One `body_json` document per score, many scores per project, integer `document_revision` CAS** | Same pattern as `composer_profiles.body_json`; atomic graph replace; Alembic stays one table | Large-row updates | **Accepted** |
| **C. Single JSON column on `projects`** | One row | No multiple scores; couples adaptive writes to composition CAS; harder to drop later | **Reject** |

### Part F — Binding checks

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Structural schema only** | Fast | Acceptance can store a section id that does not exist | **Insufficient** |
| **B. On write, resolve section/track/motif/revision against the project Composition; asset ids are shape-checked only** | Meets “references existing material”; avoids coupling the store to WAV roots | Asset existence is unchecked in v1 | **Accepted** |
| **C. Also stat audio files and neural jobs** | Stronger pins | Pulls neural/recovery/DATASET roots into a symbolic model; out of scope | **Reject** for this plan |

### Part G — Product surface

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Schema + SQLite + HTTP + docs** | Delivers the domain model and the acceptance test | No graph canvas | **Accepted** |
| **B. Also a real-time adaptive playback engine** | Plays the graph | New runtime, Tone.js scheduler, game flags; not required to define the model | **Defer** |
| **C. Also a visual node editor** | Authoring UX | Large frontend with no runtime to verify | **Defer** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| `CompositionV2` | `schema_version="composition.v2"`; `sections[]` (`id`, `start_bar`, `bar_count`, `start_tick`, `duration_ticks`); `tracks[].id`; `motifs[].id`; `extra=forbid` | Read-only binding target. Do not add adaptive fields |
| `composition_snapshots` / `project_revisions` | Content-addressed `snapshot_fingerprint`; revisions belong to `project_id` | Optional pin and `revision_id` on a material ref. Decode with the existing snapshot API; do not copy events into the adaptive body |
| `composition_snapshot_fingerprint` | `backend/app/services/composition_snapshot_encoding.py` | Compare pin vs current working score; log prefix only |
| Alembic head | `20260926_0017` (`project_activity`), chain from `20260914_0001` | Next revision `20260928_0018`, `down_revision="20260926_0017"` |
| Project delete | `PRAGMA foreign_keys=ON` in `backend/app/db/connection.py`; child tables use `ON DELETE CASCADE` | Same FK for `adaptive_scores.project_id` |
| Sidecar JSON docs | `composer_profiles` (`body_json`, `schema_version`, `normalized_name`) | Document + CAS pattern, not the global (non-project) key |
| Secret guard | `persistence_secret_guard.assert_no_secret_fields/values` | Call before INSERT/UPDATE |
| HTTP errors | Domain `Error` type + `map_*_error_to_http` → `HTTPException(detail={code,message,details})` | Same for adaptive scores |
| Collaboration | `enforce_current(project_id, action)` in routers such as `mix_plan.py` | Use it. Flag off stays open. Services do not import FastAPI |
| Migration regression | `backend/tests/studio_acceptance/test_migration_ladder.py` upgrades a planted v1 project to current head and asserts `composition.v2` | Must keep passing. Add a focused adaptive migration test; do not weaken the ladder |
| Logging | `LOG_LEVEL`; never log event arrays, prompts, or raw composition JSON at INFO | Same redaction for adaptive bodies |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Adaptive document schema | No states, transitions, layers, or stingers exist |
| Material reference type | Nothing points at a revision/section/bar range without embedding events |
| Graph validation | Uniqueness, edge endpoints, duration bounds, eligibility agreement, forbidden note keys |
| Binding validation | Section/track/motif/revision existence against the project |
| Table + migration | No `adaptive_scores` table |
| HTTP | No routes under `/projects/{id}/adaptive-scores` |
| Docs | Architecture still says V2 is the only operational music document, which remains true, but does not yet describe this sidecar |

### Coupling risks to avoid

1. Embedding `events`, `pitch`, or a nested `composition` object in the adaptive document.
2. Adding adaptive keys to `CompositionV2` or accepting `schema_version` `composition.v5`.
3. Writing `projects.composition_json` or snapshot blobs from the adaptive store or from binding (`persist_canonical=True`, history bootstrap that rewrites the score).
4. Letting `ai_agents/` import `adaptive_score_store` or SQLite.
5. Putting graph logic in `main.py` beyond `include_router`.
6. Logging `body_json`, event arrays, or full compositions at INFO.
7. Treating asset ids as proof a WAV exists, or reading `DATASET_ROOT`.
8. Introducing a scripting language for transition conditions.
9. Building the playback runtime or a node canvas in this plan.
10. Using relative “bars inside the clip” as a second coordinate system. Bar numbers are absolute composition bars, matching `CompositionV2Section.start_bar`.

## Scope And Decisions

### In scope
- `adaptive.score.v1` Pydantic models, caps, and error codes.
- Pure graph and anti-event validation.
- Binding checks against the working composition or a project revision snapshot.
- Alembic `20260928_0018_adaptive_scores`.
- Store with CAS, unique normalized name per project, one default score per project, secret guard, cascade delete.
- Router under `routers/adaptive_scores.py`.
- Tests and `docs/adaptive-score.md` plus the architecture map rows named in Task 8.
- Verbose logging controlled by `LOG_LEVEL`.

### Out of scope
- Real-time adaptive playback, Tone.js state machines, game-flag input, stinger audio scheduling.
- Visual graph editor, piano-roll changes, Playwright journeys.
- `composition.v5` or any new playable note schema.
- Copying or generating notes when a reference is missing.
- Agent authoring of adaptive scores, workspace artifacts, or `multi-agent-apply`.
- Resolving neural stem files, recovery WAVs, or `DATASET_ROOT`.
- Editing `ROADMAP.md` (roadmap owner command).
- Mix DSP. Layer `mix_hint` is a label, not a gain curve and not `mix.plan.v1`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py
        ↓  enforce_current ("read" | "write_score"); map domain errors
services/adaptive_score_service.py      (loads Composition read-only, then binds)
        ↓
services/adaptive_score_validation.py   (pure; no SQLite, no FastAPI)
services/adaptive_score_store.py        (SQLite only; no composition load)
        ↓
adaptive_scores.body_json               (adaptive.score.v1 only)
```

`adaptive_score_service.py` reads the working composition or one revision. It is not imported by the pure validator. The store does not import `project_history` or `project_store`. `ai_agents/` does not import the store.

**2. Document (`AdaptiveScoreV1`)**

```json
{
  "schema_version": "adaptive.score.v1",
  "id": "ascore_0123456789abcdef",
  "project_id": "proj-1",
  "name": "Main cue",
  "initial_state_id": "state-exploration",
  "default_state_id": "state-exploration",
  "fallback": {
    "on_missing_material": "hold",
    "on_invalid_transition": "stay",
    "on_unresolved_condition": "stay"
  },
  "states": [],
  "variants": [],
  "transitions": [],
  "layers": [],
  "stingers": []
}
```

- `schema_version` is `Literal["adaptive.score.v1"]`. Any other explicit version, including `composition.v5`, is `unsupported_schema_version` (422).
- `extra=forbid` on every model.
- On create the server assigns `id` as `ascore_` plus 16 hex characters and sets `body.id` and `body.project_id` from the route. A client `id` or `project_id` that disagrees is 422 `identity_mismatch`. PUT `body.id` must equal the path score id.
- `expected_document_revision` lives on `AdaptiveScoreUpdateRequest`, not inside `body_json`.
- Drafts may have zero states. A score is **ready** only when `states` is non-empty, `initial_state_id` and `default_state_id` exist, and validate returns no errors. Readiness is a validate result, not a second schema.
- Caps live in `adaptive_score_settings.py` (env-overridable, with safe defaults): states ≤ 32, variants per state ≤ 8, transitions ≤ 64, layers ≤ 32, stingers ≤ 32, conditions per transition ≤ 8, name ≤ 80, body JSON ≤ 262144 bytes. Exceeding a cap is 422 `adaptive_score_too_large`.

**3. Material reference (`AdaptiveMaterialRefV1`)**

Closed `kind`:

| kind | Required fields |
|------|-----------------|
| `section` | `section_id` |
| `bar_range` | `start_bar`, `end_bar` (`end_bar >= start_bar`, both ≥ 1) |
| `track_range` | `track_ids` (1..16) and optional bar range |
| `motif` | `motif_id` |
| `revision_region` | `revision_id` plus `section_id` or a bar range |
| `asset` | `asset_kind` + `asset_id` |

Shared optional fields: `revision_id`, `snapshot_fingerprint` (pin only), `section_id`, `track_ids`, `start_bar`, `end_bar`, `start_tick`, `end_tick`, `motif_id`.

`asset_kind` is `neural_stem | neural_mix | recovery_source | alignment`. Asset refs are **not** looked up on disk in this plan. Validate reports `binding_status: unchecked` for them.

Forbidden anywhere in the tree (recursive key scan before accept): `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`. A hit is 422 `embedded_note_material`. This is in addition to `extra=forbid`, so a future nested model cannot smuggle an event list under an allowed object.

**4. State**

- `id`, `name` (examples: Exploration, Suspense, Combat, Victory — names are user text; ids are stable).
- `intensity`: float 0..1, the nominal intensity of this state.
- `material`: one `AdaptiveMaterialRefV1` (the horizontal bed).
- `loop`: `{enabled, start_bar, end_bar}` using absolute composition bars. When `enabled` is true, both bars are required and `end_bar >= start_bar`.
- `entry` / `exit`: `{kind: bar|tick|material_start|material_end, bar, tick}`. `bar` required for `bar`; `tick` required for `tick` (≥ 0).
- `min_duration_bars` (≥ 0) and `max_duration_bars` (null means unbounded). When both are set, min ≤ max. Error `duration_bounds`.
- `transition_ids`: eligibility list. Every id must exist, and that transition’s `from_state_id` must be this state. Error `transition_eligibility_mismatch`.

**5. Variant**

- `id`, `state_id`, `name`, `material`, `intensity_min`, `intensity_max` (0..1, min ≤ max).
- Alternate material for one state. Not a new state and not a note list.

**6. Transition (horizontal resequencing)**

- `id`, `from_state_id`, `to_state_id`, both states must exist. Self-transitions are allowed.
- `quantization`: `immediate | beat | bar | next_exit | custom`.
- `custom_grid_bars`: required iff quantization is `custom`, integer 1..64. Error `quantization_grid`.
- `priority`: int, higher wins when several transitions are eligible. The runtime is future work; the field is stored and validated only.
- `conditions`: list of closed kinds:
  - `manual`
  - `intensity_at_least` / `intensity_at_most` with `value` in 0..1
  - `flag_equals` with `flag` matching `^[a-z][a-z0-9_]{0,40}$` and JSON boolean `value`
  - `min_time_in_state_bars` with integer `value` ≥ 0
- `fallback_behavior`: `stay | default_state | alternate_transition`.
- `fallback_state_id` required for `default_state` unless the score-level default is used — **lock:** transition `default_state` uses the score `default_state_id` and must not store a second target. `fallback_transition_id` required only for `alternate_transition` and must exist.

Cycles are valid (victory may return to exploration). Unreachable states are validate **warnings** `state_unreachable`, not save errors, so drafts can be built incrementally. `POST .../validate?strict=true` promotes warnings to errors.

**7. Layer (vertical layering)**

- `id`, `name`, `state_id` (null means the layer may apply in any state), `material`.
- `intensity_min`, `intensity_max`.
- `mix_hint`: `bed | foreground | ornament`. A label only.
- `default_active`: bool.

**8. Stinger**

- `id`, `name`, `material`, `associated_state_id` (null means any state).
- `interrupt_policy`: `overlay | duck_bed | wait_for_exit`.
- `quantization`: same enum as transitions; `custom_grid_bars` under the same rule.
- `retrigger`: `once | always`.

**9. Score-level fallback**

`on_missing_material`: `hold | silence | default_state`.
`on_invalid_transition`: `stay | default_state`.
`on_unresolved_condition`: `stay | default_state`.
`silence` means “play nothing,” not “synthesize a rest note into Composition.”

**10. Identity namespace**

Within one score, `states`, `variants`, `transitions`, `layers`, and `stingers` share one id set. Duplicates are `duplicate_id`.

**11. Table `adaptive_scores`**

```text
id TEXT PRIMARY KEY
project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE
name TEXT NOT NULL
normalized_name TEXT NOT NULL   -- NFKC + casefold, same idea as composer profiles
schema_version TEXT NOT NULL
body_json TEXT NOT NULL
document_revision INTEGER NOT NULL DEFAULT 1
is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1))
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
UNIQUE (project_id, normalized_name)
```

- Partial unique index: one `is_default=1` row per `project_id`.
- Index `(project_id, updated_at DESC)`.
- `document_revision` is the adaptive CAS counter. It is not a composition revision id.
- PUT requires `expected_document_revision` on the update DTO, beside the score body. Mismatch is 409 `adaptive_score_conflict` and does not write.
- Setting `is_default=true` clears the previous default in the same transaction.
- Body is stored after Pydantic validation and the secret guard. Canonical dump uses `model_dump(mode="json")`. Do not sort list order.
- Multiple scores per project are allowed. The acceptance case is multiple **states** on one score; multiple scores are the container (exploration cue vs menu cue).

**12. Binding**

Every non-asset material ref on one score shares one `revision_id`. All null means the working composition. A mix is 422 `mixed_revision_targets`. Asset-only scores skip composition lookup and report `binding_status: unchecked`.

When a non-asset ref exists, load one composition:

- No `revision_id`: `normalize_project_composition(projects.composition_json, persist_canonical=False)` from `project_composition.py`. Never pass `persist_canonical=True`. Do not call a path that rewrites `composition_json` while binding.
- `revision_id` set: `get_revision_detail(project_id, revision_id)` in `project_history.py` (scoped by `project_id` and `id`). Map `ProjectHistoryNotFoundError` to 404 `revision_not_found`. Decode stays inside that helper. Do not copy events into `body_json`.
- `composition_json` null, or no composition on the revision: 422 `composition_unavailable`.
- `CompositionV2Section.id` is optional. Only non-null section ids are targets. A requested `section_id`, `track_id`, or `motif_id` that is absent is 422 `material_target_missing`.
- Bar range outside `bar_count` is 422 `material_range_outside`.
- If `snapshot_fingerprint` is set and differs from `composition_snapshot_fingerprint` of the resolved composition, the row may still save. Response includes `binding_status: stale`. Match is `fresh`. No pin is `unpinned`. Asset refs are `unchecked`.
- The server does not invent or rewrite a fingerprint inside `body_json`.
- Before and after every write, `projects.composition_json` bytes are unchanged, including when the stored document is still `composition.v1` (bind migrates in memory only). Tests assert this.

**13. HTTP**

Prefix: `/projects/{project_id}/adaptive-scores`. Tag: `adaptive-scores`.

| Method | Path | Behavior |
|--------|------|----------|
| GET | `` | List summaries (id, name, schema_version, is_default, document_revision, state_count, updated_at). No `body_json` in the list |
| POST | `` | 201 create. Optional `is_default` |
| GET | `/{score_id}` | Full document + `document_revision` + computed `binding_status` |
| PUT | `/{score_id}` | Replace body. Requires `expected_document_revision` |
| DELETE | `/{score_id}` | 204 |
| POST | `/{score_id}/validate` | Read-only findings. Query `strict` default false. Does not write |

Unknown project: 404 `project_not_found`. Unknown score: 404 `adaptive_score_not_found`.

Collaboration: `enforce_current(project_id, "read")` for GET and validate; `"write_score"` for POST, PUT, and DELETE. `"edit"` is not a collaboration action (`collaboration_permissions.ACTIONS`); using it denies every role, including owner. When `COLLABORATION_ENABLED` is off, `enforce_current` returns without a lookup (existing helper). Viewers may read. Editors may write.

**14. Findings from validate**

```json
{"code": "state_unreachable", "severity": "warning", "target_id": "state-victory"}
```

`severity` is `warning` or `error`. Stable `code` values listed above plus `ok` when the error list is empty. Explanations stay short (≤ 200). Do not include composition events in `details`.

**15. Logging**

- INFO: `schema_version`, `project_id`, `score_id`, counts (states, variants, transitions, layers, stingers), `document_revision`, `binding_status`, `is_default`, duration_ms, validate error/warning counts.
- DEBUG: state ids, material `kind` values, section ids, revision id, fingerprint **prefix** via `snapshot_fingerprint_log_prefix`, finding codes.
- Never INFO or DEBUG: `body_json`, `composition_json`, event arrays, note pitches, prompts, secret values.
- Migration logs follow the existing Alembic style: revision id, stage, table name. Never log row bodies.
- Levels follow `LOG_LEVEL`. No hardcoded DEBUG that ignores it.

**16. Documentation**

`docs/adaptive-score.md` states the authority split, the two axes (horizontal resequencing, vertical layering), the reference rules, staleness, and the non-goals (no playback runtime, no `composition.v5`). Also update `AGENTS.md`, `.ai-factory/ARCHITECTURE.md` (module row + dependency note), `docs/project-persistence.md` (table + cascade), and `docs/CODEBASE_MAP.md` with a short pointer. Add one axiom to `.ai-factory/RULES.md`: an adaptive score references Composition and never stores note events.

## Commit Plan
- **Commit 1** (after tasks 1–2): `feat: add adaptive.score.v1 schema and graph validation`
- **Commit 2** (after tasks 3–4): `feat: persist adaptive scores per project`
- **Commit 3** (after tasks 5–6): `feat: expose adaptive score read and write API`
- **Commit 4** (after tasks 7–8): `test: cover adaptive score bindings and migrations`
- **Commit 5** (after task 9): `docs: describe the adaptive score domain model`

## Tasks

### Phase 1: Schema and pure validation

- [x] Task 1: Add `adaptive.score.v1` models and caps
  - Create `backend/app/adaptive_score_schemas.py` and `backend/app/adaptive_score_settings.py`.
  - Models: `AdaptiveScoreV1`, `AdaptiveScoreStateV1`, `AdaptiveScoreVariantV1`, `AdaptiveScoreTransitionV1`, `AdaptiveScoreLayerV1`, `AdaptiveScoreStingerV1`, `AdaptiveMaterialRefV1`, `AdaptiveScoreFallbackV1`, entry/exit and loop models, list/create/update/get DTOs, `AdaptiveScoreFindingV1`, `AdaptiveScoreError` with `code`, `http_status`, `message`, `details`, and `map_adaptive_score_error_to_http`.
  - Lock the enums, required-field matrix, caps, forbidden note keys, and `Literal["adaptive.score.v1"]` from Architecture decisions 2–11.
  - `AdaptiveScoreUpdateRequest` carries `expected_document_revision` beside the score body. It is not a field of `AdaptiveScoreV1`.
  - `normalize_adaptive_score_name` is a local NFKC + casefold copy of `normalize_branch_name` in `project_history_store.py` (collapse whitespace the way `normalize_profile_name` does, then casefold). Do not import `project_history_store` from the schema module.
  - LOGGING REQUIREMENTS:
    - On schema failure, log model name, field, and code at DEBUG. Do not log the rejected document or any nested event-like payload.
    - Settings load logs cap integers at DEBUG once per process (counts only).
    - Use `logging.getLogger(__name__)` and `LOG_LEVEL`. Never log secrets.
  - Files: `backend/app/adaptive_score_schemas.py`, `backend/app/adaptive_score_settings.py`.

- [x] Task 2: Validate the graph without touching Composition bytes
  - Create `backend/app/services/adaptive_score_validation.py`.
  - Pure functions: `reject_embedded_note_material(payload)`, `validate_adaptive_score_graph(score) -> findings`, `bind_material_refs(score, composition | None, *, bar_count, section_ids, track_ids, motif_ids, fingerprint) -> findings`.
  - `composition is None` with any non-asset ref yields error `composition_unavailable`. Asset-only scores return `binding_status` unchecked and no composition error.
  - The binder receives an already loaded `CompositionV2` (or precomputed id sets). It does not open SQLite and does not import FastAPI.
  - Enforce duplicate ids, edge endpoints, eligibility agreement, duration bounds, loop and quantization rules, bar range vs `composition.bar_count`, unreachable-state warnings, and one shared `revision_id` across non-asset refs (`mixed_revision_targets` when they differ).
  - Section binding uses only non-null `CompositionV2Section.id` values. A null section id is not a target.
  - `strict=True` copies warnings into errors.
  - Depends on Task 1.
  - LOGGING REQUIREMENTS:
    - INFO: finding error count, warning count, material kind counts.
    - DEBUG: finding codes and target ids.
    - Do not log composition events, pitches, or the raw score body.
  - Files: `backend/app/services/adaptive_score_validation.py`.

### Phase 2: Persistence

- [x] Task 3: Alembic migration for `adaptive_scores`
  - Add `backend/app/db/alembic/versions/20260928_0018_adaptive_scores.py`.
  - `revision="20260928_0018"`, `down_revision="20260926_0017"`.
  - `upgrade` creates the table, unique name constraint, partial unique default index, and project/updated index from decision 11. `downgrade` drops indexes then the table.
  - `ON DELETE CASCADE` from `projects(id)`.
  - Follow the logging style in `20260922_0004_composer_profiles.py` (revision id, no row data).
  - LOGGING REQUIREMENTS:
    - INFO at start and end of upgrade and downgrade with `revision` and table name.
    - No composition payload in migration logs.
  - Files: `backend/app/db/alembic/versions/20260928_0018_adaptive_scores.py`.

- [x] Task 4: Adaptive score store
  - Create `backend/app/services/adaptive_score_store.py`.
  - Functions: `create_score`, `get_score`, `list_scores`, `replace_score`, `delete_score`.
  - Use the project’s existing connection helper (`PRAGMA foreign_keys=ON` already).
  - Before write, re-check the schema and call `reject_embedded_note_material`. Run `assert_no_secret_fields` and `assert_no_secret_values` on the body. Do not load a composition and do not call `bind_material_refs` here.
  - `create_score` assigns `ascore_` plus 16 hex characters, writes that id into the body, and sets `project_id` from the argument.
  - CAS: `replace_score` updates only when `document_revision` equals `expected_document_revision`, then increments it. Zero-row update raises `adaptive_score_conflict`.
  - Default swap and name uniqueness are one transaction.
  - Do not UPDATE `projects` or `composition_snapshots`. Do not import `project_store` or `project_history`.
  - Depends on Tasks 1 and 3.
  - LOGGING REQUIREMENTS:
    - INFO: operation, `project_id`, `score_id`, new `document_revision`, counts, `is_default`.
    - DEBUG: normalized name change (boolean), conflict expected vs stored revision integers.
    - ERROR: SQLite failures with code only, no `body_json`.
  - Files: `backend/app/services/adaptive_score_store.py`.

### Phase 3: HTTP

- [x] Task 5: Load binding targets and map errors
  - Add a small orchestrator `backend/app/services/adaptive_score_service.py` that:
    - Confirms the project exists via `project_store.get_project` (404 `project_not_found`).
    - Rejects mixed `revision_id` values on non-asset refs with 422 `mixed_revision_targets`.
    - Asset-only scores skip composition lookup (`binding_status: unchecked`) and still call the store.
    - Otherwise loads the working document with `normalize_project_composition(..., persist_canonical=False)` or one revision with `get_revision_detail`. Map `ProjectHistoryNotFoundError` to 404 `revision_not_found`. Null composition is 422 `composition_unavailable`.
    - Calls the pure binder, then the store.
    - Returns `binding_status` without writing a fingerprint into the body.
  - Map `PersistenceSecretError` to 422 `persistence_secret_rejected`.
  - This module may read composition rows. It must not write them and must not pass `persist_canonical=True`. It must not import routers.
  - Depends on Tasks 2 and 4.
  - LOGGING REQUIREMENTS:
    - INFO: which source was loaded (`working` or `revision`), `binding_status`, duration_ms.
    - DEBUG: fingerprint prefix only, revision id, missing target code.
    - Never log snapshot zlib bytes or event arrays.
  - Files: `backend/app/services/adaptive_score_service.py`.

- [x] Task 6: Routes and app wiring
  - Create `backend/app/routers/adaptive_scores.py` with the six routes in decision 13.
  - Call `enforce_current(project_id, "read")` for GET and validate, and `enforce_current(project_id, "write_score")` for POST, PUT, and DELETE. Do not pass `"edit"`. Map `AdaptiveScoreError` through `map_adaptive_score_error_to_http`.
  - POST returns the server-assigned `ascore_…` id. PUT reads `expected_document_revision` from the update DTO.
  - Register the router in `backend/app/main.py` next to the other `include_router` calls. Do not add business logic to `main.py`.
  - List responses omit `body_json`.
  - Depends on Task 5.
  - LOGGING REQUIREMENTS:
    - INFO per request: method, `project_id`, `score_id` when present, status class, duration_ms, counts.
    - DEBUG: validate `strict` flag and finding codes.
    - No request body dumps.
  - Files: `backend/app/routers/adaptive_scores.py`, `backend/app/main.py`.
  <!-- Commit checkpoint: tasks 5–6 -->

### Phase 4: Tests

- [x] Task 7: Schema and graph tests
  - Add `backend/tests/test_adaptive_score_schema.py` and `backend/tests/test_adaptive_score_validation.py`.
  - Cover: round-trip of exploration, suspense, combat, victory; combat has two layers with different intensity windows; a victory stinger; a cycle back to exploration.
  - Reject `events` / `pitch` / nested `composition` / `schema_version` other than `adaptive.score.v1`.
  - Reject min duration greater than max, custom quantization without a grid, a transition whose source is not in the source state’s `transition_ids`, duplicate ids, and a bar range past `bar_count`.
  - `strict=true` turns `state_unreachable` into an error; default mode keeps it a warning.
  - Binding uses an in-memory `CompositionV2` fixture. Assert the composition model dump is unchanged after validate.
  - Cover `mixed_revision_targets`, a section whose `id` is null (not a match), and `composition_unavailable` when the binder is given no composition while a non-asset ref exists. Asset-only input stays `unchecked`.
  - Depends on Tasks 1–2.
  - LOGGING REQUIREMENTS:
    - Tests capture logs and assert the composition dump and any `events` payload are absent from log text.
    - Assert INFO records contain counts and not note pitches.
  - Files: `backend/tests/test_adaptive_score_schema.py`, `backend/tests/test_adaptive_score_validation.py`.

- [x] Task 8: Store, API, and migration tests
  - Add `backend/tests/test_adaptive_score_store.py`, `backend/tests/test_adaptive_score_api.py`, and `backend/tests/test_adaptive_score_migration.py`.
  - Store/API: create two states that reference section ids on a saved project; assert `composition_json` bytes are identical after POST and PUT, including when the stored document is still `composition.v1` (in-memory migrate only); response `id` matches `ascore_` plus 16 hex and the stored body; `body_json` has no `events` key and no `expected_document_revision`; missing section is 422 `material_target_missing`; CAS mismatch is 409; second default clears the first; delete project removes the adaptive row (FK); collaboration flag off does not require `X-Mukit-Actor`. With the flag on, a viewer POST is denied and an editor POST is allowed (`write_score`).
  - API uses `TestClient` the same way other router tests do.
  - Migration: plant a database at `20260926_0017` with a composition row, upgrade to head, assert `adaptive_scores` exists and `composition_json` is byte-identical, downgrade drops only the new table, upgrade again succeeds. Assert the chain head is `20260928_0018`.
  - Run the existing `test_open_v1_after_upgrade_to_head` (it upgrades to current head) and keep it green. Do not add `composition.v5` to the open payload.
  - Depends on Tasks 3–6.
  - LOGGING REQUIREMENTS:
    - Migration and API tests assert log records contain revision / score ids and do not contain the stored composition text.
  - Files: `backend/tests/test_adaptive_score_store.py`, `backend/tests/test_adaptive_score_api.py`, `backend/tests/test_adaptive_score_migration.py`.
  <!-- Commit checkpoint: tasks 7–8 -->

### Phase 5: Documentation

- [x] Task 9: Architecture and persistence docs
  - Add `docs/adaptive-score.md` describing authority (`composition.v2` notes vs `adaptive.score.v1` references), horizontal resequencing, vertical layering, the material-ref matrix, fallback, staleness, HTTP, logging redaction, and explicit non-goals (no playback engine, no embedded events, no `composition.v5`).
  - Update `AGENTS.md` (structure blurb, entry-point rows, doc index).
  - Update `.ai-factory/ARCHITECTURE.md` with one logical-module row and a dependency bullet: adaptive services may read Composition and must not write it; `ai_agents/` must not import the store.
  - Update `docs/project-persistence.md` with the table and cascade.
  - Add a short pointer in `docs/CODEBASE_MAP.md`.
  - Add the adaptive-score axiom to `.ai-factory/RULES.md`.
  - Add commented `ADAPTIVE_SCORE_*` cap keys to `.env.example` beside the other optional settings (states, transitions, layers, stingers, body bytes).
  - Do not edit `ROADMAP.md`.
  - Depends on Tasks 1–6 so the doc names match the shipped routes.
  - LOGGING REQUIREMENTS:
    - Document the INFO/DEBUG split and the ban on logging `body_json` and note events. No runtime code in this task.
  - Files: `docs/adaptive-score.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, `.ai-factory/RULES.md`, `docs/project-persistence.md`, `docs/CODEBASE_MAP.md`, `.env.example`.
