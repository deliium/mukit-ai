# Implementation Plan: V5 Soundtrack / Production Asset-Pack Generation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Focused **Asset Pack** surface inside the existing **Agents** tab (not a new workspace tab). Ordered steps: pack brief → AssetPackPlan preview → Generate → per-slot project status → partial regenerate. Opening the tab never auto-creates projects, never auto-generates, never joins a universe, and never starts neural renders. No piano-roll redesign, no multi-DAW picker, no replacement of Universe / Adaptive / Profiles tabs
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Composer Profile soft-conditions via pack-side `resolve_profile_merge` into each slot `creative.brief.v1` (do **not** extend `AutonomousRunStartV1` in ship-1). Locked `theme_policy.propagate` semitone/repeat table. Per-slot duration defaults seed `90` / non-seed `60` / max `120`. Generate is **synchronous**; slot status written before the next slot. Seed motif discovery by `motif_label` + `original`; reuse track = first pitched non-drum (`melody` preferred). Adaptive scaffolds lock `bar_range` (or first section) material refs. Agents UI mounts in `MultiAgentPanel.jsx`. History ops `asset-pack-generate` / `asset-pack-slot-regenerate` added where pack commits outside `autonomous-stage` / `musical-universe-theme-apply`
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). First unchecked ROADMAP items (performance conductor / spatial) are orthogonal and already planned/shipped; this plan documents a new franchise milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`
- Scope: one project brief compiles a non-playable `asset.pack.plan.v1` (AssetPackPlan), then an explicit generate creates several independent projects whose scores stay `composition.v2`, share a Musical Universe + Composer Profile + production targets, and optionally bind adaptive-score state labels. Partial regeneration rewrites only selected slots. Mocked autonomous tests under `LLM_FAKE_MODE`. Never invent `composition.v5`. Never store note events on the pack plan. Never write `DATASET_ROOT`. `ai_agents/` must not import pack stores
- Predecessors: `.ai-factory/plans/v5-musical-universe.md` (complete), `.ai-factory/plans/v5-derived-material-dependency-graph.md` (complete), `.ai-factory/plans/v5-adaptive-score-domain-model.md` (complete), V4 composer profiles + autonomous composer (shipped)

## Roadmap Linkage
Milestone: "V5 Soundtrack / production asset-pack generation"
Rationale: Musical Universe and dependency edges already share themes across projects, but nothing turns one franchise brief into a coherent family of soundtrack assets with a plan-before-generate gate and shared production targets. First unchecked ROADMAP items (performance / spatial) are orthogonal. This plan documents the new milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`.

## Goal

Make **project-level soundtrack / production asset-pack generation** a first-class V5 workflow so one creative brief produces a coherent family of music assets — not one multi-section composition — while keeping each asset an independent canonical project/revision.

Example game pack slots:

- Main Theme
- Menu
- Exploration Forest
- Exploration City
- Combat Low
- Combat High
- Boss
- Victory
- Defeat
- Credits

Ship:

1. Non-playable **`asset.pack.brief.v1`** → compile **`asset.pack.plan.v1` (AssetPackPlan)** before any score write.
2. Explicit generate that creates **N independent projects** (each with its own `composition.v2` + history revision), joins them to one **Musical Universe**, soft-conditions each generate with one **Composer Profile**, and stamps shared **production targets**.
3. **Adaptive-score concepts** as slot ↔ state labels / optional per-asset `adaptive.score.v1` scaffolds that reference that asset’s V2 without copying notes.
4. Consistency in motif vocabulary (universe Theme A + mechanical reuse / pins), harmonic language + instrumentation (plan hard constraints + profile soft), loudness + render settings (shared production targets; optional neural enqueue policy).
5. **Partial regeneration** of selected `slot_id`s; untouched assets stay byte-identical.
6. Agents-tab Asset Pack UI + **mocked autonomous tests** under `LLM_FAKE_MODE`.

Acceptance: one pack brief creates several distinct but musically related soundtrack assets with traceable shared themes (universe theme + motif pins + dependency edges), after an inspectable AssetPackPlan. Regenerating one slot does not rewrite the others. Fake-mode tests cover plan → generate → theme trace → partial regen without real LLM/neural weights.

```text
asset.pack.brief.v1
        │ compile (no notes)
        ▼
asset.pack.plan.v1  (AssetPackPlan — slots, constraints, production, theme policy)
        │ explicit Generate
        ▼
musical.universe.v1  ◄── membership ──►  project per slot (composition.v2)
composer.profile.v1  ── soft generate ─►  each slot generate/autonomous run
asset.pack.production.v1 ── shared loudness / render / palette
adaptive.score.v1 (optional per asset) ── state labels from slot roles
musical.dependency.edge.v1 ── theme → asset / variation_of
```

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**. **Asset pack** = durable `asset.pack.v1` run/document that owns a plan revision and slot→`project_id` map; it is not a score. **AssetPackPlan** = `asset.pack.plan.v1`, non-playable, required before generate. **Slot** = one planned soundtrack asset (`slot_id` + role/label). **Asset project** = ordinary SQLite project with independent revisions. **Shared production targets** = `asset.pack.production.v1` (loudness goal, master target id, neural adapter/fidelity policy, catalog instrument palette). **Theme vocabulary** = Musical Universe Theme A (and declared variants) referenced by motif pins — not pitches stored on the pack. **Adaptive slot label** = closed string matching game/adaptive state concepts (`menu`, `explore_forest`, `combat_high`, …); may seed optional per-asset adaptive scores. **Partial regeneration** = regenerate named slots only. **Commit / Generate** for the pack is the only path that creates asset projects from a plan. Opening the Agents Asset Pack panel never auto-generates.

