# Implementation Plan: MIDI and MusicXML Import into Composition V2

Branch: main
Created: 2026-09-08

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Import limits: configurable safe limits
- Planning depth: full, extra thorough

## Roadmap Linkage
Milestone: "MIDI and MusicXML import"
Rationale: Add a new ingestion milestone that extends the canonical Composition V2 workspace beyond generated and migrated JSON.

## Goal

Add secure MIDI and MusicXML ingestion that converts uploaded files directly into strict `composition.v2`, reports every material approximation or omission, and installs the result as the same canonical state used by playback, piano roll, notation, persistence, export, and AI region editing.

## Scope And Decisions

- Provide separate `POST /imports/midi` and `POST /imports/musicxml` multipart endpoints backed by shared import orchestration. Accept `.mid`/`.midi`, `.musicxml`/`.xml`, and compressed `.mxl`; detect format from content rather than trusting the filename or MIME type.
- Return `{ composition, musicxml, import_report }`. `composition` is strict V2, `musicxml` is regenerated from that V2 rather than returning untrusted source XML, and `import_report` contains source/result counts plus stable issue codes and bounded user-facing details.
- Treat imported files as ingress only. After conversion, `tracks[].events[]` is the sole playable source. Set `harmony: []`; do not run tonal, chord, form, or other musical analysis during import.
- Extend the canonical vocabulary with neutral `role: "other"` and section type `"unsectioned"`. Existing generated values remain unchanged. Use `other` unless explicit source metadata, percussion channel, or an unambiguous General MIDI family/name supports a role. Use one full-score `unsectioned` section unless explicit source markers map confidently to supported section labels.
- Preserve source MIDI programs and channels where representable. Add one authoritative General MIDI program/name/family map and route MusicXML aliases through the existing instrument identity normalization. Split MIDI source material deterministically by source track, channel, and program segment when required to avoid conflating instruments.
- Generate deterministic IDs from normalized source coordinates and short stable digests, with deterministic collision suffixes. Track IDs must remain stable across identical re-imports; every note event receives a globally unique stable ID.
- Preserve MIDI PPQ when it is valid and within the configured cap. For MusicXML, select the smallest bounded PPQ that exactly represents divisions/tuplets; if the cap prevents exact representation, quantize deterministically to nearest ticks, keep positive durations and ordering, and report the affected count/error bound.
- Preserve absolute source note timing. Pad the canonical end to the next complete bar because V2 requires complete-bar duration; represent pickups and partial final measures without inventing notes and report the structural normalization. Reject meter maps that cannot be represented without relocating a source meter change or notes.
- Preserve root and changed tempo, meter, and key metadata when V2 can represent them. Resolve duplicate same-tick declarations deterministically. When metadata is absent, use explicit compatibility defaults (`120 BPM`, `4/4`, `C major`) and report each default; never describe a default key as inferred analysis. Reject or report out-of-range/non-representable metadata according to stable policy rather than silently clamping.
- MIDI pitch spelling is unavailable, so use a deterministic key-aware spelling when a source key exists and a fixed sharp spelling otherwise; report `pitch_spelling_inferred`. MusicXML uses concert pitch for canonical playback and reports transposition normalization while preserving source enharmonic spelling where possible.
- Preserve note velocity, polyphony, track volume/pan/expression, CC automation, and sustain where representable. Pair overlapping same-pitch MIDI notes with FIFO queues; handle velocity-zero note-on as note-off; omit unmatched note-offs and close/drop dangling note-ons only under a documented deterministic policy with warnings.
- Preserve supported MusicXML voices, staves, ties, articulations, dynamics, pedals, tempo, meter, key, markers, and pitched notes. Expand repeats into linear playback order within complexity limits. Omit unsupported grace/cue notes, ornaments, lyrics, arbitrary directions, microtones, and unsafe/inexpressible constructs with explicit issue codes.
- Keep import diagnostics distinct from later export projection diagnostics and later optional analysis. Import warnings are displayed for the current import session but are not added to the canonical composition or persisted as fake LLM generation metadata.
- Importing from the project browser creates a project only after conversion succeeds. Importing in an open project requires replace confirmation, atomically replaces the composition, clears generation metadata, cancels/supersedes stale autosaves, and schedules persistence of the imported V2.
- A failed import must leave the current project/composition untouched. Import must work without an LLM provider; only the later AI edit action requires one.

