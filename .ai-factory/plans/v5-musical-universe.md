# Implementation Plan: V5 Musical Universe

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-01

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: a Universe tab that lists entities, Theme A, variants, and usage, then an explicit reuse into a member project. Opening the tab loads the stored universe. It does not write notes
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-01 (`/aif-improve`). `C4 D4 E4 F4` transposed by `+2` is `D4 E4 F#4 G4`. The destination anchor is the source motif's first note. The destination history commit and the universe usage share one SQLite connection. Reuse CAS uses the branch fields on `DurableCommitRequest`
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next franchise milestone and does not edit `ROADMAP.md`
- Scope: a durable `musical.universe.v1` shared by one project or by the projects that join it. Themes point at canonical motif occurrences. An explicit reuse writes transformed notes into the destination `composition.v2` and appends provenance. The universe row never stores a second note list

## Roadmap Linkage
Milestone: "V5 Musical Universe"
Rationale: A character theme has to stay the same identity when another cue transforms it. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A composer can name a character, place, faction, concept, relationship, or narrative theme once, bind a Theme A to motif material that already lives in a project, and later place a transformation of that theme into another member project. The destination cue gains real notes and a motif pin. The universe stores the identity, the variant, and the usage. It does not store its own pitches.

Ship:

1. A non-playable `musical.universe.v1` with entities, themes, variants, and usage history.
2. SQLite for the universe plus a membership table. One project belongs to at most one universe. Several projects on the same universe are the project group. Deleting a project removes that membership and leaves the universe and the other projects.
3. References only: motif occurrence, harmony span, and track. Bind checks those targets on member compositions. The row rejects embedded note material.
4. An explicit reuse that reads the live source occurrence, realizes one mechanical transformation into the destination score, commits that score, and records the usage. Creative LLM motif operations stay on `POST /motifs/apply`.
5. A Universe tab and tests whose main vector carries a character theme from one project into another at `+2` semitones and proves both the notes and the trace.

Acceptance: at `4/4`, 120 bpm, and 480 ticks per quarter, Project A has a four-note original motif `C4 D4 E4 F4` with duration `480` starting at tick `0`. Theme A of a character entity binds that occurrence. Project B is a member, already has one `G4` at tick `0`, and receives the theme at bar `2` transposed by `+2`. Project B gains `D4 E4 F#4 G4` at ticks `1920, 2400, 2880, 3360`. The bar-1 `G4` and every Project A event stay canonical-equal. The new motif definition stores `musical_universe_id`, `theme_id`, and `variant_id`. The usage row stores `transpose` and `transpose_semitones` `2` and both occurrence ids. The universe `body_json` contains none of the forbidden note keys. Project A’s motif relationship stays `original`. The destination notes and the usage row commit in one SQLite transaction.

