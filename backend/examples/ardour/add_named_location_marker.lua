-- Mukit example: add a named location marker at the playhead.
-- Operator installs and runs this in Ardour Scripting.
-- Mukit never remote-executes this file.
--
-- Adjust MARKER_NAME before running. Requires an open session.

ardour {
  ["type"] = "EditorAction",
  name = "Mukit: Add named location marker",
  author = "Mukit AI examples",
  description = "Add a location marker named MARKER_NAME at the playhead (manual run).",
}

function factory()
  return function()
    local MARKER_NAME = "mukit_cue"
    local session = Session
    if session == nil then
      print("Mukit Lua: no Session")
      return
    end
    local pos = session:transport_sample()
    -- Location markers API differs slightly across Ardour major versions;
    -- prefer Editor → Locations if this call is unavailable in your build.
    local ok, err = pcall(function()
      session:locations():add(pos, MARKER_NAME, false, true)
    end)
    if not ok then
      print("Mukit Lua: could not add marker: " .. tostring(err))
      print("Mukit Lua: use Editor → Locations manually, or adapt to your Ardour Lua bindings.")
      return
    end
    print(string.format("Mukit Lua: added marker %q at sample %d", MARKER_NAME, pos))
  end
end