## Import Report Contract

- Status values: `exact`, `approximated`, or `partial`; endpoint failures use structured HTTP errors rather than a successful report with `failed` status.
- Issue severities/actions: `info`, `warning`; `defaulted`, `normalized`, `quantized`, `omitted`.
- Each issue includes a stable code, bounded message, count, and optional sanitized source locator such as track/part/measure index. It never includes source bytes, XML excerpts, lyrics, absolute paths, or raw parser errors.
- Initial stable codes include `tempo_defaulted`, `tempo_rounded`, `meter_defaulted`, `key_defaulted`, `pitch_spelling_inferred`, `partial_measure_padded`, `pickup_normalized`, `timing_quantized`, `ppq_rescaled`, `program_change_split_track`, `instrument_defaulted`, `role_inferred`, `role_defaulted`, `section_defaulted`, `repeat_expanded`, `transposition_normalized`, `unsupported_midi_event_omitted`, `unsupported_notation_omitted`, `grace_note_omitted`, `dangling_note_omitted`, and `source_id_collision`.
- Summary fields include detected format, sanitized display filename, input bytes, source PPQ/divisions, target PPQ, source/result track and note counts, bar count, duration ticks, metadata change counts, and issue counts by action. Freeform source text is excluded.

## Error And Limit Contract

- Map oversized bytes or expanded archives to `413`, unsupported/contradictory formats to `415`, malformed or canonically non-representable recognized files to `422`, unavailable parser dependencies to `503`, and unexpected failures to a sanitized `500`.
- Add bounded environment settings for upload bytes, expanded MXL bytes/compression ratio/entry count, tracks/parts, notes/events, bars/measures, PPQ, metadata changes, and active MIDI notes. Defaults should support normal multi-track files while preventing unbounded memory or CPU use; enforce limits in backend code even when requests bypass Nginx.
- Read uploads in bounded chunks, reject empty input, close `UploadFile` on every path, and do not extract MXL paths to the filesystem. Reject encrypted/nested/unsafe ZIP entries and validate `META-INF/container.xml` plus exactly one score root.
- Reject XML DTD/entity declarations and external resources before `music21` receives content. Add a pinned hardened XML dependency if required, keep dependency manifests synchronized, and never render uploaded XML directly in the browser.

## Commit Plan
- **Commit 1** (after tasks 1-3): `feat(import): define canonical import contract and MIDI conversion`
- **Commit 2** (after tasks 4-6): `feat(import): add secure MusicXML ingestion and API routes`
- **Commit 3** (after tasks 7-8): `feat(frontend): add canonical composition import workflows`
- **Commit 4** (after tasks 9-11): `test(import): cover fidelity security and user journeys`
- **Commit 5** (after task 12): `docs(import): document MIDI and MusicXML ingestion`

## Tasks

### Phase 1: Canonical Contract And Shared Conversion

#### Task 1: Define import DTOs, neutral vocabulary, policies, and limits
- [x] Add strict import response/report/issue/source-summary models and domain error classes in `backend/app/import_schemas.py`; register stable issue and error codes as closed literals/constants so backend tests and frontend rendering share predictable semantics.
- [x] Extend `SUPPORTED_TRACK_ROLES` with `other` and `SUPPORTED_SECTION_TYPES` with `unsectioned` in `backend/app/composition_schemas.py`; mirror both values and their validation behavior in `frontend/src/utils/musicJsonValidation.js` and related fixtures/tests without changing generation requirements or silently mapping existing roles.
- [x] Add `backend/app/import_settings.py` with bounded environment parsing for byte and complexity settings. Wire documented defaults through `.env.example` and `docker-compose.yml`; keep limits independent from LLM generation's 32-bar/6-instrument budget.
- [x] Record the conversion policies from this plan as executable constants/types, including source defaults, supported extensions/signatures, PPQ/rounding rules, neutral values, and issue-code meanings. Do not add source-only fields to `CompositionV2`.
- [x] Logging: DEBUG effective non-sensitive limits and schema dispatch; INFO successful settings load; WARNING invalid environment values falling back to defaults; never log filenames, source text, bytes, or complete compositions. Runtime verbosity remains controlled by `LOG_LEVEL`.
- [x] Files: `backend/app/import_schemas.py`, `backend/app/import_settings.py`, `backend/app/composition_schemas.py`, `frontend/src/utils/musicJsonValidation.js`, `.env.example`, `docker-compose.yml`, and focused schema/settings tests.

