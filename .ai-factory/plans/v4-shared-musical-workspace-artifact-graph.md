# Implementation Plan: V4 Shared Musical Workspace & Typed Agent-Artifact Graph

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-22
Improved: 2026-09-22

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: deepen the shipped V4 multi-agent layer with a **typed, immutable, dependency-aware agent-artifact graph** and a **shared musical workspace** so cooperating agents exchange plans/analysis/patches without inventing competing Composition representations — **without** `composition.v4`, a mutable blackboard, microservices, or regressing session-only preview / `multi-agent-apply` CAS
- Parent plan: `.ai-factory/plans/v4-multi-agent-music-architecture.md` (spine registry/workflow already shipped; this plan owns the durable typed graph + history projection + retention)

## Roadmap Linkage
Milestone: "V4 multi-agent music architecture"
Rationale: Milestone marked complete for the agent registry/spine; this follow-on closes the remaining product gap — inspectable typed artifacts, explicit dependencies, durable revision binding, history display, and temporary-artifact GC — so an autonomous spine run can prove exactly which brief / harmony / motif / arrangement / critique / revision plan produced a Composition revision.

## Goal

Give cooperating agents a **safe, inspectable workspace** to exchange musical plans, analysis, and candidate changes while keeping canonical `composition.v2` the only playable score.

Ship:

1. First-class typed content schemas (CreativeBrief, CompositionPlan, FormPlan, HarmonyPlan, MotifPlan, ArrangementPlan, OrchestrationPlan, PerformancePlan, ProductionPlan, MusicAnalysis, CritiqueReport, RevisionPlan, CompositionPatch, RenderPlan) wrapped by an extended immutable envelope.
2. Explicit dependency rules between artifact types and Composition revisions.
3. Immutable-after-create store (new versions only; never silent mutate).
4. Validation + forbidden-playable-field gates (mirror `composition.plan.v1`).
5. Project-history projection of meaningful AI artifacts (no internal noise).
6. Retention / GC for temporary artifacts.
7. Migrations, tests, structured logging, docs.

```text
composition.v2 revision (authoritative score)
  → AgentWorkflowContext (session slots + working_draft)
  → agents emit typed payloads inside agent.artifact.v1 envelopes
  → [default] session-only artifact_log until Apply
  → [optional] temporary workspace rows when preview sets persist_workspace_artifacts
  → progressive realize → fingerprintable candidate V2
  → Apply (multi-agent-apply CAS) + artifact_role_map
  → INSERT durable immutable rows + revision_artifact_links (same transaction)
  → Versions UI shows role summary (brief / harmony / motif / …)
```

**Terminology lock:** Product V4 remains multi-agent orchestration. Canonical playable schema stays **`composition.v2`**. Typed plans are **non-playable**. `composition.v4` unsupported. Workspace is **not** a second score and **not** a mutable blackboard. Agents remain in-process under `ai_agents/`; durable writes happen only at trust-boundary services / Apply routers (never from agent `run()` into SQLite).

## Artifact Inventory (required types)

| Product name | `content_type` / schema id | Reuse | Notes |
|--------------|---------------------------|-------|-------|
| CreativeBrief | `agent.brief.v1` | Extend existing | Intent / mood / constraints; never notes |
| CompositionPlan | `composition.plan.v1` | Existing | Full plan wrapper; forbid playable fields |
| FormPlan | `agent.form_plan.v1` | New (or strict subset of plan.form) | Sections / density outline only |
| HarmonyPlan | `agent.harmony_plan.v1` | New | Chord/timeline intent — not audible invent from alone |
| MotifPlan | `agent.motif_plan.v1` | New | Motif/theme recurrence plan |
| ArrangementPlan | `agent.arrangement_plan.v1` | New | Track topology / texture intent (≠ realized candidate) |
| OrchestrationPlan | `agent.orchestration_plan.v1` | New | Instrument/range recommendations (schema yes; full adapter emit deferred) |
| PerformancePlan | `agent.performance_plan.v1` | New | Expression / velocity / CC intent (schema yes; full adapter emit deferred) |
| ProductionPlan | `agent.production_plan.v1` | New | Mix/render intent; `mutates_composition: false` (schema yes; stub emit OK) |
| MusicAnalysis | `composition.analysis.v1` | Existing sidecar | Durable form = bounded projection (see Task 2); never persist as Composition |
| CritiqueReport | `agent.critique.v1` | Extend existing | approve/revise + reason codes |
| RevisionPlan | `agent.revision_plan.v1` | New | Critic-driven revise targets / stop criteria |
| CompositionPatch | `agent.composition_patch.v1` | New | Structured patch descriptor for realize — **not** a full alternate V2 |
| RenderPlan | `agent.render_plan.v1` | New | Neural/WAV egress intent / job refs only (schema yes; stub emit OK) |

