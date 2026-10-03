# Adaptive runtime continuation

Playback can keep sounding while a symbolic model prepares the next bars. The playable result is a session buffer. The stored graph stays `adaptive.score.v1`. Playback stays `adaptive.playback.runtime.v1`. There is no `composition.v5`, no `adaptive.score.v2`, and no write to `projects.composition_json`.

The playback clock does not call a model. Maintain returns a deterministic fallback immediately. The model is a scheduled task the request does not await.

Opt-in **Continuous mode** (MusicState + virtual bars past `bar_count`) is documented in [continuous-music.md](continuous-music.md).

## Documents

| Schema | Role |
|--------|------|
| `adaptive.runtime.continuation.v1` | Snapshot: window, mode, fallback kind, source, job status, audible flag, memory, warnings, telemetry. No note events. |
| `adaptive.runtime.context.v1` | Long-term memory inside the snapshot: theme ids, one harmony label, repetition count, energy. Not the external musical-context session. |
| `adaptive.runtime.buffer.v1` | Session note buffer in process memory. GET buffer returns this document. It is not embedded in the snapshot and it is not stored in SQLite. |

Restart drops the buffer and the memory.

## Window

Anchor bar `N` is `adaptive.playback.runtime.v1.bar` at arm time (1-based).

| Window | Bars | Setting |
|--------|------|---------|
| Reserved | `N` through `N+4` inclusive | `ADAPTIVE_CONTINUATION_PLAY_BARS` = 5 |
| Target | `N+5` through `N+12` inclusive | `ADAPTIVE_CONTINUATION_GENERATE_BARS` = 8 |

| Setting | Default | Range | Meaning |
|---------|---------|-------|---------|
| `ADAPTIVE_CONTINUATION_PLAY_BARS` | 5 | 1–64 | Reserved length |
| `ADAPTIVE_CONTINUATION_GENERATE_BARS` | 8 | 1–64 | Target length |
| `ADAPTIVE_CONTINUATION_PREFIX_BARS` | 8 | 1–64 | Recent material ending at bar `N` |
| `ADAPTIVE_CONTINUATION_DEADLINE_MS` | 250 | 0–60000 | Wall budget from arm. `0` discards a model that has already finished |
| `ADAPTIVE_CONTINUATION_LATENCY_BUDGET_MS` | 50 | 1–5000 | Test ceiling for the non-awaiting arm |
| `ADAPTIVE_CONTINUATION_INTENSITY_EPSILON` | 0.25 | (0, 1] | A move this large, or larger, marks the job stale |
| `ADAPTIVE_CONTINUATION_MAX_BUFFER_EVENTS` | 512 | 1–4096 | Extra events above this are dropped and warned |

An invalid environment value logs a warning and keeps the default. The names are listed in `.env.example`.

Both windows clip to `1..bar_count`. When the target would start past the last bar, the snapshot is idle, `fallback_kind` is `reuse_loop`, and the warning is `continuation_window_past_end`. No model is scheduled.

The deadline tick is the start tick of bar `N+5`. That tick is not `horizon_end_tick`. The playback horizon stays the end of the current bar.

A local continuation composition is validated with `validate_composition_integrity` (`profile="generation"`). Events before the prefix end are dropped. The first remaining bar is shifted so its start tick lands on the deadline tick. A local bar-1 note for an anchor at bar 1 is stored at the start tick of bar 6.

## Audible and the exploration loop

`audible` is false for `reuse_loop`, for a window past the end, and when the published playback loop is enabled and the target start bar sits outside that loop. The warning is `continuation_outside_loop`. The buffer may still hold notes. The Adaptive tab does not schedule them.

With the exploration loop on bars 1–4, a model result for target bar 6 is `source: model` and `audible: false`. Transport stays `playing`. With that loop disabled, the same placement is audible and the local bar-1 note sits at the start of bar 6.

When `audible` is true, Fill ahead places each remaining buffer event on a private Tone queue, `scheduleContinuationAt`. The callback records that the tick is still ahead of the playhead. It does not call a synth attack. The notes already published by playback, including a looping state, keep sounding. `reuse_loop` and an inaudible model schedule nothing. That queue is not the AI Jam `liveScheduledEventIds` list.

## Maintain

Two maintains while the playback bar, state, revision, and prefix digest stay the same share one `job_id` while the job is `pending` or `applied`. Fill ahead starts the session when no continuation id exists, then maintains. While transport is `playing` and a continuation id exists, the panel maintains about once a second.

## Fallbacks

The order is loop, motif repeat, then fake accompaniment, unless the last two prefix bars have already repeated three or more times. Then the motif repeat comes first. `reuse_loop` adds no notes on top of the published loop. Motif repeat uses `transform_repeat` on a track copy. Accompaniment calls the fake symbolic composer with no prefix composition. The seed is a function of the anchor bar, mode, and state id. The same seed, mode, and anchor bar produce the same accompaniment pitches.

If every kind fails, the snapshot still uses `reuse_loop` with warning `continuation_invalid`. Playback is not stopped.

## Deadlines

A result is discarded when the wall time from arm reaches `ADAPTIVE_CONTINUATION_DEADLINE_MS` (default 250, tests may set 0) or the playhead has reached the target. It is also discarded when the state id, document revision, harmony tail, or prefix digest no longer match, or when intensity has moved by 0.25 or more. Late and stale results increment `late_discard_count`. A model exception sets `continuation_model_failed`, leaves `source` as `fallback`, and does not command playback.

An older job id cannot replace the active job.

## HTTP

All routes use the adaptive-score read permission.

| Method | Path | Result |
|--------|------|--------|
| POST | `/{score_id}/continuation` | Start. Body: `expected_document_revision`, `mode`, and optional `continuous` (latched when `ADAPTIVE_CONTINUOUS_ENABLED`). Playback that is not running returns 200 with `playback_not_running` and does not store a session. |
| GET | `/{score_id}/continuation` | Snapshot, or 204 |
| POST | `/{score_id}/continuation/maintain` | Fallback snapshot. 404 `continuation_not_running` when no session exists. |
| GET | `/{score_id}/continuation/buffer` | Buffer, or 204 |
| DELETE | `/{score_id}/continuation` | Cancel the task and clear the session. 204 |

## Logging

Each start, maintain, apply, discard, and stop logs one INFO line with `adaptive_runtime_continuation: true`, `project_id`, `score_id`, `continuation_id`, `job_id`, `mode`, `anchor_bar`, `target_start_bar`, `fallback_kind`, `source`, `job_status`, `audible`, `warning_codes`, `arm_count`, `model_apply_count`, `late_discard_count`, `failure_count`, and `fallback_count`.

DEBUG may add `deadline_tick`, `repetition_count`, `theme_count`, and `harmony_chord_count`. ERROR logs `code` only.

Logs do not include note events, pitches, `harmony_tail`, prompts, prefix JSON, buffer JSON, `body_json`, or `prefix_digest`.
