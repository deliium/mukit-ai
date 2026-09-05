# Composition V1

`composition.v1` is the canonical playable JSON contract returned by `POST /llm/generate-music-json`.

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
- Browser playback schedules canonical events by converting ticks to seconds from `tempo` and `ticks_per_quarter`.
- MIDI-ready mapping preserves track `midi_program`, `channel`, `volume`, `pan`, event `start_tick`, `duration_ticks`, and `velocity`.

## Diagnostics

Use backend `LOG_LEVEL=DEBUG` or browser devtools console when diagnosing schema issues. Logs include schema version, normalization path, track count, event count, duration ticks, validation failures, render decisions, and playback schedule summaries without API keys.
