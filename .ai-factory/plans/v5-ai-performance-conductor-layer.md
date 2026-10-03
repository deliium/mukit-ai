# Implementation Plan: V5 AI Performance / Conductor Layer

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: thin Performance studio tab — preset / plan select, before/after audition toggle, soft-stale banner; no piano-roll redesign, no DAW automation editor
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Task 1 locks rubato→tick_delta only (no Transport.bpm), `composition_snapshot_fingerprint`, tie-chain shared deltas, duration floor, API-only realize (no JS engine twin); Task 2 refuses embedded events/pitch/harmony in plan bodies; Task 4 depends on realize; Tasks 5–6 lock `expected_document_revision` CAS + request-composition fingerprint; Task 6 requires composition on realize/compare; new Task 7 preset catalog (no auto-INSERT on tab open); Task 8 wires `ComposerWorkspace` + `PLAYBACK_SOURCE_KIND_PERFORMANCE`; Task 9 adds tie-chain + unsaved-draft acceptance
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: introduce a durable conductor layer separate from Composition so the same canonical notes receive different expressive performances. Deterministic engine + closed presets ship first; AI augmentation is optional and gated. Never invent `composition.v5`. Never silently rewrite pitch or harmony on `composition.v2`. Predecessor `note_performances[]` (capture metadata) stays orthogonal — this plan does not redesign MIDI input.

## Roadmap Linkage
Milestone: "V5 AI performance / conductor layer"
Rationale: Capture-side `note_performances` and V2 written expression already exist, but there is no project-scoped conductor document that can reinterpret one score as multiple performances without mutating note identities. The milestone is appended to `.ai-factory/ROADMAP.md` because linkage is enabled and no incomplete milestone remained. Implementation does not edit `ROADMAP.md`.

## Goal

Allow one `composition.v2` to produce multiple distinguishable expressive performances without changing its canonical note identities (event ids, pitches, harmony spans).

Ship:

1. Versioned non-playable document **`performance.plan.v1`** (`extra=forbid`) describing conductor dimensions: tempo rubato, dynamics, phrasing, articulation, pedaling, microtiming, accent, orchestral balance.
2. Closed **deterministic presets**: `intimate`, `dramatic`, `restrained`, `mechanical` (seed catalog; user **clones** into SQLite rows — never auto-INSERT on tab open).
3. Pure **deterministic conductor** that reads Composition + Plan and emits session **`performance.realization.v1`** (performed event stream / schedule deltas) — never a second playable score.
4. Durable SQLite storage (multiple plans per project), CAS via `expected_document_revision`, composition fingerprint pin, soft-stale when the request composition drifts from the pin.
5. **Before/after comparison**: mechanical (canonical schedule) vs selected plan realization via `PLAYBACK_SOURCE_KIND_PERFORMANCE`.
6. Optional **AI augmentation** stub/path (`engine=ai_augmented`) that may propose plan parameter deltas only — never pitch/harmony rewrites; ship-1 may keep it fake/off.
7. Tests proving one fixture Composition yields distinguishable realizations across presets while pitch/harmony fingerprints stay identical.

```text
composition.v2  (canonical notes + written expression; authoritative)
        │  read-only bind (fingerprint pin)
        ▼
performance.plan.v1  (durable conductor params / preset id)
        │  deterministic conductor (± optional AI param hint)
        ▼
performance.realization.v1  (session performed stream — timing/dynamics/pedal/balance)
        │
        ▼
Tone schedule via schedule-apply helper (canonical export unchanged)
```

**Terminology lock:** Product generation is **V5**. The playable score stays **`composition.v2`**. There is no **`composition.v5`**. A **Performance Plan** is `performance.plan.v1` — durable derived conductor parameters, not notes. A **realization** is `performance.realization.v1` — session (or short-lived derived) performed stream keyed by `event_id`; consumers may ignore it. **Mechanical** means scheduling from canonical V2 fields only (existing Tone path). **Conductor** means applying a Plan to produce a realization. **Note identity** means `tracks[].events[].id` plus pitch and harmony membership — these must not change under conduction. **Capture performance** (`tracks[].note_performances[]` from expressive MIDI input) is orthogonal metadata about how notes were played in; this plan does not replace or require it. **SMF Conductor track** in `composition_midi.py` (section markers) is unrelated naming — do not overload that path. **Preset catalog** is the static four named seeds; a **cloned plan** is a SQLite row. Predecessor: `.ai-factory/plans/v5-expressive-midi-performance-input.md`, `.ai-factory/plans/v5-adaptive-score-domain-model.md`, browser playback mixer audition (`playbackSource.js`).