#### Task 2: Build shared source-to-V2 canonicalization
- [x] Create `backend/app/services/composition_import.py` with parser-neutral source dataclasses and orchestration for metadata defaulting, PPQ scaling, complete-bar duration construction, neutral section creation, marker normalization, deterministic ordering, ID allocation, instrument/role mapping, issue aggregation, complexity checks, and final `CompositionV2.model_validate()` plus `compile_timeline()` acceptance.
- [x] Add an authoritative full General MIDI level-1 program map and canonical instrument aliases, preferably in a cohesive `backend/app/services/import_instruments.py` that reuses `instrument_identity.py`. Preserve source `midi_program`; use mapped/default programs only when the source lacks one.
- [x] Restrict high-confidence role inference to explicit source role/part names, channel-10 percussion, and unambiguous instrument identities such as electric/acoustic bass. Do not infer melody, harmony, chords, key, or form from note content. Return `other` and a warning when confidence is insufficient.
- [x] Implement deterministic stable IDs from format, source part/track/channel/program, source event ordinal/onset/pitch, and a bounded digest; sort before allocation and use deterministic suffixes for collisions. Ensure IDs do not depend solely on mutable display names.
- [x] Produce exactly one `unsectioned` section over the compiled bar map unless explicit labels confidently and contiguously cover the score; always leave `harmony` empty.
- [x] Logging: DEBUG source/result counts, timing scale, ID collision counts, and decision codes; INFO canonicalization success with schema/track/event/bar counts; WARNING approximation/omission codes; ERROR bounded exception type/code on failure. Do not log event lists, labels, or composition JSON.
- [x] Files: `backend/app/services/composition_import.py`, `backend/app/services/import_instruments.py`, existing `backend/app/services/instrument_identity.py` only where reusable behavior needs extension, and focused unit tests.
- [x] Depends on Task 1.

#### Task 3: Implement deterministic MIDI parsing
- [x] Create `backend/app/services/composition_midi_import.py` using `mido` to accept SMF format 0/1 with PPQ division, accumulate absolute ticks, collect conductor metadata, split note streams by source track/channel/program segment, and pair notes correctly across explicit note-off and velocity-zero note-on events.
- [x] Preserve notes, polyphony, velocity, source PPQ, tempo, meter, key signatures, track names, channels/programs, markers, CC7 volume, CC10 pan, CC11 expression, and CC64 sustain when representable. Convert changing controls into V2 defaults/automation/pedal spans with sorted and deduplicated points.
- [x] Handle overlapping equal pitches with FIFO active-note queues, same-tick note-off-before-note-on semantics, format-0 multi-channel data, drums on MIDI channel 10, duplicate conductor events, trailing silence, and program changes. Define deterministic warning behavior for unmatched note-offs, dangling note-ons, unsupported SysEx/pitch bend/aftertouch/RPN/NRPN, and out-of-range metadata.
- [x] Reject SMPTE division and structurally malformed/truncated MIDI with stable sanitized errors. Enforce limits during event traversal rather than after allocating an unbounded intermediate representation.
- [x] Logging: DEBUG format/PPQ/source track/channel/event counts and parser timing; INFO successful parse and approximation count; WARNING issue codes/counts; ERROR sanitized parser class and stable error code. Never log raw MIDI or event dumps.
- [x] Files: `backend/app/services/composition_midi_import.py`, `backend/requirements.txt`, `backend/requirements-modern.txt`, and `backend/tests/test_midi_import.py`.
- [x] Depends on Tasks 1-2.

### Phase 2: MusicXML, HTTP, And AI Compatibility

