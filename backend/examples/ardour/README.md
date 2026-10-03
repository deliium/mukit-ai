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

## Relation to Mukit companion

Transport, mixer fader/pan/mute/solo, and master record-arm use the OSC companion (`docs/ardour-companion.md`). Use these Lua recipes only when you need session housekeeping OSC does not cover cleanly.