```text
member projects' composition.v2 motifs  (canonical notes and event ids)
        ↑ referenced by project_id + motif_id + occurrence_id
musical.universe.v1   (entities, Theme A, variants, provenance, usage)
        ↓ membership
one project, or several projects sharing that universe
        ↓ explicit reuse
destination composition.v2 notes + motif pin + history revision
        ↓
usage row on the universe
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. The universe document is `musical.universe.v1`. It is not a score. **Project group** is the set of `musical_universe_members` rows for one universe. There is no folder table. **Entity** is a story identity. Its `kind` is `character`, `location`, `faction`, `concept`, `relationship`, or `narrative_theme`. **Narrative theme** is that entity kind. **Theme** is a musical statement (`Theme A`) bound to one canonical motif occurrence. **Variant** is one declared transformation of that theme. **Usage** is one committed placement of a variant into a member project. **Canonical material** is `tracks[].events[]` on a member `composition.v2`, addressed by motif occurrence. **Mechanical reuse** uses `repeat`, `transpose`, `inversion`, `augmentation`, `diminution`, or `sequence`. **Cue** in the acceptance means the destination member project. Film-score `hit_points` stay cues on the picture document and are not this tab. **Commit** for reuse is the only write of destination `tracks[].events[]` on this path. Creating a theme does not copy notes.

Predecessor: `.ai-factory/plans/v5-film-scoring-agent.md` still treats a film-score `motif_ids` entry as a motif already on that one score. This plan does not change that check and does not make film-score preview fetch other projects.

## Approach Evaluation (locked)

### Part A — Where the universe lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Fields on `CompositionV2`** | Travels with the score | `extra=forbid` would reject a nested universe, and a copy inside each cue would fork the franchise | **Reject** |
| **B. A JSON blob inside `projects.composition_json` beside the score** | One row | Every score parser would have to strip it, and a second project could not share it | **Reject** |
| **C. `musical.universe.v1` in its own table, with membership rows pointing at projects** | Same sidecar shape as `adaptive.score.v1`. One document for the group. Project delete does not have to drop the franchise | A new migration and a membership rule | **Accepted** |

### Part B — How themes point at music

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Copy the occurrence events into the universe row** | The theme survives if the cue is deleted | A second note representation. The user forbade that | **Reject** |
| **B. Store pitch names, durations, and a private relative cell** | Smaller than full events | Still an independent note representation, and it drifts from the score | **Reject** |
| **C. Store `project_id`, `motif_id`, and `occurrence_id`. Resolve pitches from the member score at reuse time. Keep a source fingerprint for drift, not the notes** | The score stays canonical. Adaptive scores already reference `motif_id` this way | A deleted occurrence makes the theme unusable until the composer rebinds it | **Accepted** |

### Part C — What a project group is

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. A new folder or franchise table that owns projects** | Familiar hierarchy | Projects already have no parent. A second grouping concept would sit beside membership | **Reject** |
| **B. One universe id column on `projects`** | Simple | Sharing needs a name, a document, and a delete story. A column cannot hold entities | **Reject** |
| **C. `musical_universe_members` with primary key `project_id`. Many projects may name the same `universe_id`. The universe row has no `project_id` owner column** | One project, one universe. Many projects, one document. Project delete cascades the membership only | An orphan universe remains after the last member leaves, until an explicit universe delete | **Accepted** |

### Part D — How reuse writes the other cue

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Call `POST /motifs/apply` and hope the client saves both files** | Reuses the route | That route returns a composition in the response and never reads a second project. The client would hold both note lists | **Reject** |
| **B. Trust a client-supplied destination composition** | Easy to preview | A client could write an unrelated score and a forged usage row | **Reject** |
| **C. The server loads both stored scores, realizes a mechanical transform with `extract_relative_motif` and `transform_*`, and writes the destination history row and the universe usage on one SQLite connection** | Notes in the cue are the live theme. A failure rolls both back. Relative cells stay in memory | The open editor must be saved before reuse, or the stored branch fingerprint will not match | **Accepted** |

### Part E — How the destination motif remembers the theme

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Usage row only** | No V2 change | Opening the cue without the universe hides the identity | **Reject** |
| **B. Put `transpose` on the only destination occurrence and point `source_occurrence_id` at the other project** | Looks like today’s provenance | A definition needs exactly one `original`, and `source_occurrence_id` is not a cross-project key | **Reject** |
| **C. Same-project reuse appends a real relationship occurrence because the original already exists. Cross-project reuse creates one motif definition whose only occurrence is `original`, because that is this score’s first statement of the theme, and sets `musical_universe_id`, `theme_id`, and `variant_id` on the definition. The variant and the usage store the operation** | Existing V2 files still validate. Identity is on the motif and in the usage | The destination occurrence’s `relationship` is `original` even when the pitches are transposed. The operation is read from the variant | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Motif document | `CompositionV2MotifDefinition`, `CompositionV2MotifOccurrence`, `CompositionV2MotifTransformProvenance` in `backend/app/composition_schemas.py`. `extra=forbid`. Exactly one `original`. Occurrences store `event_ids`, not pitches. Caps at apply time: 128 definitions, 512 occurrences, 3..32 events | Theme source is an `original` occurrence. Do not invent retrograde |
| Mechanical transforms | `extract_relative_motif`, `transform_repeat`, `transform_transpose`, `transform_inversion`, `transform_augmentation`, `transform_diminution`, `transform_sequence` in `backend/app/services/composition_motif_transform.py`. `RelativeMotifNote` is documented as not persisted | Call these. Do not reimplement semitone math |
| Motif HTTP | `POST /motifs/apply` returns a composition. It does not write SQLite | Leave it for in-score creative operations. Reuse does not call it |
| Identity check | `transform_transpose` calls `verify_exact_transpose` and `verify_motif_identity`. A failed check raises `MotifTransformError` code `motif_identity_failed` inside `_materialize_transform` | Map that code to `universe_identity_failed`. Pitches come from `destination.anchor_midi + pitch_semitone_offset`, spelled by `_midi_to_pitch` |
| Cross-project search | `search_related_motifs(..., search_cross_project=True)` returns hits. It does not copy events or create a document | Leave it. Reuse is intentional, not a similarity hit |
| Sidecar store | `adaptive_scores` in `backend/app/services/adaptive_score_store.py`. CAS on `document_revision`. `reject_embedded_note_material` with `FORBIDDEN_NOTE_KEYS` | Same CAS, same forbidden keys, same secret guard |
| Forbidden keys | `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json` | Same set for universe bodies and command payloads |
| History | `RevisionOperationType` and `AI_ORIGIN_OPERATIONS`. `operation_type` is unbounded text | Add `musical-universe-theme-apply`. No Alembic for the enum |
| Access | `enforce_current(project_id, "write_score")` in `backend/app/routers/collaboration_guard.py` | Reuse and member removal of a project call it. Flag-off returns before lookup |
| Fingerprint | `composition_snapshot_fingerprint` | Source fingerprint at theme bind and at usage time |
| Tabs | `TABS` in `frontend/src/components/ComposerWorkspace.jsx` | Add `universe` / `Universe` after Motifs |
| Latest migration | `20260930_0019` in `backend/app/db/alembic/versions/20260930_0019_video_scoring.py`, `down_revision` `20260928_0018` | Next revision `20261001_0020` |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Universe document | No `musical.universe.v1` |
| Membership | A project cannot share a document with another project |
| Theme / variant / usage | Motif provenance cannot name another project |
| Cross-project realization | `apply_motif_operation` resolves the source inside the same composition |
| Motif pin | Definitions have `id` and `label` only |
| UI | No tab reads another project’s motif identity |

### Coupling risks to avoid

1. Writing note events, pitch lists, or relative cells into `body_json`.
2. Calling `apply_motif_operation`, `generate_symbolic_composition`, an LLM, or film-score preview from the reuse path.
3. Importing `musical_universe_store` or `project_store` from `ai_agents/`.
4. Importing `video_scoring_*` or `film_score_*` from the universe modules.
5. Changing film-score `motif_ids` so a preview pulls motifs from other projects.
6. Putting the universe inside `composition_json` or autosave.
7. Logging entity labels, theme labels, note arrays, prompts, or `body_json`.
8. Adding `composition.v5`, a folder table, or a new agent id.
9. Editing `ROADMAP.md`.
10. Cascading universe delete from project delete.

## Scope And Decisions

### In scope
- `musical.universe.v1`, entity, theme, variant, usage, and motif-reference models.
- Optional motif-definition pins on `composition.v2`.
- Tables `musical_universes` and `musical_universe_members`.
- Commands, validate, membership, and reuse routes.
- Universe tab.
- Vectors A through G below.
- Docs named in Task 8.

### Out of scope
- Film-score preview, spotting cues, and `hit_points` as universe entities.
- Creative operations `rhythmic_variation`, `melodic_variation`, `answer`, and `counterphrase` on the reuse route.
- Retrograde.
- Similarity search writing memberships.
- Adaptive score, neural render, mix plan, and arrangement changes.
- A client-supplied destination composition.
- Freezing notes inside the universe when the source fingerprint drifts.
- A new collaboration role.
- `ROADMAP.md`.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/musical_universe_schemas.py`. `extra=forbid`. This module does not import video, film, adaptive-score, or agent modules. It may import `MotifRelationshipKind` and `normalize_key` / `normalize_time_signature` from `composition_schemas.py`.

