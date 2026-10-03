# Implementation Plan: V5 Continuous Generative Music and End-to-End Platform Hardening

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-04

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: Extend Adaptive tab with opt-in **Continuous** mode controls (arm / maintain / MusicState inspect / fallback honesty). Opening Adaptive never starts continuous generation. Engine clients may arm continuous only through explicit API. No piano-roll redesign; Picture / Generate / Lab / Ardour panels unchanged except where Part B acceptance asserts existing surfaces.
- Plan depth: ultra (full mode). Locked approach tables, audit, terminology, formulas, and refuse codes below are part of the plan
- Refined: 2026-10-04 (`/aif-improve`). Playback never advances `bar` past `bar_count` — continuous anchors on `music_state.virtual_bar` when legacy planner would idle / playback at end / looping; `music_state` is optional sibling on snapshot (not nested in `context`); `continuous` latched on start request (maintain bodyless today); SPA audition extends `tickToSeconds` with constant-tempo past-end mapping; engine path is `/adaptive/session/{id}/continuous/maintain` (singular); thin client SDK methods when engine continuous enabled; architecture forbid tokens must cover MusicState + continuation modules; V5 matrix split Task 11a/11b; Task 5 depends on 1+4; Task 7 (SDK) on 6; Task 8 (UI) on 5; Task 9 on 4–8; Task 11 on 9–10; Task 11b after 11a
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Franchise milestone appended to `ROADMAP.md` via `/aif-roadmap` after verify (implementation itself must not edit the roadmap)
- Scope: (A) continuous generative music mode that upgrades runtime continuation with compressed long-term MusicState, rolling bounded windows past stored `bar_count`, anti-drift / anti-repetition guards, and deterministic fallback loops; (B) V5 end-to-end fake-mode studio + opt-in Docker hardening covering the twenty scenario classes, migration ladder V1→V5 product surface, composition source-of-truth invariants, cancel / node-failure / late-inference / rights / Ardour non-corruption gates. Never invents `composition.v5`. Never writes `tracks[].events[]` from continuous sessions. Normal CI uses tiny models, mocks, fixture audio/video, simulated MIDI, simulated OSC/Ardour. Expensive hardware/model tests stay opt-in. `ai_agents/` must not import continuous / MusicState / continuation modules
- Predecessors: `.ai-factory/plans/v5-runtime-symbolic-continuation.md` (windows, fallbacks, late discard, thin `adaptive.runtime.context.v1`), `.ai-factory/plans/v5-adaptive-score-playback-runtime.md`, `.ai-factory/plans/v5-runtime-musical-context.md`, `.ai-factory/plans/v5-adaptive-music-engine-api.md`, `.ai-factory/plans/v5-adaptive-music-client-sdks.md`, plus shipped V5 feature plans for film / universe / personal / preference / execution nodes / WebGPU / MIDI / conductor / spatial / Ardour / asset packs / provenance / rights / Model Lab / ensemble. Do not reopen their score-schema locks. This plan is the V5 platform closing milestone analogous to V3 “DAW interoperability and V3 end-to-end platform hardening” and V4 “studio acceptance and production hardening”

## Roadmap Linkage
Milestone: "V5 Continuous generative music and end-to-end platform hardening"
Rationale: Runtime continuation already fills bounded windows ahead of a stored score, but clips at `bar_count` and keeps only a thin memory. Games, ambience, and installations need theoretically unbounded generative windows with compressed MusicState and hard non-stop fallbacks. Separately, V5 features have feature-level acceptance but no unified studio/Docker gate proving the Composer OS end-to-end. Append via `/aif-roadmap` after verify; implementation does not edit `ROADMAP.md`.

## Goal

### Part A — Continuous generative music

Support theoretically unbounded music generation for ambience, games, installations, and background playback while playback never waits on generative inference.

Ship:

1. Non-playable session document **`adaptive.runtime.music_state.v1`** (compressed long-term MusicState) carried inside / beside the continuation snapshot — themes, motif usage history, recent harmonic trajectory, repetition history, energy/tension, orchestration history, state context. No note events. No pitch lists.
2. **Continuous mode** on the existing continuation session: rolling virtual timeline that may generate past stored `composition.v2` `bar_count` into the session buffer only.
3. Bounded future windows (reuse reserved/target bar counts) with caps on buffer events, MusicState rings, and summary digests — prevent unbounded context growth.
4. Soft/hard guards that reduce excessive repetition, theme drift, and harmonic dead ends before model apply; always publish a deterministic fallback loop first.
5. Opt-in Adaptive-tab Continuous UX + optional Adaptive Engine continuous arm when flag-enabled.
6. Docs: continuous music contract, honesty that buffer is not the durable score.

### Part B — V5 end-to-end validation

Ship a fake-mode studio + opt-in Docker hardening suite that exercises:

1. Adaptive game soundtrack  
2. Runtime generative continuation  
3. External context API  
4. Film scoring with hit points  
5. Picture edit → rescore  
6. Musical Universe reuse  
7. Personal model adapter  
8. Preference ranking  
9. Distributed inference  
10. WebGPU fallback  
11. Expressive MIDI performance  
12. AI Conductor performance  
13. Spatial preview  
14. Ardour round trip  
15. Asset-pack generation  
16. Provenance  
17. Rights enforcement  
18. Model Lab  
19. Model ensemble  
20. Continuous music  

Also verify: migrations V1→V5 product surface; old V1–V4 projects remain usable; canonical Composition source-of-truth; autonomous/adaptive cancellation; distributed node failure without trust escalation; runtime music never stops because inference is late; rights restrictions enforced; Ardour integration failure cannot corrupt Ardour sessions (never edit `.ardour` XML).

