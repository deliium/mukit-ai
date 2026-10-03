# Implementation Plan: V5 Expressive MIDI Performance Input

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: thin integration only — extend existing `MidiInputPanel` + capture/live path; no new studio tab. Capability / transport status may surface as a compact readiness line beside Enable MIDI / device select
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Velocity promote/degrade + relative `tick_offset` locked in Task 1; Task 3 scoped to ship-subset UMP (no WASM stack); Task 5 adds Composition-level validators + import/generate `[]`; new Task 6 prunes `note_performances` on editor delete/cut (motif-reconcile twin); Task 7 persists MPE pref in `midiInput:v1` + `VITE_MIDI_EXPRESSIVE_ENABLED`; Task 8 requires SMF `performance_expression_omitted`; Tone pitch/pressure audition stays non-acceptance
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: extend the browser performance/input model for higher-resolution and per-note expressive MIDI where the platform supports it, while preserving MIDI 1.0 / ordinary Web MIDI workflows and never breaking required `composition.v2` note fields. Simulated-device tests are mandatory. The playable score stays `composition.v2`. No `composition.v5`

## Roadmap Linkage
Milestone: "V5 Expressive MIDI performance input"
Rationale: Live MIDI capture and co-performance already commit ordinary note-on/off + CC64 into V2, but pitch bend, aftertouch, higher-resolution velocity, MPE channel mapping, and any MIDI 2.0/UMP path are ignored or unavailable. This milestone adds a transport-agnostic expressive performance model with capability-driven transport selection and a deterministic degrade path to MIDI-compatible V2 notes. The milestone is appended to `.ai-factory/ROADMAP.md` because linkage is enabled and no incomplete milestone remained. Implementation does not edit `ROADMAP.md`.

## Goal

Represent higher-resolution and per-note expressive performance where technically available, without requiring MIDI 2.0 hardware or breaking existing MIDI 1.0 / QWERTY / SMF workflows.

Ship:

1. A **browser/platform capability probe** that inspects available MIDI transports before selecting one (Web MIDI 1.0 bytes today; MPE zone hints; experimental UMP/MIDI 2.0 only when the API surface is actually present).
2. A **transport-agnostic expressive event / take model** independent of legacy MIDI 1.0 7-bit assumptions.
3. Capture and mapping for, where available:
   - higher-resolution velocity
   - per-note expression / pressure
   - pitch expression
   - controller data beyond CC64
   - MPE-compatible input mapping
   - MIDI 2.0/UMP abstractions (parse + degrade; not a hard dependency)
4. **Graceful fallback** to the existing MIDI input path (note on/off + CC64 → V2 velocity 1–127 + sustain).
5. **Optional performance metadata** persistence that references committed event ids — never invents a second playable score and never changes required note shape.
6. **Simulated-device unit tests** covering expressive capture, MPE-ish multi-channel notes, UMP/high-res degrade, and MIDI 1.0-only fallback.

Acceptance:

1. With an ordinary MIDI 1.0 device (or simulated 3-byte stream), record/commit behavior remains compatible: notes land with `velocity` in `1..127`, sustain via `sustain_pedals` as today, and existing unit/store tests stay green.
2. With a simulated expressive / MPE-capable stream, useful per-note expression (at least attack velocity high-res when present, pitch bend relative to the note, and channel/poly pressure samples) is preserved in optional performance metadata keyed by committed `event_id`.
3. Degrade is deterministic: metadata absence or strip still leaves valid `composition.v2` notes whose `velocity` matches the MIDI 1.0 projection of any high-res velocity; SMF export and Tone playback remain correct for the canonical note fields.
4. Capability probe never calls `requestMIDIAccess` at import/startup; Enable MIDI stays user-gesture gated; QWERTY test input still works when Web MIDI is unsupported.
5. No `composition.v5`, no backend WebSocket MIDI bridge, no SysEx enablement (`sysex: false` remains), no logging of full MIDI dumps / event arrays at INFO.