`MusicalUniverseV1`. Schema version `musical.universe.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `musical.universe.v1` |
| `id` | `^muniv_[0-9a-f]{16}$`. The server assigns it |
| `name` | string 1..120, stripped. Uniqueness is casefold of the trimmed name, same NFKC approach as adaptive score names |
| `entities` | 0..64 |
| `themes` | 0..64 |

`MusicalUniverseEntityV1`.

| Field | Rule |
|-------|------|
| `id` | `^ent_[0-9a-f]{8}$` |
| `kind` | `character`, `location`, `faction`, `concept`, `relationship`, `narrative_theme` |
| `label` | string 1..120 |
| `subject_entity_id`, `object_entity_id` | Required together when `kind` is `relationship`. Absent for every other kind. They differ, they exist, and neither is this entity |
| `motif_refs` | 0..16 `UniverseMotifRefV1` |
| `harmony_refs` | 0..16 `{project_id, start_tick}`. `start_tick >= 0`. No chord string |
| `track_refs` | 0..16 `{project_id, track_id}` |
| `keys` | 0..8 strings matching the existing key pattern |
| `time_signature` | optional string passed through `normalize_time_signature` |
| `texture` | optional `sparse`, `moderate`, or `dense` |
| `catalog_instrument_ids` | 0..8 strings, each 1..80 |
| `known_operations` | 0..8 mechanical relationship names, `original` excluded |

`UniverseMotifRefV1`: `project_id` 1..64, `motif_id` 1..120, `occurrence_id` 1..120. No `event_ids`.

`MusicalUniverseThemeV1`.

| Field | Rule |
|-------|------|
| `id` | `^theme_[0-9a-f]{8}$` |
| `entity_id` | an entity on this document |
| `label` | 1..120. `Theme A` is a legal label |
| `source` | `UniverseMotifRefV1` |
| `source_fingerprint` | SHA-256 hex of the source composition at bind time |
| `variants` | 0..32 |
| `usages` | 0..128 |

`MusicalUniverseVariantV1`: `id` `^var_[0-9a-f]{8}$`, `label` 1..120, `operation` one of the six mechanical names, `parameters` as below. No `pitch` key. `source_project_id`, `source_motif_id`, `source_occurrence_id` copy the theme source.

`UniverseTransformParameters`. `extra=forbid`. The same model is stored on the variant and the usage. Only the fields for `operation` are set. Any other numeric field is `universe_operation_parameters` (HTTP 422).

| Operation | Required fields | Absent fields |
|-----------|-----------------|---------------|
| `repeat` | none | `transpose_semitones`, `inversion_axis_pitch`, both time-scale fields, all three sequence fields |
| `transpose` | `transpose_semitones` int −48..48 | inversion axis, both time-scale fields, all three sequence fields |
| `inversion` | none. `inversion_axis_pitch` may be omitted; the realize path then uses the first source note as the axis, which is what `transform_inversion` does | `transpose_semitones`, both time-scale fields, all three sequence fields |
| `augmentation`, `diminution` | `time_scale_numerator` and `time_scale_denominator`, each int 1..8, both set | `transpose_semitones`, inversion axis, all three sequence fields |
| `sequence` | `sequence_steps` 1..16, `sequence_interval_semitones` −24..24, `sequence_step_ticks` `> 0` | `transpose_semitones`, inversion axis, both time-scale fields |

`MusicalUniverseUsageV1`: `id` `^use_[0-9a-f]{8}$`, `variant_id`, `destination_project_id`, `destination_motif_id`, `destination_occurrence_id`, `destination_revision_id`, `operation`, the same parameters, `source_fingerprint` at reuse time, `warning_codes` max 8.

Reject a universe body or command payload that contains any `FORBIDDEN_NOTE_KEYS` anywhere in the tree, with code `embedded_note_material` and HTTP 422. Also reject top-level `tracks`, `tempo`, and `harmony` on the universe document with `musical_universe_invalid`.

`CompositionV2MotifDefinition` gains optional `musical_universe_id`, `theme_id`, and `variant_id`. All three are present or all three are absent. Existing motifs omit them and still validate. These fields are identifiers. They are not notes.

**2. Tables**

`backend/app/db/alembic/versions/20261001_0020_musical_universe.py`. `down_revision` is `20260930_0019`.

`musical_universes`: `id`, `name`, `normalized_name`, `schema_version`, `body_json`, `document_revision`, `created_at`, `updated_at`. Unique `normalized_name`.

`musical_universe_members`: `universe_id` references `musical_universes(id)` `ON DELETE CASCADE`, `project_id` references `projects(id)` `ON DELETE CASCADE`, `created_at`. Primary key `project_id`. Index on `universe_id`.

The store is `backend/app/services/musical_universe_store.py`. It does not import `project_store` or `composition_schemas`. `body_json` passes `assert_no_secret_fields`, `assert_no_secret_values`, and `reject_embedded_note_material` before the write. CAS updates `document_revision` only when the expected revision matches. A mismatch is `musical_universe_conflict` and HTTP 409. The CAS update used by reuse accepts the caller's open `sqlite3.Connection` and does not commit it. A call without a connection opens `get_connection` itself.

Members are not copied into `body_json`. GET joins them.

**3. Commands**

`MusicalUniverseCommand` is a closed discriminated union in the schema module. `extra=forbid`. The service `backend/app/services/musical_universe_commands.py` is pure: no SQLite and no FastAPI. It returns a new document. The router persists it.

| Command | Effect |
|---------|--------|
| `create_entity` | Appends one entity. Server assigns `ent_` |
| `edit_entity` | Replaces label and reference fields of one entity. Kind stays |
| `delete_entity` | Refused with `universe_entity_in_use` when a theme still points at it |
| `create_theme` | Binds `source` after the service has checked the occurrence. The router performs the read; the pure command only accepts a bind that the service already marked valid |
| `add_variant` | Appends parameters only. It does not realize notes |
| `delete_variant` | Refused with `universe_variant_in_use` when a usage points at it |

Clients cannot POST a usage. Only reuse appends one.

`create_theme` requires the live occurrence’s `relationship` to be `original`. Anything else is `universe_theme_source_not_original`. The source `project_id` must already be a member.

**4. Bind**

`backend/app/services/musical_universe_bind.py` loads member compositions through the project store. `ai_agents/` does not import this module.

Validate checks, per reference:

- `project_id` is a member.
- Motif id and occurrence id exist, and the occurrence’s events still resolve on that track.
- Harmony `start_tick` matches one `CompositionV2HarmonyItem.start_tick`.
- `track_id` exists on that project.
- `catalog_instrument_ids` exist in `arrangement.instruments.v1`.

Failures are findings, not silent drops. `POST .../validate` writes nothing. Theme create and reuse refuse on an unresolved source with `universe_source_missing` (HTTP 409) and leave both scores unchanged.

A fingerprint that differs while the occurrence still resolves is warning `universe_source_fingerprint_drift`. Reuse continues from the live events.

**5. Reuse**

`backend/app/services/musical_universe_realize.py`.

Inputs are the theme, the operation, the parameters, the source composition, and the destination composition. It calls `extract_relative_motif` on the source and the matching `transform_*` with the destination as `composition`. It does not persist `RelativeMotifNote`. It does not import `composition_motif_editor`, `ai_agents`, LLM clients, or film modules.

`MotifDestinationSpec` uses the extracted `anchor_midi` from the source occurrence. That anchor stays the first source pitch (`C4` in the acceptance). It does not use the destination track's first event. `allow_overlap` is false. `duration_limit` is `composition.duration_ticks`, which `_materialize_transform` treats as an absolute end tick. `start_tick` is `compile_timeline(destination).bar_start_tick(destination_start_bar)`.

Placement: `destination_start_bar` is inside `1..bar_count`. Map `MotifTransformError` codes before any write:

| Transform code | Universe code | HTTP |
|----------------|---------------|------|
| `motif_destination_out_of_bounds` | `universe_destination_overflow` | 422 |
| `motif_overlap_rejected` | `universe_destination_overlap` | 422 |
| `motif_pitch_out_of_range` | `universe_pitch_out_of_range` | 422 |
| `motif_identity_failed` | `universe_identity_failed` | 422 |

More than 32 created events is `universe_transform_too_long`.

Destination track is pitched and not `is_drum` and not role `drums` or `percussion`. Violin (`G3`..`C7`) contains the acceptance pitches `D4 E4 F#4 G4`.

