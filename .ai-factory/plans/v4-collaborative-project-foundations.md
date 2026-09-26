# Implementation Plan: V4 Collaborative Project Foundations

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-26
Planning depth: ultra

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra
- Refined: 2026-09-26 (`/aif-improve`, all findings applied)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request’s permissions tests, documentation, and two-collaborator acceptance path
- Scope: optional local actors, project roles, sharing, anchored comments, revision review, activity, and the existing CAS conflict path. The history graph stays the one already shipped
- Parent (reuse, do not fork): project history in `backend/app/services/project_history.py`, `backend/app/services/project_history_store.py`, and `backend/app/project_history_schemas.py`. Conflict remains HTTP 409 `project_revision_conflict`. A conflicting draft is kept by the existing `POST /projects/{id}/branches/apply-as-branch` (`apply_as_branch_command` → `apply_as_new_branch`). Restore remains `POST /projects/{id}/revisions/{revision_id}/restore`
- Also reuse: `AiProvenance` on durable commits (`provider`, `model`, `model_id`, `user_instruction`). Autonomous checkpoint approve/reject stays the co-producer flow in `.ai-factory/plans/v4-autonomous-composer-human-control.md`. This plan does not add a second scheduler or a second history graph
- Plugin `resources` (`project_read`, `project_write`, …) are install declarations. They are not these user roles

## Roadmap Linkage
Milestone: "V4 collaborative project foundations"
Rationale: Every milestone in `.ai-factory/ROADMAP.md` is checked. Sharing, anchored comments, revision review, and activity are a new optional layer on the local project store. The docs task adds the milestone unchecked. Check it only after the two-collaborator pytest passes.

## Goal

Two people can work around one composition on the same local API. Collaboration stays off unless `COLLABORATION_ENABLED` is turned on. With it on, an owner shares a project, a second person comments on a section of an AI revision, a stale save becomes a branch, and the owner approves a final revision. Older revisions stay in the graph.

```text
COLLABORATION_ENABLED=0
    existing project routes, no membership checks, no activity rows

COLLABORATION_ENABLED=1
    actor from X-Mukit-Actor (missing → local)
    │
    ▼
membership role
    │
    ├─ comment on section / bar / track / revision / render / project
    ├─ durable edit → existing CAS
    │     mismatch → 409, reload full CAS, then apply-as-branch
    └─ owner approves a revision → accepted_revision_id
          revision rows are not rewritten
```

**Acceptance one-liner:** With collaboration enabled, the owner and an editor review an AI `generate-apply` revision of `composition_v2_expressive.json`, the editor comments on section `section-1`, a stale second save returns `project_revision_conflict` and is kept with `apply-as-branch`, and the owner approves that branch revision. The AI revision, the winning head commit, and the branch revision all remain listed.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Collaboration flag** | `COLLABORATION_ENABLED`. Unset or `0` means off. `1`, `true`, `yes`, `on` mean on. Same truthy set as `LOCAL_LLM_ENABLED` |
| **Actor** | A local row in `collaboration_actors`. The seeded id is `local`, display name `Local`. The header `X-Mukit-Actor` selects an id. It is a selector on a trusted local API, not a password or session |
| **Role** | `owner`, `editor`, `commenter`, or `viewer` on one project. The request’s “commenter/viewer” is these two non-editing roles |
| **Membership** | One `(project_id, actor_id, role)` row. A project has exactly one owner |
| **Anchor** | The single `target_kind` on a comment: `project`, `revision`, `section`, `bar_range`, `track`, or `render` |
| **Review** | A row about one immutable revision: `open`, then `approved` or `rejected`. This is not an autonomous checkpoint |
| **Accepted revision** | `projects.accepted_revision_id`. Set when a review is approved. It does not replace `current_revision_id` or the working draft |
| **Activity** | One append-only row: `user_edit`, `ai_edit`, `approval`, `comment`, or `render` |
| **Human author** | `actor_id` on the activity row and, when the HTTP route passes it, on the new revision |
| **AI provenance** | Existing revision columns from `AiProvenance` (`ai_provider`, `ai_model`, and the rest). Approval does not write those columns |

## Non-goals

- Passwords, JWT, cookies, or a login screen
- Treating `X-Mukit-Actor` as a secret. Anyone who can call the local API can send an actor id. A later auth layer can bind actors to credentials
- Google-Docs presence, cursors, or websockets
- A piano-roll CRDT or any operational transform. The repository already refuses a stale `working_version` / head revision / fingerprint and can fork with `apply-as-branch`. There is no sync transport. Simultaneous low-latency score editing is outside this plan
- Rewriting `project_revisions` rows, deleting a losing revision, or auto-retrying a 409
- Moving the branch head as a side effect of Approve. Open/restore stays the existing restore route, called only when the client asks
- Changing autonomous checkpoint semantics, `autonomy_mode`, or stage reject-and-continue
- Putting an actor id into `ai_provider` or `ai_model`
- Storing comments, activity, or reviews inside `composition.v2`
- `composition.v4`, `DATASET_ROOT`, or `ai_agents/` importing the collaboration store or SQLite
- Copying plugin `resources` into this role matrix
- A Playwright journey. Acceptance is pytest plus `node:test` helpers

