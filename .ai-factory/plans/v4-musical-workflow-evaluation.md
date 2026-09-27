# Implementation Plan: V4 Musical Workflow Evaluation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-27
Planning depth: ultra

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra
- Refined: 2026-09-27 (`/aif-improve`, all findings applied)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`) plus the request’s tests and documentation
- Scope: a versioned musical benchmark suite, deterministic hard-metric scoring, pipeline comparison, autonomous-run counters, fixed seeds where the callee already honors them, blinded human listening, and automatic regression detection for hard metrics
- Parent (reuse, do not fork): `validate_generation_constraints` in `backend/app/services/generation_constraints.py`; `evaluate_composition` and `check_section_contrast` in `backend/app/services/composition_critique.py` and `composition_critique_checks.py`; `analyze_repetition_from_context` in `backend/app/services/composition_repetition_analysis.py`; `validate_composition_integrity` in `backend/app/services/composition_validator.py`; `events_outside_targets_fingerprint` and `assert_preserve_outside_targets` in `backend/app/services/composition_revision_preserve.py`; `compute_metrics_for_samples` tonal metric in `backend/app/music_transformer/eval_metrics.py`; `operation_span("run")` plus `build_summary` as the workflow preview route uses them; `profile_active_for_fake` and `reference_features_active_for_fake`; autonomous counters on `autonomous_runs` (`agent_operation_count`, `revision_pass_count`, stage `failure_code`, `retry_stage`)
- Generation entry points stay the ones already shipped: `generate_music_json` (V3), `run_spine_workflow` with revision mode off (V4 multi-agent), `run_spine_workflow` with revision mode `balanced` (V4 iterative revision). The autonomous composer in `backend/app/services/autonomous_composer.py` is an additional measured arm, not a second scheduler

## Roadmap Linkage
Milestone: "V4 musical workflow evaluation"
Rationale: Every milestone in `.ai-factory/ROADMAP.md` is checked. Training eval and the critique engine score single compositions or token samples. They do not run one versioned brief set through V3 generate, the V4 spine, and the V4 revision loop, and they do not fail a later change when a hard metric regresses. The docs task adds this milestone unchecked. Check it only after the fake-mode pipeline test and the regression test pass.

## Goal

A model or workflow change can be scored on the same versioned briefs as the previous run. Hard numbers can fail a regression. Listening stays a separate human judgment.

```text
workflow.benchmark.v1  (committed suite + digest)
        │
        ├─ v3_direct              generate_music_json
        ├─ v4_multi_agent         run_spine_workflow(revision_mode=off)
        ├─ v4_iterative_revision  run_spine_workflow(revision_mode=balanced)
        └─ v4_autonomous          temp-db composer, include_rendering false
                │
                ▼
        workflow.benchmark_run.v1
                │
                ├─ hard regression vs stored baseline of the same suite version
                └─ workflow.listening_packet.v1  (A/B or A/B/C, no metric fields)
