# Implementation Plan: V4 AI Jam Real-Time Co-Composition

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-23
Improved: 2026-09-23 (`/aif-improve` — split belief vs features DTOs; bounded MIDI ring window; `liveJamContext` cache; multi-role scheduler API; predict store wiring + `createLivePredictAbortController`; `ensureJamRoleTracks`; optional harmony-span Commit; merge snapshot/fallback UX into panel task; fix Task 6/8/9 deps; defer user MIDI monitor voices)
Planning depth: final, ultra-thorough

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)
- Scope: ship **AI Jam product modes** on top of the shipped V4 co-performance real-time engine — user-melody / user-chords role partitions, deterministic live performance analysis with confidence/hysteresis, jam controls (complexity / density / style / instrument set / responsiveness), multi-role local+fake generation into the existing horizon buffer, multi-track Commit into editable `composition.v2`, and graceful fallback when local/runtime predict is unavailable — **without** replacing the co-performance transport/MIDI/buffer stack, **without** per-note persistence, and **without** heavy remote LLM calls on every note
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v4-co-performance-realtime-engine.md` (**shipped** — shared Tone engine, Transport-synced MIDI stream, horizon buffer, local pattern degradation, optional `POST /live/accompaniment/predict`, single-track Commit, `docs/co-performance.md`)
  - `.ai-factory/plans/v3-midi-keyboard-live-midi-input.md` (**shipped** — Web MIDI / QWERTY; exclusive with live phases)
  - `.ai-factory/plans/v2-expressive-v2-playback-and-mixing.md` (**shipped** — Transport ownership)
  - `.ai-factory/plans/v3-hybrid-llm-symbolic-composition-pipeline.md` (**shipped** — `fake:symbolic-tiny` / MT off hot path)
  - `.ai-factory/plans/v2-interactive-harmony-reharmonization.md` (**shipped** — V2 harmony spans; `parse_chord_symbol`)
  - `.ai-factory/plans/v4-multi-agent-music-architecture.md` (**shipped** — agents above runtime; never `composition.v4`)

## Roadmap Linkage
Milestone: "V4 AI Jam real-time co-composition" *(proposed — all current ROADMAP items are checked, including co-performance engine; `/aif-implement` docs checkpoint or `/aif-roadmap` must add this unchecked milestone)*
Rationale: AI Jam is the user-facing co-composition product layer that depends on the shipped real-time engine; it is a distinct milestone from bare transport/horizon infrastructure.

## Goal

Allow the user to improvise on a MIDI keyboard while AI generates **adaptive, role-partitioned accompaniment** synchronized to live transport, then stop and save the jam as an **editable** canonical Composition.

**Initial modes:**

| Mode | User performs | AI generates (ephemeral → Commit) |
|------|---------------|-----------------------------------|
| **User Melody** (`user_melody`) | Melody / lead line | Harmony context (inferred + hysteresis), bass, accompaniment texture |
| **User Chords** (`user_chords`) | Chord / harmonic material | Melody, bass, texture |

```text
composition.v2 (canonical — unchanged until Commit)
        │
Shipped co-performance engine
  Transport + live MIDI stream + horizon buffer + degradation + predict HTTP
        │
AI Jam layer (this plan)
  ├─ jam mode + controls (complexity, density, style, instruments, responsiveness)
  ├─ live performance analysis (pitch activity, beat, key, harmony, phrase)
  │     with confidence + hysteresis (no flip on one ambiguous note)
  ├─ cached musical context (planned_window; hot path reads last snapshot only)
  ├─ multi-role local/fake generators (bass / accomp / melody / texture)
  └─ multi-track Commit → tracks[].events[] (+ optional harmony[] metadata)
