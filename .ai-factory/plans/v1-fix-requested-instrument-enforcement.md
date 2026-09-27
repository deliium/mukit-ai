# Implementation Plan: Fix Requested-Instrument Enforcement

Branch: main
Created: 2026-09-07

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes

## Goal

Make requested instruments represent required sound sources rather than an exact track list. A requirement is satisfied when at least one generated track's normalized `instrument` matches it, independently of display `name` and `role`; one instrument may support several distinct roles, while generation and repair must not add another track merely because an already-present instrument was overlooked.

The target invariant is: requested instruments define required sound sources, and generated tracks define the musical roles performed by those sound sources. For a piano/bass/strings request, piano melody plus piano accompaniment, one bass track, and strings pad is valid; a second unmotivated bass/bass track is not.

## Audit Findings

- `backend/app/services/generation_constraints.py` already centralizes a partial family taxonomy and final missing/unexpected-family checks. Satisfaction is existential across `track.instrument`, so the final hard check does not require the track count to equal the request count, but the taxonomy uses broad substring matching and the generic validator has a second, inconsistent implementation.
- `backend/app/services/composition_validator.py::_check_requested_instruments` concatenates raw instrument labels and uses substring checks. It can disagree with generation constraints, emits different severities, and provides no deterministic satisfaction or duplicate report.
- The staged graph always creates melody and bass drafts, then `llm_music_generator.py::_build_accompaniment_prompt` repeats every requested family and shows both piano and strings examples without identifying which requirements the accepted melody/bass drafts already satisfy. This overlapping responsibility can induce an accompaniment-stage bass/bass duplicate.
- `_after_accompaniment_stage` checks only missing and unexpected families. `_assemble_composition` correctly preserves all semantic tracks and only repairs duplicate IDs; no code currently recognizes duplicate musical purpose.
- Repair does not synthesize a deterministic missing track. It clears the responsible draft stage and asks the LLM to regenerate it. Therefore the satisfaction report must be recomputed from preserved drafts before each retry and passed into the repair prompt; stale stage diagnostics cannot be authoritative.
- `GenerationValidationReport` already exposes stable issues and repair-attempt count, but it does not report satisfied/missing instrument requirements, suspicious duplicate groups, or actions taken during repair.
- Fake generation runs the final generation-constraint gate and should share the same analyzer. Region editing, canonical/legacy normalization, project persistence/migration, exports, frontend structural validation, and playback have no requested-instrument contract and must not start enforcing historical generation requirements or deleting tracks.

## Semantics And Decisions

- Add one pure backend normalization/analyzer boundary for instrument requirements. Normalize case, surrounding/internal whitespace, separators, and a conservative alias registry. Required current aliases include acoustic/grand piano to `piano`, bass guitar/electric bass to `bass`, and string ensemble to `strings`; preserve existing V1 compatibility such as keyboard/piano and explicitly documented string-family matching. Do not collapse distinct instruments such as trumpet and trombone merely because both are brass.
- Keep two concepts explicit where needed: canonical sound identity for duplicate analysis and requirement compatibility/family for requested-instrument conformance. Unknown labels use the complete normalized label rather than the current first-word fallback.
- Determine satisfaction only from non-empty `track.instrument`; never inspect `track.name`. A matching track satisfies its requested requirement regardless of role. Repeated request aliases normalize to one requirement and do not require repeated tracks.
- Keep role independent and normalize it only for comparison. Same instrument with distinct roles such as melody/harmony or harmony/pad remains valid. Do not reduce all same-instrument tracks to one.
- Analyze duplicate candidates deterministically by normalized sound identity plus normalized role, then include event presence and stable event-content fingerprints in the evidence. Exact/high-overlap same-purpose tracks and an accompaniment draft that repeats an accepted upstream instrument/role are repairable errors. Same identity/role tracks with clearly divergent playable content are still reported as suspicious but are not silently merged or deleted; absent explicit purpose/origin metadata, the analyzer must expose the evidence rather than guess. Different-role groups are not suspicious duplicates.
- Never use cleanup after assembly as the primary fix. Recompute accepted upstream assignments and genuinely missing requirements before every accompaniment generation/repair, tell the model both sets, require all missing requirements to be covered, and prohibit repeating an existing normalized instrument/role pair. Reusing an already-satisfied instrument for a distinct role remains allowed, including piano melody plus piano accompaniment while strings is also generated.
- Keep duplicate track-ID remapping separate from musical duplicate detection. Preserve event IDs, note events, MIDI programs, channels, and canonical track order unless a targeted LLM repair replaces a rejected stage.
- Improve names only in generation prompts/examples: request instrument-qualified names for generic melody/accompaniment/pad tracks. Names remain UI metadata and no validator or migration may rewrite or depend on them.