## Approach Evaluation (locked)

### Part A — Where the pack lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One multi-section `composition.v2` with section labels as “assets”** | Reuses autonomous composer as-is | Fails “independent project/revision”; export/game handoff is one score | **Reject** |
| **B. New folder/franchise table owning projects** | Familiar hierarchy | Duplicates Musical Universe membership as the project group | **Reject** |
| **C. Durable `asset.pack.v1` (+ plan) in its own tables; member projects via universe membership + pack slot map** | Plan/status without second note store; reuses universe as franchise | Migration + orchestrator | **Accepted** |
| **D. Universe-only, no pack document** | Fewer tables | No AssetPackPlan, production targets, or generation status | **Insufficient** |

### Part B — Plan before generation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Immediate generate from brief** | Fewer clicks | Violates “Create AssetPackPlan before generation” | **Reject** |
| **B. Compile non-playable `asset.pack.plan.v1` preview; explicit Generate writes projects** | Matches film-score / autonomous plan-preview honesty | Two-step UX | **Accepted** |
| **C. Reuse `creative.brief.v1` / `project.plan.v1` unchanged as the only plan** | Less schema | Single-project stage graph; no slots / production / adaptive labels | **Reject** as sole plan (brief may *embed* soft fields, but AssetPackPlan is required) |

### Part C — How each asset is generated

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One LLM response with N compositions** | One call | Bypasses validation, history CAS, fake engines; hard to partial-regen | **Reject** |
| **B. Orchestrator creates N projects; each slot runs existing autonomous / symbolic generate with shared profile + plan hard constraints; Main Theme seeds universe Theme A; other slots get mechanical reuse and/or soft motif conditioning** | Reuses tested pipelines; independent revisions | Longer wall time; need slot budgets | **Accepted** |
| **C. Only mechanical reuse of Main Theme into empty projects (no generate)** | Cheap | Menu/Combat/Boss need distinct form density — reuse alone fails acceptance | **Reject** as sole engine |

### Part D — Consistency (motif / harmony / instrumentation / loudness / render)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Copy notes into pack `body_json`** | Self-contained | Forbidden second note representation | **Reject** |
| **B. Universe Theme A + motif pins; plan hard key/meter/tempo/instrument palette; Composer Profile soft; `asset.pack.production.v1` for loudness/master_target/neural policy; dependency edges for trace** | Aligns with shipped axioms | Orchestration complexity | **Accepted** |
| **C. Identical notes in every asset** | Trivial consistency | Fails “distinct” assets | **Reject** |

### Part E — Adaptive-score concepts

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One `adaptive.score.v1` spanning all pack projects** | One graph | Adaptive scores are project-scoped and reference one composition; cross-project note refs unsupported | **Reject** |
| **B. Pack plan slots carry adaptive state labels; after generate, optionally create a minimal per-asset `adaptive.score.v1` whose `initial_state_id` / state set reflects that slot (and Combat Low/High as intensity-ready siblings when both exist). Pack document stores only ids + labels — never events** | Reuses adaptive concepts honestly | Extra optional writes | **Accepted** |
| **C. Ignore adaptive** | Smaller | User required adaptive-score concepts | **Reject** |

### Part F — Partial regeneration

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Delete pack and regenerate all** | Simple | Violates partial regen; destroys untouched assets | **Reject** |
| **B. `POST .../regenerate` with `slot_ids[]`; rewrite only those projects (new revision or replace-with-CAS); leave other slot projects byte-identical; refresh universe usage / dependency edges for those slots only** | Matches acceptance | Must refuse unknown slots; budget caps | **Accepted** |
| **C. Dependency-graph auto-regenerate from Theme A impact** | Fancy | Graph plan forbids auto rewrite of dependents | **Reject** |

### Part G — UI placement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New top-level Packs tab** | Strong branding | Tab sprawl; pack job is orchestration like Autonomous / Film Score | **Reject** for ship-1 |
| **B. Agents-tab Asset Pack panel** beside Autonomous / Film Score | Same “plan then commit” surface | Agents tab grows | **Accepted** |
| **C. Universe-tab only** | Franchise proximity | Universe must not own generate/LLM orchestration | **Reject** as sole UI |