## Repository findings

There is no `users` table and no auth dependency. `backend/app/routers/projects.py` and `frontend/src/api/projectApi.js` are open to whoever can reach the API. `.ai-factory/plans/feature-local-project-composition-persistence.md` chose SQLite for a single-user local app. `docs/project-persistence.md` describes one local file.

Optimistic conflicts already exist. `project_branches.working_version` increments on draft save. Durable commits send `branch_id`, `expected_active_branch_id`, `expected_working_version`, `expected_head_revision_id`, and `expected_source_fingerprint`. A mismatch raises `ProjectRevisionConflictError`, mapped to 409 `project_revision_conflict` with fingerprint prefixes only. The composer bar Reload and Save as branch path is `saveConflictAsNewBranch` in `frontend/src/store/musicStore.js`.

There is no comment, review, or activity table. `user_instruction` on a revision is bounded free text, not an identity. `operation.span.v1` is a request-scoped trace, not a feed.

Section and track anchors already exist on `composition.v2`. The expressive fixture `backend/app/fixtures/composition_v2_expressive.json` has sections `section-1` and `section-2`, track `melody-1`, and `bar_count` 4. Neural renders are `neural_audio_render.job.v1` rows with `id` and optional `project_id`. Completion is `update_job_status(..., status="complete")` in `backend/app/services/neural_audio_render_store.py`.

Alembic head is `20260926_0013` in `backend/app/db/alembic/versions/20260926_0013_autonomous_control.py`.

## Approach Evaluation (locked)

### Part A — Identity

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Password or JWT accounts** | Real remote users | No account stack exists. The request says collaboration can stay optional and local | **Reject** |
| **B. Local actors plus a header, enforcement only when the flag is on** | Two named people can share a project. Default boot stays single-user | The header is not a credential | **Accepted** |
| **C. Always enforce membership** | One code path | Existing tests and a flag-less SPA would need an actor on every call | **Reject** |

**Locked:** `collaboration_settings.py` reads `COLLABORATION_ENABLED` with the same truthy set as local LLM. Off: ignore the header, skip membership checks, skip activity inserts, and return 404 `collaboration_disabled` from share, comment, review, activity, and actor routes. `GET /collaboration/status` is the exception and returns `{ "enabled": false }` so the client can hide the tab. On: missing header resolves to actor `local`. Unknown id is 401 `collaboration_actor_unknown`.

### Part B — Roles

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One combined commenter/viewer role** | Matches the bullet count literally | Permissions tests cannot show that commenting and read-only viewing differ | **Reject** |
| **B. Four roles: owner, editor, commenter, viewer** | Commenters post comments. Viewers only read. Editors edit and branch. One owner shares and approves | One extra role value | **Accepted** |

**Locked:** The owner row is created with the project. Sharing cannot grant `owner`, and DELETE cannot remove the owner (`422 collaboration_owner_required`). There is no ownership transfer in this plan.

| Action | owner | editor | commenter | viewer |
|--------|-------|--------|------------|--------|
| Read project, revisions, branches, comments, activity, renders | yes | yes | yes | yes |
| Autosave, durable commit, restore, checkout, create branch, apply-as-branch | yes | yes | | |
| Comment | yes | yes | yes | |
| Open a review | yes | yes | | |
| Approve or reject a review | yes | | | |
| Share, change role, revoke | yes | | | |
| Delete the project, rename the project | yes | | | |
| Enqueue neural render or mix apply | yes | yes | | |

A non-member gets 403 `collaboration_not_member`. A member whose role is too low gets 403 `collaboration_role_denied`.

### Part C — Conflicts

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Piano-roll CRDT** | Simultaneous note edits | No sync channel. The acceptance path is review, comment, branch, approve. A CRDT is not required | **Reject** |
| **B. New version column on `projects`** | Looks like optimistic locking | `working_version` and head revision CAS already do this | **Reject** |
| **C. Keep the 409 body and `apply-as-branch`** | The losing draft becomes a named branch. The winner’s head stays. History grows | The loser reloads the project for the full fingerprint, then calls `apply-as-branch` | **Accepted** |

**Locked:** Role is checked before CAS. A commenter receives 403 and does not reach the conflict check. Two editors still receive 409 when their expected version is stale. The server does not write the second composition onto the winner’s branch. `ProjectRevisionConflictBody` keeps fingerprint prefixes of length `SNAPSHOT_FINGERPRINT_LOG_PREFIX_LEN` (12). `expected_source_fingerprint` requires 16–128 characters, so the loser follows `saveConflictAsNewBranch`: `GET /projects/{id}` and send `active_branch_id`, `working_version`, `current_revision_id`, and the full `working_fingerprint` to `apply-as-branch`. `_materialize_active_branch` then makes that new branch active. The source branch `head_revision_id` stays the winner.

