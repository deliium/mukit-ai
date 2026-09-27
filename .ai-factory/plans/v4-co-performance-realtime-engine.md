# Implementation Plan: V4 Co-Performance Real-Time Music Engine

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-23
Improved: 2026-09-23 (`/aif-improve` — shared PlaybackControls engine access, LivePerformanceError mapper, Transport-synced MIDI stream + midiPhase exclusion, FE chord-tone helper, predict DTO size caps + single in-flight, Worker deferred v1, Task 9/10 UI vs docs split, task deps)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **low-latency real-time co-performance infrastructure** — live transport, beat/bar clock, incoming MIDI stream, prediction horizon, scheduled accompaniment buffer, active harmony context, latency instrumentation, and degradation — so AI-assisted live performance can run **without** replacing deterministic editor/playback, **without** writing every transient MIDI event to persistence, and **without** blocking Tone.js transport on heavy LLM calls
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v2-expressive-v2-playback-and-mixing.md` (**shipped** — Tone.js Transport ownership, relocate, mixer scopes, seconds schedule)
  - `.ai-factory/plans/v3-midi-keyboard-live-midi-input.md` (**shipped** — Web MIDI / QWERTY → session take → one V2 commit; metronome ephemeral; **no** backend MIDI WebSocket)
  - `.ai-factory/plans/v4-multi-agent-music-architecture.md` (**shipped** — agents above runtime; preview → Apply CAS; never `composition.v4`)
  - `.ai-factory/plans/v3-hybrid-llm-symbolic-composition-pipeline.md` (**shipped** — `fake:symbolic-tiny` / MT note engines; heavy generation off hot path pattern)
  - `.ai-factory/plans/v4-reference-conditioned-generation-editing.md` (**related ACP / soft conditioning** — preserve/borrow/regenerate for offline generate/develop/edit; **not** wired into RT predict v1; never event arrays / never hot path)
  - `.ai-factory/plans/v2-interactive-harmony-reharmonization.md` (**shipped** — V2 harmony tick spans; reharm preview; BE `_harmony_at` + `parse_chord_symbol`)

## Roadmap Linkage
Milestone: "V4 multi-agent music architecture"
Rationale: Co-performance is a V4-era capability that depends on stable Tone.js transport, live MIDI capture, and preview-first AI boundaries already shipped under multi-agent / MIDI / playback milestones. All current ROADMAP items are checked — docs/implement should add a dedicated unchecked milestone (e.g. **V4 co-performance real-time engine**) via `/aif-roadmap` or the docs checkpoint.

## Goal

Create infrastructure for **AI-assisted live co-performance** while keeping the existing deterministic editor, playback, and persistence architecture intact.

The app must maintain **stable MIDI/Tone playback and transport** while:

1. receiving a continuous live MIDI stream (session-only);
2. maintaining an explicit beat/bar clock aligned to live transport;
3. deriving **active harmony context** from canonical V2 harmony spans at the playhead;
4. requesting / generating accompaniment for a **prediction horizon** ahead of now;
5. **scheduling** generated accompaniment into an ephemeral buffer on the same Transport;
6. **degrading** gracefully when AI is late (continue pattern — never silence transport);
7. measuring latency at MIDI → analysis → generation → schedule boundaries;
8. exposing clean **start / stop / cancel** lifecycle.

```text
composition.v2 (canonical score — playable source unchanged)
        │
        ├─→ Tone playback schedule (working / existing audition paths)
        │     via shared createPlaybackEngine in PlaybackControls
        │
Live MIDI / QWERTY ──→ incoming MIDI stream (ephemeral ring; Transport-synced ticks)
        │                      │
        │                      ├─→ optional monitor voices (session)
        │                      └─→ analysis / features (main/rAF throttled; Worker deferred)
        │
Live transport + beat/bar clock (Tone.Transport + timeline)
        │
Active harmony context @ tick (read-only from V2 harmony[])
        │
Prediction horizon (N bars / M ms ahead)
        │
Accompaniment generator
  ├─ local/fake pattern engine (always-on degradation + CI; FE chord-tone helper)
  └─ async AI / symbolic fill (HTTP; optional WS only after measurement gate)
        │
Scheduled accompaniment buffer (ephemeral events + owned Transport IDs)
        │
Tone.Transport.schedule (ahead of playback; same engine instance)
        │
