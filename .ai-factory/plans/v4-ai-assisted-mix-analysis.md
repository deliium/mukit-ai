# Implementation Plan: V4 AI-Assisted Mix Analysis Engine

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-24
Planning depth: final, ultra-thorough (post `/aif-improve` 2026-09-24)
Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`)

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: final, ultra-thorough
- Scope: analyze **already-rendered** neural stem-set / mix WAVs with deterministic DSP measurements + optional AI interpretation into concrete production observations — **never** mutating stem/mix audio, **never** mutating `composition.v2`, **never** inventing `composition.v4`, **never** writing `DATASET_ROOT`, **never** conflating this with symbolic `composition.analysis.v1` or recovery Demucs stems, **never** inventing a new `AiOperation` for interpretation in v1
- Parent plans / shipped foundations:
  - `.ai-factory/plans/v4-stem-aware-neural-rendering.md` (**shipped** — stem sets, roles, timeline_sync, download routes, fake multi-WAV, supersede)
  - `.ai-factory/plans/v3-optional-neural-audio-rendering.md` (**shipped** — mix jobs, quotas, fake engine, `NEURAL_AUDIO_RENDER_ROOT`)
  - `.ai-factory/plans/v2-deterministic-composition-v2-analysis.md` (**shipped** — symbolic analysis sidecar pattern; reuse *shape* not domain)
  - `.ai-factory/plans/v4-controlled-critique-revision-loops.md` / critique engine (**shipped** — finding locus + evidence + strata; reuse *finding shape* + `llm_composition_critique` fake path)
  - `.ai-factory/plans/v4-audio-symbolic-alignment-round-trip.md` (**shipped** — tick↔seconds mapping helpers; soft-stale fingerprints)
  - `.ai-factory/plans/v3-audio-to-symbolic-musical-input.md` (**shipped** — optional `requirements-*-extras.txt` + fake CI pattern)

## Roadmap Linkage
Milestone: "V4 AI-assisted mix analysis"
Rationale: Stem-aware neural rendering ships independent stem WAVs; users still lack concrete, measurable production observations over those stems/mixes without auto-mutating audio. Add this as a **new unchecked** milestone in `.ai-factory/ROADMAP.md` during the docs task (all prior milestones are complete).

## Goal

Allow the user to analyze a **completed multi-stem render** (and optionally its related mix job) and receive a versioned report of **concrete measurable production observations** linked to stems/tracks and time/bar ranges — without changing any audio or the symbolic score.

The user can:

1. Select a completed `neural_audio_stem_set.v1` (members `complete`) and run mix analysis.
2. See **measurements** (peak, loudness, dynamic range, clipping, stereo balance, spectral bands, LF buildup, headroom, section loudness contrast, pairwise masking proxies) produced by deterministic DSP.
3. See **observations** derived by rule engines from measurements (still objective; cite metric codes + windows).
4. Optionally request **AI interpretation** that explains measurements in production language — clearly labeled subjective / advisory, never mixed into measurement tables.
5. See useful visualizations (waveform peaks, spectral/loudness envelopes, highlight ranges for findings).
6. Optionally **persist** the report as a project-scoped durable artifact (metadata + JSON; never PCM).
7. See a **soft-stale** banner when the live stem-set / composition fingerprint diverges from the report pins (banner only; never auto-reanalyze).

```text
Completed stem set (+ optional mix job) under NEURAL_AUDIO_RENDER_ROOT
        │
Select active-head complete stems (exclude superseded; optional stem_ids filter)
        │
Read-only open via absolute_audio_path (never write_bytes on stem/mix paths)
        │
Deterministic DSP measurement suite (stdlib wave always;
  optional numpy/scipy/soundfile extras for richer metrics)
        │
Rule observations (measurement → coded finding with stem + tracks + Hz + bars/time)
        │
Optional interpretation (critique-style fake | soft language path; no new AiOperation)
        │
mix.analysis.v1 report (session response; optional durable store)
        │