Acceptance (Part A): With playback running on a looping adaptive score and `ADAPTIVE_CONTINUOUS_ENABLED=1`, start continuation with `continuous=true` and mode `continuation` (latched on the session). Maintain returns in under `ADAPTIVE_CONTINUATION_LATENCY_BUDGET_MS` with a deterministic `fallback_kind` set and transport still `playing`. When the legacy planner would idle (`target_start > bar_count`) or playback is at `duration_ticks` / looping at end — note: `playback.bar` never exceeds `bar_count` — continuous maintain uses `music_state.virtual_bar` as anchor, returns a non-idle snapshot (not `continuation_window_past_end` idle), publishes fallback first, and may apply a validated model buffer into virtual bars when applicable. Snapshot carries optional sibling `music_state` plus projected `context`. `adaptive.runtime.music_state.v1` exposes capped theme ids, motif usage digests, harmonic trajectory labels (not pitches), repetition history, energy/tension rings, orchestration history digests, and `runtime_state_id` context. Injected high-repetition prefix raises repetition pressure and prefers motif/variation fallback before reuse. A blocked model past wall/tick deadline is discarded; music keeps looping/falling back. Composition row and adaptive-score row fingerprints unchanged. Same seed + mode + virtual anchor produce the same accompaniment pitches. Flag off preserves legacy clipped continuation behavior (past-end idle); `continuous=true` while flag off → `adaptive_continuous_disabled`. SPA Fill ahead schedules virtual-buffer ticks via constant-tempo past-`durationTicks` mapping when `audible`. Opening Adaptive never starts continuous generation. `ai_agents/` cannot import continuous settings/schemas/services (architecture forbid tokens enforced).

Acceptance (Part B): `backend/tests/studio_acceptance/` (or sibling `v5/` package) runs a fake-bundle matrix covering the twenty scenarios with tiny models/mocks/fixtures; each scenario asserts the composition source-of-truth invariant helper. Migration ladder still opens a planted `composition.v1` project as `composition.v2` after upgrade to head. Cross-cutting tests: cancel autonomous + cancel continuation leave V2 head unchanged; fake execution-node failure reschedules without public-cloud escalation; late continuation model does not stop playback; rights refuse blocks personal/MT/reference train paths; Ardour exchange/companion refuse paths never write `.ardour` XML (schema + path guards). `scripts/v5_docker_acceptance.sh` is opt-in (`RUN_DOCKER_ACCEPTANCE=1`), uses fake modes only, and is not called by `scripts/run_tests.sh`. Docs cover architecture, migration, and operations for the V5 Composer OS.

```text
playback bar / virtual bar N  (already sounding; never blocked)
        ↓
MusicState summary (compressed rings + digests)
        ↓
reserved window  |  target window (may extend past stored bar_count)
        ↓
deterministic fallback loop / motif / accompaniment  (always first)
        ↓
optional symbolic model (deadline discardable)
        ↓
guards: repetition / theme drift / harmonic dead-end
        ↓
session buffer only  (never projects.composition_json)
        ↓
Tone schedules audible ahead notes; loop keeps sounding on discard
```

**Terminology lock:** Product generation is **V5**. Playable score stays **`composition.v2`**. No **`composition.v5`**, no **`adaptive.score.v2`**. **Continuous mode** = opt-in continuation behavior that may generate into a rolling virtual timeline past stored `bar_count` using session buffer + MusicState; it is not a second durable score. **MusicState** = `adaptive.runtime.music_state.v1` compressed long-term musical memory (themes, motif usage, harmonic trajectory, repetition, energy/tension, orchestration history, state context) — distinct from external **musical context** (`adaptive.musical_context.v1` game samples) and from thin legacy **`adaptive.runtime.context.v1`** (ship-1: MusicState supersedes/extends context fields; legacy context fields remain readable for one release via projection). **Virtual bar** = continuous timeline index (`music_state.virtual_bar`) that may exceed composition `bar_count`; it is the continuous anchor when the playback clock is clamped at end or looping — `playback.bar` itself never exceeds `bar_count`. Buffer events use absolute ticks from the compiled timeline extended by constant meter/tempo assumptions with honesty warning. **Window** / **fallback** / **applicable** / **late discard** keep the continuation plan meanings. **Guard** = deterministic Soft preference (fallback reorder / plan fragment) or Hard refuse of a model apply (never silence). **Part B scenario** = fake-mode studio or acceptance test vector proving one product surface; not a live GPU/Ardour requirement. **Source-of-truth invariant** = audible notes come only from `composition.v2` `tracks[].events[]` or from an explicitly labeled session buffer/preview — never from harmony, plans, MusicState, film plans, ensemble reports, or adaptive graph refs alone.

## Approach Evaluation (locked)

### Part A1 — Product surface for continuous music

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New top-level `/continuous/*` microservice + new score schema** | Clean green field | Breaks V5 axiom; duplicates playback/continuation; invents alternate playable score risk | **Reject** |
| **B. Only document that looping adaptive scores are “continuous enough”** | Cheap | Does not meet unbounded generation, MusicState, or anti-drift requirements | **Reject** |
| **C. Opt-in Continuous mode on existing continuation session + MusicState document + Adaptive UX; optional engine arm** | Reuses windows/fallbacks/deadlines; clear upgrade path; no new score schema | Extends continuation contracts carefully | **Accepted** |

### Part A2 — Where MusicState lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Persist MusicState + generated bars into SQLite / `composition.v2` every maintain** | Survives restart | Writes notes from runtime; retention explosion; violates preview/session axioms | **Reject** |
| **B. Replace `adaptive.runtime.context.v1` in place with incompatible breaking fields** | One document | Breaks shipped clients/tests mid-franchise without migration story | **Reject** |
| **C. Add `adaptive.runtime.music_state.v1` as the rich memory; project legacy `context` fields from it for compatibility; keep process-memory only** | Rich memory + non-breaking read path; restart drops (documented) | Clients wanting durability must Commit separately (out of ship-1) | **Accepted** |