## Approach Evaluation (locked)

### Part A — Where the conductor lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Mutate `tracks[].events[]` velocities/starts/articulations in-place on Apply** | Simple audition | Silently rewrites Composition; breaks “same notes, different performances”; fights undo/history | **Reject** |
| **B. Widen required note fields / invent `composition.v5` playable schema** | Clean version bump | Forbidden; destabilizes validators, tokenizer, export | **Reject** |
| **C. Stuff conductor curves into `note_performances[]`** | Reuses field | That field is capture-keyed expressive samples; overloading conflates input performance with conductor interpretation | **Reject** |
| **D. Sidecar `performance.plan.v1` + session `performance.realization.v1`; Composition stays read-only for conduction** | Matches adaptive-score / mix-plan sidecar style; multiple plans per project; clear before/after | New schema, migration, API, playback wire | **Accepted** |

### Part B — Persistence of realizations

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Persist full performed note clones as alternate compositions** | Easy A/B | Second score; drifts; invites `composition.v5` | **Reject** |
| **B. Persist only plans; realize ephemerally for audition/export each time (deterministic seed)** | Small storage; restart-safe via recompute | Heavy scores recompute cost | **Accepted** for ship-1 |
| **C. Persist realization blobs next to plans** | Faster re-open | Large rows; stale vs every edit; duplicates capture metadata | **Defer** (optional later cache with digest) |

Ship-1: durable plans only; realizations are recomputed from `(composition_fingerprint, plan_id, plan_revision, engine_version)`.

### Part C — Engine strategy

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. LLM-only conductor that rewrites JSON notes** | “AI” marketing | Non-deterministic; pitch/harmony risk; hard to test | **Reject** as default |
| **B. Deterministic parametric model first; optional AI proposes plan-parameter deltas only (`ai_augmented`), never pitch/harmony** | Meets acceptance; testable presets; AI stays soft | Two engines to document | **Accepted** |
| **C. Neural audio re-performance only** | Audible richness | Wrong layer (PCM egress); does not yield symbolic performed stream | **Reject** for this milestone |

### Part D — Dimension representation

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Freeform script / expression language** | Flexible | Untestable; logging risk | **Reject** |
| **B. Closed numeric parameters per dimension with caps + preset tables; realization emits bounded per-event deltas** | Validatable; deterministic; safe to log codes | New behaviors need a schema bump | **Accepted** |
| **C. Only global mixer gains** | Tiny | Fails rubato / microtiming / phrasing / pedaling | **Insufficient** |

### Part E — Pitch / harmony safety

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Allow conductor to “correct” pitches or rewrite `harmony[]`** | Tempting for “musical” AI | Violates acceptance; silent score rewrite | **Reject** |
| **B. Hard invariant: realization may change timing offsets, sounding duration gates, velocity/dynamics, sustain pedal spans, and track balance gains only; pitch, `harmony[]`, key, event ids unchanged. Postcondition fingerprint of pitch+harmony must match source** | Enforceable in tests | Caps needed so microtiming cannot reorder musical identity badly | **Accepted** |
| **C. Soft warn only** | Faster ship | Not acceptance-grade | **Reject** |

### Part F — Before / after comparison

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Diff panel that mutates working JSON** | Visible | Mutates score | **Reject** |
| **B. Mutual-exclusive playback source kind `performance` (plan id) vs `working` mechanical; optional lightweight summary metrics (mean |Δvelocity|, mean |Δtick|, pedal span count) without dumping events** | Reuses `playbackSource.js` / version audition pattern | Thin UI work | **Accepted** |
| **C. Side-by-side dual Tone engines** | Fancy | Complex; out of thin-UI budget | **Defer** |

