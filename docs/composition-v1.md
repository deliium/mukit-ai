# Composition V1

`composition.v1` is the canonical playable JSON contract returned by `POST /llm/generate-music-json`.

## Staged Generation

The backend generates compositions through a multi-stage LangGraph composer rather than a single full-score JSON prompt:

1. `plan_form` — tempo, key, meter, contiguous sections, instrumentation intent
2. `plan_harmony` — chord-symbol metadata and cadence goals (not audible notes)
3. `compose_melody` — primary melody track events plus motif/context handoff
4. `compose_bass` — bass events guided by harmony and form
5. `compose_accompaniment` — harmony/accompaniment events and practical optional instruments (for example strings/pad)
6. `assemble_composition` — merge stage drafts into canonical `composition.v1`
7. `validate_composition` — Pydantic schema checks plus deterministic musical integrity checks
8. `repair_composition` — retry the failed stage using structured diagnostics until `options.max_retries` is exhausted

Practical initial LLM generation bounds reject oversized prompts before provider calls: up to **32 bars** and **6 non-drum instruments**. Schema-level `duration_bars` may still allow larger values for non-LLM/manual workflows. Oversized generation requests return HTTP `422` with an actionable reduce-duration/instrumentation message.

Required playable roles are melody, bass, and harmony/accompaniment note events. Harmony metadata alone is never accepted as a substitute for `tracks[].events[]`. Repair exhaustion and other invalid staged output return HTTP `502` with sanitized diagnostic detail.

Useful log fields include `stage`, `provider`, `model`, `attempt`, section/track/event counts, diagnostic codes, and sanitized provider errors. API keys and full freeform prompts are never logged.

## Partial Region Editing

`POST /llm/edit-composition-region` edits an existing canonical Composition V1 document without regenerating the whole score.

Request shape:

- `composition`: current `composition.v1` document
- `edit.instruction`: non-empty natural-language instruction
- `edit.selection`: inclusive `start_bar` / `end_bar`, optional `track_ids`, optional section context
- `edit.allow_harmony_changes` / `edit.allow_added_tracks`: explicit opt-ins (default `false`)
- `selection` / `options`: same provider/model contract as full generation

The edit service uses a focused LangGraph (`analyze_edit_scope` → `draft_region_patch` → `validate_patch` → optional `repair_patch` → `apply_patch`) that asks the provider for a **`replace_region` patch only**, never a full composition. Deterministic helpers in `composition_region_patch.py` apply the patch immutably:

- Replace only in-region events for target tracks
- Preserve outside-region notes and metadata (`tempo`, `key`, `time_signature`, `ticks_per_quarter`, `duration_ticks`, `bar_count`, `sections`)
- Keep `harmony` unchanged unless `allow_harmony_changes` is true
- Allow `added_tracks` (for example counter-melody) only when explicitly permitted and IDs/channels are unique

Failures are non-destructive: invalid JSON, out-of-region events, preserved-region mutations, integrity failures, or repair exhaustion return HTTP `502` and leave the caller's composition unchanged. Successful responses include `composition`, explicit `patch`, `musicxml`, `provider`, `model`, and `warnings`.

Supported edit examples:

- regenerate / activate melody in the selected bars
- simplify accompaniment
- change bass line
- add counter-melody (`allow_added_tracks: true`)
- increase tension in the selected section (still defaults to note-only edits unless harmony scope is expanded)

Frontend workflow: Shift+drag (or start/end controls) on the piano roll to select bars, choose current-track vs all-tracks scope, enter an instruction, then **Regenerate Selection / AI Edit**. Successful edits push one undo snapshot; failures do not mutate `editedMusicJson` or trigger autosave.

Verbose logs stay count-based and sanitized (bar range, track counts, diagnostic codes). Raw prompts, full compositions, and API keys are never logged.

## Contract

- `schema_version`: must be `composition.v1`.
- `tempo`: BPM from `40` to `240`.
- `key`: text such as `C major` or `F# minor`.
- `time_signature`: supported meter such as `4/4`, `3/4`, or `6/8`.
- `ticks_per_quarter`: positive integer timing resolution, default `480`.
- `bar_count`: positive integer total bars.
- `duration_ticks`: total duration from composition start; must match `bar_count * bar_duration_ticks`.
- `sections`: contiguous, non-overlapping boundaries with `start_bar`, `bar_count`, `start_tick`, and `duration_ticks`.
- `tracks`: stable track metadata plus track-local note events.
- `harmony`: chord-symbol and analysis metadata only; it is not used to invent audible notes.

## Track Events

Each playable note is stored inside its track:

```json
{
  "type": "note",
  "pitch": "C4",
  "start_tick": 0,
  "duration_ticks": 480,
  "velocity": 90
}
```

`pitch` must be MIDI-range scientific pitch notation. `start_tick` is measured from composition start, `duration_ticks` must be positive, and `velocity` must be `1` through `127`.

Polyphony uses multiple events with the same `start_tick` or overlapping durations. Rests are implicit empty tick ranges and should not be represented as events.

## Migration

