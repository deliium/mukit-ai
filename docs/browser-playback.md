[← Composition Editor](composition-editor.md) · [Back to README](../README.md) · [Testing →](testing.md)

# Browser Playback

Browser playback is a **session-only projection** of canonical `composition.v2`. It never invents notes from `harmony`, never mutates export paths, and never persists mixer or instrument-profile choices into projects, undo history, or revision snapshots.

Server WAV export continues to use FluidSynth + FluidR3_GM. Browser timbre is intentionally different from that offline render.

## Technology

| Concern | Choice |
|---------|--------|
| Transport / scheduling | Tone.js `Transport` absolute seconds |
| Voices | Tone synth adapters + optional `Tone.Sampler` for audited local packs |
| Graph | Per-track expression → canonical volume → session trim/mute → pan → master/limiter; post-fader reverb send |
| Why not smplr | Keeps note-id polyphony, owned Transport IDs, and deterministic fake-Tone tests on the existing seconds schedule |

## Playable source

Only `tracks[].events[]` are scheduled. Controllers, sustain, dynamics, and tempo maps reshape *how* those events sound/time, not *which* pitches exist.

Mutual-exclusive audition sources (`working` / development / arrangement / version / AI preview) share one transport owner. Switching source or audible revision stops playback.

## Instrument mapping and fallback

1. Resolve a browser profile from instrument name → GM program → role.
2. Prefer an installed local sample pack profile when the audited manifest allows it.
3. On missing pack, load failure, or decode error, fall back to a Tone synth preset with a stable `reasonCode`.
4. Canonical `instrument` / `midi_program` fields are never rewritten by the browser mixer.

Dev fixtures live under `frontend/public/audio/dev-tone-fixtures-v1/` (project-owned synthetic WAVs). A production `core-acoustic-v1` pack must ship with exact license text, hashes, and redistribution approval before release — omit unverified samples.

## Mixer semantics

Session controls (per mixer scope):

| Control | Default | Notes |
|---------|---------|-------|
| `trimDb` | `0` | Browser-only gain relative to canonical `track.volume` |
| `panOffset` | `0` | Added to canonical pan (−1…1) |
| `muted` / `solo` | off | Live `uiGain`; also zeros wet send input |
| `reverbSend` | `0` | Post-fader send into one shared `Tone.Reverb` |

Scopes: `working`, `development`, `arrangement`, `version`, `preview`. Auditioning a candidate never prunes working controls.

## Transport and expression

- Owned Transport callback IDs only — never global `Transport.cancel()`.
- Seek / resume / loop wrap use one `relocate()` path: short fade, release, controller restore, held-note reconstruct.
- Velocity uses a soft curve for audibility; MIDI velocity values in V2 are not rewritten.
- Loop bounds and play-from-cursor are ephemeral UI state (`playbackLoop`, edit cursor).

## Performance budgets

- Prefer ≤ ~24 metered tracks; meters disable above budget.
- Activity polling ≤ 10–20 Hz; Zustand updates only on material level changes.
- Sample packs must be same-origin (or allowlisted HTTPS with integrity) — no CDN instrument hosts by default.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Silent track | Mute/solo, trim, or sample load → synth fallback status in mixer row |
| Clicks on seek | Relocation fade / release path; report stuck notes with track id only |
| Wet tail after stop | Shared reverb is disposed on stop/dispose and regenerated on next prepare |
| Export sounds different | Expected — FluidSynth SoundFont ≠ browser synth/sampler |

## Logging

Use `VITE_LOG_LEVEL` (`appLogger`). Log bounded source keys, profile/fallback reason codes, and transport lifecycle. Never log compositions, event arrays, sample bytes, or periodic meter frames.

## Project license note

The repository root `LICENSE` file is currently CC0 1.0 text while `README.md` historically claimed MIT. That pre-existing inconsistency is flagged for owner resolution and is separate from browser sample-pack licensing in `THIRD_PARTY_NOTICES.md`. This feature does not change the project license.

## Related files

- `frontend/src/utils/tonePlaybackEngine.js`
- `frontend/src/utils/playbackTracks.js` / `playbackEvents.js` / `playbackSource.js`
- `frontend/src/utils/playbackInstrumentAdapters.js` / `browserPlaybackAssets.js`
- `frontend/src/utils/playbackMixerControls.js`
- `frontend/src/components/PlaybackControls.jsx` / `TrackPlaybackControls.jsx`
- `frontend/e2e/playback-mixer.spec.js`
