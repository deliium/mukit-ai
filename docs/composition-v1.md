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
