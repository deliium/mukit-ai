# Implementation Plan: Enforce Generation Prompt Constraints

Branch: main
Created: 2026-09-07

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Roadmap Linkage
Milestone: "none"
Rationale: Skipped by user; this is correctness hardening for the completed multi-stage composer milestone.

## Goal

Treat explicit musical parameters in `LLMMusicGenerationRequest` as immutable generation constraints, carry them through every LangGraph stage, validate the final canonical `Composition` against the original request, perform targeted repair on violations, and return a clear structured failure instead of contradictory music.

The acceptance invariant is: a request for `F# minor` must never successfully return a composition whose metadata, harmony, or clearly established tonal center is `A minor`.

## Audit Findings

- `backend/app/services/llm_music_generator.py::_after_form_plan` currently turns key and meter mismatches into warnings and only logs a bar-count mismatch. The incompatible `ComposerFormPlan` is retained and becomes the authority for all later stages.
- Harmony, melody, bass, and accompaniment prompts consume the potentially replaced form key rather than an immutable copy of the original hard constraints. Several rules say `prefer`, `should`, or `when practical` for values that must be mandatory.
- `backend/app/services/composition_validator.py::validate_composition_integrity` checks schema/playability and receives requested instruments, but it does not receive requested key, meter, tempo bounds, duration, or sections. Missing strings/pad/guitar/violin/cello are currently warnings rather than hard failures.
- Current validation does not compare `Composition.key`, harmony chord symbols, or `tracks[].events[]` tonal evidence to the requested key. A structurally valid A-minor score therefore passes an F#-minor request.
- `assemble_composition` builds canonical metadata exclusively from the LLM form output. The active staged path does not have an explicit normalization node or a post-normalization request-conformance check.
- `repair_composition` routes integrity diagnostics to a stage, but hard-constraint diagnostics do not exist and repair prompts do not restate an authoritative immutable constraint block.
- `backend/app/services/fake_llm.py` returns a fixed fixture before entering the graph or integrity validation. It only warns when duration differs and can similarly return output that contradicts request constraints.
- `LLMMusicGenerationResponse.warnings` is a string list. Structured `ValidationDiagnostic` objects are internal and are discarded on success; repair-exhaustion HTTP `502` details are flattened to a string.
- `LLMPromptParameters.sections` and `duration_bars` can conflict because request validation does not require explicitly supplied section bars to sum to the requested duration.

## Constraint Policy

Hard constraints are requested key (when non-null), time signature, total bar count, explicitly requested section sequence and bar counts, requested instrument families, and inclusive tempo bounds. Soft creative inputs are genre, mood, complexity, and freeform instructions unless a future request contract explicitly promotes a value to the hard set.

When no key is requested, the form stage may select a key; that selected key is then frozen for harmony and note-content consistency checks. When no sections are requested, the form stage may design a contiguous structure totaling `duration_bars`; that structure is then frozen for all later stages.

Requested instruments are required instrument families, not a strict one-track-per-token count. Multiple tracks may use a requested family to satisfy melody, bass, and accompaniment roles. An unrequested instrument family is not allowed by default; any future opt-in for extra instrument families must be explicit in the request schema and validator rather than inferred from genre, mood, or freeform instructions.

Tonal validation must assess tonal center, not require strict diatonic membership. It will combine exact key metadata, parseable harmony function/root evidence, and duration/accent-weighted pitch-class evidence from non-drum note events. A few chromatic tones, borrowed chords, secondary dominants, and harmonic/melodic-minor alterations remain valid. Hard failure requires explicit metadata contradiction or sufficiently strong aggregate evidence for a competing tonal center; sparse or genuinely ambiguous evidence produces a structured warning rather than a false rejection.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat: define generation constraints and tonal validation`
- **Commit 2** (after tasks 4-6): `feat: enforce constraints across composer pipeline`
- **Commit 3** (after tasks 7-8): `test: cover generation constraint enforcement`

## Tasks

### Phase 1: Constraint Contracts And Analysis

- [x] Task 1: Define the immutable generation constraint snapshot and request consistency rules.

  Deliverable: Add an internal typed constraint contract, preferably in a focused `backend/app/services/generation_constraints.py` service, derived exactly once from `LLMMusicGenerationRequest`. It must separate hard fields from soft preferences, normalize instrument-family matching, preserve requested section order/counts, and support freezing an LLM-selected key/section structure only when the user omitted those fields. Update `backend/app/schemas.py` so explicitly supplied sections must sum to `duration_bars`; contradictory requests fail as request validation before any provider call. Keep `Composition` and its playable event contract unchanged.

  Files: `backend/app/schemas.py`, `backend/app/services/generation_constraints.py`, `backend/app/services/composition_planner.py`.

  Dependencies: none.

  Logging: DEBUG-log a sanitized hard-constraint summary and whether key/sections were user-specified or form-resolved; INFO-log successful snapshot creation; WARN-log request conflicts by diagnostic code and counts. Never log full freeform instructions or provider secrets.

- [x] Task 2: Implement deterministic tonal-center analysis for harmony and playable note events.

  Deliverable: Add a pure `backend/app/services/composition_tonality.py` service that parses normalized major/minor keys and common chord symbols, scores requested-key and competing-key evidence, and returns structured evidence/confidence rather than a boolean-only result. Harmony scoring must recognize tonic/dominant/subdominant relationships, cadence/section-boundary weight, minor dominant chords such as `C#7` in F# minor, secondary dominants, borrowed chords, and unparseable symbols. Note scoring must ignore drum tracks, use pitch classes with duration and metrically strong/phrase-edge weighting, and evaluate aggregate center rather than individual non-diatonic notes. Define documented minimum-evidence and confidence-margin thresholds so obvious `Am/F/C/Dm/E7` and A-centered events contradict F# minor while legitimate F#-minor chromaticism does not.

  Files: `backend/app/services/composition_tonality.py`, `backend/app/services/composition_planner.py` for reusable diagnostic models only if needed.

  Dependencies: Task 1.

  Logging: DEBUG-log requested center, evidence counts, winning candidate, confidence/margin, unparseable chord count, and threshold decisions; INFO-log completed tonal analysis; WARN-log only contradictions or ambiguity. Log chord/note counts and pitch classes, not full compositions.