```text
Device / simulated bytes / optional UMP
        │
        ▼
midi.capability.v1 probe
  web_midi_1 | mpe_hint | ump_experimental | unsupported
        │
        ▼
Transport adapter → midi.expressive.event.v1
        │
        ▼
Session take (midi.performance.take.v1)
        │
   ┌────┴────────────────────────────┐
   ▼                                 ▼
midiTakeApply (canonical V2)   optional note_performances[]
velocity 1..127 + sustain      event_id → expression samples
        │                                 │
        └────────────┬────────────────────┘
                     ▼
              composition.v2 (required notes unchanged)
```

**Terminology lock:** Product generation is **V5**. The playable score stays `composition.v2`. There is no `composition.v5`. An **expressive event** is a transport-agnostic capture atom (`midi.expressive.event.v1`) — never a playable note by itself. A **performance take** (`midi.performance.take.v1`) is session-only until Stop & commit. **Note performance metadata** is optional track-local data referencing existing `tracks[].events[].id` values — consumers may ignore it; playback/export must not invent pitches from it. **Degrade** means projecting expressive fields onto MIDI 1.0–compatible V2 note fields (`velocity` 1–127, existing sustain/automation semantics). **MPE mapping** means interpreting a channel zone (master + member channels) into per-note pitch/pressure expression without requiring MIDI 2.0. **UMP abstraction** is an optional parser interface for Universal MIDI Packet–shaped input when the browser/platform exposes it; absence must not fail ordinary capture. Predecessor: live MIDI milestone (`docs/midi-live-input.md`) and co-performance stream (`docs/co-performance.md`).

Predecessor plans: browser capability-probe style in `.ai-factory/plans/v5-browser-webgpu-inference.md` (`midiInputSupport.js` pattern). Live MIDI stack: `frontend/src/utils/midiInput*.js`, `midiPerformanceCapture.js`, `midiTakeApply.js`, `liveMidiStream.js`.

## Approach Evaluation (locked)

### Part A — Where expressive data lives after commit

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Widen required `velocity` to 16-bit / change note identity** | Matches MIDI 2.0 velocity | Breaks every validator, export, tokenizer, fixtures, SMF | **Reject** |
| **B. Stuff expression into `articulations` or harmony** | No schema add | Lies; articulations are score markings; harmony is not performance | **Reject** |
| **C. Keep required note fields identical; add optional track-local `note_performances[]` referencing `event_id` (motifs-style); session take holds full expressive curves until commit** | Preserves canonical notes; optional metadata; clear degrade | Schema + migrate validators; consumers must ignore unknown | **Accepted** |

### Part B — Transport selection

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Assume MIDI 2.0/UMP everywhere; fail otherwise** | Simple code path | Breaks Chrome/Firefox/Safari reality and CI | **Reject** |
| **B. Keep only MIDI 1.0 3-byte parse forever** | Minimal change | Cannot meet higher-res / UMP abstraction acceptance | **Reject** |
| **C. Probe first; default transport = Web MIDI 1.0 byte stream; enable MPE zone mapping when RPN/device hint or user zone config present; bind UMP adapter only when capability says available — else ignore UMP path** | Matches “inspect before selecting”; graceful fallback | Two/three adapters to maintain | **Accepted** |

### Part C — Expressive model vs MIDI 1.0 types

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Leak `Uint8Array` status bytes into capture / store / V2** | Fast | Couples all layers to MIDI 1.0; blocks UMP | **Reject** |
| **B. Parallel “MIDI2-only” capture module that bypasses existing take apply** | Isolated | Diverges commit/undo; double bugs | **Reject** |
| **C. Internal `midi.expressive.event.v1` + adapters (`midi1Bytes`, `mpeZone`, `umpOptional`); existing `parseMidiMessage` becomes the MIDI 1.0 adapter feeding the same take buffer** | Independent model; testable with simulated devices | Adapter boilerplate | **Accepted** |

