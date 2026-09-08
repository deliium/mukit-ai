# Implementation Plan: Deterministic Composition V2 Musical Analysis

Branch: main
Created: 2026-09-08

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: full, ultra-thorough
- Analysis engine: deterministic native Python by default; `music21` optional and non-authoritative

## Goal

Add a versioned, deterministic analysis layer over canonical `composition.v2` so the UI and AI edit/repair flows can consume explicit musical context without changing the composition or creating a second source of truth. A selected composition, section, or track must expose tonal context, inferred harmony, phrase/cadence information, density, melodic and structural observations, and non-blocking musical warnings derived from the current canonical note events.

## Scope And Decisions

- Keep `CompositionV2` unchanged. Analysis is returned as a separate `composition.analysis.v1` sidecar report and cached only as derived frontend state. Do not add analysis to project `composition_json`, do not add a database migration, and do not make exports or playback consume analysis.
- Analyze strict V2 only at the service boundary. The frontend already normalizes legacy/V1 input into V2; the backend analysis endpoint rejects non-V2 or structurally invalid documents with a sanitized `422` rather than guessing over malformed canonical data.
- Treat `tracks[].events[]` as the only playable and primary analytical evidence. Declared `key`, `key_changes`, `harmony`, sections, instruments, and roles are metadata evidence to compare against inferred results; they never create notes or override event-derived observations.
- Expose one thin `POST /analysis/composition` endpoint that accepts the complete current V2 plus a scope. Do not add a project-ID endpoint initially because the frontend must analyze unsaved edits in `editedMusicJson`, not a potentially stale persisted project.
- Support `composition`, `section`, and `track` scopes. Resolve tracks by required track ID. Resolve a section by canonical array index and verify its optional ID and bar bounds when supplied, so valid V2 documents with missing section IDs remain selectable without inventing persisted IDs.
- Use overlap semantics for scoped occupancy (`note.start < scope.end` and `note.end > scope.start`) and attack-at-onset semantics for note counts. Split duration-based metrics at section/bar boundaries; never assign an entire crossing note to only its starting bar.
- Return a semantic `source_fingerprint`, `algorithm_version`, resolved scope, availability/confidence/evidence fields, deterministic metrics, bounded findings, and stable warning codes. Do not include timestamps, random IDs, NaN/infinity, complete event payloads, or unbounded evidence arrays.
- Define confidence as deterministic evidence strength, not probability or objective musical truth. Every inference may abstain with `ambiguous`, `insufficient_evidence`, or `not_applicable`; declared and inferred values remain separately visible.
- Implement the public analysis algorithms in native Python over integer ticks/MIDI pitch classes. `music21` may be used by an optional adapter or reference tests for parsing/spelling, but basic analysis and public output must work without invoking it.
- Build one request-scoped analysis context with a compiled variable-meter timeline, collapsed logical tie chains, precomputed pitch/event indexes, per-bar histograms, and onset/offset sweep frames. Target near `O(N log N)` behavior and bounded output for imports up to the configured 100,000-note scale.
- Reuse and generalize the existing tonal scorer rather than creating a competing key implementation. Preserve current generation-constraint behavior and thresholds unless dedicated regression tests authorize a change.
- Add a bounded advisory projection of the report for LLM operations. Region editing receives selected-scope context; generation receives analysis only after a V2 candidate exists and only for targeted repair. User instructions, hard constraints, canonical events, and deterministic patch validation always take precedence.
- Add a fourth composer `Analysis` tab. Store request lifecycle and the latest report in Zustand; keep section selection explicit, reuse `pianoRollTrackId` for track scope, and derive freshness from analysis contract version + full `compositionRevision` + normalized scope.
- Retain at most one successful frontend report. Debounce recomputation while the Analysis tab is visible, discard stale/out-of-order responses using both request sequence and request key, retain stale results during refresh/failure, and clear cross-project analysis state on composition replacement/hydration/deletion.
- Automatic warnings cover data quality or conspicuous technical conditions, not stylistic taste. Range, overlap density, empty scope, timing anomalies, metadata contradictions, and duplicate notes are warnings unless canonical structure itself is invalid.

## Analysis Contract

