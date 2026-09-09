# Implementation Plan: AI-Assisted Arrangement and Orchestration for Composition V2

Branch: main (branch creation disabled by `.ai-factory/config.yaml`)
Created: 2026-09-09

## Settings
- Testing: yes (backend, frontend unit/integration, and Playwright)
- Logging: verbose
- Docs: yes (mandatory `$aif-docs` checkpoint at completion)

## Roadmap Linkage
Milestone: "AI-assisted arrangement / orchestration"
Rationale: Linked after context-aware composition development was marked complete; arrangement is the next roadmap milestone.

## Goal

Let users transform an existing canonical `composition.v2` arrangement without replacing it with unrelated music. The first release must support changing instrumentation, adding/removing accompaniment, orchestrating selected tracks, converting a piano sketch to an ensemble, simplifying an arrangement, increasing/decreasing texture density, creating a countermelody, and doubling a melody at another register or instrument.

The acceptance baseline is a source containing piano melody, piano accompaniment, and bass. A request to arrange it for piano, cello, and string ensemble while keeping the melody recognizable must return one or more meaningful, playable V2 redistributions with explicit `tracks[].events[]`, unchanged harmony metadata, verified melody preservation, correct GM programs/channels, and no accidental clone tracks. The working project must remain unchanged until the user explicitly applies a verified candidate.

## Scope

### Included in V2 Initial Orchestration

- Whole-composition operations over explicitly selected source tracks. The operation may add, remove, split, merge, re-instrument, or retain tracks only as authorized by its request and topology manifest.
- A curated, practical General MIDI-compatible palette: piano, organ, acoustic/electric guitar, acoustic/electric bass, violin, viola, cello, contrabass, string ensemble, harp, choir, flute/piccolo, oboe/English horn, clarinet, bassoon, soprano/alto/tenor/baritone sax, trumpet, trombone, French horn, tuba, common synth lead/pad, and a standard drum kit.
- Configurable, versioned concert-pitch metadata with absolute playable and preferred ranges. Instruments without defensible acoustic limits use an explicit unbounded/unknown policy rather than fabricated ranges.
- Strict before/after instrumentation requirements with instrument identity, role, part count, and intentional doubling semantics.
- Ephemeral 1-4 candidate preview, comparison, audition, stale/tamper protection, explicit apply, undo/redo, autosave, notation/playback/export compatibility, and deterministic fake-provider acceptance.

### Deferred

- Section/bar-scoped orchestration, mid-track instrument changes, divisi/staff-group engraving, transposed written-pitch notation, articulations/technique-specific ranges, seating, orchestral balance simulation, and complete orchestration knowledge for every GM program.
- Persisting catalog IDs or range metadata inside `composition.v2`; V2 continues to persist the existing `instrument`, `role`, `midi_program`, `channel`, and `is_drum` fields.
- Reharmonization during arrangement. `composition.harmony`, key, sections, tempo, meter, duration, markers, and timeline geometry remain exact; `preserve_harmony` governs compatibility of generated notes with that unchanged harmonic context.

## Architecture Decisions

- Add a cohesive Arrangement module: `backend/app/arrangement_schemas.py`, `backend/app/routers/arrangement.py`, focused `composition_arrangement_*` services, `frontend/src/components/ArrangementPanel.jsx`, and `frontend/src/utils/compositionArrangementCandidates.js`. Do not loosen composition-development topology invariants or grow arrangement business logic in `main.py`.
- Expose stateless `GET /composition/arrangement/instruments` and `POST /composition/arrangement/preview`. The catalog endpoint prevents backend/frontend taxonomy drift; preview never reads or writes projects.
- Accept strict `CompositionV2` only. `tracks[].events[]` remains the only playable source; harmony and analysis may guide decisions but never create audible notes implicitly.
- Keep musical role independent from instrument identity. `track.role` states function, `track.instrument` identifies the sound label, and `track.midi_program` determines GM export timbre. A cello may carry melody, bass, harmony, or countermelody; a piano melody and piano accompaniment are not duplicates merely because they share an instrument.
- Introduce a versioned instrument catalog service backed by a packaged, strictly validated JSON policy file. Allow an optional environment path override for deployments, record the catalog/range-policy version and canonical catalog fingerprint in arrangement responses and logs, and make global readiness false on invalid configured metadata. Keep the complete existing GM program table available for import, but expose only the curated arrangement palette as initially selectable.
- Store concert/sounding MIDI ranges as `playable_low/high` and `preferred_low/high`. Notes outside absolute playable range are hard candidate errors. Notes inside playable but outside preferred range are bounded `questionable_range` warnings. Unknown/unbounded synth/effect policies are explicit. Protected melody is never octave-shifted automatically; authorized non-melody material may use a request-controlled octave-fold repair that preserves pitch class and timing.
- Use operation-specific strict request validation and explicit `before`/`after` part requirements. `before` is a precondition checked before provider invocation; `after` is a candidate postcondition. Counts and roles are evaluated independently from instrument identity, and unlisted target parts are rejected unless explicitly allowed.
- Have the provider return a bounded arrangement draft, not a complete composition. Drafts reference deterministic request-local source-note IDs for copied/redistributed/doubled material and contain explicit relative notes only for genuinely generated accompaniment, density, or countermelody content. The complete compact source-note reference table for selected material is non-truncatable: truncate advisory analysis first and reject over-limit selections before provider invocation rather than asking the provider to account for unseen notes.
- Deterministic code owns authorization, exact source-note lookup, track/event IDs, programs, channels, track order, topology, note copying, range handling, motif reconciliation, canonical normalization, validation, summaries, and fingerprints. Provider-supplied channels, programs, persistent IDs, or top-level metadata are forbidden.
- Use full `composition.edit.v1` fingerprints for source and candidate documents. Return an explicit topology/change manifest; the frontend independently recomputes fingerprints, diffs actual tracks/events against the manifest, verifies required assertions, and rejects unexplained changes before Apply.
- Allow legitimate repeated instruments and intentional doubling through explicit part IDs and doubling policies. Reject unnecessary exact/high-overlap tracks plus cross-instrument/cross-role rhythmic-contour clones, octave/transposed melody clones, full-part cloning during piano-to-ensemble, and undeclared doubling. Never silently delete a suspicious track.
- Allocate pitched channels deterministically from the 15 non-drum GM channels, sharing only when program and channel-wide controllers are compatible. Reserve channel 10 for drums and return an actionable error when the requested topology cannot be represented safely.
- Preserve source composition immutability throughout preview. Candidate selection and audition remain ephemeral. Apply uses the common atomic edit path, creates exactly one undo entry, reconciles track selection/mixer/motif state, invalidates other previews and analysis, refreshes notation, marks dirty, and autosaves only the applied V2. Any loaded catalog fingerprint change makes existing previews stale and requires regeneration.