### Part D — AI proposal review

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reuse only autonomous checkpoint approve** | Already shipped | It continues a run. It is not a comment thread or a decision on an arbitrary revision | **Reject** |
| **B. Replace checkpoint approve with this review** | One button | Breaks Guided mode and the last plan | **Reject** |
| **C. `project_revision_reviews` beside the revision** | Comments and approval sit on the revision the user can open. The revision row stays immutable | One new table and a pointer column | **Accepted** |

**Locked:** Any revision except `project-create` may be reviewed. Origin is derived from `operation_type`, not stored as a second label:

- AI origin: `generate-apply`, `ai-region-edit-apply`, `creative-motif-apply`, `reharmonize-apply`, `development-apply`, `arrangement-apply`, `multi-agent-apply`, `autonomous-stage`
- Human origin: `project-create`, `manual-checkpoint`, `pre-ai-checkpoint`, `revision-restore`, `import`, `migration`

`pre-ai-checkpoint` is a human snapshot taken before an AI apply. A second open review on a revision that already has an `open` review is 409 `collaboration_review_open`. A revision that already has `approved` or `rejected` returns 409 `collaboration_review_decided`. Approve sets `accepted_revision_id` to that revision. Reject leaves `accepted_revision_id` as it was. Neither path calls `restore_revision`.

Autonomous checkpoint routes stay editor-or-owner when the flag is on, because they can still commit a score. They are not replaced by this review.

### Part E — Authorship

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Store the person in `ai_provider`** | One column | The model id is then unreadable | **Reject** |
| **B. Activity `actor_id` plus the existing provenance columns** | A person and a model can both be true for one AI apply | Callers that have no HTTP actor leave `actor_id` null | **Accepted** |

**Locked:** AI activity copies `ai_provider` and `ai_model` from the revision. The human who sent the project-route commit is `actor_id`. Autonomous stage commits that do not receive an actor leave `actor_id` null and still record `ai_edit` with the model columns. This plan does not thread the header through the autonomous scheduler.

### Part F — Where comments live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Inside `composition.v2`** | Travels with the score | Every comment bump would conflict with note edits and pollute export | **Reject** |
| **B. Sidecar tables** | The playable score stays note events. Comments survive a later edit | Anchors can dangle after the score changes | **Accepted** |

**Locked:** Validate the anchor against the revision snapshot when `revision_id` is set, otherwise against the working composition. A missing section, track, bar, or render is 422 `comment_anchor_missing` and is not stored. A later edit does not delete the comment.

## Contract

### Flag and actors

| Piece | Rule |
|-------|------|
| Setting | `collaboration_enabled()` in `backend/app/collaboration_settings.py` |
| Seed | Migration inserts actor `local` / `Local` and one owner membership per existing project |
| New project | `create_project` and `duplicate_project` take optional `actor_id` and call `ensure_owner` in the same transaction. The default is `local`, which covers `prepare_run` in `backend/app/services/autonomous_composer.py`. The projects router passes the resolved actor when the flag is on. Duplicate does not copy members, comments, or reviews |
| Create actor | `POST /collaboration/actors` body `{ "display_name": "..." }`, length 1..80. 201. Secret guard on the name |
| List actors | `GET /collaboration/actors` returns `id` and `display_name` |
| Status | `GET /collaboration/status` → `{ "enabled": true, "actor_id": "..." }` or `{ "enabled": false }` |

### Membership

`POST /projects/{id}/members` body `{ "actor_id", "role" }`. Role is `editor`, `commenter`, or `viewer`. Owner-only. A second grant for the same actor is 409 `collaboration_member_exists`, not an unmapped integrity error.

`PATCH /projects/{id}/members/{actor_id}` changes that role among the three non-owner values. Owner-only. The owner row is not patched.

`DELETE /projects/{id}/members/{actor_id}` revokes a non-owner. Owner-only.

`GET /projects/{id}/members` is any member.

`ProjectDetailResponse` gains `collaboration`. Flag off: `null`. Flag on: `{ "role", "accepted_revision_id" }`. `GET /projects` with the flag on returns only projects where the actor has a membership.

### Comments (`collaboration.comment.v1`)

`POST /projects/{id}/comments` and `GET /projects/{id}/comments`.

