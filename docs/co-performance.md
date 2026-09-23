# Co-performance real-time engine

Low-latency infrastructure for AI-assisted **live co-performance** while keeping deterministic editor playback and persistence intact.

## Summary

- **Shared Tone.js Transport** — attaches to the `PlaybackControls` `createPlaybackEngine` instance (never a second Transport owner).
- **Beat/bar clock** — derived from Transport seconds + composition timeline (`liveClock.js`).
- **Incoming MIDI stream** — Transport-synced session ring (`liveMidiStream.js`); mutually exclusive with MIDI record-take (`midiPhase`).
- **Active harmony** — read-only lookup of `composition.v2.harmony[]` at playhead (`liveHarmonyContext.js`); never invents playable notes.
- **Prediction horizon** — local pattern engine keeps accompaniment scheduled ahead; optional HTTP `POST /live/accompaniment/predict` fills asynchronously.
- **Degradation** — when AI/local coverage underruns, continue/arp/hold harmonic pattern; **never** stop Transport solely because AI is late.
- **Commit** — explicit user action writes selected stream/accompaniment into one V2 transaction (`co-performance-commit`); Cancel never mutates V2.

## Ephemeral vs canonical

| Layer | Lifecycle |
|-------|-----------|
| Live MIDI stream + accompaniment buffer + live Transport IDs | Session only |
| Explicit Commit | `tracks[].events[]` |
| Autosave / revisions | Never see live-only layers |

## Lifecycle

`idle` → `running` (optional `degraded`) → `stop` | `cancel`

- **Stop** — clear future live schedule IDs; keep last stream snapshot for Commit; V2 untouched.
- **Cancel** — discard stream + buffer; abort in-flight predict; V2 untouched.
- **Source invalidate / audition switch** — cancels co-performance automatically.
- **MIDI record arm/start** while live running (and vice versa) → rejected (`live_midi_phase_exclusion`).

## Communication evaluation (v1)

| Path | Role | v1 verdict |
|------|------|------------|
| Local / fake pattern | Hot path horizon fill | **Required** — meets coverage under fake Transport tests |
| HTTP `POST /live/accompaniment/predict` | Cold-path prefetch (fake mode) | **Optional**; max one in-flight; never awaited in MIDI/Transport callbacks |
| WebSocket chunk stream | Lower TTFT if HTTP p95 > `horizon_ms - schedule_slack_ms` | **Deferred** — not enabled; HTTP + local sufficient in measured harness |
| Web Worker analysis | Protect main thread | **Deferred** — no measured jank requirement for v1 AC |

See `frontend/src/utils/liveAccompanimentBuffer.perf.test.js` for the recorded table and assertions.

## Latency marks

FE `liveLatency.js` aggregates `midi_input`, `analysis`, `generation`, `scheduling` (ms only). DEBUG mark pairs; INFO summary ≥ 1s. Never log MIDI dumps or event arrays.

Log namespaces: `liveTransport`, `liveMidi`, `liveAccompaniment` (gated by `VITE_LOG_LEVEL`). Backend predict uses `LOG_LEVEL`.

## API

`POST /live/accompaniment/predict` — body `live.accompaniment.predict.request.v1` (bounded features; **forbidden**: composition / events / MIDI dumps). Response `live.accompaniment.chunk.v1`. Domain errors via `LivePerformanceError` → HTTP mapper.

Settings: `LIVE_*` / `VITE_LIVE_*` (see `.env.example`).

## Non-goals (v1)

- Backend MIDI device bridge / mandatory WebSocket
- Web Worker analysis
- Auto-commit of live layers
- `reference.conditioning.policy.v1` on predict (offline generate/develop/edit only)
- Inventing `composition.v4` or notes from harmony alone

## See also

- [Browser playback](browser-playback.md)
- [MIDI live input](midi-live-input.md)
- [Multi-agent (V4)](multi-agent.md)
