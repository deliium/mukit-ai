# Ardour Lua recipes (operator install only)

These scripts are **examples** for session ops that are awkward or unavailable over Ardour OSC. Mukit never remote-injects or remote-executes Lua.

## Install

1. Open Ardour with your session.
2. **Window → Scripting** (or the Lua Script Manager).
3. Add / load a script from this directory.
4. Run it manually when needed.

No secrets belong in these files. Do not paste API keys or composition JSON into Lua.

## Scripts

| File | Purpose |
|------|---------|
| `add_named_location_marker.lua` | Add a named location marker at the edit point / playhead |
| `list_track_count.lua` | Print the number of routes/tracks to the script log |
| `export_selected_midi_region.lua` | Export selected MIDI region → exchange package (`manifest.json` + `material.mid`) under `ARDOUR_EXCHANGE_ROOT` |
| `import_exchange_package.lua` | Import prepared package at `start_samples` / same bars (`ARDOUR_EXCHANGE_PACKAGE`) |

### Exchange root path

Point both Lua and the Mukit backend at the same host directory via `ARDOUR_EXCHANGE_ROOT` (Compose bind-mount into the backend container). See `docs/ardour-session-exchange.md`.

### SMF export approach

`export_selected_midi_region.lua` prefers Ardour Editor/Session MIDI export APIs when available; otherwise it documents a minimal Type-0 SMF writer from the MIDI model. Adapt note extraction to your Ardour Lua bindings. Refuse empty selection.

## Relation to Mukit companion

Transport, mixer fader/pan/mute/solo, and master record-arm use the OSC companion (`docs/ardour-companion.md`). Selected-region material exchange uses these Lua recipes plus `/ardour/exchange/*` (`docs/ardour-session-exchange.md`). Mukit never remote-executes Lua and never edits `.ardour` session XML.