## Commit Plan
- **Commit 1** (after tasks 1-3): `fix: reconcile requested instrument assignments`
- **Commit 2** (after tasks 4-6): `test: cover instrument enforcement regression`

## Tasks

### Phase 1: Instrument Analysis Contract

- [x] Task 1: Add conservative instrument identity normalization and deterministic instrumentation analysis.

  Deliverable: Create `backend/app/services/instrument_identity.py` as a pure service with typed normalized identities/requirements and an analyzer over requested labels plus track-like objects. Move/delegate the existing alias and requirement-satisfaction behavior from `generation_constraints.py` so all callers share one implementation. Return stable, request-order-preserving data for normalized requirements, satisfying track IDs, missing requirements, present/unexpected identities, and suspicious duplicate groups. Duplicate evidence must include normalized instrument, normalized role, ordered track IDs, event counts, and a deterministic content relationship based on event fingerprints that ignore display names and event IDs but preserve pitch, start, duration, velocity, staff/voice/type, and multiplicity. Add focused unit tests in a new `backend/tests/test_instrument_identity.py` for one instrument/one track, aliases, name-independent matching, repeated aliases, missing instruments, strings matching, same-instrument different-role validity, empty tracks, exact equivalents, high overlap, and distinct-content same-role reporting.

  Files: `backend/app/services/instrument_identity.py`, `backend/app/services/generation_constraints.py`, `backend/tests/test_instrument_identity.py`.

  Dependencies: none.

  Logging: Keep the pure analyzer side-effect free. At its service call sites, DEBUG-log normalized requested identities, satisfied requirement keys and track IDs, missing keys, and duplicate classifications; never log note payloads, prompts, or instrument freeform instructions.

- [x] Task 2: Extend generation validation reports with deterministic instrumentation and repair detail.

  Deliverable: Add response-safe Pydantic models in `backend/app/schemas.py` for requested-instrument satisfaction entries, suspicious duplicate groups, and repair actions, then attach an optional instrumentation section and ordered `repair_actions` to `GenerationValidationReport` without changing `Composition` or track/event schemas. Refactor `validate_generation_constraints()` to invoke the analyzer once for instrument-family and extra-instrument categories, produce one authoritative missing diagnostic per validation pass, classify actionable generated duplicates with stable codes, and populate satisfied/missing/suspicious fields even on success. Update `diagnostics_from_validation_report()` so actionable duplicate diagnostics route through existing repair machinery. Preserve the current status, tonality, and hard checks for key, tempo, meter, bars, duration, and sections.

  Files: `backend/app/schemas.py`, `backend/app/services/generation_constraints.py`, `backend/tests/test_composition_validator.py`.

  Dependencies: Task 1.

  Logging: DEBUG-log each instrumentation report category and duplicate evidence summaries; INFO-log satisfaction/missing/duplicate/action counts with validation status; WARN-log actionable missing or duplicate diagnostic codes and track IDs; ERROR remains reserved for final unrepaired hard failures.

