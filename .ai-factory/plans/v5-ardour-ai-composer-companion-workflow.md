# Implementation Plan: V5 Ardour AI Composer Companion Workflow

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Focused **Ardour workflow** surface inside the existing Ardour tab (not a new workspace tab). Ordered steps: connected session → transport/timecode → selected musical scope → Send to AI Composer → generated alternatives → Send/import back to Ardour. Opening the tab never auto-connects, never auto-ingests, never auto-applies, and never writes Ardour session files. No multi-DAW picker, no piano-roll redesign, no Tone↔Ardour playhead coupling requirement
- Plan depth: ultra (full mode). Locked approach tables, audit, LV2 verdict, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Prepare may carry SPA working `composition.v2` (forbidden-key exception on prepare only; manifest/ingest unchanged). `reharmonize_selection` locks `preserve_harmony_adapt_melody` + melody promote for typical MIDI imports. Accompaniment after-part locks `acoustic_grand_piano` `role: harmony`. Orchestrate after-parts lock violin/cello/`string_ensemble_1`. Stem workflow locks SPA-only (no new exchange route). Apply realize seeds store surfaces via `applyRealize.js`. Acceptance asserts realized material in outbound MIDI, not alignment-only
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: complete the musician-facing Ardour↔AI Composer connected workflow on top of the shipped companion OSC foundation and deep session exchange. Expand realize intents to the locked operation catalog below. Package/install docs for Ardour helper components. Evaluate LV2 and document the reject-or-minimal decision. Never invent `composition.v5`. Never edit `.ardour` XML. Never remote-execute Lua. Never reimplement the AI backend inside a plugin
- Predecessors: `.ai-factory/plans/v5-ardour-companion-integration-foundation.md` (complete), `.ai-factory/plans/v5-deep-ardour-session-interoperability.md` (complete; exchange HTTP + Lua + three realize intents + Exchange section)

## Roadmap Linkage
Milestone: "V5 Ardour AI Composer companion workflow"
Rationale: Companion + deep exchange ship the OSC and package plumbing, but the Ardour tab still reads as two operator panels rather than a single connected DAW workflow, and only three realize intents exist. First unchecked ROADMAP items (performance conductor / spatial) are orthogonal and already planned. This plan documents the new milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`.

## Goal

Make **Ardour** the primary external DAW companion for V5 so a musician can stay in Ardour for editing/transport while using AI Composer only as a connected AI assistant for scoped generate / transform / return — without treating the Studio web editor as a replacement DAW.

Ship:

1. A focused Ardour **workflow UI** (session, transport/timecode, musical scope, send, alternatives, return) on the existing Ardour tab.
2. Expanded typed realize intents wrapping existing arrangement / development / harmony / neural pipelines (locked map below) — still preview-then-Apply; no second generator.
3. Prepare outbound packages from the **SPA working score** (post-Apply) with preview alignment, so generated MIDI returns to Ardour — not only the inbound draft.
4. Workflow-oriented packaging + install instructions for operator-installed Lua helpers (and optional Editor Action / scripts-path notes), Compose bind-mount, and env flags.
5. An explicit **LV2 evaluation** with a documented verdict (reject vs minimal bridge). If reject: do not add native plugin complexity.
6. Fixtures/tests for new intents + workflow gating; docs updates (`ardour-companion`, `ardour-session-exchange`, packaging README, AGENTS.md, `.env.example` only if new env keys appear — prefer no new env).

Acceptance: a musician connects Ardour, sees live session/transport/scope, sends an 8-bar (or other) selection into AI Composer, requests one of the supported AI ops (e.g. accompaniment or counter-melody), reviews alternatives, prepares an aligned outbound package whose **MIDI contains the realized material**, and imports the result back into Ardour on the same bars — without needing the piano-roll / Develop / Arrange / Harmony tabs as the primary DAW surface.

```text
Ardour (primary DAW)
  OSC companion: session + transport + strip select
  Lua: export selected region → exchange package
        │
        ▼