## Operation Contract

| Operation | Authorized transformation | Required invariant |
|---|---|---|
| `change_instrumentation` | Re-instrument selected tracks without topology or event changes | Selected track events/roles remain exact; after inventory differs |
| `add_accompaniment` | Add explicit harmony/rhythm/pad/bass tracks | Every source track remains exact; audible event/track count increases |
| `remove_accompaniment` | Remove explicitly selected accompaniment-role tracks | Melody/lead and all unselected/protected tracks remain exact |
| `orchestrate_selected_tracks` | Redistribute selected material among target parts | Every protected source note has exactly one primary representation |
| `piano_to_ensemble` | Split selected piano parts among at least two target instruments | Material is distributed, not cloned wholesale; requested melody preservation passes |
| `simplify_arrangement` | Remove/consolidate authorized non-melody material | Selected density/simultaneity decreases; no new musical material appears |
| `increase_texture_density` | Add harmonically compatible explicit notes/parts | At least one targeted density metric materially increases |
| `decrease_texture_density` | Remove authorized non-melody notes/parts | At least one targeted density metric decreases and melody remains protected |
| `create_countermelody` | Add one or more `countermelody` parts | New line is harmonically compatible and not an excessive melody clone |
| `double_melody` | Add a declared instrument/register doubling | Only declared source/target overlap is exempt from duplicate rejection |

## API Contract Outline

Instrument catalog response:

- `catalog_version`, `range_policy_version`, and configured source fingerprint.
- Stable arrangement-only `instrument_id`, display name, aliases, GM program/family, compatibility identity/family, pitched/drum policy, absolute playable range, preferred range, and suggested roles.
- No catalog identifier is added to persisted V2 tracks; selected profiles materialize existing canonical track fields.

Preview request:

- `composition`: strict `CompositionV2`.
- `operation`: one value from the operation table.
- `source_track_ids` and `protected_track_ids`: unique existing IDs with no intersection.
- `instrumentation.before` and `instrumentation.after`: bounded one-row-per-part requirements containing unique stable `part_id`, `instrument_id`, optional role, source mapping, and `doubling_policy`; aggregate counts are derived from list multiplicity rather than an ambiguous per-row `count`.
- `allow_unlisted_after`: false by default.
- `preserve_melody`: true by default.
- `preserve_harmony`: true by default; means generated notes must remain compatible with unchanged canonical harmony/key context.
- `range_adjustment`: `reject` by default or `octave_shift_unprotected` for authorized non-melody material.
- `candidate_count`: 1-4; optional instruction is normalized and length-bounded; provider/model and repair/context options follow existing LLM conventions.

Provider draft:

- Strict `extra="forbid"` parts with `action`, request part ID, source track IDs, source-note references, and optional bounded relative canonical note drafts.
- Generated notes carry explicit pitch, relative start, duration, velocity, staff/voice, articulation, and tie data where applicable.
- No top-level composition fields, persistent IDs, channels, GM programs, or arbitrary source deletion.

Each candidate response:

- Full strict `CompositionV2`, `candidate_id`, candidate fingerprint, source edit fingerprint, operation, provider/model, algorithm version, catalog version, range-policy version, catalog fingerprint, and immutable fingerprints for every target profile used.
- Actual before/after inventories and a topology manifest of retained, removed, added, reordered, re-instrumented, split/merged, and source-to-target mappings.
- Per-track/event counts for copied, moved, generated, removed, octave-adjusted, and unchanged material.
- Before/after density metrics, range findings, duplicate findings, harmony compatibility, warnings, and required preservation assertions.
- Stable bounded error/warning codes. Valid candidates and rejected attempt summaries are separate arrays; a rejected attempt contains only ordinal/stage/stable codes and bounded reasons, never a partial composition. Partial success may therefore remain explainable without making failed output auditionable or applicable.

## Canonical and Musical Invariants