| Field | Rule |
|-------|------|
| `target_kind` | `project`, `revision`, `section`, `bar_range`, `track`, `render` |
| `body` | 1..2000 characters. `assert_no_secret_values` before insert. Never logged |
| `revision_id` | Required for `revision`. Optional on `section`, `bar_range`, and `track`. Must belong to the project |
| `section_id` | Required for `section`. Must exist on the anchored score |
| `start_bar`, `end_bar` | Required for `bar_range`. `end_bar >= start_bar`, both `>= 1`, `end_bar` within that score’s `bar_count` |
| `track_id` | Required for `track`. Must exist on the anchored score |
| `render_id` | Required for `render`. `neural_audio_renders.project_id` must match |

The fixture comment in the acceptance test uses `target_kind=section`, `section_id=section-1`, `revision_id` of the AI revision. A bar-range example that must pass validation is `start_bar=1`, `end_bar=2` on that same fixture. Track example: `melody-1`.

List order is newest first. Query `limit` defaults to 50, max 100. Response rows include author id, kind, anchor ids, body, and `created_at`. They omit composition JSON.

### Reviews (`collaboration.review.v1`)

| Method | Path | Who | Effect |
|--------|------|-----|--------|
| POST | `/projects/{id}/revisions/{revision_id}/reviews` | owner, editor | Insert `open`. Response includes `origin` `ai` or `human` |
| POST | `/projects/{id}/reviews/{review_id}/approve` | owner | `approved`, set `accepted_revision_id`, `decided_by_actor_id` |
| POST | `/projects/{id}/reviews/{review_id}/reject` | owner | `rejected`. Pointer unchanged |
| GET | `/projects/{id}/reviews` | any member | Newest first |

Optional `note` on approve and reject is 0..500 characters, secret-guarded, not logged. Approve and reject require status `open`. A repeat decision is 409 `collaboration_review_decided`.

### Activity (`collaboration.activity.v1`)

`GET /projects/{id}/activity`. Any member. Newest first. Default limit 50, max 100.

| `kind` | When | Human | Model |
|--------|------|-------|-------|
| `user_edit` | Durable commit, restore, or apply-as-branch whose operation is human-origin | `actor_id` if the route passed one | null |
| `ai_edit` | Same paths when the operation is AI-origin | `actor_id` if the route passed one | `ai_provider`, `ai_model` copied from the revision |
| `approval` | Approve or reject | deciding actor | null. `decision` is `approved` or `rejected` |
| `comment` | Comment insert | author | null. `summary` is the kind (`comment on section`), not the body |
| `render` | `update_job_status` first transition to `complete` for a job that has `project_id` | null | short `model_id` already stored on the job, if present |

Rows store ids and the kind. They do not store note lists, prompts, comment bodies, or WAV bytes. The flag-off path returns before any insert, including inside `commit_revision` and `update_job_status`, so a disabled database gains no activity rows.

`project_revisions.actor_id` is a nullable column. `_insert_revision` in `backend/app/services/project_history_store.py` accepts optional `actor_id`. The bootstrap `INSERT` in `ensure_project_history` omits the column so existing and bootstrap rows stay null. Project HTTP routes pass the actor when the flag is on. `ai_provider` and `ai_model` stay the provenance columns.

### Permission dependency

`authorize_project(actor_id, project_id, action)` lives in `backend/app/services/collaboration_access.py`. The pure matrix is `role_allows(role, action)` in `backend/app/services/collaboration_permissions.py` with no SQLite imports. `ai_agents/` does not import either module. Autonomous HTTP handlers in `backend/app/routers/ai_agents.py` call `authorize_project` only at the router.

When the flag is on, call `authorize_project` before the existing logic for project-scoped durable routes:

- `backend/app/routers/projects.py` (every handler)
- `backend/app/routers/neural_audio.py` when the request or the loaded row has `project_id`, including `GET` and `DELETE /renders/{render_id}` and `GET /renders/{render_id}/audio`, and the same pattern for stem ids
- `backend/app/routers/mix_plan.py` and `backend/app/routers/mix_analysis.py` when `project_id` is present
- `backend/app/routers/audio_recovery.py` when `project_id` is present
- Autonomous routes on `backend/app/routers/ai_agents.py` that load a run’s `project_id` (start, pause, resume, checkpoint approve, arrangement reject, stage retry, instruction, open, branch)

GET uses action `read`. Composition commits, restore, branch writes, and recovery bind use `write_score`. Render and mix enqueue or delete use `write_audio`. Session preview routes that do not take a project id stay open. Apply of those previews is a project revision route and is gated there.

### Errors

`map_collaboration_error_to_http` follows the other domain mappers.

| Status | Code |
|--------|------|
| 404 | `collaboration_disabled` (feature routes while the flag is off) |
| 401 | `collaboration_actor_unknown` |
| 403 | `collaboration_not_member` |
| 403 | `collaboration_role_denied` |
| 409 | `collaboration_review_open`, `collaboration_review_decided`, `collaboration_member_exists`, and the existing `project_revision_conflict` |
| 422 | `collaboration_owner_required`, `comment_anchor_missing`, `persistence_secret_rejected` |