UI: Mix Analysis subsection under NeuralAudioRenderPanel + soft-stale banner
```

**Acceptance one-liner:** The user can analyze a multi-stem render and receive concrete measurable production observations linked to tracks/time ranges.

Example preferred finding text:

> Cello and bass show strong spectral overlap around the lower-mid range during bars 21–28.

Not:

> The mix sounds bad.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Mix analysis** | Audio-domain production analysis of rendered stem/mix WAVs |
| **`mix.analysis.v1`** | Versioned report DTO (measurements + observations + optional interpretations); no PCM |
| **Measurement** | Deterministic DSP scalar/series with unit, window, stem_id(s); never subjective prose |
| **Observation** | Rule-derived objective finding from thresholds on measurements; includes locus + reason |
| **Interpretation** | Optional AI prose explaining measurements/observations; `kind=interpretation` / stratum `subjective` |
| **Suggestion** | Actionable observation/interpretation that names stem + frequency/range + section/time + reason |
| **Locus** | `{ stem_ids[], stem_roles[], source_track_ids[], freq_hz_low?, freq_hz_high?, start_bar?, end_bar?, start_seconds?, end_seconds? }` — `source_track_ids` from stem member `source_track_ids_json` |
| **Active head** | Per `stem_role`, the latest `complete` stem that is **not** referenced as `supersedes_stem_id` by a later `complete` sibling (or explicit `stem_ids[]` override) |
| **Symbolic analysis** | Existing `composition.analysis.v1` over V2 notes — **out of scope**; do not overload |
| **Neural stem / mix** | Durable egress WAVs under `NEURAL_AUDIO_RENDER_ROOT` — analysis **inputs**, never mutated |
| **Recovery stem** | Demucs/recovery role→track bindings — **not** mix-analysis inputs in v1 |
| **Persist** | Optional write of report JSON + SQLite row under project; default is session response only |
| **Soft-stale** | Report `source_stem_set_fingerprint` / composition fingerprint vs live values — **banner only**; never auto-reanalyze |

## Non-goals (v1)

- Auto-EQ / auto-gain / rewriting stem or mix WAVs
- DAW plugin hosting / real-time metering while playing Tone.js
- Analyzing recovery/Demucs separated assets (different product surface)
- Mix-only analysis with no `stem_set_id` (only `mix_render_id`) — defer; stem set required for AC
- Replacing symbolic `composition.analysis.v1` or critique of note events
- Claiming ITU-R BS.1770 laboratory certification or calibrated monitoring accuracy
- Mandatory GPU / librosa / scipy in default Compose image (fake + stdlib path must pass CI)
- Inventing `composition.v4` / putting mix-analysis ids on `CompositionV2NoteEvent`
- Writing `DATASET_ROOT` / auto corpus ingest
- Silent swap of incomplete stem sets into analysis (422 if members not `complete`)
- Using AI interpretation as the sole source of “measurements”
- New `AiOperation.MIX_ANALYSIS_*` (mirror critique’s local fake / soft-unavailable path)
- New Composer workspace tab solely for mix analysis (use Neural Audio subsection)
- Async analyze jobs (defer)

## Approach Evaluation (locked)

### Part A — Domain boundary vs symbolic analysis

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Extend `composition.analysis.v1` with audio scopes** | One Analysis tab | Mixes symbolic + PCM domains; breaks “analysis never needs audio” | **Reject** |
| **B. New `mix.analysis.v1` beside neural egress** | Clear boundary; reuses stem assets | New schemas/routes | **Accepted** |
| **C. Fold into critique strata** | Finding UX reuse | Critique is note-event Evaluation Engine; would pollute | **Reject** (reuse *finding shape* only) |

**Locked:** New contracts under `mix_analysis_schemas.py` + `services/mix_analysis/` (or `services/mix_analysis_*.py`). Critique `CritiqueFindingV1` is a **pattern reference** — do not import critique HTTP into mix routes.

### Part B — Input sources

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Upload arbitrary WAV** | Flexible | Quota/security/new ingress; duplicates recovery | **Defer** (not v1) |
| **B. Completed neural stem set (+ optional mix job id)** | Reuses path confinement + provenance | Requires prior render | **Accepted** |
| **C. Live Tone.js capture** | Instant | Browser-only; not durable stems | **Reject** for backend engine |

**Locked:** Request references `stem_set_id` (required) and optional `mix_render_id`. Default member selection = **active heads** only (`complete`, not superseded). Optional `stem_ids[]` filter must still be complete + readable. Composition body optional — used only for bar↔time mapping via stem-set `tempo_bpm` / `origin_tick` / sections when provided. **Without composition → seconds windows only; never invent bars.**

### Part C — DSP stack

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always require librosa/scipy** | Rich metrics | Breaks lean CI image | **Reject** |
| **B. stdlib `wave` + pure-Python core; optional extras** | CI always green; richer path when installed | Two fidelity tiers | **Accepted** |
| **C. Sidecar DSP service** | Isolates heavy deps | Ops overhead for v1 | **Reject** |

**Locked:**
- Core always available: PCM decode via `wave`, peak, clip detect, RMS, crest factor, mono/stereo channel balance, coarse band energy via simple DFT/Goertzel or numpy-if-present.
- Optional extras file `backend/requirements-mix-analysis.txt` (`numpy`, `soundfile`, `scipy` — **no** torch): enables LUFS-approx / better STFT masking / smoother spectra.
- `MIX_ANALYSIS_FAKE_MODE=1` or digest-driven fixtures: deterministic measurement JSON from WAV sha256 (no quality claim) for CI without PCM heuristics flaking; fake path must still be able to emit ≥1 observation with locus for AC.
- Metric capability flags on report: `dsp_backend: stdlib | numpy_scipy | fake`.
- Open inputs only via neural `absolute_audio_path` + read (`read_bytes` / `wave.open`); **never** `write_bytes` / rewrite on stem or mix paths.

Honest gaps when not measurable:
- **Excessive reverb/ambience:** emit `metric_unavailable` / observation skipped with code `reverb_estimate_unavailable` unless extras provide a bounded RT60/proxy — never invent “sounds wet”.
- **Masking:** pairwise spectral overlap / band energy correlation proxy — label as `masking_proxy`, not psychoacoustic MOS.

### Part D — Measurement vs suggestion separation

| Layer | Source | Fields |
|-------|--------|--------|
| `measurements[]` | DSP only | `code`, `unit`, `value` / `series_ref`, `locus`, `confidence=measured` |
| `observations[]` | Pure rules on measurements | `code`, `severity`, `message`, `locus`, `reason`, `evidence.measurement_codes[]`, `kind=observation` |
| `interpretations[]` | Optional fake / soft LLM | `kind=interpretation`, `message`, `locus`, cites observation/measurement codes; never invents numeric values |

**Locked:** Suggestions (user-facing “what to do”) live on observations/interpretations as `suggested_action` **only when** locus + reason present. UI must visually separate Measurements | Observations | AI notes. Subjective AI cannot use severity `error`.

### Part D2 — Optional AI interpretation (locked post-improve)

| Approach | Verdict |
|----------|---------|
| New `AiOperation.MIX_ANALYSIS_INTERPRET` | **Reject for v1** |
| Mirror `llm_composition_critique.py` (`LLM_FAKE_MODE` / local fake findings; real chat soft-unavailable) | **Accepted** |

**Locked:** `include_ai_interpretation=true` → fake interpretations under `LLM_FAKE_MODE` or `MIX_ANALYSIS_FAKE_MODE` that cite existing measurement/observation codes + concrete locus; without fake and without a wired client → warning (`interpretation_unavailable`), DSP layers still returned. No new AiOperation; do not fail whole analyze.

### Part E — Persistence

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Session-only forever** | Simple | Fails req 8 | **Reject** |
| **B. `agent_artifacts` content_type** | Existing workspace | Wrong retention semantics; agent promote path; viz series size vs durable caps | **Reject for v1** |
| **C. Dedicated `mix_analysis_reports` + JSON file** | Parity with neural/recovery assets; clear GC | Alembic | **Accepted** |

**Locked:** Alembic after `20260924_0007`. Files: `{project_id}/{report_id}.json` under `MIX_ANALYSIS_ROOT` (default beside project DB: `…/mix_analysis_reports`). SQLite row holds ids, stem_set_id, fingerprints, dsp_backend, created_at, byte_size, sha256_prefix. `POST …/analyze` with `persist=true` + `project_id` writes; default `persist=false` returns body only. **`cleanup_project_mix_analysis` wired in `project_store.delete_project`** beside neural/recovery GC. Responses never include PCM; series are downsampled floats with caps.

### Part F — Sync vs job

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Always async job** | Parity with neural | UX latency for short fake WAVs | **Defer** |
| **B. Sync analyze with duration/byte caps** | Simple AC | Long Stereo 24-bit stems may timeout | **Accepted for v1** |
| **C. Hybrid (sync default, job if over cap)** | Best UX | Scope creep | **Defer** |

**Locked:** Synchronous `POST /mix-analysis/analyze` with `MIX_ANALYSIS_MAX_AUDIO_SECONDS` / `MAX_TOTAL_INPUT_BYTES` / wall-clock timeout → 422/504 with stable codes. Document that v2 may add jobs if needed.

### Part G — Visualization & UI shell

| Surface | Data | Notes |
|---------|------|-------|
| Backend series | Peak envelope, band-energy over time (`sub`/`low`/`low_mid`), loudness envelope — capped points | Embedded in report as `series[]` with `id` refs |
| Frontend | Charts + highlight locus ranges; optional decode stem download for waveform via existing `audioWaveformPeaks` | Never auto-download all stems; user opts into “show waveform” |
| Shell | **Subsection under `NeuralAudioRenderPanel`** (already outside tabs in `ComposerWorkspace`) | No new workspace tab |
| Soft-stale | Banner when report fingerprints ≠ live stem-set / composition | Never auto-reanalyze |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Path | Mix-analysis reuse |
|------|------|--------------------|
| Stem set DTOs / download | `neural_audio_schemas.py`, `routers/neural_audio.py` | Input ids + `/neural-audio/stems/{id}/audio` read path via store |
| Stem store FS | `neural_audio_stem_store.py` + `absolute_audio_path` in `neural_audio_render_store.py` | Path resolve; **read-only** open |
| Supersede | `neural_audio_stems.supersedes_stem_id` | Active-head selection |
| Timeline sync | stem set `tempo_bpm`, `origin_tick`, `duration_ticks`, `sample_rate` | Bar/time locus mapping |
| Snapshot fingerprint | `composition_snapshot_encoding.py` | Soft-stale compare |
| Tick↔seconds | `audio_alignment.py` / composition timeline | Map seconds windows → bars when composition provided |
| Finding locus pattern | `critique_schemas.py` | Shape for observations (do not couple HTTP) |
| Model critique fake | `llm_composition_critique.py` | Pattern for Task 5 interpretations |
| Optional extras pattern | `requirements-audio-transcription.txt` | Mirror for mix DSP |
| Fake WAV | `fake_neural_audio.py` | CI stem sets as analysis fixtures |
| Waveform peaks FE | `audioWaveformPeaks.js` | Optional client viz |
| Neural panel | `NeuralAudioRenderPanel.jsx` | **Host** Mix Analysis subsection |
| Project delete GC | `project_store.delete_project` | Mirror neural/recovery cleanup hooks |
| Logging hygiene | RULES / neural services | Never log PCM / full reports at INFO |

### Gaps (this plan closes)

| Gap | Impact |
|-----|--------|
| No audio DSP measurement suite | Cannot produce peak/LUFS/clip/stereo/spectral metrics |
| No mix.analysis contract | No measurement vs suggestion separation |
| No stem/track-linked production findings | AC “linked to tracks/time” fails |
| No active-head selection vs superseded stems | Double-count / stale WAV analysis |
| No optional AI interpretation layer (critique-shaped) | Req 2 unmet |
| No durable mix report artifact + project-delete GC | Req 8 unmet / orphaned files |
| No Mix Analysis UI / viz / soft-stale | User cannot run AC journey safely |
| No DSP unit tests / docs / `.env.example` | Acceptance incomplete |

## Commit Plan

- **Commit 1** (tasks 1–3): `feat(mix-analysis): schemas, Alembic store, DSP measurement core`
- **Commit 2** (tasks 4–6): `feat(mix-analysis): observations, optional AI interpret, analyze API`
- **Commit 3** (tasks 7–8): `feat(mix-analysis): Mix Analysis UI + visualizations`
- **Commit 4** (tasks 9–10): `test(mix-analysis): DSP coverage + docs + ROADMAP milestone`
- **Commit 5** (task 11): `feat(mix-analysis): soft-stale report banners`

## Tasks

### Phase 1: Contracts, store, DSP core

- [x] Task 1: Define `mix.analysis.v1` DTOs + error codes
  Deliverable: Pydantic schemas (`extra="forbid"`) for report, measurement, observation, interpretation, locus, series (capped), analyze request/response, list/get persisted DTOs. Locus must include optional `source_track_ids[]` (from stem `source_track_ids_json`) alongside `stem_ids` / `stem_roles` / freq / bars / seconds. Stable codes for unavailable metrics, incomplete stem set, quota, timeout, `interpretation_unavailable`. FE JSDoc/constants mirror for unit tests (`mixAnalysisUi.js`). Explicit fields separating `measurements` / `observations` / `interpretations`.
  LOGGING: DEBUG schema accept/reject field keys only (never values of series, never PCM).
  Files: `backend/app/mix_analysis_schemas.py`, `frontend/src/utils/mixAnalysisUi.js`

- [x] Task 2: Alembic + mix analysis report store + project-delete GC
  Deliverable: Migration `20260924_0008_mix_analysis_reports` creating `mix_analysis_reports` with CHECKs/indexes (`project_id`, `stem_set_id`, `created_at`); store insert/get/list/delete/write JSON under `MIX_ANALYSIS_ROOT` path `{project_id}/{report_id}.json`; path confinement. Implement `cleanup_project_mix_analysis(project_id)` and wire it in `project_store.delete_project` beside `cleanup_project_neural_audio` / `cleanup_project_audio_recovery` (best-effort try/except + WARN, same pattern).
  LOGGING: INFO migration; INFO persist with report_id, byte_size, sha256_prefix, stem_set_id; INFO/WARN cleanup counts; never report body at INFO.
  Files: `backend/app/db/alembic/versions/20260924_0008_mix_analysis_reports.py`, `backend/app/mix_analysis_settings.py`, `backend/app/services/mix_analysis_store.py`, `backend/app/services/project_store.py`

- [x] Task 3: Deterministic DSP measurement engine (read-only WAV)
  Deliverable: Package `services/mix_analysis/` with:
  - WAV decode (`wave` always; `soundfile` if extras) via neural `absolute_audio_path` + **read-only** open (never `write_bytes` on stem/mix paths)
  - Metrics: peak (dBFS), true-peak proxy or digital peak, RMS, crest/dynamic-range proxy, clip count/ratio, stereo L/R RMS balance + correlation if stereo, headroom to 0 dBFS, coarse spectral band energies (sub/low/low-mid/high-mid/high), LF buildup score, section loudness contrast when windows provided, pairwise stem masking_proxy (spectral overlap in shared bands)
  - Windowing: full-file + optional section windows from tempo/`origin_tick`; **bars only when composition provided** — otherwise seconds-only loci (never invent bars)
  - Capability: fill `dsp_backend`; skip/flag reverb when unavailable
  - Bound decode memory
  - Fake mode: deterministic measurements from sha256 + stem_role; must support observation-triggering values for CI AC
  Optional extras: `backend/requirements-mix-analysis.txt` (numpy, soundfile, scipy).
  LOGGING: INFO stem_id, role, duration_s, sample_rate, metric_count, dsp_backend, duration_ms; WARN on skip codes; never PCM samples / full series dumps.
  Files: `backend/app/services/mix_analysis/` (`__init__.py`, `decode.py`, `metrics.py`, `windows.py`, `fake_metrics.py`), `backend/requirements-mix-analysis.txt`

### Phase 2: Observations, AI, API

- [x] Task 4: Rule observation builder
  Depends on: Task 1, Task 3
  Deliverable: Pure functions mapping measurement thresholds → `observations[]` with required locus (affected stem(s), **`source_track_ids` when known**, freq range when spectral, bars **only if composition-backed** else seconds) + `reason` citing metric codes/values. Example codes: `clipping_detected`, `low_headroom`, `stereo_imbalance`, `lf_buildup`, `spectral_masking_proxy`, `section_loudness_flat`, `peak_hot`. Cap observation count. No AI in this module.
  LOGGING: DEBUG observation codes emitted (counts per code); INFO total observation_count.
  Files: `backend/app/services/mix_analysis/observations.py`, thresholds in `mix_analysis_settings.py`

- [x] Task 5: Optional AI interpretation layer (critique-shaped; no new AiOperation)
  Depends on: Task 4
  Deliverable: When `include_ai_interpretation=true`, mirror `llm_composition_critique.py`: under `LLM_FAKE_MODE` or `MIX_ANALYSIS_FAKE_MODE` emit deterministic `interpretations[]` that **cite existing** measurement/observation codes and concrete locus; strip/reject invented numeric claims; `kind=interpretation`. Without fake and without a wired language client → status/warning `interpretation_unavailable`, still return DSP layers (do not fail whole analyze). **Do not add `AiOperation.MIX_ANALYSIS_*`.**
  LOGGING: INFO include_flag, model_id_prefix (`fake`|`unavailable`), interpretation_count, duration_ms; never prompts/completions at INFO (DEBUG truncated digest keys only).
  Files: `backend/app/services/mix_analysis/interpret.py` (pattern from `llm_composition_critique.py`)

- [x] Task 6: Orchestration router + active-head read-only stem binding
  Depends on: Tasks 2–5
  Deliverable:
  - `analyze_mix(...)` loads stem set; selects **active-head** complete members (exclude superseded) unless `stem_ids[]` provided; resolves paths via `absolute_audio_path`; validates complete + readable; runs DSP → observations → optional interpret; populates locus `source_track_ids` from stem rows; builds `mix.analysis.v1`; optional persist
  - Routes: `POST /mix-analysis/analyze`, `GET /mix-analysis/reports/{id}`, `GET /mix-analysis/reports?project_id=`, `DELETE /mix-analysis/reports/{id}`
  - Mount router in `main.py`
  - Soft-stale fields on report: `source_stem_set_fingerprint`, composition fingerprint prefixes, analyzed stem ids/sha256 prefixes
  - Assert stems untouched (sha256 before/after in tests)
  LOGGING: INFO method/path, stem_set_id, report_id, active_head_count, HTTP status, dsp_backend, counts; sanitize errors.
  Files: `backend/app/services/mix_analysis/pipeline.py`, `backend/app/services/mix_analysis/active_stems.py` (pure head selection), `backend/app/routers/mix_analysis.py`, `backend/app/main.py`

### Phase 3: UI + visualization

- [x] Task 7: Mix Analysis subsection + API client
  Depends on: Task 6
  Deliverable: **Subsection under `NeuralAudioRenderPanel`** (not a new workspace tab) — select completed stem set, dimension checklist (defaults on for objective metrics), Run analysis, Persist checkbox when project open, lists measurements vs observations vs AI notes with stem / **tracks** / Hz / bars-or-seconds / reason. Wire `musicApi.js`. Honesty copy: analysis does not modify stems; AI notes are advisory.
  LOGGING: `appLogger('mixAnalysis')` phase transitions only (start/complete/error codes).
  Files: `frontend/src/components/NeuralAudioRenderPanel.jsx`, optional `MixAnalysisPanel.jsx` extracted child, `frontend/src/api/musicApi.js`, `frontend/src/utils/mixAnalysisUi.js`, session-only store fields if needed (`musicStore.js`)

- [x] Task 8: Analysis visualizations
  Depends on: Task 7
  Deliverable: Render backend `series` (peak / band energy / loudness envelopes) as simple SVG/canvas charts; highlight observation loci; optional “Load waveform” using stem download + `audioWaveformPeaks` (user-gated). No card-spam; one viz region with finding-linked highlights.
  LOGGING: DEBUG series id + point counts only.
  Files: `frontend/src/components/mix-analysis/` or `MixAnalysisCharts.jsx`, reuse `audioWaveformPeaks.js`

### Phase 4: Tests & docs

- [x] Task 9: DSP + API tests
  Depends on: Tasks 3–6, Task 11
  Deliverable: `backend/tests/test_mix_analysis.py` — schema accept/reject; synthetic WAV fixtures (hot peak, clipped, stereo imbalance, two-stem overlap); assert measurements + observation loci including `source_track_ids` when present; **active-head ignores superseded** sibling; fake mode determinism + ≥1 observation with locus; stem files sha256 unchanged after analyze; incomplete stem set → 422; persist/list/delete; AI flag with `LLM_FAKE_MODE` yields interpretations citing codes; optional extras tests `pytest.mark.skipif` without numpy/scipy. Frontend unit tests for measurement/observation separation helpers, soft-stale compare, and copy.
  LOGGING: N/A (assert services never log PCM in existing log-capture patterns if present).
  Files: `backend/tests/test_mix_analysis.py`, `backend/tests/fixtures/audio/mix_analysis/` (short synthetic WAVs), `frontend/src/utils/mixAnalysisUi.test.js`

- [x] Task 10: Docs + ROADMAP + AGENTS + `.env.example`
  Depends on: Tasks 1–9, Task 11
  Deliverable: New `docs/mix-analysis.md` (dimensions, measurement vs suggestion, DSP backends, fake mode, active heads, soft-stale, persist API, honesty limits for reverb/masking, never mutates stems/V2, no new AiOperation). Link from `docs/neural-audio-rendering.md`, README, `docs/testing.md`. Add unchecked ROADMAP milestone **"V4 AI-assisted mix analysis"**. Update `AGENTS.md` / DESCRIPTION one bullet + structure entry for router/services. Document optional `requirements-mix-analysis.txt`. Add `MIX_ANALYSIS_*` stubs to `.env.example` (root, fake mode, caps) mirroring neural/recovery blocks.
  LOGGING: N/A.
  Files: `docs/mix-analysis.md`, `docs/neural-audio-rendering.md`, `README.md`, `docs/testing.md`, `.ai-factory/ROADMAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md` (light touch), `.env.example`

### Phase 5: Soft-stale close-out (post-improve)

- [x] Task 11: Soft-stale mix analysis banners
  Depends on: Task 6, Task 7
  Deliverable: Compare report `source_stem_set_fingerprint` / composition fingerprint prefixes to live stem-set + live snapshot fingerprint (reuse `compositionSnapshotFingerprint.js` patterns from stem UI). Show soft-stale banner only; never auto-reanalyze or mutate stems. Helpers + unit tests in `mixAnalysisUi.js` / `.test.js`.
  LOGGING: DEBUG stale compare prefixes only via `appLogger('mixAnalysis')`.
  Files: `frontend/src/utils/mixAnalysisUi.js`, `frontend/src/utils/mixAnalysisUi.test.js`, `frontend/src/components/NeuralAudioRenderPanel.jsx` (or Mix Analysis child)

## API surface (v1)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/mix-analysis/analyze` | Run analysis on stem set (+ optional mix); optional `persist` |
| `GET` | `/mix-analysis/reports/{id}` | Persisted report JSON (no PCM) |
| `GET` | `/mix-analysis/reports?project_id=` | List report metadata |
| `DELETE` | `/mix-analysis/reports/{id}` | Delete metadata + JSON file |