### Part H — Production / render policy

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Per-slot freeform neural settings** | Flexible | Breaks pack loudness/render consistency | **Reject** as default |
| **B. Shared `asset.pack.production.v1` on the plan: `master_target` (`mix.master_target.v1` ids), `loudness_goal_lufs`, catalog `instrument_ids`, neural `adapter_kind` / `fidelity_class` policy, `include_rendering` default false** | One source of truth; reuse mix master-target vocabulary | Goals remain non-guarantees (`guarantee: false`) | **Accepted** |
| **C. Require neural renders for acceptance** | Audible pack | Heavy; autonomous already defaults render off | **Reject** — symbolic + theme trace is hard acceptance; render optional |

### Part I — Composer Profile soft-conditioning (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Add `profile_id` to `AutonomousRunStartV1`** | Clean long-term | Expands autonomous surface; out of pack ship-1 | **Reject for ship-1** (follow-up ok) |
| **B. Pack orchestrator calls `resolve_profile_merge(profile_id, strength="normal")` and folds soft prefs into each slot `creative.brief.v1` (narrative / instrumentation hints only); plan hard constraints always win; stamp `composer_profile_id` + strength on pack provenance** | Reuses film-score / generate merge helper; no autonomous schema churn | Soft prefs are brief-shaped, not a prompt fragment on the autonomous runtime | **Accepted** |
| **C. Profile id on pack only; never influence generates** | Tiny | Fails “use Composer Profile” acceptance | **Reject** |

### Part J — Generate execution model (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Background job + poll only** | Better UX for long packs | New job runtime; autonomous already sync in fake e2e | **Reject for ship-1** |
| **B. Synchronous `POST .../generate`**: for each slot in deterministic order, write status, run autonomous/fake, then next; crash → pack `partial`; SPA awaits one POST (GET slots for resume/status)** | Matches autonomous e2e; resume via regenerate/pending slots | Long wall time with real LLM | **Accepted** (fake CI stays fast; per-slot duration caps apply) |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Musical Universe | `musical.universe.v1`, membership, Theme A bind, mechanical reuse, motif pins | Franchise container + theme vocabulary across asset projects |
| Dependency graph | `musical.dependency.edge.v1`, impact, `variation_of` | Trace Theme A → slot assets; stale offers for regen UX (no auto-rewrite) |
| Composer profiles | `composer.profile.v1`, resolve/merge soft fragment on generate | Soft harmonic/instrument/style consistency; never override hard constraints |
| Autonomous composer | `creative.brief.v1` → `project.plan.v1` → staged run; one `project_id` | Per-slot engine under orchestrator; fake e2e pattern |
| Adaptive score | Project-scoped `adaptive.score.v1` states/layers/transitions (refs only) | Optional per-asset scaffold from slot adaptive labels |
| Mix master targets | `mix.master_target.v1` (`dynamic` / `streaming` / `cinematic` / `demo`) + `loudness_goal_lufs` | Vocabulary for shared production targets |
| Neural audio | Job enqueue with `adapter_kind` / `fidelity_class`; never mutates V2 | Optional pack render policy |
| Project history | Durable revisions / CAS / fingerprints | Each asset remains an independent project/revision |
| Agents UI | `MultiAgentPanel.jsx` mounts `AutonomousComposerPanel`, `FilmScorePanel`, `FilmScoreAdaptPanel` | Sibling `AssetPackPanel` in the same Agents panel |
| Agent boundary | `test_ai_agents_architecture.py` forbids store imports | Extend forbid list for pack store/modules |
| Alembic | Latest `20261003_0027_spatial_scenes.py` | Next revision `20261003_0028` (or next free id) |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| AssetPackPlan / brief / pack DTOs | No `asset.pack.*.v1` |
| Pack store + migration | No slot→project map or pack run status |
| Pack plan compiler | Brief → slots + production + constraints |
| Pack orchestrator | Multi-project generate + universe join + Theme A seed + profile attach |
| Profile → brief merge | `AutonomousRunStartV1` has no `profile_id`; need pack-side `resolve_profile_merge` |
| Motif / reuse discovery | Seed motif by label + `original`; destination track + CAS for `reuse_theme` |
| Partial regenerate route | Slot-scoped rewrite |
| Adaptive slot scaffolding | Optional per-asset adaptive score with valid material refs |
| Agents UI | Asset Pack panel |
| Mocked autonomous pack tests | Multi-project fake e2e |

### Coupling risks to avoid

1. Inventing `composition.v5` or storing `tracks`/`events`/`notes` on pack plan/body.
2. Putting all assets into one project as sections.
3. Auto-generate / auto-universe-join on Agents tab open.
4. `ai_agents/` importing `asset_pack_store`, `musical_universe_store`, `project_store`, or autonomous store.
5. Copying melodies into Composer Profile or pack JSON.
6. Overriding hard `GenerationConstraints` with profile soft prefs.
7. Auto-rewriting stale dependents via dependency graph.
8. One adaptive score referencing multiple projects’ note events.
9. Writing `DATASET_ROOT` or using it as a pack root.
10. Editing `ROADMAP.md` during implementation.
11. Logging briefs, prompts, event arrays, or full pack `body_json` at INFO.
12. Requiring neural renders for hard acceptance.
13. Remote LLM in unit tests — use `LLM_FAKE_MODE` + fake symbolic.
14. Breaking existing single-project autonomous routes.
15. Extending `AutonomousRunStartV1` with `profile_id` in this milestone (Part I.A reject).
16. Calling `reuse_theme` without destination branch CAS fields / pitched track id.
17. Adaptive scaffold with state id only and no `AdaptiveMaterialRefV1`.

