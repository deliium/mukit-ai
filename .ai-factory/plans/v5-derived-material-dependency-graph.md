# Implementation Plan: V5 Derived Material Dependency Graph

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-02

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: a dependency graph inside the existing Universe tab. Opening the tab loads the graph and shows Theme A with its dependent assets. It does not rewrite notes, start a render, or call reuse
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-02 (`/aif-improve`). Development variations are recognized from `AiProvenance.operation`, which the apply request sets to `vary_section`. Capture also runs inside `apply_as_branch_command`. Motif edges are the occurrence diff against `get_snapshot_composition`. Edge deletes share the project and universe delete connections. A repeated stem-set completion updates the same edge. Collaboration reads use action `read`. The graph test is a `node --test` helper under `src/utils/`
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next franchise milestone and does not edit `ROADMAP.md`
- Scope: explicit dependency edges from motifs, themes, references, arrangements, variations, renders, and transcriptions. An upstream change marks affected assets stale and offers a targeted update. The graph never writes `tracks[].events[]`

## Roadmap Linkage
Milestone: "V5 Derived material dependency graph"
Rationale: A change to Theme A has to surface every variation, render, and other derived asset that may need regeneration, while leaving those assets stored as they are. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A composer can see which assets were derived from Theme A, and from motifs, arrangements, variations, renders, transcriptions, and reference conditioning. The edges are stored rows. When the upstream material changes, the graph lists every affected asset, shows it out of date, and offers one targeted update path. Nothing on that read rewrites the dependents.

Ship:

1. A non-playable edge document `musical.dependency.edge.v1` and a read model `musical.dependency.graph.v1`.
2. SQLite table `musical_dependency_edges`. Inserts refuse a directed cycle. The row stores fingerprints and ids, never pitches or event arrays.
3. Capture at the existing commit sites so a universe reuse, a motif apply, an arrangement apply, a development variation, a completed neural render, a recovery bind, and a reference-conditioned revision each leave one explicit edge.
4. An impact read for one theme. Status is computed from live fingerprints. Stale and transitively affected assets stay byte-identical until the composer uses an existing writer or an edge-only refresh.
5. An SVG graph in the Universe tab, plus tests whose main vector changes Theme A and lists Exploration variation, Combat variation, and Finale transformation as out of date.

Acceptance: at `4/4`, 120 bpm, and 480 ticks per quarter, Project A has original motif `C4 D4 E4 F4`. Theme A binds that occurrence. Three mechanical reuses write Exploration variation into Project B, Combat variation into Project C, and Finale transformation into Project D, each transposed by `+2` onto bar 2. Each reuse also inserts one `variation_of` edge in the same SQLite transaction. Then event `a4` in Project A changes from `F4` to `G4` and no reuse, render, or dependent commit runs. `GET` impact for Theme A returns those three assets with status `stale`. Project B, C, and D events stay `D4 E4 F#4 G4` at ticks `1920, 2400, 2880, 3360`. The graph payload contains none of the forbidden note keys.