### Part A3 — Unbounded timeline

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Append bars into durable `composition.v2` when past end** | Piano roll shows growth | Runtime write; late apply corrupts passed bars; undo hell | **Reject** |
| **B. Keep clipping at `bar_count` and only loop** | Already shipped | Not generative continuous music | **Reject** as sole behavior |
| **C. Continuous mode uses virtual bars past `bar_count`; buffer holds validated local events; playback loop + buffer audition keep sound; optional honesty warning `continuation_virtual_timeline`** | Unbounded in session; score unchanged | Restart loses buffer; meter/tempo extension must be assumed constant with warning | **Accepted** |

### Part A4 — Who waits on the model

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Playback `advance` awaits generate** | Simpler client | Hard-forbidden; music stops when inference is late | **Reject** |
| **B. Continuous maintain awaits model then returns** | One response | Breaks latency budget and non-stop rule | **Reject** |
| **C. Maintain publishes fallback immediately; model is background; late/inapplicable discarded; transport untouched** | Matches continuation lock | Client polls / Fill ahead | **Accepted** |

### Part A5 — Anti-repetition / drift / dead-end controls

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Neural quality model decides “interestingness”** | Flashy | Honesty violation; new deps; non-deterministic CI | **Reject** |
| **B. Silence or stop transport when guards fire** | Aggressive | Violates “music never stops” | **Reject** |
| **C. Deterministic MusicState rings + digest compare: raise repetition pressure → reorder fallbacks; theme-id set drift vs active themes → prefer `state_specific`/`variation` plan fragment; harmonic trajectory stuck (same label ≥K) → force `harmonic_continuation` fragment or accompaniment seed bump; hard-reject model apply only when integrity fails or guard digest equals last applied digest (duplicate window)** | Testable; always has fallback sound | Heuristic, not taste — document honesty | **Accepted** |

### Part A6 — Context growth

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Keep full prefix event history in MusicState** | Max fidelity | Unbounded memory; logs risk | **Reject** |
| **B. Unlimited rings** | Convenient | Growth unbounded | **Reject** |
| **C. Fixed caps: theme_ids≤16, motif_usage≤32 digests, harmony_trajectory≤16 labels, repetition_history≤16, energy/tension≤8 each, orchestration_history≤16 digests, summary_digest sha16; oldest dropped; prefix events stay job-private not on MusicState** | Matches thin-context spirit at richer schema | Lossy by design | **Accepted** |

### Part A7 — Engine / client surface

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Put continuous maintain on public `/adaptive/*` by default** | Games one API | Expands engine trust surface; continuation was deliberately studio-scoped | **Reject** as default |
| **B. Studio-only forever; games cannot continuous-generate** | Smallest | Blocks installation/game goal | **Reject** |
| **C. Studio routes remain primary; optional `ADAPTIVE_ENGINE_CONTINUOUS_ENABLED` (default off) adds engine `POST /adaptive/session/{id}/continuous/maintain` (singular `session`, matching shipped engine router) that proxies the same service with bearer-or-loopback; clients get thin methods only when enabled** | Explicit opt-in; preserves engine lean default | Two flags to operate | **Accepted** |

### Part A8 — UI

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New top-level Continuous tab** | Visible | Crowds V5 tabs; feature is Adaptive-adjacent | **Reject** for ship-1 |
| **B. No UI; API only** | Small | Operators cannot inspect MusicState / fallback honesty | **Reject** |
| **C. Adaptive tab Continuous subsection: enable toggle (client), arm/maintain/Fill ahead, MusicState summary, fallback/source badges; never auto-start on tab open** | Matches continuation UX | Panel grows | **Accepted** |

### Part B1 — How to organize V5 e2e

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. One giant Playwright journey requiring GPU, Ardour, LoRA** | Realistic | Flaky; blocks CI; contradicts fake-mode axiom | **Reject** |
| **B. Docs checklist only** | Cheap | Not enforceable | **Reject** |
| **C. Fake-mode studio_acceptance (or `studio_acceptance/v5/`) scenario modules + shared invariant helpers; fold/bridge existing `test_*_acceptance.py` where cheap; opt-in `scripts/v5_docker_acceptance.sh`; expensive torch/GPU/Ardour stay `importorskip` / env gates** | Matches V4 pattern; CI-safe | Some scenarios are thin wrappers asserting already-shipped acceptances | **Accepted** |

### Part B2 — Migration verification

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Invent `composition.v5` migration** | Marketing | Forbidden axiom | **Reject** |
| **B. Only re-run V1→V2 ladder** | Already exists | Misses V5 sidecar tables | **Reject** as sole |
| **C. Extend ladder: plant V1 score → upgrade head → open as V2; assert V5 sidecar tables exist/empty-safe; open fixture projects that carry adaptive/universe/rights rows when present; never require `composition.v5`** | Product-true “V1 through V5” | Careful fixture size | **Accepted** |