## Scope And Decisions

### In scope
- `asset.pack.brief.v1`, `asset.pack.plan.v1` (AssetPackPlan), `asset.pack.production.v1`, `asset.pack.v1` (+ slot status).
- Alembic tables for packs / slots.
- Deterministic plan compiler + HTTP preview (locked durations + propagate table).
- Pack-side Composer Profile brief merge (`asset_pack_brief.py` / Part I.B).
- Explicit **synchronous** generate orchestrator (create projects, universe, Theme A discovery/bind, per-slot autonomous/symbolic, `reuse_theme` CAS, dependency edges, optional adaptive scaffolds with material refs).
- Partial regenerate by `slot_ids`.
- Shared production targets applied as generate constraints + optional neural policy.
- Agents-tab Asset Pack UI via `MultiAgentPanel.jsx`.
- Mocked autonomous tests + architecture forbid updates.
- Docs: new `docs/asset-packs.md` + AGENTS.md / README pointers.

### Out of scope
- New workspace Packs tab.
- Single-score multi-section “pack”.
- Auto-regenerate from dependency impact.
- Cross-project adaptive score graph.
- Guaranteed loudness (`guarantee` stays false).
- Real neural/MusicGen weights in CI.
- Film-score / picture / Ardour as pack path.
- Personal LoRA training as pack step.
- Extending `AutonomousRunStartV1` with first-class `profile_id` (Part I.A; follow-up ok).
- Background pack job queue (Part J.A).
- Editing `ROADMAP.md`.
- Marketplace / zip export of entire pack (may be a later milestone).

### Architecture decisions (locked)

**1. Documents / schemas**

New module `backend/app/asset_pack_schemas.py`. `extra=forbid`. Forbidden playable top-level keys (same spirit as autonomous/film): `tracks`, `events`, `notes`, `note_events`, `musicxml`, `midi`, `wav`, `composition`, `harmony` (as playable), `analysis`. Reject embedded `FORBIDDEN_NOTE_KEYS` anywhere in pack bodies (`events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`).

| Document | Role |
|----------|------|
| `asset.pack.brief.v1` | User goal: title, slot list or preset (`game_soundtrack_v1`), optional profile id, production prefs, seed |
| `asset.pack.plan.v1` | **AssetPackPlan** — locked slots, hard constraints, production, theme policy, adaptive labels |
| `asset.pack.production.v1` | Shared loudness / master_target / instrument palette / neural policy |
| `asset.pack.v1` | Durable pack: plan digest, universe id, profile id, slot→project/run status, document_revision |

**Preset `game_soundtrack_v1` slot catalog (locked default):**

| `slot_id` | Label | Adaptive label | Density |
|-----------|-------|----------------|---------|
| `main_theme` | Main Theme | `main_theme` | dense |
| `menu` | Menu | `menu` | sparse |
| `explore_forest` | Exploration Forest | `explore_forest` | moderate |
| `explore_city` | Exploration City | `explore_city` | moderate |
| `combat_low` | Combat Low | `combat_low` | moderate |
| `combat_high` | Combat High | `combat_high` | dense |
| `boss` | Boss | `boss` | dense |
| `victory` | Victory | `victory` | moderate |
| `defeat` | Defeat | `defeat` | sparse |
| `credits` | Credits | `credits` | moderate |

Custom briefs may subset or rename labels but must keep stable `slot_id` tokens `^[a-z][a-z0-9_]{1,31}$`. Cap **16** slots per pack.

`AssetPackPlanV1` required fields (summary):

- `schema_version`, `title`, `slots[]` (`slot_id`, `label`, `adaptive_label`, `density`, `duration_bars` or seconds band, `narrative_intent`)
- `constraints`: shared `opening_key`, `time_signature`, `tempo_bpm` range, `instrumentation` catalog ids, `forbidden_instrument_families`
- `production`: `asset.pack.production.v1`
- `theme_policy`: `seed_slot_id` (default `main_theme`), `propagate` per non-seed slot (locked table below), `require_motif_pin: true`, default destination start bar `1`
- `composer_profile_id` optional; `composer_profile_strength` default `normal`
- `include_rendering` default `false`
- `include_adaptive_scaffolds` default `true`
- `plan_digest` (sha256 of canonical plan JSON)
- Per-slot duration bands (locked defaults): seed `duration_seconds=90`, non-seed `60`, hard max per slot `120` (still within autonomous `DURATION_SECONDS_MIN`/`MAX`)

**Locked default `theme_policy.propagate` table:**