```

**Acceptance one-liner:** With `LLM_FAKE_MODE` on, one committed suite version runs the melody-focused brief through V3 direct generation, V4 multi-agent, and V4 iterative revision; the run file records suite version, seed status, hard metrics, and autonomous counters; a worsened invalid-composition rate against the stored baseline exits non-zero; a blinded A/B/C packet for those three arms contains compositions and labels and omits metric numbers.

## Terminology lock

| Term | Meaning |
|------|---------|
| **Suite** | Committed `workflow.benchmark.v1` document. Identity is `benchmark_id` + `benchmark_version` + SHA-256 of the canonical JSON |
| **Case** | One brief in the suite. Kind is one of the seven musical kinds, or `revision-preservation` |
| **Arm** | One pipeline invocation for one case: `v3_direct`, `v4_multi_agent`, `v4_iterative_revision`, or `v4_autonomous` |
| **Hard metric** | A pass/fail or rate that can fail regression: constraint compliance, structural compliance, instrument-range correctness, revision preservation, invalid composition rate |
| **Observed metric** | Recorded and shown as a number, never a taste score and never a default regression gate: motif recurrence, section contrast, tonal consistency, latency, RSS, remote cost |
| **Baseline** | A prior `workflow.benchmark_run.v1` pinned for one suite version and one arm set. Regression compares the new run to this file |
| **Listening packet** | Blinded compositions labeled A/B or A/B/C. It has no pipeline ids, no metric values, no cost, no latency |
| **Judgment** | A human preference written after hearing the packet. Unblind happens only after the judgment file exists |
| **Scaffold** | A legal `composition.v2` built from the case timeline: key, meter, tempo, sections, one track per requested instrument, `events: []`. It is the spine’s `source`. It is not a melody |
| **Seed status** | `honored`, `ignored_by_pipeline`, or `unknown`. Taken from the callee’s returned seed, not assumed |
| **Profile conditioning** | `attached` when fake mode would apply the profile marker or real provenance contains `composer_profile_id`. `skipped_missing_profile` when generate skips a missing id. `not_wired` on spine arms |
| **Reference conditioning** | `attached` when the V3 request carries `style_references` (inline composition is enough). `not_wired` on spine arms |
| **Recovered stage** | An autonomous stage that `retry_stage` moved from `failed` to `pending` and that later reaches `completed` in the same harness run |

## Non-goals

- Claiming motif recurrence, section contrast, tonal consistency, latency, or a listening vote as musical quality. Automated reports set `musical_quality_claim: false`. The listening packet does not display automated metrics
- A new critique engine, a new constraint validator, or a new playable schema. `composition.v4` stays unsupported
- Teaching `ai_agents/` a new profile or reference channel. Spine arms record `profile_conditioning` and `reference_conditioning` as `not_wired`
- Writing `PROJECT_DB_PATH` or `DATASET_ROOT`, including inserting the suite profile so generate can load it. The autonomous arm uses a temp SQLite file under the benchmark root and deletes it at the end of the run
- Neural render, mix analysis, or listening via generated WAV. Playback uses the existing piano-roll path on the packet’s composition JSON
- Remote leaderboards, dataset ingest, or storing prompts, note lists, profile bodies, or reference events in logs or in the regression summary
- Inventing provider cost. Missing cost stays `unavailable` with a null micros field
- Running the full multi-arm suite inside the FastAPI process or on `GET`/`POST` product routes
- Playwright. Acceptance is pytest, `node:test` for the blinding helper, and the CLI

## Repository findings

`generate_music_json` in `backend/app/services/llm_music_generator.py` is the V3 path. It returns provenance in the tuple, and the fake hybrid path puts `seed` on that dict. `LLMGenerationOptions.seed` is honored by hybrid and symbolic pipelines and ignored by `llm_only` (`backend/app/schemas.py`). `resolve_profile_merge` is called with `profile_id` only and loads `PROJECT_DB_PATH`. Fake mode applies a velocity marker when `profile_active_for_fake` is true, which needs a profile id and a strength other than `off`, and when `reference_features_active_for_fake` sees `style_references`. `StyleReferenceRequest.composition` is a legal inline source. Soft merge must not override hard constraints.

`run_spine_workflow` in `backend/app/ai_agents/workflow.py` takes a `CompositionV2` source, not a brief. `revision_mode` off runs the spine once. Modes `fast` / `balanced` / `thorough` delegate to `run_revision_loop`. Pass records must not embed playable scores. `events_outside_targets_fingerprint` already checks preservation. The revision loop’s provenance sets `seed` to null today.

`compile_project_plan` locks stages `plan` through `render`. `retry_stage` re-queues one recoverable failed stage. Run rows already store `agent_operation_count`, `revision_pass_count`, `prompt_tokens`, `provider_reported_cost_micros`, `active_runtime_ms`, and `seed`. Stage rows store `status`, `failure_code`, and `recoverable`. There is no stage-status history table. Recovery is visible only to a caller that invokes `retry_stage` and then sees `completed`.

The workflow preview route wraps `run_spine_workflow` in `operation_span("run")` and calls `build_summary`. Child spans roll into that parent. `OperationSummaryV1` carries `duration_ms`, `model_call_count`, `revision_count`, `failure_count`, and token counts. RSS and `provider_reported_cost_micros` stay on the closed run span. `reserve_model_call` is what increments `model_call_count`. `note_run_outcome` copies `revision_count` onto the open run span. `WorkflowPreviewResult.agent_sequence` and `revision_history` are present even when no model call was reserved. Cost stays null when the provider omits it.

`music_transformer` eval is a separate offline tokenizer study. Its listening set and `musical_quality_claim: false` are the pattern to copy. Its metrics are not this suite.

`evaluate_composition` already maps constraint failures to `hard_constraint`, and it already runs section-contrast checks. `analyze_repetition_from_context` returns motif families. `validate_composition_integrity` covers pitch ranges. None of these persist a benchmark version or compare two pipelines.

No `workflow.benchmark` schema, CLI, fixture suite, or listening packet exists.

## Approach Evaluation (locked)

### Part A — Where the suite runs

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Product HTTP route that runs every brief** | Visible in the app | Runs models on a user request, can spend remote budget, and writes nothing the user asked to keep | **Reject** |
| **B. Offline package plus CLI, in-process calls, filesystem reports** | Same functions as production. CI can stay on fake mode. Reports are versioned files | A human must launch a real-model run on purpose | **Accepted** |
| **C. Music Transformer experiment runner** | Already stores eval and listening dirs | Scores token samples, not V3 versus V4 workflow entry points | **Reject** |

**Locked:** New package `backend/app/workflow_eval/` with `python -m app.workflow_eval.cli`. It imports services and `ai_agents`. `ai_agents/` does not import `workflow_eval`. No router. Default provider path is the existing fake LLM when `LLM_FAKE_MODE` is on. A real model run is an explicit CLI flag and still writes only under the benchmark root.

### Part B — What the three arms receive

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Revise the V3 output with the spine** | One source of notes | The multi-agent arm is no longer an independent generation | **Reject** |
| **B. V3 gets the prompt. Spine arms get an empty-event scaffold with the locked timeline** | Arms share key, meter, bars, sections, and instruments. Empty `events` is legal on V2. Preservation is measured on a separate case that uses a real source | Spine agents may return an invalid draft. That outcome is the invalid-rate metric | **Accepted** |
| **C. All arms go through the autonomous scheduler** | One stage graph | V3 direct generation would be reimplemented | **Reject** |

**Locked:** `scaffold_from_case` builds the V2 timeline and tracks with `events: []`. `v3_direct` ignores the scaffold and calls `generate_music_json`. Spine arms pass the scaffold as `source`. If scaffold validation fails, that arm’s composition status is `invalid` and the run continues with the other arms.

`v4_iterative_revision` uses revision mode `balanced` (two passes, already the product cap for that mode). `v4_multi_agent` uses mode `off`.

### Part C — Autonomous counters

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. New columns on `autonomous_runs`** | Queryable later | The product store does not need a benchmark schema | **Reject** |
| **B. Read the existing run row and count harness-invoked `retry_stage` successes** | Uses `agent_operation_count`, `revision_pass_count`, stage failure, and `retry_stage` as they are | Recovered count is harness-observed, because stage history is not stored | **Accepted** |
| **C. Treat spine `failure_count` as recovered stages** | No temp database | Those counters are not stage recovery | **Reject** |

**Locked:** Default CLI arms are the three comparison arms. `v4_autonomous` is included when `--arms` lists it or when `--include-autonomous` is set. It creates a project in a temp database via the existing composer start path, `include_rendering` false, same brief and seed. It does not call neural render. `failed_stages` counts stage rows with status `failed` at the end. `recovered_stages` counts stages the harness successfully retried once with `retry_stage` after a recoverable failure. If none fail, the count is 0. `time_to_valid_ms` is wall time from start until the head composition passes integrity plus hard constraints. If that never happens, the field is null and `invalid_composition` is true. Spine arms set `agent_calls` from `len(agent_sequence)` and `revision_passes` from `len(revision_history)`. They also store `model_calls` from `build_summary` after the arm ran inside `operation_span("run")`. They leave `recovered_stages` null with status `not_applicable`. RSS and remote cost are read from that closed run span.

### Part D — Seeds

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Assert every arm returns the case seed** | Looks strict | `llm_only` ignores seed, and the revision loop stores null | **Reject** |
| **B. Pass the case seed into every field the callee already has, and record seed status** | Reproducible where the code already branches on seed | Remote chat models can still return `unknown` | **Accepted** |

**Locked:** Each case has an integer seed. V3 sets `options.seed` and reads `seed` from the provenance dict `generate_music_json` returns. Autonomous start sets the run seed. The revision loop writes provenance `seed: None`; spine arms record `ignored_by_pipeline` and do not inject a seed into that dict. A returned seed equal to the case seed is `honored`. A non-null different seed is `unknown`. The melody-focused case sets V3 `options.pipeline` to `hybrid_plan_symbolic` so fake hybrid can demonstrate `honored`. The other cases stay `llm_only` and expect `ignored_by_pipeline` for V3.

### Part E — Conditioned briefs

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Add profile and reference fields to `AgentRunRequest`** | V4 would see the same soft prefs | New agent contract and prompt-channel risk in an eval plan | **Reject** |
| **B. Attach them only through `LLMMusicGenerationRequest`. Other arms record `not_wired`** | Uses the shipped generate path. The suite still contains both kinds | V4 numbers on those two cases are unconditioned generations scored against the hard brief | **Accepted** |

**Locked:** The reference case puts `reference_piano_phrase.json` on `style_references[].composition` with a `reference.conditioning.policy.v1` that borrows density and regenerates melody. The profile case sets `profile_id` to the fixture profile’s id and `profile_strength` to `normal`. It does not insert that profile into `PROJECT_DB_PATH`. V3 `profile_conditioning` is `attached` when `profile_active_for_fake` is true or the returned provenance contains `composer_profile_id`, and `skipped_missing_profile` when the real merge skips a missing id. V3 `reference_conditioning` is `attached` when the request carries `style_references`. Spine arms record both as `not_wired`. Prompts stay free of copied note lists. Regression does not fail an arm for `not_wired` or `skipped_missing_profile`.

### Part F — Listening

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Show metrics beside the clips** | One screen | The request forbids treating those numbers as taste | **Reject** |
| **B. CLI packet plus a session-only Agents-tab panel** | A/B and A/B/C use the existing piano roll. The packet schema has no metric keys | One lazy panel | **Accepted** |
| **C. CLI only** | Smaller | The product already plays `composition.v2`, and the workflow asks for a listening workflow | **Reject** |

**Locked:** `listen pack` writes a packet with two or three arms. Labels are `A`/`B` or `A`/`B`/`C`. A sealed map file (mode `0600` where the OS allows it) holds label → arm id, excluded from the packet. The panel loads a packet file, copies the selected label into `generationCandidate`, and turns on `setGenerationAuditionActive`. That path is what `resolvePlaybackSource` plays while generation audition is active. It leaves the open project composition alone and does not call apply. The judgment form stores a winner label and optional short comment. `listen unblind` refuses to run until a judgment file for that packet digest exists. Unblind output is the map plus the judgment. It does not print metric values.

### Part G — Regression

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Fail when any observed number moves** | Sensitive | Latency and RSS move with the machine. Tonal consistency is not a quality bar | **Reject** |
| **B. Gate hard metrics only, with tolerances stored in the suite** | A later change can break constraint compliance or validity and fail CI | Soft musical drift is visible only in the report | **Accepted** |

**Locked:** Default tolerances, overridable in the suite file: invalid composition rate may not rise; hard-constraint pass may not fall; structural pass may not fall; instrument-range pass may not fall; revision-preservation pass may not fall on the preservation case. Observed metrics are diffed in the report with `gate: false`. Missing baseline is exit code 2 and a log line `baseline_missing`, not a musical failure. A regression is exit code 1.

### Part H — Versions

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Git history of the suite file only** | No extra schema | A run can omit which bytes it used | **Reject** |
| **B. `benchmark_version` plus canonical SHA-256 inside every run, baseline, packet, and judgment** | The acceptance line is checkable | Callers must pass the suite path | **Accepted** |

**Locked:** Version string `1.0.0` on the first suite. Changing case text, seeds, tolerances, or fixtures bumps the version in the same commit. The CLI refuses to score a run against a baseline whose version or digest differs.

## Contract

### Suite (`workflow.benchmark.v1`)

File: `backend/app/fixtures/workflow_benchmark/suite.v1.json`.

| Field | Rule |
|-------|------|
| `benchmark_id` | `musical-workflows` |
| `benchmark_version` | `1.0.0` |
| `schema_version` | `workflow.benchmark.v1` |
| `cases` | Eight cases. Ids stable. Each has `seed` in `0..2147483647` |
| `tolerances` | Hard-metric gates listed in Part G |

Case kinds, one each:

| Id | Kind | What the brief locks |
|----|------|----------------------|
| `melody-line` | `melody-focused` | Piano melody plus the bass role the fake symbolic engine always adds, extra families disallowed, short form, V3 pipeline `hybrid_plan_symbolic` |
| `harmony-progression` | `harmonic` | Two instruments, explicit key, chordal second track family |
| `orchestra-tutti` | `orchestral` | Four catalog families, extra families disallowed |
| `sparse-texture` | `minimalist` | Low density band, few events expected, still a valid timeline |
| `two-section-form` | `multi-section` | Two sections whose bars sum to `duration_bars` |
| `reference-borrow` | `reference-conditioned` | Frozen reference fixture plus borrow/regenerate policy |
| `profile-soft` | `profile-conditioned` | Frozen profile fixture, strength `normal` |
| `keep-the-rest` | `revision-preservation` | Source is `composition_v2_expressive.json`. Only the iterative arm is scored for preservation |

Every generation case carries prompt fields that `LLMMusicGenerationRequest` already accepts: key, meter, tempo range, `duration_bars` at most 8 for this version, instruments, sections when the kind needs them, and a short instruction. Instructions are original text in the fixture. They are not logged.

`keep-the-rest` still has a brief so V3 and the multi-agent arm can run. Their preservation metric is `not_applicable`. The iterative arm’s source is the expressive fixture, not the empty scaffold. Preservation is `not_applicable` when the last revision pass has no affected ranges and no affected tracks (`assert_preserve_outside_targets` treats that as a full-score rewrite). When targets exist, it passes only if `events_outside_targets_fingerprint` matches the source. `revision_passes` may be 0. That is a valid measurement.

Reference and profile fixtures live beside the suite:

- `backend/app/fixtures/workflow_benchmark/reference_piano_phrase.json` — original four-bar V2, piano only
- `backend/app/fixtures/workflow_benchmark/profile_lyrical.json` — `composer.profile.v1` with coarse bands, no event arrays

### Metrics (`workflow.case_metrics.v1`)

Computed by `score_composition` from the arm’s composition, the case constraints, and, for the preservation arm, the source plus the revision plan targets. The function does not mutate the composition.

| Metric | Kind | Source |
|--------|------|--------|
| `hard_constraint_compliance` | hard | `validate_generation_constraints` errors empty |
| `structural_compliance` | hard | Section type, start bar, and bar count match the case locks; bar count matches `duration_bars` |
| `motif_recurrence` | observed | Count of motif families from the repetition analysis with at least two occurrences. Status `unavailable` if analysis fails |
| `section_contrast` | observed | Count of `section_lacks_contrast` findings from `check_section_contrast`. One-section cases: `not_applicable` |
| `tonal_consistency` | observed | `compute_metrics_for_samples` with metric `tonal_consistency` on the composition. The helper’s own detail already says the ratio ignores modulation and is not a quality score |
| `instrument_range_correctness` | hard | `validate_composition_integrity` emits no pitch-range error |
| `revision_preservation` | hard | `not_applicable` when the last pass has no affected ranges or tracks. Otherwise fingerprint equality outside those targets |
| `invalid_composition` | hard | Parser or integrity validation raised. One invalid arm increments the run’s invalid rate |
| `generation_latency_ms` | observed | Wall time around the arm call |
| `process_rss_kb` | observed | Closed run span `process_rss_kb`. `unavailable` when the span has no sample |
| `remote_cost_micros` | observed | Closed run span `provider_reported_cost_micros`. Status `unavailable` when null. `OperationSummaryV1` does not carry this field |
| `agent_calls` | autonomous counter | Spine: `len(agent_sequence)`. Autonomous: `agent_operation_count` |
| `model_calls` | observed | `build_summary(...).model_call_count` after `operation_span("run")`. Often 0 when fake agents skip `reserve_model_call` |
| `revision_passes` | autonomous counter | Spine: `len(revision_history)`. Autonomous: `revision_pass_count` |
| `failed_stages` | autonomous counter | Autonomous stage rows. Spine: `not_applicable` |
| `recovered_stages` | autonomous counter | Harness retry count. Spine: `not_applicable` |
| `time_to_valid_ms` | autonomous counter | See Part C |

Invalid rate for an arm is `invalid_cases / cases_scored` in the run summary. A failed arm still counts.

### Run file (`workflow.benchmark_run.v1`)

Directory: `EVAL_BENCHMARK_ROOT` (default `var/workflow-benchmarks`, gitignored). Layout:

```text
var/workflow-benchmarks/
  musical-workflows/1.0.0/<run_id>/
    run.json
    cases/<case_id>/<arm>.json
  baselines/musical-workflows/1.0.0.json