### Part B3 — Failure / non-corruption gates

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Live Ardour XML mutate-and-diff** | Strong | Needs real Ardour; dangerous | **Reject** |
| **B. Trust docs** | Cheap | Not a gate | **Reject** |
| **C. Assert schema refuse of `session_xml` / path escapes; companion refuse public host + stale feedback; exchange prepare never opens `.ardour`; continuous/cancel/node-fail fingerprint helpers** | Enforceable in CI | Does not prove a hostile operator with FS write | **Accepted** (honesty in docs) |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Continuation windows / fallbacks / deadlines | `adaptive_runtime_continuation*.py`, docs | Extend planner for continuous/virtual; keep fallback order + late discard |
| Thin memory | `AdaptiveRuntimeContextV1` | Project from MusicState; keep field compatibility |
| Playback clock | `adaptive.playback.runtime.v1` | Read-only; never await model |
| Musical context | external samples → playback cmds | Orthogonal; continuous may read `runtime_state_id` / intensity only |
| Engine API | `/adaptive/*` session | Optional continuous maintain behind second flag |
| Symbolic generate / fake | `generate_symbolic_composition`, `fake:symbolic-*` | Model + accompaniment paths |
| Motif transform | `transform_repeat` | Motif fallback |
| Validator | `validate_composition_integrity` | Every buffer composition |
| Studio acceptance | `backend/tests/studio_acceptance/` | Extend with V5 scenarios + invariants |
| Feature acceptances | many `test_*_acceptance.py` | Bridge or call shared helpers; avoid duplicating heavy setup |
| Docker gates | `scripts/v{1,2,3,4}_docker_acceptance.sh` | Add `v5_docker_acceptance.sh` opt-in |
| Rights / provenance / Ardour / ensemble / Lab | shipped | Assert in Part B matrix |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| MusicState document | Rich capped rings/digests not present |
| Continuous / virtual timeline | Past-end currently idle `continuation_window_past_end` |
| Guard heuristics | Repetition/drift/dead-end only partially via thin `repetition_count` |
| Engine continuous opt-in | Engine explicitly excludes continuation today; path must be `/adaptive/session/` singular |
| Maintain request body | Maintain is bodyless today — `continuous` must latch on start |
| Playback past end | Clock clamps; `bar` never exceeds `bar_count` — need virtual_bar anchor |
| Tone past-end ticks | `tickToSeconds` clamps to `durationTicks` — need constant-tempo extension for audible schedule |
| Client SDKs | No continuation/continuous methods today |
| Adaptive Continuous UX | Fill ahead exists; no MusicState inspect / continuous toggle |
| ai_agents forbid enforcement | AGENTS.md lists continuation forbids; architecture test does not yet |
| Unified V5 studio matrix | Feature tests exist; no twenty-scenario studio package |
| `v5_docker_acceptance.sh` | Missing |
| Architecture / migration / ops docs for Composer OS close | Need final docs set |

### Coupling risks to avoid

1. Writing `projects.composition_json` or adaptive `body_json` from continuous maintain.
2. Inventing `composition.v5` / `adaptive.score.v2`.
3. Awaiting the model inside playback `advance` / engine status pump.
4. Importing continuation/MusicState modules from `ai_agents/`.
5. Logging MusicState labels at INFO if they embed user prompts; never log event arrays, pitches, buffer JSON, or prefix digests.
6. Sharing AI Jam `liveScheduledEventIds` with continuous schedule queue.
7. Enabling engine continuous by default or putting continuous on `/ready`.
8. Requiring torch, real Ardour, real WebGPU, or live LLM in `run_tests.sh`.
9. Editing `ROADMAP.md` from implementation.
10. Auto-starting continuous when opening Adaptive or creating an engine session.
11. Treating critic/preference/ensemble scores as continuous guards.
12. Mutating `.ardour` XML or remote-executing Lua in any acceptance path.

## Scope And Decisions

### In scope
- `adaptive.runtime.music_state.v1` + projection to legacy context fields
- Continuous mode flag + virtual timeline planner + guards
- Service/HTTP/Adaptive UX + optional engine continuous maintain
- Part B studio matrix, invariants, migration extension, opt-in Docker script
- Final architecture, migration, and operational documentation
- Verbose structured logging per task

### Out of scope
- Durable Commit of continuous buffer into `composition.v2` (future plan)
- Redis / multi-worker MusicState
- Neural taste models / “best music” ranking for continuous
- Real Ardour, Demucs, MusicGen, LoRA, WebGPU hardware in default CI
- New Collab C actions for continuous
- Reopening ensemble/Lab/film/universe product decisions
- Editing `ROADMAP.md` during implement

### Architecture decisions (locked)

**1. Layering**

```text
HTTP routers/adaptive_scores.py  (+ optional routers/adaptive_engine.py continuous)
        ↓  enforce_current("read") / engine auth
services/adaptive_runtime_continuation_service.py
        ↓  read playback; never command it
services/adaptive_runtime_music_state.py          (pure update/compress/guards)
services/adaptive_runtime_continuation.py          (window planner + continuous virtual)
services/adaptive_runtime_continuation_fallback.py
        ↓
symbolic generate + validator
```

`ai_agents/` forbids imports of continuous settings, MusicState schemas/services, and existing continuation modules.

**2. Flags**

| Setting | Default | Meaning |
|---------|---------|---------|
| `ADAPTIVE_CONTINUOUS_ENABLED` | off | Studio continuous / virtual timeline |
| `ADAPTIVE_ENGINE_CONTINUOUS_ENABLED` | off | Engine continuous maintain proxy (requires continuous enabled) |
| Existing `ADAPTIVE_CONTINUATION_*` | unchanged | Window sizes, deadlines, buffer cap |

Invalid env → warn + default. Listed in `.env.example`. Not on `/ready`.

**3. MusicState (`adaptive.runtime.music_state.v1`)**

`extra="forbid"`. No `events` / `notes` / `pitch` / `pitches` / `midi_events` / `composition`.

| Field | Cap / rule |
|-------|------------|
| `schema_version` | `adaptive.runtime.music_state.v1` |
| `active_theme_ids` | ≤16 unique strings |
| `motif_usage` | ≤32 `{motif_id, use_count, last_virtual_bar}` |
| `harmony_trajectory` | ≤16 chord/label strings (not pitches) |
| `repetition_history` | ≤16 `{digest16, count}` |
| `energy` | ≤8 floats `0..1` |
| `tension` | ≤8 floats `0..1` (from playback intensity and/or mapped context tension when present) |
| `orchestration_history` | ≤16 digests of track-role/program fingerprint |
| `runtime_state_id` | optional score state id |
| `summary_digest` | 16 hex chars over canonical MusicState core fields |
| `virtual_bar` | int ≥1 current continuous anchor |
| `guard_flags` | closed strings subset: `repetition_pressure`, `theme_drift`, `harmonic_dead_end` |

**Snapshot nesting (locked):** `AdaptiveRuntimeContinuationV1` stays `extra="forbid"`. Add optional sibling field `music_state: AdaptiveRuntimeMusicStateV1 | None`. Keep required `context: AdaptiveRuntimeContextV1`. Never nest MusicState fields inside `context`. Service always projects MusicState → `context` when MusicState is present so legacy readers keep working.

