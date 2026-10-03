[← Performance conductor](performance-conductor.md) · [Back to README](../README.md) · [Composition Editor →](composition-editor.md)

# MIDI Live Input (Web MIDI Performance Capture)

Browser-only performance capture into canonical `composition.v2`. Play a MIDI keyboard (or QWERTY test input), optionally with count-in and metronome, then commit one undoable take onto a destination track. This is **not** file MIDI import (`POST /imports/midi`) and **not** audio melody transcription ([audio-transcription.md](audio-transcription.md)).

## Summary

| Concern | Behavior |
|---------|----------|
| Permission | Lazy on **Enable MIDI** (user gesture); never at app startup |
| Score write | One `commitCompositionTransaction` (`action: 'midi-record'`) on Stop |
| Timing | Raw ticks until optional post-record quantize |
| Metronome / count-in | Ephemeral Tone.js clicks — never written into V2 |
| Loop overdub | **Deferred** — existing loops are audition/transport only |
| Persistence | Session MIDI state is never autosaved or stored in revisions |

## Support matrix

| Environment | Web MIDI 1.0 (`requestMIDIAccess`) | UMP / MIDI 2.0 Web API | MPE-over-MIDI1 | Notes |
|-------------|------------------------------------|------------------------|----------------|--------|
| Chrome / Edge / Opera | Yes on secure contexts (HTTPS or localhost) | **Absent** as of 2026-10 — `MIDIMessageEvent.data` is MIDI 1.0 bytes only | Yes via user **MPE mapping** toggle (default **off**) | Default production path |
| Firefox | Yes from 108+; first grant may require Site Permission Add-on | **Absent** | Same MPE toggle | Treat missing API / deny as `unsupported` / `permission_denied` |
| Safari | Native API often missing; extension-only if present | **Absent** | Same when Web MIDI works | **Test input** (QWERTY) always available |
| Headless CI / insecure HTTP | Probe → `unsupported` / `insecure_context` | Stubbed `ump_api_absent` | Simulated multi-channel streams only | No hardware required |

Legacy Web MIDI reason codes: `available`, `unsupported`, `insecure_context`, `permission_denied`.

### Expressive MIDI locks (ship contract)

Frozen for V5 expressive performance input. Implementation must not invent alternate math or a full UMP/WASM stack.

| Lock | Value |
|------|--------|
| Default transport | `midi1_bytes` whenever Web MIDI is available |
| UMP transport | `ump_experimental` **only** when the capability probe finds a real UMP/MIDI 2.0 entry point; otherwise `ump_api_absent` and stay on MIDI 1.0 bytes |
| MPE mapping | Optional user toggle; default **off** (legacy channel-voice note keys); when on, default zone = master ch **1** (0-based 0), members **2–16** (0-based 1–15) |
| `VITE_MIDI_EXPRESSIVE_ENABLED` | Default `true`; falsy forces legacy note-on/off + CC64-only parse/commit |
| Curve `tick_offset` | **Relative to** the referenced note’s `start_tick` (absolute = `event.start_tick + tick_offset`) |
| Playable source | Always `tracks[].events[]`; optional `note_performances[]` never invents pitches |

#### Velocity promote / degrade

| Path | Rule |
|------|------|
| MIDI 1.0 → `velocity_u16` | `velocity_u16 = clamp(midi7, 0, 127) << 9` (max promoted **65024**, not 65535) |
| Commit degrade → V2 `velocity` | For note-on attacks: `midi7 = clamp(round(velocity_u16 / 512), 1, 127)`; `velocity_u16 == 0` stays note-off |
| UMP / true 16-bit → `velocity_u16` | Pass-through: `velocity_u16 = clamp(ump_velocity_u16, 0, 65535)` (do **not** apply `<< 9`); degrade still uses `/ 512` |

#### UMP ship-subset packet kinds (closed list)

Pure JS decode only — **no** vendored UMP/WASM stack. CI uses simulated 32-bit word packets; production browsers typically never activate this path until a probeable API exists.

| Kind id | UMP message type | Opcode (MIDI 2.0 Channel Voice) | Emits |
|---------|------------------|-------------------------------|--------|
| `ump_midi2_note_on` | `0x4` | `0x9` | `note_on` + 16-bit velocity |
| `ump_midi2_note_off` | `0x4` | `0x8` | `note_off` |
| `ump_midi2_poly_pressure` | `0x4` | `0xA` | `pressure` (poly) |
| `ump_midi2_control_change` | `0x4` | `0xB` | `control_change` (incl. CC64 → sustain) |
| `ump_midi2_channel_pressure` | `0x4` | `0xD` | `pressure` (channel) |
| `ump_midi2_pitch_bend` | `0x4` | `0xE` | `pitch_bend` |

All other UMP message types/opcodes → `ignored` with a stable reason (never throw).

#### Simulated-only in CI

