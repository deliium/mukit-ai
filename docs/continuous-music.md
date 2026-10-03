# Continuous generative music

Opt-in **Continuous mode** upgrades adaptive runtime continuation so a session may generate past stored `composition.v2` `bar_count` into a process-memory buffer while playback never waits on inference.

Playable score stays **`composition.v2`**. There is no `composition.v5` and no `adaptive.score.v2`. Continuous mode never writes `tracks[].events[]` on the project row. Restart drops the buffer and MusicState.

See also: [adaptive-runtime-continuation.md](adaptive-runtime-continuation.md), [adaptive-music-engine.md](adaptive-music-engine.md), [v5-platform.md](v5-platform.md).

## Documents

| Schema | Role |
|--------|------|
| `adaptive.runtime.music_state.v1` | Compressed long-term MusicState sibling on the continuation snapshot: theme ids, motif usage digests, harmonic trajectory labels, repetition history, energy/tension rings, orchestration digests, `runtime_state_id`, `virtual_bar`, guard flags. No note events. No pitch lists. |
| `adaptive.runtime.context.v1` | Legacy thin memory projected from MusicState for one-release compatibility. Not nested MusicState. |
| `adaptive.runtime.continuation.v1` | Snapshot gains optional sibling `music_state` and boolean `continuous` (latched on start). |
| `adaptive.runtime.buffer.v1` | Session notes only. Not SQLite. |

## Flags

| Env | Default | Meaning |
|-----|---------|---------|
| `ADAPTIVE_CONTINUOUS_ENABLED` | off | Studio continuous / virtual timeline |
| `ADAPTIVE_ENGINE_CONTINUOUS_ENABLED` | off | Engine proxy `POST /adaptive/session/{id}/continuous/maintain` (singular `session`) |

Neither flag appears on `/ready`. `continuous=true` while studio flag off → `adaptive_continuous_disabled`. Engine continuous while engine flag off → `adaptive_engine_continuous_disabled`.

## Virtual bar

`playback.bar` never exceeds composition `bar_count`. When continuous is latched and the legacy planner would idle (`target_start > bar_count`) or playback is at end / looping at end, the anchor is `music_state.virtual_bar` (rolling index that may exceed `bar_count`). Buffer ticks past authored duration assume constant meter/tempo (`continuation_meter_assumed_constant` / `continuation_virtual_timeline` warnings).

## Pipeline

1. Publish deterministic fallback first (loop / motif / accompaniment; repetition may reorder).
2. Schedule optional symbolic model; maintain does not await it.
3. Soft/hard guards (repetition pressure, theme drift, harmonic dead-end, duplicate digest) may reorder fallbacks or refuse a model apply — never silence transport.
4. Late / stale model results are discarded; transport stays `playing`.

## SPA audition

When the buffer is `audible`, Fill ahead schedules virtual ticks via constant-tempo past-`durationTicks` mapping (`tickToSeconds` / Tone `secondsBetweenTicks`). That queue is not AI Jam `liveScheduledEventIds`. Opening the Adaptive tab never starts continuous generation; the Continuous toggle only latches on the next Fill-ahead start.

## Engine / clients

Studio routes remain primary. With both flags on, the Adaptive Engine exposes continuous maintain; Python `mukit_adaptive` and TypeScript `@mukit/adaptive-music` add thin `maintainContinuous` / `getContinuousBuffer` methods that refuse clearly when disabled.

## Honesty

- Session buffer ≠ durable score.
- MusicState is lossy by design (fixed ring caps).
- Heuristic guards are not taste models.
- `ai_agents/` must not import continuous / MusicState / continuation modules.