#### Task 4: Implement hardened MusicXML and MXL parsing
- [x] Create `backend/app/services/composition_musicxml_import.py` with a hardened preflight for uncompressed MusicXML and an in-memory safe ZIP/container reader for MXL before calling `music21`. Support score-partwise and normalize/reject score-timewise according to verified `music21` behavior.
- [x] Extract parts, instruments, concert pitches, offsets/durations, chords, voices, staves, ties, supported articulations, dynamics, pedals, tempo, meter, key changes, markers, and repeat-expanded linear playback order. Preserve enharmonic spelling where V2 accepts it and preserve polyphony as overlapping events.
- [x] Compute bounded exact PPQ from source divisions/tuplets using rational arithmetic. Quantize only when the configured PPQ cap is exceeded, report counts/error bounds, prevent zero-duration notes, and reject timing/meter maps that cannot be represented without relocating source events.
- [x] Normalize pickups/partial final measures into the complete-bar V2 timeline and report the change. Omit unsupported semantics such as grace/cue notes, lyrics, slurs, wedges that cannot map to automation, ornaments, microtones, and arbitrary directions using grouped issue counts rather than one unbounded issue per element.
- [x] Logging: DEBUG safe container/parser metrics, part/measure/note counts, PPQ selection, repeat expansion, and conversion duration; INFO parse success; WARNING issue codes/counts; ERROR stable sanitized error code/type. Never log XML, titles, lyrics, direction text, or archive entry contents.
- [x] Files: `backend/app/services/composition_musicxml_import.py`, synchronized backend requirement manifests for XML hardening, and `backend/tests/test_musicxml_import.py`.
- [x] Depends on Tasks 1-2.

#### Task 5: Add bounded multipart import routes and canonical preview response
- [x] Add `backend/app/routers/imports.py` with separate `POST /imports/midi` and `POST /imports/musicxml` endpoints using `UploadFile`. Stream each upload with byte counting, verify content signatures against the selected endpoint, call format-specific and shared services, then render fresh MusicXML from the validated V2 through `music_json_renderer.py`.
- [x] Keep handlers thin and map domain exceptions to the agreed `413`/`415`/`422`/`503`/`500` responses with stable code plus bounded detail. Ensure a notation projection warning is reported separately from import conversion issues.
- [x] Register the router in `backend/app/main.py`, add `/imports` to `frontend/vite.config.js`, and add a production Nginx proxy location with an upload limit slightly above the application limit so structured backend `413` responses remain reachable.
- [x] Verify OpenAPI describes multipart files and strict V2/report output. Avoid CORS custom headers unless the report is moved out of the JSON body.
- [x] Logging: INFO route start/completion with endpoint format, byte count, result counts, status, and issue codes; DEBUG parser/render timing and configured limit; WARNING client rejection code; ERROR unexpected sanitized failure. Never log raw payloads, source names beyond a sanitized extension, or generated MusicXML.
- [x] Files: `backend/app/routers/imports.py`, `backend/app/main.py`, `frontend/vite.config.js`, `frontend/nginx.conf`, `backend/tests/test_import_routes.py`, and `backend/tests/test_openapi_v2.py`.
- [x] Depends on Tasks 3-4.

#### Task 6: Make validation and AI editing source-neutral
- [x] Separate strict canonical/timeline integrity from LLM-generation arrangement policy in `backend/app/services/composition_validator.py`. Fix any root-meter-only checks to use `composition_timeline.py`; keep melody/bass/harmony density requirements on generation only, not raw imports or post-import region edits.
- [x] Update `backend/app/services/llm_composition_editor.py` and `backend/app/services/composition_region_patch.py` so schema-valid imported compositions with `other` roles, one track, percussion, polyphony, tuplets, or variable meter can be selected and edited without requiring a generated ensemble. Preserve all untouched imported event IDs and metadata.
- [x] Confirm render/export/playback paths tolerate `other` and `unsectioned` without inventing parts or harmony. Add regression coverage for AI fake-mode editing of an imported fixture, including an untouched-region fingerprint and successful MIDI/MusicXML export.
- [x] Logging: DEBUG selected validation profile and timeline facts; INFO edit validation success; WARNING advisory musical-policy findings only in generation contexts; ERROR bounded structural failures with track/bar identifiers, never full compositions or prompts.
- [x] Files: `backend/app/services/composition_validator.py`, `backend/app/services/llm_composition_editor.py`, `backend/app/services/composition_region_patch.py`, relevant renderer/export services only if neutral values expose assumptions, and validator/editor regression tests.
- [x] Depends on Tasks 1-5.