| `slot_id` | `operation` | Parameters |
|-----------|-------------|------------|
| `menu` | `repeat` | — |
| `explore_forest` | `transpose` | `transpose_semitones: 2` |
| `explore_city` | `transpose` | `transpose_semitones: 5` |
| `combat_low` | `transpose` | `transpose_semitones: 3` |
| `combat_high` | `transpose` | `transpose_semitones: 7` |
| `boss` | `transpose` | `transpose_semitones: 12` |
| `victory` | `transpose` | `transpose_semitones: 4` |
| `defeat` | `transpose` | `transpose_semitones: -5` |
| `credits` | `repeat` | — |

Custom briefs may override propagate per slot within mechanical ops only. Compiler emits this table for `game_soundtrack_v1`.

`asset.pack.production.v1`:

- `master_target`: `MixPlanMasterTargetId` default `cinematic`
- `loudness_goal_lufs`: optional float (goal only)
- `guarantee: false` literal
- `catalog_instrument_ids`: 1..8
- `neural_adapter_kind` / `neural_fidelity_class` optional policy for later enqueue
- No PCM, no stems, no event arrays

**2. Tables**

`backend/app/db/alembic/versions/20261003_0028_asset_packs.py` (`down_revision` = `20261003_0027`).

- `asset_packs`: `id` (`apack_` + 16 hex), `name`, `schema_version`, `body_json`, `plan_json`, `document_revision`, `universe_id` nullable, `composer_profile_id` nullable, `status`, `created_at`, `updated_at`
- `asset_pack_slots`: `pack_id`, `slot_id`, `project_id` nullable, `autonomous_run_id` nullable, `adaptive_score_id` nullable, `status`, `head_revision_id` nullable, `updated_at`; PK `(pack_id, slot_id)`

Store: `backend/app/services/asset_pack_store.py` — CAS on `document_revision`; secret guard + embedded-note reject; **does not** write `composition_json`. Orchestration that needs scores uses `project_store` / autonomous / universe services from `asset_pack_service.py` (not from `ai_agents/`).

**3. HTTP**

`backend/app/routers/asset_packs.py`, registered from `main.py`.

| Route | Behavior |
|-------|----------|
| `POST /asset-packs/plan/preview` | Brief → AssetPackPlan; **no** SQLite project create |
| `POST /asset-packs` | Persist pack + plan (status `planned`); no generate |
| `GET /asset-packs` / `GET /asset-packs/{id}` | List/detail (no event arrays) |
| `POST /asset-packs/{id}/generate` | Explicit generate all pending slots (or resume); requires matching `expected_plan_digest` + CAS |
| `POST /asset-packs/{id}/regenerate` | Body `{ slot_ids, expected_revision }`; Part F.B |
| `GET /asset-packs/{id}/slots` | Slot status + project ids + theme usage ids when present |

Map domain errors to stable codes (`asset_pack_invalid`, `asset_pack_conflict`, `asset_pack_slot_unknown`, `asset_pack_plan_mismatch`, `asset_pack_generate_failed`, …). Prefer routers + services over growing `main.py`.

**4. Generate orchestration (locked order)**

`backend/app/services/asset_pack_service.py` + `asset_pack_generate.py` (+ helpers below). **Synchronous** (Part J.B).

1. Load pack + plan; refuse digest mismatch (`asset_pack_plan_mismatch` 409).
2. Ensure Musical Universe: create named `{pack.title} Universe` or use `universe_id`; add members as projects appear.
3. **Profile (Part I.B):** if `composer_profile_id` set, call `resolve_profile_merge(..., strength=plan.composer_profile_strength or "normal")` once; never log `soft_fragment` at INFO. Fold soft prefs into each slot brief via `asset_pack_brief.py` (narrative text / instrumentation **hints** only). Plan hard `constraints` always win.
4. **Seed slot** (`main_theme` by default): create project → `prepare_run` / autonomous (or fake) with slot `creative.brief.v1` (duration defaults above) + hard constraints; `include_rendering` from plan. Write slot status `running` → terminal before continuing.
5. On seed completion: **motif discovery** — motif definition whose `label` equals plan/brief `motif_label` (default `Theme A`) with an `original` occurrence; else `asset_pack_seed_motif_missing` (409/422) and pack may be `failed`/`partial`. Bind Theme A; `capture_theme_reuse_edge` / theme edge as applicable.
6. **Non-seed slots** (deterministic order by `slot_id`): create project → autonomous/fake generate with same hard constraints + slot density/intent → **mechanical reuse** via `reuse_theme` using locked propagate table: destination track = first pitched non-drum track preferring `role: melody`; start bar default `1`; fill `ThemeReuseRequest` CAS from destination branch state (`branch_id`, working version, head revision, fingerprint) + universe revision. Motif pin + `variation_of` edge (reuse path already captures when wired).
7. If `include_adaptive_scaffolds`: create minimal `adaptive.score.v1` per asset (see §4b). Default one state per asset project.
8. Update slot rows after each slot; final pack status `completed` / `partial` / `failed`. Never leave orphan writes without status.

Honesty: non-seed assets are **related** via theme transform + shared constraints, not identical clones.

**4b. Adaptive scaffold shape (locked)**

Per asset project, when enabled:

