# Implementation Plan: V5 Deep Ardour Session Interoperability

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Exchange section under the existing Ardour tab — session context readout, ingest/preview/Apply of an Ardour package, realize intents (counter-melody / arrangement variation / regenerate region), prepare outbound MIDI/stem package, link to Lua install docs. Opening the tab never auto-ingests, never auto-applies, and never writes Ardour session files. No new workspace tab, no multi-DAW picker, no piano-roll redesign
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). `counter_melody` locks to arrangement `create_countermelody` with cello after-part (not development continue/vary). Feedback bitmask locks to `9243` (`8219+1024`); `/position/samples` parses int or digit string. Forbidden-key scan is request-body only; preview/apply may carry `draft_composition`. SPA Apply uses `completeImport` (replace, not merge). Prepare reuses `filter_composition_tracks` / `filter_composition_bar_range`. Ingest multipart uses `read_upload_bounded`. Lifespan clears exchange preview. Lua export documents Editor/Session or minimal SMF writer
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: select or exchange musical material between one Ardour session and AI Composer via OSC session context, operator-installed Lua helpers, MIDI import/export, and audio/stem file exchange. Never edit Ardour `.ardour` session XML behind Ardour’s back. Never invent `composition.v5`. Working `composition.v2` changes only through explicit Apply (inbound) or stays untouched while prepare writes exchange files (outbound). Predecessor: `.ai-factory/plans/v5-ardour-companion-integration-foundation.md` (OSC transport/mixer foundation) and `docs/daw-interoperability.md` (whole-score file handoff)

## Roadmap Linkage
Milestone: "V5 Deep Ardour session interoperability"
Rationale: Companion foundation covers transport/mixer only; musicians still cannot round-trip a selected MIDI region through AI Composer and land it on the same bars. First unchecked ROADMAP items (performance conductor / spatial) are orthogonal and already have separate plans. This plan documents the new milestone for a later `/aif-roadmap` append. Implementation does **not** edit `ROADMAP.md`.

## Goal

Allow a musician to move material between Ardour and AI Composer with tempo/time alignment, track naming, MIDI timing, and selected range preserved where the supported APIs expose them.

Ship:

1. Non-playable documents `ardour.exchange.manifest.v1`, `ardour.exchange.package.v1`, `ardour.exchange.session_context.v1`, `ardour.exchange.preview.v1`, `ardour.exchange.prepare.v1`, and `ardour.exchange.realize_request.v1` (`extra=forbid`).
2. Mukit-owned exchange root `ARDOUR_EXCHANGE_ROOT` (refuse `DATASET_ROOT` / `PROJECT_DB_PATH`) plus browser upload/download of the same package shape.
3. Operator-installed Lua helpers that **export** a selected MIDI region (+ manifest) and **import** a prepared package at a declared sample/bar position — never remote-executed by Mukit; never rewrite session XML from Mukit.
4. OSC-enriched `session_context` when the companion is connected (transport samples, selected strip name/ssid, sample rate via `/strip/list` end when available); authoritative musical tempo/meter/bar span come from the Lua-written manifest for region export.
5. Ingest → session preview → explicit Apply into `composition.v2` via existing MIDI import canonicalization + SPA `completeImport` (replace).
6. Realize intents that wrap existing **arrangement** / **development** preview (`create_countermelody` for cello counter-melody, `change_instrumentation` for arrangement variation, `vary_section` for regenerate region) without inventing a second generator.
7. Prepare outbound SMF (+ optional stem WAV copy) with alignment metadata so Ardour Lua can place the region on the same bars.
8. Fixtures, mocked tests (no real Ardour), and integration docs.

Acceptance: the user selects an **8-bar MIDI idea** in an Ardour workflow, exports it into AI Composer (Lua package or upload), requests a **cello counter-melody**, and brings the generated MIDI back into the Ardour session **aligned to the same bars**.

```text
Ardour editor selection
        │  Lua: export region → package (MIDI + manifest)
        ▼
ARDOUR_EXCHANGE_ROOT / browser upload
        │  POST /ardour/exchange/ingest  (read_upload_bounded)
        ▼
ardour.exchange.preview.v1  (session-only; optional OSC context enrich)
        │  explicit Apply → SPA completeImport (replace)
        ▼
composition.v2
        │  realize: create_countermelody | change_instrumentation | vary_section
        │  → candidate Apply (manual)
        ▼
POST /ardour/exchange/prepare  →  outbound package (MIDI ± stem)
        │  Lua: import at start_samples / start_bar
        ▼
Ardour playlist region (same bars; Ardour owns session write)
```

**Terminology lock:** Product generation is **V5**. The playable score stays **`composition.v2`**. There is no **`composition.v5`**. **Companion** remains the process-memory OSC session from the foundation plan. **Exchange package** is a directory (or zip) under `ARDOUR_EXCHANGE_ROOT` or an uploaded archive with `manifest.json` + `material.mid` (+ optional `audio/`). **Manifest** is `ardour.exchange.manifest.v1` — alignment and naming metadata, never a playable alternate score. **Session context** is `ardour.exchange.session_context.v1` — OSC-observed + optional query fields; not authoritative for selected-region bar spans when a Lua manifest is present. **Ingest** reads a package into a session preview. **Apply (inbound)** replaces working `composition.v2` through SPA `completeImport` (same as MIDI import replace). **Prepare** writes an outbound package and does not mutate Ardour. **Import (Ardour)** is the operator-run Lua (or Ardour Session → Import) that places files; Mukit never edits `.ardour` XML. **Realize intent** is a thin typed request that calls existing arrangement/development preview with locked operation mapping. **Lua recipe** stays operator-install only. Predecessor: companion foundation; V3 DAW file handoff.