Conflict bodies stay `ProjectRevisionConflictBody`. Do not add composition JSON.

### Panel

When `GET /collaboration/status` reports `enabled: false`, `ComposerWorkspace.jsx` does not add a tab. The existing conflict actions on `ProjectComposerBar.jsx` stay as they are.

When enabled, add a Collaborate tab beside Versions:

- Actor switcher writes the selected id into `collaborationHeaders()` used by `projectApi.js` and `musicApi.js` for project-scoped calls
- Create actor, member list, and owner-only share controls
- Comment form: kind, the fixture-shaped ids, body
- Review buttons on the selected revision: Open review (owner and editor), Approve and Reject (owner)
- Activity list with kind labels
- `data-testid="collaboration-panel"`

`role_allows` is duplicated in `frontend/src/utils/collaborationAccess.js` and covered by `node:test`. When the role is `commenter` or `viewer`, `scheduleAutosave` returns without a PATCH, and `PianoRollEditor` receives `readOnly` so pointer-downs do not write notes. A flag-off session has `collaboration === null` and keeps today’s edit behavior.

## Logging

`LOG_LEVEL` controls verbosity. Do not log comment bodies, review notes, display names, prompts, event arrays, compositions, or secret-shaped values. Actor ids and revision ids may be logged.

- INFO on share, revoke, review open, approve, reject: `project_id`, `actor_id`, `role` or `decision`, `revision_id`
- INFO on comment create: `project_id`, `actor_id`, `target_kind`, `comment_id`. Log `body_len`, not `body`
- INFO on activity insert: `kind`, `project_id`, optional `revision_id` or `render_id`
- INFO on role denial: code `collaboration_role_denied`, `action`, `role`
- DEBUG: anchor fields that are ids (`section_id`, `track_id`, bar numbers)
- WARNING: `persistence_secret_rejected` and `comment_anchor_missing`, codes only
- ERROR: exception type name only

Frontend uses `createAppLogger('collaboration')` at `VITE_LOG_LEVEL`. INFO on share and review actions with ids. Do not log the comment body.

## Commit Plan
- **Commit 1** (after tasks 1–3): "feat: add optional project roles without changing single-user routes"
- **Commit 2** (after tasks 4–6): "feat: share projects, attach comments, and review revisions"
- **Commit 3** (after tasks 7–8): "feat: record collaboration activity and cover the two-collaborator path"
- **Commit 4** (after tasks 9–10): "feat: show collaboration in the workspace and document it"

## Tasks

### Phase 1: Roles
- [x] Task 1: Add the flag, role matrix, and error codes
- [x] Task 2: Persist actors and owner memberships
- [x] Task 3: Enforce roles on durable project routes

### Phase 2: Conversation and review
- [x] Task 4: Share a project with editor, commenter, and viewer
- [x] Task 5: Attach comments to project, revision, section, bar range, track, and render
- [x] Task 6: Open, approve, and reject a revision review

### Phase 3: History of actions
- [x] Task 7: Append activity for edits, reviews, comments, and renders
- [x] Task 8: Permissions matrix and the two-collaborator acceptance test

### Phase 4: Workspace and docs
- [x] Task 9: Collaborate tab, read-only roles, and frontend tests
- [x] Task 10: Document collaboration and add the roadmap milestone

### Task 1: Add the flag, role matrix, and error codes

**Deliverable:** `role_allows` matches the action table, the flag parser accepts only the documented truthy strings, and collaboration errors map to the status codes above. No SQLite.

**Files:**
- `backend/app/collaboration_settings.py` (new)
- `backend/app/collaboration_schemas.py` (new)
- `backend/app/services/collaboration_permissions.py` (new)
- `backend/tests/test_collaboration_permissions.py` (new)

**Behavior:**
- `collaboration_enabled` is false for unset, empty, and `0`. True for `1`, `true`, `yes`, `on` in any case.
- Actions in the matrix include `read`, `write_score`, `write_audio`, `comment`, `review_open`, `review_decide`, `share`, and `delete_project`.
- Viewer is allowed only `read`. Commenter adds `comment`. Editor adds `write_score`, `write_audio`, and `review_open`. Owner adds `review_decide`, `share`, and `delete_project`.
- `CollaborationError` codes are the ones in the contract. `extra=forbid` on the DTOs.
- AI-origin and human-origin operation sets are constants here so later tasks share one classifier. Together they cover every `RevisionOperationType`. Human origin includes `project-create` and `pre-ai-checkpoint`. Reviews still reject `project-create` with `collaboration_review_not_allowed`.

**LOGGING REQUIREMENTS:**
- No logs in the pure functions.
- The settings loader logs INFO once with `enabled` true or false. It does not log the raw environment value if it is some other string; WARNING with code `collaboration_flag_unrecognized` and treat it as off.

**Depends on:** none

### Task 2: Persist actors and owner memberships

