# Implementation Plan: Safe AI Preview, Version History, and Alternative Branches

Branch: main
Created: 2026-09-10

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "Safe AI experimentation and versioning"
Rationale: Extend local project persistence and every substantial AI workflow so experimentation is preview-first, recoverable, comparable, and durable across restarts. Added to `.ai-factory/ROADMAP.md` via `$aif-roadmap` sync with `plans/`.

## Goal

Make AI experimentation non-destructive by default. Every substantial AI result remains outside the canonical working composition until the user explicitly applies it, while immutable persistent revisions and named branches preserve accepted states and important alternatives. A user must be able to request three chorus variations, audition and compare all three, apply one on an alternative branch, and later return to the untouched original chorus after a process or container restart.

## Current-State Findings

- `projects.composition_json` stores one compact full V2 snapshot; there is no persistent revision, branch, server conflict token, or backend current-project concept.
- Zustand owns the current project and 900 ms autosave. Its request sequence ignores stale responses in the UI but cannot stop stale requests from overwriting SQLite.
- `project_store.update_project()` reads on one connection and later writes every mutable column on another, so rename/autosave and multi-tab races can lose data.
- In-memory undo/redo is capped at 50 and is intentionally cleared on project hydration, import, and full generation. It complements but cannot replace persistent history.
- Arrangement and Development already have non-mutating multi-candidate previews, source/candidate fingerprints, audition, explicit Apply, and one-step undo. Reharmonization has a one-candidate preview and textual changed-region metadata.
- Full generation immediately replaces the working composition and clears undo. Direct AI region edit and creative AI motif apply immediately commit their returned compositions.
- Candidate metadata already contains much of the required provider/model/range/track information, but Apply currently discards that provenance before autosave.
- Existing project fixtures are generally kilobytes to tens of kilobytes, but imports may contain up to 100,000 notes. History lists therefore must be metadata-only and snapshot bodies must be lazy-loaded.

## Architecture Decisions

### Snapshot Strategy

Use immutable, compressed, content-addressed full `composition.v2` snapshots as the authoritative revision representation, while keeping one mutable autosave draft per branch.

- Add a versioned `composition.snapshot.v1` encoding profile that recursively sorts object keys, emits compact UTF-8 JSON, and hashes `profile + canonical bytes` with SHA-256. Do not use the current unsorted serializer bytes as stable identity.
- Compress immutable snapshot bytes with Python stdlib zlib before storing them as a SQLite BLOB; record encoding/compression profile plus compressed and uncompressed byte sizes. API reads decompress and validate V2 before returning it.
- Store each unique canonical snapshot once in `composition_snapshots`; immutable revisions reference it by fingerprint/id. Use a real `composition.snapshot.v1:null` snapshot for an empty composition, so every project and branch has a non-null root revision/head. Snapshot decode/detail contracts return `CompositionV2 | null`; compare treats null as an empty source/target, restore-to-null clears the working composition, and frontend installation resets composition-dependent state without attempting V2 validation on null.
- Store a branch's latest autosaved working composition as a mutable draft on `project_branches`, guarded by a monotonic `working_version`. Autosave updates this draft and `projects.composition_json` but does not create immutable history every 900 ms.
- Create immutable revisions for project initialization, explicit Save/checkpoint, import, migration, substantial AI Apply, Apply-as-branch, and restore. If AI Apply starts from a draft different from the branch head, atomically checkpoint that exact pre-AI draft before creating the AI child revision.
- Keep `projects.composition_json` as the materialized active-branch draft during this release so existing open/export paths remain compatible and restart recovery stays cheap. `projects.current_revision_id` denotes the last durable revision, not every autosave.
- Do not make JSON Patch or domain patches authoritative. Current edits can reorder arrays, change track topology, omit stable backend event IDs, and still require full-result validation; replay chains would add corruption, compaction, migration, and conflict complexity.
- Retain operation-specific patches/summaries only as bounded metadata when useful for Compare. Add patch-only storage later only after measuring realistic database growth and replay costs.
- Deduplicate identical durable checkpoints by snapshot fingerprint and branch head. A no-op explicit Save may name the current head but does not create duplicate content/history.
- Keep history lists metadata-only, lazy-load at most two snapshot bodies for comparison, and define snapshot retention/garbage-collection metrics without implementing destructive GC in this release.

### Revision Graph

- Revisions are immutable nodes with `parent_revision_id`; each project's real empty root revision has no parent.
- Each revision records a monotonic per-project `sequence` for deterministic ordering independent of second-resolution timestamps.
- Restore never rewinds or deletes history. Restore is enabled only when the branch draft equals its durable head. If dirty, the UI requires and awaits an explicit Save checkpoint (or Cancel) before restore. The subsequent server-first restore command creates exactly one `revision-restore` child whose snapshot equals the selected historical revision, updates the branch draft/head, and returns the persisted project state.
- Naming an important version updates only a bounded nullable revision label; snapshot/content and operation provenance stay immutable.
- Fine-grained Zustand undo/redo remains session-local. After a successful server-first Apply/restore, a dedicated installer pushes exactly one prior local snapshot and installs the returned persisted state without scheduling a duplicate autosave. Undo creates a new local draft and follows normal draft persistence; branch checkout is a context switch and clears local undo/redo after unsaved work is resolved.

### Branch Semantics