Optional explicit Commit → composition.v2 (one transaction; never per-note persist)
```

**Terminology lock:**

| Term | Meaning |
|------|---------|
| **Live transport** | Session-owned Tone.js `Transport` clock for co-performance; shares ownership rules with existing playback engine (owned callback IDs only; no global `Transport.cancel()`) |
| **Beat/bar clock** | Derived playhead: `{ seconds, tick, bar, beatInBar, tickInBar }` from composition timeline + Transport position |
| **Incoming MIDI stream** | Continuous session ring of parsed note/CC messages with **Transport-synced** timestamps; **not** the record-take-commit path (mutually exclusive with `midiPhase` capture) |
| **Prediction horizon** | How far ahead of the playhead the system must keep accompaniment scheduled (bars and/or ms) |
| **Scheduled accompaniment buffer** | Ephemeral note events + schedule handles for future accompaniment; never autosaved; never revision snapshot |
| **Active harmony context** | Chord/symbol (+ optional span bounds) at playhead tick from `composition.v2.harmony[]`; metadata only — never invents audible notes by itself |
| **Live ephemeral events** | Stream notes, monitor notes, accompaniment buffer events, metronome clicks |
| **Canonical Composition events** | Only `tracks[].events[]` after explicit commit / Apply |
| **Degradation** | Local pattern continuation when AI miss deadline; transport keeps running |
| **Shared playback engine** | The single `createPlaybackEngine` instance owned by `PlaybackControls` — live scheduler must attach to it, never spawn a second Transport owner |

**Non-goals (v1):** full DAW punch/overdub lanes; MPE; backend MIDI device bridge; replacing FluidSynth export; inventing `composition.v4`; auto-committing every MIDI note; running LLM inside AudioWorklet; requiring WebSocket for acceptance; Web Worker analysis; wiring `reference.conditioning.policy.v1` into predict.

## Approach Evaluation (locked)

### Part A — Clock & transport ownership

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Second independent `setInterval` clock** | Simple | Drifts from Tone audio; dual seek bugs | **Reject** |
| **B. Own Tone.Transport via existing `createPlaybackEngine` ownership** | Matches shipped relocate/owned-ID rules | Must extend engine carefully | **Accepted** |
| **C. AudioWorklet-only clock** | Lowest jitter | Hard testability; breaks fake-Tone unit tests | **Reject** as sole clock; optional later for measurements only |

**Locked:** Co-performance uses the **same Transport ownership model** as `tonePlaybackEngine.js` (operation/session tokens, owned schedule IDs, relocate path). Beat/bar clock is a thin derived view over Transport seconds + `compileTimeline` / `secondsToPlaybackPosition`. Live scheduling attaches to the **shared** engine from `PlaybackControls` — never a second `createPlaybackEngine` for co-performance.

### Part B — Where accompaniment is generated

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Heavy LLM on every beat (sync HTTP on main thread)** | “Smart” | Blocks UI/audio; misses horizon | **Reject** |
| **B. Local deterministic / fake pattern engine on hot path + async AI fill** | Meets AC; degradable; CI-friendly | AI quality optional | **Accepted** |
| **C. Server-only generation (no local fallback)** | Centralized | Network SPOF; silence on lag | **Reject** |
| **D. Music Transformer / LLM as sole real-time voice** | Cool | Latency unpredictable; GPU/API | **Reject** for v1 hot path — allowed only as **async buffer filler** behind horizon |

**Locked:** Hot path = local/fake accompaniment from active harmony + recent MIDI features (FE chord-tone helper for triad/seventh PCs). Async AI (HTTP today; optional WS chunking later) **prefetches** into the buffer; misses → degradation, never stop Transport. BE fake predict may reuse `composition_tonality.parse_chord_symbol`.

### Part C — Frontend ↔ backend communication

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. WebSocket for raw MIDI to server** | — | Rejected by MIDI plan; Docker/host complexity; browser-first | **Reject** (reaffirm) |
| **B. WebSocket for accompaniment note chunks** | Streaming fill; lower TTFT than huge HTTP bodies | Ops + reconnect; not needed if horizon HTTP fits | **Deferred optional** — design interface now; ship only if measured HTTP p95 > horizon budget |
| **C. HTTP request/response prefetch (`POST /live/accompaniment/predict`)** | Fits existing FastAPI; fake mode easy | Chunking awkward | **Accepted for v1 AI path** |
| **D. Pure local browser (no backend)** | Lowest latency | No shared fake CI with backend agents; limited AI | **Accepted for degradation + CI core**; AI optional |
| **E. Web Worker for analysis / feature extract** | Protects main/audio thread | No Workers in repo today; high first-cost; cannot schedule Tone from worker | **Deferred for v1** — analysis on main/rAF with throttle; revisit only if Task 8 measures jank |

**Measured-needs gate (Task 8):** Instrument MIDI→schedule path under fake load; if async AI HTTP round-trip p95 routinely exceeds `horizon_ms - schedule_slack_ms`, enable WS chunk transport behind the same predict DTO (identical event schema). Do **not** introduce WS in Task 1 without numbers.

### Part D — Persistence boundary

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Write every note-on to SQLite / autosave** | — | Violates req #4; undo spam | **Reject** |
| **B. Session-only stream + buffer; explicit Commit/Stop→V2** | Matches MIDI take model | User must opt in to keep | **Accepted** |
| **C. Auto-commit every N bars** | Convenient | Surprising mutations | **Reject** for v1 |

**Locked:** Same axiom as MIDI live input: **no** project/revision write until explicit user Commit (may reuse/extend `midi-record` transaction or add `co-performance-commit`). Commit requires a user-selected destination track (never invent notes from harmony alone).

### Part E — Playback layering

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Switch `playbackSource` to a new kind that stops working audition** | Fits mutual-exclusive model | Breaks “stable playback while receiving MIDI” | **Reject** as sole mode |
| **B. Additive live layer on working Transport** (V2 schedule + ephemeral accompaniment IDs + optional MIDI monitor) | Matches AC | Must not leak into mixer persistence | **Accepted** |
| **C. Separate AudioContext** | Isolation | Dual clocks; complexity | **Reject** |

**Locked:** Co-performance is an **additive session layer** over the working (or explicitly chosen) audition composition via the shared engine. Switching mutual-exclusive AI preview sources / `invalidateSource` **cancels** co-performance (document). Mixer: ephemeral `live` scope controls only; never written to V2.

### Part F — Active harmony

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Invent notes from harmony for “playback” of score** | — | Forbidden by RULES | **Reject** |
| **B. Read-only span lookup @ tick → soft/hard context for generator** | Correct | Empty harmony → degrade to MIDI-inferred or last chord | **Accepted** |
| **C. Run full `/analysis/composition` every beat** | Rich | Too heavy | **Reject** on hot path |

**Locked:** `activeHarmonyAtTick(composition, tick)` pure helper (FE + mirrored BE for predict). Empty/missing/unparseable spans → warning code + degradation ladder without stopping.

### Part G — Domain → HTTP errors (predict)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Raise bare HTTPException in router** | Fast | Inconsistent with project | **Reject** |
| **B. `LivePerformanceError` + `map_live_performance_error_to_http`** | Matches profiles/critique/agents | Small schema work | **Accepted** |

**Locked error codes (non-exhaustive):** `live_horizon_invalid`, `live_features_too_large`, `live_session_context_invalid`, `live_predict_timeout`, `live_events_forbidden_in_request` → 422; provider/fake internal → 502/503 as appropriate. Never return raw event dumps in error detail.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Tone Transport / schedule | `frontend/src/utils/tonePlaybackEngine.js` — owned IDs, relocate, prepare/start/stop, no global cancel; instance created in `PlaybackControls.jsx` | Shared engine handle for live scheduler |
| Position / bar | `playbackPosition.js`, `compositionTimeline.js`; engine `getPlaybackPosition()` | Beat/bar clock derivation |
| Playback sources | `playbackSource.js` mutual-exclusive audition | Do **not** force exclusive switch for live layer; cancel live on source invalidate |
| MIDI stream building blocks | `midiInputAccess.js`, `midiInputMessages.js`, `midiPerformanceCapture.js` (wall-clock — **do not copy for live ticks**), `MidiInputPanel.jsx`, `midiPhase` state machine | Continuous stream adapter; exclusive with capture phases |
| Metronome | `midiMetronome.js` ephemeral clicks | Optional live click; never V2 |
| Take → V2 | `midiTakeApply.js` + `commitCompositionTransaction` | Pattern for optional Commit of accompaniment and/or performance |
| Harmony spans | V2 `harmony[]`; FE `compositionHarmony.js`; BE `_harmony_at` / `parse_chord_symbol` | Active harmony lookup + BE fake tones |
| Domain→HTTP mappers | `map_critique_error_to_http`, `map_composer_profile_error_to_http`, … | Mirror for `LivePerformanceError` |
| Fake symbolic | `fake_symbolic_composer.py`, `fake:symbolic-tiny` | Optional async filler; **not** hot-path sole engine; **no** new `AiOperation` required for fake predict |
| Latency logging patterns | embeddings / revision_loop `latency_ms`; FE `performance.now` in store spots | Unify into live latency marks |
| Docs axioms | `docs/browser-playback.md`, `docs/midi-live-input.md` | Extend + new `docs/co-performance.md` |
| Tests | `createFakeTone()`, MIDI unit fakes, Playwright midi/playback | Performance + lifecycle tests |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| Explicit live session state machine | start / stop / cancel / degrade codes; exclusive with `midiPhase` capture |
| Transport-synced MIDI stream | Ring buffer; ticks from live clock — **not** wall-clock root-tempo capture |
| Shared engine access from PlaybackControls | Session-scoped handle; cancel on `invalidateSource` |
| Prediction horizon + accompaniment buffer | Ahead-of-time schedule; owned live IDs separate from V2 schedule IDs |
| FE chord-tone helper + local pattern / degradation | Continue harmonic pattern when AI late |
| Latency instrumentation surface | MIDI in → analysis → generation → schedule |
| Non-blocking AI path + domain error mapper | HTTP predict + cancel; size caps; max 1 in-flight; never await on Transport tick |
| Performance tests + HTTP vs local evaluation | Fake clock + injected MIDI + horizon fill under budget |
| UI/Commit vs docs split | Panel + Commit (Task 9); docs/ROADMAP/AGENTS (Task 10) |

### Coupling risks to avoid

1. Writing stream/buffer events into `editedMusicJson` without Commit.
2. Calling `Tone.Transport.cancel()` globally (kills metronome / other owned IDs).
3. Awaiting LLM inside schedule callbacks or MIDI handlers.
4. Treating harmony metadata as playable score notes.
5. Requiring WebSocket or MIDI hardware for CI / Playwright.
6. Spawning a second `createPlaybackEngine` / Transport owner for live.
7. Using wall-clock `ticksFromElapsedMs` for co-performance stream timing (tempo-map drift).
8. Arming co-performance while `midiPhase` is `RECORDING` / `COUNTING_IN` / armed capture.
9. Logging MIDI dumps, event arrays, or full compositions at INFO.
10. Persisting live mixer / horizon / latency panels into project revisions.
11. Wiring `reference.conditioning.policy.v1` or multi-agent spine onto the live hot path.
12. Introducing the repo’s first Web Worker in this plan without a measured jank need.

## Scope And Decisions

### In scope
- Live session module (FE utils + thin store slice + panel).
- Shared playback engine access for live scheduling.
- Beat/bar clock derived from Transport + timeline.
- Incoming MIDI stream ring (Transport-synced; session-only) with start/stop/cancel.
- Mutual exclusion with MIDI record-take phases.
- Active harmony context helper + empty-harmony degradation.
- Thin FE chord-tone helper for local pattern engine.
- Prediction horizon config (`bars` + `ms` caps via env/UI bounded).
- Scheduled accompaniment buffer + owned live Transport IDs on shared engine.
- Local/fake pattern engine as default hot path + degradation.
- Optional async `POST /live/accompaniment/predict` (fake mode) with domain error mapper, size caps, single in-flight.
- Latency instrumentation (`live.latency.v1` marks; DEBUG/INFO aggregates only).
- Clean lifecycle: start → running → degrade? → stop | cancel.
- Unit + performance-style tests; docs; roadmap note.
- Logging via existing `LOG_LEVEL` / `VITE_LOG_LEVEL` namespaces (`liveTransport`, `liveMidi`, `liveAccompaniment`).

### Out of scope (v1)
- Backend Web MIDI bridge / OS devices in Docker.
- Mandatory WebSocket (interface stub OK; enable only after measurement gate).
- Web Worker analysis (deferred until measured jank).
- Auto-commit / autosave of live layers.
- Full multi-agent spine on the audio thread.
- `reference.conditioning.policy.v1` on predict (ACP plan remains offline generate/develop/edit).
- Neural audio / MusicGen as live accompaniment.
- Loop overdub punch lanes (still deferred from MIDI plan).
- Changing Composition V2 schema or Alembic migrations for live state.
- New `AiOperation` solely for fake local predict.
- Replacing editor playback with a new engine.

### Architecture decisions (locked)

**1. Ephemeral vs canonical**

```text
Live stream + accompaniment buffer  →  session only
Explicit Commit                     →  composition.v2 tracks[].events[]
Autosave / revisions                →  never see live-only layers
```

**2. Hot path vs cold path**

| Path | Budget mindset | Work allowed |
|------|----------------|--------------|
| Hot (Transport / MIDI handler) | sub-ms–few-ms | parse MIDI, clock read, buffer peek, schedule local pattern notes, latency mark |
| Warm (rAF, throttled) | tens of ms | feature summarize, harmony lookup cache refresh |
| Cold (async fetch) | hundreds of ms–seconds | LLM / MT predict; result merges into buffer if still relevant |

**3. Cancel semantics**
- **Stop:** freeze clock optional; clear future accompaniment IDs; keep last stream for Commit/Discard; leave V2 untouched.
- **Cancel:** discard stream + buffer; clear owned live IDs; restore pre-session transport policy (same as MIDI discard).
- **In-flight predict:** AbortController / ignore stale `request_id` by session epoch (mirror playback `operationEpoch`); **max one in-flight** per session.
- **Source invalidate / audition switch:** cancel co-performance automatically.

**4. Degradation strategy (required)**
When `now + slack >= next uncovered horizon tick`:
1. Prefer **continue last accompaniment pattern** transposed/adapted to current active harmony (via FE chord-tone helper);
2. Else **arpeggiate / pad** active chord tones at last known density;
3. Else **hold** last voicing (sustained soft notes) rather than silence;
4. Emit warning code `live_degraded_pattern_continue` (count + reason only);
5. Never stop Transport solely because AI failed.

**5. Schema sketches (non-playable)**

`live.session.v1` (FE store / optional BE echo — **not** persisted in PROJECT_DB):

```text
{
  schema: "live.session.v1",
  session_id: string,
  phase: "idle"|"arming"|"running"|"degraded"|"stopping"|"cancelled",
  transport: { playing: bool, tick: int, bar: int, beat: int },
  horizon: { bars: number, ms: number },
  active_harmony: { symbol: string|null, start_tick: int|null, duration_ticks: int|null },
  latency_ms: { midi_input: number|null, analysis: number|null, generation: number|null, scheduling: number|null },
  degradation: { active: bool, code: string|null, count: int }
}
```

`live.accompaniment.predict.request.v1` (HTTP body — **bounded**):

```text
{
  schema: "live.accompaniment.predict.request.v1",
  session_id: string,
  request_id: string,
  clock: { tick: int, bar: int, beat: int, tempo?: number },
  active_harmony: { symbol: string|null, start_tick?: int, duration_ticks?: int },
  features: { ...bounded numeric/categorical summary only... },  # max bytes via LIVE_PREDICT_MAX_FEATURES_BYTES
  horizon: { bars: number, ms: number }
  # FORBIDDEN: composition, tracks, events[], motifs note payloads, raw MIDI dumps
}
```

`live.accompaniment.chunk.v1` (predict response / buffer insert):

```text
{
  schema: "live.accompaniment.chunk.v1",
  request_id: string,
  session_id: string,
  start_tick: int,
  events: [{ pitch, start_tick, duration_ticks, velocity, track_role? }],  # ephemeral — not V2 ids until Commit
  source: "local_pattern"|"fake"|"symbolic"|"llm",
  generated_at_ms: number,
  latency_ms?: { generation: number }
}
```

Never store chunks in SQLite. Cap event counts (`LIVE_ACCOMP_MAX_EVENTS_PER_CHUNK`) and feature payload size. Reject requests that include event arrays → `live_events_forbidden_in_request`.

**6. Backend surface (minimal)**

| Route | Role |
|-------|------|
| `POST /live/accompaniment/predict` | Optional cold-path fill; bounded request DTO; returns chunk |
| (optional later) `WS /live/accompaniment/stream` | Same DTOs; only if measurement gate fails |

Prefer `routers/live_performance.py` + `services/live_accompaniment_predict.py` + `live_performance_schemas.py` (incl. `LivePerformanceError` + mapper); register with `app.include_router` in `main.py` — do not grow unrelated logic in `main.py`. Fake mode returns deterministic pattern from harmony hash. **No** new `AiOperation` required for fake predict.

**7. Reference conditioning / multi-agent**
Out of RT hot path and **out of predict v1 request**. Offline ACP plan (`.ai-factory/plans/v4-reference-conditioned-generation-editing.md`) remains complementary for generate/develop/edit only. Future soft prefs into predict must stay abstract — **never** event arrays from references.

**8. Shared engine access**
`PlaybackControls` registers the live engine instance (e.g. store callback / module registry with dispose clear). Co-performance start fails closed if no engine. Dispose/unmount clears the handle and cancels live session.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat(live): add co-performance session contracts, clock, and MIDI stream`
- **Commit 2** (after tasks 4–6): `feat(live): schedule accompaniment buffer with degradation and latency marks`
- **Commit 3** (after tasks 7–8): `feat(live): optional predict API and performance gates`
- **Commit 4** (after tasks 9–10): `feat(live): co-performance UI, commit path, and docs`