- One state: `id` = slot `adaptive_label` (must match adaptive entity token rules; if label invalid for `_ID_RE`, use a sanitized token and keep label in `name`).
- `initial_state_id` / `default_state_id` = that state.
- `material`: prefer `kind: "section"` with the composition’s first section id when present; else `kind: "bar_range"` with `start_bar: 1` and `end_bar` = compiled bar count.
- Empty `transitions`, `layers`, `stingers`, `variants`.
- Create via existing adaptive score store/API helpers; **never** rewrite `composition.v2` notes.
- Invalid material → skip scaffold with WARN + slot warning code; do not fail the whole pack.

**5. Partial regenerate**

- Input: `slot_ids` non-empty subset.
- For each: new autonomous/symbolic run into that project (CAS replace or durable commit with operation_type `asset-pack-slot-regenerate` when the pack path commits outside `autonomous-stage`); re-run theme reuse for that destination; refresh dependency edge fingerprints.
- Slots not listed: project rows and composition snapshots unchanged (byte-identical assertion in tests).
- Regenerating `main_theme` marks other theme usages stale via existing impact read; does **not** auto-regen others (offer only).

**6. Composer Profile + hard constraints**

- Strength default `normal` (Part I.B).
- Plan `constraints` compile into the same hard constraint shape autonomous already enforces.
- Soft merge is brief-fold only; conflict → hard wins.
- Do not add `profile_id` to `AutonomousRunStartV1` in ship-1.

**7. UI**

`frontend/src/components/AssetPackPanel.jsx` mounted from `frontend/src/components/MultiAgentPanel.jsx` (beside Autonomous / Film Score / Film Score Adapt):

1. Brief form (preset game pack checkbox + title + optional profile select + strength default normal + production master_target).
2. Preview AssetPackPlan (slot table, constraints, production, theme policy / propagate table) — not playable.
3. Save pack / Generate (awaits sync POST; show per-slot status from response or follow-up GET).
4. Slot status list (project link/open, run status, theme pin indicator).
5. Multi-select Partial regenerate.
6. Copy: each slot is its own project; Universe holds shared themes; Adaptive scaffolds are optional graphs over that project’s V2.

API client: `frontend/src/api/assetPackApi.js`. Opening panel lists packs; never POSTs generate.

**8. Logging (verbose)**

- INFO: pack id truncated, plan digest prefix, slot_id, project_id truncated, status transitions, regenerate slot set size.
- DEBUG: compiler slot counts, universe id truncated, profile id present/absent, adaptive scaffold created/skipped.
- WARN/ERROR: digest mismatch, slot unknown, generate failure codes.
- Never: prompts, soft fragments, event arrays, full brief narrative text at INFO, WAV bytes.

**9. Feature flags**

No new env flag required for ship-1 (always available like universe). Optional `ASSET_PACK_MAX_SLOTS` / duration caps may live in `asset_pack_settings.py` with safe defaults. Prefer no new env unless caps need ops knobs; if added, document in `.env.example`.

**10. Agent boundary**

Extend `test_ai_agents_architecture.py` forbidden imports with `asset_pack_store`, `asset_pack_service`, `asset_pack_generate`, and related SQLite-touching pack modules. Pack orchestration stays in `services/` + `routers/`; may call autonomous / universe / adaptive services the same way other routers do — **`ai_agents/` package itself does not import pack modules**.

**11. History operation types**

Add to `RevisionOperationType` and `AI_ORIGIN_OPERATIONS` as needed:

- `asset-pack-generate` — only if the pack path commits a revision outside `autonomous-stage` / `musical-universe-theme-apply`
- `asset-pack-slot-regenerate` — same rule for partial regen commits

Universe reuse continues to use `musical-universe-theme-apply`. Autonomous stage commits stay `autonomous-stage`.

## Commit Plan

- **Commit 1** (tasks 1–2b): `feat(asset-pack): add asset.pack schemas, migration, plan compiler, and profile brief merge`
- **Commit 2** (tasks 3–4): `feat(asset-pack): generate orchestrator with universe theme and partial regen`
- **Commit 3** (tasks 5–6): `feat(asset-pack): Agents-tab panel and mocked autonomous pack tests`
- **Commit 4** (tasks 7–8): `docs(asset-pack): soundtrack asset-pack workflow and AGENTS map`

## Tasks

### Phase 1: Schemas, migration, plan compiler, profile brief merge

- [x] Task 1: Add `backend/app/asset_pack_schemas.py` with `asset.pack.brief.v1`, `asset.pack.plan.v1`, `asset.pack.production.v1`, `asset.pack.v1`, slot status enums, error codes/HTTP map (incl. `asset_pack_seed_motif_missing`), forbidden-key reject helpers, and the locked `game_soundtrack_v1` preset catalog + propagate table constants. Unit-test validation (extra fields, embedded notes, slot id regex, production `guarantee: false`, propagate table completeness for the preset).

  LOGGING REQUIREMENTS:
  - DEBUG: schema reject field path + stable code only
  - Never log full brief/plan bodies at INFO

  Files: `backend/app/asset_pack_schemas.py`, `backend/tests/test_asset_pack_schemas.py`