- A branch is a named pointer to a revision head. Branch names are unique per project using normalized case-insensitive comparison.
- Existing projects are lazily and transactionally bootstrapped with a branch named `Original` and a root revision pointing to the canonical current composition (or the explicit empty snapshot). New projects initialize the same graph immediately.
- The project stores one persisted `active_branch_id`, materializes that branch's draft in `projects.composition_json`, and records the active branch's durable `current_revision_id`. This preserves current `/projects/{id}` and restart semantics for the local single-workspace product.
- Every draft save supplies `branch_id`, `expected_active_branch_id`, and `expected_working_version`. Every durable commit additionally supplies `expected_head_revision_id` and the exact source fingerprint.
- The backend atomically verifies `projects.active_branch_id == branch_id == expected_active_branch_id`, the branch working version, head, and source fingerprint before updating anything. Durable commands insert/deduplicate snapshots, create revisions, advance the branch, update its draft, and update the materialized project row in one transaction.
- Every commit/checkout finishes with the invariant `projects.current_revision_id == active_branch.head_revision_id` and `projects.composition_json == active_branch.working_composition_json`; enforce and test this invariant in persistence helpers.
- A stale head returns `409` with bounded IDs/sequences only. It never silently forks, overwrites, or includes composition/event payloads in the error.
- Branch checkout requires a successfully flushed draft or explicit user cancellation and includes expected active branch, working version, and head preconditions. It atomically changes `active_branch_id` and materialized composition, then rehydrates the workspace. It does not create a revision.
- Candidate Apply supports two destinations: `Apply` to the active branch, or `Apply as new branch`, which atomically creates a named branch from the preview base, commits the candidate as its first child, activates it, and preserves the source branch head.
- Store both display name and a Python-generated NFKC+casefold `normalized_name` under a unique `(project_id, normalized_name)` constraint. Do not rely on SQLite's ASCII-only `NOCASE` for Unicode identity.
- Initial scope includes list, create, rename, checkout, and Apply-as-branch. Branch deletion and merge are intentionally deferred because they are not required for safe alternatives and introduce reachability/retention policy.

### Revision Metadata

Each composition-changing revision records:

- Server UTC timestamp and monotonic project sequence.
- `operation_type`, using one centrally defined enum with stable values such as `project-create`, `manual-checkpoint`, `pre-ai-checkpoint`, `generate-apply`, `ai-region-edit-apply`, `creative-motif-apply`, `reharmonize-apply`, `development-apply`, `arrangement-apply`, `revision-restore`, `import`, and `migration`. Autosave is a draft update, not a revision operation.
- Optional AI provider and model when the operation actually used or returned them.
- Optional bounded user instruction. Before any persistence, scan all free text and nested generation metadata for exact configured provider secret values and conservative common API-key/token patterns; reject suspicious requests with a sanitized `422` rather than storing or silently redacting them. Store the user's accepted instruction, not internal prompts, chain-of-thought, provider request bodies, or credentials.
- Affected scope as bounded inclusive bar ranges and track IDs derived server-side from the committed source and target snapshots. Preserve requested/declared scope separately only for authorization checks; reject an AI commit whose actual diff escapes its authorized scope.
- Optional bounded summary/warning codes and candidate ID/fingerprint for traceability, never a full analysis report, event array, catalog override, or rejected provider payload.
- Optional revision label, separately editable by the user.

Reuse recursive forbidden-secret-field validation and add value-level credential detection for every existing and new persistence DTO, including `generation_prompt_json` instructions. Never store or log API keys, authorization headers, provider secrets, internal prompts, full compositions, event arrays, or snapshot JSON. User instruction may be stored only after bounded validation and secret rejection, and must never be logged.

### Preview Policy

The canonical `editedMusicJson` changes only after explicit Apply, restore, import confirmation, or direct manual editing.

- Full generation: retain the generated V2 as a generation candidate; show full-document affected scope, provider/model, validation warnings, audition, Compare, Apply, Apply as new branch, and Reject. Even an empty project requires explicit Apply for consistent behavior.
- AI region edit: retain the existing response composition and `replace_region` patch as a candidate instead of calling `completeAiEdit`; display selected bars/tracks, provider/model, event-count deltas, audition, Compare, Apply, Apply as new branch, and Reject.
- Creative AI motif apply: stage the returned composition as a candidate when provider/model AI mode is used. Deterministic local motif transforms can remain direct, undoable edits because they are not AI experimentation.
- Development: keep 1-4 candidates, add explicit per-candidate Reject and generic Compare, and preserve provider/model/instruction/scope metadata on Apply. Fix the `assertion.satisfied === false` verification gap and ensure real-provider prompts actually receive the bounded user instruction.
- Arrangement: keep 1-4 candidates and existing rich audition/verification, add per-candidate Reject and Apply-as-branch, and preserve candidate operation metadata. Ensure real-provider prompts receive the bounded instruction.
- Reharmonization: retain preview-first behavior, add source/candidate audition and generic Compare, strengthen proposal verification with full edit fingerprints, and preserve metadata on Apply. Label deterministic realization honestly even when an AI provider is selected.
- Reject is session-only and non-mutating. It removes one candidate (or all via existing Discard), stops its audition, and never creates a project revision.
- Any source edit, branch checkout, project switch, or fingerprint mismatch marks candidates stale or clears them. Apply rechecks project ID, branch ID, local composition revision, expected server head, source fingerprint, candidate fingerprint, and operation-specific preservation assertions.
- Apply and restore are server-first for open projects: validate locally, submit one atomic durable command, and only then install the returned state through a persisted-composition transaction that creates one local undo entry and suppresses autosave. API failure/conflict leaves the source and candidate intact. A clearly documented no-project fallback may remain local-only, but it cannot claim persistent history.

### Compare Scope

Provide a deterministic musical/structural comparison, not a graphical Git diff:

- Overall bars, duration/timeline, tempo, key, and meter changes.
- Added, removed, reordered, renamed, and reinstrumented tracks.
- Per-track event counts and event-ID matched added/removed/changed counts. Match unique IDs first; for missing/duplicate IDs use a deterministic multiset key of musical fields with stable occurrence indexing. Ignore ID-only churn for musical changed counts and report it separately as identity churn.
- Affected inclusive bar ranges derived from changed event ticks and timeline spans.
- Section, harmony, marker, motif, and expressive-metadata count changes.
- Provider/model, operation type, instruction presence, timestamp, revision/branch names, and warning codes where available.
- Compare working composition to a candidate or historical revision without assigning the target to `editedMusicJson`.
- Compare two persisted revisions by lazy-loading at most the selected pair; history list responses never include composition JSON.