- [x] Task 3: Add deterministic final request-conformance validation and structured reports.

  Deliverable: Extend validation through a focused `validate_generation_constraints(composition, constraints)` function, keeping generic `validate_composition_integrity` usable by edit/export paths. Validate exact requested key metadata, tempo inclusion, time signature, `bar_count`, derived duration, requested section types/start bars/bar counts, required requested instrument families, and the explicit extra-track/instrument policy. Invoke tonal analysis for both harmony and actual `tracks[].events[]`; issue stable diagnostic codes with expected/actual and stage/track context. Add response-safe Pydantic report models in `backend/app/schemas.py` for constraints checked, errors, warnings, repair attempts, and final status.

  Files: `backend/app/services/generation_constraints.py`, `backend/app/services/composition_validator.py`, `backend/app/schemas.py`.

  Dependencies: Tasks 1 and 2.

  Logging: DEBUG-log each validation category and sanitized expected/actual summaries; INFO-log pass status and category counts; WARN-log violations with stable codes, responsible stage, and repairability; ERROR-log only final unrepaired hard-constraint failures.

### Phase 2: Pipeline Enforcement And Repair

- [x] Task 4: Carry the authoritative hard-constraint block through every LLM stage and reject stage drift.

  Deliverable: Store the immutable snapshot in `_GenerationState` and inject the same machine-readable hard/soft distinction into form, harmony, melody, bass, accompaniment/additional-track, and repair prompts. Replace advisory wording (`prefer`, `should`, `when practical`) for hard fields with explicit invariants. Validate each parsed stage output against its applicable constraints before accepting it: form cannot replace requested key/meter/tempo/duration/sections/instrumentation; harmony must remain in the locked tonal context; every requested family must be assigned to a generated track stage; unrequested families must obey the explicit policy. Form-selected key/sections may be locked only for omitted request fields. Stage drift must become structured diagnostics and repair routing, not warnings that allow downstream continuation.

  Files: `backend/app/services/llm_music_generator.py`, `backend/app/services/composition_planner.py`, `backend/app/services/generation_constraints.py`.

  Dependencies: Tasks 1-3.

  Logging: INFO-log stage constraint-check pass/fail and repair attempt; DEBUG-log sanitized constraint IDs/values, responsible stage, instrument assignments, and section summaries; WARN-log drift with diagnostic codes. Do not log full prompts; remove the existing prompt preview if it could expose freeform instructions.

- [x] Task 5: Make assembly and normalization constraint-preserving and add the final validation gate.

  Deliverable: Add an explicit `normalize_composition` graph boundary after assembly (or an equivalently named deterministic node) that runs canonical normalization without changing locked metadata, form, or track policy. Compare pre/post-normalization hard fields and fail if normalization rewrites them. Build final metadata and sections from the locked constraint snapshot rather than trusting later LLM output. Run both generic integrity validation and request-conformance validation after normalization; `validation_ok` may be true only when both pass. Keep `composition.v1` unchanged and ensure MusicXML, MIDI, WAV, Tone.js playback, piano roll, and persistence continue receiving the same canonical `Composition` shape.

  Files: `backend/app/services/llm_music_generator.py`, `backend/app/services/composition_normalizer.py`, `backend/app/services/composition_validator.py`.

  Dependencies: Tasks 3 and 4.

  Logging: DEBUG-log pre/post normalization hard-field hashes or summaries and both validator result counts; INFO-log normalization path and final gate success; ERROR-log any attempted hard-field rewrite with field names only, never raw payloads.