- Source input is never mutated; candidate construction starts from a validated copy and ends with strict V2 plus integrity validation.
- Top-level form/timeline/harmony metadata remains byte-equivalent. Arrangement never invents notes from harmony metadata.
- Unselected and protected tracks remain byte-equivalent unless the operation contract explicitly authorizes metadata-only re-instrumentation of a selected track.
- Melody preservation compares a cross-track semantic multiset of complete logical notes so legal redistribution is allowed while pitch, onset, duration, velocity, articulation, voice/staff, and tie changes are detected. Intentional doubling does not satisfy the required primary representation.
- Harmony preservation keeps authored metadata exact and checks new/changed notes against declared or deterministically inferred harmony without persisting analysis.
- Every output track uses explicit canonical events, a supported role, and globally unique IDs. Added/re-instrumented tracks additionally have catalog-consistent instrument/program/drum identity and safely allocated channels; byte-preserved source tracks may retain pre-existing metadata/range findings.
- Range findings for added, re-instrumented, or pitch-adjusted target material are reproducible against the response's catalog/range-policy version, catalog fingerprint, and target-profile fingerprints. Absolute failures cannot be overridden at Apply; preferred-range warnings require explicit confirmation. Unchanged source tracks retain baseline mismatch/range findings without causing an unrelated add/remove operation to fail.
- Every base track and candidate track is explained by the topology manifest. Every selected source note is retained, removed, redistributed, or doubled exactly as permitted by the operation.
- Density and simplification claims are measured over selected source/target tracks with event count, attack count, active-track count, union occupancy, and maximum simultaneity; global averages alone cannot establish success.
- Existing motifs remain valid. Preserve exact references where events remain; deterministically remap moved events only when policy permits; keep doubling copies outside existing occurrences. Because one V2 occurrence belongs to one track, all events in an affected occurrence must route to one primary target; reject a draft that splits an occurrence across targets. Prune an occurrence only when its source material is explicitly removed and report it.
- MIDI program/channel conflicts and MusicXML/playback projection are checked before a candidate is accepted. Instrument identity is not inferred from role during rendering. The catalog endpoint returns the complete canonical `SUPPORTED_TRACK_ROLES` list separately from advisory per-instrument role suggestions.
- Logs never contain API keys, instruction text, prompts, provider raw output, full compositions, full analysis, event arrays, pitch lists, or uploaded/export payloads.

## Commit Plan

- **Commit 1** (after tasks 1-3): `feat: define arrangement instruments and contracts`
- **Commit 2** (after tasks 4-6): `feat: realize AI arrangement candidates`
- **Commit 3** (after tasks 7-9): `feat: expose and consume arrangement previews`
- **Commit 4** (after tasks 10-12): `feat: add verified arrangement workflow`
- **Commit 5** (after tasks 13-15): `test: complete arrangement acceptance and docs`

## Tasks

### Phase 1: Instrument and API Foundations

- [x] Task 1: Add the configurable, versioned practical GM arrangement catalog without changing persisted/imported source semantics.
  - Files: create `backend/app/services/instrument_catalog.py`, `backend/app/fixtures/arrangement_instruments.v1.json`, and `backend/tests/test_instrument_catalog.py`; update `backend/app/services/instrument_identity.py` only for public exact identity/compatibility APIs required by the catalog; update `.env.example`, `docker-compose.yml`, and `compose.dev.yml` only as needed to pass a container-visible optional `ARRANGEMENT_INSTRUMENT_CATALOG_PATH` and document a read-only bind override.
  - Deliverable: define immutable profile/resolution models; strictly load and validate unique IDs/aliases, valid zero-based GM programs, advisory role suggestions, drum policy, `0 <= playable_low <= preferred_low <= preferred_high <= playable_high <= 127`, nullable/unbounded ranges, catalog/range-policy versions, canonical catalog fingerprint, and per-profile fingerprints. Reuse the existing 128-entry GM import lookup as input; expose the curated arrangement palette without moving import ownership or rewriting import/normalizer/analysis/validator behavior in this task.
  - Deliverable: resolve exact profile ID first, then exact alias/program, then coarse identity without substring bugs such as bassoon→bass or English horn→brass. Return explicit baseline instrument/program/channel mismatch findings instead of silently changing existing V2 metadata.
  - Expected behavior: existing V2/import documents remain valid and unchanged; added, re-instrumented, or pitch-adjusted arrangement targets always materialize internally consistent labels, programs, drum flags, channels, and concert-pitch ranges. Unchanged source tracks may retain baseline mismatch/outlier findings. Analysis retains `note_outside_instrument_range`, while arrangement can distinguish absolute versus preferred target-range findings.
  - Tests: catalog schema/version/fingerprints, override load and invalid override failure, container-visible path handling, stable common program vectors, alias collision, all range boundaries, bassoon/English-horn regressions, source-program precedence, unknown/unbounded policies, canonical role-list independence, and no V1/V2 serialization changes.
  - Logging requirements: INFO catalog version/source/profile count on load; WARN/ERROR only sanitized path category and validation code on failure; DEBUG profile/alias/count diagnostics. Never log the full external catalog, note data, or compositions.
  - Dependencies: none.

- [x] Task 2: Define strict arrangement request, provider draft, candidate, manifest, assertion, warning, and error contracts.
  - Files: create `backend/app/arrangement_schemas.py` and `backend/tests/test_arrangement_schemas.py`; reuse `CompositionV2`, `LLMModelSelection`, and existing bounded settings patterns without importing services into schemas.
  - Deliverable: implement all ten operations, source/protected IDs, one-row-per-part before/after requirements, role/doubling policies, preservation flags, range-adjustment policy, candidate/options bounds, and operation-specific validation. Define transient source references, bounded explicit note drafts, topology/change/density/range/duplicate summaries, valid-candidate and rejected-attempt DTOs, algorithm versions, and stable domain error/warning codes.
  - Expected behavior: V1, duplicate or unknown IDs, source/protected overlap, no-op instrumentation, invalid accompaniment roles, piano-to-ensemble without piano or multiple targets, countermelody without its role, doubling without exactly one authorized relationship, and excessive request/draft sizes fail before realization/provider use.
  - Tests: complete operation matrix, strict extra-field rejection, normalized unique part IDs with derived aggregate counts, before/after inventories, candidate/rejection bounds, text/context limits, invalid draft metadata, list bounds, serialization/OpenAPI compatibility, and response/catalog/profile fingerprint consistency.
  - Logging requirements: schemas do not log payloads; boundary helpers may DEBUG stable validation codes, field names, and counts only. Never log instructions, drafts, compositions, or note arrays.
  - Dependencies: Task 1.

