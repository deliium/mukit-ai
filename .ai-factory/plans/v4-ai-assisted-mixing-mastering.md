# Implementation Plan: V4 AI-Assisted Mixing and Mastering

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-25
Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-25)
Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-25)
- Scope: non-destructive **mix plans** over **completed neural stem sets** — inspectable gain / pan / EQ / compression / reverb-send / simple filtering / bounded automation, compiled from mix-analysis observations and natural-language intents, previewed, explicitly applied as a **new mix/master revision**, rejected, and undone — **never** rewriting source stem bytes, **never** mutating `composition.v2`, **never** inventing `composition.v4`, **never** writing `DATASET_ROOT`, **never** treating master targets as loudness guarantees, **never** hosting DAW plugins
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v4-ai-assisted-mix-analysis.md` (**shipped** — `mix.analysis.v1` measurements, observations, locus, optional advisory interpretation; read-only WAV; active heads; soft-stale banners)
  - `.ai-factory/plans/v4-stem-aware-neural-rendering.md` (**shipped** — stem sets, roles `piano|bass|strings|drums|vocals|other`, `absolute_audio_path`, supersede, fake multi-WAV)
  - `.ai-factory/plans/v3-optional-neural-audio-rendering.md` (**shipped** — mix job egress, quotas, path confinement)
  - `.ai-factory/plans/v2-expressive-v2-playback-and-mixing.md` (**shipped** — ephemeral Tone.js trim/pan/send; **session-only**; not this product)
  - `.ai-factory/plans/v2-safe-ai-preview-version-history-branches.md` (**shipped** — preview then explicit apply; reuse *shape* of head/parent, not composition snapshots)

## Roadmap Linkage
Milestone: "V4 AI-assisted mixing and mastering"
Rationale: Mix analysis already reports measurable stem observations, and users still cannot preview or apply production parameter changes as a new mix revision while source stems stay byte-identical. Add this as a **new unchecked** milestone in `.ai-factory/ROADMAP.md` during the docs task (every prior milestone is complete).

## Goal

Allow the user to turn a completed multi-stem render (and an optional `mix.analysis.v1` report) into an inspectable **MixPlan**, hear a before/after preview, and apply it as a **new mix/master revision** without altering original stem WAVs.

The user can:

1. Start from a completed `neural_audio_stem_set.v1` (active-head `complete` members) and optionally a persisted or in-session `mix.analysis.v1` report.
2. Request a suggestion in natural language, including the acceptance phrase "make bass less dominant" and the examples "Make the strings less dominant.", "Give the piano more space.", "Make the climax wider.", and "Reduce low-frequency masking."
3. Accept an analysis-driven suggestion (observation code + locus → structured ops) with the same inspectable parameter document.
4. Choose a master target **Dynamic**, **Streaming**, **Cinematic**, or **Demo** — a production preset/goal with `guarantee: false`.
5. Preview the resulting `mix.plan.v1`: every op shows target stem/role, unit, bounded before→after values, automation points, and a reason that cites an observation code or an intent code.
6. Compare before/after: a parameter diff against unity (or the current applied head) and a short A/B of dry-sum vs processed preview audio.
7. Edit numeric fields inside schema bounds before applying (the user keeps control; the compiler proposal is not applied silently).
8. **Apply** to persist the plan and write a **new** mix WAV (master bus included). Source stem paths stay read-only and their sha256 is unchanged.
9. **Reject** a preview (session + preview assets only).
10. **Undo** an apply by moving the working head to the parent revision. Prior revision WAVs remain; stems remain.

```text
Completed stem set (active heads) + optional mix.analysis.v1
        │
Deterministic intent compiler (NL phrase and/or observation codes + locus)
        │  optional LLM may only propose mix.intent.v1; same compiler emits ops
        │
mix.plan.v1  (ops + master target + stem sha256 pins; no PCM)
        │
Preview: parameter diff + capped dry/processed WAVs under MIX_PLAN_ROOT/previews
        │     Reject deletes preview assets only
        │
Apply: new mix_plan_revisions row + {revision_id}/mix.wav
        │     never write_bytes on neural stem paths
        │
Undo: head_revision_id → parent_revision_id (assets kept)
        │
