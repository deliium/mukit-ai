[← Browser Playback](browser-playback.md) · [Back to README](../README.md) · [Composition Editor →](composition-editor.md)

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

| Environment | Notes |
|-------------|--------|
| Chrome / Edge / Opera | Web MIDI on secure contexts (HTTPS or localhost) |
| Firefox | May require a flag or remain unsupported — UI offers Test input |
| Safari | Often unsupported — Test input still works |
| Headless CI / insecure HTTP | Probe returns `unsupported` / `insecure_context`; no crash |

Reason codes: `available`, `unsupported`, `insecure_context`, `permission_denied`.

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

- `requestMIDIAccess({ sysex: false })` — no SysEx.
- Logs use namespaces `midiInput` / `midiCapture` with counts, phase, and truncated device ids only — never full MIDI dumps or composition JSON at INFO.
- Optional `localStorage` key `midiInput:v1` stores last device id preference only (non-secret).

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
- [MIDI / MusicXML import](import.md) — file ingest (separate path)
- [Testing](testing.md) — unit / e2e gates