### Part G — Product surface

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Schema + store + HTTP + deterministic engine + thin Performance tab + tests + docs** | Meets acceptance | No full expression editor | **Accepted** |
| **B. Also bake performances into SMF as default export** | DAW hears rubato | Changes DAW handoff contract; canonical export must stay mechanical unless explicit “export performed” opt-in | **Accepted opt-in only** (`export_mode=performed` later task optional; default SMF unchanged) |
| **C. Wire into `ai_agents/` spine as required stage** | Agent visibility | Couples agents to conductor; violates sidecar isolation | **Reject** — `ai_agents/` must not import conductor modules |

### Part H — Audition compute locus

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Full JS conductor twin in the SPA** | Offline audition | Dual math drift vs backend acceptance | **Reject** for ship-1 |
| **B. `POST .../realize` with request composition; frontend schedule-apply helper only** | Single engine; matches Analysis-style draft body | Needs network for audition | **Accepted** |
| **C. Auto-INSERT four preset rows on project create / tab open** | Instant list | Silent writes; empty-project side effects | **Reject** — catalog clone only |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Canonical notes | `composition.v2` `tracks[].events[]`; pitch/velocity/articulations/dynamics/sustain/automation | Read-only input to conductor |
| Capture metadata | `tracks[].note_performances[]` (expressive MIDI) | Orthogonal; ship-1 conductor **ignores** capture curves (document; do not delete) |
| Written expression helpers | `articulation_gate_ticks`, `articulation_velocity`, `combined_expression` in `composition_schemas.py` | May inform mechanical baseline; conductor adds plan deltas on top |
| Tie collapse | `composition_logical_notes.collapse_track_tie_chains` | Conductor tie policy (shared `tick_delta` / fan-out) |
| Browser playback | `tonePlaybackEngine.js`, `playbackSource.js`, ephemeral mixer | `PLAYBACK_SOURCE_KIND_PERFORMANCE` + mixer scope `preview` |
| Version / candidate audition | Develop / Arrange / Versions toggles in `musicStore` | Pattern for before/after; clear on project switch |
| Workspace tabs | `ComposerWorkspace.jsx` `TABS` | Add `{ id: 'performance', label: 'Performance' }` |
| Sidecar persistence | `adaptive_scores` body_json + `document_revision` + `expected_document_revision` CAS | Clone pattern for `performance_plans` |
| Fingerprints | `composition_snapshot_fingerprint` | **Locked** soft-stale pin (not `composition_source_fingerprint`) |
| Request composition | Analysis `request.composition` | Realize/compare require draft body |
| Compare helpers | `compositionVersionComparison.js` musical keys | Pitch/harmony identity assertions in tests |
| Role heuristics | `import_instruments.infer_import_role` / track `role` | Orchestral balance biases by role when present |
| SMF “Conductor” track | Section markers in `composition_midi.py` | **Do not reuse name in APIs**; keep export markers unchanged |
| Alembic head | `20261003_0025` (`scheduling_policy`) | Next revision `20261003_0026` |
| Secret guard | `persistence_secret_guard` | Before INSERT/UPDATE |
| Fake LLM | `LLM_FAKE_MODE` / `fake_llm` | Optional AI augment path returns deterministic param deltas |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| `performance.plan.v1` schema | No conductor document; must refuse embedded events |
| Preset seed tables + catalog API/static | intimate / dramatic / restrained / mechanical; no auto-INSERT |
| Deterministic conductor engine | Rubato→tick_delta; tie-aware; duration floor |
| `performance.realization.v1` | No performed stream DTO |
| SQLite `performance_plans` + Alembic | CAS `expected_document_revision` |
| HTTP CRUD + realize/compare | Composition required on realize/compare |
| Playback source `performance` | Missing in `playbackSource.js` |
| Thin Performance tab UI | `ComposerWorkspace` + panel |
| Schedule-apply helper | Apply realization deltas without mutating V2 |
| Before/after metrics helper | Missing |
| Pitch/harmony + tie + unsaved-draft tests | Missing |
| Docs `docs/performance-conductor.md` | Missing |
| Optional AI augment | Stub + flag; default off |

### Coupling risks to avoid