### Phase 3: Frontend Import State And UX

#### Task 7: Add multipart API and atomic Zustand import transitions
- [ ] Add `importMidi(file)` and `importMusicXml(file)` to `frontend/src/api/musicApi.js`. Build `FormData` without manually setting the multipart boundary, preserve HTTP status and structured error code/details, normalize and validate returned V2, and retain separate import and notation projection reports.
- [ ] Add dedicated import state/actions in `frontend/src/store/musicStore.js`: `importStatus`, `importError`, `importReport`, `startImport`, `completeImport`, and `failImport`. `completeImport` must atomically validate/install the V2 as both reset baseline and edited state, install canonical rendered MusicXML, rebuild controls, stop/reset playback, clear note/AI selections and undo history, update revisions, and mark an open project dirty only after validation succeeds.
- [ ] Correct project generation metadata semantics so imported compositions persist with `generationMeta: null` and `clear_generation: true`, hydration retains null metadata, prompt edits do not fabricate generation metadata for imported projects, and save fingerprints/autosave remain race-safe. Supersede pending save responses/timers when replacing a composition.
- [ ] Add a `createImportedProject` store flow that parses first, derives a bounded default project name from the local basename, creates the project with canonical V2 and no generation metadata, hydrates it as saved, and exposes the report. A failed parse must not create an empty project.
- [ ] Logging: DEBUG format/file byte count, state transitions, revision/count summaries, and request status; INFO import/project creation success; WARNING structured issue/error codes; ERROR bounded messages. Never log file contents, XML, MIDI bytes, complete filenames, or full compositions.
- [ ] Files: `frontend/src/api/musicApi.js`, `frontend/src/api/musicApi.test.js`, `frontend/src/store/musicStore.js`, `frontend/src/store/musicStore.test.js`, `frontend/src/store/musicStore.project.test.js`, and `frontend/src/utils/projectPersistRevision.js` if null provenance requires revision changes.
- [ ] Depends on Task 5.

#### Task 8: Add accessible select/drop workflows and warning summary
- [ ] Create `frontend/src/components/ImportControls.jsx` with explicit MIDI and MusicXML actions, an accessible hidden/native file input, keyboard-operable drop zone, accurate `accept` values, visible configured-size guidance, duplicate-submit protection, same-file reselection reset, loading/progress copy, and mobile/desktop responsive behavior matching the existing visual language.
- [ ] Add import-as-new-project controls beside `New Project` and in the empty project state in `ProjectBrowser.jsx`. Add import/replace controls to the composer side panel or `ProjectComposerBar.jsx`; require confirmation before replacing a non-empty composition and state that failed conversion leaves current music intact.
- [ ] Restrict drag/drop interception to file payloads over the import target so piano-roll pointer drag/resize and Shift+drag selection are unaffected. Reject mixed/multiple files client-side with clear messages while treating backend validation as authoritative.
- [ ] Render a concise successful summary with detected format, source/result track/note/bar counts, and grouped approximated/defaulted/omitted warnings. Keep import issues visually and semantically separate from generation warnings and export projection warnings. Make empty notation/workspace text source-neutral.
- [ ] Ensure importing remains enabled when no LLM provider is configured. After import, piano roll, playback, notation, AI edit, save, and export controls should appear through canonical state without format-specific branches.
- [ ] Logging: DEBUG UI selection/drop and state status using format/size/counts only; INFO user-confirmed replace and completion; WARNING rejected client selection or report codes; ERROR sanitized API failure. Never log local paths, file content, or complete composition state.
- [ ] Files: `frontend/src/components/ImportControls.jsx`, `frontend/src/components/ProjectBrowser.jsx`, `frontend/src/components/MusicGenerator.jsx`, `frontend/src/components/ProjectComposerBar.jsx`, `frontend/src/components/NotationViewer.jsx`, and existing styled components as needed.
- [ ] Depends on Task 7.

### Phase 4: Fixtures, Fidelity, Security, And Acceptance