### Part D — What ships for pitch / controllers / live stream

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Full DAW-grade automation commit for every CC / continuous pitch sample into V2 automation lanes** | Rich | Risks overwriting authored automation; large takes; out of “metadata only where necessary” | **Reject** as default |
| **B. Capture only note on/off velocity forever; document other CCs as future** | Tiny | Fails acceptance for pitch/controller/per-note expression | **Reject** |
| **C. Record path: preserve per-note pitch/pressure/controller samples on `note_performances`; map CC64 → sustain as today; map selected continuous controllers into performance metadata (not silent overwrite of track automation). Live stream (`liveMidiStream`): accept the same expressive events into the ring for Jam features, but Commit of live/Jam stays on existing note degrade unless a thin optional metadata hook is free — do not redesign Jam Commit in this milestone** | Meets acceptance on record-take; keeps Jam scope bounded | Live Commit may not persist expression metadata in ship-1 | **Accepted** |

### Part E — MPE

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require hardware MPE configuration SysEx** | Accurate | `sysex: false`; privacy/docs forbid | **Reject** |
| **B. Ignore channels; treat all notes as channel 0** | Simple | Destroys MPE per-note bend/pressure | **Reject** |
| **C. Configurable / default MPE zone (master ch 1, members 2–16) + optional “MPE off = legacy channel voice”; note identity = (memberChannel, note) while open; on close, fold into expressive note with pitch/pressure timelines** | Works over MIDI 1.0; testable with simulated multi-channel streams | Zone config UX | **Accepted** |

### Part F — MIDI 2.0 / UMP in browsers (2026)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Vendor a WASM UMP stack and claim full MIDI 2.0** | Marketing | Heavy; most browsers still expose MIDI 1.0 `MIDIMessageEvent.data` | **Reject** for ship |
| **B. Define `UmpTransport` interface + pure JS packet decode for the subset we need; activate only when probe finds a real UMP/MIDI 2.0 entry point; CI uses simulated UMP packets without `navigator` | Honest abstraction; tests without hardware | May stay inactive in production browsers until APIs land | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Capability probe | `midiInputSupport.js` (`unsupported` / `insecure_context` / `permission_denied` / `available`) | Extend with transport/MPE/UMP reason codes; still no `requestMIDIAccess` on import |
| Access registry | `midiInputAccess.js` — user gesture, injectable `requestMIDIAccess`, `sysex: false` | Keep; add optional capability snapshot after enable |
| MIDI 1.0 parse | `midiInputMessages.js` — note on/off, CC64; pitch bend/other → `ignored` | Become MIDI 1.0 adapter; expand kinds carefully |
| Capture buffer | `midiPerformanceCapture.js` — notes + sustain; velocity clamped 1–127 | Feed from expressive events; retain injectMessage for tests |
| Commit | `midiTakeApply.js` — V2 notes + sustain merge; timeline extend | Always write canonical notes; attach optional `note_performances` |
| Live stream | `liveMidiStream.js` — ring of note/CC; same parser | Accept expressive events; Jam Commit degrade unchanged (Part D) |
| Simulated tests | `midiInputAccess.test.js`, `midiPerformanceCapture.test.js`, `liveJam.simulatedMidi.test.js`, `musicStore.midiInput.test.js` | Pattern for fake `requestMIDIAccess` + byte injection |
| V2 note contract | `CompositionV2NoteEvent`: `velocity` 1–127, `extra=forbid`; track `sustain_pedals` / `automation` | Do not change required fields |
| Docs | `docs/midi-live-input.md`, `docs/composition-v2.md`, `docs/co-performance.md` | Extend support matrix + degrade rules |
| Export | `composition_midi.py` SMF — note velocity + CC7/10/11/64 | Canonical notes only; optional metadata ignored or future issue codes |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Transport capability document | No `midi.capability.v1` (MPE/UMP/high-res flags) |
| Expressive event / take schemas | Capture still MIDI 1.0–shaped only |
| Pitch bend / pressure / non-64 CC parse | Explicitly ignored today |
| Higher-res velocity | Only 7-bit; no u16 / degrade helpers |
| MPE zone mapping | Channel used only as note key; no bend-per-channel fold |
| UMP abstraction | None |
| Optional `note_performances` on V2 tracks | Schema + frontend validation + persistence |
| Editor GC for `note_performances` | `deleteNotes` only reconciles motifs today — orphans would fail autosave |
| Degrade helpers + export honesty | No projection from high-res → velocity 1–127 documented in code; SMF issue code absent |
| `VITE_MIDI_EXPRESSIVE_ENABLED` | Not in `.env.example` / store gate |
| Simulated expressive / MPE / UMP fixtures | Missing |
| Docs for expressive fallback | Support matrix still MIDI 1.0–only |