```text
Theme A  (universe theme → source motif on Project A)
 ├─ variation_of → Exploration variation   (Project B notes + motif pin)
 ├─ variation_of → Combat variation        (Project C notes + motif pin)
 └─ variation_of → Finale transformation   (Project D notes + motif pin)
        └─ rendered_from → neural job      (downstream_of_stale; WAV unchanged)

motif occurrence ── motif_derived_from
arrangement revision ── arrangement_of
development revision ── variation_of
neural job / stem set ── rendered_from
recovery bind ── transcribed_from
reference project ── reference_conditioned_by
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. The stored row is `musical.dependency.edge.v1`. The assembled read model is `musical.dependency.graph.v1`. The impact read is `musical.dependency.impact.v1`. The offer is `musical.dependency.update_offer.v1`. None of these is a score. **Upstream** is the material an edge depends on. **Downstream** is the derived asset. **Fresh** means the recorded upstream fingerprint still matches the live upstream. **Stale** means that fingerprint differs. **Missing** means the upstream target cannot be resolved. **Downstream of stale** means this edge's own fingerprint still matches, and a walk reaches it from a stale edge. **Targeted update** is an offer for one edge, or an edge-row refresh. It is not a batch rewrite. **Theme**, **variant**, and **usage** keep the meanings in `.ai-factory/plans/v5-musical-universe.md`. A **variation** in the example tree is the variant label on a `variation_of` edge. Film-score `hit_points` are not graph nodes.

Predecessor: `.ai-factory/plans/v5-musical-universe.md`. Themes, variants, usages, and mechanical reuse stay the writers of destination notes. This plan adds the edge written beside that reuse. It does not change reuse pitch math, motif pins, or film-score `motif_ids`.

## Approach Evaluation (locked)

### Part A — Where edges live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Infer the tree by walking universe usages and fingerprints at read time** | No migration | Arrangement, render, transcription, and reference links are not all recoverable. The user asked for explicit edges | **Reject** |
| **B. Append edges inside `musical.universe.v1` `body_json`** | One CAS document | A render or a reference project is not a universe entity. Every render completion would bump the franchise revision. Projects without a universe could not record an edge | **Reject** |
| **C. Rows in `musical_dependency_edges`, returned as `musical.dependency.graph.v1`** | Impact is a query. Cycle check sees real edges. Universe reuse can insert the row on the connection it already holds | A new migration and a capture call at each writer | **Accepted** |

### Part B — What an upstream change does to dependents

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Re-run reuse, render, and generate for every downstream edge** | The tree stays current | The user forbade automatic rewrite. A theme edit would fan out into notes, WAVs, and LLM calls | **Reject** |
| **B. Store a `stale` boolean and update it with a background job** | Fast reads | The flag can lag the score, and a job that writes the flag is another writer | **Reject** |
| **C. Store the upstream fingerprint on the edge. Compute `fresh`, `stale`, `missing`, and `downstream_of_stale` on the impact read. Leave `composition.v2` and WAV bytes untouched** | The same idea as neural soft-stale and `universe_source_fingerprint_drift`, lifted to a graph. The read has no side effects | The read loads member scores to hash them | **Accepted** |

### Part C — Who may refresh an asset

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. `POST /edges/{id}/regenerate` calls reuse, arrangement, render, and the LLM** | One button | The dependency module becomes a second writer for every domain and can rewrite notes from a graph click | **Reject** |
| **B. Impact returns `musical.dependency.update_offer.v1` that names the existing action. The only graph writes are `accept-current` and `record-refresh`, which touch the edge row** | Theme A reuse stays `POST /musical-universes/{id}/themes/{theme_id}/reuse`. Render again stays the neural route. The graph cannot invent pitches | The composer confirms the existing control. Two extra routes exist for the edge row | **Accepted** |

### Part D — How cycles are judged

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Allow every cycle and draw it** | Nothing is refused | Regeneration would loop. "Detect cycles where inappropriate" would be a comment | **Reject** |
| **B. Allow cycles only for `reference_conditioned_by`** | Mutual influence is imaginable | A cycle still has no safe regeneration order | **Reject** |
| **C. All six types share one directed graph. A new edge whose upstream already reaches the new downstream is `dependency_cycle`. Endpoint kinds that cannot be that type are `dependency_endpoint_kind`. Sibling edges and diamonds stay legal** | Theme A can have three variations. A variation cannot become the parent of Theme A. The stored graph is a DAG, so the SVG layout has layers | A rejected edge rolls back the transaction that tried to insert it | **Accepted** |

### Part E — How the studio draws the tree

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Add reactflow, cytoscape, or elk** | Pan and zoom | `frontend/package.json` has no graph library. A new dependency for a tree the acceptance already describes is extra weight | **Reject** |
| **B. A nested list only** | Accessible | The user asked for graph visualization | **Reject** |
| **C. An SVG inside the Universe tab. `layoutDependencyGraph` places each node once, with depth as the longest path from the queried theme. No new package** | Matches Theme A, Exploration, Combat, Finale. Opening the tab only GETs. Stale nodes show "Out of date" | Hand-written layout, capped at the edge limit | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Universe document | `musical.universe.v1` in `backend/app/musical_universe_schemas.py`. Theme holds `source` (`project_id`, `motif_id`, `occurrence_id`) and `source_fingerprint`. Usages hold destination motif, occurrence, revision, operation, and the live fingerprint at reuse | Theme identity stays here. The graph does not copy usages into `body_json` |
| Drift | `bind_universe_references` and `reuse_theme` compare `composition_snapshot_fingerprint` with `theme.source_fingerprint` and append warning `universe_source_fingerprint_drift`. Neither rewrites the theme hash or the destination notes on a later edit | Impact uses the same snapshot function against the edge's stored fingerprint |
| Reuse transaction | `reuse_theme` in `backend/app/services/musical_universe_reuse.py` commits the destination score, usage, and activity on one `get_connection` | Insert the `variation_of` edge on that connection before the block exits |
| Motif provenance | `CompositionV2MotifTransformProvenance.source_occurrence_id` and `MotifRelationshipKind` in `backend/app/composition_schemas.py`. Apply route returns a composition. The client commits `creative-motif-apply` through `commit_revision` | Capture `motif_derived_from` inside that commit |
| Arrangement and development | Client commits `arrangement-apply` and `development-apply` through `commit_revision` and through `apply_as_branch_command` (`backend/app/services/project_history.py`). `AiProvenance.operation` exists. The development apply payload does not set it today, so `continue`, `add_section`, and `vary_section` share one `operation_type` | Set `ai.operation` from `developmentOperation` on both requests. Record `variation_of` only when that value is `vary_section`. Capture on both commit paths |
| Neural soft-stale | `neural_audio_renders.source_fingerprint` and `isNeuralRenderStale` in `frontend/src/utils/compositionSnapshotFingerprint.js`. The WAV stays downloadable | Capture `rendered_from` when status becomes `complete`. Leave the existing badge in place |
| Recovery bind | `bind_audio_recovery_job` writes alignment on an open connection in `backend/app/services/audio_recovery/pipeline.py`. `AssetWriteResult` keeps `sha256_prefix` only. The full digest exists on the in-memory payload inside `write_durable_asset_bytes` | Hash that payload at bind time and store the 64-hex digest on the edge. Impact re-hashes the stored asset file |
| Reference | `CompositionReferenceProvenanceV1` (`composition.reference_provenance.v1`) nests a fingerprint prefix under generation parameters | At commit time, hash the live reference project with `composition_snapshot_fingerprint` and store the full digest on the edge |
| Snapshot hash | `composition_snapshot_fingerprint` and `snapshot_fingerprint_log_prefix` in `backend/app/services/composition_snapshot_encoding.py` | Whole-score fingerprints use this. Same-project motif edges use a new `motif.occurrence.v1` digest |
| Graph UI | Universe tab is `MusicalUniversePanel.jsx`. Lists entities, themes, and a reuse form. No graph package in `frontend/package.json` | Add the SVG under the theme list |
| Latest migration | `20261001_0020` in `backend/app/db/alembic/versions/20261001_0020_musical_universe.py`, `down_revision` `20260930_0019` | Next revision `20261002_0021` |
| Agent boundary | `backend/tests/test_ai_agents_architecture.py` forbids `from app.services.musical_universe_store` | Add the dependency store to that tuple |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Edge row | No table names a derived asset's upstream |
| Impact | Drift is a warning on bind or on one usage. It does not list Exploration, Combat, and Finale together |
| Cycle check | Relationship entities have subject and object ids. Nothing walks derivation edges |
| Render / transcription / reference edges | Fingerprints exist on those assets and are not graph neighbors of a theme |
| Graph view | The Universe tab does not draw dependents |

### Coupling risks to avoid

1. Writing note events, pitch lists, relative cells, or WAV bytes into the edge table or the graph response.
2. Calling `reuse_theme`, `realize_mechanical_theme`, `commit_revision`, a neural enqueue, a transcription engine, or an LLM from the impact read or from `accept-current`.
3. Importing `musical_dependency_store` or `musical_universe_store` from `ai_agents/`.
4. Importing `film_score_*` or `video_scoring_*` from the dependency modules.
5. Replacing universe usages or motif pins with edges. Both stay. The edge is additional.
6. Logging variant labels, entity labels, prompts, note arrays, or full fingerprints. Log `snapshot_fingerprint_log_prefix` only.
7. Adding `composition.v5`, a folder table, a graph npm dependency, or a new agent id.
8. Editing `ROADMAP.md`.
9. Auto-rewriting Project B, C, or D when Project A's motif changes.
10. Treating a frozen history revision as stale because a later edit changed the live score. Same-project `arrangement_of` and revision-rooted `variation_of` stay `fresh` while that revision row exists.

## Scope And Decisions

### In scope
- Edge, graph, impact, and update-offer schemas.
- Table `musical_dependency_edges`, cycle refusal, and caps.
- Capture for the six dependency types at the writers named below.
- Impact for a theme, project graph GET, `accept-current`, and `record-refresh`.
- Universe-tab SVG and tests.
- Vectors A through H.

### Out of scope
- Regenerating notes, WAVs, or LLM output from the graph module.
- Mic transcription that never binds a recovery asset. That path has no durable audio hash to be upstream.
- Film-score cues, adaptive-score transition edges, mix-plan graphs, and plugin graphs.
- A new workspace tab. The graph section lives in the Universe tab.
- Changing `theme.source_fingerprint` when the source score drifts.
- `ROADMAP.md`.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/musical_dependency_schemas.py`. `extra=forbid`. This module does not import video, film, adaptive-score, agent, or store modules. It may import `reject_embedded_note_material` logic only as a local copy of `FORBIDDEN_NOTE_KEYS`: `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`. A payload that contains any of those keys anywhere is `embedded_note_material` (HTTP 422).