## Proposed Database Schema

First make the migration runner execute each migration DDL and its `schema_migrations` insert in one explicit SQLite transaction, with recoverable failure behavior. Then create `backend/app/db/migrations/002_create_composition_history.sql` with tables/columns equivalent to the following design (exact SQLite DDL may be adjusted for migration compatibility):

```sql
CREATE TABLE composition_snapshots (
    fingerprint TEXT PRIMARY KEY,
    encoding_profile TEXT NOT NULL,
    compression_profile TEXT NOT NULL,
    payload_zlib BLOB NOT NULL,
    uncompressed_byte_size INTEGER NOT NULL,
    compressed_byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (uncompressed_byte_size >= 0),
    CHECK (compressed_byte_size >= 0)
);

CREATE TABLE project_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_revision_id TEXT,
    snapshot_fingerprint TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    name TEXT,
    operation_type TEXT NOT NULL,
    ai_provider TEXT,
    ai_model TEXT,
    user_instruction TEXT,
    affected_ranges_json TEXT NOT NULL DEFAULT '[]',
    affected_track_ids_json TEXT NOT NULL DEFAULT '[]',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, parent_revision_id)
        REFERENCES project_revisions(project_id, id),
    FOREIGN KEY (snapshot_fingerprint) REFERENCES composition_snapshots(fingerprint),
    UNIQUE (project_id, id),
    UNIQUE (project_id, sequence)
);

CREATE TABLE project_branches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    head_revision_id TEXT NOT NULL,
    created_from_revision_id TEXT,
    working_composition_json TEXT,
    working_fingerprint TEXT NOT NULL,
    working_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id, head_revision_id)
        REFERENCES project_revisions(project_id, id),
    FOREIGN KEY (project_id, created_from_revision_id)
        REFERENCES project_revisions(project_id, id),
    UNIQUE (project_id, id),
    UNIQUE (project_id, normalized_name),
    CHECK (working_version >= 0)
);
```

Add nullable `active_branch_id` and `current_revision_id` columns to `projects` for rollout/backfill compatibility, then require history bootstrap before returning a project. Because SQLite cannot add the required composite foreign keys to the existing table directly, add tested INSERT/UPDATE triggers (or transactionally rebuild `projects`) that reject cross-project active branches/revisions. Add indexes for revision pagination, project branch lookup, parent traversal, and snapshot reachability. The post-bootstrap application invariant is that both pointers are non-null and project-local.

Migration/backfill rules:

- Change `backend/app/db/connection.py` so one migration's DDL and migration-registry insert succeed or roll back together. Test injected failures after each DDL/ALTER stage and a clean retry; do not leave an unregistered partially applied migration.
- The SQL migration creates structures without rewriting existing V1/legacy source data blindly.
- `ensure_project_history()` runs inside the first project read/write/history transaction after migration, normalizes the stored composition through `project_composition`, creates the deduplicated compressed root snapshot/revision and `Original` branch with matching working draft, sets project pointers, and remains idempotent under concurrent calls.
- Migrated legacy/V1 data records `operation_type=migration` with no fabricated AI metadata. Failed canonical migration leaves both the old project row and history bootstrap unchanged.
- New project creation, duplication, and import-as-new initialize history atomically. Duplication starts a new project with one root snapshot and `Original`; it does not clone the complete source graph in this release.
- Project deletion cascades revisions/branches. Snapshot garbage collection is deferred; unreferenced content-addressed snapshots are safe but should be measurable and documented for a later maintenance task.

## Proposed API Contracts