**4. Continuous window formula and anchor semantics (locked)**

When continuous disabled: keep shipped clip-to-`bar_count` + past-end idle.

When continuous enabled and session latched `continuous=true`:

- **Playback fact:** `adaptive_playback` clamps position to `timeline.duration_ticks`; `bar` never exceeds `bar_count`. Acceptance must not require `playback.bar > bar_count`.
- **Anchor `N`:** while the legacy planner would produce a non-idle in-score target, use `playback.bar`. When legacy would idle (`target_start > bar_count`) **or** playback `position_tick >= duration_ticks` **or** a published loop is wrapping at end with continuous latched → use and advance `music_state.virtual_bar` (start `virtual_bar` at `bar_count + 1` on first virtual maintain).
- Reserved / target lengths unchanged (`PLAY_BARS` / `GENERATE_BARS`); for virtual anchors they are not clipped to `bar_count`.
- Past stored end: do **not** emit `continuation_window_past_end` idle; emit `continuation_virtual_timeline` warning; schedule model allowed; fallback always published.
- Tick mapping: never call `compile_timeline.bar_start_tick` for `bar > bar_count` (raises `ValueError`). Use `deadline_tick = last_compiled_bar_end_tick + (virtual_offset * ticks_per_bar_assumed)` with warning `continuation_meter_assumed_constant`.
- Buffer still capped by `MAX_BUFFER_EVENTS`; oldest extra dropped + `continuation_truncated`.
- `result_applicable` for virtual jobs compares virtual anchor / digest / state / intensity; tick deadline uses the computed virtual deadline.

**5. Guards (deterministic)**

| Signal | Soft effect | Hard effect |
|--------|-------------|-------------|
| `repetition_count` or digest count ≥3 | Prefer `motif_variation` before `reuse_loop` (already partial); continuous also bumps accompaniment seed | If model buffer digest equals last applied digest → discard apply (`continuation_guard_duplicate`) keep fallback |
| Active themes empty but motif_usage non-empty / theme set disjoint from score state motifs | Prefer `variation` / `state_specific` plan fragment | none (fallback still plays) |
| Same `harmony_trajectory` tail label ≥4 | Prefer `harmonic_continuation` fragment / seed bump | none |
| Integrity fail | n/a | never apply; fallback remains |

Guards never call stop; never set `instructions.stop`.

**6. Fallbacks**

Keep ordered `reuse_loop` → `motif_variation` → `accompaniment` with repetition reorder. Continuous past end: `reuse_loop` means continue publishing existing loop instructions / last audible buffer cycle; if no loop enabled, accompaniment fallback is mandatory so sound does not depend on silence. If all fail → still `reuse_loop` + `continuation_invalid`.

**7. Engine continuous**

Only when both flags on. Exact path: `POST /adaptive/session/{session_id}/continuous/maintain` (and optional `GET …/continuous/buffer`). Proxies the same continuation service for the engine-bound `(project_id, score_id)`. Echo `continuous_enabled` and `engine_continuous_enabled` on engine session/status documents (and/or studio continuation snapshot extras). No buffer bytes on status websocket by default. Not on `/ready`. Do not auto-arm on engine start. Thin SDK methods in `mukit-adaptive` / `@mukit/adaptive-music` call these routes when enabled; clear typed errors when disabled.

**8. Request latch and HTTP bodies (locked)**

Today `POST …/continuation/maintain` is bodyless. Ship-1:

- Extend start body: `{ expected_document_revision, mode, continuous?: bool }` — default `continuous=false`. Refuse `continuous=true` when `ADAPTIVE_CONTINUOUS_ENABLED` off → `adaptive_continuous_disabled`.
- Latch `continuous` on the in-memory session at start. Maintain uses the latch (optional empty/echo body allowed later; not required).
- Snapshot response includes `continuous: bool`, optional `music_state`, projected `context`.

**9. SPA / Tone audition (locked)**

Ship-1: loop/fallback keep sounding via existing playback instructions. When continuous buffer is `audible`, Fill ahead schedules via an extended `tickToSeconds` / `secondsBetweenTicks` that maps ticks `> durationTicks` with constant tempo/meter (same assumption as virtual deadline), never clamping virtual ticks to end-of-score. Do not share `liveScheduledEventIds`. Document honesty: virtual audition is session-only and not a durable score write.

**10. Part B matrix layout**

```text
backend/tests/studio_acceptance/
  invariants.py              # extend: assert_composition_sot, assert_no_ardour_xml_write, fingerprint
  test_migration_ladder.py   # extend V5 sidecar emptiness / open
  v5/
    conftest.py              # FAKE_BUNDLE + V5 flags overlay (continuous, ensemble, lab, ardour fake, …)
    # Task 10a — core
    test_scenario_adaptive_game.py
    test_scenario_continuation.py
    test_scenario_external_context.py
    test_scenario_film_scoring.py
    test_scenario_picture_adapt.py
    test_scenario_universe_reuse.py
    test_scenario_continuous_music.py
    test_scenario_cancel_and_node_failure.py
    # Task 10b — remaining surfaces
    test_scenario_personal_adapter.py
    test_scenario_preference_rank.py
    test_scenario_distributed_inference.py
    test_scenario_webgpu_fallback.py
    test_scenario_expressive_midi.py      # path/unit pointer + discovery fallback; no browser GPU
    test_scenario_conductor.py
    test_scenario_spatial.py
    test_scenario_ardour_roundtrip.py
    test_scenario_asset_pack.py
    test_scenario_provenance.py
    test_scenario_rights.py
    test_scenario_model_lab.py
    test_scenario_ensemble.py
```

Scenarios may import helpers from existing acceptance modules; they must not require torch/GPU/Ardour. Frontend-only surfaces (expressive MIDI, WebGPU) assert via documented unit/e2e pointers plus a studio stub that checks API/discovery fallback contracts.

**11. Docker**