`MusicalDependencyEdgeV1`. Schema version `musical.dependency.edge.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `musical.dependency.edge.v1` |
| `id` | `^dep_[0-9a-f]{16}$`. The server assigns it with `secrets.token_hex(8)` |
| `dependency_type` | `motif_derived_from`, `arrangement_of`, `variation_of`, `rendered_from`, `transcribed_from`, `reference_conditioned_by` |
| `upstream_kind` | `theme`, `motif_occurrence`, `revision`, `project`, `audio_asset` |
| `downstream_kind` | `motif_occurrence`, `revision`, `project`, `neural_render`, `neural_stem_set` |
| `universe_id` | optional `^muniv_[0-9a-f]{16}$` |
| `upstream_project_id`, `downstream_project_id` | optional strings 1..64 |
| `upstream_theme_id` | optional `^theme_[0-9a-f]{8}$` |
| `variant_id` | optional `^var_[0-9a-f]{8}$`. Present for a universe reuse edge |
| `motif_id`, `occurrence_id` | optional strings 1..120. Set on the downstream when the downstream is a motif occurrence |
| `source_motif_id`, `source_occurrence_id` | optional strings 1..120. Set when the upstream is a motif occurrence |
| `upstream_revision_id`, `downstream_revision_id` | optional strings 1..80 |
| `downstream_asset_id` | optional string 1..80. Neural job id or recovery source asset id |
| `upstream_fingerprint` | `^[0-9a-f]{64}$` |
| `downstream_fingerprint` | optional `^[0-9a-f]{64}$` |
| `created_at` | server timestamp |

`MusicalDependencyGraphV1`. Schema version `musical.dependency.graph.v1`. `nodes` max 256. `edges` max 256. A node has `node_key`, `kind`, `label` 0..120, and the ids needed to address it. A graph edge echoes the dependency type, the two node keys, and a status. No event arrays.

`MusicalDependencyImpactV1`. Schema version `musical.dependency.impact.v1`. Fields: `theme_id`, `live_source_fingerprint`, `dependents` max 256. Each dependent has `edge_id`, `dependency_type`, `label`, `node_key`, `status` (`fresh`, `stale`, `missing`, `downstream_of_stale`), and `update_offer`.

`MusicalDependencyUpdateOfferV1`. Schema version `musical.dependency.update_offer.v1`.

| `dependency_type` | `action` | What the UI does with it |
|-------------------|----------|--------------------------|
| `variation_of` with `universe_id` and `variant_id` | `reuse_theme` | Selects that variant in the existing reuse form. The existing reuse button remains the only note writer |
| `motif_derived_from` | `open_motif` | Selects the Motifs tab. No apply call on graph mount |
| `arrangement_of` | `open_arrangement` | Selects the Arrange tab |
| `variation_of` without a universe id | `open_development` | Selects the Develop tab |
| `rendered_from` | `open_neural_render` | Selects the existing neural panel. Does not POST a render |
| `transcribed_from` | `open_transcription` | Selects recovery review. Does not re-bind |
| `reference_conditioned_by` | `open_reference` | Names the reference `project_id` for the existing conditioning controls |
| unresolved target | `review_only` | No route |

Endpoint kinds:

| Type | Upstream kind | Downstream kind |
|------|---------------|-----------------|
| `motif_derived_from` | `motif_occurrence` or `theme` | `motif_occurrence` |
| `arrangement_of` | `revision` or `motif_occurrence` | `revision` |
| `variation_of` | `theme` or `revision` | `motif_occurrence` or `revision` |
| `rendered_from` | `project` | `neural_render` or `neural_stem_set` |
| `transcribed_from` | `audio_asset` | `project` |
| `reference_conditioned_by` | `project` | `project`, and the project ids differ |

A mismatch is `dependency_endpoint_kind` (HTTP 422). A self-edge is `dependency_cycle`.

**2. Fingerprints**

| Edge | Value stored in `upstream_fingerprint` | Read-time comparison |
|------|----------------------------------------|----------------------|
| Universe `variation_of` | `composition_snapshot_fingerprint` of the theme source project at reuse | Live hash of that same project |
| Cross-project `motif_derived_from` | Whole-score hash of the source project | Live hash of that project |
| Same-project `motif_derived_from` | New helper `motif_occurrence_fingerprint`: SHA-256 of profile `motif.occurrence.v1`, a NUL, and canonical JSON of the source occurrence's events in event-id order | Recompute from the live score. Unrelated edits on other events stay `fresh`. A missing event id is `missing` |
| `arrangement_of` and revision-rooted `variation_of` | Fingerprint already stored on the upstream revision | `fresh` while the revision row exists, `missing` when it does not. A later edit of the live score does not flip these |
| `rendered_from` | The job's existing `source_fingerprint` | Live `composition_snapshot_fingerprint` of that project |
| `transcribed_from` | Full sha256 of the bound source asset bytes | Re-hash the stored asset file. Absent file is `missing` |
| `reference_conditioned_by` | Live whole-score hash of the reference project at commit time | Live hash of that reference project later |

`downstream_of_stale` is a walk, not a hash mismatch. Cap the walk at 512 edges. A theme's direct variations become `stale` when Project A changes. A render whose upstream is Project B stays hash-fresh and is reported `downstream_of_stale` because it is reachable from a stale variation. The WAV stays.

**3. Tables**

`backend/app/db/alembic/versions/20261002_0021_musical_dependency.py`. `down_revision` is `20261001_0020`.

`musical_dependency_edges`: the columns of `MusicalDependencyEdgeV1`, plus indexes on `universe_id`, `upstream_theme_id`, `upstream_project_id`, and `downstream_project_id`. No foreign key to `projects` or `musical_universes`. Deletes are explicit so an upstream project can disappear and leave the edge visible as `missing`.

Caps: 256 edges that touch one `project_id` (either side), 512 edges that share one `universe_id`. The next insert past a cap is `dependency_graph_limit` (HTTP 422) and writes nothing.

The store is `backend/app/services/musical_dependency_store.py`. It does not import `project_store`, `musical_universe_store`, or `composition_schemas`. `record_dependency_edge(connection, edge)` inserts on the caller's open connection and does not commit it. A call without a connection opens `get_connection` and commits. Before insert it runs the kind check, the forbidden-key scan, and the cycle walk. Cycle: from the proposed upstream node, follow stored edges backward (rows whose downstream node key is the current node). If the proposed downstream node key appears, raise `dependency_cycle` and insert nothing.

Unique key: `dependency_type` plus the upstream node key plus the downstream node key. When that key already exists, `record_dependency_edge` updates `upstream_fingerprint` on the existing row and does not insert a second edge. A fingerprint update still refuses the write when the new fingerprint would close a cycle.

`delete_edges_for_downstream_project(conn, project_id)` runs inside the `with get_connection` block in `delete_project`, before `DELETE FROM projects`. It removes rows whose `downstream_project_id` is that project. Rows that only name it as upstream remain. `delete_universe` deletes rows whose `universe_id` matches on the same connection, before the `DELETE FROM musical_universes`. Member scores stay. Do not add a foreign key that removes upstream-only rows.

Node keys are stable strings:

- theme: `theme:{universe_id}:{theme_id}`
- motif: `motif:{project_id}:{motif_id}:{occurrence_id}`
- revision: `revision:{project_id}:{revision_id}`
- project: `project:{project_id}`
- audio: `audio:{asset_id}`
- neural render: `render:{job_id}`
- stem set: `stems:{stem_set_id}`

**4. Capture**

`backend/app/services/musical_dependency_capture.py` builds edge models and calls `record_dependency_edge`. It does not realize notes.

| Writer | Type | Transaction |
|--------|------|-------------|
| `reuse_theme`, after usage update, same `conn` | one `variation_of` from the theme to the destination motif occurrence. `variant_id` is the usage's variant. `upstream_fingerprint` is the live source hash already computed in `reuse_theme` | Failure, including `dependency_cycle`, rolls back the destination score and the usage |
| `commit_revision` and `apply_as_branch_command` when `operation_type` is `creative-motif-apply` | one `motif_derived_from` per occurrence that is absent from `get_snapshot_composition(conn, expected_source_fingerprint)` and whose `transform.source_occurrence_id` is set | Same connection as `commit_durable_revision` or `apply_as_new_branch`. Cycle rolls back the commit |
| `commit_revision` and `apply_as_branch_command` when `operation_type` is `arrangement-apply` | one `arrangement_of` from the previous revision (`expected_source_fingerprint`, `expected_head_revision_id`) to the new head revision | Same connection |
| `commit_revision` and `apply_as_branch_command` when `operation_type` is `development-apply` and `request.ai.operation` is `vary_section` | one `variation_of` from that previous revision to the new revision | Same connection. `continue` and `add_section` do not record a `variation_of` edge. Do not infer the operation from `summary_json` |
| `commit_revision` and `apply_as_branch_command` when `generation_parameters` names a reference `project_id` | one `reference_conditioned_by`. Hash that project inside the transaction | If the reference project row is absent, log WARNING `dependency_reference_unresolved` and still commit the score. Do not store a prefix as if it were a full hash |
| `run_neural_audio_job` where `update_job_status` sets `complete` | one `rendered_from` from the project to the job, fingerprint copied from `source_fingerprint` when it matches `^[0-9a-f]{64}$` | Same connection as the status write. A fingerprint that is not 64 hex logs WARNING `dependency_fingerprint_unusable`, leaves the job `complete`, and skips the edge. The WAV file is already written before this transaction |
| `update_stem_set_status(..., status="complete")` inside `run_stem_set` and inside the selective rerender success path | one `rendered_from` whose downstream kind is `neural_stem_set` | Same connection as that status write. A second completion of the same stem set updates `upstream_fingerprint` on the existing row. Do not record an edge from `update_stem_status` of one stem |
| `bind_audio_recovery_job`, after the alignment row | one `transcribed_from` from the source asset to the project. `upstream_fingerprint` is sha256 of the source payload passed to `write_durable_asset_bytes`, not `sha256_prefix` | Same `conn` as the bind |

Capture does not run for autosave, for `musical-universe-theme-apply` inside `commit_durable_revision` (reuse records the edge itself), or for mic transcription.

**5. Impact and edge refresh**

`backend/app/services/musical_dependency_impact.py` loads edges, universe theme source, and member compositions. It returns `MusicalDependencyImpactV1`. It does not open a write connection. Labels come from the variant label when `variant_id` resolves, otherwise from the dependency type. A deleted variant still returns the edge with the variant id as the label.

`accept-current` sets `upstream_fingerprint` to the fingerprint the impact read would compute now. `record-refresh` does that only when the request's `observed_downstream_fingerprint` equals the live downstream hash for that downstream kind: `composition_snapshot_fingerprint` of the project, or `motif_occurrence_fingerprint` when the downstream is a motif occurrence. A mismatch is `dependency_refresh_conflict` (HTTP 409) and the row stays. Neither route loads a generative model or calls reuse. A project or occurrence that cannot be loaded is status `missing` on the impact read. It is not an exception.

**6. HTTP**

Router `backend/app/routers/musical_dependency.py`, registered in `backend/app/main.py`. Reads use `enforce_current(..., "read")`. There is no `read_score` action. Before the impact or graph load, the route lists the project ids named by the edges and calls `enforce_current` with `read` on each of them. `accept-current` and `record-refresh` use `write_score` on the downstream project. Collaboration flag-off returns before lookup, matching `collaboration_guard.py`.

| Method | Path | Effect |
|--------|------|--------|
| `GET` | `/musical-universes/{universe_id}/themes/{theme_id}/dependents` | Impact. No writes |
| `GET` | `/musical-universes/{universe_id}/dependency-graph` | Graph rooted at that universe's themes |
| `GET` | `/projects/{project_id}/dependency-graph` | Edges that touch the project |
| `POST` | `/musical-dependency/edges/{edge_id}/accept-current` | Updates one fingerprint column |
| `POST` | `/musical-dependency/edges/{edge_id}/record-refresh` | Updates one fingerprint column when the observed downstream hash matches |

There is no `POST` that inserts an edge from the client.

**7. UI**

`frontend/src/utils/dependencyGraphLayout.js` is a pure layer layout. `frontend/src/components/DependencyGraph.jsx` renders one SVG. `MusicalUniversePanel.jsx` mounts it under the theme list when the tab is shown, and requests the universe graph. Composer mount does not GET the graph. The panel does not put the graph into `composition` or into autosave.

Theme A is the root. Exploration variation, Combat variation, and Finale transformation are children. Status `stale` shows the text `Out of date`. Status `downstream_of_stale` shows `May need regeneration`. A control labeled `Review update` calls a callback the panel supplies; the graph does not call `reuseMusicalUniverseTheme`. `Accept current` POSTs the edge route and then reloads the graph. No `console.log` of labels or notes.

**8. Vectors**

Shared setup unless a vector says otherwise: key `C major`, `4/4`, 120, 480 ticks per quarter, `bar_count` `4`, `duration_ticks` `7680`, one `verse` section. Track `track_melody`. Project A motif `motif_theme_a`, occurrence `occ_original`, relationship `original`, event ids `a1 a2 a3 a4`, pitches `C4 D4 E4 F4`, duration `480`, starts `0 480 960 1440`. Projects B, C, and D each start with one `G4` at tick `0`, duration `480`. Theme A of a character entity binds `occ_original`. Variant labels are `Exploration variation`, `Combat variation`, and `Finale transformation`. Each reuse is `transpose` `+2` at bar 2 on `track_melody`.

Bar-2 tick is `compile_timeline(...).bar_start_tick(2)` = `1920`. Source offsets from `C4` are `0, 2, 4, 5`. Adding 2 semitones spells `D4 E4 F#4 G4` through the existing realize path.

