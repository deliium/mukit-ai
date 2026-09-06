# Refactor LLM Generator Into Multi-Stage Composition V1 Composer

Created: 2026-09-06
Branch: current branch, no new branch requested

## Settings

- Testing: yes, deterministic mocked tests required; real-provider checks must be opt-in and excluded from the normal suite.
- Logging: verbose, structured `logging` extras for graph stages, provider/model, prompt sizes, section/track/event counts, validation failures, repair attempts, and sanitized provider errors. Never log API keys or full freeform prompts beyond existing sanitized previews.
- Docs: yes, update user-facing generation docs and Composition V1 guidance.
- Roadmap Linkage: none, no roadmap artifact exists in this repo.

## Current State

- `backend/app/services/llm_music_generator.py` contains the existing LangGraph integration. It currently builds a two-node graph: `call_llm` then `parse_and_validate`.
- Provider discovery and selection already support OpenAI and DeepSeek via `backend/app/llm_settings.py`, `GET /llm/models`, and `LLMModelSelection` in `backend/app/schemas.py`.
- Generation currently asks one LLM call to return a legacy JSON shape with top-level `sections`, `tracks`, `harmony`, and `notes`; `normalize_composition_json` migrates explicit top-level notes to canonical `composition.v1` track-local events.
- `Composition` in `backend/app/schemas.py` is already canonical V1 and validates schema version, key, time signature, duration, contiguous sections, unique track IDs, event pitch/velocity/timing, and composition bounds.
- Existing deterministic tests cover provider errors, mocked generation success, legacy normalization, canonical schema, MusicXML rendering, and routes.
- Existing docs already describe `composition.v1`, but the generation architecture still says the backend builds a single strict JSON prompt.

## Goals

- Keep the existing LangGraph, LangChain `ChatOpenAI`, OpenAI-compatible provider, model override, and FastAPI response path.
- Refactor generation into musically meaningful graph stages that produce complete playable `composition.v1` note-event data rather than relying on one arbitrary full-composition JSON blob.
- Generate at least form/sections, harmonic progression, primary melody, bass, and harmonic/accompaniment track; add requested instruments such as strings when practical.
- Preserve section-to-section context so motifs recur and develop.
- Add deterministic post-generation validation for structural and musical integrity beyond Pydantic schema checks.
- Add a graph-level repair/retry path for invalid stage or final output, with actionable frontend errors when repair fails.
- Put practical caps on initial V1 generation size so requests remain reliable and affordable.
- Provide tests and docs for the staged composer and opt-in real-provider smoke test path.

## Non-Goals

- Do not replace LangGraph with a separate orchestration framework.
- Do not remove legacy normalization unless a later cleanup explicitly decides to remove backwards compatibility.
- Do not make the renderer or playback layer invent missing notes from harmony.
- Do not include real API calls in the normal test suite.

## Proposed Design

### Graph Stages

Refactor `_build_generation_graph()` in `backend/app/services/llm_music_generator.py` into a staged LangGraph with typed state fields for intermediate musical artifacts:

- `plan_form`: derive bounded global form from prompt parameters, requested bars, key, meter, tempo, genre, mood, complexity, and requested sections.
- `plan_harmony`: generate per-section harmonic progression metadata using the form and preserving cadence goals.
- `compose_melody`: generate a primary melody track in canonical tick timing, carrying a motif/context summary across sections.
- `compose_bass`: generate bass events from form, harmony, meter, and melody contour.
- `compose_accompaniment`: generate harmonic/accompaniment events, including piano left/right hand handling and optional strings/pads when requested.
- `assemble_composition`: merge stage outputs into canonical `Composition` JSON with stable track IDs, MIDI metadata, sections, harmony, and `tracks[].events[]`.
- `validate_composition`: run Pydantic validation plus deterministic musical integrity checks.
- `repair_composition`: retry only invalid stage/final output using a correction prompt with structured validation diagnostics.

Use conditional edges from validation to either `END` or `repair_composition`, and from repair back to the failed generation/assembly stage or final validation. Keep `request.options.max_retries` as the retry budget.

### Intermediate JSON Contracts

Define small internal Pydantic models close to `llm_music_generator.py` or in a new focused module such as `backend/app/services/composition_planner.py` only if the file becomes too large:

- `ComposerFormPlan`: tempo, key, time signature, bar count, sections with `type`, `start_bar`, `bar_count`, dynamic/intensity notes, and instrumentation plan.
- `ComposerHarmonyPlan`: chord events keyed by bar and section, with cadence/function metadata if useful.
- `ComposerMotifContext`: compact motif IDs, intervals/rhythm cells, section development notes, and handoff context for recurrence.
- `ComposerTrackDraft`: track metadata and canonical-ish note events before final assembly.