## Approach Evaluation (locked)

### Part A — How material leaves Ardour

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Stream note events over OSC** | Live feel | Ardour OSC is a control surface, not a MIDI region dump; timing/region fidelity unreliable | **Reject** |
| **B. Mukit parses/edits the `.ardour` session XML on disk** | Full access | Explicitly forbidden; races with Ardour; corruption risk | **Reject** |
| **C. Operator Lua exports the selected MIDI region to SMF + `ardour.exchange.manifest.v1` into a Mukit exchange root (or user downloads the package); Mukit ingests via HTTP** | Uses Session/Editor APIs; Ardour owns export; testable with fixtures | Manual Lua run (or Editor Action) | **Accepted** |
| **D. Whole-session SMF stem export only (existing Session → Export)** | Already documented | Loses “selected 8 bars” and track focus; weak acceptance fit | **Insufficient** alone; keep as fallback doc path |

### Part B — Where packages live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Inside the Ardour session directory** | Convenient for Lua | Couples Mukit to session layout; risk of writing next to `.ardour` | **Reject** as default |
| **B. Mukit-owned `ARDOUR_EXCHANGE_ROOT` with `storage_root_policy` refuse of `DATASET_ROOT` / `PROJECT_DB_PATH`, plus browser upload/download of the same package shape** | Matches neural/recovery roots; CI uses fixtures without Ardour | Operator must point Lua at the root (or use upload) | **Accepted** |
| **C. SQLite BLOB of MIDI bytes on the project row** | Durable | Mixes DAW bytes into project DB; GC/privacy noise | **Reject** for ship-1 |

### Part C — When `composition.v2` is written

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Auto-apply on ingest** | One click fewer | Surprises; breaks preview/review pattern | **Reject** |
| **B. Ingest builds `ardour.exchange.preview.v1` (imported draft composition + alignment); Apply **replaces** working V2 via SPA `completeImport` (same as MIDI import replace). Merge-into-existing-project is out of scope** | Matches ImportControls replace path; clear for 8-bar fixture workflow | Does not merge into an unrelated open score | **Accepted** |
| **C. Realize intents auto-Apply the top development candidate** | Faster demo | Violates candidate Apply contract | **Reject** |

### Part D — Tempo / bar alignment authority

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Trust OSC locate samples alone** | Already in companion status | No bar/tempo map; meter changes invisible | **Reject** as sole authority |
| **B. Trust SMF conductor track alone** | Existing MIDI import | Selected-region export may omit full map; Ardour import tempo disposition varies | **Insufficient** alone |
| **C. Lua manifest carries `tempo_bpm`, `time_signature`, `ticks_per_quarter`, `start_bar`, `bar_count`, `start_samples`, `sample_rate`, `track_name`, `source_track_ssid` (optional); SMF carries notes; OSC context enriches live transport/selection; inbound Apply and outbound prepare both round-trip the manifest alignment fields** | Preserves acceptance bars; honest when OSC lacks region APIs | Lua must compute extents | **Accepted** |

### Part E — Counter-melody / variations

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New Ardour-specific note generator in companion service** | One module | Duplicates development/arrangement; pulls LLM into OSC layer | **Reject** |
| **B. Typed `realize_intent` with locked operation map: `counter_melody` → arrangement `create_countermelody` (after inventory retains source melody + adds `cello` / GM 42 part with `role: countermelody`); `arrangement_variation` → arrangement `change_instrumentation`; `regenerate_region` → development `vary_section` on alignment bar range. User Applies the candidate as today** | Reuses tested pipelines and fake_llm `create_countermelody`; catalog already has `cello` | Extra HTTP hop from Exchange UI | **Accepted** |
| **C. Film-score / autonomous composer path** | Fancy | Wrong surface; agents must not import companion/exchange stores | **Reject** |
| **D. Map `counter_melody` to development `continue` / `vary_section` only** | Fewer arrangement fields | Misses the existing countermelody operation and role checks | **Reject** |

### Part F — Returning material to Ardour

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. OSC “create MIDI track and paste notes”** | No files | Not a supported faithful region API | **Reject** |
| **B. Prepare SMF (+ optional stem) + manifest; Lua `Editor:do_import` / Session APIs place at `start_samples` with tempo disposition locked in the recipe; document manual Session → Import fallback** | Ardour owns the write; aligns bars | Operator runs Lua or import UI | **Accepted** |
| **C. Mukit writes playlist entries into session files** | Automated | Forbidden | **Reject** |