### Coupling risks to avoid

1. Changing required `velocity` range or inventing `composition.v5`.
2. Enabling SysEx (`sysex: true`) for MPE config.
3. Overwriting existing track `automation` / `expression` lanes from live CC storms without an explicit user action (out of scope).
4. Making Web MIDI / MIDI 2.0 required at startup or failing QWERTY when probe is negative.
5. Logging raw MIDI/UMP dumps, full takes, or composition event arrays at INFO.
6. Letting performance metadata invent audible pitches when note events are stripped.
7. Redesigning AI Jam Commit / multi-track apply in this milestone.
8. Backend MIDI streaming or new FastAPI routes for live bytes.
9. Editing `ROADMAP.md` during implementation.
10. Treating browser UMP as available without a probe that can be stubbed false in CI.
11. Leaving orphan `note_performances` after editor delete/cut (must prune like motifs).
12. Promoting Tone.js pitch/pressure audition into acceptance for this milestone.
13. Vendoring a full UMP/WASM stack instead of the Task-1 ship-subset decode.

## Scope And Decisions

### In scope
- `midi.capability.v1` probe (Web MIDI 1.0, MPE zone eligibility, experimental UMP flag, high-res velocity path availability).
- Transport-agnostic `midi.expressive.event.v1` + session `midi.performance.take.v1`.
- Adapters: MIDI 1.0 bytes (expanded), MPE zone fold, optional UMP subset (simulated + real if present).
- Capture upgrades in `midiPerformanceCapture` (and thin `liveMidiStream` ingest of expressive events).
- Commit: canonical V2 notes + optional `tracks[].note_performances[]`.
- Backend Pydantic + frontend validation for the optional metadata; secret guard unchanged; import/generate leave metadata empty.
- Editor prune of `note_performances` when event ids are removed (motif-reconcile twin).
- Deterministic degrade helpers (u16→velocity, cents→ignored-on-export, etc.).
- SMF export ignores metadata and emits `performance_expression_omitted` when any rows present.
- Simulated-device tests (MIDI 1.0, MPE multi-channel, high-res/UMP packets, fallback).
- `VITE_MIDI_EXPRESSIVE_ENABLED` (default true) documented in `.env.example` and gated in store/panel.
- Docs: extend `docs/midi-live-input.md` (+ short composition-v2 note on `note_performances`); AGENTS entry points if structure changes.
- Thin MidiInputPanel status for transport/MPE/fallback.

### Out of scope
- Full MIDI 2.0 property exchange, profiles, or SysEx device configuration.
- Vendoring a full UMP/WASM stack (ship-subset decode + simulated packets only).
- DAW-grade commit of all CCs into V2 automation lanes.
- Redesign of AI Jam / co-performance Commit semantics (beyond accepting expressive events into the live ring).
- Tone.js pitch/pressure audition from `note_performances` (non-acceptance; do not add a ship task).
- Backend WebSocket/MIDI bridge; server-side UMP.
- Changing SMF to MIDI 2.0 clip formats.
- Loop overdub (still deferred).
- Editing `ROADMAP.md` during implementation.
- New studio tab.

### Architecture decisions (locked)

**1. Capability document (`midi.capability.v1`)**

Session-only (not stored on Composition). Fields (minimum):