Same project: append one occurrence on the source motif definition with `relationship` equal to the operation and provenance `source_occurrence_id` set to the source occurrence. If the definition’s pins are empty, set them. If they name another universe or theme, `universe_motif_identity_conflict`.

Other project: create one motif definition. The server assigns `motif_` plus 8 hex and an occurrence id `occ_` plus 8 hex. Label is the theme label, with a numeric suffix if that label already exists. Sole occurrence `relationship` is `original` and `transform` is null. Pins are the universe, theme, and new variant ids. Existing destination events stay. New event ids come from the transform result.

`transform_transpose` already raises `motif_identity_failed` when `verify_exact_transpose` fails. Realize does not compare occurrences itself.

The service `backend/app/services/musical_universe_reuse.py` orders the writes inside one `get_connection` block. `get_connection` commits on a clean exit and rolls back on any exception. Do not call `commit_revision`, because that helper opens a second connection.

1. Load the universe and both projects. Refuse unless both are members.
2. Realize in memory.
3. On that connection, call `commit_durable_revision` for the destination. `operation_type` is `musical-universe-theme-apply`, added to `RevisionOperationType` and `AI_ORIGIN_OPERATIONS`. `revision_origin("musical-universe-theme-apply")` returns `ai`.
4. The commit request carries `branch_id`, `expected_active_branch_id`, `expected_working_version`, `expected_head_revision_id`, and `expected_source_fingerprint` for the destination project, plus `expected_universe_revision`. A branch CAS miss is HTTP 409 `universe_destination_conflict`. A universe revision miss is HTTP 409 `musical_universe_conflict`. Either miss leaves both the score and the usage unwritten.
5. Append the variant when the request did not name one, append the usage, and CAS the universe on the same connection. The store must not commit that connection.
6. Call `maybe_invalidate_project_embeddings` and `record_revision_activity` the way `commit_revision` does, still inside the block.
7. `AiProvenance` uses `provider` `musical-universe` and `operation` `musical-universe-theme-apply`. `generation_parameters` carry the operation name and the numeric parameter fields. They carry no pitches and no labels. `user_instruction` is omitted.