```

**Acceptance one-liner:** User improvises a melody for several bars; AI keeps synchronized bass + harmonic accompaniment ahead of the playhead; Stop → Commit yields an editable multi-track `composition.v2`; Cancel leaves V2 untouched.

## Terminology lock (extends co-performance)

| Term | Meaning |
|------|---------|
| **AI Jam session** | Co-performance session with a selected jam mode + jam controls; same lifecycle idle→running→degraded→stop\|cancel |
| **Jam mode** | `user_melody` \| `user_chords` — partitions which roles the user owns vs AI fills |
| **Live performance features** | Bounded **raw** analysis summary: pitch activity, beat position, probable key, probable harmony, phrase boundary hints — **not** a full `composition.analysis.v1` report; **does not** embed post-hysteresis belief |
| **Harmony belief** | Smoothed probable harmony + confidence after hysteresis; lives on **session snapshot** (and drives predict `active_harmony`); generators consume belief, never raw `probable_harmony` alone |
| **Role partition** | Mapping of ephemeral `track_role` (`melody` / `bass` / `harmony` / `accompaniment` / `texture`) to generators and Commit destination tracks |
| **Jam controls** | Session-only: complexity, density, accompaniment style, instrument set, responsiveness — never persisted into revisions as jam state |
| **Cached musical context** (`liveJamContext`) | Rolling planned key/harmony/phrase window derived from belief + V2 spans; refreshed on warm path; hot-path generators read last snapshot only |
| **Multi-track Commit** | One `ai-jam-commit` (or extended `co-performance-commit`) transaction writing user stream + AI roles onto **user-mapped** destination tracks; optional belief→`harmony[]` metadata |

Reuse all co-performance terms (horizon, degradation, shared engine, ephemeral vs canonical) unchanged.

## Non-goals (v1)

- Replacing or forking the co-performance Transport / stream / buffer stack
- Per-note remote LLM / Music Transformer on the hot path
- WebSocket MIDI bridge or mandatory WS predict (keep parent measurement gate)
- Web Worker analysis (still deferred unless measured jank from jam analysis)
- Auto-commit / autosave of jam layers
- Inventing `composition.v4` or playable notes from harmony metadata alone
- Wiring `reference.conditioning.policy.v1` or multi-agent spine onto the jam hot path
- Full DAW punch/overdub lanes / MPE
- Changing Composition V2 schema or Alembic for jam state
- New `AiOperation` solely for fake jam predict
- **User MIDI monitor / audition voices** while jamming (scheduler `onTrigger` remains stub-quality; defer until measured UX gap — out of v1 AC)

## Approach Evaluation (locked)

### Part A — Product layer vs new engine

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New parallel jam engine** | Clean slate | Duplicates Transport/MIDI; violates parent locks | **Reject** |
| **B. Extend co-performance with jam mode + analysis + multi-role generators** | Reuses shipped AC; smallest surface | Must carefully extend Commit | **Accepted** |
| **C. Multi-agent spine on audio thread** | “V4 branded” | Latency; preview/Apply mismatch | **Reject** |

**Locked:** AI Jam is a **product/policy layer** on the shipped engine. Extend contracts, pattern engines, predict features, panel, and Commit — do not spawn a second `createPlaybackEngine`.

### Part B — Live analysis location

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Full `POST /analysis/composition` every beat** | Rich | Too heavy; wrong contract | **Reject** |
| **B. Deterministic FE warm-path feature extractor + optional BE echo in predict features** | Meets AC; CI-friendly | Must stay bounded | **Accepted** |
| **C. Remote LLM key/harmony inference** | Fancy | Latency; instability | **Reject** for v1 |

**Locked:** Pure deterministic analysis over a **bounded** recent MIDI ring window + clock + optional V2 harmony spans. Feature DTO is numeric/categorical only (byte-capped). Reuse BE `parse_chord_symbol` / pitch-class helpers; thin FE mirrors for hot/warm path. Never persist analysis as composition data. Never dump the full ring into predict bodies.

### Part C — Harmony stability (confidence / hysteresis)

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Instant chord = last MIDI pitch set** | Simple | Flips on passing tones | **Reject** |
| **B. Confidence + hold + min dwell + change threshold** | Matches req #6–7 | Tuning needed | **Accepted** |
| **C. Only trust pre-authored V2 harmony[]** | Stable | Breaks empty-harmony / chord-mode improvisation | **Reject** as sole source; **Accepted** as high-priority prior when present |

**Locked degradation ladder for harmony belief:**
1. Prefer **active V2 harmony span** at tick when present and parseable (confidence high).
2. Else **smoothed MIDI-inferred** chord/key with confidence score.
3. Change symbol only when: new candidate confidence ≥ threshold **and** dwell ≥ `min_harmony_dwell_ms` (or beats) **and** (optional) distance from current belief exceeds hysteresis band.
4. Ambiguous single notes → **hold** last belief; emit `jam_harmony_hold` count (not INFO spam).
5. Generators and predict `active_harmony` consume **belief symbol**, never raw per-note guesses.

### Part D — Generation engines per role

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One generic accompaniment pattern for all roles** | Already shipped | Cannot meet mode partitions | **Insufficient alone** |
| **B. Mode-aware multi-role local generators + fake BE predict roles** | Deterministic; degradable | More code | **Accepted** |
| **C. Symbolic MT / LLM as sole voices** | Quality aspirational | Latency SPOF | **Reject** hot path; optional cold fill only behind horizon + unavailable fallback |

**Locked:**
- Hot path: `liveJamRoleEngine` (or extended `livePatternEngine`) emits role-tagged events from **belief** + jam controls + **cached context snapshot**.
- Scheduler: extend `maintainHorizonWithPattern` (or successor `maintainHorizonWithJam`) to accept `belief`, `jam_mode`, `controls`, `role_mask` — today’s single-`harmonySymbol` API is insufficient.
- Cold path: extend fake predict + **store `requestLivePredictFill`** wiring (currently hardcodes density / V2 harmony); still max 1 in-flight; use `createLivePredictAbortController` (`globalThis.AbortController`); never await on Transport.
- Unavailable predict → local roles + degradation; transport never silences.

### Part E — Controls surface

| Control | Semantics (v1) | Persistence |
|---------|----------------|-------------|
| **Complexity** | Pattern vocabulary size / non-chord tone allowance (low/med/high) | Session only |
| **Density** | Note rate / rhythmic subdivision (already partially on pattern engine) | Session only |
| **Accompaniment style** | Enum: `block` / `arp` / `alberti` / `pad` (bounded set) | Session only |
| **Instrument set** | Map roles → GM program / existing track instruments (catalog IDs only when **ensuring** new tracks at Commit — never store catalog ranges on V2 notes) | Session + Commit track metadata only |
| **Responsiveness** | Scales dwell/thresholds + horizon aggressiveness within caps | Session only |

**Locked:** Controls never override Transport ownership, never write to autosave, never appear in revision snapshots except as resulting note events / optional harmony spans after Commit.

### Part F — Commit / editability

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep single-track Commit only** | Already shipped | Mixes melody+bass; hard to edit roles | **Reject** for jam AC |
| **B. Multi-track Commit with role→track map + ensure helper** | Editable parts | Need valid V2 track shape | **Accepted** |
| **C. Auto-create tracks every jam without user map** | Convenient | Surprising score growth | **Reject** as silent default — opt-in `ensure_missing_tracks` |

**Locked:** Commit writes user stream → user-role track and AI role events → mapped tracks in **one** undoable transaction. Pure `ensureJamRoleTracks(composition, roleMap, instrumentSet)` creates valid V2 tracks (`name`, `instrument`, `role`, `midi_program`, `channel`, `events: []`) when opted in — pass `musicJsonValidation`. Optional `commit_harmony_spans` (default **on** for `user_chords`, **off** for `user_melody`) writes belief window into `harmony[]` metadata only — **never** invents playable notes from spans. Cancel discards all ephemeral layers.

### Part G — Fallback when local model/runtime unavailable

| Layer | Behavior |
|-------|----------|
| Shared playback engine missing | Fail closed on Start (`live_engine_unavailable`) — unchanged |
| Predict HTTP / symbolic cold path down | Ignore; hot-path local roles continue; `jam_predict_unavailable` badge; never stop Transport |
| Fake mode | Always available for CI (`LLM_FAKE_MODE` / `LIVE_PREDICT_FORCE_FAKE`) |
| AbortController missing | Skip cold predict via `createLivePredictAbortController` (`live_predict_no_abort`); local roles continue |
| Unparseable harmony | Hold belief / ephemeral soft pad only — never write default pad to V2 without Commit of generator output |

## Audit Summary (current state)

### What already exists (reuse — do not rebuild)

| Area | Path | Jam reuse |
|------|------|-----------|
| Session contracts / phases | `liveSessionContracts.js`, `live_performance_schemas.py` | Extend with jam_mode, controls; tighten features schema |
| Clock / harmony-at-tick | `liveClock.js`, `liveHarmonyContext.js`, `live_harmony_context.py` | Prior for belief; keep empty warnings |
| MIDI stream | `liveMidiStream.js` (`getSnapshot` / `getClosedNotes`) | Add bounded `getRecentEvents`; input to feature extractor |
| Shared engine + buffer | `livePlaybackEngineAccess.js`, `liveAccompanimentBuffer.js`, `liveAccompanimentScheduler.js` | Extend maintain API for multi-role; `track_role` already on events |
| Pattern / degradation | `livePatternEngine.js`, `liveChordTones.js` | Specialize by role + style + density |
| Predict API | `routers/live_performance.py`, `live_accompaniment_predict.py`, `fake_live_accompaniment.py` | Mode-aware fake chunks |
| Predict client | `livePerformanceApi.js` (`createLivePredictAbortController`) | Wire jam body; never bare `AbortController` |
| Store pump | `musicStore.js` `requestLivePredictFill` / `pumpLiveAccompaniment` | Replace hardcoded density/V2 harmony with belief + features |
| Latency | `liveLatency.js` | Mark `analysis` for feature extract |
| UI lifecycle | `CoPerformancePanel.jsx`, musicStore live slice | Host jam mode/controls + belief/fallback badges |
| Single-track Commit | `liveTakeApply.js` / `midiTakeApply.js` | Extend multi-track + ensure + optional harmony |
| Validation | `musicJsonValidation.js` | Gate ensure-track shape |
| Tonality / chords | `composition_tonality.py` | BE predict + tests; FE thin PC helpers |
| Docs | `docs/co-performance.md` | Extend + new `docs/ai-jam.md` |

### Gaps (must build)

| Gap | Notes |
|-----|--------|
| Jam mode enum + role partition policy | user_melody / user_chords ownership matrices |
| Bounded ring window API | `getRecentEvents({ fromTick, maxEvents })` — no full-ring predict dumps |
| Live performance feature extractor | Raw features only (no belief embedded) |
| Harmony belief + hysteresis | Session snapshot; drives generators + predict `active_harmony` |
| `liveJamContext` cache | planned_window + phrase ticks + key belief; warm refresh |
| Jam controls + panel + snapshot UX | Mode, controls, belief/hold/fallback badges (merged former Task 9) |
| Multi-role generators + scheduler API | Extend beyond single `harmonySymbol` maintain |
| Predict DTO + **store wiring** | jam_mode, controls, features, role_mask; AbortController factory |
| `ensureJamRoleTracks` + multi-track Commit | Valid V2 tracks; optional harmony spans |
| Simulated MIDI stream fixtures/tests | Deterministic multi-bar melody + chord streams |
| Docs + ROADMAP milestone | Dedicated AI Jam milestone |

### Coupling risks to avoid

1. Rebuilding Transport / second engine.
2. Awaiting LLM inside MIDI/Transport callbacks.
3. Flipping harmony on every note-on.
4. Writing jam controls or belief state into project revisions (except optional committed `harmony[]` spans).
5. Committing all roles onto one track without an explicit single-track opt-in.
6. Inventing playable notes from harmony metadata without generator→events→Commit.
7. Logging MIDI dumps, full feature histograms at INFO, or event arrays.
8. Requiring hardware MIDI for CI — simulate streams in tests.
9. Calling full `/analysis/composition` on the live path.
10. Wiring reference policy / agents onto predict.
11. Nesting belief inside features DTO (couples Task analysis ↔ hysteresis).
12. Dumping full MIDI ring into predict `features`.
13. Bare `new AbortController()` in store (ESLint `no-undef` — use `createLivePredictAbortController`).
14. Expanding into user MIDI monitor voices without a measured UX gate.

## Scope And Decisions

### In scope
- Jam mode contracts + role partition
- Deterministic live feature analysis + harmony belief (confidence/hysteresis)
- Session-only `liveJamContext` planned window
- Jam controls (complexity, density, style, instrument set, responsiveness)
- Multi-role local generators on hot path; mode-aware fake predict + store wiring on cold path
- Multi-track Commit + ensure-track helper; optional harmony-span Commit; post-jam editability
- Graceful fallback when predict/runtime/AbortController unavailable
- Unit + simulated MIDI stream tests; docs; ROADMAP milestone
- Verbose logging (`liveJam`, reuse `liveMidi` / `liveAccompaniment` / `liveTransport`)

### Out of scope (v1)
- See Non-goals above (incl. user MIDI monitor voices)

### Architecture decisions (locked)

**1. Mode → role matrix**

```text
user_melody:
  user_roles:     [melody]
  ai_roles:       [bass, accompaniment]   # harmony = belief context, not a note track unless style needs pad
  optional_ai:    [texture]               # when complexity ≥ medium