| Field | Rule |
|-------|------|
| `web_midi` | `available` \| `unsupported` \| `insecure_context` \| `permission_denied` |
| `transport` | `midi1_bytes` \| `ump_experimental` \| `none` |
| `mpe` | `{ eligible: boolean, zone?: { master_channel: 1..16, member_channel_low, member_channel_high }, source: 'default'\|'user'\|'device_hint' }` |
| `high_res_velocity` | `true` only when transport/adapter can supply >7-bit attack velocity |
| `reason_codes[]` | stable codes for UI (`ump_api_absent`, `mpe_zone_default`, …) |

Probe Web MIDI support as today. UMP/`high_res_velocity` default **false** until an adapter reports otherwise. Never throw when UMP absent.

**2. Expressive events (`midi.expressive.event.v1`)**

Transport-agnostic kinds (closed set for ship):

- `note_on` / `note_off` — `note` 0..127, `velocity_u16` 0..65535 (MIDI 1.0 adapter sets `velocity_u16 = midi7 << 9` or equivalent locked mapping), `channel`, optional `group`
- `pitch_bend` — normalized `bend` in `[-1, 1]` or cents delta; channel (MPE member) or note-key when already folded
- `pressure` — `poly` (note) or `channel`; normalized `0..1`
- `control_change` — controller number + value (`value_u32` normalized from 7-bit or 32-bit); sustain still recognized at CC64
- `ignored` — with reason

Locked velocity mappings (Task 1 freezes; later tasks must not invent alternate math):

| Path | Rule |
|------|------|
| MIDI 1.0 promote | `velocity_u16 = clamp(midi7, 0, 127) << 9` (max promoted value **65024**, not 65535) |
| Commit degrade | `midi7 = clamp(round(velocity_u16 / 512), 1, 127)` for note-on attacks; `velocity_u16 == 0` stays note-off path |
| UMP / true 16-bit | Task 1 locks a single ship-subset map into `velocity_u16` such that degrade(midi7) is stable and tested; do not silently reuse `<< 9` for full 16-bit packets without documenting the difference |

**3. Optional V2 metadata (`note_performances`)**

On `CompositionV2Track`, additive optional field:

```text
note_performances: list[{
  event_id: str,                 # must resolve to an event on this track
  velocity_u16?: int,            # 0..65535; when set, event.velocity MUST equal degrade(velocity_u16)
  pitch_cents?: list[{tick_offset: int, cents: int}],  # capped length
  pressure?: list[{tick_offset: int, value: number}],   # 0..1, capped
  controllers?: list[{tick_offset: int, controller: int, value: int}],  # capped; CC64 spans still prefer sustain_pedals
}]
```

Rules:

- Default `[]`; absence identical to today’s documents.
- `extra=forbid` remains; field is explicit.
- Orphan `event_id` → validation error on persist/validate; commit path must not create orphans; **editor delete/cut must prune** (Task 6).
- **`tick_offset` is relative to the referenced event’s `start_tick`** (not absolute composition tick). Quantize/move of the note keeps curves valid without rewriting points; absolute timeline is `event.start_tick + tick_offset`.
- Caps (defaults, Task 2 locks numbers): ≤ 32 points per curve, ≤ 8 controller ids per note, max notes with performance metadata per take = take note count.
- Playable source remains `events[]` only.
- Export SMF: **must** ignore `note_performances` and emit structured projection issue `performance_expression_omitted` when any rows are present (Task 8).
- Tone playback: attack `velocity` only for acceptance; pitch/pressure audition is explicitly **non-acceptance** and must not appear as a ship task.
- Import / LLM generate / fake fixtures: leave `note_performances` absent or `[]` — never invent performance metadata from file MIDI or prompts.

**4. MPE zone (defaults)**

- Default zone: master channel **1** (0-based ch 0), members **2–16** (ch 1–15), matching common MPE “lower zone” practice — confirm in Task 1 docs table.
- User toggle in MidiInputPanel: **MPE mapping** off (legacy: channel is only note-key namespace) / on (default off to preserve today’s single-channel keyboards).
- When on: pitch bend + channel pressure on a member channel attach to the open note on that channel; master-channel CCs (e.g. CC64) apply globally as today.