#### Task 9: Add deterministic fixtures and backend fidelity tests
- [ ] Add small auditable deterministic fixtures under `backend/tests/fixtures/import/`: a normal type-1 multi-track MIDI with tempo/meter/key changes, program/channel metadata, polyphony, velocity, controls, sustain, and percussion; a corresponding multi-part MusicXML with voices/staves/ties/dynamics/articulations; an MXL container; and malformed/truncated/security fixtures. Store expected semantic vectors in JSON rather than asserting re-exported byte identity.
- [ ] Add fixture builders under `backend/tests/fixtures/` that can regenerate binary fixtures deterministically with pinned `mido`; include a parity/hash assertion so committed bytes and documented builder output cannot drift unnoticed.
- [ ] Add `backend/tests/test_import_fidelity.py` to import each valid fixture twice, assert identical canonical JSON/IDs/report codes, validate V2/timeline, compare expected note/metadata tuples, re-export to MIDI and MusicXML, parse those exports, and compare playable semantics within each documented approximation.
- [ ] Exercise existing persistence services/routes by saving and reopening imported V2 with equal event IDs and note fingerprints; exercise fake-provider region editing and verify untouched regions remain identical.
- [ ] Logging: use `caplog` to assert expected structured success/warning fields and deterministic codes while ensuring fixture sentinel text/bytes never appears. Test DEBUG/INFO/WARNING/ERROR paths without weakening production sanitization.
- [ ] Files: `backend/tests/fixtures/import/*`, `backend/tests/fixtures/build_import_fixtures.py`, `backend/tests/test_import_fidelity.py`, project persistence acceptance tests, and export/editor tests.
- [ ] Depends on Tasks 3-6.

#### Task 10: Add malformed-input, resource-limit, and secret-hygiene gates
- [ ] Add `backend/tests/test_import_security.py` covering forged MIME/extensions, empty files, bad MIDI headers/truncation/SMPTE division, DTD/entities, malformed XML, unsafe/encrypted/nested MXL entries, zip-slip names, excessive ratio/expanded size/entry count, and every configurable track/event/bar/PPQ/metadata/active-note limit.
- [ ] Assert exact HTTP status and stable error code, bounded response detail, upload closure, no temporary-file leakage, and no persistence/state mutation. Include direct-backend tests so protection does not depend on Nginx.
- [ ] Extend `backend/tests/test_secret_hygiene.py` with unique source filename/XML metadata/lyric/byte sentinels and assert none appear in logs or user-facing internal-error details.
- [ ] Logging: verify accepted files log counts/codes only, rejected files log sanitized type/code and no payload, and verbosity remains governed by `LOG_LEVEL`.
- [ ] Files: `backend/tests/test_import_security.py`, `backend/tests/test_secret_hygiene.py`, route/service limit tests, and fixtures from Task 9.
- [ ] Depends on Tasks 4-5 and 9.

#### Task 11: Add frontend, Playwright, and Docker acceptance coverage
- [ ] Extend frontend API/store tests for FormData requests, status/code preservation, atomic success, failure non-mutation, reset baseline, playback/selection/history reset, warning retention, null generation metadata, save/autosave race handling, new-project creation after parse, and reload of imported V2.
- [ ] Add `frontend/e2e/import-user-journey.spec.js` using deterministic fixture files and `setInputFiles`, plus one DataTransfer drop path. Cover the acceptance journey: import normal multi-track MIDI, inspect tracks/notes, play, edit a note, render notation, save, reopen, perform fake-provider AI region editing, and download valid MIDI/MusicXML exports.
- [ ] Add MusicXML/MXL success, no-LLM import availability, current-project replace confirmation, malformed-file failure with unchanged composition fingerprint, and visible grouped warning summary. Use store snapshots and parsed downloads rather than visual timing guesses where possible.
- [ ] Extend `scripts/v2_docker_acceptance.sh` to exercise multipart imports through production Nginx, canonical save/reopen, and re-export; add or update the pytest Docker acceptance wrapper so this gate is runnable consistently.
- [ ] Run focused and full gates: `../.venv/bin/python -m pytest` from `backend/`; `npm test`, `npm run lint`, and `npm run build` from `frontend/`; `npm run test:e2e -- e2e/import-user-journey.spec.js`; and `RUN_DOCKER_ACCEPTANCE=1 ./scripts/v2_docker_acceptance.sh` when Docker is available.
- [ ] Logging: assert browser console and server logs contain format/status/count diagnostics but no payload or local-path sentinels; preserve Playwright traces/screenshots only as existing failure artifacts.
- [ ] Files: frontend API/store tests, `frontend/e2e/import-user-journey.spec.js`, `frontend/e2e/helpers.js`, `scripts/v2_docker_acceptance.sh`, and `backend/tests/test_docker_persistence_acceptance.py`.
- [ ] Depends on Tasks 7-10.