Keep intermediate outputs smaller than final `Composition` where possible, and validate each stage before using it downstream.

### Request Bounds

Add deterministic generation bounds in `llm_music_generator.py` or a helper:

- Initial staged V1 generation cap: clamp or reject prompt sizes above a practical bar/track/event threshold for LLM generation, for example 32 bars and 6 non-drum requested instruments initially.
- Prefer actionable rejection for oversized initial requests: HTTP `400` or `422` with text such as `Initial LLM Composition V1 generation supports up to 32 bars and 6 instruments; reduce duration or instrumentation.`
- Keep schema-level `duration_bars <= 512` if non-LLM/manual workflows need it, but enforce a lower LLM-generation cap before provider calls.
- Log caps with requested/effective bars, requested instrument count, complexity, provider, and model.

### Deterministic Validation

Add a focused validator module, for example `backend/app/services/composition_validator.py`, returning a structured result with errors and warnings:

- Schema integrity: `Composition.model_validate()` must pass.
- Boundaries: `duration_ticks == bar_count * bar_duration_ticks`, sections exactly cover the composition, events fit composition duration.
- Bar lengths: for each non-drum track, detect impossible overflow inside bars and optionally warn about very sparse bars; do not require every bar to be fully filled because rests are implicit.
- Track requirements: fail if required roles are missing: `melody`, `bass`, and `harmony` or requested equivalent accompaniment; fail if substantial requested instruments are missing without an explicit warning/reason.
- Note density: fail if a required musical track has no events or too few events for the requested bar count/complexity; fail harmony-only output.
- Timing: all `start_tick` and `duration_ticks` align to expected rhythmic grid for the stage contract, stay non-negative, and do not cross composition boundaries unintentionally.
- Pitch ranges: enforce practical ranges by role/instrument, e.g. bass C1-C4, piano bass C2-B3, piano treble C4-C7, strings C3-C7, melody role commonly C4-C6 unless instrument-specific.
- Track ranges: MIDI program/channel/volume/pan already have Pydantic bounds; add semantic checks such as drums on channel 10 when `is_drum`.
- Harmony usefulness: warn if harmonic metadata is sparse or does not cover major section starts, but fail only when harmony is used as a substitute for notes.

The validator should produce machine-readable diagnostic codes such as `missing_required_track`, `empty_required_track`, `event_out_of_range`, `bar_overflow`, `oversized_generation_request`, and `schema_invalid` so repair prompts and frontend errors are actionable.

### Provider And Error Handling

- Preserve `_select_provider()` and `_selected_model()` behavior exactly, including model override.
- Keep `ChatOpenAI` with `base_url` so DeepSeek remains OpenAI-compatible.
- Wrap provider/API failures with stage context: provider, model, stage name, retry count, sanitized exception type/detail.
- Update FastAPI error handling in `backend/app/main.py` so invalid/repair failures include sanitized actionable detail rather than only `LLM returned invalid or non-playable music JSON`.
- Ensure repair failures are distinguishable from provider failures, validation failures, and unsupported-provider errors.

## Tasks

### Phase 1: Stage Contracts And Bounds

- [x] Task 1: Define internal staged composer state and intermediate output contracts.
  Files: `backend/app/services/llm_music_generator.py`; optionally `backend/app/services/composition_planner.py` if extracted.
  Deliverable: `_GenerationState` includes form, harmony, melody, bass, accompaniment, motif/context, validation diagnostics, stage name, and retry counters. Intermediate models or typed dictionaries describe each stage output with small JSON contracts.
  Logging: DEBUG log stage state summaries with counts and IDs only; INFO log graph setup with stage names; never log API keys or full raw prompts.

- [x] Task 2: Add deterministic LLM generation request bounds.
  Files: `backend/app/services/llm_music_generator.py`; `backend/app/main.py` if a new exception maps to `400`/`422`; tests in `backend/tests/test_llm_music_generation.py` or a new validator test file.
  Deliverable: before provider calls, reject or cap oversized initial V1 generation requests using practical upper bounds for bars, instruments, and approximate event budget. Error messages must tell the frontend what to reduce.
  Logging: WARNING log rejected oversized requests with requested bars, instrument count, complexity, provider/model if selected, and bound values.

### Phase 2: Multi-Stage LangGraph Refactor

- [x] Task 3: Replace the two-node graph with staged LangGraph nodes while preserving provider calls.
  Files: `backend/app/services/llm_music_generator.py`.
  Deliverable: `_build_generation_graph()` adds nodes for form, harmony, melody, bass, accompaniment, assembly, validation, and repair. Existing `generate_music_json()` still returns `(Composition, warnings, provider)` and still uses `_select_provider()`.
  Logging: INFO log each stage start/completion with provider, model, stage name, attempt, prompt/response length, section count, track role, and event count; DEBUG log sanitized prompt parameters and compact stage summaries.

