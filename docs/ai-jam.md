[← Co-performance](co-performance.md) · [Back to README](../README.md) · [MIDI live input →](midi-live-input.md)

# AI Jam real-time co-composition

Product layer on the shipped [co-performance](co-performance.md) engine: jam modes, live performance features, harmony belief with hysteresis, session-only jam controls, multi-role local generation, and multi-track Commit into editable `composition.v2`.

## Summary

| Concern | Behavior |
|---------|----------|
| Modes | `user_melody` — user lead; AI bass + accompaniment (+ texture at medium+) |
| | `user_chords` — user harmonic material; AI melody + bass + texture |
| Features | Bounded raw `live.performance.features.v1` (pitch activity, beat, probable key/harmony, phrase) — **no nested belief** |
| Belief | Smoothed harmony after confidence / dwell / hysteresis; drives generators + predict `active_harmony` |
| Context cache | Session `liveJamContext` planned window — hot path reads last snapshot only |
| Controls | Complexity, density, style, **instrument set** (per-role track + GM program), responsiveness — session only (never autosaved) |
| Commit | Multi-track role map; optional `ensure_missing_tracks`; optional belief → `harmony[]` metadata |
| Fallback | Predict / AbortController unavailable → local roles + degradation; Transport never silenced solely by AI absence |

## Modes → role partition

```text
user_melody:
  user_roles:     [melody]
  ai_roles:       [bass, accompaniment]
  optional_ai:    [texture]   # complexity ≥ medium

user_chords:
  user_roles:     [harmony]
  ai_roles:       [melody, bass, texture]
```

Harmony **belief** is generator context. Pad/texture **notes** are explicit generator output with `track_role` — never “playback of `harmony[]`”.

## Feature vs belief split

- **Features** (`live.performance.features.v1`) — raw analysis over a **bounded** MIDI ring window (`getRecentEvents`). Never embed post-hysteresis belief.
- **Belief** — session snapshot `{ symbol, confidence, held, reason_code }`. Prefer active V2 harmony span; else inferred with dwell/hysteresis. Passing tones must not flip accompaniment abruptly.

Settings: `LIVE_JAM_*` / `VITE_LIVE_JAM_*` (confidence min, dwell ms, hysteresis). Responsiveness scales dwell within a floor.

## Cached musical context (`liveJamContext`)

Warm-path refresh builds `{ belief_symbol, belief_confidence, planned_window[], phrase_boundary_ticks[], key_belief }`. Generators and `maintainHorizonWithJam` read `getSnapshot()` only. Clear on Cancel; freeze after Stop for optional Commit harmony spans.

## Controls

| Control | Effect |
|---------|--------|
| Complexity | Pattern vocabulary / optional texture role |
| Density | Note rate / subdivision |
| Style | `block` / `arp` / `alberti` / `pad` |
| Instrument set | Role → Commit destination `track_id` + GM program (panel picker; applied on **Ensure missing tracks**) |
| Responsiveness | Scales hysteresis dwell/thresholds within caps |

Controls never override Transport ownership and never appear in revision snapshots except as resulting note events / optional committed harmony spans.

## Multi-track Commit

One undoable `ai-jam-commit` transaction:

1. User stream → user-role destination track
2. AI buffer events grouped by `track_role` → mapped tracks
3. Incomplete map when AI events exist → `jam_commit_map_incomplete` (unless **Ensure missing tracks**)
4. `ensureJamRoleTracks` creates valid V2 tracks (`name`, `instrument`, `role`, `midi_program`, `channel`, `expression`, `sustain_pedals: []`, `events: []`) when opted in
5. Optional **Commit harmony spans** (default **on** for `user_chords`, **off** for `user_melody`) writes `planned_window` into `harmony[]` **metadata only** — never invents playable notes from spans

When `jamMode` is null, single-track co-performance Commit remains available.

Cancel never mutates V2.

## Fallback

| Condition | Behavior |
|-----------|----------|
| Shared playback engine missing | Start fails closed (`live_engine_unavailable`) |
| Predict HTTP / symbolic cold path down | Local roles continue; `jam_predict_unavailable` badge |
| `AbortController` missing | Skip cold predict via `createLivePredictAbortController` (`live_predict_no_abort`) |
| Unparseable harmony | Hold belief; soft pad only as generator events after Commit |

Cold predict: max one in-flight; never awaited inside MIDI/Transport handlers. Use `createLivePredictAbortController` — never bare `new AbortController()` in the store (ESLint `no-undef`).

## Simulated tests

`frontend/src/utils/liveJam.simulatedMidi.test.js` injects Transport-synced melody and chordal fixtures (no hardware MIDI). Asserts belief stability under passing tones, horizon coverage under local jam engine, no V2 mutation until Commit, correct multi-track Commit (+ optional harmony for chords), bounded feature windows, midiPhase exclusion, and no-abort predict fallback.

## Non-goals (v1)

- User MIDI monitor / audition voices while jamming (scheduler `onTrigger` remains stub-quality)
- Per-note remote LLM / Music Transformer on the hot path
- WebSocket MIDI bridge or mandatory WS predict
- Web Worker analysis
- Auto-commit / inventing `composition.v4`
- Wiring reference conditioning or multi-agent spine onto the jam hot path

## Logging

Namespaces: `liveJam` (plus reused `liveMidi` / `liveAccompaniment` / `liveTransport`). INFO: enums, counts, truncated track id prefixes, commit summary `{ userCount, roleCounts, tracksEnsured, harmonySpansCommitted }` — **never** event arrays, MIDI dumps, or full feature histograms at INFO. Gate with `VITE_LOG_LEVEL` / `LOG_LEVEL`.

## See also

- [Co-performance](co-performance.md) — Transport, stream, horizon buffer, degradation, predict
- [MIDI live input](midi-live-input.md) — record-take (mutually exclusive with live jam phases)
- [Composition V2](composition-v2.md) — canonical playable contract