UI: Mix assist subsection under NeuralAudioRenderPanel
```

**Acceptance one-liner:** The user can request "make bass less dominant", preview the suggested parameter changes, compare before/after, and apply them without altering the original stems.

Preferred applied document shape (illustrative, bounds enforced by schema):

```text
intent: reduce_dominance
target_role: bass
op: gain  before 0.0 dB  after -4.5 dB
reason: cites observation spectral_masking_proxy | phrase reduce_dominance
master_target: dynamic
guarantee: false
stem_sha256_prefix[bass]: unchanged after apply
output: new revision mix.wav (distinct path)
```

Not:

> A plugin chain rewrote the bass stem in place and the mix "sounds professional."

## Terminology lock

| Term | Meaning |
|------|---------|
| **Mix plan** | Versioned non-playable parameter document `mix.plan.v1`. No PCM, no note events |
| **Mix patch / op** | One bounded operation inside the plan: `gain`, `pan`, `eq`, `compressor`, `send`, `filter`, or `automation` of gain/pan/eq-gain/send |
| **Mix intent** | Structured `mix.intent.v1` (`intent_code`, roles, polarity, optional locus). The only object an optional language model may propose |
| **Suggestion** | Compiler output that names stem ids/roles, parameter before→after, locus, and reason. Distinct from mix-analysis prose `suggested_action` (that string is display-only and is never executed) |
| **Preview** | Session plan + parameter diff + optional short dry/processed WAVs. Not the applied head |
| **Apply** | Persist plan JSON + write a **new** mix WAV revision and move `head_revision_id` |
| **Reject** | Drop the preview and delete its preview assets. Does not move the head and does not delete applied revisions |
| **Undo** | Point `head_revision_id` at `parent_revision_id`. Does not delete WAVs or stems |
| **Master target** | Named preset/goal: `dynamic`, `streaming`, `cinematic`, `demo`. Field `guarantee` is always `false` |
| **Mix revision** | Durable row + output WAV under `MIX_PLAN_ROOT`. A new id every apply |
| **Original stems** | Neural stem WAVs under `NEURAL_AUDIO_RENDER_ROOT`. Inputs only. sha256 pinned on the plan and re-checked before write |
| **Dry sum** | Unity-gain sum of the selected stems (no plan ops, still through the chosen target's documented master ceiling only when that target is the baseline — see Part E). Preview "before" is this dry sum when no head exists |
| **Tone mixer** | Existing ephemeral `playbackMixerControls` for composition audition. Out of scope; never persisted as a mix plan |
| **Soft-stale** | Banner when live stem-set fingerprint or pinned stem hashes diverge from the plan/revision. Banner only; never auto-apply |

## Non-goals (v1)

- Rewriting, truncating, or replacing source stem WAVs or the original neural mix-job WAV
- DAW plugin hosting (VST/AU/LV2), clip launchers, or a full automation-lane editor
- Replacing the ephemeral Tone.js composition mixer or FluidSynth export
- Automatic apply without an explicit Apply of the inspected plan
- Treating Dynamic/Streaming/Cinematic/Demo as LUFS, true-peak, or streaming-platform certification (`guarantee: false`; outcome may be `master_target_unverified`)
- Claiming ITU-R BS.1770 laboratory compliance (output `lufs_approx` / sample-peak only when the same honesty rules as mix analysis allow)
- Convolution-reverb IR libraries, multiband mastering, mid/side matrices beyond a single width/pan op, linear-phase EQ, or reference-track matching
- Executing English `suggested_action` strings from `mix.analysis.v1` as instructions
- New `AiOperation` enum values (optional language help returns `mix.intent.v1` only, or a soft warning)
- Analyzing or mixing recovery/Demucs assets
- Mix-only plans with no `stem_set_id`
- Putting mix-plan ids on `CompositionV2NoteEvent` or inventing `composition.v4`
- Writing `DATASET_ROOT` or auto-ingesting revisions into the symbolic corpus
- New Composer workspace tab (subsection under `NeuralAudioRenderPanel`)
- Async render jobs (sync with byte/duration/timeout caps; jobs deferred)
- Composition undo/redo coupling (mix head is a separate graph)
- Mandatory numpy/scipy/torch in the default image (fake + pure-Python path must pass CI)
- Real-time metering inside the Tone transport

## Approach Evaluation (locked)

### Part A — Where the mix lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Persist the Tone.js track mixer** | UI already has trim/pan/send | Session-only by contract; not stem-hashed; would mutate the wrong domain if saved onto V2 | **Reject** |
| **B. New `mix.plan.v1` over neural stem WAVs** | Matches analysis locus, preserves stems, yields a new mix revision | New schema, store, bounce | **Accepted** |
| **C. Bake gain into note velocities** | Symbolic and cheap | Destroys "preserve stems"; ignores audio masking | **Reject** |

**Locked:** Contracts in `backend/app/mix_plan_schemas.py`. Services under `backend/app/services/mix_plan/`. Router `backend/app/routers/mix_plan.py` mounted from `main.py` beside mix analysis. Do not extend `playbackMixerControls.js` or `composition.v2`.

### Part B — Non-destructive output

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Overwrite stem or mix-job WAVs** | One file | Violates requirements 2, 8, and analysis "read-only inputs" | **Reject** |
| **B. Parameter plan only; no new audio** | Tiny | Fails "new mix/master revisions" and audible before/after | **Reject** as the apply result |
| **C. New revision WAV + immutable plan JSON; stems read-only** | Hash-testable; undo keeps prior WAVs | Disk quota | **Accepted** |

**Locked:** Apply writes only under `MIX_PLAN_ROOT` (`{project_id}/{revision_id}/plan.json` and `{project_id}/{revision_id}/mix.wav`). Open stems with neural `absolute_audio_path` and read (`wave` / existing mix-analysis decode). **Never** `write_bytes` on stem or neural mix-job paths. Before opening outputs, record per-stem sha256; after bounce, hashes must match. Preview files live under `{project_id}/previews/{preview_id}/` and are the only files Reject may delete.

### Part C — Suggestion source

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. LLM emits free DSP numbers** | Flexible prose | Unbounded, uninspectable, flakes CI | **Reject** |
| **B. Execute observation `suggested_action` English** | Reuses analysis copy | Unstable parser; prose is not a contract | **Reject** |
| **C. Deterministic compiler from `mix.intent.v1` and from observation code + locus** | Testable AC phrase; same ops the UI shows | Limited vocabulary | **Accepted** |

**Locked:** `compile_intent` and `compile_observations` are pure functions. They emit ops already clamped to schema bounds, each with `reason.kind` of `intent` or `observation` plus ids/codes. Optional language model (fake path under `LLM_FAKE_MODE` or `MIX_PLAN_FAKE_MODE`) may return **only** `mix.intent.v1`. The compiler, not the model, chooses numeric values. Unwired model → warning `intent_llm_unavailable`; the deterministic phrase table still answers. No new `AiOperation`.

Intent table (v1, case-insensitive, punctuation-insensitive):

| Phrase pattern | `intent_code` | Ops (caps applied in Task 3) |
|----------------|---------------|------------------------------|
| "make {role} less dominant" / "make the {role} less dominant" (AC: bass) | `reduce_dominance` | `gain` on that role, negative dB |
| "give the {role} more space" (piano) | `more_space` | modest `gain` up on that role + `send` increase; small `eq` dip on other stems that share a masking locus when a report is attached |
| "make the climax wider" | `wider_climax` | `pan` or width-toward-stereo on the master bus over the climax window when the report/section locus has bars or seconds; otherwise the last third of the stem duration with warning `section_unspecified` |
| "reduce low-frequency masking" | `reduce_lf_masking` | `filter` high-pass or low-shelf cut on non-bass roles inside the masking locus when a report contains `spectral_masking_proxy` or `lf_buildup`; without a report, low-shelf on non-bass active heads plus warning `analysis_report_absent` |

Unknown phrase → `422 mix_plan_intent_unrecognized` (no partial apply). Role tokens must be `NeuralAudioStemRole` values present on the active heads; unknown role → `422 mix_plan_role_unavailable`.

Observation → ops (used by "Suggest from analysis", also merged when the NL request includes `report_id`):

| Observation code | Op |
|------------------|----|
| `clipping_detected`, `peak_hot`, `low_headroom` | negative `gain` and/or a gentle `compressor` on locus stems |
| `stereo_imbalance` | `pan` toward center, magnitude capped |
| `lf_buildup` | `filter` high-pass on locus stems that are not the bass role |
| `spectral_masking_proxy` | `eq` cut inside `freq_hz_low/high` on the second role in the locus (never both stems with the same cut) |
| `section_loudness_flat` | candidate only; **not** auto-merged into an unrelated NL request. Shown as its own suggestion chip |

Every op copies `stem_ids`, `stem_roles`, `source_track_ids`, and time/freq fields from the locus when present. Bars are copied from the report; this feature does not invent bars.

### Part D — DSP stack

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require a mastering library / torch** | Richer | Breaks lean CI; invites a DAW | **Reject** |
| **B. Pure Python biquad/dynamics + optional numpy/scipy; fake bounce for CI** | CI green; honest `dsp_backend` | Modest sound quality | **Accepted** |
| **C. Browser-only Web Audio as the archive master** | Instant | Apply result would depend on the client and fail headless AC | **Reject** as the durable render |

**Locked:**
- `dsp_backend`: `stdlib` (always), `numpy_scipy` (when extras from `backend/requirements-mix-analysis.txt` import), `fake`.
- Fake (`MIX_PLAN_FAKE_MODE=1`): still returns a full valid plan; writes a deterministic tiny WAV whose PCM depends on the plan digest (not a quality render). Tests assert a **new path** and **unchanged stem hashes**, not timbral excellence.
- Real `stdlib` path implements the op list on decoded PCM: constant-power pan, gain, biquad peaking/shelf, one-pole or biquad HPF/LPF, feed-forward compressor with bounded attack/release, send into a shared short feedback-delay reverb bus labeled `algorithmic_room` (not a sampled room). Automation breakpoints (max 32 per op) are seconds, linearly interpolated, sorted.
- Decode via the mix-analysis WAV reader. Do not fork a second PCM stack.
- Caps: `MIX_PLAN_MAX_AUDIO_SECONDS`, `MIX_PLAN_MAX_TOTAL_INPUT_BYTES`, `MIX_PLAN_JOB_TIMEOUT_SECONDS` → 422/504 stable codes.
- Preview bounce may use a shorter cap (`MIX_PLAN_PREVIEW_MAX_SECONDS`, default 30) and must say so on the preview (`preview_truncated: true` when the stem is longer). Apply bounce uses the full stem duration up to `MIX_PLAN_MAX_AUDIO_SECONDS`.

### Part E — Master targets

Targets are documents in code (`mix.master_target.v1`), not user-uploaded curves.

| Id | UI label | Goal (preset, not a guarantee) |
|----|----------|--------------------------------|
| `dynamic` | Dynamic | Preserve crest: light compressor ceiling, no loudness push, headroom goal described as sample-peak below 0 dBFS |
| `streaming` | Streaming | More even level: slightly stronger bus compressor/limiter ceiling and a **labeled** loudness aim (document −14 LUFS as a goal string). If `lufs_approx` cannot be measured, warning `master_target_unverified` |
| `cinematic` | Cinematic | Wider pan/width defaults, higher default send, lighter limiting |
| `demo` | Demo | Conservative gain cuts, mild high-pass on non-bass, moderate ceiling — a sketch, not a release master |

**Locked:** The plan stores `master_target` and `guarantee: false`. UI copy states they are goals. Compiler may add **master-bus** ops tagged `bus: master` in addition to stem ops. Applying a target never hides stem ops. Changing the target recompiles and requires a new preview before Apply (stale preview digest → `409 mix_plan_preview_stale`).

### Part F — Preview, apply, reject, undo

| Step | Persistence | Audio |
|------|-------------|-------|
| Preview | Response body. Optional SQLite row **not** required. Preview files only | `dry.wav` + `processed.wav` when `include_audio_preview=true` (AC tests turn this on) |
| Reject | `DELETE /mix-plan/previews/{id}` removes preview files. 404 if unknown. 409 if the id was already applied | Does not touch revision WAVs or stems |
| Apply | Requires `preview_id` + plan digest match. Inserts revision, parent = previous head or null, sets head | New `mix.wav`. Stems re-hashed; mismatch → `409 mix_plan_stem_changed` and no output file |
| Undo | `POST /mix-plan/revisions/{id}/undo` moves head to parent. `409 mix_plan_undo_empty` when parent is null | No deletes |

**Locked:** The client may submit an edited plan on Apply. Server re-validates bounds and recomputes the digest; it does not bounce a plan that failed validation. Apply always allocates a new revision id (repeat apply of the same numbers is a new revision, not an in-place overwrite). One head per `(project_id, stem_set_id)`.

Parameter comparison payload (always, even when audio preview is off):

```text
changes[]: { op_id, type, target, unit, before, after, reason }
```

`before` is unity defaults (0 dB, pan 0, send 0, filter bypassed) when no head exists; otherwise the head plan's values for the same op identity.

### Part G — UI shell

**Locked:** `MixAssistPanel.jsx` subsection under `NeuralAudioRenderPanel`, next to Mix Analysis. No new workspace tab. Local React state (same pattern as `MixAnalysisPanel`), excluded from project autosave / composition undo. Controls: report selector (session report or saved id), suggestion chips, NL field, master-target radios, parameter table (editable numbers), Preview / Reject / Apply / Undo, A/B play of preview WAVs via existing audio element helpers (not a new `playbackSource`), soft-stale banner. Empty state explains that a completed stem set is required.

### Part H — Store

**Locked:** Alembic `20260925_0009` revises `20260924_0008`. Table `mix_plan_revisions` with CHECKs (`status` in `applied` only for this table — previews are files + client; do not store preview blobs in SQLite), indexes on `(project_id, stem_set_id, created_at)`, unique partial head. Columns: id, project_id, stem_set_id, parent_revision_id, master_target, plan_relpath, mix_relpath, source_stem_set_fingerprint, stem_sha256_pins_json (prefixes + lengths only, not PCM), dsp_backend, byte_size, sha256_prefix of the **output** mix, created_at, is_head. `cleanup_project_mix_plans(project_id)` wired in `project_store.delete_project` beside `cleanup_project_mix_analysis` (best-effort try/except + WARN). Path confinement copied from `mix_analysis_store`. Quota `MIX_PLAN_MAX_REVISIONS_PER_PROJECT`.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Path | Reuse |
|------|------|-------|
| Analysis report + locus + observation codes | `mix_analysis_schemas.py`, `services/mix_analysis/observations.py` | Input to the compiler. Do not parse `suggested_action` |
| Active-head selection | `services/mix_analysis/active_stems.py` | Same stem selection rule |
| Read-only WAV open + sha256 pin | `services/mix_analysis/pipeline.py`, `neural_audio_render_store.absolute_audio_path` | Read path + immutability check pattern |
| Stem roles | `neural_audio_schemas.NeuralAudioStemRole` | Intent role tokens |
| Optional DSP extras | `requirements-mix-analysis.txt` | Do not add a second extras file unless a proven gap appears |
| Fake WAV discipline | `tests/fixtures/audio/mix_analysis/builders.py`, `MIX_ANALYSIS_FAKE_MODE` | Mirror fixture builders + fake bounce |
| Panel host | `NeuralAudioRenderPanel.jsx`, `MixAnalysisPanel.jsx` | Subsection sibling; local state |
| API style | `routers/mix_analysis.py`, `musicApi.js` | Error `code`/`message`/`details`, logging of ids and counts |
| Project-delete GC | `project_store.delete_project` | Add mix-plan cleanup beside mix analysis |
| Ephemeral mixer | `playbackMixerControls.js` | **Boundary:** do not persist, do not import into the compiler |

### Gaps (this plan closes)

| Gap | Impact |
|-----|--------|
| No `mix.plan.v1` / intent / revision contract | Parameter changes are not inspectable data |
| No compiler from phrases or observation codes | AC phrase cannot preview stable numbers |
| No non-destructive bounce or revision WAV | Apply would either do nothing or overwrite stems |
| No preview/apply/reject/undo head | User cannot keep control |
| No master-target presets with an explicit non-guarantee | Requirement 7 unmet |
| No Mix assist UI or before/after compare | AC journey has no surface |
| No stem-hash regression test or mix-plan fixtures | "Original stems preserved" is unverified |
| No docs / ROADMAP / env template | Acceptance incomplete |

## Commit Plan

- **Commit 1** (tasks 1–3): `feat(mix-plan): schemas, revision store, intent and analysis compilers`
- **Commit 2** (tasks 4–6): `feat(mix-plan): non-destructive bounce, master targets, preview/apply API`
- **Commit 3** (tasks 7–8): `feat(mix-plan): Mix assist preview, compare, and undo UI`
- **Commit 4** (tasks 9–11): `test(mix-plan): stem immutability, fixtures, docs`
- **Commit 5** (tasks 12–13): `feat(mix-plan): head-relative diff, stereo pan, analysis suggestion chips`

## Tasks

### Phase 1: Contracts, store, compilers

- [x] Task 1: Define `mix.plan.v1` DTOs and error codes
  Deliverable: Pydantic models (`extra="forbid"`) for `mix.intent.v1`, `mix.op.v1` (types gain, pan, eq, compressor, send, filter, automation), `mix.master_target.v1` (`guarantee: Literal[false]`), `mix.plan.v1` (ops, changes/diff shape, stem pins, source report id, fingerprints, `dsp_backend`), preview request/response, apply request/response, reject, undo, revision list/get. Stable codes: `mix_plan_intent_unrecognized`, `mix_plan_role_unavailable`, `mix_plan_stem_set_incomplete`, `mix_plan_not_found`, `mix_plan_preview_stale`, `mix_plan_stem_changed`, `mix_plan_undo_empty`, `mix_plan_quota_exceeded`, `mix_plan_input_too_large`, `mix_plan_timeout`, `mix_plan_invalid_request`, `intent_llm_unavailable`, `master_target_unverified`, `section_unspecified`, `analysis_report_absent`. HTTP map mirrors mix analysis (soft warnings stay on 200 bodies, hard failures 422/404/409/504). Parameter bounds documented as schema ge/le (gain_db −24..+12, pan −1..1, send 0..1, eq gain −12..+12, compressor ratio 1..8, automation points ≤ 32, ops ≤ 64). Frontend mirror `frontend/src/utils/mixPlanUi.js` with JSDoc for codes, target ids, and diff row shape (used by unit tests).
  LOGGING: DEBUG accept/reject field keys only. Never log PCM, full plans, NL text, or parameter value arrays at INFO. INFO may log schema name + op type counts on successful validation.
  Files: `backend/app/mix_plan_schemas.py`, `frontend/src/utils/mixPlanUi.js`

- [x] Task 2: Alembic revision store and project-delete GC
  Deliverable: Migration `20260925_0009_mix_plan_revisions` (down_revision `20260924_0008`) creating `mix_plan_revisions` as specified in Part H. `mix_plan_settings.py` reads `MIX_PLAN_ROOT` (default beside project DB: `…/mix_plans`), fake flag, caps, quota. Store module: path confinement, write plan JSON, allocate revision, set/clear head, get/list by project, delete preview tree, `cleanup_project_mix_plans`. Wire cleanup in `project_store.delete_project` beside mix-analysis cleanup (try/except, WARN, continue). Responses never embed PCM.
  LOGGING: INFO migration id. INFO persist with revision_id, project_id, stem_set_id, byte_size, output sha256_prefix, master_target, dsp_backend. INFO/WARN cleanup file and row counts. Never plan JSON or WAV bytes at INFO.
  Files: `backend/app/db/alembic/versions/20260925_0009_mix_plan_revisions.py`, `backend/app/mix_plan_settings.py`, `backend/app/services/mix_plan_store.py`, `backend/app/services/project_store.py`
  Depends on: Task 1

- [x] Task 3: Deterministic intent and observation compilers
  Deliverable: Pure `compile_phrase(text, active_roles) -> mix.intent.v1` covering the acceptance phrase and the four example sentences (role capture for bass, strings, piano). Pure `compile_plan(intent, observations|None, master_target) -> mix.plan.v1` implementing Part C tables and Part E master-bus ops, with warnings for missing report/section. Reject unknown phrases and missing roles with the stable codes. Do not read `suggested_action`. Optional `propose_intent_llm` stub: under fake mode, map the same phrase table to intent JSON; otherwise append warning `intent_llm_unavailable` and keep the deterministic intent. Unit-level tests can wait for Task 9, but this task must leave the functions importable without audio files.
  LOGGING: INFO intent_code, role count, observation codes consumed, op_count, warning codes. DEBUG op types. Never log the raw phrase at INFO (DEBUG truncated length + char_count is allowed). Never log note events.
  Files: `backend/app/services/mix_plan/intent.py`, `backend/app/services/mix_plan/compile.py`, `backend/app/services/mix_plan/targets.py`
  Depends on: Task 1

### Phase 2: Bounce and HTTP

- [x] Task 4: Read-only bounce engine and preview assets
  Deliverable: Package functions that select active-head stems (reuse `select_active_head_stems`), hash pins, decode with the mix-analysis reader, render ops from Part D, and write **only** under the preview or revision directory. Immutability helper returns before/after stem sha256 and raises `mix_plan_stem_changed` if they differ (and if any code path attempted a write, the test in Task 9 must fail). Fake mode writes digest-derived tiny PCM without numpy. Real mode flags `dsp_backend`. Preview honors `MIX_PLAN_PREVIEW_MAX_SECONDS` and sets `preview_truncated`. Dry render uses unity stem ops so A/B is the same decoder. Reverb bus labeled `algorithmic_room`. In-memory sample caps enforced before allocate.
  LOGGING: INFO stem_set_id, stem count, duration_s, sample_rate, op_count, dsp_backend, output byte_size, duration_ms, preview_id or revision_id. WARN on truncate and skip codes. Never PCM samples or full automation point lists at INFO.
  Files: `backend/app/services/mix_plan/render.py`, `backend/app/services/mix_plan/pipeline.py`
  Depends on: Tasks 2, 3

- [x] Task 5: Master-target application and outcome honesty
  Deliverable: Applying a target adds the documented master-bus ops and a `master_outcome` block on preview/apply responses: `target_id`, `guarantee: false`, measured sample-peak of the **output** when DSP ran, optional `lufs_approx` only if the existing mix-analysis metric helper can compute it, otherwise `master_target_unverified`. Streaming's −14 LUFS aim is a string goal, never overwritten with a fabricated number. Changing target invalidates the previous preview digest.
  LOGGING: INFO master_target, outcome warning codes, output peak when numeric. Never claim certification in log messages.
  Files: `backend/app/services/mix_plan/targets.py`, `backend/app/services/mix_plan/pipeline.py`
  Depends on: Tasks 3, 4

- [x] Task 6: Preview, reject, apply, undo, download API
  Deliverable: Router prefix `/mix-plan`:
  - `POST /mix-plan/preview` — body: project_id, stem_set_id, phrase and/or report_id and/or explicit intent, master_target, include_audio_preview, optional edited-op seed. Returns plan, `changes[]`, warnings, preview_id, digest, audio URLs when requested.
  - `DELETE /mix-plan/previews/{id}` — reject.
  - `POST /mix-plan/apply` — preview_id, digest, plan (possibly edited). 409 on digest/hash/stale target. Returns revision meta + head id.
  - `POST /mix-plan/revisions/{id}/undo` — new head = parent.
  - `GET /mix-plan/revisions?project_id=&stem_set_id=` and `GET /mix-plan/revisions/{id}`.
  - `GET /mix-plan/revisions/{id}/audio` and `GET /mix-plan/previews/{id}/audio?which=dry|processed` — path-confined WAV download, no directory listing.
  Domain errors mapped like mix analysis. Register router in `main.py`. Client wrappers in `frontend/src/api/musicApi.js` (no behavior UI yet).
  LOGGING: INFO method, preview_id/revision_id, stem_set_id, http_status, duration_ms, op_count. WARN on 4xx codes. ERROR unexpected with error_type only. Never log phrase body or PCM.
  Files: `backend/app/routers/mix_plan.py`, `backend/app/main.py`, `frontend/src/api/musicApi.js`
  Depends on: Tasks 4, 5

### Phase 3: Mix assist UI

- [x] Task 7: Preview, inspect, compare, apply, reject, undo
  Deliverable: `MixAssistPanel.jsx` hosted from `NeuralAudioRenderPanel` when a stem set id is available. Loads suggestion chips by calling preview compile with the current mix-analysis report's observations (or asks the API to compile from `report_id` — prefer one server preview call so numbers match). NL field submits the phrase. Master-target radios show the goal sentence and "Not a guarantee". Parameter table lists every change (type, target role/stem, before, after, unit, reason code) and allows numeric edits inside the bounds from `mixPlanUi.js`. Preview refreshes the diff and enables A/B audio when URLs return. Reject clears local preview and calls DELETE. Apply sends the edited plan + digest. Undo calls the undo route and shows the restored head's op summary. Errors render `detail.code` with a short message. Panel state stays out of `musicStore` composition persistence (local state is enough). Buttons disabled while a request is in flight.
  LOGGING: browser `console.debug` only for preview_id / revision_id / codes — no phrases, no PCM.
  Files: `frontend/src/components/MixAssistPanel.jsx`, `frontend/src/components/NeuralAudioRenderPanel.jsx`, `frontend/src/utils/mixPlanUi.js`
  Depends on: Tasks 1, 6

- [x] Task 8: Soft-stale banner
  Deliverable: When the live stem-set fingerprint (existing neural fingerprint helper used by mix analysis) or a returned `stem_changed` warning differs from the revision pins, show a banner. Do not auto-preview or auto-apply. Apply stays available only after a fresh preview if the server would 409.
  LOGGING: none beyond existing UI error path (no console noise on match).
  Files: `frontend/src/components/MixAssistPanel.jsx`, `frontend/src/utils/mixPlanUi.js`
  Depends on: Task 7

### Phase 4: Tests, fixtures, docs

- [x] Task 9: Backend tests and audio fixtures
  Deliverable:
  - `backend/tests/fixtures/audio/mix_plan/builders.py` + `README.md` generating deterministic short WAVs (bass, piano, strings; distinct frequencies; stereo or dual-mono documented) via `wave`, same pattern as `fixtures/audio/mix_analysis/builders.py`. Tests write them under `tmp_path` and register them as a fake completed stem set (follow `test_mix_analysis.py` / neural fake stem-set helpers — do not point the store at the fixture directory in place if that would allow writes beside the fixtures).
  - `backend/tests/test_mix_plan.py` covering: schema extra-field reject; phrase "make bass less dominant" yields a bass `gain` with `after < before`; strings / piano / climax / LF-masking examples; observation `spectral_masking_proxy` produces an eq op citing that code; unknown phrase 422; master target `guarantee` is false and streaming without LUFS adds `master_target_unverified` in fake mode; preview then apply leaves each source stem sha256 identical and creates a new mix path; second apply does not overwrite the first file; reject removes preview files and leaves stems; undo restores parent head and leaves both mix WAVs; stem hash flip between preview and apply returns 409 and writes no revision WAV; path escape rejected; project delete removes `MIX_PLAN_ROOT` for that project.
  - Fake mode is the default for this test module so CI does not need scipy. One stdlib render test may skip if decoding the fixture fails, but the sha256 immutability test must run in fake mode without skip.
  LOGGING: tests may assert INFO records contain revision_id and do not contain raw PCM marker strings; no new logs of phrases.
  Files: `backend/tests/test_mix_plan.py`, `backend/tests/fixtures/audio/mix_plan/builders.py`, `backend/tests/fixtures/audio/mix_plan/README.md`
  Depends on: Task 6

- [x] Task 10: Frontend unit tests for the diff and targets
  Deliverable: `frontend/src/utils/mixPlanUi.test.js` (node:test) for diff-row display formatting, bound clamping of edited after-values, master-target labels including a non-guarantee string, and soft-stale predicate (fingerprint mismatch → banner flag). If Task 7 extracted no pure helpers, add them to `mixPlanUi.js` rather than mounting the panel in this task.
  LOGGING: not applicable (pure functions). Do not add console logs in the helpers.
  Files: `frontend/src/utils/mixPlanUi.js`, `frontend/src/utils/mixPlanUi.test.js`
  Depends on: Tasks 7, 8

- [x] Task 11: Documentation, env template, roadmap milestone
  Deliverable: `docs/ai-assisted-mixing.md` describing the journey, contracts, endpoints, master-target honesty, stem immutability, fake mode, caps, and the verification commands. Cross-link from `docs/mix-analysis.md` (analysis suggests; mix plan applies) and `docs/neural-audio-rendering.md`. Add `MIX_PLAN_*` keys to `.env.example` (commented defaults). Add the unchecked milestone to `.ai-factory/ROADMAP.md`. Update `AGENTS.md` and `.ai-factory/ARCHITECTURE.md` entries for the router, schemas, store, and panel (structure only; do not invent a new composition version). Docs must state this is not a professional mastering suite and not a DAW.
  LOGGING: document the INFO fields and the ban on PCM / full prompts in the operations section of the new doc (short).
  Files: `docs/ai-assisted-mixing.md`, `docs/mix-analysis.md`, `docs/neural-audio-rendering.md`, `.env.example`, `.ai-factory/ROADMAP.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`
  Depends on: Tasks 6, 9

### Phase 5: Head-relative diff and analysis suggestions

Checked tasks above stay as implemented. This phase covers the gaps `/aif-improve` found: `before` is always unity, `stereo_imbalance` is pan `0 → 0`, send ops are unlabeled, and Mix assist never sends `report_id` or `observation_codes`.

- [x] Task 12: Head-relative before values, signed stereo pan, reverb label
  Deliverable: After `compile_plan` in `preview_mix`, if `head_revision_id` is set for `(project_id, stem_set_id)`, load that revision with `load_revision_plan` and set each compiled op’s `before` from the head op with the same `op_id`. No matching head op keeps the compiler’s unity default (0 dB, pan 0, send 0, compressor ratio 1, filter cutoff 0). Rebuild `changes[]` with `change_from_op` so the table matches. `structure_digest` stays identity-only (it already ignores `before` and `after`), so numeric edits still apply against the same preview.
  Stereo: `MixPlanObservationRef` gains optional `measurement_value` (signed dB, default null). `_collect_observations` copies `stereo_lr_rms_balance_db` from the report measurement whose locus overlaps the observation’s stems (left RMS dBFS minus right RMS dBFS, as `metrics.py` defines it). Do not read `suggested_action`. The pan op’s `after` moves toward center: opposite the sign of `measurement_value`, magnitude `min(0.35, abs(value) / 24)`, clamped to pan −1..1 (positive balance is left-heavy, so `after` is positive). If `measurement_value` is missing, emit no pan op and add warning `stereo_imbalance_unsigned` instead of a `0 → 0` row.
  Send: `MixPlanOp.reverb_id` is optional and, when `type == "send"`, the model validator sets `algorithmic_room` if it was omitted, so existing `plan.json` files still load. New send ops from `_send_op` set that id. The bounce stays the current short feedback delay.
  Tests in `backend/tests/test_mix_plan.py`: a second preview after Apply shows `before` equal to the head op’s `after` for the same `op_id`; a stereo observation with `measurement_value > 0` yields `after > 0` and `abs(after) <= 0.35`; a stereo observation with no measurement adds `stereo_imbalance_unsigned` and no pan op; every send op has `reverb_id == "algorithmic_room"`. One sentence in `docs/ai-assisted-mixing.md` states that `before` follows the head when one exists and that the send bus is `algorithmic_room`.
  LOGGING: INFO on head overlay with `revision_id` and `matched_op_count` only. WARN when `stereo_imbalance_unsigned` is attached. Never log `measurement_value`, before/after arrays, or PCM at INFO.
  Files: `backend/app/mix_plan_schemas.py`, `backend/app/services/mix_plan/compile.py`, `backend/app/services/mix_plan/pipeline.py`, `backend/tests/test_mix_plan.py`, `docs/ai-assisted-mixing.md`
  Depends on: Task 6

- [x] Task 13: Mix assist report selector and section-loudness chip
  Deliverable: `MixAnalysisPanel` reports the in-session report id upward through a callback held in `NeuralAudioRenderPanel` local state (not `musicStore`, not composition autosave). `MixAssistPanel` adds a report selector: “Session report” when that id is set, plus saved rows from `listMixAnalysisReports`. Preview sends `report_id` when a report is selected, in the same `previewMixPlan` call as the phrase, so observation ops stay server-compiled. A separate chip “Section loudness” is enabled only when a report is selected; its preview body sets `observation_codes: ["section_loudness_flat"]` and omits `phrase`, so unrelated phrase ops are not merged. Phrase chips stay phrase-only. Empty selector copy states that analysis suggestions need a saved or session report.
  Extract a pure `mixAssistPreviewRequest` in `frontend/src/utils/mixPlanUi.js` and cover it in `mixPlanUi.test.js`: phrase chips do not set `observation_codes`; the section chip sets only `section_loudness_flat` and no phrase; a selected report id is copied through. Backend test: `POST /mix-plan/preview` with a persisted report that contains `section_loudness_flat` and `observation_codes: ["section_loudness_flat"]` returns an op citing that code and does not require a phrase. Add one sentence to `docs/ai-assisted-mixing.md` describing the report selector and the section-loudness chip.
  LOGGING: browser `console.debug` may include `report_id` and observation-code count. Do not log the phrase. Server logs stay on the existing preview INFO line (method, ids, op_count).
  Files: `frontend/src/components/MixAssistPanel.jsx`, `frontend/src/components/MixAnalysisPanel.jsx`, `frontend/src/components/NeuralAudioRenderPanel.jsx`, `frontend/src/utils/mixPlanUi.js`, `frontend/src/utils/mixPlanUi.test.js`, `backend/tests/test_mix_plan.py`, `docs/ai-assisted-mixing.md`
  Depends on: Task 7

## Environment

| Key | Purpose |
|-----|---------|
| `MIX_PLAN_ROOT` | Preview + revision files (never `DATASET_ROOT`, never the neural stem root) |
| `MIX_PLAN_FAKE_MODE` | Digest WAV + fake intent path for CI |
| `MIX_PLAN_MAX_AUDIO_SECONDS` | Apply decode cap |
| `MIX_PLAN_PREVIEW_MAX_SECONDS` | Preview bounce cap |
| `MIX_PLAN_MAX_TOTAL_INPUT_BYTES` | Sum of stem bytes |
| `MIX_PLAN_MAX_REVISIONS_PER_PROJECT` | Apply quota |
| `MIX_PLAN_JOB_TIMEOUT_SECONDS` | Sync wall clock |

Document stubs in `.env.example` (Task 11). Default fake mode off. Tests set `MIX_PLAN_FAKE_MODE=1`.

## Coupling risks

1. `write_bytes` or truncate on neural stem paths during preview/apply
2. Reusing superseded stems (must call the existing active-head selector)
3. Saving mix ops into `composition.v2`, the Tone mixer, or composition undo
4. Parsing `suggested_action` prose or letting an LLM choose unbounded numbers
5. Adding `AiOperation` values or a new Composer tab
6. Reporting Streaming/Cinematic results as certified loudness
7. Logging phrases, PCM, or full plans at INFO
8. Requiring scipy in core requirements
9. Undo deleting the current mix WAV or the parent (breaks compare-after-undo)
10. Reject deleting applied revisions
11. Orphaned `MIX_PLAN_ROOT` trees if project-delete GC is skipped
12. Writing `DATASET_ROOT` or inventing `composition.v4`
13. Diverging from mix-analysis bar policy (copy locus bars; never invent them)
14. Treating the ephemeral playback mixer as the apply target

## Verification

```bash
cd backend && MIX_PLAN_FAKE_MODE=1 MIX_ANALYSIS_FAKE_MODE=1 NEURAL_AUDIO_FAKE_MODE=1 LLM_FAKE_MODE=1 \
  ../.venv/bin/python -m pytest tests/test_mix_plan.py tests/test_mix_analysis.py -q