AI Composer Ardour workflow UI
  scope readout → Send (ingest) → Realize op → alternatives → Apply candidate
        │
        ▼
  Prepare outbound (working composition + preview alignment ± stem)
        │
        ▼
Ardour Lua import at start_samples / same bars
```

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**. **Companion** = process-memory OSC session. **Exchange package** = directory/zip under `ARDOUR_EXCHANGE_ROOT` with `manifest.json` + `material.mid` (+ optional `audio/`). **Workflow UI** = ordered Ardour-tab surface that composes companion status + exchange + realize + prepare; it does not own notes. **Send to AI Composer** = ingest (+ optional inbound Apply via `completeImport` replace). **Generated alternatives** = arrangement/development/harmony preview candidates (session). **Send back** = prepare outbound from **working** `composition.v2` + operator Lua import. **LV2** = native Ardour plugin; out of ship unless the evaluation locks a minimal bridge. Predecessors remain authoritative for package/manifest/OSC feedback `9243` contracts except where this plan explicitly narrows the prepare forbidden-key exception.

## Approach Evaluation (locked)

### Part A — Where the workflow UI lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New top-level workspace tab (“Ardour Workflow”)** | Strong branding | Splits companion connect from workflow; contradicts “no new tab” predecessor pattern | **Reject** |
| **B. Keep separate Companion + Exchange panels as today; only add intents** | Smallest delta | Still operator-panel UX; fails “focused workflow” acceptance | **Insufficient** alone |
| **C. Restructure the existing Ardour tab into an ordered workflow surface** that embeds connect/transport, scope, send, alternatives, return; keep advanced mixer strips as a collapsible secondary section | One composition for the musician job; reuses APIs | Larger frontend refactor of `ArdourCompanionPanel` / `ArdourExchangePanel` | **Accepted** |
| **D. Headless HTTP-only / no SPA change** | Backend-only | Violates UI acceptance | **Reject** |

### Part B — How AI ops are invoked

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Deep-link into Arrange / Develop / Harmony / Neural tabs** | Zero new realize code | Forces web-editor DAW workflow; fails acceptance | **Reject** as primary path |
| **B. Expand `ardour.exchange.realize_request.v1` intent enum + `ardour_exchange_realize.py` map onto existing preview engines; SPA Apply helpers stay explicit** | One Send→Realize→Apply→Prepare path; reuses tested pipelines | More intent builders | **Accepted** |
| **C. New Ardour-specific LLM generator in companion service** | One module | Duplicates arrangement/development/harmony; pulls LLM into OSC layer | **Reject** |
| **D. Autonomous / film-score / agents path** | Fancy | Wrong surface; `ai_agents/` must not import companion/exchange | **Reject** |

### Part C — Locked realize / workflow operation map

| Musician op | Intent / action id | Existing engine | Notes |
|-------------|-------------------|-----------------|-------|
| Generate accompaniment | `add_accompaniment` | arrangement `add_accompaniment` | Promote source to melody; before = melody part; after = retain melody + **`acoustic_grand_piano` `role: harmony`** (catalog id; fake_llm adds remaining after parts) |
| Generate counterpoint | `counter_melody` | arrangement `create_countermelody` (cello GM 42) | **Already shipped** — keep |
| Create variation | `regenerate_region` | development `vary_section` on alignment bars | **Already shipped** — keep |
| Reharmonize selection | `reharmonize_selection` | `preview_reharmonization` / harmony preview | Lock **`engine=deterministic`**, **`operation=reharmonize`**, **`content_policy=preserve_harmony_adapt_melody`**, promote sources to melody, selection from alignment bars. Empty `harmony: []` may invent spans (existing engine). Honesty: adapts melody to new/invented harmony metadata. Map `ReharmonizeError` → exchange error codes |
| Orchestrate selection | `orchestrate_selection` | arrangement `orchestrate_selected_tracks` | Promote source; after = **violin `melody` + cello `bass` + `string_ensemble_1` `harmony`** (same shape as arrangement fake fixtures). Keep `arrangement_variation` → `change_instrumentation` for backward compatibility |
| Create stems / render | SPA stem workflow (not a realize intent) | `musicApi` stem-set enqueue/poll + `prepare` with `stem_id` | **SPA-only** — no `POST /ardour/exchange/render-stems`, no `ArdourExchangeWorkflowActionV1` in ship-1. Optional; MIDI round-trip remains hard acceptance. Honesty: stem WAV not sample-locked to MIDI |

`arrangement_variation` remains supported (map → `change_instrumentation`) so existing Exchange UI/tests do not break.

### Part D — Inbound Apply vs “don’t use web editor as DAW”

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Never Apply inbound; realize only on exchange draft preview** | Avoids replacing working score | Some engines expect working-composition Apply paths already wired | **Reject** as sole path |
| **B. Keep SPA `completeImport` replace for inbound; workflow copy states clearly that AI Composer holds a temporary working score for AI ops, Ardour remains the DAW** | Matches shipped exchange; honest | Replaces any unrelated open score | **Accepted** (ship-1; merge-into-existing still out of scope) |
| **C. Auto-Apply on ingest** | Fewer clicks | Surprises | **Reject** |

### Part E — Transport / timecode display

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Raw `locate_samples` only** | Already in context | Not musician-readable | **Insufficient** |
| **B. Reuse `videoScoringMap` SMPTE helpers** | Exists | Couples Ardour to picture domain; frame-rate assumptions wrong | **Reject** |
| **C. Pure Ardour display helper: samples + `sample_rate` → `HH:MM:SS` (and optional fractional frames at 30 fps display-only); when preview alignment present, also show `bars start–end` + tempo/meter from **manifest**** | Honest; no video import | Not true Ardour BBT without bit `+32` | **Accepted**. BBT OSC bit (`+32`) stays out of scope (predecessor) |

### Part F — LV2 plugin

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Full LV2 AI host inside Ardour (weights, generate, UI)** | Native feel | Reimplements backend; packaging hell; violates “do not reimplement AI in plugin” | **Reject** |
| **B. Minimal LV2 MIDI/audio/control bridge** that only shuttles buffers/ports while HTTP backend does AI | Could skip Lua file copy | Still native build/CI/matrix; OSC+Lua already cover selected-region MIDI + transport; no remaining acceptance gap requires in-graph DSP | **Reject for this milestone** |
| **C. Document that OSC + Lua + file/session exchange is sufficient; no LV2** | Matches foundation + deep-exchange honesty; installable today | Manual Lua run remains | **Accepted** |

**LV2 verdict (locked):** **Not required.** Remaining gaps (continuous bidirectional note sync, in-graph meter taps, zero-file MIDI paste over a plugin port) are out of acceptance scope. Do **not** scaffold an LV2 project in this plan. Revisit only if a future milestone needs sample-accurate in-graph audio I/O that files cannot provide.

### Part G — Packaging / install

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Docs-only README (status quo)** | Already exists | Easy to miss Compose mount + env pairing | **Insufficient** alone |
| **B. Operator install guide + optional `scripts/install_ardour_helpers.sh` that copies Lua recipes to a user-supplied Ardour scripts directory and prints env checklist** | Actionable; no remote exec | Script must never start Ardour or write session XML | **Accepted** |
| **C. Bundle a `.lv2` / binary in the repo** | — | Conflicts with Part F reject | **Reject** |

### Part H — Prepare source composition (added by `/aif-improve`)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep prepare = draft only** (deep-exchange status quo) | No schema change | Outbound MIDI lacks realized notes; fails this plan’s acceptance | **Reject** |
| **B. Server-side mutate exchange draft after realize** | SPA need not send V2 | Session mutation; races with SPA Apply; overlaps preview semantics | **Reject** (out of scope) |
| **C. Narrow exception: `ArdourExchangePrepareRequestV1` may include `composition` (playable V2); prefer it when present; else draft. Manifest + ingest forbidden-key scan unchanged** | Returns generated material; SPA owns Apply | Deliberate exception to deep-exchange forbid list | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Companion OSC | `/ardour/companion/*`, fake peer, feedback `9243`, transport/strips/`locate_samples`/`selected_ssid` | Session + transport section of workflow |
| Exchange packages | `ardour.exchange.*.v1`, store, ingest, preview, apply payload, prepare, download | Send / return spine |
| Realize (3 intents) | `counter_melody`, `arrangement_variation`, `regenerate_region` in `ardour_exchange_realize.py` | Keep; extend enum + builders |
| Arrangement ops | `add_accompaniment`, `orchestrate_selected_tracks`, `create_countermelody`, `change_instrumentation`, … | New intent maps |
| Development | `vary_section` | Existing variation |
| Harmony | `preview_reharmonization` + SPA `applyReharmonizePreview` | New `reharmonize_selection` with locked policy |
| Neural stems | `/neural-audio/stem-sets` via `musicApi`, prepare `stem_id` copy | SPA stem workflow |
| SPA Apply inbound | `applyExchangeInbound` → `completeImport` replace | Keep |
| Lua recipes | `export_selected_midi_region.lua`, `import_exchange_package.lua`, README | Packaging expansion |
| UI | `ArdourCompanionPanel` + nested `ArdourExchangePanel` | Restructure into workflow surface |
| Agent boundary | `ai_agents/` must not import companion/exchange | Keep + extend forbid tests if new modules |
| Docs | `docs/ardour-companion.md`, `docs/ardour-session-exchange.md`, examples README | Workflow + LV2 decision + install |
| Alembic | N/A for exchange | **No migration** |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Workflow UI composition | Ordered session → scope → send → alternatives → return; not two flat panels |
| Transport/timecode readability | Samples→clock helper; show playing/stop; no BBT claim |
| Musical scope card | Manifest alignment when preview present; else OSC strip + locate + “export selection via Lua” CTA |
| New realize intents | `add_accompaniment`, `orchestrate_selection`, `reharmonize_selection` with locked inventories/policy |
| Prepare working composition | Exempt `composition` on prepare request; SPA sends post-Apply working V2 |
| Apply realize store seeding | Seed arrangement/development/harmony fields before existing Apply helpers (`applyRealize.js`) |
| Stem/render action | SPA-only enqueue/poll + prepare `stem_id` |
| Install packaging | Script + checklist; Editor Action notes; Compose bind-mount pairing |
| LV2 decision doc | Explicit “not required” with capability matrix |
| Tests | Intent builders + prepare material assert + UI gating + fake stem path; no real Ardour |

### Coupling risks to avoid

1. Editing `.ardour` XML / playlists from Mukit.
2. Remote-executing Lua or shipping an LV2 that embeds the LLM.
3. Auto-connect / auto-ingest / auto-Apply.
4. Inventing `composition.v5` or playable notes inside manifests.
5. `ai_agents/` importing companion/exchange/neural stores.
6. Mapping orchestrate → only `change_instrumentation` when `orchestrate_selected_tracks` exists.
7. Claiming stem WAV is sample-locked to MIDI.
8. Tone.js ↔ Ardour playhead coupling as a requirement.
9. Multi-DAW abstraction.
10. Editing `ROADMAP.md` during implementation.
11. Requiring the piano-roll as the only way to review alternatives (Tone audition of candidate composition is OK; deep editor tabs must not be mandatory).
12. Logging MIDI bytes, full event arrays, or secrets at INFO.
13. Using `DATASET_ROOT` / `PROJECT_DB_PATH` as exchange root.
14. Breaking existing three realize intents / exchange HTTP contracts.
15. Preparing outbound MIDI from inbound draft after realize Apply (must send working composition).
16. Calling arrangement/development Apply without seeding operation/instrumentation/fingerprint store fields.

## Scope And Decisions

### In scope
- Ardour-tab workflow UI restructure + timecode/scope helpers.
- Expand realize intent enum + builders + HTTP validation + Exchange/workflow UI labels.
- Reharmonize bridge with locked melody-adapt policy for import-shaped scores.
- Prepare request exception for working `composition` + SPA wiring.
- Apply-realize store seeding helper.
- Stem/render SPA workflow using existing neural APIs + prepare.
- Install script + packaging docs; LV2 reject documentation.
- Tests (fake modes) and docs checkpoint.

### Out of scope
- LV2 / native plugin scaffold.
- Continuous bidirectional note sync; OSC note streaming as material path.
- Merge-into-existing-score inbound Apply.
- Server-side mutation of exchange draft after realize (Part H.B).
- BBT feedback bit `+32`.
- New workspace tab; multi-DAW; Ableton Link / JACK UI.
- Autonomous composer / film-score as the Ardour path.
- Editing `ROADMAP.md`.
- Requiring neural stems for hard acceptance (MIDI round-trip remains sufficient).
- New `POST /ardour/exchange/render-stems` or `ArdourExchangeWorkflowActionV1` DTO.

### Architecture decisions (locked)

**1. Documents / intents**

Extend `ArdourExchangeRealizeIntent` in `backend/app/ardour_exchange_schemas.py`:

```text
counter_melody | arrangement_variation | regenerate_region
| add_accompaniment | orchestrate_selection | reharmonize_selection
```

Stem/render is **SPA-only** (`frontend/src/utils/ardourExchange/stemWorkflow.js` + existing `musicApi` stem-set APIs + prepare `stem_id`). No new exchange route and no `ArdourExchangeWorkflowActionV1` in ship-1.

**Forbidden-key scan (refined):** Exact keys `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`, `session_xml`, `ardour_session`, `prompt` remain forbidden on **manifest JSON and ingest request bodies**. **`ArdourExchangePrepareRequestV1` may include top-level `composition` as playable `composition.v2`** (same class of exemption as preview `draft_composition`). Nested event arrays inside that composition are expected and are not scanned as forbidden top-level keys. Realize request bodies still must not carry event arrays / full compositions (intent + optional instruction only).

**2. Realize builders**

Extend `backend/app/services/ardour_exchange_realize.py`:

- `add_accompaniment` → arrangement `add_accompaniment`; promote sources to melody; after = melody + `acoustic_grand_piano` `role: harmony`.
- `orchestrate_selection` → `orchestrate_selected_tracks`; after = violin `melody` + cello `bass` + `string_ensemble_1` `harmony`.
- `reharmonize_selection` → `preview_reharmonization` with locked policy above; normalize response to `{ surface: "harmony", preview, … }` with a singleton candidate shape the UI can list; map domain errors to `ArdourExchangeError`.
- Keep prior three intents bitwise-compatible.

**3. Prepare**

`prepare_outbound_package`: if request carries `composition`, validate as V2 and slice/export that; else fall back to `preview.draft_composition` (deep-exchange behavior). Alignment still from preview when `use_preview_alignment=true`. Workflow UI always passes working store composition after Apply.

**4. Workflow UI + Apply helper**

Primary files:

- `frontend/src/components/ArdourCompanionPanel.jsx` — shell: workflow steps + collapsible mixer
- `frontend/src/components/ArdourExchangePanel.jsx` and/or `ArdourWorkflowPanel.jsx`
- `frontend/src/utils/ardourCompanion/timecode.js`
- `frontend/src/utils/ardourExchange/scopeSummary.js`
- `frontend/src/utils/ardourExchange/applyRealize.js` — seed store surface from realize envelope, then call existing Apply helpers
- `frontend/src/utils/ardourExchange/stemWorkflow.js` — SPA-only stem enqueue/poll/prepare
- Keep `ardourExchangeApi.js` / `ardourCompanionApi.js` (prepare body includes optional `composition`)

Musician-facing labels:

| Label | Intent / action |
|-------|-----------------|
| Generate accompaniment | `add_accompaniment` |
| Generate counterpoint | `counter_melody` |
| Create variation | `regenerate_region` |
| Reharmonize selection | `reharmonize_selection` |
| Orchestrate selection | `orchestrate_selection` |
| Create stems / render | SPA stem workflow |

**5. Packaging**

- `scripts/install_ardour_helpers.sh` — copies `backend/examples/ardour/*.lua` to `$1` or `$ARDOUR_SCRIPTS_DIR`; prints checklist; refuse destinations that look like a `.ardour` session file path.
- Expand `backend/examples/ardour/README.md` with Editor Action / Script Manager steps and connected-workflow quickstart.
- Docs “Why not LV2” expanded with Part F matrix.

**6. Feature flags**

No new flags. Companion + exchange flags remain. Stem action respects existing neural settings (disabled → clear UI message).

## Commit Plan

- **Commit 1** (tasks 1–2): `feat(ardour): expand exchange realize intents for accompaniment, orchestrate, reharmonize`
- **Commit 2** (tasks 2b–4): `feat(ardour): prepare working composition, scope/timecode helpers, SPA stem workflow`
- **Commit 3** (tasks 5a–6): `feat(ardour): focused Ardour companion workflow UI and apply-realize helper`
- **Commit 4** (tasks 7–8): `docs(ardour): packaging install helpers and LV2 sufficiency decision`

## Tasks

### Phase 1: Realize intent expansion

- [x] Task 1: Expand `ArdourExchangeRealizeIntent` and request validation in `backend/app/ardour_exchange_schemas.py` with `add_accompaniment`, `orchestrate_selection`, `reharmonize_selection`. Keep existing three intents. Update schema unit tests for enum + forbidden-key scope. Error code `ardour_exchange_realize_unsupported` remains for unknown intents.

  LOGGING REQUIREMENTS:
  - DEBUG: intent accepted/rejected (no instruction body at INFO if present)
  - INFO: schema validation failures with stable error code only
  - Never log draft event arrays

  Files: `backend/app/ardour_exchange_schemas.py`, `backend/tests/test_ardour_exchange_schemas.py`

- [x] Task 2: Implement builders in `backend/app/services/ardour_exchange_realize.py` for the three new intents (Part C locks). Reuse `run_composition_arrangement_preview` / `preview_reharmonization`; do not Apply; do not import `ai_agents/`. Fake-mode deterministic tests for each new intent + regression for `counter_melody`. Reharmonize: alignment bar selection; promote sources to melody; locked policy `preserve_harmony_adapt_melody` / `deterministic` / `reharmonize`; map `ReharmonizeError` → `ArdourExchangeError`. Normalize harmony realize envelope to `surface: "harmony"` with a UI-listable singleton candidate. (depends on 1)

  LOGGING REQUIREMENTS:
  - INFO: realize start/end with intent, preview_id, correlation id, candidate count (no pitches)
  - DEBUG: track ids selected, engine surface (`arrangement` | `development` | `harmony`)
  - WARN/ERROR: unsupported composition shape, missing targets, reharmonize mapped codes
  - Format: module logger extras consistent with existing realize logs

  Files: `backend/app/services/ardour_exchange_realize.py`, `backend/tests/test_ardour_exchange_realize.py`, `backend/app/routers/ardour_exchange.py` if response envelope needs a harmony branch

<!-- Commit checkpoint: tasks 1-2 -->

### Phase 2: Prepare working composition + helpers + stems

- [x] Task 2b: Allow prepare to export **working** material. Narrow forbidden-key exception so `ArdourExchangePrepareRequestV1` may include top-level `composition` (`composition.v2`). Update `prepare_outbound_package` to prefer request composition when present, else draft. Update `prepareArdourExchange` client to pass working score. Unit/API tests: prepare with a realized candidate composition yields MIDI/track evidence of the transform (e.g. countermelody role or extra accompaniment track), while alignment fields still echo the preview. Manifest + ingest forbid rules unchanged. (depends on 1; pairs with 2 for fixtures)

  LOGGING REQUIREMENTS:
  - INFO: prepare source (`working` | `draft`), package_id truncated, has_audio
  - DEBUG: track_id count / bar span only — never event arrays or MIDI bytes
  - WARN: missing preview when `use_preview_alignment` and no preview

  Files: `backend/app/ardour_exchange_schemas.py`, `backend/app/services/ardour_exchange_prepare.py`, `backend/app/routers/ardour_exchange.py`, `frontend/src/api/ardourExchangeApi.js`, `backend/tests/test_ardour_exchange_prepare.py`, `backend/tests/test_ardour_exchange_schemas.py`

- [x] Task 3: Add pure frontend helpers: `frontend/src/utils/ardourCompanion/timecode.js` (samples + sample_rate → display string; guards for missing rate) and `frontend/src/utils/ardourExchange/scopeSummary.js` (connected state, strip name, locate clock, preview alignment bars/tempo/meter/track). Unit tests. Do not import video scoring modules. (depends on none; can parallel Task 1)

  LOGGING REQUIREMENTS:
  - Frontend: `createAppLogger` DEBUG when falling back to “samples only” because `sample_rate` missing; never log full context objects with paths

  Files: `frontend/src/utils/ardourCompanion/timecode.js`, `frontend/src/utils/ardourCompanion/timecode.test.js`, `frontend/src/utils/ardourExchange/scopeSummary.js`, `frontend/src/utils/ardourExchange/scopeSummary.test.js`

- [x] Task 4: SPA-only stem/render workflow in `frontend/src/utils/ardourExchange/stemWorkflow.js`: enqueue neural stem-set via existing `musicApi`, poll until a stem is `complete` (or fail/timeout with clear UI error), then `prepareArdourExchange({ stem_id, composition: working, use_preview_alignment: true })`. No new backend exchange route. Fake neural mode in tests. Honesty banner: stems optional / not sample-locked. (depends on 2b)

  LOGGING REQUIREMENTS:
  - INFO: stem-set id truncated, prepare package_id truncated, stem_id truncated
  - WARN: neural disabled / stem incomplete / poll timeout
  - Never log WAV bytes

  Files: `frontend/src/utils/ardourExchange/stemWorkflow.js`, `frontend/src/utils/ardourExchange/stemWorkflow.test.js`, `frontend/src/api/ardourExchangeApi.js`

<!-- Commit checkpoint: tasks 2b-4 -->

### Phase 3: Apply helper + focused workflow UI

- [x] Task 5a: Add `frontend/src/utils/ardourExchange/applyRealize.js` that, given a realize envelope + selected candidate id, seeds the correct Zustand surface (`arrangement*` / `development*` / reharmonize fields including operation, instrumentation or harmony candidate, fingerprints, base revision) then calls `applySelectedArrangementCandidate` / `applySelectedDevelopmentCandidate` / `applyReharmonizePreview`. Unit tests cover arrangement + harmony seeding so Apply does not fail on empty store request reconstruction. (depends on 2)

  LOGGING REQUIREMENTS:
  - INFO: surface + intent + candidate id suffix on successful seed/Apply
  - WARN: missing candidate / Apply false
  - Never log full compositions

  Files: `frontend/src/utils/ardourExchange/applyRealize.js`, `frontend/src/utils/ardourExchange/applyRealize.test.js`

- [x] Task 5: Restructure Ardour tab into the ordered workflow surface (Part A.C): (1) Connected session + Connect controls, (2) Transport + timecode readout, (3) Selected musical scope card, (4) Send to AI Composer (scan root / upload / ingest), (5) AI op select + Realize + alternatives + Apply via `applyRealize`, (6) Prepare / download (pass **working** composition) + Lua return CTA; optional Create stems / render via `stemWorkflow`. Collapse strip mixer under “Mixer (advanced)”. Preserve existing `data-testid`s where possible; add `ardour-workflow-panel`. Never auto-ingest on mount. Clear copy: AI Composer holds a temporary working score; Ardour remains the DAW. Wire new intent labels. (depends on 2, 2b, 3, 4, 5a)

  LOGGING REQUIREMENTS:
  - INFO: workflow step transitions (send/realize/apply/prepare) with codes only
  - DEBUG: poll ticks omitted or sampled; do not spam INFO every 1000 ms
  - ERROR: user-visible failures with API error codes

  Files: `frontend/src/components/ArdourCompanionPanel.jsx`, `frontend/src/components/ArdourExchangePanel.jsx` (and/or new `ArdourWorkflowPanel.jsx`), related CSS-in-JS, unit tests for gating

- [x] Task 6: Acceptance-oriented tests: fake companion + fixture ingest → each new realize intent returns candidates → prepare **with working/candidate composition** asserts realized material (roles/track count/notes present) **and** alignment unchanged; UI unit tests for scope/timecode helpers + applyRealize seeding; architecture forbid still blocks `ai_agents/` imports of new modules. Extend `test_ardour_exchange_acceptance.py`; no Docker Ardour. (depends on 2, 2b, 5, 5a)

  LOGGING REQUIREMENTS:
  - Tests assert log extras / codes where existing acceptance style does; no new noisy fixtures

  Files: `backend/tests/test_ardour_exchange_acceptance.py`, `backend/tests/test_ardour_exchange_realize.py`, frontend tests under `frontend/src/**`

<!-- Commit checkpoint: tasks 5a-6 -->

### Phase 4: Packaging, LV2 decision, docs

- [x] Task 7: Add `scripts/install_ardour_helpers.sh` + expand `backend/examples/ardour/README.md` with install checklist, Compose bind-mount pairing, Editor/Script Manager steps, and connected-workflow quickstart (export → Studio workflow → import). Script must be idempotent copy-only; refuse to run if destination looks like a `.ardour` session file path. (depends on 5 for accurate UI names in docs)

  LOGGING REQUIREMENTS:
  - Script: stdout checklist only; no secrets; exit non-zero on bad args

  Files: `scripts/install_ardour_helpers.sh`, `backend/examples/ardour/README.md`

- [x] Task 8: Docs checkpoint: update `docs/ardour-session-exchange.md` (full intent table, prepare working-composition exception, workflow UI), `docs/ardour-companion.md` (workflow surface + expanded “Why not LV2” capability matrix locking Part F reject), `docs/daw-interoperability.md` cross-link, `AGENTS.md` entry points if component names change, README pointer if present. State clearly that OSC + Lua + exchange satisfies V5 Ardour companion acceptance without LV2. Mandatory `/aif-docs` alignment. (depends on 5–7)

  LOGGING REQUIREMENTS:
  - N/A for prose; ensure documented log redaction rules unchanged (no MIDI/event logging)

  Files: `docs/ardour-session-exchange.md`, `docs/ardour-companion.md`, `docs/daw-interoperability.md`, `AGENTS.md`, optionally `README.md`

<!-- Commit checkpoint: tasks 7-8 -->

## Implementation Notes

- Prefer extending `ardour_exchange_realize.py` / `ardour_exchange_prepare.py` over growing `main.py`.
- Harmony realize response differs from arrangement candidate lists — normalize in realize service so the workflow UI has one alternatives list model (`surface`, candidates or singleton).
- Keep `ARDOUR_EXCHANGE_ENABLED` / `ARDOUR_COMPANION_ENABLED` semantics from predecessors.
- Constant-tempo alignment assumption from deep exchange remains for bar↔samples display helpers.
- Do not invent catalog IDs outside `arrangement_instruments.v1.json`.
- Deep-exchange acceptance that only checked alignment is insufficient for this milestone; extend it.

## Verification Gate (for `/aif-verify`)

1. New realize intents return candidates under `LLM_FAKE_MODE` from the eight-bar fixture.
2. Prepare with working/candidate composition exports MIDI reflecting realized transform; alignment fields still match preview.
3. Workflow UI unit tests: disabled flags gate Send/Realize; timecode helper formats known samples/rate; scope card prefers manifest bars when preview present; `applyRealize` seeds store before Apply.
4. SPA stem workflow covered in fake mode (optional path); no new exchange stem route.
5. `ai_agents/` import forbid still green.
6. Docs state LV2 is not required; install script runs `--help` / dry copy in CI if lightweight.
7. No `.ardour` XML writers; no LV2 tree added.