- [x] Task 3: Remove inconsistent request matching while preserving generic Composition V1 validation.

  Deliverable: Make `composition_validator.py` delegate explicit `requested_instruments` checks to the central analyzer or remove that duplicate check from the staged generation call so raw display-name/substring logic cannot contradict final conformance. Generic integrity validation must continue checking schema, required playable roles, event ranges/density, and harmony usefulness without treating requested instruments as an exact track list. Decide and test one diagnostic ownership rule so generation does not emit both `missing_requested_instrument` and `constraint_missing_instrument_family` for the same requirement. Do not add duplicate rejection to the base `Composition` schema because manually authored and edited multi-track arrangements lack generation-purpose context.

  Files: `backend/app/services/composition_validator.py`, `backend/app/services/llm_music_generator.py`, `backend/tests/test_composition_validator.py`, `backend/tests/test_composition_schema.py` only if a schema regression assertion is useful.

  Dependencies: Tasks 1 and 2.

  Logging: DEBUG-log whether requested-instrument conformance is delegated to the generation validator; preserve existing INFO pass summaries and WARN integrity diagnostics without duplicating instrument failures.

### Phase 2: Generation And Repair Integration

- [x] Task 4: Make accompaniment generation and targeted repair assignment-aware.

  Deliverable: In `llm_music_generator.py`, derive accepted upstream instrument/role assignments and satisfied/missing requested requirements from the current melody and bass drafts immediately before every accompaniment attempt, including retries. Add sanitized machine-readable `already_satisfied`, `missing_requirements`, and `reserved_instrument_roles` blocks to the accompaniment prompt. Require the stage to cover every genuinely missing requirement, allow an already-satisfied instrument only for a distinct role, and prohibit another normalized instrument/role pair such as bass/bass. Replace the fixed example with an assignment-aware or neutral example and request instrument-qualified display names without making names authoritative. Re-run the analyzer in `_after_accompaniment_stage` and final validation; map duplicate-purpose diagnostics to `compose_accompaniment`, preserve valid upstream drafts, and append ordered repair actions (target, attempt, diagnostic codes, affected requirements/track IDs) to the final report. Do not remove tracks in `_assemble_composition`; keep unique-ID allocation unchanged.

  Files: `backend/app/services/llm_music_generator.py`, `backend/app/services/generation_constraints.py`, `backend/app/schemas.py`, `backend/tests/test_llm_staged_composer.py`.

  Dependencies: Tasks 1-3.

  Logging: INFO-log each sanitized assignment resolution and applied repair action; DEBUG-log upstream assignments, satisfied/missing normalized requirements, reserved instrument/role pairs, and diagnostic-to-stage routing; WARN-log attempted redundant assignments and targeted retries. Do not log full prompts or event payloads.

- [x] Task 5: Apply the same final invariant to fake generation and audit non-generation mutation paths.

  Deliverable: Ensure `fake_llm.py` returns the same instrumentation report and enforces missing/actionable-duplicate policy through `validate_generation_constraints()`. Audit canonical/legacy normalization, region added-track handling, composition editing, project save/open/duplication and migrations to confirm none infer or reapply historical requested instruments, synthesize repair tracks, rewrite names, or deduplicate valid tracks. Add narrow regression assertions where current coverage is insufficient: canonical normalization preserves same-instrument multi-role tracks; region edits preserve arbitrary existing instruments; persistence round-trips all tracks unchanged; fake generation honors aliases and fails genuinely missing requirements. Verify export and frontend paths require no production changes because they consume canonical tracks rather than generation requests.

  Files: `backend/app/services/fake_llm.py`, `backend/tests/test_llm_fake_provider.py`, `backend/tests/test_composition_normalizer.py`, `backend/tests/test_composition_region_patch.py`, `backend/tests/test_project_persistence_acceptance.py`; production edit/persistence/export files only if the audit finds an actual mutation bug.

  Dependencies: Tasks 2-4.

  Logging: INFO-log fake-provider instrumentation status and counts; DEBUG-log audit-path normalization/persistence summaries already available; WARN-log fake fixture conformance failures by code. Never log stored compositions, raw fixture payloads, or export bytes.