| Vector | Action | Required result |
|--------|--------|-----------------|
| A | Three reuses, B then C then D | Three `variation_of` edges. Graph nodes use those variant labels. Each destination has `D4 E4 F#4 G4` at `1920 2400 2880 3360` and keeps its `G4`. Project A stays `C4 D4 E4 F4`. Status `fresh`. Graph JSON has no forbidden note key |
| B | Change Project A `a4` from `F4` to `G4`. Do not reuse | Impact lists the three variation assets `stale` and, when a completed render of B exists, that job `downstream_of_stale`. B, C, and D events stay as in A. The render row stays `complete` and no second job is created. A's `a4` is `G4` |
| C | Insert an edge whose upstream is Project B and whose downstream is Theme A | `dependency_cycle`. The three edges and all four scores stay as they were |
| D | Commit `arrangement-apply` on Project B | One `arrangement_of` edge. Project B's events equal the arrangement result and the other projects stay. A later unrelated edit of B does not mark this revision edge `stale` |
| E | Complete a fake neural render of Project B, then edit one note on B | `rendered_from` is `stale`. Job status stays `complete`. Exactly one job row |
| F | Bind a recovery job whose source bytes are known | One `transcribed_from` edge. `upstream_fingerprint` equals sha256 of those bytes. Graph JSON has no forbidden note key |
| G | Commit a revision whose generation parameters name Project A as the reference, then change `a4` on A | `reference_conditioned_by` is `stale`. The conditioned project's events stay the committed events |
| H | `accept-current` on one stale variation edge | That edge becomes `fresh`. Destination events are unchanged. The other two variation edges stay `stale` |