1. Inventing `composition.v5` or a second `events[]` list on the plan.
2. Silently rewriting pitch, `harmony[]`, key, or event ids when auditioning or saving a plan.
3. Overloading `note_performances[]` as the conductor store.
4. Confusing SMF Conductor meta track with this feature in routes/docs.
5. Making AI / LLM required for presets or audition.
6. Default SMF/WAV export switching to performed timing without explicit opt-in.
7. `ai_agents/` importing conductor store/engine.
8. Logging full realization event arrays or composition payloads at INFO.
9. Writing `DATASET_ROOT` or treating plans as training corpus.
10. Editing `ROADMAP.md` during implementation.
11. Letting microtiming deltas reorder notes across barlines without caps.
12. Applying conductor pedal/balance into durable V2 `sustain_pedals` / track `volume` without an explicit future Apply task (out of scope).
13. Mutating `Tone.Transport.bpm` for rubato instead of tick deltas (breaks tick-based compare/export honesty).
14. Shipping a full frontend conductor twin that drifts from backend realize.
15. Auto-INSERT of preset rows on tab open or project create.
16. Soft-stale against saved project JSON while realizing an unsaved draft body.

## Scope And Decisions

### In scope
- `performance.plan.v1` + `performance.realization.v1` Pydantic (+ frontend plan-body validation for UI edits — not a realize twin).
- Deterministic conductor covering all eight dimensions with capped parameters; rubato/microtiming as tick deltas only.
- Four named presets as a **catalog**; clone creates SQLite rows; opening the Performance tab never writes.
- SQLite `performance_plans` (project-scoped, many rows, CAS `expected_document_revision`).
- HTTP: list/create/get/put/delete, preset catalog, `POST .../realize`, `POST .../compare` — realize/compare **require** `composition` in the body.
- Soft-stale when plan `source_composition_fingerprint` ≠ `composition_snapshot_fingerprint(request.composition)`.
- Thin Performance tab in `ComposerWorkspace.jsx`: catalog clone, plan select, mechanical vs performed audition, stale banner.
- Tone schedule-apply from realization when source kind is `performance` (mixer scope `preview`).
- Optional AI augment: env flag default off; fake deterministic deltas when on + fake mode; never pitch/harmony.
- Tests: schema, engine distinguishability, identity invariant, ties, unsaved draft, store CAS, API, schedule-apply, playback source.
- Docs: `docs/performance-conductor.md` + short links from `docs/composition-v2.md`, `docs/browser-playback.md`, `AGENTS.md` entry points.
- `.env.example`: `PERFORMANCE_CONDUCTOR_AI_ENABLED` default false.

### Out of scope
- Baking conductor output into working `composition.v2` on Apply.
- Redesign of expressive MIDI capture / `note_performances`.
- Full DAW automation lane authoring UI.
- Dual simultaneous Tone engines.
- Full frontend conductor twin (parity realize engine in JS).
- `musical.dependency` edge type for “performance_of” (separate milestone if needed).
- Changing default SMF/MusicXML/WAV export to performed (opt-in may land later; not required for acceptance).
- Neural audio / mix-plan integration.
- Adaptive Score runtime using conductor (future).
- Editing `ROADMAP.md` during implementation.
- Marketplace of presets.

### Architecture decisions (locked)

**1. Document `performance.plan.v1`**

| Field | Rule |
|-------|------|
| `schema_version` | `"performance.plan.v1"` |
| `id` | uuid string |
| `project_id` | owning project |
| `name` | display |
| `preset_id` | `intimate` \| `dramatic` \| `restrained` \| `mechanical` \| `custom` |
| `engine` | `deterministic` \| `ai_augmented` (ship-1 realize uses deterministic core; AI only adjusts params when enabled) |
| `source_composition_fingerprint` | pin at last bind/save; algorithm = `composition_snapshot_fingerprint` |
| `dimensions` | closed object — see below |
| `seed` | int for deterministic noise/rubato LFO phase |
| `extra` | forbid |

Plan body **must not** contain `events`, note pitches, or `harmony` arrays — validation refuses with stable reason codes (`plan_embeds_events`, `plan_embeds_harmony`, …).

**Dimensions object (minimum keys; Task 1 locks ranges):**