Keep routes thin under `backend/app/routers/projects.py` and put orchestration in a new history service/store cluster.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/projects/{id}/revisions?branch_id=&limit=&before_sequence=` | Metadata-only paginated revision history |
| `GET` | `/projects/{id}/revisions/{revision_id}` | One validated `CompositionV2 | null` snapshot plus bounded revision metadata |
| `POST` | `/projects/{id}/revisions` | Server-first durable commit for explicit Save or candidate Apply |
| `PATCH` | `/projects/{id}/revisions/{revision_id}` | Name/rename an important revision only |
| `POST` | `/projects/{id}/revisions/{revision_id}/restore` | Atomically create a restore child on the active branch using expected head |
| `GET` | `/projects/{id}/branches` | List names, heads, timestamps, and active flag |
| `POST` | `/projects/{id}/branches` | Create from a revision, optionally with an initial candidate revision and checkout |
| `PATCH` | `/projects/{id}/branches/{branch_id}` | Rename a branch |
| `POST` | `/projects/{id}/branches/{branch_id}/checkout` | Flush-gated active-branch switch returning full project detail |

Extend project detail/save contracts with:

- `active_branch_id`, `active_branch_name`, `current_revision_id`, `current_revision_sequence`, `working_version`, and `working_fingerprint`.
- Autosave PATCH preconditions `branch_id`, `expected_active_branch_id`, `expected_working_version`, and source fingerprint; it updates only the active branch draft/materialized project and does not create an immutable revision.
- Durable commit/restore/branch commands additionally require `expected_head_revision_id` and bind validated operation metadata to the exact target candidate fingerprint. The service derives actual changed scope from source and target and rejects unauthorized scope drift.
- A structured `409` body with code `project_revision_conflict`, expected/current active branch, revision IDs, working versions, and current sequence. Do not include compositions or instructions.
- Rename-only PATCH updates only `name`, does not create a revision, and does not overwrite composition/generation columns.
- Explicit Save promotes the current branch draft to a durable checkpoint; no-op saves return the current head and mark `revision_created=false`. AI Apply from a dirty draft atomically creates a pre-AI checkpoint and then the AI revision before returning success.
- All server-first commands return the persisted composition, active branch/head, working version/fingerprint, created revision summaries, and `revision_created`; the frontend installs this response without issuing another autosave.

## Commit Plan

- **Commit 1** (after tasks 1-3): `feat(history): add revision and branch persistence`
- **Commit 2** (after tasks 4-6): `feat(history): expose safe revision workflows`
- **Commit 3** (after tasks 7-9): `feat(history): add compare audition and branch UX`
- **Commit 4** (after tasks 10-12): `feat(ai): make composition operations preview-first`
- **Commit 5** (after tasks 13-14): `test(history): cover alternatives restore and restart`
- **Commit 6** (after task 15): `docs: document safe previews and composition history`

## Tasks

### Phase 1: Persistence Foundation

- [ ] **Task 1: Add the composition snapshot, revision, and branch schema plus idempotent history bootstrap.**
  - Deliverables: Make each numbered migration and registry insert one recoverable transaction; create migration `002_create_composition_history.sql`; define compressed/versioned content-addressed snapshots, immutable project-local revision edges, branch drafts/heads, project active-head triggers/constraints, and indexes; implement a real empty root revision; add `ensure_project_history()` for canonical lazy backfill and atomic new-project initialization. Preserve V1/legacy fidelity and make concurrent bootstrap idempotent.
  - Files: `backend/app/db/connection.py`, `backend/app/db/migrations/002_create_composition_history.sql`, `backend/app/services/composition_snapshot_encoding.py` (new), `backend/app/services/project_history_store.py` (new), `backend/app/services/project_composition.py`, `backend/app/services/project_store.py`.
  - Tests: Extend `backend/tests/test_project_store.py`; add `backend/tests/test_composition_snapshot_encoding.py` and `backend/tests/test_project_history_store.py` for transaction failure/retry after DDL stages, migration registration, stable canonical bytes across key ordering/restart, compression round-trip, root `Original`, project-local FK/trigger rejection, deduplication, null snapshot decode/detail/restore, V1/legacy bootstrap, failed migration atomicity, cascade behavior, deterministic sequence ordering, and restart/reopen.
  - Logging: DEBUG migration/bootstrap stage, project ID, branch/revision IDs, fingerprint prefix, byte size, and dedupe result; INFO first successful bootstrap; WARN bounded migration/conflict codes; ERROR exception type and IDs. Never log snapshot JSON, event arrays, prompts, instructions, or secrets.
  - Dependencies: none.

- [ ] **Task 2: Replace lost-update-prone project writes with one atomic revision-aware transaction.**
  - Deliverables: Refactor draft autosave, durable commit, restore, checkout, and Apply-as-branch into explicit one-connection commands. Compare-and-swap expected active branch, working version, durable head, and source fingerprint as applicable; update only supplied rename fields; define `ProjectRevisionConflictError`; keep autosave as a mutable branch draft; deduplicate/promote explicit checkpoints; checkpoint a dirty pre-AI draft before AI Apply; assign project sequence atomically; assert active project materialization equals the active branch after every command.
  - Files: `backend/app/services/project_store.py`, `backend/app/services/project_history_store.py`, `backend/app/db/connection.py` if explicit transaction modes or busy handling are needed.
  - Tests: Add two-writer tests where the same active-branch/working-version/head preconditions yield one success and one conflict; cover checkout-versus-stale-save, save to an inactive branch, rename/autosave overlap, restore races, no-op saves, dirty-draft AI Apply creating exactly two ordered revisions, rollback after injected failures, and deterministic head/draft/materialized-row agreement.
  - Logging: DEBUG transaction start/check/dedupe/commit with IDs and sequence; INFO revision creation, restore, branch advance, and rename; WARN conflict code with expected/current ID prefixes; ERROR rollback stage and exception type. Do not log SQL payload values containing composition or instruction data.
  - Dependencies: Task 1.

- [ ] **Task 3: Define strict project-history schemas and domain services.**
  - Deliverables: Add one stable operation enum (including `revision-restore`) and Pydantic DTOs for bounded declared scope, AI provenance, nullable-composition revision details, revision naming, branches, checkout, durable commits, pagination, and conflicts. Add value-level credential detection against configured provider secrets and common token patterns to all persisted free text/generation metadata; reject rather than redact ambiguously. Normalize branch display names to an NFKC+casefold uniqueness key. Implement null-aware services that validate ownership/ancestry, derive actual changed bars/tracks from source/target snapshots, enforce declared AI scope, and orchestrate revision/branch commands.
  - Files: `backend/app/project_history_schemas.py` (new), `backend/app/project_schemas.py`, `backend/app/services/persistence_secret_guard.py` (new), `backend/app/services/composition_change_summary.py` (new), `backend/app/services/project_history.py` (new), `backend/app/services/project_history_store.py`.
  - Tests: Add schema/change-summary/secret-guard tests for bounds, cross-project IDs, Unicode-equivalent branch names, invalid ancestry, forbidden secret keys, exact configured secrets and representative token patterns inside instructions/generation prompts, false-positive-safe ordinary prose, actual-versus-declared scope, pagination, and no full composition in list DTOs.
  - Logging: DEBUG normalized metadata counts and service decisions; INFO named checkpoint/branch lifecycle using IDs and name length only; WARN rejected ownership, bounds, ancestry, and secret-field codes; no branch/version names, instructions, or payload bodies in logs.
  - Dependencies: Tasks 1-2.

<!-- Commit checkpoint: tasks 1-3 -->

### Phase 2: History API and Current-Project Safety

- [ ] **Task 4: Expose revision and branch APIs and extend project open/save responses.**
  - Deliverables: Add metadata list/detail/name, durable commit, restore, and branch endpoints; map not-found/validation/conflict/domain errors to sanitized `404`/`409`/`422`; return active branch/head/working fields from create/open/patch/duplicate; require working preconditions for draft autosave and stronger head/source preconditions for durable commands; preserve rename behavior without full-row writes; add OpenAPI contracts.
  - Files: `backend/app/routers/projects.py`, `backend/app/project_schemas.py`, `backend/app/project_history_schemas.py`, `backend/app/main.py` only if router registration changes.
  - Tests: Extend `backend/tests/test_project_routes.py`, `backend/tests/test_openapi_v2.py`, and `backend/tests/test_project_persistence_acceptance.py` for every endpoint, pagination, naming, branch checkout, Apply-as-branch payload, restore-as-new-revision, stale `409`, V2-only detail, no secret acceptance, and no full documents in list/conflict responses.
  - Logging: INFO route lifecycle with project/branch/revision IDs and status; DEBUG pagination counts and revision-created flag; WARN sanitized domain code for 4xx; ERROR unexpected type only. Never log request bodies, snapshot JSON, instructions, or secret-like fields.
  - Dependencies: Tasks 1-3.

- [ ] **Task 5: Add frontend history API methods and preserve structured conflicts.**
  - Deliverables: Add list/get/name/commit/restore revision and list/create/rename/checkout branch clients; extend draft PATCH with active-branch/working-version/source preconditions; preserve safe structured `409` details in a typed-by-convention API error while retaining current messages for other endpoints; URL-encode all IDs and pagination values.
  - Files: `frontend/src/api/projectApi.js`, `frontend/src/api/projectApi.test.js`.
  - Tests: Verify exact methods/paths/payloads, pagination, encoded IDs, lazy detail response, secret-free conflict parsing, and malformed error fallback.
  - Logging: DEBUG method/path, IDs, status, and request purpose only; WARN safe HTTP status/code; never log bodies, compositions, instruction text, authorization, or Axios config headers.
  - Dependencies: Task 4.

- [ ] **Task 6: Make Zustand autosave branch/head-aware and navigation-safe.**
  - Deliverables: Track active branch, durable head, working version/fingerprint, and local `compositionRevision` separately. Autosave persists only the active branch draft with compare-and-swap preconditions; explicit Save promotes a checkpoint. Stop retrying on `409` and show Reload/Save as branch choices. Internal navigation/project or branch switch awaits draft flush or offers Save/Discard/Cancel. Browser `beforeunload` warns when dirty/in-flight because async unload persistence is not guaranteed; do not claim otherwise. Invalidate all asynchronous responses by captured project/branch IDs and fix generation/direct-AI-edit stale guards while touching this lifecycle.
  - Files: `frontend/src/store/musicStore.js`, `frontend/src/utils/projectPersistRevision.js`, `frontend/src/components/ProjectComposerBar.jsx`, `frontend/src/App.jsx` or a focused unload/navigation hook if needed.
  - Tests: Extend `frontend/src/store/musicStore.project.test.js`; add draft-versus-checkpoint semantics, conflict, rename/autosave overlap, delayed stale response, project/branch switch, pending timer flush, in-flight save, Reload/Save-as-branch resolution, and unload warning behavior. Confirm previews alone never autosave or create revisions and no UI claims an unconfirmed unload save succeeded.
  - Logging: DEBUG autosave scheduling, expected/current revision prefixes, operation type, scope counts, request sequence, and navigation decision; INFO successful revision/checkout lifecycle; WARN conflict/stale-response codes; ERROR sanitized API status. Never log composition, generation prompt, user instruction, or secret values.
  - Dependencies: Tasks 4-5.

<!-- Commit checkpoint: tasks 4-6 -->

### Phase 3: Version Browser, Compare, and Branch UX

- [ ] **Task 7: Implement deterministic composition comparison utilities.**
  - Deliverables: Build a pure, source-immutable comparison for working/candidate/revision pairs covering timeline metadata, track topology/instruments, affected bars, sections, harmony, markers, motifs, and expressive counts. Match events by unique ID first, then by a documented canonical musical-field multiset key with stable occurrence indexes for missing/duplicate IDs; treat ID-only replacement as identity churn rather than a musical change. Return bounded summaries and `identical`; handle variable meter without inventing notes from harmony. Reuse existing canonical/event helpers rather than duplicating V2 rules.
  - Files: `frontend/src/utils/compositionVersionComparison.js` (new), `frontend/src/utils/compositionVersionComparison.test.js`, targeted exports from `frontend/src/utils/compositionCandidates.js` or `compositionCanonical.js` if necessary.
  - Tests: Golden cases for note pitch/timing, metadata-only edits, track add/remove/reorder/reinstrument, harmony-only differences, expressive fields, missing IDs, variable meter, bounds, identical documents, and input immutability.
  - Logging: Pure utility emits no routine logs; caller logs DEBUG comparison type and bounded change counts only, and WARN invalid-composition code without payload data.
  - Dependencies: none; align DTO terminology with Task 3.

- [ ] **Task 8: Add persistent version/branch state and safe historical audition to the store.**
  - Deliverables: Add metadata-only revision pagination, lazy detail loading for at most the selected comparison pair, branch list/selection, stale request guards, revision naming, create/rename/checkout branch, and Apply-as-branch actions. Restore is disabled while the draft is dirty and offers Save checkpoint/Cancel; after a successful checkpoint it submits one server-first `revision-restore` command, then installs the returned persisted state with one local undo entry and autosave suppression. Null restore clears composition-dependent state safely. Checkout flushes and rehydrates while clearing local undo. Add explicit `working|development|arrangement|version` playback-source resolution or a tested selector that enforces mutual exclusivity, isolated historical mixer controls, and loop reconciliation.
  - Files: `frontend/src/store/musicStore.js`, `frontend/src/utils/playbackSource.js` (new if extraction is chosen), `frontend/src/components/PlaybackControls.jsx`, `frontend/src/store/musicStore.versionHistory.test.js`, related playback utility tests.
  - Tests: Non-mutating list/select/compare/audition, lazy body loading, null revision compare/audition/restore behavior, stale project/branch response rejection, topology-safe mixer, shorter-version loop bounds, dirty-draft restore checkpoint guard, exactly one restore revision and one local undo entry, branch checkout semantics, Apply-as-branch preserving source head, conflict atomicity, and state reset on project delete/open/import/generation.
  - Logging: DEBUG metadata page/detail request IDs, audition source, restore/checkout preconditions, and bounded counts; INFO branch/revision lifecycle; WARN stale/conflict/invalid snapshot codes; never log snapshot documents, events, names, or instructions.
  - Dependencies: Tasks 5-7.

- [ ] **Task 9: Build the responsive Versions UI and branch controls.**
  - Deliverables: Add a `Versions` workspace tab and project-bar History entry point. Show active branch, branch switch/create/rename controls, draft-saved versus durable-checkpoint status, paginated chronological revisions, current-head badge, timestamp, optional revision name, operation/provider/model, safely truncated accepted instruction, affected bars/tracks, and warning codes. Support Save checkpoint, Name version, Compare working/two revisions, Play working, Audition selected, Restore with confirmation, and conflict recovery. Clearly state that autosave preserves the branch draft, explicit Save/AI Apply creates durable history, audition/compare is non-mutating, and restore creates a new version. Keep all actions keyboard-accessible and usable at 390 px without sticky transport overlap.
  - Files: `frontend/src/components/ProjectVersionsPanel.jsx` (new), `frontend/src/components/ComposerWorkspace.jsx`, `frontend/src/components/ProjectComposerBar.jsx`, optional shared panel styles following existing candidate panels.
  - Tests: Component behavior through store/E2E coverage for loading/empty/error/conflict states, radio/list selection, keyboard tabs, confirmation, branch name validation, disabled stale actions, accessibility status announcements, and narrow viewport overflow.
  - Logging: DEBUG UI action type and IDs only; INFO user-confirmed restore/checkout/branch creation; WARN disabled/conflict reason codes. Do not log displayed labels, branch names, instructions, or composition details.
  - Dependencies: Tasks 6-8.

<!-- Commit checkpoint: tasks 7-9 -->

### Phase 4: Preview-First AI Operations

- [ ] **Task 10: Introduce a shared frontend AI candidate lifecycle and make full generation preview-first.**
  - Deliverables: Define the minimal shared candidate envelope/state conventions (`candidate_id`, source/candidate fingerprints, provider/model, instruction, declared ranges/tracks, warnings, composition, status) without forcing operation-specific diagnostics into one generic schema. Change generation completion to stage a candidate instead of replacing `editedMusicJson`; show source/candidate audition, Compare, Apply, Apply as new branch, and Reject in `MusicGenerator`. Apply validates locally, sends one server-first durable commit bound to the candidate/source fingerprints, and only then installs the persisted response with one undo entry and no duplicate autosave; API failure preserves source and candidate. Capture project/branch/head/working version at request start and reject stale responses.
  - Files: `frontend/src/store/musicStore.js`, `frontend/src/components/MusicGenerator.jsx`, `frontend/src/utils/compositionCandidateLifecycle.js` (new if useful), `frontend/src/components/CompositionCandidateActions.jsx` (new only if reuse remains simple), `frontend/src/api/musicApi.js`; backend generation response/schema only if candidate/source fingerprints must be supplied server-side.
  - Tests: Extend `frontend/src/store/musicStore.generation.test.js` and project tests for non-mutating generation success, audition/Compare, Reject, Apply/undo/redo/autosave, Apply-as-branch, empty-project Apply, provider failure, source edits during request, project switch, and three-candidate interoperability where supplied by Development.
  - Logging: DEBUG generation candidate lifecycle, fingerprint prefixes, provider/model, base IDs, scope counts, and stale decisions; INFO Apply/Reject by candidate ID; WARN validation/provider/stale codes; never log generation prompts, instructions, full response, MusicXML, events, or credentials.
  - Dependencies: Tasks 6-9.

- [ ] **Task 11: Convert direct AI region edit and creative motif application to preview candidates, and make confirmed import replacement durable.**
  - Deliverables: Retain region-edit response composition/patch/provider/model as a candidate instead of immediate `completeAiEdit`; expose affected bars/tracks and event deltas; add audition, Compare, server-first Apply, Apply as new branch, and Reject. Stage creative/AI motif results similarly while leaving deterministic mechanical motif transforms direct and undoable. Add full source/candidate fingerprints and request IDs where absent; verify patch scope and candidate fingerprint again at Apply; submit bounded metadata bound to the target fingerprint and let the server derive actual committed scope. Route confirmed MIDI/MusicXML replacement of an open project's composition through the same server-first durable commit lifecycle with `operation_type=import`; import-as-new continues to initialize `Original` atomically. API failure leaves the previous composition intact.
  - Files: `backend/app/schemas.py`, `backend/app/motif_schemas.py`, `backend/app/routers/motifs.py`, `backend/app/services/llm_composition_editor.py`, `backend/app/services/composition_motif_editor.py` only as needed for candidate metadata; `frontend/src/components/AiRegionEditPanel.jsx`, `frontend/src/components/MotifPanel.jsx`, `frontend/src/components/ImportControls.jsx`, `frontend/src/store/musicStore.js`, `frontend/src/utils/compositionCandidateLifecycle.js`, `frontend/src/api/musicApi.js`, `frontend/src/api/projectApi.js`.
  - Tests: Extend `backend/tests/test_llm_composition_editing.py`, `backend/tests/test_motif_routes.py`, `backend/tests/test_llm_motif_editing.py`, `frontend/src/store/musicStore.test.js`, `musicStore.motif.test.js`, and import store/E2E tests for source immutability, exact scope, stale response/apply rejection, candidate tamper, audition, Reject, Apply one-step undo, Apply-as-branch, provider/model/instruction metadata, deterministic motif direct-edit behavior, confirmed import revision creation, import failure atomicity, and import-as-new root initialization.
  - Logging: DEBUG request/candidate IDs, fingerprint prefixes, operation mode, provider/model, range and track counts; INFO preview ready/Apply/Reject; WARN repair, preservation, stale, and validation codes; never log instructions, motif/event arrays, prompts, provider payloads, or secrets.
  - Dependencies: Task 10.

- [ ] **Task 12: Complete Compare, Reject, audition, provenance, and verification across existing preview workflows.**
  - Deliverables: Add per-candidate Reject and generic Compare to Development and Arrangement; add candidate audition and Compare to Reharmonization; make current-branch Apply server-first and add Apply-as-new-branch to all three. Submit provider/model, secret-checked bounded instruction, operation type, declared ranges/tracks, candidate ID/fingerprint, and warning codes; persist only after server-derived diff/scope verification. Fix Development's `satisfied` assertion handling, use full edit fingerprints for Reharmonization, and pass bounded user instruction text to real Development/Arrangement provider prompts. Keep deterministic Reharmonization attribution honest and avoid claiming a provider generated notes when it did not.
  - Files: `backend/app/services/llm_composition_development.py`, `backend/app/services/llm_composition_arrangement.py`, `backend/app/services/llm_reharmonizer.py`, relevant schemas; `frontend/src/components/CompositionDevelopmentPanel.jsx`, `ArrangementPanel.jsx`, `HarmonyTimelinePanel.jsx`, `frontend/src/store/musicStore.js`, `frontend/src/utils/compositionCandidates.js`, `compositionArrangementCandidates.js`, `compositionHarmony.js`.
  - Tests: Extend existing backend provider/prompt/log-hygiene suites and frontend candidate/store suites for actual instruction inclusion with bounded redaction, `satisfied=false` Apply rejection, full-fingerprint staleness, per-candidate Reject, source/candidate audition, Compare summaries, metadata handoff, Apply-as-branch, and no preview persistence before Apply.
  - Logging: DEBUG candidate counts/IDs, safe fingerprint prefixes, scope counts, operation/provider/model, and verification stage; INFO preview/Reject/Apply destination; WARN rejected-attempt and preservation codes; never log instructions, prompts, catalogs, reports, compositions, or events.
  - Dependencies: Tasks 7-11.

<!-- Commit checkpoint: tasks 10-12 -->

### Phase 5: Acceptance, Resilience, and Documentation

- [ ] **Task 13: Add backend persistence, concurrency, storage-efficiency, and restart acceptance coverage.**
  - Deliverables: Build an end-to-end backend matrix for transactional migration/retry, bootstrap, revision/branch APIs, restore, active-head/draft materialization, free-text secret rejection, concurrent active-branch/working-version/head commands, and Docker restart. Add realistic large-composition measurements that assert zlib compression, content deduplication, autosave draft overwrite without revision explosion, and bounded metadata-list responses without brittle timing thresholds. Verify preview endpoints remain stateless and never write revisions.
  - Files: `backend/tests/test_project_history_store.py`, `backend/tests/test_project_history_routes.py` (new), `backend/tests/test_project_persistence_acceptance.py`, `backend/tests/test_docker_persistence_acceptance.py`, targeted preview route suites, `scripts/v1_docker_acceptance.sh` or a versioned replacement only if needed.
  - Tests: Run targeted pytest first, then the full backend suite. Include restart with multiple branches and a restore revision; ensure no provider API keys are required and no secrets/full compositions appear in captured logs.
  - Logging: Test INFO lifecycle/IDs and assert log hygiene; enable DEBUG only for bounded diagnostics; explicitly fail on API-key values, full user instructions, snapshot JSON fragments, and event-array leakage.
  - Dependencies: Tasks 1-4 and 10-12.

- [ ] **Task 14: Add frontend unit and Playwright acceptance for three alternative choruses and original restoration.**
  - Deliverables: Add the canonical acceptance journey: open a project on `Original`; request `vary_section` for chorus bars with `candidate_count=3`; verify all three candidates leave working JSON/revision/save/undo unchanged; compare and audition each; Reject one; Apply another as branch `Darker harmony` and verify the server-first persisted response; switch to `Original`; restart/reload and verify original chorus/history remain; switch back to `Darker harmony`; restore the original chorus revision as a new child; undo/redo restore; name an important revision. Add conflict, stale response, API failure atomicity, keyboard, and 390x844 coverage.
  - Files: `frontend/e2e/project-version-history.spec.js` (new), `frontend/e2e/helpers.js`, `frontend/src/store/musicStore.versionHistory.test.js`, existing Development/Arrangement workflow specs where shared assertions belong, `frontend/src/api/projectApi.test.js`.
  - Tests: Run `npm test`, lint, build, targeted Playwright with fake LLM, then full E2E as practical. Gate Docker restart consistently with existing `RUN_PLAYWRIGHT_DOCKER_RESTART=1`; assert project restart preserves revisions, branch names, active head, and original composition.
  - Logging: E2E captures bounded action/status/ID logs for failures; assert no API keys, full prompts/instructions, compositions, MusicXML, or event arrays reach console/server logs.
  - Dependencies: Tasks 5-13.

<!-- Commit checkpoint: tasks 13-14 -->

- [ ] **Task 15: Document preview safety, revision/branch semantics, recovery, APIs, and operational limits.**
  - Deliverables: Update user and developer documentation through the mandatory docs checkpoint. Explain Preview/Compare/Audition/Apply/Reject/Undo, named versions, `Original` and alternative branches, restore-as-new-revision, autosave and branch checkout behavior, `409` recovery, snapshot-vs-patch decision, deduplication, lazy history loading, migration/backfill, restart/volume behavior, deferred branch deletion/merge/GC, and exact logging/security exclusions. Update structural maps for new modules and routes.
  - Files: `README.md`, `docs/project-persistence.md`, `docs/composition-development.md`, `docs/composition-arrangement.md`, `docs/composition-v2.md` only to clarify that history is outside V2, `docs/testing.md`, `docs/CODEBASE_MAP.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`, `AGENTS.md` if project structure changes materially.
  - Tests: Validate documented commands and API examples against OpenAPI/tests; run link/path checks available in the repository and ensure docs never include real credentials or internal provider prompts.
  - Logging: Document safe DEBUG/INFO/WARN fields and redaction rules; no new runtime logging in this task unless documentation validation reports bounded file/path errors.
  - Dependencies: Tasks 1-14.

<!-- Commit checkpoint: task 15 -->

## Verification Commands

Run commands separately so failures remain attributable:

```bash
cd backend
../.venv/bin/python -m pytest tests/test_project_store.py tests/test_project_history_store.py tests/test_project_history_schemas.py tests/test_project_history_routes.py tests/test_project_routes.py tests/test_project_persistence_acceptance.py
../.venv/bin/python -m pytest tests/test_llm_music_generation.py tests/test_llm_composition_editing.py tests/test_llm_composition_development.py tests/test_composition_development_routes.py tests/test_llm_composition_arrangement.py tests/test_composition_arrangement_routes.py tests/test_llm_motif_editing.py tests/test_motif_routes.py
../.venv/bin/python -m pytest
```

```bash
cd frontend
npm test
npm run lint
npm run build
npx playwright test e2e/project-version-history.spec.js e2e/composition-development-workflow.spec.js e2e/arrangement-workflow.spec.js
```

Run the existing opt-in Docker restart suite with its documented environment flag after targeted and full local suites pass.

## Acceptance Traceability

| Acceptance requirement | Planned proof |
|---|---|
| Substantial AI operations preview before destructive Apply | Tasks 10-12 store candidates outside `editedMusicJson`; unit/E2E assert unchanged composition, revision, undo, and autosave before Apply |
| Show changed region/tracks | Tasks 7 and 10-12 derive/display bounded bar ranges, track IDs, topology, and event deltas |
| Apply | Every candidate panel uses explicit verified Apply to active branch or new branch |
| Reject | Per-candidate Reject plus discard-all, both non-mutating and non-persistent |
| Undo | Server-first Apply/restore install one persisted composition transaction with one local undo snapshot and no duplicate autosave; tests prove undo/redo |
| Compare | Generic musical/structural comparison for working/candidate/revision and revision/revision |
| Persistent revisions/history | Tasks 1-4 use immutable SQLite revision nodes and snapshots; restart tests prove durability |
| Efficient storage | Versioned canonical zlib snapshots for meaningful checkpoints, mutable per-branch autosave drafts, content deduplication, no-op suppression, metadata pagination, lazy detail bodies, large-document tests |
| Required metadata | Strict revision DTO/table fields and Apply metadata propagation in Tasks 3, 10-12 |
| Name important versions | Revision label endpoint/store/UI in Tasks 3-9 |
| Alternative branches | Named branch heads, checkout, and Apply-as-new-branch in Tasks 1-9 |
| Never store API secrets | Recursive field-name and free-text value rejection, exact configured-secret checks, bounded fields, schema/log tests, docs exclusions |
| Clear autosave/current semantics | Mutable branch draft versus durable checkpoint, active-branch/working-version/head CAS, atomic commands, conflict state, flush-gated internal navigation, and honest unload warnings in Tasks 2 and 6 |
| Project restart preserves history | Backend Docker and frontend restart acceptance in Tasks 13-14 |
| Three alternative choruses, keep one, restore original | Task 14's `vary_section`, three-candidate audition/compare, `Darker harmony` branch, `Original` checkout, and restore-as-child journey |

## Risks and Guardrails

- **Database growth:** Compression, meaningful-checkpoint cadence, deduplication, and no-op suppression limit repeated snapshots, but divergent large revisions still consume space. Measure compressed/uncompressed bytes and revision frequency; defer retention/GC until product policy is explicit.
- **Global active branch:** The persisted active branch is appropriate for the current local single-workspace model, but multiple tabs can change it. Every save/checkout compares active branch, working version, durable head, and source fingerprint as applicable; stale tabs stop with a visible conflict rather than follow silently.
- **Candidate provenance trust:** Preview APIs are stateless, so persisted metadata is validated but supplied by the client at Apply. Store only bounded descriptive provenance and continue validating the complete V2 and expected source/head; do not treat metadata as cryptographic proof of provider execution.
- **Prompt privacy:** User instructions are required history metadata but may be sensitive. Enforce strict length/secret-key rejection, display deliberately, and never log instruction content. Do not persist internal prompts or provider payloads.
- **Snapshot compatibility:** Every detail/restore path normalizes and validates V2. History remains outside `composition.v2`; exports/playback continue to consume only `tracks[].events[]` from the selected working/candidate snapshot.
- **Preview unification:** Reuse lifecycle primitives without flattening operation-specific preservation, catalog, or harmony diagnostics into a weak generic contract.
- **Apply failure ordering:** Do not clear candidates or replace the canonical working state until local verification succeeds. If subsequent autosave conflicts, preserve the local candidate/result and offer Reload or Save as branch; never silently discard it.

## Deferred Scope

- Graphical piano-roll/Git-style note diff.
- Branch merge, rebase, deletion, or remote collaboration.
- User accounts, permissions, cloud synchronization, or multi-user attribution.
- Patch-only authoritative history or replay/compaction chains.
- Automatic retention, snapshot garbage collection, or database quotas without measured product requirements.
- Persisting rejected/session candidates by default. Alternatives become durable only through Apply, Apply as new branch, or an explicit named checkpoint.