### Analyze request (sketch)

```json
{
  "project_id": "…",
  "stem_set_id": "…",
  "mix_render_id": null,
  "stem_ids": null,
  "composition": null,
  "dimensions": ["peak", "loudness", "dynamic_range", "clipping", "stereo_balance", "spectral_balance", "lf_buildup", "masking_proxy", "section_loudness", "headroom"],
  "include_ai_interpretation": false,
  "persist": false
}
```

### Report sketch (conceptual)

```json
{
  "schema_version": "mix.analysis.v1",
  "dsp_backend": "stdlib",
  "mutates_audio": false,
  "mutates_composition": false,
  "stem_set_id": "…",
  "source_stem_set_fingerprint": "…",
  "measurements": [
    {
      "code": "peak_dbfs",
      "unit": "dbfs",
      "value": -1.2,
      "locus": {
        "stem_ids": ["…"],
        "stem_roles": ["bass"],
        "source_track_ids": ["track-bass-1"]
      }
    }
  ],
  "observations": [
    {
      "kind": "observation",
      "code": "spectral_masking_proxy",
      "message": "Cello and bass show strong spectral overlap around the lower-mid range during bars 21-28.",
      "locus": {
        "stem_roles": ["strings", "bass"],
        "source_track_ids": ["track-cello", "track-bass-1"],
        "freq_hz_low": 150,
        "freq_hz_high": 400,
        "start_bar": 21,
        "end_bar": 28
      },
      "reason": "masking_proxy_score above threshold for band low_mid in window",
      "evidence": { "measurement_codes": ["band_energy_low_mid", "masking_proxy"] },
      "suggested_action": "Carve 200–350 Hz on strings or high-pass bass slightly in bars 21–28."
    }
  ],
  "interpretations": [],
  "series": [],
  "warnings": []
}
```

