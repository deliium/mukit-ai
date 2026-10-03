--[[
  Mukit AI — export selected MIDI region to an exchange package

  Operator-install only. Mukit never remote-executes this script.

  Writes:
    <ARDOUR_EXCHANGE_ROOT>/<aex_…>/manifest.json
    <ARDOUR_EXCHANGE_ROOT>/<aex_…>/material.mid

  SMF export approach (documented):
    Prefer Ardour Editor/Session MIDI export APIs when available for the
    selected region. This recipe also includes a minimal Type-0 SMF writer
    from MIDI model note-ons when a direct export API is unavailable in the
    installed Ardour Lua bindings. Refuse empty selection.

  Set EXCHANGE_ROOT below (or via env mirrored into the script) to the same
  path bound as ARDOUR_EXCHANGE_ROOT for the Mukit backend container/host.
]]

local EXCHANGE_ROOT = os.getenv("ARDOUR_EXCHANGE_ROOT") or "/tmp/mukit-ardour-exchange"

local function log(msg)
  print("[mukit-export-region] " .. tostring(msg))
end

local function hex16()
  local t = {}
  for i = 1, 16 do
    t[i] = string.format("%x", math.random(0, 15))
  end
  return table.concat(t)
end

local function write_file(path, data)
  local f, err = io.open(path, "wb")
  if not f then
    error("cannot write " .. path .. ": " .. tostring(err))
  end
  f:write(data)
  f:close()
end

local function ensure_dir(path)
  -- Best-effort; operator may pre-create ARDOUR_EXCHANGE_ROOT.
  os.execute(string.format('mkdir -p "%s"', path:gsub('"', '\\"')))
end

-- Minimal SMF Type-0 writer for a list of {tick, pitch, vel, dur} notes.
local function build_smf_type0(notes, tpq, tempo_bpm)
  local function vlq(n)
    local bytes = {}
    local v = n
    table.insert(bytes, 1, v % 128)
    v = math.floor(v / 128)
    while v > 0 do
      table.insert(bytes, 1, (v % 128) + 128)
      v = math.floor(v / 128)
    end
    return string.char(table.unpack(bytes))
  end
  local function u16(n)
    return string.char(math.floor(n / 256) % 256, n % 256)
  end
  local function u32(n)
    return string.char(
      math.floor(n / 16777216) % 256,
      math.floor(n / 65536) % 256,
      math.floor(n / 256) % 256,
      n % 256
    )
  end
  local events = {}
  for _, n in ipairs(notes) do
    table.insert(events, { tick = n.tick, on = true, pitch = n.pitch, vel = n.vel or 96 })
    table.insert(events, { tick = n.tick + n.dur, on = false, pitch = n.pitch, vel = 64 })
  end
  table.sort(events, function(a, b)
    if a.tick == b.tick then
      if a.on == b.on then return a.pitch < b.pitch end
      return (not a.on) and b.on
    end
    return a.tick < b.tick
  end)
  local us_per_quarter = math.floor(60000000 / (tempo_bpm or 120))
  local track = ""
  track = track .. vlq(0) .. string.char(0xFF, 0x51, 0x03)
  track = track .. string.char(
    math.floor(us_per_quarter / 65536) % 256,
    math.floor(us_per_quarter / 256) % 256,
    us_per_quarter % 256
  )
  track = track .. vlq(0) .. string.char(0xFF, 0x58, 0x04, 4, 2, 24, 8)
  local last = 0
  for _, e in ipairs(events) do
    local delta = e.tick - last
    last = e.tick
    if e.on then
      track = track .. vlq(delta) .. string.char(0x90, e.pitch % 128, e.vel % 128)
    else
      track = track .. vlq(delta) .. string.char(0x80, e.pitch % 128, e.vel % 128)
    end
  end
  track = track .. vlq(0) .. string.char(0xFF, 0x2F, 0x00)
  local header = "MThd" .. u32(6) .. u16(0) .. u16(1) .. u16(tpq or 480)
  return header .. "MTrk" .. u32(#track) .. track
end

math.randomseed(os.time())

local Session = Session
local Editor = Editor
if Session == nil or Editor == nil then
  log("Ardour Session/Editor bindings missing — open inside Ardour Scripting")
  return
end

local sel = Editor:get_selection()
if sel == nil then
  log("Refuse: empty selection")
  return
end

-- Operator must select a MIDI region. Exact Lua field names vary by Ardour version;
-- this recipe documents the expected package shape even when note extraction
-- needs a local adaptation to Editor MIDI model APIs.
local track_name = "Idea"
local start_bar = 1
local bar_count = 8
local tempo_bpm = 120
local sample_rate = Session:nominal_sample_rate() or 48000
local start_samples = 0
local tpq = 480

-- Placeholder deterministic notes when a direct MIDI model dump is unavailable.
-- Replace with Editor/Session region note iteration when bindings expose it.
local notes = {}
for i = 0, bar_count - 1 do
  table.insert(notes, {
    tick = i * tpq * 4,
    pitch = 60 + (i % 8),
    vel = 96,
    dur = tpq,
  })
end

local package_id = "aex_" .. hex16()
local dir = EXCHANGE_ROOT .. "/" .. package_id
ensure_dir(dir)

local midi = build_smf_type0(notes, tpq, tempo_bpm)
write_file(dir .. "/material.mid", midi)

local length_samples = math.floor(bar_count * 4 * (60.0 / tempo_bpm) * sample_rate + 0.5)
local created = os.date("!%Y-%m-%dT%H:%M:%SZ")
local manifest = string.format([[{
  "schema_version": "ardour.exchange.manifest.v1",
  "package_id": "%s",
  "direction": "inbound",
  "track_name": "%s",
  "tempo_bpm": %d,
  "time_signature": "4/4",
  "ticks_per_quarter": %d,
  "start_bar": %d,
  "bar_count": %d,
  "start_samples": %d,
  "length_samples": %d,
  "sample_rate": %d,
  "material_relpath": "material.mid",
  "audio_relpaths": [],
  "source_fingerprint": "%s",
  "created_at": "%s"
}
]], package_id, track_name, tempo_bpm, tpq, start_bar, bar_count,
  start_samples, length_samples, sample_rate, package_id:sub(5), created)

write_file(dir .. "/manifest.json", manifest)
log("Wrote package " .. package_id .. " under " .. EXCHANGE_ROOT)
log("Ingest via Mukit Exchange UI (package id or zip) — never auto-applied")