node --test frontend/src/utils/mixPlanUi.test.js
```

AC manual (fake modes acceptable for the parameter journey; stdlib bounce when extras or the pure-Python renderer is on): render or seed piano/bass/strings stem set → open Mix assist → enter "make bass less dominant" → Preview → confirm a bass gain row with after lower than before, a reason code, and dry vs processed playback → edit the dB value inside the allowed range → Apply → confirm a new revision id, playback of the new mix, and unchanged sha256 on each stem file → Undo → confirm the head returns to the previous revision (or empty) and both mix files still exist → Reject on a second preview removes only preview files.

Also confirm master target Streaming shows "not a guarantee", a composition save does not contain the plan, and a stem-set rerender shows the soft-stale banner without applying.

UI verification during implementation: exercise preview, A/B, apply, undo, and reject in the browser against the running app. If no browser tools are available, the pytest AC plus a rendered panel unit/helper test is the fallback and the gap must be stated.

## Implementation notes for `/aif-implement`

- Tasks 12 and 13 are the post-improve remainder. Do not reopen Tasks 1–11.
- Prefer `routers/mix_plan.py` + `services/mix_plan/` over growing `mix_analysis` routes. Analysis stays read-only.
- Reuse `absolute_audio_path`, active-head selection, and WAV decode. Do not duplicate path confinement logic carelessly — follow `mix_analysis_store` confinement tests.
- Parameter numbers the UI shows must be the API `changes[]` values, not a second client-side musical guess.
- Host UI under `NeuralAudioRenderPanel`. Do not add a Composer tab.
- Docs policy is **yes** → mandatory `/aif-docs` checkpoint at completion.
- Keep master-target copy factual. Goals are presets. Measured fields stay measured.