`enforce` stays in the router, before this service runs.

**6. HTTP**

`backend/app/routers/musical_universe.py`, registered from `backend/app/main.py`.

| Route | Behavior |
|-------|----------|
| `POST /musical-universes` | Body is `name` plus `project_id`. Creates the document and the first member |
| `GET /musical-universes` | Summaries: id, name, member count, revision. No `body_json` |
| `GET /musical-universes/{universe_id}` | Document plus member project ids |
| `GET /projects/{project_id}/musical-universe` | The one universe, or 404 `universe_not_linked` |
| `POST /musical-universes/{universe_id}/members` | Adds `project_id`. The other project’s current universe is `universe_project_busy` (409) |
| `DELETE /musical-universes/{universe_id}/members/{project_id}` | Removes membership. `enforce_current(..., "write_score")` |
| `DELETE /musical-universes/{universe_id}` | Deletes the document and memberships. Projects and scores stay |
| `POST /musical-universes/{universe_id}/commands` | Expected revision plus one command |
| `POST /musical-universes/{universe_id}/validate` | Findings only |
| `POST /musical-universes/{universe_id}/themes/{theme_id}/reuse` | The commit path |

Read routes use `enforce_current` with `read` on each concrete project id they touch. Reuse uses `write_score` on the destination and `read` on the source when they differ.