**5. Fallback order**

1. If Web MIDI unsupported → QWERTY / existing unsupported UI (unchanged).
2. If enabled but UMP API absent → `transport=midi1_bytes`.
3. If MPE toggle off → legacy channel voice parse (expanded kinds still recorded into expressive events).
4. If expressive metadata commit fails validation → still commit canonical notes; drop metadata with WARN code `performance_metadata_dropped` (never fail the take solely for optional metadata).

**6. Logging**

| Level | What |
|-------|------|
| DEBUG | probe fields, adapter chosen, event kind counts, MPE zone, point caps, degrade summaries |
| INFO | capture start/stop note/pedal/performance counts; fallback transitions with reason codes |
| WARN | metadata dropped; UMP init failed; orphan skip; curve truncated |
| ERROR | unexpected adapter exceptions (sanitized) |

Never: raw byte/UMP dumps, full take JSON, composition event arrays at INFO.

**7. Feature flags**

- No new backend env required.
- `VITE_MIDI_EXPRESSIVE_ENABLED` default `true` — document in `.env.example`; when falsy, force legacy parse/commit path (MIDI 1.0 note/CC64 only) for bisect. Store/panel must read the same gate (Task 7).

**8. Editor reconcile**

Any path that removes `tracks[].events[].id` values (at least `deleteNotes` / cut) must drop matching `note_performances` rows on that track, mirroring motif occurrence reconcile. Do not leave orphans for autosave validation to discover.

## Commit Plan
- **Commit 1** (after tasks 1–2): `docs(midi): lock expressive MIDI capability and degrade contracts`
- **Commit 2** (after tasks 3–4): `feat(midi): add expressive event adapters and capture buffer`
- **Commit 3** (after tasks 5–7): `feat(composition): note_performances schema, editor prune, and MIDI commit`
- **Commit 4** (after tasks 8–9): `test(midi): simulated expressive devices, SMF omission, and docs`

## Tasks

### Phase 1: Capability matrix and contracts
- [x] Task 1: Inspect and document current browser/platform MIDI capabilities relevant to this repo (Web MIDI 1.0 `requestMIDIAccess`, absence/presence of any UMP/MIDI 2.0 entry points in Chromium/Firefox/Safari as of plan date, MPE-over-MIDI1 practice). Produce a locked support matrix in `docs/midi-live-input.md` (or a stub section) and freeze: default transport `midi1_bytes`; UMP experimental opt-in via probe; MPE optional user toggle default **off**. **Also freeze** Architecture decision 2 velocity tables (MIDI1 `<< 9` / `/ 512` degrade; separate UMP→`velocity_u16` ship-subset map), the **UMP packet kinds** allowed in ship-subset decode (explicit closed list — no full UMP/WASM stack), and that performance curve `tick_offset` values are **relative to `event.start_tick`**. Record what is simulated-only in CI.

  LOGGING: n/a for the markdown table; any probe harness DEBUG prints reason codes only (no dumps).

  Files: `docs/midi-live-input.md` (matrix stub ok), optional `frontend/src/utils/midiExpressive/capability.fixture.json`

- [x] Task 2: Freeze document shapes: `midi.capability.v1`, `midi.expressive.event.v1`, `midi.performance.take.v1`, and V2 `note_performances` field rules + caps + degrade invariants from Architecture decisions 1–3 (including relative `tick_offset`). Prefer JSDoc typedefs / small pure modules under `frontend/src/utils/midiExpressive/` mirroring existing midi utils; export named constants for velocity promote/degrade used by later tasks. Note `VITE_MIDI_EXPRESSIVE_ENABLED` default in constants comments; `.env.example` line lands with Task 7. Backend Pydantic for `note_performances` lands in Task 5. Short additive subsection in `docs/composition-v2.md`. (depends on 1)

  LOGGING: n/a (contracts/docs).

  Files: `frontend/src/utils/midiExpressive/*.js` (schemas/constants), `docs/midi-live-input.md`, `docs/composition-v2.md`