## Tasks

### Phase 1: Contracts, clock, MIDI stream

- [x] **Task 1: Define live session contracts, settings, error codes, and bounded predict DTOs.**
  Deliverable: Versioned FE modules (+ BE Pydantic) for `live.session.v1`, `live.accompaniment.predict.request.v1`, `live.accompaniment.chunk.v1`, horizon bounds, degradation codes, latency mark keys; **`LivePerformanceError` + `map_live_performance_error_to_http`**; settings `LIVE_*` / `VITE_LIVE_*` (horizon bars 1–2, max chunk events, max features bytes, predict timeout, max in-flight = 1). Explicitly forbid composition/events in predict request schema. Document terminology lock in module headers.
  Files: `frontend/src/utils/liveSessionContracts.js` (+ `.test.js`); `backend/app/live_performance_schemas.py`; `backend/app/live_performance_settings.py`; `.env.example` keys (no secrets).
  LOGGING: DEBUG schema validate pass/fail codes only; never log event arrays. INFO settings snapshot (bounds only) at backend import.
  Dependencies: none.

- [x] **Task 2: Implement transport-synced beat/bar clock and active harmony lookup.**
  Deliverable: Pure helpers `getLiveClock(transportSeconds, composition)` → tick/bar/beat (via `compileTimeline` / `secondsToPlaybackPosition`); `activeHarmonyAtTick(composition, tick)` with empty handling; unit tests with fake timeline + harmony spans. Prefer sampling `engine.getPlaybackPosition()` when an engine is attached — never mutate V2.
  Files: `frontend/src/utils/liveClock.js`, `frontend/src/utils/liveHarmonyContext.js` (+ tests); thin BE twin `services/live_harmony_context.py` for predict validation (may wrap `_harmony_at` pattern).
  LOGGING: DEBUG clock sample throttled (e.g. once/bar) with tick/bar only; WARN `live_harmony_empty` counts.
  Dependencies: Task 1.