| Key | Meaning |
|-----|---------|
| `tempo_rubato` | depth, rate, phrase_anchor (`bar`\|`section`) — mapped to per-note `tick_delta` only |
| `dynamics` | curve strength, contrast, floor/ceiling velocity |
| `phrasing` | breath_gap_ticks bias, phrase_arc |
| `articulation` | legato/staccato bias relative to written marks |
| `pedaling` | sustain style (`none`\|`literal`\|`harmonic`\|`dry`) + depth |
| `microtiming` | swing/humanize amplitude in ticks |
| `accent` | metric accent strengths (downbeat/offbeat) |
| `orchestral_balance` | per-role or per-track gain biases (use track `role` / import heuristic; no pitch change) |

**2. Realization `performance.realization.v1`**

Session DTO (not stored in ship-1):

```text
{
  schema_version: "performance.realization.v1",
  plan_id, plan_revision, engine_version,
  source_composition_fingerprint,
  notes: [{
    event_id, track_id,
    tick_delta,           # applied to start; capped
    duration_delta,       # sounding gate adjust; pitch unchanged
    velocity,             # 1..127 performed
  }],
  sustain_spans: [{ track_id, start_tick, end_tick }],  # realization only
  track_gains: [{ track_id, gain }],                    # session 0..1-ish
  metrics: { mean_abs_tick_delta, mean_abs_velocity_delta, ... }
}
```

Rules:
- Every `event_id` must resolve on the source Composition track.
- No pitch field on realization notes (readers take pitch from Composition).
- Empty plan / mechanical preset → near-zero deltas (mechanical may still apply written articulations as today).
- `duration_ticks + duration_delta >= 1` after clamps.
- Tie chains: all members of a tie group receive the **same** `tick_delta` (compute on collapsed logical note, then fan out to `source_event_ids`); do not split chains.

**3. Task 1 freezes (later tasks must not invent alternate math)**

| Topic | Lock |
|-------|------|
| Rubato / microtiming | Per-note `tick_delta` (+ optional `duration_delta`) only — **never** mutate `Tone.Transport.bpm` for conduction |
| Fingerprint | `composition_snapshot_fingerprint` only |
| Microtiming cap | `|tick_delta| ≤ min(N, max(0, duration_ticks - 1))` with Task 1 choosing concrete `N` |
| Rubato window | Local tempo factor in `[0.85, 1.15]` projected into tick deltas |
| Velocity | Performed velocity stays in `1..127` |
| Duration floor | `duration_ticks + duration_delta >= 1` |
| Ties | Shared `tick_delta` across tie-chain members via logical-note collapse + fan-out |
| Audition locus | Backend `realize` only; frontend schedule-apply helper; **no** JS conductor twin |
| Pedaling | Realization `sustain_spans` only — does not write Composition `sustain_pedals` |

**4. Presets (seed parameter tables)**

| Preset | Intent (Task 1 freezes numbers) |
|--------|----------------------------------|
| `mechanical` | Near-zero rubato/humanize; literal pedaling; flat accents |
| `restrained` | Light rubato, modest dynamics, dry-leaning pedal |
| `intimate` | Gentle rubato, soft dynamics ceiling, legato bias, warm pedal |
| `dramatic` | Deeper rubato, high contrast dynamics, strong accents, wider humanize |

Catalog is readable without SQLite writes. `POST` clone (or create-from-preset) creates one row. Same Composition + different presets must produce **distinguishable** metric digests (Task 9 asserts).

**5. Identity postcondition**

After realize:

```text
identity_digest(composition) == identity_digest(composition')
```

where digest covers sorted `(event_id, pitch, start_tick_canonical, harmony_span_fingerprint)` — **canonical** start ticks from Composition, not performed. Realization deltas must not be written back. Identity helpers hash canonical fields only.

**6. Storage row**

Table `performance_plans`:

- `id`, `project_id` FK CASCADE, `name`, `preset_id`, `body_json`, `schema_version`, `document_revision`, `source_composition_fingerprint`, `created_at`, `updated_at`
- PUT requires `expected_document_revision` (adaptive-score CAS twin); conflict → 409
- Secret guard on body
- Project delete GC via FK