| Scenario | How CI covers it |
|----------|------------------|
| Ordinary MIDI 1.0 melody + CC64 | Injected 3-byte streams / fake `requestMIDIAccess` |
| MPE-like per-note bend/pressure | Simulated multi-channel MIDI 1.0 with MPE toggle on |
| High-res / UMP note-on | Simulated UMP word packets through the ship-subset decoder (no `navigator` UMP API) |
| UMP API absent | Default probe: `transport=midi1_bytes`, `high_res_velocity=false`, reason `ump_api_absent` |
| Expressive disabled | `VITE_MIDI_EXPRESSIVE_ENABLED` falsy → legacy kinds only |

Machine-readable echo: `frontend/src/utils/midiExpressive/capability.fixture.json`.

## Record flow

1. Open a composition with at least one track.
2. **Enable MIDI** (optional if using Test input) → select device.
3. Choose destination track (defaults to the piano-roll track).
4. Optional: count-in 0–2 bars, metronome, quantize-after.
5. **Record** → play → **Stop & commit**.
6. Undo removes the whole take; quantize-after (if on) is a second undo step.

Mid-take disconnect stops capture, keeps a **partial** take for Commit or Discard, and clears active-note highlights. Reconnect refreshes the device list and does **not** auto-resume recording.

### Timeline extension

Notes past the current `duration_ticks` extend the composition by complete bars under the meter active at the end (last section grows). Extension never invents pitches from `harmony`.

### Sustain

CC64 (sustain pedal) maps to `tracks[].sustain_pedals[]` when spans are valid and non-overlapping. Invalid/overlapping pedals are skipped with warnings.

### Quantize after recording

When enabled, after a successful take commit the store runs `quantizeNotes` on the new note refs only (snap = piano-roll snap, strength 100). Toggle off to keep raw timing.

## Test keyboard (QWERTY)

Two octaves via `KeyboardEvent.code` (locale-stable):

| Region | Keys |
|--------|------|
| C3–B3 whites | `Z X C V B N M` |
| C3–B3 blacks | `S D G H J` |
| C4–B4 whites | `Q W E R T Y U` |
| C4–B4 blacks | `2 3 5 6 7` |

Disabled while focus is in inputs / textarea / contenteditable (same guard as piano-roll shortcuts). Labelled **Test input** in the MIDI panel.

## Privacy

- `requestMIDIAccess({ sysex: false })` — no SysEx (MPE zone is user toggle / default, never SysEx device config).
- Logs use namespaces `midiInput` / `midiCapture` / `midiExpressive` with counts, phase, transport, and truncated device ids only — never full MIDI/UMP dumps, full takes, or composition JSON at INFO.
- Optional `localStorage` key `midiInput:v1` stores last device id and **MPE mapping** preference only (non-secret; MPE default **off**).

## Expressive commit / export

- Optional `tracks[].note_performances[]` references committed `event_id` values; playable pitches stay in `events[]` only.
- Curve `tick_offset` values are relative to the note’s `start_tick`.
- SMF export ignores `note_performances` and emits projection issue `performance_expression_omitted` when any rows are present.
- Strip metadata anytime — remaining notes stay valid `composition.v2` with MIDI 1.0 `velocity` 1–127.
- The [performance conductor](performance-conductor.md) is a separate sidecar that reinterprets notes for audition; ship-1 ignores `note_performances[]` and does not replace capture.

## Manual acceptance checklist

1. Cold start with no MIDI hardware — generator, editor, and playback work.
2. Enable → select device or Test input → count-in 1 → metronome on → record a melody → stop → notes appear with velocities.
3. Optional quantize-after → undo twice (quantize, then take) or once if quantize off.
4. Sustain pedal produces `sustain_pedals` and playback reflects sustain.
5. Unplug mid-note → warning + partial commit/discard; no stuck highlights.
6. Develop / Motifs / Save / Export MIDI/MusicXML succeed on recorded notes.
7. Loop overdub is not offered.

## Logging

Frontend: `VITE_LOG_LEVEL` gates `midiInput` / `midiCapture` via `appLogger`. See [testing.md](testing.md) for running colocated unit tests (`midiInput*.test.js`, `midiPerformanceCapture.test.js`, `midiTakeApply.test.js`, `musicStore.midiInput.test.js`).

## See also

- [Composition Editor](composition-editor.md) — piano-roll edit after capture
- [Browser Playback](browser-playback.md) — Tone.js audition of committed notes
- [Performance conductor](performance-conductor.md) — durable expressive reinterpretation (orthogonal to capture)
- [Co-performance](co-performance.md) — live stream + accompaniment (exclusive with record-take)
- [AI Jam](ai-jam.md) — jam modes / multi-track Commit on co-performance
- [MIDI / MusicXML import](import.md) — file ingest (separate path)
- [Testing](testing.md) — unit / e2e gates
