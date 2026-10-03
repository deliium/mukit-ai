--[[
  Mukit AI — import an outbound exchange package at start_samples / start_bar

  Operator-install only. Mukit never remote-executes this script and never
  edits .ardour session XML from the backend.

  Reads:
    <package_dir>/manifest.json
    <package_dir>/material.mid

  Placement:
    Prefer Editor:do_import / Session import APIs at manifest.start_samples.
    Use SMFTempoIgnore (or the documented tempo disposition for your Ardour
    version) so Mukit conductor tempo in the SMF + manifest.tempo_bpm stay
    honest. Create or select a MIDI track named manifest.track_name when possible.

  Fallback: Session → Import the MIDI file manually and nudge to start_bar.
]]

local function log(msg)
  print("[mukit-import-package] " .. tostring(msg))
end

local function read_file(path)
  local f, err = io.open(path, "rb")
  if not f then
    error("cannot read " .. path .. ": " .. tostring(err))
  end
  local data = f:read("*a")
  f:close()
  return data
end

local function json_string(blob, key)
  local pattern = '"' .. key .. '"%s*:%s*"([^"]*)"'
  return blob:match(pattern)
end

local function json_number(blob, key)
  local pattern = '"' .. key .. '"%s*:%s*(-?%d+)'
  local v = blob:match(pattern)
  return v and tonumber(v) or nil
end

-- Operator sets PACKAGE_DIR to the outbound package directory from Mukit prepare.
local PACKAGE_DIR = os.getenv("ARDOUR_EXCHANGE_PACKAGE") or ""
if PACKAGE_DIR == "" then
  log("Set ARDOUR_EXCHANGE_PACKAGE to the package directory path")
  return
end

local Session = Session
local Editor = Editor
if Session == nil then
  log("Ardour Session binding missing — open inside Ardour Scripting")
  return
end

local manifest_path = PACKAGE_DIR .. "/manifest.json"
local midi_path = PACKAGE_DIR .. "/material.mid"
local manifest = read_file(manifest_path)
local track_name = json_string(manifest, "track_name") or "Exchange"
local start_samples = json_number(manifest, "start_samples") or 0
local start_bar = json_number(manifest, "start_bar") or 1
local tempo_bpm = json_number(manifest, "tempo_bpm") or 120
local package_id = json_string(manifest, "package_id") or "unknown"

log(string.format(
  "Import %s track=%s start_samples=%d start_bar=%d tempo=%d",
  package_id, track_name, start_samples, start_bar, tempo_bpm
))

-- Prefer Editor:do_import when present. Exact enum names vary by Ardour build;
-- document SMFTempoIgnore disposition so session tempo map is not clobbered.
if Editor ~= nil and Editor.do_import ~= nil then
  -- Example call shape (adapt to your Ardour Lua bindings):
  -- Editor:do_import({ midi_path }, ImportAsTrack, SMFTempoIgnore, start_samples)
  log("Editor:do_import available — adapt call for your Ardour version")
  log("Target position samples=" .. tostring(start_samples) .. " (same bars as export)")
else
  log("Fallback: use Session → Import for " .. midi_path)
  log("Place the region at sample " .. tostring(start_samples) .. " / bar " .. tostring(start_bar))
end

log("Done. Mukit never wrote playlist/session XML.")