- [x] **Task 3: Continuous incoming MIDI stream (Transport-synced) with lifecycle + midiPhase exclusion.**
  Deliverable: Stream adapter over existing `midiInputAccess` / message parse that pushes events into a bounded ring with timestamps from **Task 2 live clock / Transport seconds** — **do not** reuse wall-clock `ticksFromElapsedMs` for co-performance. Start/stop/cancel; no auto-commit. **Mutually exclusive** with MIDI record-take: reject arm/start when `midiPhase` is `ARMED` / `COUNTING_IN` / `RECORDING` / `STOPPING` (and reject MIDI record arm while live session is running). Optional MIDI monitor schedules ephemeral attacks without writing V2.
  Files: `frontend/src/utils/liveMidiStream.js` (+ tests); musicStore live slice + guards next to existing MIDI phase helpers; minimal UI affordances deferred to Task 9 if needed for smoke.
  LOGGING: INFO stream start/stop/cancel + reason codes (incl. exclusion rejects); DEBUG note on/off counts; never MIDI dumps.
  Dependencies: Tasks 1, 2.

### Phase 2: Shared engine, buffer, degradation, latency

- [x] **Task 4: Shared PlaybackControls engine access + scheduled accompaniment buffer.**
  Deliverable: (a) Session-scoped shared engine handle registered from `PlaybackControls.jsx` when `createPlaybackEngine` is created/disposed; (b) `liveAccompanimentScheduler.js` + buffer that schedules/clears **owned live** Transport IDs via that engine — never a second engine; (c) on seek/relocate/pause: clear/rebuild **future** live IDs only; (d) on `invalidateSource` / audible revision / audition switch: **cancel** co-performance; (e) working V2 schedule remains authoritative for score notes. Additive layer must keep transport stable while stream is active.
  Files: `frontend/src/utils/livePlaybackEngineAccess.js`, `frontend/src/utils/liveAccompanimentBuffer.js`, `frontend/src/utils/liveAccompanimentScheduler.js`, `frontend/src/components/PlaybackControls.jsx` (register/clear handle only), minimal hooks in `tonePlaybackEngine.js` only if owned-ID list split is required; tests with `createFakeTone()`.
  LOGGING: INFO schedule fill counts + horizon coverage + engine attach/detach; DEBUG owned live ID clear reasons; ERROR schedule failures without payloads; WARN start without engine (`live_engine_unavailable`).
  Dependencies: Tasks 1, 2, 3.

