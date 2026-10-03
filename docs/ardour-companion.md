# Ardour Companion

Connect the Studio **Ardour** tab to one active Ardour session over **Ardour OSC**. Product generation is **V5**; the playable score stays **`composition.v2`**. There is no `composition.v5`. The companion never rewrites note events from feedback.

## What it does

1. Connect with host, OSC port, and feedback listen port.
2. With explicit **Allow DAW control**, send play / stop / locate, master record-arm, and strip fader / pan / mute / solo.
3. Treat **OSC feedback** as the source of truth (`accepted` ≠ observed).
4. Drive the focused **Ardour workflow** surface (session → transport/timecode → musical scope → Send → alternatives → return) without treating the Studio editor as a replacement DAW.
5. Exchange selected-region packages via Lua + `/ardour/exchange/*` (see [ardour-session-exchange.md](ardour-session-exchange.md)); whole-score handoff still uses MIDI / MusicXML / WAV export.

```text
Ardour (primary DAW)
  OSC companion: session + transport + strip select
  Lua: export/import exchange packages
        │
        ▼
Studio Ardour workflow tab
  scope → Send (ingest) → Realize → Apply → Prepare (working V2)
```

## Enable in Ardour

1. **Edit → Preferences → Control Surfaces** — enable **Open Sound Control (OSC)**.
2. Note the control port (default **3819**).
3. Ensure Ardour can send feedback to Mukit’s feedback listen port (default **8000**).
4. Keep an open session; connect from the Studio only when you intend to control it.

On connect, Mukit sends locked `/set_surface` args:

| Arg | Value | Meaning |
|-----|-------|---------|
| `bank_size` | `16` | Bank of 16 strips |
| `strip_types` | `159` | Normal strip set |
| `feedback` | `9243` | Foundation `8219` + `1024` (position in samples). `/position/samples` may arrive as int or digit string |
| `gainmode` | `0` | Locked ship-1 value |
| `port` | feedback port | So Ardour targets Mukit’s listen port |

After connect, Mukit may send `/strip/list`; `end_route_list` can supply `sample_rate` for exchange session context.

## Mukit environment

| Variable | Default | Notes |
|----------|---------|-------|
| `ARDOUR_COMPANION_ENABLED` | off | Unrecognized → off |
| `ARDOUR_COMPANION_FAKE` | off | In-process mock peer for CI |
| `ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS` | off | Public IPs refused unless on |
| `ARDOUR_COMPANION_DEFAULT_HOST` | `127.0.0.1` | UI default |
| `ARDOUR_COMPANION_DEFAULT_OSC_PORT` | `3819` | Never bind feedback here |
| `ARDOUR_COMPANION_DEFAULT_FEEDBACK_PORT` | `8000` | Companion listen port |
| `ARDOUR_COMPANION_FEEDBACK_STALE_MS` | `3000` | Stale threshold |

Session is **process memory only** — restart clears it. See `.env.example`.

## Permission model

- Feature flag off → `GET /ardour/companion/status` still returns **200** with `enabled=false`; mutating routes **403**.
- Connect body / UI checkbox **Allow DAW control** must be true before mutating OSC commands.
- Host allowlist: loopback + private; refuse link-local / multicast / unspecified. Hostnames allowed only when DNS resolves to an allowed address (unless public hosts are enabled).

## Command honesty

- HTTP success means the OSC datagram was **accepted** (sent), not that Ardour applied it.
- Status fields update only from feedback (or the fake peer).
- Master record-arm uses `/rec_enable_toggle` only when observed arm ≠ desired; if observed is unknown/stale → refuse `ardour_feedback_stale`. Per-strip `/strip/recenable` is out of ship-1.

## Compose / host networking

The companion targets the **Ardour host**, not the SPA origin.