Errors use `map_musical_universe_error_to_http`. Detail is `{code, message}` plus scalar `details`. Responses and logs omit note arrays.

**7. UI**

`frontend/src/components/MusicalUniversePanel.jsx` and `frontend/src/api/musicalUniverseApi.js`.

The tab id is `universe`. The panel loads when it is shown. Composer mount does not GET the universe and does not POST reuse.

Empty state offers create, or join by universe id. The entity list is grouped by kind. Selecting a theme shows the source project id, motif id, occurrence id, variants, and usages. It does not render a private piano roll of copied notes.

Reuse control: operation, the matching parameters, destination project (member list, default the open project), destination track, and start bar. The button POSTs reuse only. The request has no composition body. When the destination is the open project, the body copies `activeBranchId`, `workingVersion`, `currentRevisionId`, and `workingFingerprint`, plus `expected_universe_revision`. When the destination is another member, the panel loads that project's branch fields and sends those. The button stays disabled while the open project is dirty: `saveStatus` is `saving`, `unsaved`, or `conflict`, or `projectPersistRevisionKey` differs from `lastSavedPersistRevision`, the same dirty reasons as `computeVersionRestoreBlockReason`. The panel tells the composer to save first. After a reuse into the open project, reload that project from the server. A reuse into another member leaves the open composition object unchanged.

**8. Vectors**

Shared setup unless a vector says otherwise: key `C major`, `4/4`, 120, 480 ticks per quarter, `bar_count` `4`, `duration_ticks` `7680`, one `verse` section from bar 1 covering those bars. One melody track `track_melody`, instrument string `violin`. Project A motif id `motif_theme_a`, occurrence `occ_original`, relationship `original`, event ids `a1 a2 a3 a4`, pitches `C4 D4 E4 F4`, duration `480`, starts `0 480 960 1440`. Project B event `b0` is `G4` at tick `0` and duration `480`. No other events.

| Vector | Action | Required result |
|--------|--------|-----------------|
| A | Create universe, member Project A, character entity, Theme A bound to `occ_original` | `body_json` has no forbidden note key. Project A events are canonical-equal. `source_fingerprint` equals `composition_snapshot_fingerprint` of A |
| B | Member Project B. Reuse Theme A, `transpose`, `+2`, bar `2`, `track_melody` | Events `D4 E4 F#4 G4` at `1920 2400 2880 3360`. `b0` and Project A unchanged. Pins set. Usage `transpose` / `2`. Destination revision operation is `musical-universe-theme-apply` and `revision_origin` is `ai`. Universe revision increments by 1 |
| C | Reuse into a project that is not a member | `universe_project_not_member`. Destination fingerprint unchanged. Usage count stays 0 |
| D | Delete `occ_original` from A, then reuse | `universe_source_missing`. Project B fingerprint unchanged |
| E | Delete Project A | Membership row for A is gone. Universe, Theme A, and Project B remain. GET still returns the theme |
| F | Command payload containing `events` | `embedded_note_material`. Stored revision unchanged |
| G | Add Project B to a second universe while it is already a member | `universe_project_busy` |