- [x] Task 4: Implement form/section generation prompt and validation.
  Files: `backend/app/services/llm_music_generator.py` and intermediate contract file if created.
  Deliverable: stage respects requested key, time signature, approximate tempo, requested bars, sections, genre, mood, complexity, and freeform instructions. It produces contiguous form with start bars and musical intent per section.
  Logging: INFO log resolved bar count, section count, tempo, key, time signature; WARNING log mismatch corrections or stage validation failures.

- [x] Task 5: Implement harmonic progression stage.
  Files: `backend/app/services/llm_music_generator.py`.
  Deliverable: stage generates chord metadata across sections and cadence goals; harmony remains metadata and is explicitly passed to later note-generation stages.
  Logging: INFO log harmony item count and section coverage; DEBUG log first few chord symbols and bars only.

- [x] Task 6: Implement primary melody stage with motif context.
  Files: `backend/app/services/llm_music_generator.py`.
  Deliverable: stage emits canonical tick-based note events for a melody role, plus compact motif/context for recurring and developing material across sections. The acceptance scenario must produce enough melody notes for immediate playback.
  Logging: INFO log melody event count, covered bars, pitch range, motif IDs; WARNING log empty/sparse melody diagnostics.

- [x] Task 7: Implement bass stage using harmony and melody context.
  Files: `backend/app/services/llm_music_generator.py`.
  Deliverable: stage emits canonical tick-based bass notes that follow harmonic roots, cadences, and section intensity while staying in bass range.
  Logging: INFO log bass event count, covered bars, pitch range; WARNING log out-of-range or sparse bass validation diagnostics.

- [x] Task 8: Implement accompaniment and optional requested-instrument stages.
  Files: `backend/app/services/llm_music_generator.py`; possibly `backend/app/services/composition_normalizer.py` if instrument program mapping needs small additions.
  Deliverable: stage emits harmonic/accompaniment track events and practical optional tracks such as strings/pad/countermelody. Piano handling should prefer one piano track with grand staff when representing right/left-hand material unless existing rendering constraints require another compatible layout.
  Logging: INFO log generated roles/instruments, event counts per track, skipped optional instruments with reasons; WARNING log missing required accompaniment.

### Phase 3: Assembly And Validation

- [x] Task 9: Assemble final canonical `composition.v1` directly.
  Files: `backend/app/services/llm_music_generator.py`; optionally new helper module.
  Deliverable: output is a `Composition` object with `schema_version`, `tempo`, `key`, `time_signature`, `ticks_per_quarter`, `bar_count`, `duration_ticks`, contiguous sections, track metadata, `tracks[].events[]`, and harmony. Avoid relying on legacy top-level `notes` for the staged happy path.
  Logging: INFO log final schema version, bar count, track count, event count, and duration ticks; DEBUG log track IDs/roles/event counts.

- [x] Task 10: Add deterministic composition integrity validator.
  Files: new `backend/app/services/composition_validator.py`; tests in `backend/tests/test_composition_validator.py`.
  Deliverable: validator checks schema integrity, bar/timing bounds, pitch ranges, required roles/tracks, missing requested instruments, empty/sparse required tracks, track MIDI semantics, composition boundaries, and harmony-only substitution. It returns structured errors/warnings usable by repair prompts and HTTP details.
  Logging: DEBUG log each validation category summary; WARNING log failed validation with diagnostic codes and compact context; INFO log validation pass with event/track/bar summaries.

- [x] Task 11: Integrate validator and repair/retry graph path.
  Files: `backend/app/services/llm_music_generator.py`; `backend/app/main.py`.
  Deliverable: validation failure routes to repair until `request.options.max_retries` is exhausted. Repair prompts include prior stage output plus structured diagnostics. Final failure raises `InvalidLLMOutputError` with safe actionable details for the frontend.
  Logging: WARNING log repair attempt start and diagnostic codes; INFO log successful repair; ERROR log exhausted repair budget with provider/model/stage/retry count and diagnostic codes.

### Phase 4: Tests

- [x] Task 12: Add deterministic mocked staged-generation tests.
  Files: `backend/tests/test_llm_music_generation.py` and/or new `backend/tests/test_llm_staged_composer.py`.
  Deliverable: tests mock provider/stage calls with no real API usage and cover successful 16-32 bar acceptance scenario shape, stage sequencing, model override preservation, OpenAI/DeepSeek provider selection, invalid output repair success, repair exhaustion, missing required tracks, harmony-only rejection, oversized request rejection, and actionable API error details.
  Logging: tests assert representative log messages/diagnostic codes using `caplog` where useful, without making tests brittle on exact prompt text.