| Layout | Host field tip |
|--------|----------------|
| Backend + Ardour on the same host | `127.0.0.1` |
| Backend in Docker, Ardour on the Docker host | `host.docker.internal` (Desktop) or the bridge gateway IP (often `172.17.0.1` on Linux) — **not** container `127.0.0.1` |
| Ardour on another LAN machine | That machine’s private IP (public IPs need `ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS=1`) |

If feedback never arrives: confirm Ardour OSC is enabled, feedback port matches, firewall allows UDP both ways, and you did not point a containerized backend at `127.0.0.1` expecting the host.

## Fake mode

`ARDOUR_COMPANION_FAKE=1` uses an in-process mock peer. CI never requires a real Ardour process.

## Lua recipes

Operator-installed examples live under `backend/examples/ardour/`. Copy them with `./scripts/install_ardour_helpers.sh <ardour-scripts-dir>` (idempotent; refuses `.ardour` session paths). Mukit does **not** remote-execute Lua.

## Why not LV2

**Verdict: not required.** OSC + Lua + file/session exchange satisfies V5 Ardour companion acceptance without a native plugin. Do not scaffold an LV2 project for this milestone.

| Capability | OSC + Lua + exchange | Full LV2 AI host | Minimal LV2 bridge |
|------------|----------------------|------------------|--------------------|
| Transport / locate / strip mixer | Yes (OSC) | Overkill | Unnecessary |
| Selected-region MIDI round-trip | Yes (Lua packages) | Would reimplement backend | Still needs file or port I/O |
| AI generate / transform | HTTP backend preview engines | Reimplements AI in-plugin | Shuttles buffers only |
| Continuous bidirectional note sync | Out of acceptance | Possible, huge scope | Possible, huge scope |
| In-graph meter taps / sample-accurate audio I/O | Out of acceptance | Native | Native |
| Packaging / CI matrix | Docs + install script today | Native build hell | Native build/CI matrix |

Remaining gaps (continuous note sync, in-graph meters, zero-file MIDI paste over a plugin port) are explicitly out of acceptance. Revisit LV2 only if a future milestone needs sample-accurate in-graph audio I/O that files cannot provide.

## Honesty limits

- No bidirectional continuous note sync over OSC.
- No auto-import of Ardour sessions into `composition.v2`.
- Selected-region material exchange is a separate package path — see [ardour-session-exchange.md](ardour-session-exchange.md) (Lua export/import + `/ardour/exchange/*`). Mukit still never edits `.ardour` XML.
- No Tone.js transport coupling to Ardour playhead as a requirement.
- No multi-DAW abstraction; Ardour-only.
- Timecode display is samples÷`sample_rate` (optional 30 fps frames display-only), not true Ardour BBT (OSC bit `+32` out of scope).
- `ai_agents/` must not import companion or exchange modules.

## UI

Studio tab **Ardour** is an ordered workflow surface (`data-testid="ardour-workflow-panel"`):

1. Connected session (host/ports/permission/Connect)
2. Transport + timecode readout
3. Selected musical scope (manifest bars when preview present; else OSC strip + locate + Lua export CTA)
4. Send to AI Composer (scan/upload/ingest; explicit inbound Apply)
5. AI op → Realize → alternatives → Apply
6. Prepare / download (working `composition.v2`) + Lua return CTA; optional stems

Strip mixer lives under **Mixer (advanced)**. Polls companion status every **1000 ms** while the tab is selected (DEBUG only — not INFO spam). Opening the tab never connects, ingests, or applies. Mixer/transport controls follow **session** `control_permission` from connect.

## See also

- [ardour-session-exchange.md](ardour-session-exchange.md) — selected-region package exchange (ingest / realize / prepare)
- [daw-interoperability.md](daw-interoperability.md) — SMF / MusicXML / WAV handoff + Ardour import steps
- [composition-v2.md](composition-v2.md) — playable score contract
- `backend/examples/ardour/README.md` — Lua install + connected-workflow quickstart
- `scripts/install_ardour_helpers.sh` — copy Lua recipes to an Ardour scripts directory