- [x] Task 3: Build deterministic arrangement context, instrumentation preconditions, source-note references, and bounded provider projection.
  - Files: create `backend/app/services/composition_arrangement_context.py` and `backend/tests/test_composition_arrangement_context.py`; reuse `composition_edit_fingerprint.py`, role/density/harmony analysis, timeline, and bounded analysis-context helpers.
  - Deliverable: validate source/protected authorization and explicit before inventory; compute actual instrument/role/part counts; derive effective melody/lead identity; assign deterministic request-local references to every logical source note including ID-less events and ties; summarize register, density, rhythm, sections, harmony, and target ranges within a fixed context budget. Always include the complete compact selected-note reference table; truncate advisory analysis/examples first and reject a selection exceeding the hard note/context cap with `422` before provider invocation.
  - Deliverable: keep authored roles and inferred roles separate, with declared roles authoritative unless an ambiguity warning is necessary. Mark analysis as advisory and never persist it or use harmony metadata as playable content.
  - Expected behavior: the example piano melody/accompaniment/bass source produces a bounded context that identifies separate piano roles and exact target cello/string ranges without dumping the source document. Invalid before requirements or source IDs fail before an LLM call.
  - Tests: deterministic references, ID-less events, tie chains, same instrument/different roles, ambiguous roles, explicit inventory counts, protected sets, empty harmony, mixed meter, drums, non-truncatable selected-note references, advisory truncation order, over-limit pre-provider rejection, analysis failure fallback, and source immutability.
  - Logging requirements: INFO operation, selected/protected track counts, before/after part counts, event count, context size/truncation, and fingerprint prefix; DEBUG normalized identities, role counts, and metric names only. Never log note references, pitches, prompts, analysis reports, or instructions.
  - Dependencies: Tasks 1 and 2.

### Phase 2: Deterministic Realization and Musical Validation

- [x] Task 4: Implement operation-specific topology realization with explicit canonical notes, deterministic IDs, safe channels, and motif reconciliation.
  - Files: create `backend/app/services/composition_arrangement_patch.py` and `backend/tests/test_composition_arrangement_patch.py`; update `backend/app/services/composition_region_patch.py` only to expose genuinely reusable tie/boundary/ID helpers without weakening existing edits; update `backend/app/services/composition_midi.py` for shared-channel program conflict validation.
  - Deliverable: realize source-note references by exact copying, materialize provider-created notes, add/remove/re-instrument/split/merge tracks according to the operation, allocate deterministic collision-free track/event IDs and GM channels, apply catalog programs/ranges to changed/created targets, sort events/tracks deterministically, preserve global metadata, and reconcile motifs. Keep all events in one affected motif occurrence on one primary target; reject drafts that split an occurrence across targets, and keep doubling copies outside the source occurrence.
  - Deliverable: implement controlled octave folding only for authorized non-melody notes when requested; preserve pitch class/timing and report every adjustment. Never clamp protected melody, truncate ties, invent missing referenced material, silently drop a track, or accept provider IDs/programs/channels.
  - Expected behavior: piano-to-ensemble distributes low, middle, and melody material across requested targets rather than cloning the complete piano sketch; every accepted target has explicit `events[]`; more than 15 independently programmed pitched channels returns an actionable error.
  - Tests: every operation's topology, deterministic IDs, channel 10 reservation/exhaustion/sharing, exact note copy, generated note materialization, octave policy, expressive event/tie preservation, motif one-target remap, split rejection and authorized prune, no input mutation, canonical ordering, shared-channel program/CC conflicts, and MIDI/MusicXML smoke.
  - Logging requirements: INFO operation, added/removed/re-instrumented track counts, copied/generated/removed/adjusted event counts, channel allocation result, motif reconciliation count, and codes; DEBUG bounded per-track count summaries and collision counts. Never log IDs in bulk, pitches, event bodies, or complete documents.
  - Dependencies: Tasks 1-3.

- [x] Task 5: Add dedicated arrangement preservation, range, harmony, density, duplicate, and postcondition validation.
  - Files: create `backend/app/services/composition_arrangement_validation.py` and `backend/tests/test_composition_arrangement_validation.py`; update `backend/app/services/composition_validator.py` and its tests with an opt-in practical-range scope while preserving the default behavior; reuse `composition_harmony_compatibility.py`, density/role analysis, and content-overlap helpers from `instrument_identity.py` through public APIs.
  - Deliverable: validate exact global/protected/unselected preservation, semantic melody multisets across tracks, unchanged harmony metadata, generated-note harmonic compatibility, actual after instrumentation/counts/roles, absolute/preferred ranges for changed/created target material, baseline-only findings for untouched source tracks, source-note cardinality, density direction, audible effect, topology authorization, motif integrity, and canonical V2 validity. Add an explicit validator option that still runs all structural checks but scopes practical-range hard errors to changed target track IDs; default callers retain today's all-track behavior, and arrangement may suppress only source/candidate range errors proven byte-identical at the same track/event location.
  - Deliverable: reject accidental exact/high-overlap same-instrument/same-role clones plus cross-role/cross-instrument source-target clones using onset/rhythm-normalized contour, octave, and transposition similarity checks. Allow distinct same-instrument parts and only explicitly declared unison/octave doubling. Return bounded findings/assertions and measured before/after metrics for the UI.
  - Expected behavior: impossible preserved-melody range, missing requested target part, unrelated rewritten melody, changed harmony metadata, failed density direction, full-part piano clones, or unexplained topology fail candidate realization. Preferred-range outliers and mild countermelody non-chord tones remain explicit warnings where policy allows.
  - Tests: melody dropped/duplicated/transposed/rhythm-changed, cross-instrument/cross-role and octave-clone rejection, intentional doubling exception, legitimate Violin I/II, all catalog range boundaries, questionable and impossible changed-target ranges, unchanged imported outlier/mismatch baseline acceptance, changed outlier rejection, default validator regression, structural errors outside the range scope, unknown target instrument, harmony exactness/compatibility, each density operation, simplification subset, empty/inaudible tracks, after count mismatch, and contradictory manifest summaries.
  - Logging requirements: INFO assertion outcome counts, density deltas, range/duplicate/compatibility codes, and candidate status; DEBUG rounded aggregate metrics and normalized instrument-role pairs only. Never log source/candidate events, pitches, harmony arrays, or full findings with musical payloads.
  - Dependencies: Task 4.