**Envelope (extended `agent.artifact.v1`):** every artifact carries `artifact_schema`, `artifact_id`, `content_type`, `producer_agent_id`, `source_revision_id` (nullable for session-only), `source_fingerprint`, `created_at` (UTC ISO), `parent_artifact_ids`, typed `depends_on` edges, optional `supersedes_artifact_id`, model provenance, `retention_class` (`temporary`|`durable`), `mutates_composition: false`. Payload **must** validate against the typed content model for `content_type` (not allow-list alone).

**Link roles (locked):** `brief`, `harmony_plan`, `motif_plan`, `arrangement_plan`, `critique`, `revision_plan`.

## Approach Evaluation (locked)

### Part A — Where the workspace lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Mutable in-memory blackboard dict shared globally** | Fast | Races; violates immutability; hidden coupling | **Reject** |
| **B. Session-only `AgentWorkflowContext` forever** | Already shipped | Cannot satisfy history display / GC / durable binding AC | **Insufficient alone** |
| **C. Durable immutable SQLite artifact store + session context** | Inspectable; revision-linkable; GC-able | Migration + retention work | **Accepted** |
| **D. Store full V2 per agent step** | Easy audit | Competing Composition representations | **Reject** |

### Part B — Typed payloads vs generic envelopes

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Only `kind` + freeform `payload` dict** | Flexible | Weak validation; plans can smuggle playable fields | **Reject** as sole design |
| **B. Strict Pydantic content schemas + allow-listed `content_type` + payload validate** | Matches plan.v1 / analysis.v1 discipline | Schema surface grows | **Accepted** |
| **C. Overload `composition.v2` fields for plans** | One schema | Breaks playable/canonical rules | **Reject** |

### Part C — Who writes the durable store

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Agents call `project_store` / history from `run()`** | Convenient | Breaks V4 forbidden-write gate; secret risk | **Reject** |
| **B. Workspace service at router/Apply boundary; agents return envelopes only** | Preserves agent purity | Staging must be explicit | **Accepted** |
| **C. HTTP round-trips between agents for every artifact** | Clear | Too heavy in-process | **Reject** |

### Part D — History display

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Dump full artifact payloads on revision list** | Complete | Noise; prompts/risk; UI overload | **Reject** |
| **B. `revision.ai_artifact_summary.v1` role → {artifact_id, content_type, producer_agent_id, created_at, model_id}** | Meaningful; secret-safe | Needs link table | **Accepted** |
| **C. Only provenance stages (status quo)** | Already works | Cannot answer “which MotifPlan?” | **Insufficient** |

### Part E — CompositionPatch shape

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Embed full candidate V2 as the patch artifact** | Simple Apply | Creates alternate playable score artifact | **Reject** as typed patch content |
| **B. Patch descriptor (op refs + realize recipe) + separate fingerprintable candidate V2 on Apply envelope** | Clear boundary | Two objects | **Accepted** |
| **C. Diff-only event ops without realize trust boundary** | Compact | Re-invents validators | **Reject** |