## Tasks

### Phase 1: Edge store
- [x] Task 1: Add the dependency documents
- [x] Task 2: Persist edges and refuse cycles

### Phase 2: Impact and capture
- [x] Task 3: Report stale dependents without rewriting them
- [x] Task 4: Record theme, motif, arrangement, and variation edges
- [x] Task 5: Record render, transcription, and reference edges

### Phase 3: HTTP and review
- [x] Task 6: Serve impact, the graph, and edge refresh
- [x] Task 7: Draw Theme A and its dependents in the Universe tab

### Phase 4: Docs
- [x] Task 8: Document the derived-material graph

## Commit Plan
- **Commit 1** (after tasks 1-2): "feat: store explicit edges for derived musical material"
- **Commit 2** (after tasks 3-5): "feat: mark dependents stale when Theme A changes"
- **Commit 3** (after tasks 6-7): "feat: show Theme A dependents and their update offers"
- **Commit 4** (after task 8): "docs: describe the derived-material dependency graph"

## Tasks (detail)

### Task 1: Add the dependency documents

Add the models in the schema decision to `backend/app/musical_dependency_schemas.py`. Add `MusicalDependencyError` with `code`, `message`, and `http_status`, plus `map_musical_dependency_error_to_http`. Codes: `embedded_note_material`, `dependency_invalid`, `dependency_endpoint_kind`, `dependency_cycle`, `dependency_graph_limit`, `dependency_not_found`, `dependency_refresh_conflict`, `dependency_reference_unresolved`, `dependency_fingerprint_unusable`. Scan `FORBIDDEN_NOTE_KEYS` on the way in.