**Deliverable:** Migration `20260926_0014` adds the actor and membership tables, seeds `local`, and gives every existing project an owner membership. `GET` project still succeeds with the flag off.

**Files:**
- `backend/app/db/alembic/versions/20260926_0014_collaboration_memberships.py` (new)
- `backend/app/services/collaboration_store.py` (new)
- `backend/tests/test_collaboration_store.py` (new)

**Behavior:**
- Additive DDL only. `collaboration_actors(id, display_name, created_at)`. `project_memberships(project_id, actor_id, role, created_at)` with primary key `(project_id, actor_id)`, role CHECK of the four roles, and a foreign key to `projects` and `collaboration_actors`.
- `ALTER TABLE projects ADD COLUMN accepted_revision_id TEXT`.
- `ALTER TABLE project_revisions ADD COLUMN actor_id TEXT`.
- Backfill inserts `local` and one owner row per current project. Upgrade from `20260926_0013` in the store test.
- `ensure_owner`, `list_members`, `grant_member`, `change_role`, and `revoke_member` enforce the single-owner rules in the store, including `collaboration_owner_required`.
- Do not import `ai_agents`.

**LOGGING REQUIREMENTS:**
- INFO on migration with revision id `20260926_0014` and `membership_count`.
- INFO on grant and revoke: `project_id`, `actor_id`, `role`.
- Do not log `display_name`.

**Depends on:** Task 1

### Task 3: Enforce roles on durable project routes

**Deliverable:** With the flag off, current project pytest modules pass without sending a header. With the flag on, a non-member `GET` is 403 `collaboration_not_member`, a commenter `POST` revision is 403 `collaboration_role_denied`, and an editor stale commit is still 409 `project_revision_conflict`.

**Files:**
- `backend/app/services/collaboration_access.py` (new)
- `backend/app/services/project_store.py`
- `backend/app/services/project_history_store.py`
- `backend/app/routers/projects.py`
- `backend/app/routers/neural_audio.py`
- `backend/app/routers/mix_plan.py`
- `backend/app/routers/mix_analysis.py`
- `backend/app/routers/audio_recovery.py`
- `backend/app/routers/ai_agents.py`
- `backend/app/main.py` (no behavior beyond keeping the existing router includes)
- `backend/tests/test_collaboration_access.py` (new)

**Behavior:**
- Resolve the actor only when the flag is on. Header missing → `local`.
- `create_project` and `duplicate_project` take optional `actor_id` (default `local`) and call `ensure_owner` in the same transaction. The projects router passes the resolved actor. `prepare_run` keeps the default, so an autonomous project has a `local` owner. Duplicate does not copy members, comments, or reviews. Membership rows are written even when the flag is off. Enforcement stays flag-gated.
- `GET /projects` filters by membership only when the flag is on.
- `authorize_project` runs before CAS and before durable writes on the route list in the contract. GET uses `read`. Composition commits, restore, branch writes, and recovery bind use `write_score`. Render and mix enqueue or delete use `write_audio`. Neural render and stem routes that only have an id load the row and authorize when that row has `project_id`. Preview routes that lack a project id are unchanged.
- Extend `_insert_revision` with optional `actor_id`. The bootstrap `INSERT` in `ensure_project_history` omits the column. Editor and owner durable commits pass `actor_id` when the flag is on. No activity insert yet (Task 7).
- A 409 body stays the existing schema, including 12-character fingerprint prefixes.

**LOGGING REQUIREMENTS:**
- INFO when access is denied: `project_id`, `actor_id`, `action`, `code`.
- DEBUG when access is allowed: `action`, `role`.
- Do not log composition JSON or the header name alongside a secret. The actor id is not a secret.

**Depends on:** Task 2

### Task 4: Share a project with editor, commenter, and viewer

**Deliverable:** The owner can add each non-owner role, change it, and revoke it. A second request that grants `owner` returns 422. The status route reports the flag.

**Files:**
- `backend/app/routers/collaboration.py` (new)
- `backend/app/main.py`
- `backend/app/services/collaboration_store.py`
- `backend/tests/test_collaboration_members.py` (new)

**Behavior:**
- Mount the router from `main.py`.
- Implement status, actor create/list, and the member routes in the contract.
- Flag off: status returns `enabled: false` with 200. Actor and member routes return 404 `collaboration_disabled`.
- Unknown share target returns 401 `collaboration_actor_unknown`.
- A second grant for an actor who already has a membership returns 409 `collaboration_member_exists`.
- Revoking the owner returns 422 `collaboration_owner_required`.
- Display names pass `assert_no_secret_values`.

**LOGGING REQUIREMENTS:**
- INFO on actor create: `actor_id` and `display_name_len`.
- INFO on each membership change, as in Task 2.
- WARNING on secret rejection: the code only.

**Depends on:** Task 3

### Task 5: Attach comments to project, revision, section, bar range, track, and render