Vector B's bar-2 tick is `compile_timeline(...).bar_start_tick(2)`, which is `1 * 4 * 480 = 1920`. Source offsets from `C4` are `0, 2, 4, 5`. Adding 2 semitones and spelling with `_midi_to_pitch` yields `D4 E4 F#4 G4`. The destination anchor remains the source `C4`, so the existing `G4` does not shift the new pitches.

## Tasks

### Phase 1: Document and store
- [x] Task 1: Add the universe documents and motif pins
- [x] Task 2: Persist the universe and its memberships
- [x] Task 3: Bind references to member scores

### Phase 2: Theme reuse
- [x] Task 4: Edit entities, themes, and variants by command
- [x] Task 5: Realize a mechanical theme into another project

### Phase 3: HTTP and review
- [x] Task 6: Serve membership, commands, validate, and reuse
- [x] Task 7: Review the universe and reuse a theme from the tab

### Phase 4: Docs
- [x] Task 8: Document the musical universe

## Commit Plan
- **Commit 1** (after tasks 1-3): "feat: store a musical universe that points at motif occurrences"
- **Commit 2** (after tasks 4-5): "feat: reuse a character theme as a transformation in another project"
- **Commit 3** (after tasks 6-7): "feat: edit and reuse a shared musical universe from the studio"
- **Commit 4** (after task 8): "docs: describe the musical universe shared by a project group"

## Tasks (detail)

### Task 1: Add the universe documents and motif pins

Add the models in the schema decision to `backend/app/musical_universe_schemas.py`. Add `MusicalUniverseError` with `code`, `message`, and `http_status`, plus `map_musical_universe_error_to_http`. Scan forbidden keys with the same key set as `FORBIDDEN_NOTE_KEYS`. Add the three optional pin fields on `CompositionV2MotifDefinition` and the all-or-nothing validator.

`backend/tests/test_musical_universe_schemas.py` accepts a character entity and a Theme A whose source is a motif ref, with `committed` not a field and with no note keys. It rejects `tracks`, `events`, a 65th entity, a relationship entity missing `object_entity_id`, a theme source that includes `event_ids`, a partial motif pin, a universe id that fails the regex, and `transpose` parameters that also set `sequence_steps`. A composition fixture that omits the pins still validates.

LOGGING: DEBUG on schema rejection with the model name and the error code. Do not log labels or note fields. Levels follow `LOG_LEVEL`.

Files: `backend/app/musical_universe_schemas.py`, `backend/app/composition_schemas.py`, `backend/tests/test_musical_universe_schemas.py`.

### Task 2: Persist the universe and its memberships

Add the Alembic revision and `musical_universe_store.py` as specified. `delete_project` already deletes the `projects` row; the membership foreign key removes that project’s row. Do not add a file GC pass. Do not delete `musical_universes` from `delete_project`.

`backend/tests/test_musical_universe_store.py` covers create, unique normalized name, CAS conflict, and a successful revision bump. `backend/tests/test_musical_universe_migration.py` upgrades a database that is at `20260930_0019` and downgrades this revision. A store test deletes one member project and asserts the universe row and the other membership remain.

LOGGING: INFO on insert and CAS update with `universe_id`, revision, entity count, and theme count. WARNING on conflict with the code only. Do not log `body_json` or labels. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Bind references to member scores

Add `musical_universe_bind.py`. Findings use codes `universe_project_not_member`, `universe_source_missing`, `universe_harmony_missing`, `universe_track_missing`, `universe_instrument_unknown`, and warning `universe_source_fingerprint_drift`. The function returns findings and does not write.

`backend/tests/test_musical_universe_bind.py` builds the vector A composition in memory and asserts a matching original occurrence yields no errors. A harmony ref whose `start_tick` is absent yields `universe_harmony_missing`. A catalog id that is not in `arrangement.instruments.v1` yields `universe_instrument_unknown`. A drifted fingerprint with the occurrence still present is a warning only.

LOGGING: INFO bind finished with error count and warning count. DEBUG each finding code and target id. Do not log pitches, chord symbols, or labels. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 4: Edit entities, themes, and variants by command