### Part G — Feature flag / enablement

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always on when backend is up** | Convenient | Opens a writable exchange root by surprise | **Reject** |
| **B. Gate mutating exchange routes on `ARDOUR_COMPANION_ENABLED` only** | One flag | Blocks pure file upload when OSC is off | **Reject** |
| **C. `ARDOUR_EXCHANGE_ENABLED` (default off; same truthy set as companion); ingest/prepare/list require it; OSC context enrichment additionally requires companion connected; status GET for exchange always 200 with `enabled`** | File path works without UDP; OSC optional | Two flags in docs | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Companion OSC | `ardour_companion_*`, `/ardour/companion/*`, fake peer, feedback-observed transport/strips/`locate_samples`/`selected_ssid`; reducer already lists `/position/samples` but parses as int only; `SET_SURFACE_FEEDBACK=8219` hardcoded in codec/acceptance tests | Lock feedback **`9243`** (`8219+1024`); parse position as int **or** digit string; update companion tests in-lockstep; bank/strip/gainmode stay locked |
| Lifespan teardown | `main.py` → `shutdown_ardour_companion()` | Clear exchange preview (+ optional package GC) beside companion |
| Bounded upload | `audio_upload.read_upload_bounded` | Zip ingest multipart cap → 413 / `ardour_exchange_package_too_large` |
| Composition slice | `filter_composition_tracks` / `filter_composition_bar_range` in `neural_audio_stem_partition.py` | Prepare outbound SMF from selected tracks/bars |
| Import Apply (SPA) | `ImportControls` → `completeImport` (replace working score) | Exchange Apply uses the same helper |
| Lua examples | `backend/examples/ardour/{add_named_location_marker,list_track_count}.lua` + README | Add export/import region recipes; keep operator-install only |
| MIDI import | `POST /imports/midi`, `composition_midi_import`, tempo/PPQ → V2 | Canonicalize inbound `material.mid` |
| MIDI/WAV export | `POST /export/midi`, `/export/wav`, `render_midi_with_report` | Build outbound SMF after slice |
| Arrangement | `create_countermelody` (requires after `role: countermelody`); `change_instrumentation`; fake_llm + patch tests; catalog `cello` / GM 42 | Realize `counter_melody` / `arrangement_variation` |
| Development | `vary_section` with source bar range | Realize `regenerate_region` |
| Neural stem egress | `/neural-audio/stems/{id}/audio` | Optional outbound `audio/` copy |
| Storage root policy | `storage_root_policy.reject_storage_root` | `ARDOUR_EXCHANGE_ROOT` |
| Thin Ardour tab | `ArdourCompanionPanel.jsx`, `ardourCompanionApi.js` | Add Exchange section |
| Agent boundary | `test_ai_agents_architecture.py` forbids store imports | Forbid `ardour_exchange_*` / companion imports from `ai_agents/` |
| Alembic head | `20261003_0027` spatial_scenes | **No migration** (FS + session-memory packages) |
| Docs | `docs/ardour-companion.md`, `docs/daw-interoperability.md` | New exchange doc + cross-links |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Exchange documents + root | No package/manifest/preview DTOs; no exchange root |
| Selected-region export Lua | Foundation Lua does not export MIDI regions |
| Positioned import Lua | No bar-aligned import helper |
| Ingest/prepare HTTP | Companion routes are transport/mixer only; need bounded zip read |
| Session context snapshot | Need `9243` feedback + string `/position/samples` + optional `sample_rate` |
| Realize bridge | Must call `create_countermelody` / `change_instrumentation` / `vary_section` |
| Outbound alignment package | Slice via existing filters + manifest |
| Lifespan preview clear | Restart must drop session preview honesty |
| Fixtures + acceptance | No 8-bar round-trip fixture |
| Docs for deep exchange | Honesty limits still say no bidirectional material workflow |

### Coupling risks to avoid

1. Editing Ardour session XML / playlist files from Mukit.
2. Remote-executing or injecting Lua over OSC/HTTP.
3. Writing `tracks[].events[]` from ingest, prepare, context poll, or Lua install docs.
4. Inventing `composition.v5` or a second playable score inside the manifest.
5. Auto-Apply on ingest or auto-Apply of realize candidates.
6. Streaming notes over OSC as the material path.
7. `ai_agents/` importing exchange/companion modules.
8. Logging MIDI bytes, full manifests with paths that embed secrets, or event arrays at INFO.
9. Using `DATASET_ROOT` or `PROJECT_DB_PATH` as the exchange root.
10. Claiming stem WAV is sample-locked to regenerated MIDI when neural fidelity says otherwise.
11. Breaking foundation companion connect/transport contracts or requiring LV2 — feedback bit change is additive (`9243`) and companion tests update in the same task.
12. Editing `ROADMAP.md` during implementation.
13. Multi-DAW abstraction.
14. Coupling Tone.js transport to Ardour playhead as a requirement for exchange.
15. Mapping `counter_melody` to development-only operations when `create_countermelody` exists.
16. Merge-into-existing-score Apply (ship-1 is replace via `completeImport` only).

## Scope And Decisions

### In scope
- Exchange schemas, settings (`ARDOUR_EXCHANGE_ENABLED`, `ARDOUR_EXCHANGE_ROOT`, max bytes, TTL/GC caps), package store, lifespan preview clear.
- Lua: export selected MIDI region → package; import package at `start_samples` / `start_bar`.
- OSC session context enrichment (`SET_SURFACE_FEEDBACK=9243`; string-safe `/position/samples`; `/strip/list` end for `sample_rate`).
- HTTP: exchange status, context, ingest (bounded upload or root path), preview get, apply, realize, prepare, download package.
- Ardour-tab Exchange UI with `completeImport` Apply.
- Fixtures: 8-bar source MIDI + manifest; cello counter-melody expected alignment fields; fake-mode tests.
- Docs: `docs/ardour-session-exchange.md` + updates to companion / daw-interop / AGENTS.md / `.env.example`.