### Phase 3: Regression Coverage And Documentation

- [x] Task 6: Reproduce the duplicate-Bass regression, cover acceptance semantics, document the contract, and run compatibility gates.

  Deliverable: Extend the mocked staged-composer fixtures so a piano/bass/strings request first returns piano melody, bass/bass, then an accompaniment response containing another bass/bass plus strings pad. Prove the existing bass satisfies the request before repair, only accompaniment is retried, the corrected response retains piano melody plus piano accompaniment plus bass plus strings pad, and no redundant bass survives. Add graph tests for one requested instrument/one satisfying track, one instrument across multiple roles, an already-present requested instrument, a genuinely missing instrument, alias normalization, accidental same-instrument/same-role duplication, legitimate same-instrument/different-role tracks, name-independent matching, and stable report/repair-action ordering. Retain duplicate-ID tests with semantically distinct tracks. Through the mandatory `$aif-docs` checkpoint, update `docs/composition-v1.md`, `docs/testing.md`, and concise README wording to define generation-only requested-instrument semantics, aliases, duplicate reporting/repair, display-name non-authority, and the fact that edits/persistence/exports do not reapply historical requests. Run focused tests, the full backend suite, frontend tests, and existing fidelity suites to prove note events, playback, MIDI, MusicXML, WAV, persistence, and all non-instrument hard constraints remain compatible.

  Files: `backend/tests/test_llm_staged_composer.py`, `backend/tests/test_instrument_identity.py`, `backend/tests/test_composition_validator.py`, `backend/tests/test_llm_fake_provider.py`, `docs/composition-v1.md`, `docs/testing.md`, `README.md` as needed.

  Dependencies: Tasks 1-5.

  Logging: Use `caplog` to assert assignment, suspicious-duplicate, repair-target, and final-action records at DEBUG/INFO/WARN with stable codes and track IDs. Explicitly assert logs omit full prompts, event lists, API keys, and raw composition/export payloads.

## Verification Commands

```bash
# Run from backend/
pytest tests/test_instrument_identity.py tests/test_composition_validator.py tests/test_llm_staged_composer.py tests/test_llm_fake_provider.py
pytest tests/test_composition_normalizer.py tests/test_composition_region_patch.py tests/test_project_persistence_acceptance.py
pytest tests/test_composition_midi.py tests/test_music_json_renderer.py tests/test_export_fidelity.py
pytest

# Run from the repository root
npm --prefix frontend test
```

## Acceptance Checks

- A piano/bass/strings request passes with four tracks when piano has melody and accompaniment roles; validation never compares requested count with track count.
- `track.instrument`, after conservative normalization, is the sole source for requirement identity. `name: "Melody", instrument: "piano", role: "melody"` satisfies piano.
- Piano aliases, bass aliases, and string ensemble/strings behave consistently in prompts, early stage checks, final validation, fake generation, and reports.
- The accepted bass draft marks bass satisfied before accompaniment generation/repair. A second bass/bass accompaniment draft produces a stable actionable diagnostic and targeted retry rather than a duplicate output track.
- Same normalized instrument with different roles remains valid and is preserved. Same instrument/role candidates include deterministic event evidence in the suspicious-duplicate report and are never blindly merged or deleted.
- Final validation reports satisfied requirements, missing requirements, suspicious duplicates, repair attempts, and ordered repair actions on both passed/repaired and failed outcomes.
- Canonical note events and track metadata remain the source for playback and MIDI/MusicXML/WAV export; normalization, edits, and persistence preserve legitimate multi-track arrangements.
- Existing tests for key, tempo, time signature, bars, duration, sections, tonality, and repair routing continue to pass.