Legacy JSON with top-level `notes` is normalized by converting one-based `bar` and `beat` quarter-unit timing into canonical ticks. Missing legacy velocity defaults to `80` and is logged as a compatibility warning.

Legacy JSON with harmony/tracks but no explicit notes is rejected:

```text
Legacy music JSON has harmony/tracks but no note events; regenerate or add notes before canonical rendering/playback.
```

## Consumers

- Backend generation returns `Composition` in the response `music` field.
- Backend MusicXML rendering consumes canonical track events and inserts rests for empty ranges.
- `POST /export/musicxml` accepts canonical `composition.v1` JSON and returns a downloadable MusicXML attachment (`application/vnd.recordare.musicxml+xml`).
- `POST /export/musicxml/preview` accepts the same JSON and returns MusicXML text for notation refresh **without** a `Content-Disposition` download header.
- `POST /export/midi` accepts the same JSON and returns a Standard MIDI File attachment (`audio/midi`) built with `mido`.
- `POST /export/wav` accepts the same JSON and returns a WAV attachment (`audio/wav`) synthesized by FluidSynth from `render_midi()` bytes. Notes, programs, channels, tempo, and duration come from MIDI; Tone.js remains browser preview only. Empty compositions yield silent WAV matching `duration_ticks`. Short FluidSynth output is padded. Missing FluidSynth/SoundFont → `503`; synthesis failures → `500`.
- Env vars: `FLUIDSYNTH_BIN`, `COMPOSITION_WAV_SOUNDFONT` (Docker default `/usr/share/sounds/sf2/FluidR3_GM.sf2` from Debian `fluid-soundfont-gm` / FluidR3_GM), `COMPOSITION_WAV_SAMPLE_RATE`, `COMPOSITION_WAV_GAIN`, `COMPOSITION_WAV_TIMEOUT_SECONDS`.
- SoundFont package: Debian/Ubuntu `fluid-soundfont-gm` (Fluid R3 GM). License/attribution: see `/usr/share/doc/fluid-soundfont-gm/copyright` in the image (MIT-style Fluid R3 license; do not bundle the `.sf2` in this repo).
- Browser playback schedules exact `tracks[].events[]` note tuples through a multi-track Tone.js engine (`frontend/src/utils/tonePlaybackEngine.js`), converting ticks to seconds from `tempo` and `ticks_per_quarter`.
- Playback preserves track identity, pitch, start tick/time, duration, velocity, and polyphonic simultaneity. It never invents substitute notes from `harmony`.
- Per-track mute, solo, and volume controls change routing gain only; they do not mutate the canonical composition JSON.
- Unsupported instruments use an explicit fallback synth strategy (logged with track ID, instrument, role, program) rather than silently rewriting musical content.
- The piano-roll editor (`frontend/src/components/PianoRollEditor.jsx`) edits the same Zustand `editedMusicJson` as the JSON editor: create/move/transpose/resize/delete notes on `tracks[].events[]` with snap (`1/4`, `1/8`, `1/16`), zoom, track focus, context tracks, and bounded undo/redo for note edits only.
- AI region editing selects inclusive bars (Shift+drag or controls), defaults target tracks to the focused piano-roll track (or all tracks), and calls `POST /llm/edit-composition-region` via `AiRegionEditPanel`.
- After valid piano-roll note edits or successful AI region edits, the frontend debounces/refreshes `POST /export/musicxml/preview` and updates `musicXml` so OSMD notation stays in sync without forcing a download.
- Frontend Export MusicXML / Export MIDI / Export WAV actions download from the export endpoints using the current edited JSON; MusicXML export also refreshes notation. WAV is export-only (not preview playback).
- Piano roll, JSON editor, MIDI/MusicXML/WAV export, playback, and notation all consume the same `tracks[].events[]`; harmony remains metadata only and never invents export or audible notes.

## Diagnostics

Use backend `LOG_LEVEL=DEBUG` or browser devtools console when diagnosing schema, export, playback, or piano-roll issues. Useful sanitized frontend fields include:

- playback path (`canonical` vs `legacy`)
- tempo, `ticks_per_quarter`, track/event counts
- scheduled event counts and transport transitions (`play` / `pause` / `resume` / `stop`)
- instrument strategy / unsupported fallback warnings
- mute/solo/volume effective gain per track
- composition revision changes that stop active playback
- piano-roll track/note selection, snap/zoom, create/update/delete/undo/redo summaries (pitch, start tick, duration)
- playback cursor tick/px (throttled) and MusicXML preview debounce schedule/cancel/complete
- MusicXML preview request schema version, event count, and MusicXML length

Backend preview-render logs include format `musicxml_preview`, track/event counts, duration ticks, and sanitized render failures without dumping full compositions.

Logs include schema version, normalization path, track count, event count, duration ticks, export format, byte length, validation failures, render decisions, and playback schedule summaries without API keys, prompts, or raw MusicXML/MIDI payloads.

## Project persistence

Saved projects store canonical `composition.v1` JSON in SQLite. Opening a project re-runs normalization so older/legacy payloads upgrade before the client receives them. See [project-persistence.md](./project-persistence.md).