- Top-level identity: `schema_version: "composition.analysis.v1"`, semantic `algorithm_version`, `source_schema_version`, `source_fingerprint`, deterministic `status`, and `resolved_scope`.
- Core result groups: global/local tonality, chord spans and harmonic rhythm, scale-degree summaries, melodic profiles, phrases/cadences, rhythmic/harmonic density, track-role estimates, repeated material, section summaries, dissonance/tension components, and warnings.
- Common inference fields: `status`, nullable `confidence`, evidence count/mass/coverage, winner and runner-up scores where relevant, fixed method/version, and bounded source locators using track/section IDs or indexes and tick/bar ranges.
- Scope semantics: composition analyzes all tracks and bars; section clips ensemble evidence to one canonical section; track clips note-derived track metrics while retaining declared timeline and bounded ensemble context needed to interpret harmony/tension.
- Detail policy: default UI responses are summary-level. Per-event scale degrees and high-resolution tension curves are aggregated by phrase/bar and bounded; truncation is explicit through stable warning codes.
- Stable ordering: preserve canonical track/section order; sort time spans by tick bounds and stable type/ID; rank candidates by score then fixed pitch-class/mode/quality order; sort warnings by severity, code, scope, and location; round floats with one shared fixed-precision helper.
- Fingerprint semantics: hash a versioned, sorted-key compact JSON projection of analysis-relevant timeline, sections, track identity/role/drum metadata, note pitch/timing/velocity/tie semantics, declared key timeline, and harmony. Exclude unrelated UI state and generated analysis itself.

## Deterministic Analysis Policy

- **Global/local key:** accumulate duration- and metric-weighted non-drum pitch-class histograms, bass and phrase/section-edge evidence, score all 24 major/minor candidates, expose relative-key ambiguity, analyze section/fixed bar windows, smooth local candidates with fixed transition penalties, and merge adjacent accepted windows. Compare inferred spans with declared root/key changes without treating metadata as proof.
- **Chords/harmonic rhythm:** build bounded harmonic frames from note onsets/offsets and beat/bar boundaries using an active-note sweep; score a fixed triad/seventh/suspension vocabulary using coverage, missing/extra tones, bass/root evidence, continuity, and weak local-key context; abstain for ambiguous dyads/noise; merge equal adjacent frames and derive chord changes per bar/quarter. Compare declared harmony separately.
- **Scale degrees:** resolve the accepted local/global key at each logical onset; classify diatonic degree or nearest degree plus signed chromatic alteration with deterministic tie-breaking; aggregate by selected scope/phrase/track rather than returning an unbounded note list by default.
- **Melody and phrases:** choose declared melody/lead first, otherwise use high-confidence inferred role; abstain on close candidates. Analyze monophonic lines directly and use a documented skyline reduction for polyphonic tracks with a warning. Compute pitch range, tessitura, interval direction, contour shape, rest/long-note/accent/section/harmony-based phrase boundaries, and cadence types from accepted key/chord/endpoint evidence.
- **Density:** report logical attacks per quarter/bar, median inter-onset interval, active-time union ratio, note load, mean/max simultaneity, distinct pitch-class density, chord changes, active-track ratio, and section-relative density. Distinguish union occupancy from polyphonic note load so only the latter may exceed `1.0`.
- **Track roles:** return declared, inferred, and effective role plus ranked evidence from normalized instrument identity, register, monophony/polyphony, highest/lowest-voice participation, duration, attack density, repetition, and section coverage. Never mutate imported/defaulted roles and abstain where melody/lead or harmony/rhythm distinctions are weak.
- **Repeated material:** detect bounded exact, transposed, and rhythm-only motifs using normalized interval/rhythm tokens, rolling hashes plus exact verification, non-overlap preference, deterministic redundancy suppression, and result caps. Compare section fingerprints without inferring new canonical form labels.
- **Dissonance/tension:** compute duration-weighted vertical interval-class dissonance with a documented versioned table. Add tonal components only when key/chord evidence exists: chromatic mass, non-chord-tone mass, functional distance, and unresolved dominant/leading-tone tendencies. Return components and limitations; do not label high tension as an error.

## Warning And Error Policy

