[← Composition V2](composition-v2.md) · [Back to README](../README.md) · [Composition V1 →](composition-v1.md)

# MIDI and MusicXML Import

Secure multipart ingestion converts uploaded MIDI or MusicXML into strict `composition.v2`, installs the same canonical state used by playback, piano roll, notation, persistence, export, and AI region editing, and returns a session-scoped import report.

Source files are **ingress only**. After conversion, `tracks[].events[]` is the sole playable source. Raw import performs **no** harmony, form, key, or tonal analysis. Source bytes are not retained on the composition or in project storage.

## Endpoints

| Method | Path | Accepts (content-detected) |
|--------|------|----------------------------|
| `POST` | `/imports/midi` | SMF Type 0/1 with PPQ division (`.mid` / `.midi`) |
| `POST` | `/imports/musicxml` | Uncompressed MusicXML (`.musicxml` / `.xml`) or compressed MXL (`.mxl`) |

Filename and MIME type are not authoritative; content signatures are checked against the selected endpoint.

**Response body:**

```json
{
  "composition": { "schema_version": "composition.v2", "...": "..." },
  "musicxml": "<!-- regenerated from V2; never the uploaded source -->",
  "import_report": { "status": "approximated", "issues": [], "summary": {} },
  "notation_report": { "status": "exact", "issues": [] }
}
```

- `composition` — validated V2 (`harmony: []`)
- `musicxml` — fresh render from that V2 via `music_json_renderer` (never untrusted source XML)
- `import_report` — conversion diagnostics for the current session only (not persisted as generation metadata)
- `notation_report` — separate projection issues from regenerating notation (distinct from import codes)

Import works without an LLM provider. Only later AI edit requires one.

## Frontend workflows

| Entry | Behavior |
|-------|----------|
| Project browser / empty state | Import creates a project **only after** conversion succeeds |
| Open project (composer) | Replace confirmation required; success atomically replaces composition, clears `generationMeta`, supersedes stale autosaves, schedules save of imported V2 |
| Failure | Current project/composition is left untouched |

Session UI shows grouped import warnings (defaulted / normalized / quantized / omitted). Those warnings are not written into the canonical document.

## Neutral vocabulary and defaults

| When source lacks confident metadata | Canonical value | Issue code |
|--------------------------------------|-----------------|------------|
| Track role | `role: "other"` | `role_defaulted` (or `role_inferred` when high-confidence) |
| Form / sections | one full-score `type: "unsectioned"` section | `section_defaulted` |
| Tempo | `120` BPM | `tempo_defaulted` |
| Meter | `4/4` | `meter_defaulted` |
| Key | `C major` (compatibility default — **not** inferred analysis) | `key_defaulted` |

High-confidence role inference is limited to explicit part/role names, MIDI channel 10 percussion, and unambiguous instrument identities (for example electric/acoustic bass). Melody/harmony/form are never inferred from note content.

Generated compositions continue to use existing roles/section types; import does not silently remap them.

## Timing and structure

- **MIDI PPQ:** preserved when valid and ≤ `IMPORT_MAX_PPQ`; otherwise rescaled with `ppq_rescaled` / quantization reports.
- **MusicXML PPQ:** smallest bounded PPQ that exactly represents divisions/tuplets; if the cap prevents exact representation, quantize to nearest ticks (`timing_quantized`), keep positive durations and ordering, report counts/error bounds.
- **Complete bars:** absolute source note timing is preserved; canonical end is padded to the next complete bar (`partial_measure_padded`). Pickups/partial opening measures are normalized (`pickup_normalized`) without inventing notes.
- **Non-representable meter maps** that would relocate source meter changes or notes are rejected (`422` / `import_non_representable`).

## MIDI mapping (preserved when representable)

Notes, polyphony, velocity, source PPQ, tempo/meter/key, track names, channels/programs, markers, CC7 volume, CC10 pan, CC11 expression, CC64 sustain. Program changes may split tracks (`program_change_split_track`). Pitch spelling uses key-aware deterministic spelling when a source key exists, otherwise fixed sharps (`pitch_spelling_inferred`).

**Omitted / warned:** SysEx, pitch bend, aftertouch, RPN/NRPN, unmatched note-offs, dangling note-ons (deterministic close/drop policy), SMPTE division (rejected).

## MusicXML / MXL mapping

**Preserved when supported:** parts, instruments, concert pitches, offsets/durations, chords, voices, staves, ties, articulations, dynamics, pedals, tempo/meter/key, markers, repeat-expanded linear order (`repeat_expanded`), enharmonic spelling where V2 accepts it.

Transposing instruments normalize to concert pitch for playback (`transposition_normalized`) while preserving source spelling where possible.

**Omitted with issue codes:** grace/cue notes, lyrics, slurs, unmappable wedges, ornaments, microtones, arbitrary directions, unsafe/inexpressible constructs (`grace_note_omitted`, `unsupported_notation_omitted`).

MXL is read in memory only: reject encrypted/nested/unsafe ZIP entries, validate `META-INF/container.xml` and exactly one score root. XML DTD/entity declarations and external resources are rejected before `music21`.