### Phase 3: LLM, Fake Provider, and HTTP Integration

- [x] Task 6: Implement independent multi-candidate LLM orchestration, bounded repair, and deterministic fake drafts for every operation.
  - Files: create `backend/app/services/llm_composition_arrangement.py`; update `backend/app/services/fake_llm.py`; refactor `backend/app/services/composition_edit_fingerprint.py` to contain only composition-level canonical hashing and move any development request/candidate-ID derivation into a development-owned helper before arrangement consumes it; minimally share provider/model selection helpers if existing private APIs would otherwise be duplicated; create `backend/tests/test_llm_composition_arrangement.py`, extend `backend/tests/test_composition_edit_fingerprint.py`, and extend `backend/tests/test_llm_fake_provider.py`.
  - Deliverable: invoke one structured-output call per candidate with immutable operation constraints, transient note references, target profiles/ranges, before/after inventory, and bounded advisory context. Independently validate/realize each draft, repair with stable codes and aggregate summaries only, permit partial success, and fail with sanitized `502` when none survive.
  - Deliverable: fake mode implements all operations and candidate variation through the production context/realization/validation path. Its example mapping retains recognizable piano melody, maps bass support to cello where requested, and distributes accompaniment across piano/string ensemble; it must not return preassembled V2. Collect rejected attempts as bounded ordinal/stage/code summaries while returning valid candidates separately.
  - Expected behavior: provider choices affect bounded arrangement decisions, but cannot change protected metadata, IDs, programs, channels, or authorization. Multiple candidates are independently fingerprinted and a failed repair cannot contaminate another candidate.
  - Tests: every fake operation, example acceptance, 1-4 separate calls, candidate independence/distinctness, composition-level fingerprint decoupling, OpenAI/DeepSeek selection, no provider, malformed draft, repair success/exhaustion, partial success with rejected-attempt summaries, timeout/error sanitization, context/request caps before invocation, and no network in fake mode.
  - Logging requirements: INFO request/candidate ordinal, operation, provider/model, stage, repair attempt, outcome code, counts, fingerprint prefix, and elapsed time; DEBUG prompt character budget and diagnostic-code counts. Never log prompt/instruction text, raw provider output, API keys, drafts, events, or candidates.
  - Dependencies: Tasks 1-5.

- [x] Task 7: Expose the instrument catalog and stateless arrangement preview through thin FastAPI routes.
  - Files: create `backend/app/routers/arrangement.py`; update `backend/app/main.py` only to register the router; update `backend/app/ready.py`; create `backend/tests/test_composition_arrangement_routes.py`; extend `backend/tests/test_openapi_v2.py`, `backend/tests/test_health_and_cors.py`, and `backend/tests/test_secret_hygiene.py`.
  - Deliverable: implement `GET /composition/arrangement/instruments` and `POST /composition/arrangement/preview`; return the complete canonical track-role vocabulary separately from instrument role suggestions; load validated catalog/provider settings at the boundary. Map impossible request/preflight ranges and authorization/inventory errors to `422`; map provider-generated range/postcondition violations that exhaust repair/all candidates to `502`; map unavailable provider/catalog dependency to `503`; map unexpected failures to sanitized `500`.
  - Deliverable: expose catalog validity/version/fingerprint in readiness without leaking override contents, and make the global readiness result false when a configured arrangement catalog cannot be loaded or validated. Return stable `{code, message, details?}` errors with bounded scalar/code details and no project persistence or rendering side effects.
  - Expected behavior: OpenAPI exposes V2-only strict contracts and 1-4 canonical candidates plus bounded rejected-attempt summaries sharing the exact source/catalog fingerprints. Arrangement controls consume independent authoritative role and instrument lists; the synchronous core V2 validator retains its small local role mirror with an explicit backend/frontend parity test.
  - Tests: route/catalog success, canonical role parity, all operations, invalid V1/body/source/inventory, preflight range `422`, provider range exhaustion `502`, no provider, invalid catalog override with `ready: false`, partial/total provider failure, source immutability, no SQLite writes, CORS/OpenAPI/readiness, safe error details, and log/secret hygiene.
  - Logging requirements: INFO endpoint, operation, provider/model, selected/target/candidate counts, catalog version, fingerprint prefix, warning count, status, and elapsed time; WARN/ERROR stable code and exception class once at the boundary. Never log catalog payloads, instructions, compositions, events, prompts, keys, or provider bodies.
  - Dependencies: Task 6.