- [x] **Task 5: FE chord-tone helper + local pattern engine + degradation strategy.**
  Deliverable: Thin FE helper mapping common chord symbols → pitch classes (triad/seventh); unparseable → empty set. Deterministic local generator from active harmony + last pattern state; on horizon underrun apply locked degradation ladder; expose `degradation.active` + codes on session snapshot; never stop Transport on AI absence. BE fake predict may use `parse_chord_symbol` independently.
  Files: `frontend/src/utils/liveChordTones.js`, `frontend/src/utils/livePatternEngine.js` (+ tests); wire into buffer maintain loop (rAF or Transport `scheduleOnce` pump — not `setInterval` drift).
  LOGGING: WARN each degradation activation (code + count); DEBUG unparseable chord counts; INFO recover-from-degraded when coverage restored.
  Dependencies: Task 4.

- [x] **Task 6: Latency instrumentation.**
  Deliverable: Mark/measure helpers for `midi_input`, `analysis`, `generation`, `scheduling` using `performance.now()` (FE) / `time.perf_counter()` (BE predict); session snapshot aggregates (last + optional ewma); unit tests with injected clocks; DEBUG samples gated by `VITE_LOG_LEVEL`; no per-note INFO spam.
  Files: `frontend/src/utils/liveLatency.js` (+ tests); surface on session DTO; BE predict returns `latency_ms.generation` when used.
  LOGGING: DEBUG mark pairs; INFO periodic summary (interval ≥ 1s) with ms only.
  Dependencies: Tasks 3–5.