`backend/tests/test_musical_dependency_schemas.py` accepts a `variation_of` edge from a theme to a motif occurrence and a graph of Theme A with three children. It rejects `events`, `pitch`, a seventh dependency type, a `rendered_from` edge whose upstream kind is `theme`, a fingerprint that is not 64 hex characters, and an update offer that includes `notes`.

LOGGING: DEBUG on schema rejection with the model name and the error code. Do not log labels or note fields. Levels follow `LOG_LEVEL`.

Files: `backend/app/musical_dependency_schemas.py`, `backend/tests/test_musical_dependency_schemas.py`.

### Task 2: Persist edges and refuse cycles

Add the Alembic revision and `musical_dependency_store.py` as specified. `motif_occurrence_fingerprint` lives in `backend/app/services/composition_snapshot_encoding.py` beside the snapshot hash, profile string `motif.occurrence.v1`. `delete_project` calls `delete_edges_for_downstream_project` on the connection that deletes the project row. `delete_universe` deletes matching `universe_id` rows on the connection that deletes the universe. A unique key on dependency type plus the two node keys makes a second `record_dependency_edge` update `upstream_fingerprint` instead of inserting. Do not add a foreign key that removes upstream-only rows.

`backend/tests/test_musical_dependency_store.py` inserts three theme edges, refuses the vector C cycle, refuses the 257th edge on one project with `dependency_graph_limit`, and refuses a self-edge. Recording the same stem-set endpoints again keeps a row count of 1 and replaces `upstream_fingerprint`. A project delete inside one transaction removes edges whose downstream project is gone and keeps an edge whose upstream project is gone. `backend/tests/test_musical_dependency_migration.py` upgrades a database at `20261001_0020` and downgrades this revision.