Add `musical_universe_commands.py` with the command table. `create_theme` in this module assumes the caller already verified the original occurrence; the HTTP layer in Task 6 calls bind before the command. Deleting an entity that a theme uses raises `universe_entity_in_use`. Deleting a variant that a usage uses raises `universe_variant_in_use`.

`backend/tests/test_musical_universe_commands.py` creates a character and Theme A, rejects a source that is not shaped as a motif ref, rejects a 33rd variant, and rejects deleting the character while Theme A remains. A command dict that contains `notes` is rejected before a document is returned.

LOGGING: DEBUG command name, entity id or theme id, and resulting counts. Do not log labels. Levels follow `LOG_LEVEL`.

Depends on Tasks 1 and 3.

### Task 5: Realize a mechanical theme into another project

Add `musical_universe_realize.py` and `musical_universe_reuse.py` using the reuse decision. Add `musical-universe-theme-apply` to `RevisionOperationType` and `AI_ORIGIN_OPERATIONS`.

`backend/tests/test_musical_universe_reuse.py` runs vector B against stored projects and asserts the pitches `D4 E4 F#4 G4`, ticks `1920 2400 2880 3360`, surviving `G4`, Project A equality, pins, usage, history operation, and a universe document that still has no forbidden keys. Forcing the universe CAS to fail inside the same connection leaves the destination fingerprint unchanged. Vector C and vector D are in this file. A same-project reuse of `transpose` appends a non-original occurrence and leaves the original occurrence’s `event_ids` unchanged. `rhythmic_variation` is rejected with `universe_operation_unsupported`. `musical_universe_realize.py` does not import `composition_motif_editor` or `ai_agents`. The reuse module does not call `commit_revision`.

Update `backend/tests/test_ai_agents_architecture.py` so the persistence import ban includes `app.services.musical_universe_store`.

LOGGING: INFO reuse finished with universe id, theme id, destination project id, operation, created event count, and the 12-character destination fingerprint prefix. WARNING on `universe_identity_failed` and `universe_source_missing` with the code only. Do not log pitches or relative cells. Levels follow `LOG_LEVEL`.

Depends on Tasks 2, 3, and 4.

### Task 6: Serve membership, commands, validate, and reuse

Add the router and register it in `backend/app/main.py`. Wire `enforce_current` as specified. Command `create_theme` calls bind and refuses `universe_source_missing` and `universe_theme_source_not_original` before the command mutates the document.

`backend/tests/test_musical_universe_api.py` covers create, GET by project, join, vector G, validate, command create theme, reuse vector B through HTTP, and vector F. The reuse body carries the destination branch CAS fields and `expected_universe_revision`, and it carries no composition. A stale `expected_source_fingerprint` returns `universe_destination_conflict` and leaves both the score and the usage count unchanged. Delete universe leaves both project compositions available. List responses do not include `body_json`. `revision_origin("musical-universe-theme-apply")` is `ai`.

LOGGING: INFO each route with method, `universe_id`, `project_id`, status, and `duration_ms`. DEBUG command name. Do not log labels or bodies. Levels follow `LOG_LEVEL`.

Depends on Task 5.

### Task 7: Review the universe and reuse a theme from the tab

Add the API client, the panel, and the `universe` tab. Store the loaded universe outside `composition` so autosave cannot write it into `composition_json`.

`frontend/src/utils/musicalUniverseReuse.test.js` asserts the reuse request contains the operation, semitones, destination project, track, bar, `branch_id`, `expected_active_branch_id`, `expected_working_version`, `expected_head_revision_id`, `expected_source_fingerprint`, and `expected_universe_revision`, and that it has no `events`, `pitch`, or `composition`. A second test uses the pure helper in `frontend/src/utils/musicalUniverseReuse.js`: `saveStatus` `unsaved`, `saving`, or `conflict`, and a persist-revision mismatch, each yield disabled; a clean project yields an enabled payload. Mounting the helper must not call fetch.

LOGGING: the panel does not `console.log` labels or note arrays. API failures surface the server `code`.

Depends on Task 6.

### Task 8: Document the musical universe

Add `docs/musical-universe.md` with the terminology lock, the reference rule, membership, the reuse commit, and the statement that film-score `motif_ids` still mean motifs on that score. Link it from `AGENTS.md`, `docs/CODEBASE_MAP.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md` beside the adaptive-score lines. Do not edit `ROADMAP.md`.

LOGGING: none. This task does not add runtime logs.

Depends on Tasks 6 and 7.
