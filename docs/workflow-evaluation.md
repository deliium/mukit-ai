# Musical workflow evaluation

Offline comparison of the shipped generation entry points on one versioned brief set. Automated numbers are constraint and structure checks. They are not musical taste. Reports set `musical_quality_claim: false`. Listening is a separate human judgment on a blinded packet.

This suite is not the Music Transformer symbolic eval. That study scores token samples. This one runs V3 generate, the V4 spine, and the V4 revision loop on the same briefs.

## Suite

Committed file: `backend/app/fixtures/workflow_benchmark/suite.v1.json` (`workflow.benchmark.v1`, version `1.0.0`, id `musical-workflows`).

Identity is `benchmark_id` + `benchmark_version` + the SHA-256 of the canonical JSON. Changing case text, seeds, tolerances, or fixtures bumps `benchmark_version` in the same commit.

| Case | Kind | What is locked |
|------|------|----------------|
| `melody-line` | melody-focused | Short form. Piano is the melodic line. Bass is listed too so the fake symbolic bass role stays inside the requested families. Extra families are disallowed. V3 pipeline `hybrid_plan_symbolic` |
| `harmony-progression` | harmonic | Two instruments, explicit key |
| `orchestra-tutti` | orchestral | Four families, extra families disallowed |
| `sparse-texture` | minimalist | Low-density brief, still a valid timeline |
| `two-section-form` | multi-section | Two sections whose bars sum to `duration_bars` |
| `reference-borrow` | reference-conditioned | `reference_piano_phrase.json` plus a policy that borrows density and regenerates melody |
| `profile-soft` | profile-conditioned | `profile_lyrical.json`, strength `normal` |
| `keep-the-rest` | revision-preservation | Iterative arm starts from `composition_v2_expressive.json` |

Brief instructions stay in the fixture. Logs do not print them, note lists, profile bodies, or reference events. Safe fields are case ids, arm ids, versions, digest prefixes, metric names, and statuses.

## Arms

| Arm | Entry |
|-----|--------|
| `v3_direct` | `generate_music_json` |
| `v4_multi_agent` | `run_spine_workflow` with revision mode off |
| `v4_iterative_revision` | `run_spine_workflow` with revision mode `balanced` |
| `v4_autonomous` | Composer start on a temp SQLite file, `include_rendering` false |

Default CLI arms are the first three. Add `v4_autonomous` with `--include-autonomous` or `--arms`.

Spine arms receive an empty-event `composition.v2` scaffold that locks key, meter, bars, sections, and instruments. The preservation arm uses the expressive fixture as its source instead. V3 ignores the scaffold and uses the prompt. Profile and reference attachments go through the V3 request only. Spine arms record `profile_conditioning` and `reference_conditioning` as `not_wired`. The harness does not insert the suite profile into `PROJECT_DB_PATH`.

## Hard metrics and observed numbers

Hard metrics can fail a regression: constraint compliance, structural compliance, instrument-range correctness, revision preservation, and invalid composition rate. Default tolerances (overridable in the suite) say the invalid rate may not rise and the pass rates may not fall.

Observed numbers are recorded and shown with `gate: false`: motif recurrence, section contrast, tonal consistency, latency, RSS, and remote cost. A missing provider cost stays `unavailable` with a null micros field. `tonal_consistency` is the existing diatonic coverage ratio. It ignores modulation and is not a quality score.

Revision preservation is `not_applicable` when the last revision pass has no affected ranges and no affected tracks. When targets exist, it passes only if events outside those targets still match the source.

## Seed status

Each case has an integer seed. Status is `honored`, `ignored_by_pipeline`, or `unknown`, taken from the callee's returned seed. V3 hybrid records `honored` when fake mode returns the case seed. V3 `llm_only` ignores the seed. Spine provenance stores a null seed, so those arms record `ignored_by_pipeline`.

## Runs, baselines, and regression

Reports go under `EVAL_BENCHMARK_ROOT` (default `var/workflow-benchmarks`, gitignored). The loader refuses a root that resolves to `DATASET_ROOT` or `PROJECT_DB_PATH`. The autonomous arm uses a temp database under that root and deletes it at the end of the run.

```text
var/workflow-benchmarks/musical-workflows/1.0.0/<run_id>/run.json
var/workflow-benchmarks/baselines/musical-workflows/1.0.0.json
```

`python -m app.workflow_eval.cli regress --run <id>` compares hard metrics to the baseline of the same version and digest. Exit 0 when they pass. Exit 1 on a regression. Exit 2 when the baseline is missing or the digest does not match (`baseline_missing`, `suite_digest_mismatch`).

Real model runs require `--real-models`. They still write only under the benchmark root. There is no product HTTP route for the suite.

## Listening

`listen pack` writes `workflow.listening_packet.v1` with labels A/B or A/B/C and the compositions. The packet has no pipeline ids, metric values, cost, or latency. A sibling label map (mode `0600` where the OS allows it) holds label → arm id.

The Agents tab **Listening** panel loads that packet, copies the selected label into the generation candidate, and turns on generation audition. Playback uses the existing piano-roll path. The panel does not apply the candidate and does not replace the open project composition.

`listen judge` writes `workflow.listening_judgment.v1`. `listen unblind` refuses until a judgment file for that packet digest exists. Unblind prints the map and the judgment. It does not print metric values.

## CLI

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

Run from `backend/` so `app` is importable. `LLM_FAKE_MODE=1` is the CI path.

## Logging

`LOG_LEVEL` controls verbosity. Do not log prompts, brief instructions, profile JSON, reference compositions, note events, or full case files. Regression summaries log failing metric names only.