Error codes (add): `mix_analysis_stem_set_incomplete`, `mix_analysis_stem_not_ready`, `mix_analysis_not_found`, `mix_analysis_quota_exceeded`, `mix_analysis_input_too_large`, `mix_analysis_timeout`, `mix_analysis_dimension_unknown`, `metric_unavailable`, `interpretation_unavailable`, `mix_analysis_internal_error`.

## Env / settings (`MIX_ANALYSIS_*`)

| Var | Purpose |
|-----|---------|
| `MIX_ANALYSIS_ROOT` | Durable report JSON root (default beside project DB) |
| `MIX_ANALYSIS_FAKE_MODE` | Deterministic measurements + fake interpretations without heavy DSP |
| `MIX_ANALYSIS_MAX_AUDIO_SECONDS` | Per-stem decode/analyze cap |
| `MIX_ANALYSIS_MAX_TOTAL_INPUT_BYTES` | Sum of input WAV bytes |
| `MIX_ANALYSIS_MAX_REPORTS_PER_PROJECT` | Persist quota |
| `MIX_ANALYSIS_JOB_TIMEOUT_SECONDS` | Wall clock for sync analyze |
| `MIX_ANALYSIS_SERIES_MAX_POINTS` | Viz series cap |
| `MIX_ANALYSIS_OBSERVATION_MAX` | Finding cap |

