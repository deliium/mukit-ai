-- Mukit example: list route/track count for the open session.
-- Operator installs and runs this in Ardour Scripting.
-- Mukit never remote-executes this file.

ardour {
  ["type"] = "EditorAction",
  name = "Mukit: List track count",
  author = "Mukit AI examples",
  description = "Print the number of routes in the open session to the script log.",
}

function factory()
  return function()
    local session = Session
    if session == nil then
      print("Mukit Lua: no Session")
      return
    end
    local count = session:nroutes()
    print(string.format("Mukit Lua: route count = %d", count))
  end
end