**Deliverable:** Each `target_kind` can be stored on a project that uses `composition_v2_expressive.json`. A bad section id returns 422 `comment_anchor_missing`. A viewer’s POST returns 403. A commenter can post.

**Files:**
- `backend/app/db/alembic/versions/20260926_0015_collaboration_comments.py` (new) if Task 2’s migration should stay membership-only. If Task 2 has not shipped, fold this CREATE into `20260926_0014` and drop this file. Do not create both if they would duplicate the table
- `backend/app/services/collaboration_comments.py` (new)
- `backend/app/routers/collaboration.py`
- `backend/tests/test_collaboration_comments.py` (new)

**Behavior:**
- Table `project_comments`: `id`, `project_id`, `author_actor_id`, `target_kind`, nullable anchor columns, `body`, `created_at`.
- When `revision_id` is set, load that score with `get_revision_detail` and `decode_composition_snapshot`. Otherwise use the working composition. Compare section id, track id, and bar bounds to that score. Do not add a new snapshot reader. Do not log the snapshot.
- Render anchors look up `neural_audio_renders` by id and require `project_id` to match.
- GET and POST follow the contract. Body is secret-guarded.

**LOGGING REQUIREMENTS:**
- INFO on create: `comment_id`, `project_id`, `actor_id`, `target_kind`, `body_len`.
- DEBUG: `section_id`, `track_id`, `start_bar`, `end_bar`, `render_id` when those anchors are used.
- WARNING on `comment_anchor_missing`: ids only.
- Do not log `body`.

**Depends on:** Task 4

### Task 6: Open, approve, and reject a revision review

**Deliverable:** An editor can open a review on a `generate-apply` revision and receives `origin=ai`. The owner can approve it, which sets `accepted_revision_id` and leaves `current_revision_id` unchanged. An editor approve returns 403. A second decision returns 409 `collaboration_review_decided`.

**Files:**
- `backend/app/services/collaboration_reviews.py` (new)
- `backend/app/routers/collaboration.py`
- `backend/app/db/alembic/versions/20260926_0014_collaboration_memberships.py` or the comments migration if that is where new tables landed. Prefer one new migration `20260926_0016_collaboration_reviews.py` when 0014 and 0015 already exist
- `backend/tests/test_collaboration_reviews.py` (new)

**Behavior:**
- Table `project_revision_reviews`: `id`, `project_id`, `revision_id`, `status`, `opened_by_actor_id`, `decided_by_actor_id`, `note`, `created_at`, `decided_at`.
- `project-create` returns 422 `collaboration_review_not_allowed`.
- Approve and reject run in one transaction with the pointer update. Reject does not change the pointer.
- Do not call `restore_revision` or `commit_revision`.

**LOGGING REQUIREMENTS:**
- INFO on open, approve, and reject: `review_id`, `revision_id`, `project_id`, `actor_id`, `origin`, `decision`.
- WARNING on `collaboration_review_open` and `collaboration_review_decided`: codes and ids.
- Do not log `note`.

**Depends on:** Task 5

### Task 7: Append activity for edits, reviews, comments, and renders

**Deliverable:** With the flag on, a human `manual-checkpoint` writes `user_edit`, a `generate-apply` writes `ai_edit` with the revision’s provider and model, a comment and a review write their kinds, and `update_job_status` to `complete` writes `render`. With the flag off, those same calls write zero activity rows.

**Files:**
- `backend/app/services/collaboration_activity.py` (new)
- `backend/app/services/project_history.py`
- `backend/app/services/collaboration_comments.py`
- `backend/app/services/collaboration_reviews.py`
- `backend/app/services/neural_audio_render_store.py`
- `backend/app/routers/collaboration.py` (GET activity)
- `backend/tests/test_collaboration_activity.py` (new)

**Behavior:**
- Table `project_activity` in migration `20260926_0017_collaboration_activity.py` (or the latest free revision id after Task 6): `id`, `project_id`, `kind`, `actor_id`, `revision_id`, `comment_id`, `review_id`, `render_id`, `decision`, `ai_provider`, `ai_model`, `summary`, `created_at`.
- `record_*` functions take the caller’s SQLite connection so the activity row commits with the revision, comment, review, or job update. The first lines return when the flag is off.
- Classify kind with the Task 1 origin sets. Copy provider and model. Leave `actor_id` null when the caller did not pass one.
- Render insert only on the first transition into `complete`, and only when the job has `project_id`.
- `summary` for a comment is `comment on <target_kind>`. It is not the comment body.
- GET returns the activity DTO. No composition fields.

**LOGGING REQUIREMENTS:**
- INFO per insert: `kind`, `project_id`, and the relevant id.
- DEBUG when the flag-off guard returns before insert: `kind` and `skipped=true`.
- Do not log summaries that could contain user text. The comment summary is the fixed template only.

**Depends on:** Task 6