<!-- Commit checkpoint: tasks 1-2 -->

### Phase 2: Adapters and capture
- [x] Task 3: Implement transport adapters + capability probe extensions **within Task 1 locks**. Extend `probeWebMidiSupport` / post-enable snapshot to emit `midi.capability.v1`. Implement `midi1Bytes` adapter (expand `midiInputMessages` or wrap it) for note on/off, pitch bend, channel/poly pressure, control change (not only CC64). Implement MPE zone fold (toggleable; channels 0-based in adapters). Implement `UmpTransport` interface + pure JS decode **only** for the Task 1 ship-subset packet kinds; activate only when capability says so; provide simulated packet helpers. **Forbid** vendoring a full UMP/WASM stack. Honor `VITE_MIDI_EXPRESSIVE_ENABLED` at the adapter entry (when false → legacy note/CC64 kinds only). Unit-test each adapter with simulated device streams (no hardware). End-to-end capture→commit journeys stay in Task 8. (depends on 1, 2)

  LOGGING: DEBUG probe + adapter selection; INFO once per enable with `{transport, mpeEligible, highResVelocity}`; WARN `ump_api_absent` / `expressive_disabled`.

  Files: `frontend/src/utils/midiInputSupport.js`, `frontend/src/utils/midiInputMessages.js`, `frontend/src/utils/midiExpressive/*`, `frontend/src/utils/midiInputAccess.js` (thin wire), `*.test.js`

- [x] Task 4: Upgrade `midiPerformanceCapture` to consume expressive events (keep `injectMessage(bytes)` as MIDI 1.0 convenience that routes through the adapter). Retain per-note open state rich enough for pitch/pressure/controller samples with **relative** `tick_offset` on finalize; finalize `midi.performance.take.v1` summary including degraded velocity 1–127 per closed note. Thin-wire `liveMidiStream` to accept the same expressive events into the ring (counts + recent features only — no Jam Commit redesign). (depends on 3)

  LOGGING: DEBUG open/close with kind counts; INFO finalize `{noteCount, performanceNoteCount, pedalCount, ignoredCount}`.

  Files: `frontend/src/utils/midiPerformanceCapture.js`, `frontend/src/utils/midiPerformanceCapture.test.js`, `frontend/src/utils/liveMidiStream.js`, `frontend/src/utils/liveMidiStream.test.js`

<!-- Commit checkpoint: tasks 3-4 -->

### Phase 3: Schema, editor prune, UI
- [x] Task 5: Extend `CompositionV2Track` with optional `note_performances` (Pydantic `extra=forbid`). Add Composition-level validation mirroring motifs (`event_id` resolve on the same track + velocity degrade invariant + caps + relative `tick_offset` bounds). Mirror in frontend `musicJsonValidation.js`. Update `midiTakeApply` to: (a) always write canonical notes + sustain as today; (b) attach `note_performances` when expressive samples exist; (c) on metadata validation failure, WARN and commit notes only. Ensure MIDI import, LLM generate/assemble, and fake fixtures leave `note_performances` absent or `[]` — never invent from file MIDI. Fixture + backend tests for accept/reject/orphan/degrade mismatch. (depends on 2, 4)

  LOGGING: INFO commit `{noteCount, performanceCount}`; WARN `performance_metadata_dropped` with reason code; never log curves at INFO.

  Files: `backend/app/composition_schemas.py`, `frontend/src/utils/midiTakeApply.js`, `frontend/src/utils/musicJsonValidation.js`, `backend/tests/test_composition_v2_note_performances.py` (new), `frontend/src/utils/midiTakeApply.test.js`, touch import/generate paths only if they construct tracks without defaults