### Part F — When SQLite rows appear (locked 2026-09-22 improve)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Every preview hop writes temps** | Always inspectable | Preview mutates DB; fights agent purity | **Reject** as default |
| **B. Session-only until Apply; durable INSERT in Apply transaction** | Matches arrange/develop Apply; simple GC default | Mid-preview inspect needs flag | **Accepted default** |
| **C. Optional temps when preview has `project_id` + `persist_workspace_artifacts=true`** | Opt-in inspectability | Needs GC | **Accepted optional** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| V4 `ai_agents/` | registry, spine, progressive realize, fake agents, HTTP preview/run | Keep; extend schemas + adapters |
| `AgentArtifactV1` | envelope with `parent_artifact_ids`, provenance, content_type allow-list | Extend fields; keep immutability via `model_copy` |
| Payload schemas | `agent.brief.v1`, `agent.workflow_plan.v1`, `agent.critique.v1` | Extend brief/critique; add remaining plan types |
| `composition.plan.v1` | Forbidden playable top-level fields | Pattern for all new plan schemas + shared validate helper |
| `composition.analysis.v1` | Critic sidecar | MusicAnalysis content_type; durable = bounded projection |
| Arrangement / reharm / motif candidates | Session preview shapes | Map into ArrangementPlan / CompositionPatch / realize path |
| `MULTI_AGENT_APPLY` | Operation enum + FE envelope | Attach `artifact_role_map` + promote in commit transaction |
| Provenance stages + `agent_id` | Sanitizer allow-list | Keep; optional `artifact_id` on stages |
| Neural audio migration | Soft FK + project CASCADE | Pattern for artifact tables |
| Audio transcription retention | Explicit delete-after-use | Pattern for temporary GC semantics |
| Forbidden-import gate | `test_ai_agents_*` scans for DB writes | Extend to forbid `agent_artifact_workspace` imports from `ai_agents/` |
| Versions panel | `formatRevisionProvenanceSummary` | Consume new AI artifact summary DTO (Task 13) |
| `map_agent_error_to_http` | Stable agent error codes | Extend with workspace codes (Task 5) |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Typed plan schemas beyond brief/critique | Form/Harmony/Motif/Arrangement/Orchestration/Performance/Production/Revision/Patch/Render |
| Envelope fields + payload validate | `source_revision_id`, `created_at`, `depends_on`, `supersedes_artifact_id`, `retention_class`; typed payload check |
| Explicit dependency registry + validation | MotifPlan→Brief+HarmonyPlan; ArrangementPlan→CompositionPlan+source revision; etc. |
| Workspace error codes in `errors.py` | Map new codes → HTTP |
| Durable workspace tables + Alembic | Immutable insert; link role CHECK; `content_digest` |
| Promote-on-Apply + `artifact_role_map` | FE/API contract; transactional bind |
| History projection API (backend) | Meaningful summary without payload dump |
| GC / retention | TTL for optional temps; CASCADE on project delete; bound max rows |
| Adapter updates (spine) | Emit typed plans (not only candidate_patch blobs); stubs OK for non-spine |
| Acceptance test | Revision exposes exact brief/harmony/motif/arrangement/critique/revision_plan ids |
| Docs | Workspace + graph diagram; update `docs/multi-agent.md` |

### Coupling risks to avoid

1. Inventing `composition.v4` or treating any plan as playable notes.
2. Letting `ai_agents/` import `project_store` / `project_history_store` / `PROJECT_DB_PATH` / `agent_artifact_workspace`.
3. UPDATE-in-place of artifact payloads (must insert new version).
4. Persisting full V2 event arrays or full unbounded analysis reports inside durable artifacts or history summaries.
5. Logging prompts, API keys, full analysis reports, or full payloads at INFO.
6. Auto-committing Critic approve without Apply.
7. Making ArrangementPlan ≡ arrangement.candidate (keep plan vs realized candidate distinct).
8. Breaking V3 single-op routes or session-only arrange/develop/reharm previews.
9. Storing secrets in artifact JSON (run `persistence_secret_guard` before insert).
10. Growing workspace logic into `main.py` — prefer `services/agent_artifact_workspace.py` + router helpers.
11. Defaulting preview to write SQLite without explicit `persist_workspace_artifacts`.

## Scope And Decisions

### In scope
- Extended `agent.artifact.v1` envelope + all typed content schemas listed above.
- Dependency rule registry + validation helpers + workspace error HTTP map.
- Immutable SQLite workspace (`agent_artifacts`, `revision_artifact_links`) + Alembic migration.
- Session-default + optional temp staging; promote-on-`multi-agent-apply` with `artifact_role_map`.
- History/list/detail projection DTOs (backend) + thin Versions UI surfacing.
- Retention settings + GC helper + Compose/`.env.example` passthrough.
- Spine adapter updates to emit typed plans for acceptance roles (non-spine: schemas + stub emit only).
- Tests, logging, `docs/multi-agent.md` (+ optional workspace diagram section).