**7. HTTP surface** (under `/projects/{project_id}/performance-plans`)

| Method | Behavior |
|--------|----------|
| `GET /` | list summaries (may be empty) |
| `GET /presets` | static catalog of four seeds (no DB write) — or document static client catalog equivalent |
| `POST /` | create (from `preset_id` clone or custom body) |
| `GET /{id}` | get |
| `PUT /{id}` | CAS replace (`expected_document_revision`) |
| `DELETE /{id}` | delete |
| `POST /{id}/realize` | **requires** `composition`; returns realization + `stale` vs plan pin |
| `POST /{id}/compare` | **requires** `composition`; mechanical vs plan metrics (+ capped sample deltas) |

No composition write routes. Soft-stale compares plan pin to fingerprint of **request** composition, not silently to a different saved row.

**8. Playback**

- Extend `playbackSource.js` with `PLAYBACK_SOURCE_KIND_PERFORMANCE`; mixer scope **`preview`** (do not pollute working mixer).
- When active, schedule-apply helper merges realization deltas at schedule time (tick+delta, performed velocity, session gains/pedals).
- Switching to working restores mechanical schedule.
- Clear performance audition on project switch.
- Do not persist mixer/performance into Composition.

**9. Optional AI augmentation**

- Flag `PERFORMANCE_CONDUCTOR_AI_ENABLED` default **false**.
- When true: may call a bounded soft prompt / fake path that returns **dimension parameter patches** only (validated against caps); then deterministic realize runs.
- Refusal codes if model returns pitch/harmony/event arrays.
- `ai_agents/` does not import these modules.

**10. Logging**

| Level | What |
|-------|------|
| DEBUG | dimension params, engine_version, delta caps, soft-stale, tie fan-out counts |
| INFO | create/realize/compare with `{plan_id, preset_id, note_count, mean_abs_tick_delta, mean_abs_velocity_delta, stale}` |
| WARN | soft-stale, AI patch refused, CAS conflict, plan embed refuse |
| ERROR | unexpected engine failures (sanitized) |

Never: full realization notes, composition event arrays, prompts, or API keys at INFO.

## Commit Plan
- **Commit 1** (after tasks 1–2): `docs(performance): lock conductor plan and realization contracts`
- **Commit 2** (after tasks 3–4): `feat(performance): add deterministic conductor engine and presets`
- **Commit 3** (after tasks 5–7): `feat(performance): persist plans, HTTP realize/compare, and preset catalog`
- **Commit 4** (after tasks 8–9): `feat(performance): audition before/after and distinguishability tests`
- **Commit 5** (after task 10): `docs(performance): conductor layer guide and AGENTS entry points`

## Tasks

### Phase 1: Contracts and safety invariants
- [x] Task 1: Freeze in `docs/performance-conductor.md` (stub ok) + constants module: dimension parameter ranges, preset seed numeric tables, `engine_version`, microtiming/rubato/velocity caps, duration floor (`duration_ticks + duration_delta >= 1`), identity-digest algorithm, **rubato/microtiming → tick_delta only (no Transport.bpm)**, fingerprint = `composition_snapshot_fingerprint`, **tie-chain shared tick_delta** (logical collapse + fan-out), orthogonality vs `note_performances[]` and SMF Conductor markers, default export stays mechanical, **ship-1 audition = API realize (no JS conductor twin)**.

  LOGGING: n/a for docs; constants module may DEBUG log engine_version only in tests.

  Files: `docs/performance-conductor.md`, `backend/app/performance_conductor_constants.py` (new), optional `frontend/src/utils/performanceConductor/constants.js`

- [x] Task 2: Add Pydantic models for `performance.plan.v1` and `performance.realization.v1` (`extra=forbid`), validators (caps, preset_id enum, no pitch fields on realization notes, **refuse plan bodies embedding events/pitches/harmony** with stable codes), and pure `identity_digest` / compare-metrics helpers (canonical fields only). Frontend plan-body validators for UI edits (not a realize twin). Unit tests for accept/reject fixtures including embed refuse.

  LOGGING: validation failures DEBUG with reason codes only.

  Files: `backend/app/performance_schemas.py` (new), `backend/app/services/performance_identity.py` (new), `backend/tests/test_performance_schemas.py` (new), `frontend/src/utils/performanceConductor/planValidation.js` (new)