```

`run.json` stores `benchmark_id`, `benchmark_version`, `suite_sha256`, `created_at`, arm list, per-arm rollups, `musical_quality_claim: false`, and relative paths to case files. Case files store metric records, seed status, `profile_conditioning`, `reference_conditioning`, duration, and a composition SHA-256. They omit prompt text. Compositions for listening are copied only into the packet directory, not into the regression summary.

A baseline is a run file promoted by `baseline set --run <id>`. One baseline per suite version. Replacing it requires `--force` and logs the old and new run ids.

### Regression

`python -m app.workflow_eval.cli regress --run <id>` loads the baseline with the same version and digest. It writes `regression.json` next to the run. Each hard metric has `status: pass|fail|not_applicable`. Exit 0 when every comparable hard metric passes. Exit 1 when any fails. Exit 2 when the baseline or digest does not match.

### CLI

```text
python -m app.workflow_eval.cli suite digest
python -m app.workflow_eval.cli run --suite backend/app/fixtures/workflow_benchmark/suite.v1.json
python -m app.workflow_eval.cli run --cases melody-line --arms v3_direct,v4_multi_agent,v4_iterative_revision
python -m app.workflow_eval.cli run --include-autonomous
python -m app.workflow_eval.cli baseline set --run <id>
python -m app.workflow_eval.cli regress --run <id>
python -m app.workflow_eval.cli listen pack --run <id> --cases melody-line --mode abc
python -m app.workflow_eval.cli listen judge --packet <dir> --winner B
python -m app.workflow_eval.cli listen unblind --packet <dir>
```

`listen pack --mode ab` uses the first two arms in the case file. `--mode abc` requires three scored compositions and otherwise exits with code `listening_arm_missing`.

### Listening panel

Lazy panel on the Agents tab, under the autonomous panel, title “Listening”. File input reads a packet JSON the CLI wrote. Radio labels A/B or A/B/C. Play copies that label’s composition into `generationCandidate` and calls `setGenerationAuditionActive(true)`. Winner control writes a judgment JSON via download, the same download helper used for other exports. The panel does not render metric fields if they appear; unknown keys are ignored. It does not call apply and does not replace the project composition.

`frontend/src/utils/workflowListening.js` exposes `packetLabels`, `assertPacketHasNoMetrics`, and `judgmentFromWinner`. `node:test` covers A/B, A/B/C, and rejection of a packet that contains `hard_constraint_compliance` or `musical_quality`.

### Settings

`workflow_eval_settings.py` reads `EVAL_BENCHMARK_ROOT`. The default directory is created on first write. The loader refuses a root that resolves to `DATASET_ROOT` or `PROJECT_DB_PATH`.

## Logging

`LOG_LEVEL` controls verbosity. Do not log prompts, brief instructions, profile JSON, reference compositions, note events, or full case files. Case ids, arm ids, versions, digests, metric names, and statuses are safe.

- INFO at run start: `benchmark_id`, `benchmark_version`, `suite_sha256` prefix (12 hex chars), arm list
- INFO at arm finish: `case_id`, `arm`, `invalid_composition`, `seed_status`, `profile_conditioning`, `reference_conditioning`, `generation_latency_ms`, `remote_cost_status`
- INFO at regression finish: `status`, failing metric names only
- INFO at packet write: `case_id`, `mode`, packet digest prefix. Do not log the label map at INFO. DEBUG may log label letters without arm ids
- WARNING: `baseline_missing`, `suite_digest_mismatch`, `listening_arm_missing`, `benchmark_root_rejected`
- ERROR: exception type name only

Frontend logger name `workflowListening`. INFO when a packet loads (`label_count`) and when a judgment downloads (`winner` letter). Do not log compositions.

## Commit Plan
- **Commit 1** (after tasks 1–3): "feat: add a versioned musical benchmark suite and hard-metric scores"
- **Commit 2** (after tasks 4–6): "feat: score V3 and V4 workflow arms on the shared suite"
- **Commit 3** (after tasks 7–8): "feat: fail benchmark runs when hard metrics regress"
- **Commit 4** (after tasks 9–11): "feat: add blinded listening and cover the fake-mode comparison"
- **Commit 5** (after tasks 12–13): "docs: describe musical workflow evaluation and the roadmap milestone"

## Tasks

### Phase 1: Suite and scores
- [x] Task 1: Add benchmark schemas, settings, and the committed suite
  Deliverable: Pydantic models for `workflow.benchmark.v1`, `workflow.case_metrics.v1`, `workflow.benchmark_run.v1`, `workflow.listening_packet.v1`, and `workflow.listening_judgment.v1` with `extra=forbid`. `workflow_eval_settings.py` resolves `EVAL_BENCHMARK_ROOT` and rejects dataset and project database paths. `suite.v1.json` plus the reference phrase and lyrical profile fixtures. Digest helper returns the canonical SHA-256.
  Files: `backend/app/workflow_eval/schemas.py`, `backend/app/workflow_eval_settings.py`, `backend/app/fixtures/workflow_benchmark/suite.v1.json`, `reference_piano_phrase.json`, `profile_lyrical.json`
  Logging: INFO when a suite loads (`benchmark_version`, case count). WARNING `benchmark_root_rejected` with the reason code. DEBUG the digest prefix.

- [x] Task 2: Score one composition with the hard and observed metrics
  Deliverable: `score_composition` returns the metric record in the contract table. It calls `validate_generation_constraints`, integrity validation, and repetition analysis. `section_contrast` is the count of `section_lacks_contrast` findings from `check_section_contrast`, or `not_applicable` when the piece has one section. `tonal_consistency` comes from `compute_metrics_for_samples` with that metric name. `revision_preservation` is `not_applicable` when the last pass has no affected ranges or tracks; otherwise it compares `events_outside_targets_fingerprint`. Invalid input sets `invalid_composition` true and leaves musical metrics `unavailable`. Dump equality proves the composition bytes do not change.
  Files: `backend/app/workflow_eval/metrics.py`
  Logging: DEBUG per metric name and status. INFO one summary line with hard pass flags and `musical_quality_claim` false. No pitches and no event counts beyond the existing analysis logger.

- [x] Task 3: Build the scaffold and the preservation source
  Deliverable: `scaffold_from_case` returns a V2 timeline with empty events. The preservation case loader returns the expressive fixture as the iterative source and marks other arms `revision_preservation=not_applicable`.
  Files: `backend/app/workflow_eval/scaffold.py`
  Logging: INFO `case_id`, track count, bar count. DEBUG section types.

### Phase 2: Arms and storage
- [x] Task 4: Run the three comparison arms
  Deliverable: `run_arm` dispatches V3 `generate_music_json`, spine mode off, and spine mode `balanced`. Seeds follow Part D: V3 seed status is the provenance dict’s `seed`. Spine provenance seed stays null and the arm records `ignored_by_pipeline`. Conditioning follows Part E: V3 sends the fixture profile id and inline `style_references` composition, and records `profile_conditioning` and `reference_conditioning` from `profile_active_for_fake`, provenance `composer_profile_id`, and whether `style_references` is set. A skipped real profile load is `skipped_missing_profile`. The harness does not insert into `PROJECT_DB_PATH`. Each spine call runs inside `async with operation_span("run")`, then `build_summary(span)`, matching `backend/app/routers/ai_agents.py`. `agent_calls` is `len(agent_sequence)`. `revision_passes` is `len(revision_history)`. `model_calls` is `summary.model_call_count`. `process_rss_kb` and `provider_reported_cost_micros` come from the closed run span. Each result includes the composition or an invalid flag. Fake mode does not call a remote provider.
  Files: `backend/app/workflow_eval/pipelines.py`
  Depends on tasks 1–3.
  Logging: INFO at arm start and finish as in the Logging section. WARNING when a conditioning status is `not_wired` or `skipped_missing_profile` (code only). ERROR exception type.

- [x] Task 5: Collect autonomous-run counters
  Deliverable: `run_autonomous_arm` starts a composer run in a temp database, skips render, reads the existing counters, retries one recoverable failure through `retry_stage` when one occurs, and records `time_to_valid_ms`. Temp database path stays under the benchmark root and is removed in a `finally` block.
  Files: `backend/app/workflow_eval/autonomous_arm.py`
  Depends on task 4.
  Logging: INFO `case_id`, `agent_calls`, `revision_passes`, `failed_stages`, `recovered_stages`, `time_to_valid_ms` or null. Do not log the brief.

- [x] Task 6: Store versioned runs
  Deliverable: Writer creates the directory layout and `run.json` with version and suite digest. Case files store metric JSON and composition hash. Reader rejects a truncated run. Root escape outside `EVAL_BENCHMARK_ROOT` raises before write.
  Files: `backend/app/workflow_eval/store.py`
  Depends on tasks 1 and 4.
  Logging: INFO run id prefix (12), version, path relative to the root. WARNING on escape attempt with code `benchmark_path_escape`.

### Phase 3: Regression and CLI
- [x] Task 7: Compare a run with its baseline
  Deliverable: `regress` implements Part G and Part H. Output lists hard metrics with pass, fail, or not applicable. Observed diffs are present and marked `gate: false`.
  Files: `backend/app/workflow_eval/regress.py`
  Depends on task 6.
  Logging: INFO failing hard metric names. WARNING `baseline_missing` and `suite_digest_mismatch`.

- [x] Task 8: Expose the CLI
  Deliverable: `python -m app.workflow_eval.cli` subcommands in the contract. Exit codes match regression and listening errors. `--cases` and `--arms` filter without dropping suite identity.
  Files: `backend/app/workflow_eval/cli.py`, `backend/app/workflow_eval/__main__.py`
  Depends on tasks 4–7.
  Logging: INFO subcommand name and the run id prefix. Reuse arm and regression logs. No argument echo of instruction text.

### Phase 4: Listening and tests
- [x] Task 9: Pack, judge, and unblind
  Deliverable: Packet schema omits metric names by validator (a forbidden-key list covering the metric ids, cost, latency, and arm ids). Sealed map is a sibling file. Judge writes `workflow.listening_judgment.v1`. Unblind checks the packet digest and refuses when the judgment is missing.
  Files: `backend/app/workflow_eval/listening.py`
  Depends on task 6.
  Logging: INFO packet digest prefix and mode. WARNING `listening_arm_missing` and `judgment_missing`. DEBUG label letters only.

- [x] Task 10: Add the session listening panel
  Deliverable: Lazy Agents-tab panel and `workflowListening.js` as specified. Play writes the selected label into `generationCandidate` and calls `setGenerationAuditionActive(true)` so `resolvePlaybackSource` auditions that copy. The panel does not call apply and does not replace the project composition. The panel has `data-testid="workflow-listening-panel"`.
  Files: `frontend/src/components/WorkflowListeningPanel.jsx`, `frontend/src/utils/workflowListening.js`, `frontend/src/components/MultiAgentPanel.jsx`
  Depends on task 9.
  Logging: `createAppLogger('workflowListening')` as in the Logging section.

- [x] Task 11: Cover the suite, regression, blinding, and one fake comparison
  Deliverable: Pytest for digest stability, metric scoring on the expressive fixture, preservation not applicable versus applicable, regression exit behavior on a synthetic worse invalid rate, packet key rejection, and one `LLM_FAKE_MODE` run of `melody-line` through the three comparison arms that writes a run file with version `1.0.0`. One autonomous-arm test on `sparse-texture` with a temp database asserts the counter keys exist. `node:test` for the blinding helper.
  Files: `backend/tests/test_workflow_eval_suite.py`, `backend/tests/test_workflow_eval_metrics.py`, `backend/tests/test_workflow_eval_regress.py`, `backend/tests/test_workflow_eval_listening.py`, `backend/tests/test_workflow_eval_fake_run.py`, `frontend/src/utils/workflowListening.test.js`
  Depends on tasks 1–10.
  Logging: Tests assert log events contain `case_id` and do not contain the fixture instruction string.

### Phase 5: Documentation
- [x] Task 12: Document the framework
  Deliverable: `docs/workflow-evaluation.md` with the suite kinds, the four arms, hard versus observed metrics, seed status, listening unblind rule, CLI, `EVAL_BENCHMARK_ROOT`, and the statement that automated metrics are not musical taste. Link from `README.md`, `docs/composition-critique.md`, `docs/multi-agent.md`, `docs/autonomous-composer.md`, and `docs/music-transformer.md` (separate from symbolic eval). `AGENTS.md` gains the package and CLI rows. `.env.example` documents `EVAL_BENCHMARK_ROOT`. `.gitignore` ignores `var/workflow-benchmarks/`.
  Files: those docs and `AGENTS.md`, `.env.example`, `.gitignore`
  Depends on tasks 8 and 9.
  Logging: none in docs. Mention the log redaction rules in the doc.

- [x] Task 13: Add the roadmap milestone
  Deliverable: Unchecked milestone **V4 musical workflow evaluation** in `.ai-factory/ROADMAP.md` with a one-line description that names the versioned suite and hard-metric regression. Leave it unchecked until task 11 is green, then check it and add the completed row.
  Files: `.ai-factory/ROADMAP.md`
  Depends on task 11.
  Logging: none.