- [x] Task 6: Route targeted repairs from hard-constraint diagnostics and expose structured outcomes.

  Deliverable: Extend `_infer_failed_stage`/repair routing so form/tempo/meter/section errors restart at `plan_form`, harmony-center errors restart at `plan_harmony`, aggregate note-center errors restart at `compose_melody` and continue through dependent parts, and missing/extra instrument errors restart at the earliest responsible track stage. Preserve valid upstream plans/drafts whenever the graph permits and always restate immutable constraints in repair prompts. Validate again after each repair. On exhausted retries, raise a domain error carrying sanitized diagnostics and map it in `backend/app/main.py` to a clear structured `502` detail. On success, return `LLMMusicGenerationResponse.validation` while retaining human-readable warnings for current UI compatibility. Apply the same final conformance gate to `fake_llm.py`; it must deterministically produce/transform a compliant fixture or fail clearly, never warn and return a contradictory fixture.

  Files: `backend/app/services/llm_music_generator.py`, `backend/app/services/fake_llm.py`, `backend/app/main.py`, `backend/app/schemas.py`, `frontend/src/api/musicApi.js`, and associated API/store tests if the response plumbing changes.

  Dependencies: Tasks 4 and 5.

  Logging: INFO-log repair target, preserved upstream stages, attempt count, and final report status; DEBUG-log diagnostic-to-stage routing and response diagnostic counts; WARN-log each targeted retry; ERROR-log exhausted repairs with codes and expected/actual summaries. Frontend logs report status/counts only.

### Phase 3: Regression Coverage And Documentation

- [x] Task 7: Add mocked-LLM unit, graph, API, and fake-provider regression tests.

  Deliverable: Extend `backend/tests/test_llm_staged_composer.py` using the existing `_invoke_chat` monkeypatch pattern to reproduce a 20-bar `F# minor`, `4/4`, piano/bass/strings request whose first mocked outputs return A-minor form, `Am/F/C/Dm/E7` harmony, and A-centered note events. Prove it is targeted for repair and can only succeed after compliant F#-minor outputs; with no repair budget or repeated bad output, prove it returns structured `502` diagnostics and never returns A minor. Add focused tests for exact tempo bounds, meter, total bars, section positions/counts, all requested instruments as errors, unrequested instrument policy, and no silent mutation during normalization/repair. Add tonal tests proving chromatic passing tones, harmonic/melodic-minor alterations, secondary dominants, and borrowed chords do not cause false failures. Update fake-provider tests so any fixture mismatch is repaired deterministically or rejected rather than warned through. Mock all LLM calls; do not consume API credits.

  Files: `backend/tests/test_llm_staged_composer.py`, `backend/tests/test_composition_validator.py`, new `backend/tests/test_composition_tonality.py`, `backend/tests/test_llm_routes.py`, `backend/tests/test_llm_fake_provider.py`, and frontend API tests only if response handling changes.

  Dependencies: Tasks 1-6.

  Logging: Assert representative INFO/WARN/ERROR records and stable diagnostic codes without asserting secrets/full prompts; use `caplog` to prove repair routing and exhaustion are distinguishable from provider failures.

- [x] Task 8: Document the hard/soft contract and verify all canonical consumers remain intact.

  Deliverable: Through the mandatory `$aif-docs` checkpoint, update `docs/composition-v1.md`, `docs/testing.md`, and API/request documentation in `README.md` where applicable. Document hard versus soft inputs, omitted-key/section resolution, instrument/extra-track policy, tonal-center semantics and chromaticism allowance, targeted repair behavior, structured validation response/failure shape, and diagnostic logging. Run focused backend tests, the complete backend suite, relevant frontend tests, and canonical export/playback regression tests.

  Files: `docs/composition-v1.md`, `docs/testing.md`, `README.md` as needed; no production consumer changes unless verification exposes a regression.

  Dependencies: Task 7.

  Logging: Document `LOG_LEVEL=DEBUG` diagnostics and secret-safety limits; verification must inspect logs for stage/provider/model/attempt/codes and confirm no full prompt, API key, or raw composition/export payload is emitted.

## Verification Commands

```bash
# Run from backend/
pytest tests/test_composition_tonality.py tests/test_composition_validator.py tests/test_llm_staged_composer.py tests/test_llm_routes.py tests/test_llm_fake_provider.py
pytest

# Run from the repository root
npm --prefix frontend test
```

## Acceptance Checks

- A mocked F#-minor request followed by persistent A-minor stage outputs exhausts repair and returns structured failure; no `Composition` is returned.
- A first-pass A-minor contradiction followed by corrected F#-minor harmony/events succeeds only after revalidation, with repair diagnostics retained.
- Final metadata, tempo, meter, duration, sections, and requested instrument families exactly satisfy the original hard constraints.
- Obvious A-minor harmonic and note-event evidence fails an F#-minor request even if metadata is relabeled F# minor.
- Valid F#-minor music containing chromatic passing tones, `C#7`, leading-tone `E#`, secondary dominants, or borrowed chords passes when its aggregate tonic center remains F# minor.
- Generic Composition V1 validation, region editing, persistence, rendering, MIDI/WAV export, browser playback, and piano-roll flows retain their canonical event-based behavior.