### Out of scope
- Editing `.ardour` XML; remote Lua execution; LV2; multi-DAW.
- Bidirectional continuous note sync / co-editing playlists.
- Durable SQLite package rows / Alembic.
- Auto-Apply; autonomous composer; film-score commit path.
- Merge-into-existing-project Apply (replace only).
- Tone.js ↔ Ardour playhead coupling.
- Requiring neural stems for acceptance (MIDI round-trip is enough; stem prepare is supported when a completed stem exists).
- Ableton Link / JACK graph UI.
- BBT position feedback bit (`+32`) in ship-1.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Documents**

Schemas live in `backend/app/ardour_exchange_schemas.py`. `extra=forbid`. This module does not import FastAPI, SQLite project_store, `ai_agents/`, torch, or video modules.

**Forbidden-key scan** (exact key names): `events`, `notes`, `pitch`, `pitches`, `midi_events`, `composition`, `composition_json`, `session_xml`, `ardour_session`, `prompt`. Scope: **manifest JSON and prepare/ingest request bodies only**. Realize carries `intent` enum + optional short `instruction` ≤ 200 chars on the request DTO (not inside the manifest). **`ardour.exchange.preview.v1` and apply responses may include `draft_composition` / `composition` as playable `composition.v2`** — that is intentional and exempt from the forbidden-key scan.

`ArdourExchangeManifestV1`. Schema version `ardour.exchange.manifest.v1`.

| Field | Rule |
|-------|------|
| `schema_version` | literal `ardour.exchange.manifest.v1` |
| `package_id` | `^aex_[0-9a-f]{16}$` |
| `direction` | `inbound` \| `outbound` |
| `track_name` | 1..120 |
| `source_track_ssid` | optional int 1..1024 |
| `tempo_bpm` | 40..240 (int or finite float accepted then normalized to display int in UI) |
| `time_signature` | V2 meter string (e.g. `4/4`) |
| `ticks_per_quarter` | default 480, gt 0 |
| `start_bar` | int ≥ 1 |
| `bar_count` | int 1..256 |
| `start_samples` | int ≥ 0 |
| `length_samples` | optional int ≥ 0 |
| `sample_rate` | int 8000..192000 |
| `material_relpath` | literal `material.mid` for ship-1 MIDI packages |
| `audio_relpaths` | optional list of `audio/*.wav` relative paths, max 16 |
| `source_fingerprint` | 16..128 hex/fingerprint string for soft-stale |
| `created_at` | server or Lua ISO timestamp |

`ArdourExchangeSessionContextV1`. Schema version `ardour.exchange.session_context.v1`. Fields: companion `connection_state`, `locate_samples`, `transport_playing`, `selected_ssid`, `selected_strip_name`, `sample_rate` (nullable until `/strip/list` end observed), `tempo_bpm` nullable (OSC may not supply; prefer manifest), `stale`, `warnings[]`.

`ArdourExchangePreviewV1`. Schema version `ardour.exchange.preview.v1`. Holds `manifest`, optional `session_context`, `draft_composition` (`composition.v2`), `import_report` summary codes only (no raw MIDI), `alignment` echo (`start_bar`, `bar_count`, `tempo_bpm`, `time_signature`). Session-only.

`ArdourExchangePrepareRequestV1` / `ArdourExchangePrepareResultV1`. Select tracks/bars from working V2 (or last applied draft), optional `stem_id` to copy WAV into `audio/`, write package + download URL. Slice via `filter_composition_tracks` then `filter_composition_bar_range` before `render_midi_with_report`.

`ArdourExchangeRealizeRequestV1`. `intent`: `counter_melody` \| `arrangement_variation` \| `regenerate_region`. Locked map:

| Intent | Calls | Locked soft constraints |
|--------|-------|-------------------------|
| `counter_melody` | `run_composition_arrangement_preview` with `operation=create_countermelody` | After instrumentation: retain source melody part(s) + add catalog `cello` (GM 42) part with `role: countermelody`; source tracks from draft; span = alignment `bar_count` (acceptance 8) |
| `arrangement_variation` | arrangement `change_instrumentation` | Explicit before/after inventories that differ; fake-mode deterministic |
| `regenerate_region` | development `vary_section` | Source = alignment `start_bar` .. `start_bar+bar_count-1`; omit `output_bars` |

Does not Apply. Correlation id ties to exchange preview.

**2. Package layout (ship-1)**

```text
<aex_…>/
  manifest.json          # ardour.exchange.manifest.v1
  material.mid           # SMF Type 1 preferred; Type 0 accepted on ingest
  audio/                 # optional
    stem_<id>.wav
```

Zip upload must expand to the same shape; refuse path escape (`..`), symlinks, and files over `ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES`. Multipart bytes via `read_upload_bounded`.

**3. OSC / companion delta**