user_chords:
  user_roles:     [harmony]               # user MIDI treated as harmonic material for belief
  ai_roles:       [melody, bass, texture]
```

Harmony **belief** is always metadata for generators; optional pad/texture notes are explicit generator output with `track_role`, never “playback of harmony[]”.

**2. Feature vs belief split (locked)**

`live.performance.features.v1` — **raw analysis only** (nested under predict `features` or replaces loose `LivePredictFeatures` fields):

```text
{
  schema: "live.performance.features.v1",
  pitch_activity: { note_on_rate, pc_histogram_12, register_mean, register_var },
  beat: { tick, bar, beat_in_bar, tick_in_bar },
  probable_key: { tonic_pc, mode, confidence },
  probable_harmony: { symbol, root_pc, quality, confidence },
  phrase: { boundary_likely: bool, bars_since_boundary, confidence }
}
```

Belief is **not** inside features. Session snapshot / optional predict field:

```text
belief: { symbol, confidence, held: bool, reason_code? }
```

Predict sends **belief** as `active_harmony.symbol` (plus optional explicit `belief` object). Tighten BE nested feature model (`extra=forbid` on jam nested objects) while keeping byte cap + forbid-list; do not leave unbounded `extra="allow"` as the only guard.

**3. Hysteresis parameters (settings)**

| Setting | Default (tunable) | Role |
|---------|-------------------|------|
| `LIVE_JAM_HARMONY_CONFIDENCE_MIN` | 0.55 | Min confidence to adopt new symbol |
| `LIVE_JAM_HARMONY_DWELL_MS` | 400 | Min dwell before change |
| `LIVE_JAM_HARMONY_HYSTERESIS` | 0.15 | Extra margin vs current belief |
| `VITE_LIVE_JAM_*` | mirror | FE warm-path |

Responsiveness control scales dwell/thresholds within clamps (high responsiveness → shorter dwell, never below floor).

**4. Cached musical context (`liveJamContext`)**

Warm-path structure (session memory only):

```text
{
  belief_symbol,
  belief_confidence,
  planned_window: [{ start_tick, end_tick, symbol, source: "v2"|"inferred"|"held" }],
  phrase_boundary_ticks: number[],
  key_belief: { tonic_pc, mode, confidence }
}
```

Hot-path generators read last snapshot — no re-analysis inside note handlers beyond O(1) lookups.

**5. Predict request extension + store wiring**

Add optional fields to `live.accompaniment.predict.request.v1` (backward compatible):

- `jam_mode`: `user_melody` | `user_chords`
- `controls`: `{ complexity, density, style, responsiveness }`
- `features`: bounded `live.performance.features.v1`
- `role_mask`: which AI roles to fill this chunk
- `active_harmony`: **belief** symbol/span (not raw V2-only, not pre-hysteresis guess)

`requestLivePredictFill` must populate the above from session state (replace hardcoded `density: 0.5` / V2-only harmony). Abort via `createLivePredictAbortController`.

**6. Commit mapping**

```text
{
  user_track_id,
  role_tracks: { bass?: id, accompaniment?: id, melody?: id, texture?: id },
  ensure_missing_tracks?: bool,
  commit_harmony_spans?: bool   # default on for user_chords, off for user_melody
}
```

`ensureJamRoleTracks` → valid V2 track objects only. One transaction; counts-only logging.

**7. Scheduler maintain API**

Replace/extend single-symbol maintain:

```text
maintainHorizonWithJam({
  playheadTick, belief, jamMode, controls, roleMask, horizon, jamRoleEngine, jamContext
})
```

Role-preserving coverage + degradation ladder unchanged in spirit (continue / arp / hold per role).

## Commit Plan
- **Commit 1** (after tasks 1–4): `feat(jam): add AI Jam contracts, features, belief, and context cache`
- **Commit 2** (after tasks 5–7): `feat(jam): multi-role generators, panel controls, and predict wiring`
- **Commit 3** (after tasks 8–9): `feat(jam): multi-track commit, ensure tracks, and simulated MIDI tests`
- **Commit 4** (after task 10): `docs(jam): AI Jam docs, AGENTS, and roadmap milestone`

## Tasks

### Phase 1: Contracts, analysis, hysteresis, context

- [x] **Task 1: Define AI Jam contracts — modes, role matrix, controls, feature/belief DTOs (split), settings.**
  Deliverable: Versioned extensions to FE `liveSessionContracts.js` (+ optional `liveJamContracts.js`) and BE `live_performance_schemas.py` / `live_performance_settings.py` for `jam_mode` (`user_melody`|`user_chords`), role partition helpers, jam controls enums/clamps, **`live.performance.features.v1` as raw analysis only** (no nested belief), separate belief fields on session snapshot / optional predict `belief`, hysteresis settings (`LIVE_JAM_*` / `VITE_LIVE_JAM_*`), warning codes (`jam_harmony_hold`, `jam_harmony_changed`, `jam_predict_unavailable`, `jam_role_skipped`, `jam_commit_map_incomplete`). Keep forbid-list; tighten nested feature models vs unbounded `LivePredictFeatures.extra="allow"` alone. Document matrix + feature/belief split in module headers. Unit tests for clamps + role partition + schema reject of belief-inside-features mistakes if applicable.
  Files: `frontend/src/utils/liveSessionContracts.js` (+ tests), `frontend/src/utils/liveJamContracts.js` (optional), `backend/app/live_performance_schemas.py`, `backend/app/live_performance_settings.py`, `.env.example`.
  LOGGING: DEBUG validate pass/fail codes; INFO settings snapshot (bounds only) at import; never log histograms at INFO.
  Dependencies: none (builds on shipped co-performance contracts).

- [x] **Task 2: Deterministic live performance feature extractor (warm path) + bounded ring window.**
  Deliverable: (a) Add `getRecentEvents({ fromTick, maxEvents })` (or equivalent) on `liveMidiStream` — analysis **must not** rely on dumping the full ring into predict; (b) pure `livePerformanceFeatures.js` summarizing that window + live clock into `live.performance.features.v1` (pitch activity, beat, probable key via PC-mass scoring without torch, probable harmony templates, phrase IOI/bar silence heuristic); (c) throttle via pump/rAF — not inside every MIDI handler beyond enqueue; (d) mark `analysis` latency. Unit tests with injected note sequences (no Web MIDI). Optional BE twin for predict validation/tests.
  Files: `frontend/src/utils/liveMidiStream.js` (+ tests), `frontend/src/utils/livePerformanceFeatures.js` (+ `.test.js`); optional `backend/app/services/live_performance_features.py`.
  LOGGING: DEBUG analysis mark pairs + window size; WARN on empty ring; never MIDI dumps.
  Dependencies: Task 1.

- [x] **Task 3: Harmony belief with confidence + hysteresis.**
  Deliverable: `updateHarmonyBelief(prev, features, v2HarmonyAtTick, controls.responsiveness, settings)` → `{ symbol, confidence, held, reason_code }`. Prefer V2 span when present; else inferred; apply confidence min, dwell, hysteresis; hold on ambiguous single-note flips. Expose belief on **session snapshot only** (not inside features). Generators + predict must read belief. Unit tests: passing tones must not change symbol; sustained chord tones eventually adopt; V2 span wins.
  Files: `frontend/src/utils/liveHarmonyBelief.js` (+ tests); wire into musicStore warm-path pump (analysis step before generate/predict).
  LOGGING: INFO only on symbol change (code + old/new truncated); DEBUG holds with counts; aggregate `jam_harmony_hold` counts.
  Dependencies: Tasks 1, 2.

- [x] **Task 4: Session-only `liveJamContext` cache (planned window).**
  Deliverable: Module holding `{ belief_symbol, belief_confidence, planned_window[], phrase_boundary_ticks[], key_belief }` refreshed on warm path from belief + V2 spans + features.phrase; hot-path generators/`maintainHorizonWithJam` read **last snapshot only** (O(1)). Clear on Cancel; freeze snapshot available after Stop for Commit harmony spans. Unit tests for window advance / hold source tags (`v2`|`inferred`|`held`).
  Files: `frontend/src/utils/liveJamContext.js` (+ tests); musicStore pump wiring.
  LOGGING: DEBUG context refresh throttled (once/bar); INFO clear/reset reasons; never full window dumps at INFO.
  Dependencies: Task 3.

### Phase 2: Generators, panel, predict wiring

- [x] **Task 5: Multi-role local jam generators + extended scheduler maintain API.**
  Deliverable: `liveJamRoleEngine.js` (or extend `livePatternEngine.js`) emits role-tagged ephemeral events for AI roles only given `jam_mode` + **belief** + controls + **jamContext** snapshot. Styles: block/arp/alberti/pad; density/complexity modulate step / non-chord allowance; instrument set affects register only on hot path. **Extend** `liveAccompanimentScheduler` beyond `maintainHorizonWithPattern({ harmonySymbol, patternEngine })` to `maintainHorizonWithJam({ belief, jamMode, controls, roleMask, jamRoleEngine, jamContext, … })` with role-preserving coverage. Degradation ladder unchanged (continue/arp/hold) but role-aware. Tests with fake clock / fake engine.
  Files: `frontend/src/utils/liveJamRoleEngine.js` (or `livePatternEngine.js`), `liveAccompanimentScheduler.js` (+ tests), musicStore pump call sites.
  LOGGING: INFO role fill counts per maintain; WARN degradation with role; DEBUG style/density apply.
  Dependencies: Tasks 1, 3, 4.

- [x] **Task 6: Jam controls + mode panel UI + session snapshot / fallback badges.**
  Deliverable: Session state for mode + controls; Start requires mode; panel selectors for complexity, density, style, instrument set (per-role destination / GM program), responsiveness; show belief symbol + confidence + hold badge + `jam_predict_unavailable` badge; latency includes analysis; Start fails closed without shared engine; keep Start/Stop/Cancel/Commit. Mutual exclusion with `midiPhase` unchanged. Do **not** persist controls into autosave. (Absorbs former standalone snapshot/UX task.)
  Files: `frontend/src/store/musicStore.js` (live jam fields + snapshot), `frontend/src/components/CoPerformancePanel.jsx` (or `AiJamPanel.jsx` + mount).
  LOGGING: INFO mode/control changes (enums only); INFO fallback transitions; DEBUG snapshot throttle; lifecycle reuse existing live logs.
  Dependencies: Tasks 1, 3, 5.

- [x] **Task 7: Extend predict API + fake engine + store `requestLivePredictFill` wiring; fallback.**
  Deliverable: Accept optional jam fields on `POST /live/accompaniment/predict`; fake chunk emits multi-role events from belief/features/controls (deterministic hash); **wire** `requestLivePredictFill` to send belief→`active_harmony`, bounded features from Task 2, `jam_mode`, `controls`, `role_mask` (replace hardcoded density/V2-only harmony); use `createLivePredictAbortController` (never bare `AbortController`); still max 1 in-flight; stale discard; 503/`LIVE_PREDICT_UNAVAILABLE` / network / no-abort → local roles continue + `jam_predict_unavailable`. No new `AiOperation`. Tests: schema reject (events forbidden), mode-aware fake determinism, unavailable path, AbortController missing path.
  Files: `backend/app/live_performance_schemas.py`, `services/fake_live_accompaniment.py`, `services/live_accompaniment_predict.py`, `routers/live_performance.py`, `frontend/src/api/livePerformanceApi.js`, `frontend/src/store/musicStore.js` (`requestLivePredictFill`), `backend/tests/test_live_performance_predict.py` (+ jam cases).
  LOGGING: INFO predict with jam_mode + event_count + latency; WARN unavailable/fallback/stale; never prompts/MIDI.
  Dependencies: Tasks 1–5 (**not** Task 6 — API/store wiring must not wait on panel).

### Phase 3: Commit, tests, docs

- [x] **Task 8: Multi-track Commit + `ensureJamRoleTracks` + optional harmony spans.**
  Deliverable: Pure `ensureJamRoleTracks(composition, roleMap, instrumentSet)` creating valid V2 tracks when `ensure_missing_tracks`; extend `liveTakeApply.js` / `commitLiveCoPerformance` (or `commitAiJam`) to map roles→tracks in **one** undoable transaction; user stream → user track; AI roles → mapped tracks; reject Commit with incomplete map when AI events exist (`jam_commit_map_incomplete`); optional `commit_harmony_spans` (default on for `user_chords`, off for `user_melody`) writes belief/`jamContext.planned_window` into `harmony[]` **metadata only** — never invents notes from spans; Cancel unchanged. Assert committed composition passes validation and remains piano-roll editable. Preserve single-track Commit as explicit non-jam opt-in if needed.
  Files: `frontend/src/utils/liveJamEnsureTracks.js` (or inside `liveTakeApply.js`) (+ tests), `liveTakeApply.js`, musicStore commit path, panel Commit UX for role track picks / ensure toggle / harmony-span toggle.
  LOGGING: INFO commit summary `{ userCount, roleCounts, tracksEnsured, harmonySpansCommitted, trackId prefixes }` only.
  Dependencies: Tasks 5, 6.

- [x] **Task 9: Deterministic simulated MIDI stream tests + multi-bar jam acceptance.**
  Deliverable: Fixture builders simulating Transport-synced MIDI streams (melody several bars; chordal stream for `user_chords`). Assert: (1) belief stable under passing tones; (2) AI roles scheduled ≥ horizon under local engine; (3) predict delay → degradation, transport continues; (4) no V2 mutation until Commit; (5) after Commit, events on correct role tracks + valid V2 (+ optional harmony spans for chords mode); (6) predict unavailable / no-abort fallback; (7) midiPhase exclusion; (8) features built from bounded window only. Prefer FE unit/perf style alongside `liveAccompanimentBuffer.perf.test.js`; backend fake jam determinism tests.
  Files: `frontend/src/utils/liveJam.simulatedMidi.test.js` (or split), backend jam predict tests.
  LOGGING: tests may spy hold/degradation codes; no new production spam.
  Dependencies: Tasks 2–5, 7, 8 (**not** Task 6 panel).

- [x] **Task 10: Docs, AGENTS, DESCRIPTION, ROADMAP milestone.**
  Deliverable: New `docs/ai-jam.md` (modes, feature/belief split, hysteresis, context cache, controls, multi-track Commit, optional harmony spans, fallback, simulated tests, explicit non-goal: user monitor voices) + cross-links from `docs/co-performance.md`, `docs/midi-live-input.md`, README; `AGENTS.md` entry; unchecked ROADMAP milestone **V4 AI Jam real-time co-composition**; DESCRIPTION one-liner for jam modes.
  Files: `docs/ai-jam.md`, `docs/co-performance.md`, `README.md`, `AGENTS.md`, `.ai-factory/ROADMAP.md`, `.ai-factory/DESCRIPTION.md`.
  LOGGING: N/A; document namespaces `liveJam` + never-log rules + AbortController factory note.
  Dependencies: Tasks 6, 8, 9.

<!-- Commit checkpoint: task 10 → Commit 4 -->

## Acceptance Criteria

1. **User Melody:** User improvises a melody for several bars (simulated or live); AI schedules synchronized **bass** and **harmonic accompaniment** for the prediction horizon on the shared Transport; Stop → Commit yields editable V2 with user notes and AI roles on distinct mapped tracks.
2. **User Chords:** User supplies chordal material; AI schedules **melody**, **bass**, and **texture**; Commit maps roles correctly; optional belief→`harmony[]` metadata when enabled.
3. Live analysis exposes pitch activity, beat position, probable key, probable harmony, and phrase boundary hints as bounded **raw** features (no full analysis report persistence; no belief nested inside features).
4. Harmony belief uses confidence/hysteresis — a single ambiguous live note does **not** abruptly change accompaniment harmony (proven by simulated stream tests).
5. Jam controls (complexity, density, style, instrument set, responsiveness) affect generation without persisting jam UI state into revisions.
6. No large remote LLM call per note; hot path remains local/fake; cold predict max one in-flight, wired with belief/features/`jam_mode`, and never blocks Transport/MIDI handlers.
7. When local model/runtime/predict/AbortController is unavailable, local role patterns + degradation continue; transport does not stop solely due to AI absence.
8. Cancel never mutates V2; Commit is one transaction (with optional ensure-tracks); result editable in existing editors.
9. Tests cover deterministic simulated MIDI streams for both modes + fallback + hysteresis + bounded feature windows.
10. Docs + ROADMAP milestone shipped.

## Implementation Notes for `/aif-implement`

- Treat `.ai-factory/plans/v4-co-performance-realtime-engine.md` as **shipped foundation** — extend, do not duplicate.
- Prefer `routers/` + `services/` + FE `utils/` over growing `main.py` / unrelated store sprawl.
- RULES: only `tracks[].events[]` are playable; harmony/belief are context; never log secrets/payloads.
- Verbose logging default; gate with `LOG_LEVEL` / `VITE_LOG_LEVEL`.
- Keep `ai_agents/` free of SQLite; do not put jam on the multi-agent spine.
- Do not enable WebSocket or Web Workers in this plan without a new measured gate task.
- Fake determinism is mandatory for CI (`LLM_FAKE_MODE`).
- Prefer `globalThis` / `createLivePredictAbortController` for browser APIs (patch 2026-09-23).
- Do **not** implement user MIDI monitor voices in this plan.

## INFO

`INFO [aif-plan] resolved plan file: .ai-factory/plans/v4-ai-jam-realtime-co-composition.md (format=slug)`
`INFO [aif-improve] applied all refinements 2026-09-23 — tasks 1–10 restructured; belief/features split; liveJamContext; scheduler API; predict store wiring; ensureJamRoleTracks; optional harmony Commit; Task 9 UX merged into Task 6; deps fixed; user MIDI monitor deferred`
`INFO [aif-improve] parent ACP co-performance plan left unchanged (shipped / all tasks [x])`