### Task 8: Permissions matrix and the two-collaborator acceptance test

**Deliverable:** The acceptance one-liner is a pytest. A separate test walks every role against comment, commit, share, and approve. Flag-off project routes used by `backend/tests/test_project_routes.py` and `backend/tests/test_project_history_routes.py` stay green.

**Files:**
- `backend/tests/test_collaboration_acceptance.py` (new)
- `backend/tests/test_collaboration_permissions_http.py` (new)

**Behavior:**
- Enable the flag in these tests only.
- Create actor B. Owner `local` creates a project from `composition_v2_expressive.json` and durable-commits `operation_type=generate-apply` with `ai.provider` and `ai.model` set.
- Share B as `editor`.
- B posts a section comment on `section-1` and that AI `revision_id`.
- Owner and B both attempt a durable commit with the same expected version and head. One succeeds. The other receives 409 and the winner’s fingerprint is unchanged. Do not pass the 409 fingerprint prefixes as `expected_source_fingerprint`.
- The loser `GET /projects/{id}` and calls `POST /projects/{id}/branches/apply-as-branch` with `active_branch_id`, `working_version`, `current_revision_id`, the full `working_fingerprint`, and their draft composition. Response includes a new branch revision id.
- `GET /projects/{id}/branches` shows the original branch `head_revision_id` still equal to the winning revision. `active_branch_id` is the new branch. List revisions and assert the AI revision id, the winning revision id, and the branch revision id are all present.
- Owner opens a review on the AI revision (`origin=ai`) and a review on the branch revision, then approves the branch review. `accepted_revision_id` equals the branch revision. No revision id from earlier in the test disappears.
- Activity for the project includes `ai_edit`, `user_edit`, `comment`, and `approval`.
- HTTP matrix: commenter can comment and cannot commit or approve. Viewer cannot comment. Editor cannot approve or share. Outsider cannot GET the project.

**LOGGING REQUIREMENTS:**
- Tests assert status codes and ids, not log text.
- Implementation logs follow Tasks 3–7. Do not add a log of the comment body to make the test easier to debug.

**Depends on:** Task 7

### Task 9: Collaborate tab, read-only roles, and frontend tests

**Deliverable:** With collaboration disabled, the tab row has no Collaborate tab and autosave behavior is unchanged. With it enabled, the panel can switch actor, share, comment, and approve, and a viewer does not autosave.

**Files:**
- `frontend/src/components/CollaborationPanel.jsx` (new)
- `frontend/src/components/ComposerWorkspace.jsx`
- `frontend/src/components/PianoRollEditor.jsx` (read-only guard only)
- `frontend/src/utils/collaborationAccess.js` (new)
- `frontend/src/utils/collaborationAccess.test.js` (new)
- `frontend/src/api/projectApi.js`
- `frontend/src/api/musicApi.js`
- `frontend/src/store/musicStore.js`
- `frontend/src/store/musicStore.project.test.js`

**Behavior:**
- `collaborationHeaders()` adds `X-Mukit-Actor` only when an actor id is selected.
- The tab is inserted only when status `enabled` is true. Panel test id `collaboration-panel`.
- Share controls render for `share`. Approve and Reject render for `review_decide`. The comment box renders for `comment`.
- `readOnly` on the piano roll skips note mutation when the role is `commenter` or `viewer`. `scheduleAutosave` returns immediately for those roles.
- `collaboration === null` keeps the piano roll writable.
- Do not replace the conflict UI in `ProjectComposerBar.jsx`.

**LOGGING REQUIREMENTS:**
- `createAppLogger('collaboration')` INFO on share, comment submit, and review actions with ids and `body_len`.
- DEBUG when autosave is skipped because the role is read-only: `role`.
- Do not log the comment body or display name.

**Depends on:** Task 8

### Task 10: Document collaboration and add the roadmap milestone

**Deliverable:** `docs/collaboration.md` describes the flag, the four roles, anchors, reviews, activity, the 409-plus-branch path, and the split between `actor_id` and AI provenance. `AGENTS.md` lists the new modules. The roadmap shows the milestone checked only after Task 8’s pytest is green.

**Files:**
- `docs/collaboration.md` (new)
- `docs/project-persistence.md` (See Also link)
- `AGENTS.md` (structure, entry points, docs table)
- `.env.example` (`# COLLABORATION_ENABLED=0`)
- `.ai-factory/ROADMAP.md`

**Behavior:**
- State that the header is a local selector.
- State that simultaneous piano-roll CRDT editing is outside this feature.
- State that autonomous checkpoints remain the co-producer controls.
- Add the milestone under Milestones and the Completed table only after the acceptance test passes. Until then the checkbox stays open.

**LOGGING REQUIREMENTS:**
- Docs mention the INFO fields and the ban on comment bodies, review notes, and display names in logs.
- No new runtime logs in this task.

**Depends on:** Task 9