Foundation `/set_surface` bank_size=`16`, strip_types=`159`, gainmode=`0` stay locked. Feedback bitmask locks to **`SET_SURFACE_FEEDBACK = 9243`** (= foundation `8219` + **`1024`** position-in-samples). Reducing bits is forbidden. BBT (`+32`) is out of ship-1. `/position/samples` feedback is often a **string** — reducer must accept `int` or digit string. `/strip/list` may be sent after connect to capture `sample_rate` from `end_route_list`. Update all companion tests that assert `8219` in the same task. No OSC note streaming. No OSC session-file write.

**4. Lua recipes (operator install)**

| Script | Role |
|--------|------|
| `export_selected_midi_region.lua` | Read `Editor:get_selection` / extents; write SMF + manifest into `ARDOUR_EXCHANGE_ROOT`. Prefer Ardour Editor/Session MIDI export APIs when available; otherwise a minimal Type 0/1 SMF writer from the MIDI model. README states the chosen API. Refuse empty selection |
| `import_exchange_package.lua` | Read package dir or zip path; `Editor:do_import` (or Session import APIs) at `start_samples`; use `SMFTempoIgnore` or documented disposition so Mukit conductor tempo in SMF + manifest `tempo_bpm` stay honest; create/select named MIDI track from `track_name` when possible |
| Existing marker / track-count scripts | Unchanged |

Mukit never ships a remote trigger for these scripts.