## Stable IDs

Track and event IDs are derived from format, source part/track/channel/program, event ordinal/onset/pitch, and a short digest — not mutable display names. Collisions get deterministic suffixes (`source_id_collision`). Identical re-imports of the same bytes produce identical canonical JSON and report codes.

## Import report contract

| Field | Values |
|-------|--------|
| `status` | `exact`, `approximated`, `partial` |
| Issue `severity` | `info`, `warning` |
| Issue `action` | `defaulted`, `normalized`, `quantized`, `omitted` |

Each issue has a stable `code`, bounded `message`, `count`, and optional sanitized locator (track/part/measure/channel/tick). Never includes source bytes, XML excerpts, lyrics, absolute paths, or raw parser errors.

Endpoint failures use structured HTTP errors — not a successful report with a `failed` status.

### Stable issue codes

`tempo_defaulted`, `tempo_rounded`, `meter_defaulted`, `key_defaulted`, `pitch_spelling_inferred`, `partial_measure_padded`, `pickup_normalized`, `timing_quantized`, `ppq_rescaled`, `program_change_split_track`, `instrument_defaulted`, `role_inferred`, `role_defaulted`, `section_defaulted`, `repeat_expanded`, `transposition_normalized`, `unsupported_midi_event_omitted`, `unsupported_notation_omitted`, `grace_note_omitted`, `dangling_note_omitted`, `source_id_collision`.

### HTTP error mapping

| Status | Code examples | Meaning |
|--------|---------------|---------|
| `413` | `import_payload_too_large` | Upload or expanded archive over limit |
| `415` | `import_unsupported_media_type` | Signature mismatch for endpoint |
| `422` | `import_malformed_source`, `import_non_representable`, `import_complexity_exceeded` | Malformed, non-representable, or over complexity |
| `503` | `import_dependency_unavailable` | Parser dependency missing |
| `500` | `import_internal_error` | Unexpected sanitized failure |

## Limits (environment)

Independent from LLM generation’s 32-bar / 6-instrument budget. Enforced in backend code even when requests bypass Nginx. Defaults support normal multi-track files:

| Variable | Default |
|----------|---------|
| `IMPORT_MAX_UPLOAD_BYTES` | 5 MiB |
| `IMPORT_MAX_EXPANDED_BYTES` | 25 MiB |
| `IMPORT_MAX_COMPRESSION_RATIO` | 50 |
| `IMPORT_MAX_ARCHIVE_ENTRIES` | 64 |
| `IMPORT_MAX_TRACKS` | 64 |
| `IMPORT_MAX_NOTES` | 100000 |
| `IMPORT_MAX_BARS` | 512 |
| `IMPORT_MAX_PPQ` | 1920 |
| `IMPORT_MAX_METADATA_CHANGES` | 512 |
| `IMPORT_MAX_ACTIVE_NOTES` | 512 |
| `IMPORT_DEFAULT_TARGET_PPQ` | 480 (optional override) |

Nginx upload limit sits slightly above the app limit so structured `413` responses remain reachable.

## Validation and AI edit after import

Canonical/timeline integrity applies to imports. Generation-only ensemble density (melody/bass/harmony requirements) does **not**. Schema-valid imports with `other` roles, one track, percussion, polyphony, tuplets, or variable meter can be region-edited; untouched event IDs and metadata are preserved. AI analysis/edit is a separate explicit user action.

## Logging

Control verbosity with `LOG_LEVEL`. Safe fields: endpoint format, byte counts, track/note/bar counts, status, issue/error codes, parser/render timing, configured limits.

**Never log:** source MIDI/XML bytes, archive entry contents, lyrics/titles/direction text, complete filenames beyond a sanitized extension, full compositions, or regenerated MusicXML payloads.

## Round-trip limits

Import → edit → export is fidelity within documented approximations. Re-exported MIDI/MusicXML will not byte-match the upload. Export projection codes (`X-Mukit-Projection-*`) are separate from import issue codes. Notation shown after import is always regenerated from canonical events.

## Implementation map

| Layer | Location |
|-------|----------|
| DTOs / codes | `backend/app/import_schemas.py` |
| Limits / policy | `backend/app/import_settings.py` |
| Shared canonicalize | `backend/app/services/composition_import.py` |
| GM / roles | `backend/app/services/import_instruments.py` |
| MIDI parse | `backend/app/services/composition_midi_import.py` |
| MusicXML/MXL | `backend/app/services/composition_musicxml_import.py` |
| HTTP | `backend/app/routers/imports.py` |
| Client API | `frontend/src/api/musicApi.js` (`importMidi`, `importMusicXml`) |
| Store | `frontend/src/store/musicStore.js` (`completeImport`, …) |
| UI | `frontend/src/components/ImportControls.jsx` |

## See Also

- [Composition V2](composition-v2.md) — canonical contract and export fidelity
- [Testing](testing.md) — import fixtures, fidelity/security tests, E2E
- [Project persistence](project-persistence.md) — save/reopen after import