- [x] Task 8: Complete backend regression, scale, import, analysis, and export-fidelity coverage.
  - Files: extend tests from Tasks 1-7 plus `backend/tests/test_instrument_identity.py`, `backend/tests/test_import_instruments.py`, `backend/tests/test_composition_analysis_warnings.py`, `backend/tests/test_composition_validator.py`, `backend/tests/test_composition_midi.py`, `backend/tests/test_music_json_renderer.py`, and `backend/tests/test_export_fidelity.py`; add a compact arrangement fixture only if existing builders cannot express the acceptance source clearly.
  - Deliverable: prove the arrangement catalog does not alter persisted/migrated/imported source metadata, update `backend/app/services/music_json_renderer.py` and playback/export resolution only where required so explicit target instrument/program identity is not overridden by role, keep practical range policy scoped to changed arrangement targets, and prove accepted candidates remain playable/exportable from explicit events.
  - Expected behavior: generation, import, migration, analysis, motif, harmony, development, region edit, and export semantics remain stable. Maximum allowed source/candidate/context sizes are bounded and over-limit requests fail before provider invocation.
  - Tests: common GM profile matrix, imported mismatches, empty harmony, expressive V2, ties/motifs/drums, variable meter/key/tempo, 15-channel limit, four-candidate scale, repeated deterministic fake runs, MIDI/MusicXML/WAV-ready projection, and captured-log prohibited-data assertions.
  - Logging requirements: tests assert useful DEBUG/INFO stage/count/code/version fields while proving distinctive instructions, secrets, pitches, event IDs, serialized compositions, and provider content never appear.
  - Dependencies: Tasks 1-7.

### Phase 4: Frontend Candidate Lifecycle and UX

- [x] Task 9: Add frontend catalog/API contracts and topology-aware pure candidate verification.
  - Files: create `frontend/src/utils/compositionArrangementCandidates.js`, `frontend/src/utils/compositionArrangementCandidates.test.js`, and `frontend/src/utils/appLogger.js` if no existing environment-controlled logger can satisfy the feature; update `frontend/src/api/musicApi.js`, `frontend/src/api/musicApi.test.js`, and `frontend/src/utils/compositionCandidates.js` only to expose shared full-document fingerprint primitives without weakening development verification.
  - Deliverable: load/cache the versioned selectable catalog; normalize every operation request and explicit part mapping; validate response bounds, candidate compositions, version/fingerprint consistency, manifests, summaries, warnings, and required assertions; preserve structured API errors.
  - Deliverable: independently recompute source/candidate fingerprints and actual topology/event diffs. Verify the currently loaded catalog fingerprint and target-profile fingerprints still match, every base/result track is explained, protected/unselected tracks are exact, added/removed/re-instrumented tracks are authorized, complete event fields and root metadata satisfy preservation, motif references are valid, and server summary counts agree with actual V2.
  - Expected behavior: malformed, stale, tampered, unexplained, or failed-assertion candidates never enter/apply from store state. Valid topology changes are accepted without weakening the existing development verifier that requires fixed topology.
  - Tests: catalog normalization/version/fingerprint changes, role-list parity, every request operation, source/protected conflicts, one-row-per-part mappings, duplicate IDs, fingerprint parity vectors, valid/invalid topology manifests, metadata/event tampering, protected changes, intentional doubling, summary contradictions, rejected-attempt normalization, range warnings, canonical validation, and request immutability.
  - Logging requirements: API/utils may DEBUG endpoint, operation, catalog version, counts, status/code, and fingerprint prefixes only through an environment-controlled logger (for example `VITE_LOG_LEVEL`, disabled/reduced in production without source edits). Never console-log request/response payloads, instructions, compositions, manifests containing event data, or events.
  - Dependencies: Tasks 2 and 7.

- [x] Task 10: Implement ephemeral Zustand arrangement state, race/stale handling, topology-safe Apply/undo, and isolated audition playback.
  - Files: update `frontend/src/store/musicStore.js`, `frontend/src/components/PlaybackControls.jsx`, `frontend/src/components/TrackPlaybackControls.jsx`, `frontend/src/utils/playbackTracks.js`, and `frontend/src/utils/playbackTracks.test.js`; create `frontend/src/store/musicStore.arrangement.test.js`; extend other focused playback/state helpers and tests as required.
  - Deliverable: add operation/source/protected/target/preservation/range/candidate controls; catalog/loading/error state; request sequence/base revision/fingerprint; candidates/selection; stale reason; source/candidate audition mode; and separate candidate mixer controls. Ignore superseded responses and mark retained previews stale when source/settings change.
  - Deliverable: preview/select/audition/discard without composition/history/dirty/autosave/analysis/notation mutation. Apply only after Task 9 verification through `applyCompositionEdit`, creating one history entry, recovering selected track/note IDs, snapshotting/restoring mixer state for topology undo/redo, invalidating other previews, reconciling motif/analysis selections, and explicitly refreshing notation. Update browser playback strategy to prefer canonical instrument/program identity and use role only as a fallback, with diagnostics routed through the Task 9 controlled logger.
  - Expected behavior: removed tracks cannot leave invalid piano-roll selection; new tracks receive mixer defaults; retained tracks keep working mixer settings; candidate audition cannot prune source controls; project hydration/import/generation fully resets arrangement state.
  - Tests: request races, discard/in-flight invalidation, settings/source/catalog staleness, candidate selection/audition isolation, instrument/program-over-role playback selection, controlled playback diagnostics, mixer independence, fingerprint tamper rejection, one-entry apply/undo/redo with exact topology and mixer restoration, autosave only after Apply, analysis/notation refresh, selected-track recovery, other-preview invalidation, and no preview persistence.
  - Logging requirements: INFO request/select/audition/apply/discard lifecycle with operation, revision, counts, candidate ID suffix/fingerprint prefix, status/code; DEBUG race/stale reasons and assertion-code counts through the Task 9 environment-controlled logger. Never log instructions, candidates, composition/event data, catalog payloads, or project JSON.
  - Dependencies: Task 9.