**5. HTTP surface (locked)**

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/ardour/exchange/status` | Always **200**: `{ enabled, root_configured, companion_connected, … }` |
| GET | `/ardour/exchange/context` | `session_context.v1` (empty/disconnected fields when companion down) |
| POST | `/ardour/exchange/ingest` | multipart package zip (**`read_upload_bounded`**) **or** `{ package_id }` under root → preview |
| GET | `/ardour/exchange/preview` | Current session preview or 404 |
| POST | `/ardour/exchange/apply` | Returns `{ composition, manifest }` for SPA **`completeImport`** (replace working V2). Server does **not** write `projects.composition_json` |
| POST | `/ardour/exchange/realize` | Builds/calls arrangement or development preview per locked map; returns that preview payload + exchange correlation id; does not Apply |
| POST | `/ardour/exchange/prepare` | Writes outbound package; returns manifest + download path |
| GET | `/ardour/exchange/packages/{package_id}/download` | Zip download |
| DELETE | `/ardour/exchange/preview` | Clear session preview |

Mutating routes when `ARDOUR_EXCHANGE_ENABLED` off → `403` / `ardour_exchange_disabled`. Never edit Ardour files. Lifespan clears preview (and may GC exchange temp packages).

**6. Fake mode / fixtures**

- `backend/fixtures/ardour_exchange/eight_bar_idea/` — 8 bars, `4/4`, 120 bpm, 480 TPQ, track name `Idea`, `start_bar=1`, `bar_count=8`, deterministic melody SMF + manifest.
- `backend/fixtures/ardour_exchange/cello_counter_melody_outbound/` — expected outbound manifest alignment fields after realize+prepare (composition JSON optional for unit tests).
- Tests use fixtures + `ARDOUR_EXCHANGE_ENABLED=1`; companion fake optional for context tests. **No real Ardour process in CI.**

**7. Stable error codes (additions)**

| Code | When |
|------|------|
| `ardour_exchange_disabled` | Flag off (mutating) |
| `ardour_exchange_root_unconfigured` | Root missing/refused |
| `ardour_exchange_package_invalid` | Bad layout / manifest |
| `ardour_exchange_package_too_large` | Upload exceeds max bytes (HTTP 413) |
| `ardour_exchange_midi_invalid` | Import canonicalization failed |
| `ardour_exchange_preview_missing` | Apply/prepare without preview when required |
| `ardour_exchange_alignment_invalid` | Bar/tempo/sample fields inconsistent |
| `ardour_exchange_realize_unsupported` | Unknown intent |
| `ardour_exchange_stem_unavailable` | Optional stem copy missing/incomplete |

## UI (locked)

- Remain on Ardour tab; subsection **Exchange**.
- Show exchange `enabled` / root configured; session context (samples, strip, sample rate).
- Ingest: file picker (zip/package) + “Scan exchange root” list (package ids only).
- Preview card: track name, tempo, meter, `start_bar`–`start_bar+bar_count-1`, Apply / Discard.
- Apply inbound: call **`completeImport`** with draft composition + import report (replace working score — same copy as ImportControls). Discard clears preview via DELETE.
- Realize: intent select (Counter-melody / Arrangement variation / Regenerate region); runs preview; **inline candidate list**; Apply uses existing arrangement/development Apply helpers (`applySelectedArrangementCandidate` / `applySelectedDevelopmentCandidate`); do not duplicate fingerprint math; never auto-Apply.
- Prepare: build outbound package; Download zip; copy path hint for Lua import.
- Opening tab never ingests/applies/prepares.
- Docs link to Lua install + honesty limits.

## Tests (locked)

1. Manifest schema refuse/round-trip; forbidden keys on request bodies; package path escape refuse; preview may carry `draft_composition`.
2. Ingest eight-bar fixture → preview `bar_count=8`, tempo 120, draft events non-empty; Apply returns composition; server project row untouched without SPA save; oversized zip → 413 / `ardour_exchange_package_too_large`.
3. Realize `counter_melody` with fake LLM → arrangement `create_countermelody`; candidate includes cello/countermelody track (program 42); span preserves 8 bars.
4. Prepare outbound → uses track/bar filters; manifest `start_bar`/`bar_count`/`tempo_bpm` match inbound; SMF import round-trip timing within import quantization policy.
5. Flag off → status 200 `enabled=false`, mutating 403.
6. Root set to `DATASET_ROOT` → refused at settings load / prepare.
7. `ai_agents/` import forbid for exchange modules.
8. Lua scripts exist, README states operator-install only + chosen SMF export approach; static parse/smoke (no Ardour).
9. Companion `SET_SURFACE_FEEDBACK == 9243`; fake peer string `/position/samples` updates `locate_samples`; context enrichment does not write notes.
10. Frontend: Apply uses `completeImport`; poll/list cleanup; no auto-ingest on mount.
11. Lifespan/teardown clears exchange preview (no leftover preview after shutdown helper).

## Docs (locked)

- New `docs/ardour-session-exchange.md`: workflows Ardour→Composer and Composer→Ardour; package layout; Lua install; alignment rules; Compose volume mount for `ARDOUR_EXCHANGE_ROOT`; honesty limits (no session XML edits; no remote Lua; OSC context vs manifest authority; replace Apply).
- Update `docs/ardour-companion.md` (feedback `9243` / position string) and `docs/daw-interoperability.md` with exchange pointers.
- `.env.example`, `AGENTS.md` entry points, examples README.
- Mandatory `/aif-docs` checkpoint after implement.

## Commit Plan

- **Commit 1** (tasks 1–2): `feat(ardour): add exchange schemas, settings, and package store`
- **Commit 2** (tasks 3–4): `feat(ardour): ingest MIDI packages into exchange preview`
- **Commit 3** (tasks 5–6): `feat(ardour): realize intents and prepare aligned outbound packages`
- **Commit 4** (tasks 7–8, 11): `feat(ardour): exchange HTTP API, position feedback 9243, and lifespan clear`
- **Commit 5** (tasks 9–10): `feat(ardour): add Exchange UI, Lua recipes, docs, and fixtures`

## Tasks

### Phase 1: Contracts and package store

- [x] Task 1: Define `ardour.exchange.manifest.v1`, `session_context.v1`, `preview.v1`, prepare/realize DTOs, and stable error codes (incl. `ardour_exchange_package_too_large`) in `ardour_exchange_schemas.py` (`extra=forbid`). Forbidden-key scan on **manifest + prepare/ingest request bodies only**; preview/apply may include `draft_composition` / `composition`. Implement `ardour_exchange_settings.py`: `ARDOUR_EXCHANGE_ENABLED` (default off; unrecognized→off), `ARDOUR_EXCHANGE_ROOT`, `ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES`, TTL/GC caps; call `reject_storage_root` against `DATASET_ROOT` and `PROJECT_DB_PATH`. Unit tests for schema + settings + forbidden-key scope.

  LOGGING: DEBUG settings load `{enabled, root_basename, max_bytes}` — never full absolute home paths at INFO; INFO unrecognized flag → off with code `ardour_exchange_flag_unrecognized`; WARN root refuse with `storage_root_rejected` reason only.

  Files: `backend/app/ardour_exchange_schemas.py` (new), `backend/app/ardour_exchange_settings.py` (new), `backend/tests/test_ardour_exchange_schemas.py` (new), `backend/tests/test_ardour_exchange_settings.py` (new)

- [x] Task 2: Package store under `ARDOUR_EXCHANGE_ROOT`: create/read/list/delete packages, zip round-trip, path-escape refuse, atomic writes (`manifest.json` + `material.mid` + optional `audio/`). Process-memory registry of current preview id. No SQLite. GC oldest packages beyond cap. Expose `clear_exchange_preview()` / shutdown hook for lifespan. (depends on 1)

  LOGGING: INFO package create/delete `{package_id, direction, byte_size}`; WARN invalid layout codes; never log MIDI payloads.

  Files: `backend/app/services/ardour_exchange_store.py` (new), `backend/tests/test_ardour_exchange_store.py` (new)

<!-- Commit checkpoint: tasks 1-2 -->

### Phase 2: Ingest and alignment

- [x] Task 3: Ingest pipeline: validate manifest + MIDI; call existing MIDI import canonicalization to build draft `composition.v2`; build `ardour.exchange.preview.v1` with alignment echo; refuse inconsistent `bar_count` vs timeline when detectable. Support `package_id` from root and zip bytes already bounded by the router. Pure helpers for bar↔samples using manifest `tempo_bpm` + `sample_rate` (constant tempo across span for ship-1). (depends on 1, 2)

  LOGGING: INFO ingest `{package_id, bar_count, tempo_bpm, track_name}`; WARN import issue codes only; never event arrays.

  Files: `backend/app/services/ardour_exchange_ingest.py` (new), `backend/app/services/ardour_exchange_align.py` (new), `backend/tests/test_ardour_exchange_ingest.py` (new), `backend/tests/test_ardour_exchange_align.py` (new)

- [x] Task 4: Fixture package `backend/fixtures/ardour_exchange/eight_bar_idea/` (manifest + SMF) matching acceptance (8 bars, 4/4, 120 bpm, 480 TPQ). Golden ingest test locks alignment fields and non-empty draft notes. (depends on 3)

  LOGGING: tests assert codes/fields only.

  Files: `backend/fixtures/ardour_exchange/eight_bar_idea/**` (new), tests extending Task 3

<!-- Commit checkpoint: tasks 3-4 -->

### Phase 3: Realize + prepare

- [x] Task 5: Realize service with locked intent map — `counter_melody` → `run_composition_arrangement_preview` / `create_countermelody` (after: retain source melody + add catalog `cello` GM 42 `role: countermelody`); `arrangement_variation` → arrangement `change_instrumentation`; `regenerate_region` → development `vary_section` on alignment bar range. Build requests from ingested draft composition. `LLM_FAKE_MODE` deterministic path. Return candidate preview payload; **do not** Apply; **do not** import `ai_agents/`. Correlation id ties back to exchange preview. (depends on 3)

  LOGGING: INFO realize `{intent, operation, correlation_id, candidate_count}`; WARN unsupported intent; never prompts or note dumps.

  Files: `backend/app/services/ardour_exchange_realize.py` (new), `backend/tests/test_ardour_exchange_realize.py` (new)

- [x] Task 6: Prepare outbound package: slice with **`filter_composition_tracks` then `filter_composition_bar_range`** (`neural_audio_stem_partition.py`); SMF via `render_midi_with_report`; manifest `direction=outbound` copying alignment (`start_bar`, `bar_count`, tempo, meter, `start_samples`, `track_name`); optional copy of completed neural stem WAV into `audio/` by `stem_id` (refuse incomplete). Zip download helper. Fixture `cello_counter_melody_outbound` for expected alignment fields. Do not invent a third slicer. (depends on 2, 3)

  LOGGING: INFO prepare `{package_id, bar_count, has_audio}`; WARN stem unavailable; never WAV byte hashes of full files at INFO (size only).

  Files: `backend/app/services/ardour_exchange_prepare.py` (new), `backend/fixtures/ardour_exchange/cello_counter_melody_outbound/**` (new), `backend/tests/test_ardour_exchange_prepare.py` (new)

<!-- Commit checkpoint: tasks 5-6 -->

### Phase 4: HTTP + OSC context + lifespan

- [x] Task 7: Lock companion `SET_SURFACE_FEEDBACK = 9243` (`8219+1024`); update codec/acceptance tests that asserted `8219`. Parse `/position/samples` as int **or** digit string in `ardour_feedback_state`. Optional `/strip/list` after connect to capture `sample_rate` from `end_route_list`. Build `ardour.exchange.session_context.v1` from companion status without claiming region bar authority. Fake peer emits string position + end_route_list. Foundation transport/mixer behaviors must keep passing with the new bitmask. (depends on 1)

  LOGGING: DEBUG context snapshot field presence; WARN when sample_rate unknown; never raw OSC blobs at INFO.

  Files: `backend/app/services/ardour_osc_paths.py`, `backend/app/services/ardour_feedback_state.py`, `backend/app/services/ardour_osc_fake_peer.py`, `backend/app/services/ardour_exchange_context.py` (new), `backend/tests/test_ardour_osc_codec.py`, `backend/tests/test_ardour_companion_acceptance.py`, `backend/tests/test_ardour_feedback_state.py`, new context tests

- [x] Task 8: Router `ardour_exchange.py` with locked HTTP surface; register in `main.py`. Ingest multipart via **`read_upload_bounded`** (`ARDOUR_EXCHANGE_MAX_PACKAGE_BYTES`) → 413 / `ardour_exchange_package_too_large` on overflow. Status GET always 200. Map domain errors to HTTP. API tests with fixtures + fake companion. Confirm no `projects.composition_json` write on apply (SPA-owned working set). Architecture test: forbid `ai_agents/` imports of exchange modules. (depends on 2, 3–7)

  LOGGING: INFO route outcomes `{path, code}`; never multipart bodies.

  Files: `backend/app/routers/ardour_exchange.py` (new), `backend/app/main.py`, `backend/app/audio_upload.py` (reuse), `.env.example`, `backend/tests/test_ardour_exchange_api.py` (new), `backend/tests/test_ai_agents_architecture.py`

- [x] Task 11: Wire exchange preview clear (+ optional temp package GC) into FastAPI lifespan beside `shutdown_ardour_companion()`. Test that after shutdown helper, GET preview is missing / empty and no stale preview id remains. (depends on 2, 8)

  LOGGING: INFO lifespan exchange teardown; never package MIDI dumps.

  Files: `backend/app/main.py`, `backend/app/services/ardour_exchange_store.py`, `backend/tests/test_ardour_exchange_api.py` (or dedicated teardown test)

<!-- Commit checkpoint: tasks 7-8, 11 -->

### Phase 5: UI, Lua, docs, acceptance

- [x] Task 9: Frontend `ardourExchangeApi.js` + Exchange section in `ArdourCompanionPanel.jsx` (or extracted `ArdourExchangePanel.jsx` rendered inside the Ardour tab). Ingest/preview/**Apply via `completeImport`**/Discard, realize intents (arrangement/development Apply helpers), prepare/download, context readout. Never auto-ingest on mount. Unit tests for gating and `completeImport` wiring. (depends on 8)

  LOGGING: `createAppLogger('ardourExchange')` with package_id / intent / enabled only.

  Files: `frontend/src/api/ardourExchangeApi.js` (new), `frontend/src/components/ArdourExchangePanel.jsx` and/or `ArdourCompanionPanel.jsx`, `frontend/src/utils/ardourExchange/*` (new as needed), colocated `*.test.js`