- [x] Task 2: Alembic `20261003_0028_asset_packs.py` + `asset_pack_store.py` (CAS, secret guard, embedded-note reject) + pure compiler `asset_pack_plan.py` (brief → AssetPackPlan with digests; locked duration defaults seed `90` / non-seed `60` / max `120`; locked `theme_policy.propagate` table). `POST /asset-packs/plan/preview` and `POST/GET /asset-packs` in `routers/asset_packs.py`. No generate yet. (depends on 1)

  LOGGING REQUIREMENTS:
  - INFO: pack create id truncated, status `planned`, slot count
  - DEBUG: plan digest prefix, preset id, duration defaults applied
  - WARN: CAS conflict codes

  Files: `backend/app/db/alembic/versions/20261003_0028_asset_packs.py`, `backend/app/services/asset_pack_store.py`, `backend/app/services/asset_pack_plan.py`, `backend/app/routers/asset_packs.py`, `backend/app/main.py`, `backend/tests/test_asset_pack_plan_preview.py`, `backend/tests/test_asset_pack_store.py`

- [x] Task 2b: Pack-side Composer Profile brief merge (Part I.B). Add `backend/app/services/asset_pack_brief.py` that calls `resolve_profile_merge(profile_id, strength=...)` and folds soft prefs into slot `creative.brief.v1` narrative/instrumentation **hints** only; never mutates plan hard constraints; never extends `AutonomousRunStartV1`. Unit-test: hard instruments/key win over soft; empty profile → identity brief; never logs soft_fragment at INFO. (depends on 1, 2)

  LOGGING REQUIREMENTS:
  - DEBUG: profile merge applied/skipped, strength, applied_field_count (no fragment text)
  - WARN: unknown profile id → stable pack error code
  - Never log soft_fragment contents

  Files: `backend/app/services/asset_pack_brief.py`, `backend/tests/test_asset_pack_brief.py`

<!-- Commit checkpoint: tasks 1-2b -->

### Phase 2: Generate orchestrator + partial regen + adaptive scaffolds

- [x] Task 3: Implement **synchronous** generate orchestration in `backend/app/services/asset_pack_generate.py` (+ thin `asset_pack_service.py` facade): create projects per slot; build each slot brief via Task 2b; run autonomous/fake per slot with plan hard constraints; create/join Musical Universe; **seed motif discovery** (`motif_label` + `original`) or `asset_pack_seed_motif_missing`; bind Theme A; non-seed `reuse_theme` with locked propagate table, destination track = first pitched non-drum (`melody` preferred), start bar `1`, full `ThemeReuseRequest` CAS from destination branch + universe revision; dependency edges; write slot status before next slot; crash/`partial` honesty. `POST /asset-packs/{id}/generate` with plan digest CAS. Add `asset-pack-generate` / `asset-pack-slot-regenerate` to `RevisionOperationType` + `AI_ORIGIN_OPERATIONS` only where pack commits outside `autonomous-stage` / `musical-universe-theme-apply`. Fake-mode deterministic path mandatory. (depends on 2, 2b)

  LOGGING REQUIREMENTS:
  - INFO: generate start/end, pack id truncated, each slot_id → project_id truncated + terminal status
  - DEBUG: universe id truncated, theme bind ok, reuse operation name + semitone (no pitches), profile merge present/absent
  - ERROR: per-slot failure code without aborting unrelated completed slots (pack status `partial` when mixed); seed motif missing code
  - Format: `[AssetPackGenerate.*]` style extras; no prompts/events/soft_fragment

  Files: `backend/app/services/asset_pack_generate.py`, `backend/app/services/asset_pack_service.py`, `backend/app/routers/asset_packs.py`, `backend/app/project_history_schemas.py`, `backend/app/services/collaboration_permissions.py`, `backend/tests/test_asset_pack_generate.py`

- [x] Task 4: Partial regenerate + optional adaptive scaffolds (§4b). `POST /asset-packs/{id}/regenerate` with `slot_ids`; assert non-selected projects unchanged; regenerating seed marks others stale via existing impact (no auto fan-out). When `include_adaptive_scaffolds`, create minimal per-asset `adaptive.score.v1`: state id = `adaptive_label` (sanitize to entity token if needed), material = first section or `bar_range` 1..bar_count, empty transitions/layers/stingers; invalid material → WARN skip, not pack fail. Extend architecture forbid list for pack store/generate/brief imports in `ai_agents/`. (depends on 3)

  LOGGING REQUIREMENTS:
  - INFO: regenerate slot_ids count + list of slot_id tokens only
  - DEBUG: adaptive_score_id truncated created/skipped; material kind chosen
  - WARN: unknown slot_id → `asset_pack_slot_unknown`; scaffold skip reason code

  Files: `backend/app/services/asset_pack_generate.py`, `backend/app/routers/asset_packs.py`, `backend/tests/test_asset_pack_regenerate.py`, `backend/tests/test_ai_agents_architecture.py`

<!-- Commit checkpoint: tasks 3-4 -->

### Phase 3: UI + mocked autonomous acceptance

