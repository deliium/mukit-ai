[← Composition V2](composition-v2.md) · [Back to README](../README.md) · [Testing →](testing.md)

# Composition Editor (V2 Piano Roll)

Productive multi-note editing for canonical `composition.v2`. Playable source remains `tracks[].events[]` only — never invent notes from `harmony`.

## Mental model

| Concern | Source of truth | Persistence |
|---------|-----------------|-------------|
| Notes / expression | `editedMusicJson` (`composition.v2`) | Project autosave / Save |
| Multi-note selection | `{ trackId, eventId }` refs in Zustand | Session only |
| Clipboard | Store clipboard (not OS clipboard) | Session only |
| Undo / redo | Composition-edit history (bounded) | Session only |
| Track hide / lock | Editor prefs | Session only (not V2) |
| Mute / solo / volume | Playback track controls | Session only |
| Edit cursor / loop | Transport UI state | Session only |
| Arrangement / analysis | Separate preview sidecars | Not composition data |

All successful note/bulk/JSON-canonical edits go through one **composition transaction**: validate → commit `editedMusicJson` → one undo entry → reconcile selection → invalidate analysis/previews as needed. Invalid JSON in the JSON tool stays repairable without clobbering valid history.

## Selection and gestures

- Click / Ctrl·Cmd-click toggle / Shift-click range on notes
- **Alt+drag** (or Meta+drag): note box select across visible tracks
- **Shift+drag**: AI bar-region selection (unchanged)
- Hidden tracks are excluded from selection; locked tracks reject mutating ops
- Select All Visible / Clear Selection toolbar actions

## Transforms

Inspector / shortcuts cover transpose (±1 / ±octave), velocity set/delta, quantize, length ops, humanize, duplicate, articulations (tie-safe skips reported), and track dynamics (`dynamic_marks` upsert/remove at cursor or bar).

Clipboard: Ctrl/Cmd+C / X / V / D. Paste anchors at the **edit cursor**. Motif metadata is not copied as note data; deletes reconcile motif refs.

## Navigation and transport

- Edit cursor (ruler / Ctrl+click); bar and section prev/next
- Zoom in/out, fit, zoom-to-selection; Ctrl+wheel zoom
- **Play From Cursor**, loop from selection / toggle / clear
- Auto-follow scrolls the viewport during playback without rerendering every note on each tick
- Browser instruments/mixer (trim/pan/mute/solo/send) are ephemeral — see [browser-playback.md](browser-playback.md)

## Keyboard focus

Shortcuts bind to the piano-roll application shell (`aria-label="Piano roll note grid"`). They are ignored in inputs, textareas, buttons (including note blocks with `role="button"`), and the JSON editor — focus the shell before Ctrl+C / undo keys in automation.

## Logging

Use `VITE_LOG_LEVEL`. Editor namespaces go through `appLogger` (`musicStore`, `pianoRoll.*`, `editor.perf`). Logs may include operation ids, counts, tick bounds, revision prefixes — never event arrays, clipboard bodies, or full compositions. Perf counters (`editorPerfInstrumentation`) stay inert unless explicitly enabled for E2E.

## Tests

See [testing.md](testing.md) § V2 piano-roll editor workflow (100-bar fixture, Playwright journey, performance budgets).