### Phase 3: Async AI path, gates, UI, docs

- [x] **Task 7: Non-blocking accompaniment predict API (fake-first) + cancel/stale ignore.**
  Deliverable: `POST /live/accompaniment/predict` validating bounded `live.accompaniment.predict.request.v1`; map `LivePerformanceError` → HTTP; fake deterministic chunk (harmony hash / `parse_chord_symbol`); client uses AbortController + session `request_id` / epoch; **at most one in-flight** predict per session; late responses discarded; **never** awaited inside MIDI/Transport callbacks. Register router via `app.include_router` in `main.py`. **No** new `AiOperation` for fake path. Document measurement gate for optional WS (stub comment only unless Task 8 fails the budget).
  Files: `backend/app/routers/live_performance.py`, `backend/app/services/live_accompaniment_predict.py`, `backend/app/services/fake_live_accompaniment.py`, `backend/app/main.py` (include_router only), `frontend/src/api/livePerformanceApi.js`; FE async filler wiring; `backend/tests/test_live_performance_*.py`.
  LOGGING: INFO predict accepted/completed with request_id, latency_ms, event_count; WARN stale discard / size reject codes; never log prompts/MIDI arrays.
  Dependencies: Tasks 1, 4–6.

- [x] **Task 8: Performance tests + communication evaluation record.**
  Deliverable: Automated tests that (1) inject MIDI + advance fake Transport on shared fake engine, (2) assert accompaniment remains scheduled ≥ horizon under local engine, (3) assert transport continues when predict is delayed/aborted, (4) assert no V2 mutation without Commit, (5) assert midiPhase exclusion, (6) record measured budgets in test comments / docs table (HTTP vs local; Worker not required). If HTTP p95 exceeds budget in a documented stress harness, add WS stub **or** explicitly document “HTTP sufficient” with numbers — do not silently skip the evaluation requirement.
  Files: `frontend/src/utils/liveAccompanimentBuffer.perf.test.js` (or colocated performance tests), backend predict latency/size tests; short section reserved for Task 10 docs.
  LOGGING: tests may assert log spies for degradation / exclusion codes; no new production log noise.
  Dependencies: Task 7.