- [x] Task 5: Agents-tab `AssetPackPanel.jsx` + `assetPackApi.js`: brief → plan preview → save → generate → slot status → partial regen multi-select. Mount from `MultiAgentPanel.jsx` beside Autonomous / Film Score / Film Score Adapt (not a new workspace tab). Profile picker + strength default `normal`. Opening never auto-generates. Unit tests for request shaping / gating. (depends on 2, 2b, 3, 4)

  LOGGING REQUIREMENTS:
  - Frontend `createAppLogger`: INFO on generate/regen clicks with pack id suffix; WARN on API error codes; never log plan JSON dumps or soft fragments

  Files: `frontend/src/components/AssetPackPanel.jsx`, `frontend/src/api/assetPackApi.js`, `frontend/src/components/MultiAgentPanel.jsx`, related unit tests

- [x] Task 6: Mocked autonomous pack acceptance tests under `LLM_FAKE_MODE` (pattern from `test_autonomous_composer_e2e.py`): preview plan for `game_soundtrack_v1` → persist → generate → assert ≥3 (prefer full 10) distinct `project_id`s; seed motif pins Theme A; at least one non-seed project has universe motif pins + dependency `variation_of` edge; shared key/instrument constraints hold; pack stamps `composer_profile_id` when provided (soft merge path exercised); regenerate one non-seed slot changes only that project’s head revision fingerprint; pack/plan JSON contain no forbidden note keys; adaptive scaffold present when enabled with material ref. (depends on 2b, 3, 4)

  LOGGING REQUIREMENTS:
  - Tests may cap log capture; assert stable error codes on mismatch / seed-motif-missing paths
  - No network LLM; no neural weights

  Files: `backend/tests/test_asset_pack_autonomous_e2e.py`, fixtures as needed under `backend/tests/` or `backend/app/fixtures/`

<!-- Commit checkpoint: tasks 5-6 -->

### Phase 4: Docs

- [x] Task 7: Write `docs/asset-packs.md` covering brief → AssetPackPlan → generate → universe theme trace → production targets → profile brief-merge (not AutonomousRunStart profile_id) → partial regen → adaptive scaffolds with material refs; honesty limits (loudness goals not guarantees; render optional; sync generate). (depends on 5–6)

  LOGGING REQUIREMENTS:
  - Document redaction rules (no event/prompt/soft_fragment logging)

  Files: `docs/asset-packs.md`

- [x] Task 8: Docs checkpoint — update `AGENTS.md` entry points (incl. `MultiAgentPanel` / `AssetPackPanel`), `docs/CODEBASE_MAP.md` if present, README feature pointer, cross-links from `docs/musical-universe.md` / `docs/autonomous-composer.md` / `docs/composer-profiles.md` / `docs/adaptive-score.md`. Mandatory `/aif-docs` alignment. Do not edit `ROADMAP.md`. (depends on 7)

  LOGGING REQUIREMENTS:
  - N/A for prose

  Files: `AGENTS.md`, `README.md`, `docs/musical-universe.md`, `docs/autonomous-composer.md`, `docs/composer-profiles.md`, `docs/adaptive-score.md`, optionally `docs/CODEBASE_MAP.md`

<!-- Commit checkpoint: tasks 7-8 -->

## Implementation Notes

- Prefer `routers/asset_packs.py` + `services/asset_pack_*.py` over growing `main.py`.
- Reuse autonomous fake path and universe reuse pitch math; do not reimplement transpose.
- Seed slot must complete before non-seed theme reuse; if seed motif missing, fail that path with `asset_pack_seed_motif_missing` — no silent empty themes.
- Combat Low/High are distinct projects by default; intensity layering across them is adaptive-runtime concern, not pack note merging.
- Production `loudness_goal_lufs` is a goal for later mix/neural policy — do not invent a DSP guarantee in ship-1.
- Caps: max 16 slots; per-slot duration seed 90 / non-seed 60 / max 120; synchronous generate writes status per slot.
- History ops: add `asset-pack-generate` / `asset-pack-slot-regenerate` only when pack commits outside existing autonomous/universe ops.
- Composer Profile: pack-side brief merge only in ship-1 (Part I.B).

## Verification Gate (for `/aif-verify`)

1. `POST /asset-packs/plan/preview` returns `asset.pack.plan.v1` with game preset slots, locked propagate table, duration defaults, and no playable keys.
2. Explicit sync generate under `LLM_FAKE_MODE` creates multiple projects, one universe, Theme A pins, and dependency edges.
3. Partial regenerate changes only selected slot project fingerprint.
4. Optional adaptive scaffolds exist per asset with matching state ids **and** section/`bar_range` material refs when enabled.
5. Profile brief merge: hard constraints win; `composer_profile_id` stamped when provided; `AutonomousRunStartV1` unchanged.
6. Agents UI unit tests: mounted from `MultiAgentPanel`; no auto-generate on mount; plan preview before generate enabled.
7. `ai_agents/` forbid list includes pack store/generate/brief modules.
8. Docs describe AssetPackPlan-before-generate, independent project/revision rule, and profile brief-merge.
9. No `composition.v5`; no `DATASET_ROOT` writes; no note arrays on pack rows.
