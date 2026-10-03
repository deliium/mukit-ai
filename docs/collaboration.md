# Collaborative project foundations

Two named people can share one local project when `COLLABORATION_ENABLED` is on. The history graph is the existing revision and branch graph. Comments, reviews, and activity are sidecar rows. They are not fields of `composition.v2`.

`X-Mukit-Actor` is a local selector for a row in `collaboration_actors`. It is not a password, cookie, or session. Anyone who can call the API can send an actor id. A missing header resolves to the seeded actor `local`.

Unset, empty, or `0` leaves collaboration off: project routes skip membership checks and write no activity rows. Share, comment, review, activity, and actor routes return 404 `collaboration_disabled`. `GET /collaboration/status` still returns `{ "enabled": false }` so the workspace can hide the Collaborate tab. `1`, `true`, `yes`, and `on` turn the feature on. Any other value is treated as off and logs `collaboration_flag_unrecognized` without the raw value.

## Roles

Each project has exactly one `owner`. The owner may share `editor`, `commenter`, or `viewer`. Sharing cannot grant `owner`, and the owner row cannot be removed or patched.

| Action | owner | editor | commenter | viewer |
|--------|-------|--------|-----------|--------|
| Read the project, revisions, comments, activity | yes | yes | yes | yes |
| Autosave, durable commit, restore, branch | yes | yes | | |
| Comment | yes | yes | yes | |
| Open a review | yes | yes | | |
| Approve or reject a review | yes | | | |
| Share, change role, revoke | yes | | | |
| Train a personal composer adapter (`train_adapter`) | yes | | | |
| Train a Model Lab experiment (`train_model_lab`) | yes | | | |

A non-member receives 403 `collaboration_not_member`. A member whose role is too low receives 403 `collaboration_role_denied`. The role check runs before the existing compare-and-swap check.

Plugin `resources` (`project_read`, `project_write`, and the rest) are install declarations. They are not these roles.

## Conflicts

A stale durable save still returns HTTP 409 `project_revision_conflict`. The response carries fingerprint prefixes only. The client reloads the project and calls `POST /projects/{id}/branches/apply-as-branch` with the full working fingerprint. The losing draft becomes a branch. The winning branch head stays. Simultaneous piano-roll CRDT editing is outside this feature.

## Comments and reviews

A comment has one anchor: `project`, `revision`, `section`, `bar_range`, `track`, or `render`. The server checks that anchor against the revision snapshot when `revision_id` is set, otherwise against the working score. A missing target is 422 `comment_anchor_missing` and is not stored. Later edits do not delete the comment.

A review is a row about one immutable revision: `open`, then `approved` or `rejected`. Approve sets `projects.accepted_revision_id`. It does not change `current_revision_id`, rewrite the revision row, or restore the score. Reject leaves the accepted pointer as it was. `project-create` cannot be reviewed. Autonomous checkpoint approve and reject stay the co-producer controls on the autonomous run. This review does not continue or stop a run.

## Authorship and activity

`project_revisions.actor_id` is the person who sent the project-route commit, when the flag is on. `ai_provider` and `ai_model` stay the existing provenance columns. Approval does not write those columns. Autonomous stage commits that do not receive an actor leave `actor_id` null and can still record `ai_edit`.

Activity is append-only: `user_edit`, `ai_edit`, `comment`, `approval`, or `render`. Comment summaries are `comment on <target_kind>`. Rows store ids, not note lists, prompts, comment bodies, or audio bytes.

## Logs

INFO records actor ids, project ids, kinds, and `body_len`. DEBUG records a skipped insert when the flag is off (`skipped=true`). Logs do not include comment bodies, review notes, display names, prompts, event arrays, or compositions.