`scripts/v5_docker_acceptance.sh`: compose up fake stack; hit a thin subset (open migrated project, adaptive playback+continuous maintain late-discard, rights refuse, ardour status fake, ensemble status); backend restart; reopen fingerprint. `RUN_DOCKER_ACCEPTANCE=1` only. Not in `run_tests.sh`.

**12. Docs axiom**

Must produce/update:

- `docs/continuous-music.md` — MusicState, virtual timeline, virtual_bar anchor (not playback.bar > bar_count), guards, flags, SPA audition, fallbacks, honesty
- `docs/v5-platform.md` (or extend `docs/v4-studio-operations.md` with a V5 sibling section) — architecture summary, migration, operations, CI vs opt-in
- Cross-links in `docs/adaptive-runtime-continuation.md`, `docs/adaptive-music-engine.md`, `docs/adaptive-music-client.md`, `docs/testing.md`, `README.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`, `.env.example`

**13. Locked refuse / warning codes (additive)**

| Code | When |
|------|------|
| `adaptive_continuous_disabled` | Start/maintain with continuous while studio flag off (legacy continuation still allowed) |
| `adaptive_engine_continuous_disabled` | Engine continuous while either flag off |
| `continuation_virtual_timeline` | Warning: generating past stored bars |
| `continuation_guard_duplicate` | Warning/discard: model duplicate of last apply |
| `continuation_meter_assumed_constant` | Warning: virtual tick map / SPA past-end seconds |
| Existing continuation warnings | Unchanged meanings when continuous off |

**14. ai_agents forbid tokens (additive to `test_ai_agents_architecture.py`)**