- [x] Task 10: Ship Lua `export_selected_midi_region.lua` and `import_exchange_package.lua` under `backend/examples/ardour/` with README install + `ARDOUR_EXCHANGE_ROOT` path guidance (Compose bind-mount) + **documented SMF export approach** (Editor/Session export API or minimal Type 0/1 writer). Write `docs/ardour-session-exchange.md`; update `docs/ardour-companion.md` (feedback `9243`), `docs/daw-interoperability.md`, `AGENTS.md`, short README pointer. Acceptance test module covering the locked scenario end-to-end in fake modes (ingest fixture → realize `create_countermelody` → prepare → assert alignment). Mandatory `/aif-docs` checkpoint. (depends on 4–9, 11)

  LOGGING: n/a for docs; acceptance asserts codes/alignment only.

  Files: `backend/examples/ardour/export_selected_midi_region.lua` (new), `backend/examples/ardour/import_exchange_package.lua` (new), `backend/examples/ardour/README.md`, `docs/ardour-session-exchange.md` (new), `docs/ardour-companion.md`, `docs/daw-interoperability.md`, `AGENTS.md`, `README.md`, `backend/tests/test_ardour_exchange_acceptance.py` (new)

<!-- Commit checkpoint: tasks 9-10 -->

## Acceptance Criteria