- [x] Task 11: Build the responsive accessible Arrange tab for explicit source/target mapping, candidate comparison, audition, and Apply.
  - Files: create `frontend/src/components/ArrangementPanel.jsx`; update `frontend/src/components/ComposerWorkspace.jsx`; update `frontend/src/components/TrackPlaybackControls.jsx` for `aria-pressed` and track-specific labels if not completed in Task 10.
  - Deliverable: add an `Arrange` tab after Develop with operation-specific help; source and protected track fieldsets; actual before inventory; target part rows populated from the backend catalog with independent role/instrument controls; preservation/range controls; candidate count/provider/model/instruction; and clear invalid-request reasons.
  - Deliverable: render valid candidates as a real radio group with before/after instrumentation, topology and event counts, density deltas, preservation assertions, range/duplicate/harmony warnings, provider/model, and expandable details. Render rejected attempts separately with ordinal/stage/stable reasons and no audition/apply controls. Add Generate/Regenerate, Play source, Audition candidate, Discard, Apply selected, and inline confirmation summarizing destructive/warning counts.
  - Expected behavior: the user can express piano melody + piano accompaniment + bass → piano + cello + string ensemble without conflating role and instrument. Rejected attempts remain explainable but cannot be selected/auditioned/applied; a catalog fingerprint or composition/settings change leaves the preview visibly stale until regenerated; layouts do not overflow at 390px.
  - Tests: expose stable Playwright selectors; use semantic fieldsets/legends, radio controls, `aria-live` status, alerts, keyboard focus, 44px touch targets, wrapped mobile cards, reduced-motion styling, and non-color warning labels. Avoid a new component-test dependency unless existing tools require it.
  - Logging requirements: no render-time logging. UI actions use the environment-controlled Task 9 logger with bounded operation/part/candidate/status metadata only; never log freeform instruction or musical payloads.
  - Dependencies: Task 10.

### Phase 5: Acceptance, Documentation, and Quality Gates

- [x] Task 12: Finish frontend API, utility, store, playback, history, and regression coverage.
  - Files: complete `frontend/src/api/musicApi.test.js`, `frontend/src/utils/compositionArrangementCandidates.test.js`, `frontend/src/store/musicStore.arrangement.test.js`, `frontend/src/utils/musicJsonValidation.test.js`, and relevant playback/store regression suites.
  - Deliverable: prove non-mutating preview/selection/audition, strict topology authorization, catalog-driven roles/instruments/programs, safe stale/tamper handling, atomic topology Apply, mixer/history restoration, and no candidate state in project payloads across all operations.
  - Expected behavior: existing generation, import, analysis, motif, harmony, development, piano-roll, playback, notation, project, save, and reset tests retain their semantics.
  - Tests: all ten operations, source/target inventory counts, protected tracks, range hard/warning paths, same-instrument roles, accidental/intentional doubling, topology add/remove/reorder, selected-track removal, motifs, variable meter, empty harmony, API 422/502/503, console hygiene, and backend/frontend fingerprint vectors.
  - Logging requirements: captured-console assertions verify the environment/build gate suppresses disabled levels, permit only operation/version/count/status/code/fingerprint metadata when enabled, and reject instruction text, event IDs/pitches, complete candidates, compositions, and API payloads.
  - Dependencies: Tasks 9-11.

- [ ] Task 13: Add deterministic Playwright acceptance for the example, every operation family, stale protection, persistence, and mobile accessibility.
  - Files: create `frontend/e2e/arrangement-workflow.spec.js`; update `frontend/e2e/helpers.js` and mock/fake-provider routing helpers as needed.
  - Deliverable: seed piano melody, piano accompaniment, and bass; request three piano/cello/string-ensemble candidates with melody preservation; prove the working V2/revision/history/save state is unchanged through preview and audition; apply a non-first candidate; verify recognizable melody, exact harmony/top-level metadata, explicit target notes, catalog-consistent programs/ranges, declared topology, no accidental clones, one undo entry, and save/reopen persistence of only the applied V2.
  - Deliverable: cover add/remove accompaniment, simplify/density direction, countermelody, intentional doubling, stale after note/settings change, warning confirmation, rejected impossible range, source/candidate playback, undo/redo, API failure atomicity, keyboard operation, and 390x844 no-overflow layout.
  - Expected behavior: unselected candidates/instructions never persist; playback, notation, MIDI/MusicXML export, and mixer use the applied candidate's canonical events only; protected source material remains exact.
  - Logging requirements: E2E may assert sanitized operation/candidate-count/version/status/code logs only. Do not emit or attach raw prompts, instructions, compositions, event arrays, or secrets beyond normal governed Playwright failure artifacts.
  - Dependencies: Tasks 7, 11, and 12.

- [ ] Task 14: Document arrangement contracts, catalog/range policy, operation semantics, lifecycle, safety guarantees, and tests.
  - Files: create `docs/composition-arrangement.md`; update `README.md`, `docs/composition-v2.md`, `docs/composition-analysis.md`, `docs/project-persistence.md`, `docs/testing.md`, `docs/CODEBASE_MAP.md`, `.env.example`, `docker-compose.yml`/`compose.dev.yml` usage documentation where the optional override is exposed, and `AGENTS.md`. Route documentation through the mandatory `$aif-docs` checkpoint.
  - Deliverable: document API examples including explicit before/after instrumentation, role-versus-instrument semantics, curated GM scope, configurable catalog validation/versioning, concert-pitch absolute/preferred ranges, hard errors versus warnings, source-note redistribution, duplicate/doubling policy, harmony/melody preservation, explicit-event requirement, channel limits, topology manifest, fingerprints, candidate lifecycle, fake mode, and error/warning codes.
  - Deliverable: document the piano/cello/string-ensemble acceptance example, focused/full test commands, privacy-safe logging fields, frontend log-level control, and deferred sectional/advanced orchestration scope. State clearly that previews are session-only and only applied Composition V2 is persisted. Explain that an override path must be visible inside the backend container and provide a read-only Compose override example rather than implying an arbitrary host path works automatically.
  - Expected behavior: README remains a concise entry point; V2 docs do not imply catalog IDs/ranges are persisted; codebase maps and AGENTS identify all new entry points accurately.
  - Logging requirements: docs list allowed operation/stage/provider/model/version/count/timing/code/fingerprint fields and prohibited keys, prompt/instruction text, provider output, full composition/analysis, event/pitch/harmony arrays, and catalog override contents.
  - Dependencies: Tasks 7, 11, 12, and 13.