### Phase 5: Documentation Checkpoint

#### Task 12: Document supported import behavior and architecture
- [ ] Use `$aif-docs` for the mandatory documentation checkpoint. Update `README.md` with import workflows, accepted formats, limits, warning semantics, and troubleshooting; update `docs/composition-v2.md` with exact MIDI/MusicXML mappings, neutral values, defaults, quantization/padding, unsupported constructs, stable IDs, and round-trip limits.
- [ ] Update `docs/testing.md` with fixture regeneration, focused/full commands, E2E/Docker import checks, and manual smoke cases. Clarify in `docs/composition-v1.md` that external music imports produce V2 directly and do not enter the legacy V1 parser path.
- [ ] Update `docs/CODEBASE_MAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, and `.ai-factory/ARCHITECTURE.md` for the new import router/services/UI/data flow; correct stale notation-regeneration statements encountered while editing.
- [ ] Document that raw import performs no harmony/form/key analysis, source files are not retained, import reports are session diagnostics, notation is regenerated from canonical events, and later AI editing/analysis is a separate explicit action.
- [ ] Logging: document `LOG_LEVEL`, safe diagnostic fields, import-related environment limits, stable error/issue codes, and the prohibition on logging source MIDI/XML or complete canonical payloads.
- [ ] Files: `README.md`, `docs/composition-v2.md`, `docs/composition-v1.md`, `docs/testing.md`, `docs/CODEBASE_MAP.md`, `AGENTS.md`, `.ai-factory/DESCRIPTION.md`, `.ai-factory/ARCHITECTURE.md`, and `.env.example` verification.
- [ ] Depends on Tasks 1-11.

## Acceptance Gates

- A normal multi-track MIDI imports into strict `composition.v2` with stable track/event IDs and preserved notes, timing, velocity, channels/programs, tempo, meter, key, controls, percussion, and polyphony within documented representational limits.
- The imported composition immediately plays, appears in the piano roll and regenerated notation, accepts note edits and fake/real AI region edits, saves/reopens without semantic or ID drift, and exports valid MIDI and MusicXML.
- A representative MusicXML and MXL import preserves parts, pitched notes, voices/staves, durations, dynamics, instruments, ties/articulations, tempo/meter/key maps, and polyphony where supported, with visible stable warnings for every material approximation or omission.
- Raw imports contain `harmony: []`, use neutral roles/sections when metadata is insufficient, and do not invoke musical analysis.
- Re-importing identical fixture bytes produces identical canonical JSON and report codes.
- Malformed, hostile, unsupported, and over-limit files return clean structured errors, leak no raw payload details, create no project, and do not mutate an open composition.
- Backend tests, frontend unit/lint/build checks, Playwright import journey, and Docker/Nginx acceptance pass.

## Risks And Mitigations

- V2's complete-bar and bar-boundary invariants cannot represent every valid source score. Use exact rational timing, deterministic end padding, explicit pickup normalization, and reject rather than silently relocate non-representable meter changes.
- Adding neutral vocabulary can expose assumptions in validators/renderers/generation. Keep generation constraints unchanged, add cross-surface regression tests, and use neutral values only on import/fallback paths.
- `music21` can be expensive and MusicXML/MXL can be hostile. Apply byte/archive/XML/complexity gates before and during traversal, avoid path extraction, and sanitize all failures.
- Existing project saves fabricate generation metadata when it is absent. Make null generation provenance a first-class persistence state and test hydration/autosave races before exposing replacement import.
- Original MusicXML may contain notation not representable in V2. Never display it as authoritative; regenerate notation from canonical events and expose structured losses.
- Generation-oriented validation may reject valid imported solo or mixed-meter material. Split validation profiles and make timeline compilation the structural source of truth before claiming AI-edit compatibility.