- [x] **Task 9: UI lifecycle + Commit boundary.**
  Deliverable: Start / Stop / Cancel controls; show clock, active harmony, horizon coverage, latency summary, degradation badge; engine-unavailable and midiPhase-exclusion messaging; Commit path that converts selected ephemeral accompaniment and/or stream into **one** V2 transaction onto a **user-selected destination track** (reuse `midiTakeApply` / `commitCompositionTransaction` patterns; action e.g. `co-performance-commit`); Cancel never mutates V2. Playwright smoke optional if fake MIDI harness exists; otherwise unit coverage + manual checklist is AC-sufficient for UI.
  Files: `frontend/src/components/CoPerformancePanel.jsx` (or MidiInputPanel extension), musicStore live UI wiring, commit helper if needed next to `midiTakeApply.js`, component tests as practical.
  LOGGING: INFO lifecycle transitions + commit summary (counts/track id only); never persist latency panels or event arrays.
  Dependencies: Tasks 1–8.

- [x] **Task 10: Docs, AGENTS, ROADMAP milestone.**
  Deliverable: `docs/co-performance.md` (architecture, ephemeral vs canonical, clock, horizon, degradation, latency marks, communication evaluation table, lifecycle, Commit rules, explicit non-goals incl. Worker deferred + no reference-policy on predict); cross-links from `docs/browser-playback.md`, `docs/midi-live-input.md`, `README.md`; `AGENTS.md` entry; unchecked ROADMAP milestone for co-performance; `DESCRIPTION.md` one-liner if appropriate.
  Files: `docs/co-performance.md`, `docs/browser-playback.md`, `docs/midi-live-input.md`, `README.md`, `AGENTS.md`, `.ai-factory/ROADMAP.md`, `.ai-factory/DESCRIPTION.md` as needed.
  LOGGING: N/A for docs; note log namespaces and never-log rules in the docs page.
  Dependencies: Task 9.