LOGGING: INFO on insert with `edge_id`, `dependency_type`, and `snapshot_fingerprint_log_prefix` of the upstream fingerprint. WARNING on cycle and cap with the code only. Do not log labels or `body` payloads. Levels follow `LOG_LEVEL`.

Depends on Task 1.

### Task 3: Report stale dependents without rewriting them

Add `musical_dependency_impact.py`. Vector B's comparison uses `composition_snapshot_fingerprint` of Project A against each variation edge. Include reachable `downstream_of_stale` nodes inside the cap. Build the update offer from the type table. A missing project or occurrence returns status `missing` and does not raise. This module's imports do not include `musical_universe_reuse`, `neural_audio_render`, `llm_composition_arrangement`, or `ai_agents`.

`backend/tests/test_musical_dependency_impact.py` builds the vector A rows and scores in SQLite, applies the `F4` → `G4` edit on Project A only, and asserts the three labels and `stale`. It adds a render edge for Project B and asserts `downstream_of_stale` while the destination compositions stay canonical-equal to the pre-edit fixtures. The test spies that reuse and render enqueue functions are not called.

LOGGING: INFO impact finished with `theme_id`, dependent count, and stale count. DEBUG each edge id and status. Do not log labels, pitches, or full fingerprints. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 4: Record theme, motif, arrangement, and variation edges

Add `musical_dependency_capture.py` and call it from `reuse_theme`, from `commit_revision`, and from `apply_as_branch_command` for the operation types in the capture table. In `frontend/src/store/musicStore.js`, set `ai.operation` from `developmentOperation` on both the revision commit and the apply-as-branch request. Record `variation_of` only when `request.ai.operation` is `vary_section`. For `creative-motif-apply`, load the previous score with `get_snapshot_composition(conn, expected_source_fingerprint)` and record an edge for each new occurrence that has `transform.source_occurrence_id`. Reuse and the history commands keep their current connection. A cycle raised from `record_dependency_edge` aborts that transaction.

`backend/tests/test_musical_dependency_capture.py` runs vector A through `reuse_theme` and asserts one edge per destination, variant ids, and a single SQLite transaction: forcing the edge insert to fail leaves Project B's pre-reuse fingerprint and a usage count of 0. Vector D commits `arrangement-apply` and asserts the revision edge. The same assertion holds for `apply_as_branch_command`. A same-project motif commit with `source_occurrence_id` records `motif_derived_from` using `motif_occurrence_fingerprint`, then an unrelated note edit leaves that edge `fresh`, and an edit of the source occurrence's pitch makes it `stale`. A `development-apply` whose `ai.operation` is `continue` does not add a `variation_of` edge. One whose `ai.operation` is `vary_section` does. `frontend/src/store/musicStore.development.test.js` asserts the durable apply payload sets `ai.operation` from `developmentOperation`.