1. Musician can export a selected MIDI region from Ardour (Lua package) or upload the same package shape into AI Composer and see tempo, meter, track name, and bar span in a preview.
2. Explicit Apply **replaces** working `composition.v2` via `completeImport` without Mukit editing Ardour session files.
3. Realize intent `counter_melody` runs arrangement `create_countermelody` with a cello countermelody part over the same bar span; Apply remains manual.
4. Prepare writes an outbound package whose manifest alignment matches the inbound 8-bar span (and tempo/meter); Lua import recipe documents placement at `start_samples` / same bars.
5. Optional stem/audio file can be included in an outbound package from a completed neural stem without mutating the stem source or V2 notes.
6. OSC session context uses feedback `9243` and enriches transport position / selected strip / sample rate when companion connected; manifest remains authoritative for region bar alignment.
7. `ARDOUR_EXCHANGE_ENABLED` defaults off; status GET readable when off; exchange root refuses dataset/DB paths; oversized upload refused.
8. No remote Lua execution; no `.ardour` XML writes; no `composition.v5`; no LV2; no multi-DAW bus.
9. Fixtures + mocked tests pass without a real Ardour process; integration docs exist; lifespan clears exchange preview.

## Acceptance Criteria Mapping

| Criterion | Tasks |
|-----------|-------|
| Ingest selected MIDI + preview metadata | 1, 2, 3, 4, 8, 9, 10 |
| Explicit Apply replace via completeImport; no Ardour XML | 3, 8, 9, 10 |
| Cello counter-melody via create_countermelody + manual Apply | 5, 9, 10 |
| Outbound package aligned to same bars | 6, 10 |
| Optional stem in package | 6, 8 |
| OSC context `9243` + string position vs manifest authority | 7, 8, 9 |
| Flag off + root policy + upload bound | 1, 2, 8 |
| No remote Lua / no v5 / no LV2 | 8, 10 |
| Fixtures, fake tests, docs, lifespan clear | 4, 6, 10, 11 |

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer new `ardour_exchange_*` modules; thin wires into `main.py` and the Ardour tab only.
3. Never invent `composition.v5`; never write notes from OSC feedback or prepare.
4. Never edit Ardour session files; never remote-execute Lua.
5. `ai_agents/` must not import exchange or companion modules.
6. Reuse MIDI import/export, arrangement `create_countermelody`, development `vary_section`, and stem partition filters — do not fork generators or slicers.
7. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
8. Verbose logging with redaction; no MIDI/WAV payloads at INFO.
9. Companion feedback locks to **`9243`**; update foundation tests that hardcoded `8219` in Task 7 — do not leave mixed constants.
10. Opening the Ardour tab must never auto-ingest, auto-apply, or auto-prepare.
11. Fake fixtures are required for CI; real Ardour is optional manual verification.
12. Ship-1 constant-tempo alignment math; multi-tempo spans inside one region may WARN and still carry SMF conductor data.
13. Docker: document binding `ARDOUR_EXCHANGE_ROOT` into both the backend container and the host path Lua writes to.
14. Counter-melody acceptance uses arrangement `create_countermelody` + catalog `cello` / GM 42.
15. SPA Apply is **`completeImport` replace**; do not silently CAS-write projects; merge-into-existing is out of scope.
16. Ingest zips through `read_upload_bounded`; overflow → `ardour_exchange_package_too_large` / 413.
17. Lifespan must clear exchange preview (Task 11) for restart honesty.