- [x] Task 6: Prune `note_performances` whenever editor paths remove event ids — at least `deleteNotes` / cut in `compositionEditorOperations.js`, mirroring `applyMotifReconciliation`. Unit-test: delete a note with performance metadata → row gone; unrelated notes keep metadata; composition still validates. (depends on 5)

  LOGGING: DEBUG prune counts `{removedPerformanceCount}`; never log curve payloads.

  Files: `frontend/src/utils/compositionEditorOperations.js`, `frontend/src/utils/compositionEditorOperations.test.js` (extend), optional small `pruneNotePerformances.js` helper under `midiExpressive/` or editor utils

- [x] Task 7: Thin MidiInputPanel / store wiring: show capability transport + MPE toggle + expressive enabled state; **persist MPE mapping preference in `midiInput:v1`** (non-secret; default **off**); document and honor `VITE_MIDI_EXPRESSIVE_ENABLED` in `.env.example` + store/panel gate (falsy → legacy path). Enable MIDI stays user-gesture-only; QWERTY still produces ordinary takes; store commit uses upgraded take apply. Extend `musicStore.midiInput.test.js` with simulated expressive device. (depends on 3, 5, 6)

  LOGGING: DEBUG UI toggle / preference writes; reuse existing `midiInput` / `midiCapture` loggers.

  Files: `frontend/src/components/MidiInputPanel.jsx`, `frontend/src/store/musicStore.js` (MIDI slice only), `frontend/src/utils/midiInputAccess.js` (pref read/write), `.env.example`, `frontend/src/store/musicStore.midiInput.test.js`

<!-- Commit checkpoint: tasks 5-7 -->

### Phase 4: Simulated devices, export honesty, docs
- [x] Task 8: Add simulated-device **journey** tests (capture→commit→validate), distinct from Task 3 adapter units: (1) legacy MIDI 1.0 melody + CC64 unchanged; (2) MPE-like multi-channel notes with per-channel pitch bend/pressure → metadata + degraded velocity; (3) high-res / simulated UMP note-on → `velocity_u16` preserved and `velocity` degraded; (4) expressive disabled / UMP absent → graceful MIDI 1.0 path; (5) metadata strip still validates as composition.v2; (6) editor delete prunes performance rows. **Required** export unit: SMF ignores `note_performances` and reports `performance_expression_omitted` when any present. Prefer colocated `*.simulatedMidi.test.js` style from `liveJam.simulatedMidi.test.js`. (depends on 3, 5, 6, 7)

  LOGGING: tests assert reason codes; no dump fixtures at INFO.

  Files: `frontend/src/utils/midiExpressive/*.simulatedMidi.test.js` (new), `backend/app/services/composition_midi.py` (issue emit), `backend/tests/test_composition_midi_performance_omitted.py` (new)

- [x] Task 9: Complete docs — support matrix, expressive model, degrade rules (including relative `tick_offset`), MPE toggle defaults + `midiInput:v1` pref, UMP experimental ship-subset, `VITE_MIDI_EXPRESSIVE_ENABLED`, privacy (`sysex: false`), logging redaction, SMF omission issue; link from `docs/composition-v2.md` and `docs/co-performance.md` as needed; update `AGENTS.md` entry points for `midiExpressive/` if added. (depends on 1–8)

  LOGGING: n/a (docs).

  Files: `docs/midi-live-input.md`, `docs/composition-v2.md`, `docs/co-performance.md`, `AGENTS.md`

<!-- Commit checkpoint: tasks 8-9 -->

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer extending `frontend/src/utils/midiExpressive/` + thin wires into existing midi/capture/take modules over a parallel capture stack.
3. Required `CompositionV2NoteEvent` fields stay unchanged; optional metadata is additive and ignorable.
4. Never enable SysEx; never log raw MIDI/UMP payloads at INFO.
5. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
6. Simulated devices are the acceptance path in CI — do not require physical MPE/MIDI 2.0 hardware.
7. Do not redesign AI Jam Commit or invent `composition.v5`.
8. Keep QWERTY / unsupported-browser paths green.
9. Do not vendor a full UMP/WASM stack; ship-subset only as frozen in Task 1.
10. Do not treat Tone pitch/pressure audition as acceptance work in this milestone.
11. Editor delete/cut must prune `note_performances` before autosave validation.