<!-- Commit checkpoint: tasks 1-2 -->

### Phase 2: Deterministic conductor
- [x] Task 3: Implement pure deterministic conductor `realize_performance(composition, plan) -> realization` covering all eight dimensions within Task 1 caps. Seed-stable. Ignore `note_performances` in ship-1 (`capture_performance_ignored`). Tie-aware shared `tick_delta`. Duration floor. Postcondition: identity digest unchanged. No FastAPI/SQLite/LLM imports. Prefer reusing `collapse_track_tie_chains` for tie policy.

  LOGGING: DEBUG per-dimension application summaries (counts only); INFO not used inside pure module (caller logs).

  Files: `backend/app/services/performance_conductor.py` (new), `backend/tests/test_performance_conductor.py` (new)

- [x] Task 4: Encode the four preset seed plans as data and `clone_preset(preset_id) -> plan` helper. **Distinguishability asserts must call `realize_performance`** from Task 3 (pairwise metric digests differ; `mechanical` ≈ zero deltas). (depends on 1, 3)

  LOGGING: DEBUG preset clone `{preset_id}`.

  Files: `backend/app/fixtures/performance_presets.v1.json` (new) or constants, `backend/app/services/performance_presets.py` (new), `backend/tests/test_performance_presets.py` (new)

<!-- Commit checkpoint: tasks 3-4 -->

### Phase 3: Persistence, HTTP, preset catalog
- [x] Task 5: Alembic migration `20261003_0026` creating `performance_plans` with FK CASCADE, `document_revision`, fingerprint column. Store service: list/create/get/put/delete + secret guard + **CAS on `expected_document_revision`** (409 on conflict). Soft-stale helpers compare pin to a provided fingerprint. Project-delete via FK. Service tests with temp DB. (depends on 2)

  LOGGING: INFO create/update/delete with ids + revision; WARN CAS conflict; never body_json dumps.

  Files: `backend/app/db/alembic/versions/20261003_0026_performance_plans.py` (new), `backend/app/services/performance_plan_store.py` (new), `backend/tests/test_performance_plan_store.py` (new)

- [x] Task 6: Router `performance_plans.py`: CRUD + `realize` + `compare`. **Realize/compare request bodies require `composition`** (Analysis-style draft); soft-stale = plan pin vs `composition_snapshot_fingerprint(request.composition)`. PUT CAS via `expected_document_revision`. Optional AI patch path behind `PERFORMANCE_CONDUCTOR_AI_ENABLED` (default false) + fake deltas; refuse pitch/harmony payloads. Register router in `main.py`. API tests with fixture composition. (depends on 3, 4, 5)

  LOGGING: INFO realize/compare summaries; WARN `performance_plan_stale`, `ai_augment_refused`; never log realization notes at INFO.

  Files: `backend/app/routers/performance_plans.py` (new), `backend/app/performance_settings.py` (new), `backend/app/services/performance_plan_service.py` (new), `backend/app/main.py`, `backend/tests/test_performance_plans_api.py` (new), `.env.example`

- [x] Task 7: Preset **catalog** without SQLite writes: `GET .../performance-plans/presets` (or documented static client catalog twin of Task 4 seeds) listing the four seeds; `POST` create-from-preset clones one row. Opening the Performance tab / listing plans **never** auto-creates rows. Tests: empty list on fresh project; clone increases count by one; catalog GET is side-effect free. (depends on 4, 5, 6)

  LOGGING: DEBUG catalog serve `{preset_count}`; INFO clone `{preset_id, plan_id}`.

  Files: extend `backend/app/routers/performance_plans.py`, `backend/app/services/performance_presets.py`, `backend/tests/test_performance_plans_api.py`

<!-- Commit checkpoint: tasks 5-7 -->