### Out of scope
- `composition.v4`.
- Microservices / message queues / cross-process agent bus.
- Replacing progressive realize or inventing a new Apply CAS.
- Full nine-agent expanded graph / full Orchestration|Performance|Production|Render adapters (schemas + stubs only).
- Per-token billing.
- Playwright SPA journey (API + unit/architecture tests suffice).
- Training new models.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP (ai_agents, projects history)
        ↓
ai_agents/  (emit typed envelopes only; no SQLite; no workspace imports)
        ↓
services/agent_artifact_workspace.py  (validate, insert, link, GC, summarize)
        ↓
services/  (progressive realize / validators — existing trust boundary)
        ↓
SQLite PROJECT_DB_PATH (immutable artifact rows + revision links)
```

**2. Envelope extensions (`agent.artifact.v1`)**

| Field | Rule |
|-------|------|
| `created_at` | UTC ISO-8601; set at envelope construction |
| `source_revision_id` | Optional until Apply/promote; required for durable rows when project open |
| `depends_on` | list of `{artifact_id, relation}` validated against registry |
| `supersedes_artifact_id` | Optional; when set, prior id remains immutable |
| `retention_class` | `temporary` (optional preview persist) \| `durable` (after promote) |
| `expires_at` | Required when temporary; null when durable |
| payload | Must pass `validate_artifact_payload(content_type, payload)` |

**3. Dependency registry (initial)**

| Artifact | Depends on |
|----------|------------|
| MotifPlan | CreativeBrief, HarmonyPlan |
| ArrangementPlan | CompositionPlan (or FormPlan+HarmonyPlan), existing Composition revision (`source_revision_id` or source fingerprint) |
| OrchestrationPlan | ArrangementPlan |
| PerformancePlan | MotifPlan or ArrangementPlan (at least one musical plan) |
| ProductionPlan / RenderPlan | Composition revision and/or ArrangementPlan |
| CritiqueReport | working draft fingerprint; preferably HarmonyPlan + MotifPlan + ArrangementPlan when present |
| RevisionPlan | CritiqueReport with `recommendation=revise` |
| CompositionPatch | at least one plan artifact that progressive realize understands |

Validation fails closed with stable code `artifact_dependency_unsatisfied`.

**4. Immutability**

- Store: INSERT only for payloads; no UPDATE of `payload_json` / `content_type`.
- `content_digest = sha256(canonical_json(payload))` (stable key order, UTF-8, no whitespace variance).
- Corrections = new `artifact_id` + `supersedes_artifact_id`.
- Session context continues copy-on-write (`with_slot` / `with_artifact_log`).

**5. Staging vs Apply (locked)**

- **Default:** workflow preview remains session-only (`artifact_log` in HTTP/FE envelope); **no** SQLite writes from preview.
- **Optional:** if preview request includes `project_id` **and** `persist_workspace_artifacts=true`, insert `retention_class=temporary` rows (GC applies).
- **Apply (project open):** validate `artifact_role_map`, INSERT durable rows + `revision_artifact_links` in the **same SQLite transaction** as DurableCommit / ApplyAsBranch.
- **Apply (no project):** local composition commit only; skip durable promote.

**6. `artifact_role_map` (Apply contract)**

```json
{
  "brief": {"artifact_id": "…", "content_type": "agent.brief.v1"},
  "harmony_plan": {"artifact_id": "…", "content_type": "agent.harmony_plan.v1"},
  "motif_plan": {"artifact_id": "…", "content_type": "agent.motif_plan.v1"},
  "arrangement_plan": {"artifact_id": "…", "content_type": "agent.arrangement_plan.v1"},
  "critique": {"artifact_id": "…", "content_type": "agent.critique.v1"},
  "revision_plan": null
}
```

Carried in `generation_parameters.artifact_role_map` (and/or dedicated `AiProvenance` field). FE builds map from preview `artifact_log`; server resolves envelopes (from request body or prior temp rows) before promote.

**7. History projection**

`GET` revision detail (and list enrichment) returns `revision.ai_artifact_summary.v1` role metadata only — never full payloads/prompts/events. Optional `GET /projects/{id}/artifacts/{artifact_id}` for inspect (secret-guarded; size-capped).

**8. Retention / GC**

| Class | Rule |
|-------|------|
| temporary | TTL `AGENT_ARTIFACT_TEMP_TTL_HOURS` (default 24); GC deletes expired/orphan temps |
| durable | Project lifetime; CASCADE on project delete |
| caps | `AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT` (default ~200) |

GC: best-effort on workspace write + project delete; not a microservice. Env keys in `.env.example` + `docker-compose.yml` passthrough.

**9. Logging**

- INFO: artifact_id prefix, content_type, producer_agent_id, retention_class, dependency counts, promote role map, GC deleted counts, duration_ms.
- DEBUG: depends_on id prefixes, supersedes id, fingerprint prefixes.
- Never: prompts, keys, full payloads, full V2 events, full critique/analysis prose at INFO.

**10. Domain errors (additive)**

| Code | HTTP |
|------|------|
| `artifact_dependency_unsatisfied` | 422 |
| `artifact_immutable_violation` | 422 |
| `artifact_not_found` | 404 |
| `artifact_payload_rejected` | 422 |
| `artifact_playable_fields_forbidden` | 422 |
| `artifact_retention_rejected` | 422 |
| `workspace_quota_exceeded` | 429/422 |

## Acceptance criteria mapping

| Criterion | Tasks |
|----------|-------|
| Inspect current schemas before new types | 1 |
| Canonical Composition separate from planning artifacts | 2–4, 9 |
| Plans never become alternate playable formats | 2–4, 7, 14 |
| Envelope metadata + payload validate | 2, 4, 7 |
| Explicit dependency relationships | 3, 9 |
| Workspace error HTTP map | 5 |
| Immutable after creation; new versions only | 6–7, 14 |
| Artifact validation | 2–4, 14 |
| Project history meaningful AI artifacts | 8, 12–13 |
| GC / retention for temporary artifacts | 7, 11, 14 |
| Autonomous run exposes brief/harmony/motif/arrangement/critique/revision_plan | 9–12, 14 |
| Migrations, tests, logging, docs | 6, 14–15 |

## Commit Plan
- **Commit 1** (tasks 1–5): `feat(ai-artifacts): typed schemas, dependencies, envelope, error codes`
- **Commit 2** (tasks 6–8): `feat(ai-artifacts): immutable workspace store, links, history projection`
- **Commit 3** (tasks 9–13): `feat(ai-artifacts): spine typed plans, role map, promote-on-apply, Versions UI`
- **Commit 4** (tasks 14–15): `test(ai-artifacts): GC, architecture gates, docs`

## Tasks

### Phase 0: Inventory lock

- [x] Task 1: Inventory existing schemas and freeze non-goals
  Deliverable: Short audit module docstring / checklist in the new workspace package stub documenting: (a) existing `AgentArtifactV1` / brief / critique / `composition.plan.v1` / `composition.analysis.v1` reuse; (b) forbidden playable field set shared with plan schemas; (c) agents must not write SQLite / import workspace; (d) no `composition.v4`; (e) default session-only until Apply. Add focused tests that current spine workflow still returns `artifact_log` without requiring durable workspace rows (non-regression).
  LOGGING: DEBUG checklist ids; INFO skip reasons only.
  Files: `backend/app/services/agent_artifact_workspace.py` (stub + docstring), `backend/tests/test_agent_artifact_inventory.py` (new), references to `backend/app/ai_agents/schemas.py`, `composition_plan_schemas.py`.
  Depends on: none.

### Phase 1: Typed contracts

- [x] Task 2: Define typed content schemas for all required artifact kinds
  Deliverable: Pydantic models for FormPlan, HarmonyPlan, MotifPlan, ArrangementPlan, OrchestrationPlan, PerformancePlan, ProductionPlan, RevisionPlan, CompositionPatch, RenderPlan; extend CreativeBrief / CritiqueReport only if missing required fields. Each schema: `schema_version` literal, `extra=forbid`, shared forbidden-playable guards (tracks/events/notes/musicxml/midi/wav). CompositionPlan continues to use `composition.plan.v1`. **MusicAnalysis durable bound:** define a bounded projection / hard byte cap for promote (warning_codes + counts + scope digests); full `composition.analysis.v1` remains session-only unless under cap; oversized → `artifact_payload_rejected`. CompositionPatch must describe realize ops/refs — reject embedding a full `tracks[].events[]` score. Unit-test accept/reject fixtures per type.
  LOGGING: INFO schema load counts; DEBUG reject codes; never payload bodies at INFO.
  Files: `backend/app/ai_agents/artifact_schemas.py` (new) and/or extend `ai_agents/schemas.py`; `backend/tests/test_agent_artifact_typed_schemas.py`.
  Depends on: Task 1.

- [x] Task 3: Dependency registry + validation
  Deliverable: Declarative dependency map (MotifPlan→Brief+HarmonyPlan; ArrangementPlan→CompositionPlan|+source revision; etc.). Validator that checks `depends_on` / `parent_artifact_ids` + available context slots / source_revision_id. Stable error `artifact_dependency_unsatisfied`. Unit tests for happy path + missing HarmonyPlan for MotifPlan + ArrangementPlan without revision/fingerprint.
  LOGGING: INFO validation pass/fail with content_type + missing dependency types; DEBUG artifact id prefixes.
  Files: `backend/app/ai_agents/artifact_dependencies.py` (new), tests `backend/tests/test_agent_artifact_dependencies.py`.
  Depends on: Task 2.

- [x] Task 4: Extend `AgentArtifactV1` envelope metadata + payload validate
  Deliverable: Add `created_at`, `source_revision_id`, `depends_on`, `supersedes_artifact_id`, `retention_class`, `expires_at` with validators; extend `AGENT_CONTENT_TYPES` allow-list; ensure `mutates_composition` remains false. Provide `validate_artifact_payload(content_type, payload)` used on envelope construction (and later on workspace insert). Session artifacts default temporary TTL only when persisted; in-memory envelopes may omit `expires_at`. Tests for immutability of validated model.
  LOGGING: DEBUG envelope field presence; INFO reject codes.
  Files: `backend/app/ai_agents/schemas.py`, payload validate helper (schemas or `artifact_schemas.py`), `backend/tests/test_ai_agents_schemas.py` (extend).
  Depends on: Task 2, Task 3.

- [x] Task 5: Extend agent/workspace error HTTP map
  Deliverable: Add stable codes to `ai_agents/errors.py` + `_HTTP_STATUS` / `map_agent_error_to_http`: `artifact_dependency_unsatisfied`, `artifact_immutable_violation`, `artifact_not_found`, `artifact_payload_rejected`, `artifact_playable_fields_forbidden`, `artifact_retention_rejected`, `workspace_quota_exceeded`. Exception subclasses or factory as needed. Unit-test code → status mapping. Sanitized `detail` only (no payloads/prompts).
  LOGGING: WARN/ERROR with code + artifact_id/project_id prefixes only.
  Files: `backend/app/ai_agents/errors.py`, `backend/tests/test_ai_agents_errors.py` (extend).
  Depends on: Task 4.

### Phase 2: Durable workspace

- [x] Task 6: Alembic migration for immutable artifact tables
  Deliverable: Migration after `20260921_0002` (e.g. `20260922_0003_agent_artifacts`) creating:
  - `agent_artifacts` (id PK, project_id FK CASCADE, content_type, kind, producer_agent_id, source_revision_id, source_fingerprint, payload_json, parent_ids_json, depends_on_json, provenance_json, supersedes_artifact_id, retention_class, expires_at, content_digest, created_at; CHECK retention; indexes on project+created, expires, revision)
  - `revision_artifact_links` (revision_id, role, artifact_id, PRIMARY KEY(revision_id, role); FK cascade; **CHECK role IN** (`brief`,`harmony_plan`,`motif_plan`,`arrangement_plan`,`critique`,`revision_plan`))
  Document `content_digest = sha256(canonical_json(payload))`. Soft FK to revisions like neural audio. Downgrade drops indexes/tables.
  LOGGING: INFO upgrade/downgrade; never dump payloads.
  Files: `backend/app/db/alembic/versions/20260922_0003_agent_artifacts.py` (new).
  Depends on: Task 4.

- [x] Task 7: Workspace service — insert, fetch, promote, GC + env passthrough
  Deliverable: `agent_artifact_workspace` APIs: `insert_temporary` (optional preview path only), `get_artifact`, `list_project_artifacts` (metadata only), `promote_and_link_revision`, `gc_expired_temporary`. INSERT-only payloads; UPDATE payload → `artifact_immutable_violation`. Call `validate_artifact_payload` + secret guard before insert. Enforce temp quota. **Must not be imported by `ai_agents/` agent modules.** Settings: `AGENT_ARTIFACT_TEMP_TTL_HOURS`, `AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT` in `agent_artifact_settings.py` + `.env.example` + **`docker-compose.yml` passthrough** (and compose.dev if present).
  LOGGING: INFO insert/promote/gc counts + project_id + retention; DEBUG id prefixes; ERROR codes only.
  Files: `backend/app/services/agent_artifact_workspace.py`, `backend/app/agent_artifact_settings.py` (new), `.env.example`, `docker-compose.yml`, tests `backend/tests/test_agent_artifact_workspace.py`.
  Depends on: Task 5, Task 6.

- [x] Task 8: History projection DTOs + API enrichment (backend only)
  Deliverable: `revision.ai_artifact_summary.v1` DTO; enrich revision detail (and list when cheap) for `multi-agent-apply` revisions with role map. Optional `GET /projects/{id}/artifacts/{artifact_id}` for inspect (payload allowed under size cap, secret-guarded, never on list). **No FE panel work in this task** (Task 13).
  LOGGING: INFO summary role counts; DEBUG missing roles; never payloads at INFO.
  Files: `backend/app/project_history_schemas.py`, `backend/app/services/project_history.py` / store join helpers, `backend/app/routers/projects.py` as needed, backend tests for summary shape.
  Depends on: Task 7.

### Phase 3: Wire spine + Apply

- [x] Task 9: Update spine agents to emit typed plans
  Deliverable: Creative Director → CreativeBrief (+ CompositionPlan or FormPlan as available); Harmony → HarmonyPlan (+ CompositionPatch for realize); Melody/Motif → MotifPlan (+ patch); Arrangement → ArrangementPlan (+ arrangement candidate/patch); Critic → bounded MusicAnalysis + CritiqueReport + optional RevisionPlan when revise. Set `depends_on` / parents correctly. Progressive realize still only via existing trust-boundary helpers. Fake agents updated for deterministic CI. Non-spine agents: stub/recommendation emit only (schemas exist; no full adapter expansion).
  LOGGING: INFO agent_id + content_types produced; DEBUG dependency edges; never prompts.
  Files: `backend/app/ai_agents/agents/*.py`, `fake_agents.py`, `progressive_realize.py` adapters if needed; tests `test_ai_agents_workflow.py` / new typed emit tests.
  Depends on: Task 2, Task 3, Task 4.

- [x] Task 10: Lock `artifact_role_map` Apply / FE contract
  Deliverable: Define and document `generation_parameters.artifact_role_map` (role → `{artifact_id, content_type}`). Update `buildAiCandidateEnvelope` / multi-agent extras so preview `artifact_log` produces the map; `applyMultiAgentCandidate` forwards it on DurableCommit / ApplyAsBranch. Server-side validator rejects missing required roles for spine Apply (`brief`, `harmony_plan`, `motif_plan`, `arrangement_plan`, `critique`; `revision_plan` required only on revise path). Unit tests FE + backend schema accept/reject.
  LOGGING: INFO role keys present/absent; DEBUG artifact_id prefixes; never payloads.
  Files: `frontend/src/utils/compositionCandidateLifecycle.js` (+ multiAgent test), `frontend/src/store/musicStore.js`, `backend/app/project_history_schemas.py` / AiProvenance as needed, tests.
  Depends on: Task 9.

- [x] Task 11: Promote artifacts on `multi-agent-apply`
  Deliverable: When FE/API commits multi-agent candidate with project open, Apply path validates `artifact_role_map`, promotes envelopes into durable rows + `revision_artifact_links` **in the same transaction** as the revision commit. Fingerprint gate unchanged. No project → skip promote (local apply only). Competing session candidates discard policy unchanged. Acceptance: after Apply, revision detail exposes non-null brief, harmony_plan, motif_plan, arrangement_plan, critique (`revision_plan` null when approve).
  LOGGING: INFO promote role map + revision_id prefix; WARN missing optional roles; ERROR on promote failure (no half-linked state).
  Files: `backend/app/services/project_history.py` (or apply helper), routers as needed, tests `backend/tests/test_agent_artifact_apply_binding.py`.
  Depends on: Task 7, Task 8, Task 9, Task 10.

- [x] Task 12: Acceptance spine integration test
  Deliverable: End-to-end (fake mode): workflow preview → Apply → GET revision shows exact artifact ids for brief, harmony_plan, motif_plan, arrangement_plan, critique, and revision_plan when revise path used. Assert payloads are non-playable (no tracks/events). Assert `ai_agents/` still has no DB / workspace imports.
  LOGGING: INFO workflow_id + revision_id prefixes + role keys present.
  Files: `backend/tests/test_agent_artifact_acceptance.py` (new); extend architecture import gate.
  Depends on: Task 8, Task 11.

- [x] Task 13: Thin Versions UI surfacing
  Deliverable: Show meaningful AI artifact role summary on multi-agent revisions using `ai_artifacts` / summary DTO from Task 8. Product role labels only (no raw slot names like `harmony_artifact`). Extend or complement `formatRevisionProvenanceSummary`. Accessibility: text summary sufficient.
  LOGGING: FE debug only under existing VITE_LOG_LEVEL; no payload dumps.
  Files: `frontend/src/components/ProjectVersionsPanel.jsx`, `frontend/src/utils/compositionCandidateLifecycle.js` (or small helper) + unit test.
  Depends on: Task 8, Task 11.

### Phase 4: Retention, hardening, docs

- [x] Task 14: GC/retention tests + architecture gates
  Deliverable: Tests for TTL expiry deletion, quota enforcement, immutable update rejection, forbidden playable fields, dependency failures, project CASCADE cleanup, payload validate on insert, and `ai_agents/` forbidden import of workspace write entrypoints / `PROJECT_DB_PATH`. Structured logging assertions where practical.
  LOGGING: INFO GC deleted counts in tests via caplog where useful.
  Files: `backend/tests/test_agent_artifact_gc.py`, extend `test_ai_agents_architecture.py` / registry forbidden-import tests.
  Depends on: Task 7, Task 11.

- [x] Task 15: Documentation checkpoint
  Deliverable: Update `docs/multi-agent.md` with Shared Musical Workspace section: typed inventory, dependency examples, session-default vs optional temps, immutability/`content_digest`, `artifact_role_map`, promote-on-Apply, history summary DTO, retention env knobs, anti-patterns. Cross-link `docs/project-persistence.md`. Update `AGENTS.md` if structure changed significantly. Note that artifact graph completes inspectability AC for V4 (do not uncheck completed roadmap milestone unless product owner requests).
  LOGGING: n/a for docs prose; keep log policy section accurate.
  Files: `docs/multi-agent.md`, optional `docs/project-persistence.md`, `AGENTS.md` if needed, `.env.example`.
  Depends on: Task 12, Task 14.

## Implementation notes for `/aif-implement`

- Prefer extending `ai_agents/schemas.py` carefully; if file grows unwieldy, split payload models into `artifact_schemas.py` and keep envelope/registry types in `schemas.py`.
- Reuse `composition.plan.v1` forbidden-field helper patterns rather than duplicating ad hoc checks.
- Keep arrangement.candidate / reharm.candidate as realize inputs; ArrangementPlan is the typed intent artifact for history — both may appear in `artifact_log`.
- Transactionality: promote + revision insert must be one SQLite transaction with the durable commit.
- Default `max_revisions=0` spine still must emit CritiqueReport; RevisionPlan only when revise recommendation is produced.
- Forbidden-import gate must include `agent_artifact_workspace` (and settings) for `ai_agents/` package scans.

## Non-goals reminder

Do not invent `composition.v4`. Do not auto-apply Critic approve. Do not store alternate full scores as “patches.” Do not let agents write the workspace directly. Do not expand non-spine adapters beyond stubs in this plan.

## Appendix — Improvement notes (2026-09-22)

Applied `/aif-improve`:
- Added Task 5 (workspace error HTTP map) and Task 10 (`artifact_role_map` FE/API contract)
- Locked session-default vs optional `persist_workspace_artifacts` temps
- Bounded durable MusicAnalysis; required `validate_artifact_payload`
- Split history backend (Task 8) from Versions UI (Task 13)
- Locked link roles CHECK + `content_digest` algorithm
- Compose/`.env` passthrough for retention knobs
- Fixed deps: Task 9→2; Task 12→8; Task 11 blocked by Task 5/7/10
- Deferred full non-spine adapter expansion (out of scope)