- [x] Task 13: Add validator unit tests.
  Files: `backend/tests/test_composition_validator.py`.
  Deliverable: deterministic tests cover bar overflow, note out of range, event outside composition, missing melody/bass/accompaniment, empty required track, missing requested strings warning/error behavior, track range semantics, and valid acceptance-scenario composition.
  Logging: tests verify failed validations emit structured diagnostic logs.

- [x] Task 14: Add opt-in real-provider smoke-test path.
  Files: new `backend/tests/test_llm_real_provider_smoke.py` or `backend/scripts/llm_smoke_test.py`; docs in `docs/testing.md` and/or README.
  Deliverable: normal `pytest` does not spend credits. Real provider smoke test runs only when explicitly enabled, for example `RUN_LLM_SMOKE=1` plus provider API key, and requests a small bounded composition. It validates canonical notes and prints provider/model/stage diagnostics.
  Logging: INFO log smoke test provider/model, bars, tracks, events, and validation outcome; skip reason must be clear when env vars are absent.

### Phase 5: Documentation And Verification

- [x] Task 15: Update Composition V1 and generation documentation.
  Files: `docs/composition-v1.md`, `docs/testing.md` if present/needed, `README.md`.
  Deliverable: docs explain staged generation, required explicit note events, harmony as metadata, practical size bounds, repair/retry behavior, actionable errors, mocked tests, and opt-in real-provider smoke tests.
  Logging: no runtime logging; docs must mention useful DEBUG/INFO log fields and secret handling.

- [x] Task 16: Run verification commands and fix failures.
  Files: code/tests/docs affected by earlier tasks.
  Deliverable: run backend tests, targeted staged/validator tests, and any available frontend route/error tests if touched. Confirm no normal test requires API keys.
  Logging: capture failures in terminal output; if real smoke test is not run, document it as intentionally skipped unless `RUN_LLM_SMOKE=1` is supplied.

## Acceptance Criteria

- `POST /llm/generate-music-json` returns canonical `composition.v1` with track-local note events for a 16-32 bar prompt like: `Melancholic piano piece in A minor, 4/4, around 80 BPM, with piano melody, piano accompaniment, bass and strings; quiet opening, stronger middle section, resolved ending.`
- Returned composition includes contiguous sections, useful harmony metadata, melody events, bass events, accompaniment events, and practical strings events when requested.
- Harmony is never accepted as a substitute for playable `tracks[].events[]`.
- Requested key, meter, approximate tempo, bar count, instruments, genre, mood, and complexity are reflected or produce actionable warnings/errors.
- Missing substantial required sections/tracks causes validation/repair and ultimately an actionable failure, not a successful response.
- OpenAI and DeepSeek provider discovery/selection/model override still work.
- Provider failures, invalid LLM output, repair exhaustion, and oversized generation requests map to distinct actionable frontend-facing errors.
- Normal tests use mocks and do not spend real API credits.
- Real-provider smoke test is opt-in only.

## Verification Commands

- `cd backend && ../.venv/bin/python -m pytest`
- `cd backend && RUN_LLM_SMOKE=1 LLM_SMOKE_PROVIDER=openai ../.venv/bin/python -m pytest backend/tests/test_llm_real_provider_smoke.py` only when intentionally spending real API credits and required keys are configured. Adjust path as needed if the smoke test lives under `backend/tests/` and the command is run from repo root.

## Risks And Mitigations

- Risk: Staged prompts increase request count and latency. Mitigation: keep contracts compact, cap bars/instruments, and log stage timings/counts.
- Risk: LLM note density may be sparse or musically weak. Mitigation: deterministic validator checks required role density and repair prompts include exact missing/sparse diagnostics.
- Risk: Repair loops can become expensive. Mitigation: honor `max_retries`, reuse structured diagnostics, and stop with actionable errors.
- Risk: More intermediate models can overcomplicate the module. Mitigation: start in `llm_music_generator.py`; extract only if size/readability requires it.
- Risk: Instrument role mapping can conflict with piano grand-staff rendering. Mitigation: preserve existing renderer constraints and test piano melody/accompaniment/bass acceptance explicitly.

## Commit Plan

- Commit 1: `refactor: add staged composition contracts and request bounds` after Tasks 1-2.
- Commit 2: `refactor: orchestrate multi-stage llm composition graph` after Tasks 3-9.
- Commit 3: `feat: validate and repair canonical llm compositions` after Tasks 10-11.
- Commit 4: `test: cover staged composition generation` after Tasks 12-14.
- Commit 5: `docs: document staged composition v1 generation` after Tasks 15-16.