Add exact substrings (do not remove existing): `adaptive_runtime_music_state_schemas`, `adaptive_runtime_music_state`, `from app.adaptive_runtime_continuation_settings`, `from app.adaptive_runtime_continuation_schemas`, `adaptive_runtime_continuation_service`, `adaptive_runtime_continuation_fallback`, `adaptive_runtime_continuation_runtime`, `from app.services.adaptive_runtime_continuation import`.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: add MusicState contract and continuous window planner`
- **Commit 2** (after tasks 4–6): `feat: run continuous generative maintain with guards and optional engine arm`
- **Commit 3** (after tasks 7–9): `feat: Adaptive Continuous UX, client SDK hooks, and continuous acceptance`
- **Commit 4** (after tasks 10–12): `test: add V5 studio e2e matrix and docker acceptance gate`
- **Commit 5** (after task 13): `docs: document continuous music and V5 Composer OS operations`

## Tasks

### Phase 1: MusicState and continuous planner
- [x] Task 1: Add MusicState schemas, settings flags, start-request `continuous`, sibling snapshot field, legacy context projection
- [x] Task 2: Extend pure window planner for continuous virtual timeline + virtual_bar anchor semantics
- [x] Task 3: Implement MusicState update, compression, and guard helpers

### Phase 2: Service, HTTP, engine, clients
- [x] Task 4: Wire continuous mode into continuation service (fallback-first, guards, virtual buffer)
- [x] Task 5: Studio HTTP continuous fields + ai_agents forbid tokens (depends on 1, 4)
- [x] Task 6: Optional Adaptive Engine continuous maintain proxy (depends on 4–5)
- [x] Task 7: Thin adaptive client SDK methods for engine continuous (depends on 6)

### Phase 3: UI and Part A acceptance
- [x] Task 8: Adaptive-tab Continuous UX + MusicState inspect + past-end Tone mapping (depends on 5)
- [x] Task 9: Part A deterministic acceptance tests (depends on 4–8)

### Phase 4: Part B e2e hardening
- [x] Task 10: Studio invariant helpers + migration ladder V5 sidecar checks
- [x] Task 11: V5 studio scenario matrix 10a core + 10b remaining (depends on 9–10)
- [x] Task 12: Opt-in `scripts/v5_docker_acceptance.sh` + testing docs pointers (depends on 11)

### Phase 5: Final documentation
- [x] Task 13: Continuous music + V5 platform architecture/migration/ops docs (depends on 9, 11–12)

<!-- Commit checkpoint: tasks 1-3 -->
<!-- Commit checkpoint: tasks 4-6 -->
<!-- Commit checkpoint: tasks 7-9 -->
<!-- Commit checkpoint: tasks 10-12 -->
<!-- Commit checkpoint: task 13 -->

### Task 1: Add MusicState schemas, settings flags, start-request `continuous`, sibling snapshot field, legacy context projection

Create/extend:

- `backend/app/adaptive_runtime_music_state_schemas.py` (prefer dedicated module)
- `backend/app/adaptive_runtime_continuation_settings.py` — `ADAPTIVE_CONTINUOUS_ENABLED`, `ADAPTIVE_ENGINE_CONTINUOUS_ENABLED` (collaboration-style truthy set)
- On `AdaptiveRuntimeContinuationV1` (`extra=forbid`): optional sibling `music_state`; keep required `context`; add `continuous: bool` echo
- Start request model: `{ expected_document_revision, mode, continuous?: bool }` default false
- Projection helper `project_legacy_runtime_context(music_state) -> AdaptiveRuntimeContextV1`

Enforce caps from Architecture decision 3; forbid note/pitch keys; `summary_digest` optional on schema (set by Task 3 helper). Never nest MusicState fields inside `context`.

Tests: `backend/tests/test_adaptive_runtime_music_state_schemas.py` — accept minimal MusicState; reject oversized rings / `events` key; projection fills legacy context; snapshot accepts sibling `music_state` + refuses unknown keys; start body accepts `continuous`.

LOGGING: INFO settings load `{adaptive_continuous_enabled, adaptive_engine_continuous_enabled}`; DEBUG schema reject field+code; never log full MusicState at INFO.

### Task 2: Extend pure window planner for continuous virtual timeline + virtual_bar anchor semantics

Update `backend/app/services/adaptive_runtime_continuation.py` (`plan_runtime_window` and/or `plan_continuous_window`) to accept `continuous`, `bar_count`, `virtual_bar`, playback-at-end / loop-wrap hints as plain ints/bools (no playback object import). When continuous and legacy would idle: return non-idle virtual target, warning `continuation_virtual_timeline`, virtual deadline tick input. Lock Architecture decision 4 anchor rules: never require `playback.bar > bar_count`. When `continuous=false`, preserve exact shipped past-end idle + existing tests.

Pure module: no FastAPI, SQLite, symbolic composer, or playback imports. Do not call `bar_start_tick` for virtual bars.

Tests: extend `backend/tests/test_adaptive_runtime_continuation.py` — legacy past-end idle unchanged; continuous virtual non-idle + warning; reserved/target lengths; anchor uses `virtual_bar` when `target_start > bar_count`.

LOGGING: DEBUG window numbers + continuous bool + virtual_bar + warning codes only.

Depends on Task 1 for warning/code constants if shared.

### Task 3: Implement MusicState update, compression, and guard helpers

Add `backend/app/services/adaptive_runtime_music_state.py` with pure functions:

- `update_music_state(...)` from prefix digest, themes, harmony labels, intensity/tension, orchestration fingerprint, runtime_state_id, virtual_bar
- `compress_music_state` / ring drop oldest
- `evaluate_continuous_guards(music_state) -> guard_flags + fallback_reorder hint + seed_bump`
- `music_state_summary_digest`
- helper to decide next `virtual_bar` advance (deterministic)

No I/O. Deterministic.

Tests: `backend/tests/test_adaptive_runtime_music_state.py` — rings drop at caps; repetition_pressure at threshold; duplicate digest detection input; digest stable for same canonical state.

LOGGING: DEBUG guard_flags only; never harmony labels at INFO.

Depends on Task 1.

### Task 4: Wire continuous mode into continuation service

Update `adaptive_runtime_continuation_service.py` + fallback module as needed:

- Latch `continuous` from start request; refuse `continuous=true` when flag off (`adaptive_continuous_disabled`); maintain uses latch (bodyless OK)
- Maintain path: update MusicState → plan window (Task 2 anchors) → fallback first → schedule model → guards on apply → buffer
- Virtual past-end: accompaniment/loop cycle ensures non-silence; late discard unchanged; never write composition/adaptive rows
- Snapshot includes `continuous`, sibling `music_state`, projected `context`
- Acceptance wording: trigger virtual path via end-of-score / loop / legacy idle — not `playback.bar > bar_count`

Tests: `backend/tests/test_adaptive_runtime_continuation_service.py` (extend) — continuous maintain under latency budget with blocked model; virtual non-idle at end; duplicate model discarded; fingerprints unchanged; flag-off refuse for `continuous=true` on start.

LOGGING: INFO maintain with continuous bool, virtual_bar, fallback_kind, source, job_status, guard_flags, late_discard_count; never buffer/events.

Depends on Tasks 2, 3.

### Task 5: Studio HTTP continuous fields + ai_agents forbid tokens

Extend `routers/adaptive_scores.py` start body + responses for `continuous` + sibling `music_state`. Map `adaptive_continuous_disabled`. Update `test_ai_agents_architecture.py` with Architecture decision 14 tokens (MusicState + continuation modules).

Tests: router happy path + disabled refuse; forbid import test fails if tokens appear under `ai_agents/`.

LOGGING: INFO route codes only.

Depends on Tasks 1, 4.

### Task 6: Optional Adaptive Engine continuous maintain proxy

When both flags on, add `POST /adaptive/session/{session_id}/continuous/maintain` (+ optional buffer GET) using existing bearer-or-loopback. Echo `continuous_enabled` / `engine_continuous_enabled` on session/status. Either flag off → `adaptive_engine_continuous_disabled`. Not on `/ready`. Do not auto-arm on engine start. No buffer on status websocket.

Tests: `backend/tests/test_adaptive_engine_continuous.py` — disabled refuse; enabled fake maintain returns fallback snapshot without writing score; path is singular `session`.

LOGGING: INFO engine continuous maintain codes; never tokens.

Depends on Tasks 4, 5.

### Task 7: Thin adaptive client SDK methods for engine continuous

Add thin methods to `clients/python` (`mukit_adaptive`) and `clients/typescript` (`@mukit/adaptive-music`) for continuous maintain (+ optional buffer) against `/adaptive/session/{id}/continuous/*`. When engine continuous disabled, return clear typed/HTTP errors. Do not import studio modules. Update client unit tests with mocked HTTP.

LOGGING: clients follow existing redaction (no tokens, no buffer dumps at info).

Depends on Task 6.

### Task 8: Adaptive-tab Continuous UX + MusicState inspect + past-end Tone mapping

Extend `AdaptiveContinuationPanel.jsx` / Adaptive tab + `adaptiveContinuation.js` / playback helpers:

- Continuous toggle wired to start latch (`continuous: true`); disabled reason when flag off
- MusicState summary (theme count, trajectory tail, guard flags, virtual bar) — not raw digests
- Extend `tickToSeconds` / `secondsBetweenTicks` for ticks `> durationTicks` with constant tempo (Architecture decision 9); Fill ahead schedules audible virtual buffer; do not use jam `liveScheduledEventIds`
- Honesty copy: session buffer ≠ durable score; fallback may be looping
- Opening Adaptive does not arm continuous

Frontend unit tests for form/state helpers + past-end tick mapping.

LOGGING: `console.debug` continuous/virtual_bar/source; no event dumps.

Depends on Task 5.

### Task 9: Part A deterministic acceptance tests

Add `backend/tests/test_continuous_music_acceptance.py` encoding Part A Goal acceptance (latency, virtual_bar at end/loop, MusicState caps/sibling, guard duplicate, fingerprint freeze, seed stability, flag off legacy, SPA mapping unit). Frontend unit coverage for Continuous toggle gating + past-end seconds.

LOGGING: assert log extras omit event arrays where project patterns exist.

Depends on Tasks 4–8.

### Task 10: Studio invariant helpers + migration ladder V5 sidecar checks

Extend `backend/tests/studio_acceptance/invariants.py` with:

- `assert_composition_source_of_truth(...)` — open as `composition.v2`; no `composition.v5`
- `assert_score_fingerprints_unchanged(...)`
- `assert_ardour_corruption_guards(client)` — refuse `session_xml` / path tricks as applicable

Extend `test_migration_ladder.py` to assert V5-related tables exist or open safely after upgrade (`adaptive_scores`, `rights_registry_entries`, content provenance, `execution_nodes`, universe/asset/spatial/performance/model_lab as present in Alembic). MusicState is session-only — **no Alembic** for MusicState.

LOGGING: n/a beyond existing.

### Task 11: V5 studio scenario matrix (10a core + 10b remaining)

Add `backend/tests/studio_acceptance/v5/` per Architecture decision 10.

**11a (core):** adaptive game, continuation, external context, film scoring, picture adapt, universe reuse, continuous music, cancel/node-failure. Shared `conftest.py` FAKE_BUNDLE + V5 flag overlay.

**11b (remaining):** personal, preference, distributed inference, WebGPU stub, expressive MIDI stub, conductor, spatial, Ardour roundtrip, asset pack, provenance, rights, Model Lab, ensemble.

Each scenario: HTTP-level or bridge to existing acceptance helper; SoT invariant; tiny/fake/fixture only; no torch/GPU/Ardour.

Depends on Tasks 9–10. Implement 11a before 11b; 11b may reuse 11a helpers.

### Task 12: Opt-in V5 Docker acceptance script

Add `scripts/v5_docker_acceptance.sh` mirroring v4 thin gate: fake env, continuous+rights+ardour fake flags, restart, reopen fingerprint. Document in `docs/testing.md` / V5 platform doc. Ensure `run_tests.sh` does **not** invoke it. Header states `RUN_DOCKER_ACCEPTANCE=1`.

Depends on Task 11.

### Task 13: Continuous music + V5 platform architecture/migration/ops docs

Write/update:

- `docs/continuous-music.md` (incl. virtual_bar anchor, SPA past-end audition, request latch)
- `docs/v5-platform.md` (Composer OS architecture, migration V1–V5 product surface, operations, CI vs opt-in, scenario matrix index)
- Pointers in continuation/engine/client/testing/README/AGENTS/DESCRIPTION/ARCHITECTURE/`.env.example`

Must state Definition of Done capabilities with honesty about fake CI vs opt-in hardware.

LOGGING: n/a (docs). Satisfies `Docs: yes` checkpoint.

Depends on Tasks 9, 11–12.

## Test Plan

| Case | Expect |
|------|--------|
| Legacy continuation past end (continuous off) | idle + `continuation_window_past_end` |
| Continuous at end / loop (virtual_bar) | non-idle; `continuation_virtual_timeline`; fallback first; `playback.bar <= bar_count` |
| Blocked model | maintain < latency budget; transport playing; late discard |
| MusicState sibling + caps | sibling on snapshot; oldest dropped; schema refuse oversize |
| Start latch | `continuous=true` on start; maintain uses latch |
| Repetition pressure | fallback reorder / seed bump; duplicate model discarded |
| Flag off `continuous=true` | `adaptive_continuous_disabled` |
| Engine path + flags off | `/adaptive/session/...`; `adaptive_engine_continuous_disabled` |
| SPA past-end ticks | seconds mapping beyond `durationTicks`; Fill ahead can schedule |
| Client SDK disabled | clear error; no studio import |
| Fingerprints | composition + adaptive score unchanged after maintains |
| Seed stability | same accompaniment pitches |
| ai_agents forbid | architecture test covers new tokens |
| Migration ladder | V1 plant → head → open V2; V5 tables safe; no composition.v5 |
| Each of 20 scenarios | fake green + SoT invariant |
| Cancel / node failure | V2 head unchanged; no cloud trust escalate |
| Rights refuse | train/reference hard-fail |
| Ardour guards | no `.ardour` write; session_xml refused |
| `run_tests.sh` | does not call v5 docker script |
| Opt-in docker | restart reopen fingerprint with fakes |

## Verification Gate

- [x] Continuous virtual timeline works via `virtual_bar` without writing V2 and without requiring `playback.bar > bar_count`
- [x] MusicState sibling + projected context; compressed + guarded
- [x] Fallback-first + late discard + non-stop transport proven
- [x] Engine continuous default off; singular session path; not on `/ready`
- [x] Client SDK thin methods when enabled
- [x] SPA past-end Tone mapping + Continuous UX does not auto-start
- [x] Part B matrix 10a+10b covers 20 scenarios + cross-cutting failures
- [x] Migration ladder + SoT invariant green
- [x] `v5_docker_acceptance.sh` opt-in only
- [x] Docs checkpoint complete
- [x] No `ROADMAP.md` edit from implementation
- [x] No `composition.v5`

## Open Questions

None blocking ship-1. Deferred: durable Commit of continuous buffer into `composition.v2`; multi-worker MusicState; default-on engine continuous; automatic theme borrowing from Musical Universe during continuous (explicit reuse stays manual); critic-driven continuous variation.

## Risks

| Risk | Mitigation |
|------|------------|
| Virtual tick map drifts from authored tempo map | Honesty warning + constant meter assumption; tests lock formula |
| SPA past-end seconds wrong vs backend deadline | Shared constant-tempo formula + unit parity tests |
| Studio matrix becomes unmaintainably slow | 10a/10b split; thin scenarios; reuse existing acceptance helpers; no torch |
| Dual flags confuse operators | Docs table + status endpoints echo both booleans |
| MusicState mistaken for playable score | Forbidden keys + UI honesty + SoT invariant |
| Playback clamp misunderstood as bug | Docs + acceptance explicitly state bar never exceeds bar_count |