### Phase 4: Playback, UI, acceptance tests, docs
- [x] Task 8: Frontend: `performanceApi.js`, thin `PerformancePanel.jsx`, wire `{ id: 'performance', label: 'Performance' }` in `ComposerWorkspace.jsx` `TABS`. Catalog clone buttons + plan list (empty-safe). Extend `playbackSource.js` with `PLAYBACK_SOURCE_KIND_PERFORMANCE` (mixer scope `preview`). Audition **calls `POST .../realize`** with current `editedMusicJson`, then pure schedule-apply helper into `tonePlaybackEngine` (tick_delta, velocity, session pedals/gains) without mutating pitches/harmony. Clear performance audition on project switch. Unit tests for source resolution + schedule-apply. **No JS conductor twin.** (depends on 6, 7)

  LOGGING: DEBUG audition toggles; INFO `{planId, mode: mechanical|performed}`; never dump realization arrays.

  Files: `frontend/src/api/performanceApi.js` (new), `frontend/src/components/PerformancePanel.jsx` (new), `frontend/src/components/ComposerWorkspace.jsx`, `frontend/src/utils/playbackSource.js`, `frontend/src/utils/performanceConductor/*`, `frontend/src/utils/tonePlaybackEngine.js` (schedule seam only), `frontend/src/store/musicStore.js` (thin slice), colocated `*.test.js`

- [x] Task 9: Acceptance tests: (1) one Composition + four presets → four distinguishable metric digests via `realize_performance`; (2) pitch/harmony/event-id identity digest invariant; (3) **tie-chain members share tick_delta** / identity holds; (4) mechanical vs dramatic compare returns non-zero deltas; (5) saving a plan does not change `projects.composition_json`; (6) **realize/compare with request composition ≠ saved project JSON still does not write the project row**; (7) soft-stale when request fingerprint ≠ plan pin; (8) AI flag off → no model call; (9) realization without pitch fields validates; (10) catalog GET is side-effect free. (depends on 3, 4, 6, 7, 8)

  LOGGING: tests assert reason codes / metrics only.

  Files: `backend/tests/test_performance_conductor_acceptance.py` (new), `frontend/src/utils/performanceConductor/*.test.js`, extend API/store tests as needed

- [x] Task 10: Complete docs — architecture diagram, Task 1 freezes, presets/catalog clone, realize/compare API (composition required), before/after audition, soft-stale vs request fingerprint, AI optional path, logging redaction, orthogonality to `note_performances` and SMF Conductor markers, no JS twin; link from `docs/composition-v2.md` and `docs/browser-playback.md`; update `AGENTS.md` / DESCRIPTION bullet if structure warrants. Mandatory `/aif-docs` checkpoint after implement. (depends on 1–9)

  LOGGING: n/a (docs).

  Files: `docs/performance-conductor.md`, `docs/composition-v2.md`, `docs/browser-playback.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md` (short bullet)

<!-- Commit checkpoint: tasks 8-10 -->

## Acceptance Criteria

1. One fixture `composition.v2` can produce at least the four presets as distinguishable performances (compare metrics differ) **without** any change to event ids, pitches, or `harmony[]`.
2. Realizing or auditioning a plan never writes pitch/harmony into the project composition row (including when the request composition differs from the saved project JSON).
3. Before/after works: user can audition mechanical vs a selected plan via `PLAYBACK_SOURCE_KIND_PERFORMANCE`.
4. Deterministic engine is the default; AI augmentation is optional and off by default.
5. Plans persist across restart (SQLite) as derived data with fingerprint soft-stale behavior against the **request** composition.
6. Preset catalog is readable without writes; opening the Performance tab does not auto-create plans.
7. Automated tests cover schema, engine (incl. ties), identity invariant, store CAS, API, catalog, and audition source helpers.

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer new `performance_*` modules + thin wires into playback/store; do not grow unrelated logic in `main.py` beyond router include.
3. Never invent `composition.v5`; never treat realization as playable without the source Composition.
4. Do not overload `note_performances` or SMF Conductor markers.
5. `ai_agents/` must not import performance conductor modules.
6. Keep default MIDI/MusicXML/WAV export mechanical unless an explicit opt-in lands (not required).
7. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
8. Verbose logging with redaction; no event-array dumps at INFO.
9. Caps and freezes from Task 1 are locked — later tasks must not invent alternate rubato/velocity math, Transport.bpm rubato, or a JS conductor twin.
10. Do not add `musical.dependency` “performance_of” edges in this milestone.
)