<!-- Commit checkpoint: tasks 9–10 → Commit 4 -->

## Acceptance Criteria

1. With co-performance **running**, Tone transport continues stably while live MIDI events arrive into the session stream (no per-note persistence); stream ticks follow Transport/timeline, not wall-clock-only capture.
2. Beat/bar clock tracks Transport position; active harmony updates at span boundaries (or reports empty).
3. Accompaniment for the configured **prediction horizon** stays scheduled ahead of the playhead under the local/fake engine on the **shared** playback engine.
4. When async AI/predict is delayed or cancelled, **degradation** continues a harmonic pattern — audio/transport does not stop solely due to AI latency.
5. Heavy LLM/symbolic predict never blocks MIDI handlers or Transport schedule callbacks (proven by test with artificial delay); at most one in-flight predict.
6. Start / Stop / Cancel lifecycle clears or preserves session state as specified; Cancel does not mutate V2; audition/`invalidateSource` cancels live; MIDI record phases and live arming are mutually exclusive.
7. Latency marks expose MIDI input, analysis, generation, and scheduling timings (aggregates).
8. Communication evaluation recorded: local + HTTP (+ WS only if measured need); Worker not required for v1 AC.
9. Tests + instrumentation + documentation shipped.

## Implementation Notes for `/aif-implement`

- Prefer extending modules over forking a second Tone stack; attach to `PlaybackControls` engine.
- Keep `ai_agents/` free of SQLite; live predict service may call fake helpers but must not import project history stores.
- Respect RULES: only `tracks[].events[]` are playable canonical notes; harmony is context only.
- Verbose logging default; strip payloads; gate with env levels.
- Do **not** implement reference-conditioning policy on predict or UI for this plan — keep RT engine independent of `.ai-factory/plans/v4-reference-conditioned-generation-editing.md`.
- Do **not** introduce Web Workers unless Task 8 produces measured main-thread jank evidence and a follow-on task is explicitly added.

## INFO

`INFO [aif-plan] resolved plan file: .ai-factory/plans/v4-co-performance-realtime-engine.md (format=slug)`
`INFO [aif-plan] git.create_branches=false — plan authored on main; no feature branch created`
`INFO [aif-plan] prefs from config: testing=yes logging=verbose docs=yes roadmap=auto→V4 multi-agent (all milestones checked; new line recommended at docs checkpoint)`
`INFO [aif-improve] applied all refinements 2026-09-23 — tasks 1–10; Worker deferred; shared engine + error mapper + Transport-synced stream locks`