Document stubs in `.env.example` (Task 10).

## Coupling risks

1. Mutating stem/mix WAV bytes or rewriting neural store rows during analyze
2. Analyzing superseded stems and double-counting roles
3. Overloading `composition.analysis.v1` or critique evaluate with PCM concerns
4. Treating AI prose as measurements / inventing LUFS numbers in interpretations
5. Adding a new `AiOperation` unnecessarily
6. Analyzing recovery Demucs stems under the same API without explicit product decision
7. Logging PCM, full series, or full report bodies at INFO
8. Requiring scipy/librosa in core `requirements.txt` (breaks lean CI)
9. Claiming calibrated loudness / BS.1770 compliance
10. Silent analyze on incomplete stem sets
11. Inventing bar numbers without composition
12. Orphaned `MIX_ANALYSIS_ROOT` files if project-delete GC is skipped
13. Writing `DATASET_ROOT` or inventing `composition.v4`

## Verification

```bash
cd backend && MIX_ANALYSIS_FAKE_MODE=1 NEURAL_AUDIO_FAKE_MODE=1 pytest tests/test_mix_analysis.py tests/test_neural_audio_stems.py -q
node --test frontend/src/utils/mixAnalysisUi.test.js
```

AC manual: render piano/bass/strings stem set → Run Mix Analysis → confirm measurements + at least one observation with stem + tracks + (freq or bars/seconds) + reason → toggle AI interpretation (fake) → Persist report → reload list → confirm stem WAV sha256 unchanged and V2 JSON unchanged → edit composition fingerprint → confirm soft-stale banner without auto-reanalyze. After selective strings rerender, confirm analysis uses the new strings head (not superseded prior).

## Implementation notes for `/aif-implement`

- Prefer new `routers/mix_analysis.py` + `services/mix_analysis/` over growing `neural_audio.py` beyond thin path helpers (`absolute_audio_path`).
- Reuse neural stem store path resolution; do not duplicate quota roots into `DATASET_ROOT`.
- Observation messages should be written like the cello/bass example — concrete loci, not vibe checks.
- Host UI under `NeuralAudioRenderPanel`; do not add a dedicated Composer tab for v1.
- Docs policy is **yes** → mandatory `/aif-docs` checkpoint at completion.

## Improvement notes (`/aif-improve` 2026-09-24)

- Added Task 11 soft-stale banners; wired project-delete GC into Task 2; locked active heads, `source_track_ids` on locus, critique-shaped interpretation without new AiOperation, seconds-only bars without composition, Neural panel subsection (no new tab), `.env.example` in Task 10.
- Deferred as out of scope: mix-only without stem set; `AiOperation.MIX_ANALYSIS_*`; async jobs; recovery stems as inputs.