Update `backend/tests/test_ai_agents_architecture.py` so the persistence import ban includes `from app.services.musical_dependency_store`.

LOGGING: INFO capture recorded with `dependency_type`, `edge_id`, project ids, and fingerprint prefixes. WARNING on rollback with the code only. Do not log pitches or variant labels. Levels follow `LOG_LEVEL`.

Depends on Task 2.

### Task 5: Record render, transcription, and reference edges

Call the capture helper from `run_neural_audio_job` on the connection that sets the mix job to `complete`, and from the two `update_stem_set_status(..., status="complete")` connections in `run_stem_set` and the selective rerender path. Do not hook per-stem `update_stem_status`. A second completion of the same stem set updates the existing edge. When `source_fingerprint` is not 64 hex, log `dependency_fingerprint_unusable`, leave the job `complete`, and skip the edge. In `bind_audio_recovery_job`, hash the source payload bytes and store that digest. In `commit_revision` and `apply_as_branch_command`, when generation parameters include a reference project id, hash that project with `composition_snapshot_fingerprint` and record `reference_conditioned_by`. A missing reference project logs `dependency_reference_unresolved` and still returns the revision.

`backend/tests/test_musical_dependency_capture.py` gains vectors E, F, and G. Vector E uses the fake neural completion path already in the render tests and asserts one edge and one job after a later note edit. Completing that same job again leaves one edge and refreshes `upstream_fingerprint`. A non-hex fingerprint leaves the job `complete` and the edge count unchanged. Vector F binds a tiny recovery fixture and compares the edge hash to sha256 of the source payload, not to `sha256_prefix`. Vector G changes the reference score and asserts the conditioned score's event ids are unchanged.

LOGGING: INFO for each recorded type with asset id and fingerprint prefix. WARNING `dependency_reference_unresolved` with the reference project id and no prompt text. WARNING `dependency_fingerprint_unusable` with the job id only. Do not log audio bytes or WAV paths beyond the existing render logs. Levels follow `LOG_LEVEL`.

Depends on Task 4.

### Task 6: Serve impact, the graph, and edge refresh

Add the router and register it. `accept-current` and `record-refresh` update the edge only. `record-refresh` returns `dependency_refresh_conflict` when `observed_downstream_fingerprint` differs from the live downstream hash for that downstream kind (`composition_snapshot_fingerprint`, or `motif_occurrence_fingerprint` for a motif occurrence). GET routes call `enforce_current(project_id, "read")` for every project id named by the selected edges before loading those scores. POST routes call `enforce_current(downstream_project_id, "write_score")`. Do not pass `read_score`.

`backend/tests/test_musical_dependency_api.py` covers GET impact for vector B, GET graph shape (three children under Theme A), vector H, and a conflict on `record-refresh`. Responses omit note keys. The test can keep collaboration disabled. A separate test with collaboration enabled and action `read` allows a viewer GET, and the same GET with action `read_score` is not what the route calls. A GET while the source project is unchanged returns `fresh` and does not change `document_revision` on the universe or `working_fingerprint` on B, C, or D.

LOGGING: INFO each route with method, path id, status, and `duration_ms`. DEBUG edge id on refresh. Do not log labels or bodies. Levels follow `LOG_LEVEL`.

Depends on Task 5.

### Task 7: Draw Theme A and its dependents in the Universe tab

Add the layout helper, the badge and review helpers in the same module, `DependencyGraph.jsx`, and the API methods on `frontend/src/api/musicalUniverseApi.js` (or a sibling `musicalDependencyApi.js` used only by the panel). Mount the graph from `MusicalUniversePanel.jsx`. Stale copy is `Out of date`. Transitive copy is `May need regeneration`.

`npm test` runs `node --test` on `src/utils/*.test.js`, `src/store/*.test.js`, and `src/api/*.test.js`. Do not add `DependencyGraph.test.jsx`. `frontend/src/utils/dependencyGraphLayout.test.js` lays out a root plus three children on distinct x positions and a shared child depth. It maps status `stale` to `Out of date` and `downstream_of_stale` to `May need regeneration`. `reviewUpdateEdgeId` returns the selected edge id and does not import `reuseMusicalUniverseTheme`. The initial call of that helper is not the review action.

LOGGING: the panel does not `console.log` labels or note arrays. API failures surface the server `code`.

Depends on Task 6.

### Task 8: Document the derived-material graph

Add `docs/derived-material-graph.md` with the terminology lock, the six types, the rule that an upstream change lists dependents and does not rewrite them, cycle refusal, and the Universe-tab graph. Link it from `AGENTS.md`, `docs/CODEBASE_MAP.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md` beside the musical-universe lines. State that `ai_agents/` does not import `musical_dependency_store`. Do not edit `ROADMAP.md`.

LOGGING: none. This task does not add runtime logs.

Depends on Tasks 6 and 7.