- [ ] Task 15: Run focused and full quality gates and fix regressions without weakening arrangement invariants.
  - Files: no planned artifact beyond corrections required by failing checks; do not create a test report.
  - Deliverable: run focused backend catalog/schema/context/patch/validation/LLM/route tests, full backend pytest, frontend unit tests, production build, focused fake-provider Playwright arrangement workflow, and relevant development/harmony/motif/import/export E2E regressions when the stack is available.
  - Commands: from `backend/`, run `../.venv/bin/python -m pytest tests/test_instrument_catalog.py tests/test_arrangement_schemas.py tests/test_composition_arrangement_context.py tests/test_composition_arrangement_patch.py tests/test_composition_arrangement_validation.py tests/test_llm_composition_arrangement.py tests/test_composition_arrangement_routes.py`, then `../.venv/bin/python -m pytest`. From `frontend/`, run `npm test`, `npm run build`, and `npm run test:e2e -- e2e/arrangement-workflow.spec.js` against `LLM_FAKE_MODE=1`.
  - Expected behavior: all available gates pass and the acceptance scenario proves meaningful redistribution, exact requested preservation, explicit canonical target events, range/duplicate enforcement, non-mutating preview, and apply-only persistence. Report unavailable live E2E dependencies in the final implementation response rather than claiming success.
  - Logging requirements: run captured-log/secret-hygiene assertions at DEBUG and inspect failures without adding raw provider, catalog, instruction, composition, or event payloads to diagnostics.
  - Dependencies: Tasks 1-14.

## Risks and Mitigations

- Practical range policy is not standardized by GM. Version it separately, use concert pitch, distinguish absolute/preferred/unknown policies, expose the exact version in candidates, and avoid treating all manual/imported V2 range outliers as schema errors.
- A catalog override can change under the same human-readable version. Fingerprint canonical catalog/profile content, invalidate previews on any mismatch, and never claim a warning is reproducible from a version label alone.
- Catalog integration can accidentally change imports or old generation defaults. Keep arrangement metadata separate from import ownership in V1, preserve source-program and migration fidelity, and test known import/generation vectors before any later consolidation.
- Instrument label and GM program can disagree in existing V2. Report the mismatch; do not rewrite persisted source metadata. Require newly arranged target tracks to be internally consistent.
- Legitimate orchestration repeats instruments. Make part IDs, role, source mapping, and doubling policy explicit; classify content overlap instead of banning same-instrument tracks globally.
- Melody recognizability cannot be fully judged by a deterministic metric. Make exact semantic preservation a hard option, add transparent similarity diagnostics for relaxed future modes, and never advertise an unrelated candidate as preserved.
- Harmony metadata can be empty or inaccurate. Preserve it exactly, use declared/inferred harmony only as bounded advisory compatibility evidence, and avoid synthesizing hidden notes from it.
- Topology-changing candidates create a larger apply attack surface. Require a complete server manifest plus independent client fingerprint/diff verification and reject every unexplained track or event change.
- More parts can exhaust MIDI channels or create channel-wide program/controller conflicts. Reserve channel 10, allocate deterministically, validate sharing compatibility, and fail before Apply/export when representation is unsafe.
- Moving/removing events can break motif occurrences, ties, expression, and selection state. Reconcile through deterministic maps, validate references, preserve complete event semantics, and include mixer/selection state in topology-aware undo.
- Selected note references cannot be advisory-truncated when exhaustive redistribution is required. Reserve prompt budget for the complete compact reference table and reject over-limit source selections before calling a provider.
- Full candidates increase memory, latency, and provider cost. Bound input events/context/candidates/repairs, generate candidates independently, permit partial success, and clear/invalidate ephemeral state predictably.
- The Zustand store is already large. Keep request normalization, topology diffing, and verification in pure utilities; add only orchestration state/actions to the store and defer an unrelated store-slice refactor.

## Definition of Done

- All ten arrangement operations produce strict, explicit-note `composition.v2` candidates through fake mode and mocked real-provider drafts.
- The piano melody/accompaniment/bass acceptance source can be redistributed to piano, cello, and string ensemble while the requested melody and harmony remain verifiably recognizable/exact and no accidental duplicate tracks are introduced.
- Roles and instruments remain independent, requested before/after inventories are enforced, target programs/channels are GM-compatible, and the curated configurable catalog is versioned and exposed to the frontend.
- Absolute range failures in changed/created target material block candidates; preferred-range concerns are visible warnings requiring confirmation; unchanged source outliers remain baseline findings and no protected melody is silently octave-shifted.
- Users can generate, compare, audition, discard, and apply candidates. Nothing mutates, dirties, autosaves, or persists before Apply.
- Apply independently verifies fingerprints, topology, preservation, summaries, motifs, and canonical validity; it is one-step undoable/redoable and safely reconciles mixer, track selection, analysis, notation, playback, and other previews.
- Backend unit/route/fake/provider tests, frontend API/util/store/playback tests, Playwright acceptance, full pytest, `npm test`, and `npm run build` pass where the required stack is available.
- Verbose observability is useful and controlled by backend `LOG_LEVEL` plus frontend `VITE_LOG_LEVEL` (with production suppression tests) without leaking secrets, prompts/instructions, provider output, compositions, analysis reports, event arrays, or pitch data.
- Documentation, README, project maps, environment template, and AGENTS are updated through the mandatory docs checkpoint.