- Stable automatic warning codes include `note_outside_instrument_range`, `dense_overlapping_material`, `empty_analysis_scope`, `timing_grid_anomaly`, `overlapping_same_pitch_timing`, `declared_key_conflicts_with_inference`, `declared_key_change_conflicts_with_inference`, `declared_harmony_conflicts_with_inference`, `declared_harmony_unparseable`, `excessive_duplicate_notes`, and bounded ambiguity/limitation codes.
- Instrument-range warnings use normalized instrument identity and documented practical ranges, with role fallback only when identity is unknown. Avoid the existing substring pitfall where `bassoon` can match `bass`; drums use percussion semantics and are not judged by pitched ranges.
- Dense-overlap warnings use objective concurrency/note-load thresholds and include actual/threshold values. They do not reject intentional chords, pads, counterpoint, or dense style.
- Duplicate-note warnings count exact same-track pitch/start/duration/staff/voice duplicates and high-volume repeated collisions separately from legitimate unisons across tracks. Evidence lists are capped.
- An explicitly selected section/track with no overlapping logical attacks emits `empty_analysis_scope`; an empty authored section in composition-wide analysis is identified by section index/ID and remains a warning.
- Fresh Pydantic failures, out-of-bounds events, broken section coverage, invalid meter maps, or malformed tie chains return `422 analysis_invalid_composition`. Valid but suspicious timing, such as excessive same-pitch overlap or strong off-grid inconsistency, remains an analysis warning. The report must not claim to have analyzed timing that could not be resolved.
- Harmony/key contradictions require minimum evidence and margin. Sparse or relative-major/minor ambiguity produces an informational/low-confidence warning, not a contradiction.
- Requested style preferences may later be implemented as explicit threshold options, but no default warning or error should encode genre/style taste such as desired complexity, cadence type, consonance, or repetition amount.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat(analysis): add deterministic analysis foundation and tonality`
- **Commit 2** (after tasks 4-6): `feat(analysis): derive harmony melody texture and structure`
- **Commit 3** (after tasks 7-8): `feat(api): expose analysis and advisory AI context`
- **Commit 4** (after tasks 9-10): `feat(frontend): add scoped composition analysis panel`
- **Commit 5** (after tasks 11-12): `test(analysis): cover deterministic reports and user journeys`
- **Commit 6** (after task 13): `docs(analysis): document derived musical analysis`

## Tasks

### Phase 1: Contract And Shared Analysis Context

#### Task 1: Define the versioned sidecar schema, scope, fingerprint, and limits
- [x] Create strict Pydantic DTOs in `backend/app/analysis_schemas.py` for requests, composition/section/track scopes, resolved scope, confidence/evidence, key/chord spans, melody/phrase/cadence summaries, density/role/repetition/tension results, warning locators, and the `composition.analysis.v1` response. Use closed literals/constants for statuses, severities, and stable codes while keeping bounded extensible scalar details.
- [x] Define one semantic algorithm/profile version, shared float rounding, deterministic derived-ID rules, evidence/result caps, and source-fingerprint serialization. Add `backend/app/services/composition_fingerprint.py` rather than importing the private migration hash helper.
- [x] Make direct V2 validation explicit: serialize/revalidate model instances before analysis, reject V1/unknown/structurally malformed input, validate unique scope selectors, and resolve optional-ID sections through index + expected ID/bounds checks.
- [x] Document in types and docstrings that the response is derived/cacheable, has no timestamp, and cannot be submitted as authoritative composition data. Do not modify `backend/app/composition_schemas.py` except for exporting an already-needed public helper.
- [x] Add focused schema/fingerprint tests for strict extras, scope combinations, ID-less sections, canonical object-key stability, relevant-field invalidation, deterministic ordering/rounding, bounded details, and no NaN/infinity.
- [x] Logging: DEBUG profile/version, sanitized scope, input counts, and fingerprint prefix; INFO validated request summary; WARNING stable rejection/truncation codes; ERROR sanitized exception type. Never log composition JSON, event arrays, freeform labels, or full fingerprints where a short prefix suffices.
- [x] Files: `backend/app/analysis_schemas.py`, `backend/app/services/composition_fingerprint.py`, `backend/tests/test_analysis_schemas.py`, and `backend/tests/test_composition_fingerprint.py`.

#### Task 2: Build the reusable analysis context and bounded temporal indexes
- [x] Create `backend/app/services/composition_analysis_context.py` to compile `CompiledTimeline` once, resolve `[start_tick, end_tick)` scope intervals, normalize stable track/section order, convert pitches once, classify drum/non-drum tracks, and build bar/section lookup tables.
- [x] Extract or implement a shared logical-note/tie-collapse helper so each tie chain is one attack and one occupancy span while retaining bounded source event IDs. Update MIDI/render private implementations to delegate only if parity tests prove no export behavior changes.
- [x] Precompute sorted onset/offset points, active pitch-class counters, bar pitch histograms/prefix sums, per-track fingerprints, and interval clipping helpers. Avoid note-by-frame and note-by-window nested scans; use sweeps/binary search where timeline methods currently scan linearly.
- [x] Define exact attack, overlap, occupancy, note-load, and cross-boundary allocation semantics in code. Preserve unsorted canonical event arrays but make analytical output independent of their input order where order is non-semantic.
- [x] Add tests for mixed meter/key/tempo timelines, unsorted events, ties, polyphony, notes crossing bars/sections/scope bounds, drums, empty tracks, ID-less events, and stable source locators.
- [x] Logging: DEBUG context build phases, counts, frame/index sizes, scope bounds, tie-collapse counts, and elapsed milliseconds; INFO context completion; WARNING limit/truncation/unsupported-pedal codes; ERROR sanitized build failure. Never log notes or pitch sequences.
- [x] Files: `backend/app/services/composition_analysis_context.py`, shared note helper if extracted, relevant MIDI/renderer parity tests, and `backend/tests/test_composition_analysis_context.py`.
- [x] Depends on Task 1.

#### Task 3: Generalize deterministic global and local tonality analysis
- [x] Refactor `backend/app/services/composition_tonality.py` so its 24-key candidate scoring can consume scoped, variable-meter pitch histograms and return neutral inferred-key results without requiring a requested key. Preserve `analyze_composition_tonality()` as the existing generation-conformance wrapper.
- [x] Implement global key ranking, evidence thresholds, relative-major/minor ambiguity, section and fixed-window local-key analysis, deterministic smoothing/transition penalties, and adjacent-span merging. Use canonical key changes only for comparison and fallback labeling, not to force inferred winners.
- [x] Return declared/inferred/effective tonal context separately with candidate scores, confidence, coverage, method version, and contradictions only after documented evidence/margin thresholds.
- [x] Add regression tests for current F# minor/A minor behavior, mixed meter, modulation, declared key changes, enharmonic labels, sparse/chromatic/drone/percussion-only material, relative-key ties, scope isolation, and deterministic tie-breaking.
- [x] Logging: DEBUG candidate/window score summaries and smoothing decisions using labels/counts only; INFO accepted global/local span counts and status; WARNING ambiguity/contradiction codes; preserve `LOG_LEVEL` control and avoid pitch/event dumps.
- [x] Files: `backend/app/services/composition_tonality.py`, `backend/tests/test_composition_tonality.py`, and focused local-key fixtures/tests.
- [x] Depends on Tasks 1-2.

### Phase 2: Harmony, Melody, Texture, And Structure

#### Task 4: Infer chord spans, harmonic rhythm, and scale-degree relationships
- [ ] Create `backend/app/services/composition_harmony_analysis.py` using the context onset/offset sweep and beat/bar boundaries. Score a documented fixed chord vocabulary, account for bass/root/role evidence and non-chord tones, abstain when winner coverage/margin is insufficient, and merge equivalent adjacent frames deterministically.
- [ ] Derive harmonic-rhythm metrics per bar/section/scope and Roman/function labels only when both accepted local key and chord confidence support them. Compute scale-degree distributions with signed chromatic alterations and bounded note locators.
- [ ] Compare declared `harmony` entries with inferred spans as agreement, partial agreement, conflict, unparseable, or insufficient evidence. Do not extend harmony beyond its declared bar or create playable notes.
- [ ] Add tests for triads, inversions, seventh/suspended/diminished chords, missing fifths, passing tones, ambiguous dyads, silent frames, modulation-aware degrees, harmonic-rhythm merging, and declared-harmony contradictions.
- [ ] Logging: DEBUG frame/candidate/merge counts and bounded status summaries; INFO inferred span and harmonic-change counts; WARNING ambiguity/unparseable/contradiction codes; ERROR sanitized analyzer failures without active-pitch dumps.
- [ ] Files: `backend/app/services/composition_harmony_analysis.py`, `backend/tests/test_composition_harmony_analysis.py`, and shared fixture builders.
- [ ] Depends on Task 3.

#### Task 5: Analyze melodic range, contour, phrases, and cadences
- [ ] Create `backend/app/services/composition_melody_analysis.py` to select declared or high-confidence inferred melodic candidates, handle monophonic lines directly, and apply a documented deterministic skyline reduction only when needed. Expose ambiguity/reduction status instead of silently flattening polyphony.
- [ ] Compute min/max/range, duration-weighted tessitura, interval statistics, net displacement, direction changes, and a stable compressed contour classification for each selected track and scoped section.
- [ ] Score phrase boundaries from rests, preceding duration, metric/section position, contour breaks, repeated-material boundaries, and harmonic resolution; suppress implausibly close competing boundaries deterministically without forcing canonical section boundaries to be phrases.
- [ ] Classify authentic/perfect/imperfect, half, plagal, deceptive, or unclassified cadences from accepted local key, inferred chord spans, phrase boundary, bass/inversion, and melodic endpoint evidence. Bound cadence confidence by the weakest required evidence source.
- [ ] Add tests for ascending/descending/arch/static/mixed contours, range/tessitura, rests and crossing notes, polyphonic reduction, ambiguous melody tracks, phrase thresholds, modulation-aware scale degrees, and each supported cadence/abstention path.
- [ ] Logging: DEBUG candidate role/contour/boundary score summaries and cadence evidence counts; INFO melodic profile/phrase/cadence counts; WARNING ambiguity/reduction/insufficient-evidence codes; never log melodic note sequences.
- [ ] Files: `backend/app/services/composition_melody_analysis.py` and `backend/tests/test_composition_melody_analysis.py`.
- [ ] Depends on Task 4. Use declared melody/lead roles plus a generic deterministic melodic-candidate feature interface; Task 6 will connect inferred-role results through that interface without changing phrase/contour algorithms.

#### Task 6: Analyze density, track roles, repetition, and tension
- [ ] Create `backend/app/services/composition_density_analysis.py` for track/bar/section/scope attack density, inter-onset intervals, active-time union, note load, simultaneity, active-track ratios, pitch-class density, and section-relative note density using variable-meter boundaries and clipped crossing spans.
- [ ] Create `backend/app/services/composition_role_analysis.py` to reuse normalized instrument identity and score sidecar role candidates from register, monophony/polyphony, highest/lowest-voice participation, attack/duration behavior, repetition, and coverage. Return declared/inferred/effective roles and abstain on weak margins; do not change import-time role inference. Connect accepted melodic candidates to Task 5 through its feature interface and rerun only the bounded final candidate selection, not melody analysis from scratch.
- [ ] Create `backend/app/services/composition_repetition_analysis.py` with bounded exact, transposed, and rhythm-only motif tokens, rolling hashes followed by exact verification, non-overlap preference, redundancy suppression, section fingerprints, deterministic ordering, and explicit search truncation.
- [ ] Create `backend/app/services/composition_tension_analysis.py` with a versioned interval-class dissonance table and duration-weighted vertical scores. Add chromatic, non-chord-tone, functional-distance, and unresolved-tendency components only when their tonal/harmonic inputs are available.
- [ ] Add focused tests for union occupancy versus note load, mixed meter and crossing notes, melody/bass/pad/rhythm/drum/ambiguous roles, declared-role conflicts, exact/transposed/rhythm motifs, repeated sections, consonant versus clustered sonorities, dominant resolution, and unavailable tension components.
- [ ] Logging: each analyzer logs DEBUG sanitized feature/frame/hash counts and truncation decisions, INFO result counts/status, WARNING ambiguity/limit codes, and ERROR only bounded exception context. No motif token streams, active pitch sets, or note payloads in logs.
- [ ] Files: `backend/app/services/composition_density_analysis.py`, `composition_role_analysis.py`, `composition_repetition_analysis.py`, `composition_tension_analysis.py`, related tests, and `instrument_identity.py` only for public reusable normalization/fingerprint hooks.
- [ ] Depends on Tasks 2-4; role results feed final melodic selection in Task 5.

### Phase 3: Warnings, API, And AI Context

#### Task 7: Assemble reports and evaluate non-blocking musical warnings
- [ ] Create `backend/app/services/composition_analysis_warnings.py` with one stable warning registry, objective default thresholds, severity/category policy, bounded evidence, and deterministic aggregation/deduplication. Keep automatic technical warnings separate from future user-requested style thresholds.
- [ ] Implement practical instrument-range warnings through normalized identities with documented role fallback, dense-overlap warnings from concurrency/note load, empty selected/authored section warnings, valid-but-suspicious timing warnings, key/harmony contradiction warnings from Tasks 3-4, and excessive exact duplicate-note warnings.
- [ ] Create `backend/app/services/composition_analysis.py` as the fixed-order orchestrator: validate/fingerprint, build context, run tonality, harmony, initial/final roles and melody, density, repetition, tension, warning evaluation, then assemble stable output. Ensure all analyzers are pure with respect to input V2 and verify before/after canonical dumps are equal.
- [ ] Handle no pitched notes, percussion-only scores, silent selected scope, unsupported sustain interpretation, analyzer result caps, and partial analytical availability without turning valid compositions into failures.
- [ ] Add orchestrator/warning tests for every required warning, threshold boundaries, false positives on legitimate chords/unisons/pads, warning order, side-effect freedom, repeated byte-equivalent output, and controlled partial results.
- [ ] Logging: DEBUG fixed stage start/end, durations, counts, threshold decisions, and fingerprint prefix; INFO completion with algorithm version/scope/result and warning-code counts; WARNING emitted warning codes and truncation; ERROR stage name plus sanitized exception class. Never log the report prose or source payload.
- [ ] Files: `backend/app/services/composition_analysis_warnings.py`, `backend/app/services/composition_analysis.py`, `backend/tests/test_composition_analysis_warnings.py`, and `backend/tests/test_composition_analysis.py`.
- [ ] Depends on Tasks 3-6.

#### Task 8: Expose the analysis API and bounded advisory context to AI operations
- [ ] Add `backend/app/routers/analysis.py` with `POST /analysis/composition`, typed request/response models, thin service delegation, and structured sanitized `422` errors for invalid V2/scope/limits. Musical warnings and insufficient evidence return `200` with report status, not HTTP failures.
- [ ] Register the router in `backend/app/main.py` and extend `backend/tests/test_openapi_v2.py`. Keep request handling independent of project persistence and do not accept client-provided cached analysis as an authority.
- [ ] Add a deterministic `build_llm_analysis_context()` projection with a named 4-8 KB serialized budget, priority-ordered facts, bounded observations/track summaries, advisory/source-of-truth language, and explicit truncation. Do not serialize full report arrays into prompts.
- [ ] Integrate selected-scope context into `_analyze_edit_scope()` / `_build_draft_prompt()` in `backend/app/services/llm_composition_editor.py`; recompute from the normalized current composition, include it on draft and repair prompts, and leave patch scope/application/validation authoritative.
- [ ] Integrate candidate analysis into `backend/app/services/llm_music_generator.py` only after assembly/validation and only for targeted repair prompts. Do not add analysis to immutable `GenerationConstraints`, initial form/harmony/melody stages, or fake-provider behavior.
- [ ] Add route tests and monkeypatched real-provider orchestration tests for correct scope, advisory labeling, size cap/truncation, repair refresh, unchanged hard constraints, fake-provider stability, `200` warning responses, structured `422`, and absence of source payloads in logs/errors.
- [ ] Logging: route INFO start/completion with version, scope, counts, elapsed time, and warning codes; DEBUG AI projection character count/truncation and stage use; WARNING controlled rejection/truncation; ERROR sanitized domain mapping. Never log prompts, reports, composition JSON, or analysis evidence arrays.
- [ ] Files: `backend/app/routers/analysis.py`, `backend/app/main.py`, `backend/app/services/composition_analysis.py`, `backend/app/services/llm_composition_editor.py`, `backend/app/services/llm_music_generator.py`, `backend/tests/test_analysis_routes.py`, `backend/tests/test_llm_composition_editing.py`, `backend/tests/test_llm_staged_composer.py`, `backend/tests/test_llm_fake_provider.py`, and `backend/tests/test_openapi_v2.py`.
- [ ] Depends on Task 7.

### Phase 4: Frontend State And Analysis Panel

#### Task 9: Add the frontend API adapter, scope helpers, and race-safe derived cache
- [ ] Add `analyzeComposition(composition, scope)` to `frontend/src/api/musicApi.js`. Normalize/validate complete V2 input, validate track/section targets locally, POST to `/analysis/composition`, validate the top-level report contract, normalize optional arrays/warnings, and preserve structured safe backend errors.
- [ ] Create pure scope/freshness helpers in `frontend/src/utils/compositionAnalysis.js` for ID-less section keys, section recovery, request scope construction, and a request key composed from `composition.analysis.v1`, the full `compositionRevision`, and only the active normalized scope fields.
- [ ] Extend `frontend/src/store/musicStore.js` with `analysisScope`, explicit selected section key, latest result/result key, attempt key, status/error/warnings, and actions for scope/section selection, request, refresh/retry, and reset. Reuse `pianoRollTrackId` for track scope and retain only one result.
- [ ] Use a module-level request sequence plus request-key equality to discard stale A-to-B-to-A races. Derive stale/current state rather than manually invalidating every note mutation; retain stale results during loading/error and clear analysis state on generation/import/project hydration/replacement/deletion.
- [ ] Add a debounced request trigger usable only while the Analysis tab is visible. Reuse a current successful result, deduplicate an identical in-flight request, and force refresh/retry when requested.
- [ ] Add `/analysis` proxy routes to `frontend/vite.config.js` and `frontend/nginx.conf` so development and production-local deployments reach the backend rather than SPA fallback.
- [ ] Logging: API/store DEBUG scope, request sequence, revision prefix, counts, freshness/truncation, and stale-response discard; INFO accepted result; WARNING safe warning/error codes; no full composition, report, freeform finding text, or full revision string.
- [ ] Files: `frontend/src/api/musicApi.js`, `frontend/src/utils/compositionAnalysis.js`, `frontend/src/store/musicStore.js`, `frontend/vite.config.js`, and `frontend/nginx.conf`.
- [ ] Depends on Task 8.

#### Task 10: Build the accessible responsive Analysis tab and scoped results UI
- [ ] Create `frontend/src/components/CompositionAnalysisPanel.jsx` and add an `Analysis` tab in `ComposerWorkspace.jsx`. Complete tab IDs, `aria-controls`/`aria-labelledby`, selected state, and keyboard behavior while preserving current workspace styling.
- [ ] Provide whole-composition, selected-section, and selected-track controls; disambiguate repeated section labels with type/bar range; reuse the shared current track selection; and show the resolved scope clearly.
- [ ] Render concise tonal context/local modulation, inferred chord/harmonic-rhythm summaries, phrase/cadence information, note/rhythmic/harmonic density, range/contour, role and repetition observations, tension components, and a semantic warning list. Present confidence/ambiguity and declared-versus-inferred values without asserting heuristic results as facts.
- [ ] Implement current/updating/out-of-date/failed states: initial accessible loader, retained stale cards during refresh, `aria-busy`, live status, alert on failure, refresh/retry, and a clear invalid-composition state. Do not reuse global generation errors.
- [ ] Use responsive auto-fit metric grids, stacked controls near 700px, `min-width: 0`, wrapping long warning text, touch-sized controls, and no page-level horizontal overflow at mobile width.
- [ ] Logging: DEBUG tab/scope/refresh interactions and status transitions using IDs/counts only; INFO manual refresh/retry; WARNING render-contract fallback codes; never log report objects or user composition data.
- [ ] Files: `frontend/src/components/CompositionAnalysisPanel.jsx` and `frontend/src/components/ComposerWorkspace.jsx`.
- [ ] Depends on Task 9.

### Phase 5: Determinism, Performance, And Acceptance Tests

#### Task 11: Add backend golden, integration, mutation, and performance coverage
- [ ] Add small auditable V2 fixtures under `backend/tests/fixtures/analysis/` covering major/minor tonality, one detectable modulation, chord inversions/non-chord tones, four cadence types, repeated/transposed motifs, mixed meter, polyphony/dissonance, practical range violations, duplicate notes, empty sections/tracks, and percussion-only material.
- [ ] Create golden semantic vectors rather than brittle prose snapshots. Assert exact stable codes, IDs, scope bounds, rounded values, candidate ordering, and fingerprint/report equality across repeated runs and object-key/event-order permutations where order is non-semantic.
- [ ] Test a model mutated after initial Pydantic construction and malformed direct JSON so the service/route returns controlled validation errors instead of crashes or misleading reports. Assert input dumps are unchanged after every analyzer.
- [ ] Add synthetic normal-limit coverage up to 100,000 notes/64 tracks/512 bars for bounded runtime, frame/motif/evidence caps, response size, and explicit truncation. Use a generous non-flaky benchmark ceiling or complexity/count assertions rather than machine-sensitive microbenchmarks.
- [ ] Run focused and full backend gates: `../.venv/bin/python -m pytest tests/test_analysis_schemas.py tests/test_composition_analysis_context.py tests/test_composition_tonality.py tests/test_composition_harmony_analysis.py tests/test_composition_melody_analysis.py tests/test_composition_analysis.py tests/test_analysis_routes.py`, then `../.venv/bin/python -m pytest` from `backend/`.
- [ ] Logging: use `caplog` to verify stage timings/counts/codes at DEBUG/INFO/WARNING, sanitized structural failures at ERROR, `LOG_LEVEL` control, and no fixture sentinel note sequences, full JSON, prompts, labels, or evidence payloads.
- [ ] Files: `backend/tests/fixtures/analysis/*`, all focused analyzer/route tests, `backend/tests/test_secret_hygiene.py`, and reusable fixture builders.
- [ ] Depends on Tasks 1-8.

#### Task 12: Add frontend API/store tests and Playwright analysis journeys
- [ ] Extend `frontend/src/api/musicApi.test.js` for all scopes, complete V2 payloads, local target rejection, normalized optional arrays/warnings, malformed responses, and safe structured errors. Add `frontend/src/utils/compositionAnalysis.test.js` for request keys, ID-less/repeated sections, selection recovery, and warning normalization.
- [ ] Add `frontend/src/store/musicStore.analysis.test.js` for defaults, section/track scope, deduplication, forced refresh, stale derivation after notes/scope changes, stale-response and A-to-B-to-A rejection, retained results on failure, retry, reset on project/composition replacement, undo freshness recovery, and no invalidation from mute/solo/volume UI state.
- [ ] Add `frontend/e2e/analysis-panel.spec.js` plus helper snapshot/wait support. Cover opening Analysis, whole-composition results, selected-section harmony/tonality/phrase/density/warnings, selected-track results, note-edit stale state and recomputation, warning/error/retry behavior, and a 390px responsive no-overflow journey.
- [ ] Use the real fake backend composition for the core journey and route interception only for deterministic UI error/warning states. Assert stable test IDs/store state and avoid arbitrary waits.
- [ ] Run `npm test`, `npm run lint`, `npm run build`, and `npm run test:e2e -- e2e/analysis-panel.spec.js` from `frontend/`; run the existing V2 journey as a regression when the full stack is available.
- [ ] Logging: assert browser console diagnostics contain scope/status/counts but no full composition/report; retain existing Playwright trace/video only on failure.
- [ ] Files: `frontend/src/api/musicApi.test.js`, `frontend/src/utils/compositionAnalysis.test.js`, `frontend/src/store/musicStore.analysis.test.js`, `frontend/e2e/analysis-panel.spec.js`, and `frontend/e2e/helpers.js`.
- [ ] Depends on Tasks 9-10 and the backend fixtures/API from Task 11.

### Phase 6: Documentation Checkpoint

#### Task 13: Document the analysis contract, heuristics, UX, and operating limits
- [ ] Use `$aif-docs` for the mandatory documentation checkpoint. Add `docs/composition-analysis.md` describing sidecar/cache semantics, canonical authority, scopes, versioning/fingerprints, result groups, confidence/abstention, stable warning codes, algorithm definitions/thresholds, limits, and declared-versus-inferred provenance.
- [ ] Update `README.md` with the Analysis panel and deterministic/optional-LLM behavior; update `docs/composition-v2.md` to state that analysis is not part of canonical V2; update `docs/import.md` to preserve the rule that raw import performs no analysis; and update `docs/testing.md` with focused, full, performance, and Playwright commands.
- [ ] Update `docs/CODEBASE_MAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md` for the analysis router/schema/service cluster, frontend panel/state flow, and bounded AI context integration.
- [ ] Document known limitations: heuristic key/chord/phrase/role/tension outputs, no pedal-aware sounding-duration analysis in v1, optional section IDs, summary-level scale degrees, bounded motif search, and no persisted server cache.
- [ ] Logging: document safe fields (`algorithm_version`, fingerprint prefix, scope/counts, elapsed time, warning codes, truncation) and prohibited fields (full compositions/reports/prompts/event arrays). Confirm runtime verbosity remains controlled by `LOG_LEVEL`.
- [ ] Files: `docs/composition-analysis.md`, `README.md`, `docs/composition-v2.md`, `docs/import.md`, `docs/testing.md`, `docs/CODEBASE_MAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md`.
- [ ] Depends on Tasks 1-12.

## Acceptance Gates

- Selecting a canonical composition, one section, or one track displays detected tonal context, inferred harmony/harmonic rhythm, phrase/cadence information, note/rhythmic density, and relevant warnings without modifying any canonical field or playable note.
- Editing a relevant note makes the visible report stale and triggers one debounced recomputation while Analysis is active; stale/out-of-order responses cannot overwrite newer scope/revision results.
- Repeating an identical request produces the same semantic fingerprint, ordering, rounded values, warning codes, and JSON result; no LLM call is required.
- Local-key/modulation, chord, phrase, cadence, role, repetition, and tension inference expose confidence/evidence and abstain for insufficient or ambiguous material rather than inventing certainty.
- Out-of-range notes, conspicuously dense overlaps, empty requested/authored sections, analyzable timing anomalies, key/harmony contradictions, and excessive duplicate notes appear as stable non-blocking warnings. Structurally invalid canonical timing returns a controlled `422`.
- Playback, notation, export, persistence, and import continue to use only canonical V2. Raw import still leaves `harmony: []` and does not invoke analysis automatically.
- Region-edit prompts receive only a bounded advisory scope summary; generation uses analysis only for post-assembly targeted repair. Hard constraints, user instructions, canonical events, and patch validation remain authoritative.
- Backend focused/full tests, frontend unit/lint/build tests, and the Analysis Playwright journey pass; scale-limit tests demonstrate bounded frames, motifs, evidence, and response size.
- Logs contain versions, scopes, counts, timings, status, warning codes, and short fingerprint prefixes only; they do not contain full compositions, reports, prompts, note sequences, or raw payloads.

## Risks And Mitigations

- Musical inference is inherently ambiguous. Use explicit availability states, evidence/margins, deterministic abstention, and declared-versus-inferred separation; avoid objective-sounding prose.
- Dense imports can make chord frames and motif search expensive. Build one shared indexed context, use sweeps/prefix histograms/rolling hashes, cap detailed output/search, and test at configured import scale.
- Relative keys, enharmonic spelling, polyphonic melody, pedal, and arbitrary harmony symbols can produce misleading labels. Preserve spelling/provenance where practical, warn on reduction/unsupported semantics, and make `music21` optional rather than silently changing native results.
- Existing generation tonality and validator checks encode generation policy and some constant-meter assumptions. Generalize reusable primitives behind stable wrappers; do not change generation enforcement or export behavior without regression coverage.
- Frontend `compositionRevision` is a large serialization and may over-invalidate. Use it only as a one-result freshness token, never as an unbounded cache key map; return a backend semantic fingerprint for report identity.
- Adding analysis to LLM prompts can increase latency and token use. Use a fixed compact projection budget, selected scope, deterministic truncation, and post-assembly/repair-only generation integration.
- A successful report could become stale during an in-flight edit or project switch. Require request sequence plus complete request-key equality before installing results and retain visibly marked stale results only within the same active composition lifecycle.
