# Implementation Plan: V5 Ardour Companion Integration Foundation

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-10-03

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: thin Ardour studio tab — host/OSC/feedback ports, Connect/Disconnect, explicit control-permission checkbox, connection + transport + selected-strip + mixer readouts from feedback, play/stop/locate + volume/pan/mute/solo controls; no piano-roll redesign, no multi-DAW picker, no LV2 host UI
- Plan depth: ultra (full mode). Locked approach tables, audit, and terminology below are part of the plan
- Refined: 2026-10-03 (`/aif-improve`). Task 1 locks `ipaddress` host allow (loopback + private; refuse link-local/multicast/unspecified; hostname only if resolved address passes); Task 2 locks `/set_surface` `bank_size=16`, `strip_types=159`, `feedback=8219` (1+2+8+16+8192), `gainmode=0`, and master record-arm via `/rec_enable_toggle` only when observed ≠ desired (stale → refuse, no strip recenable); Task 4 locks connect-replace + lifespan-honest clear; Task 5 folds lifespan UDP teardown into `main.py` and makes `GET /status` always 200 with `enabled` (mutating routes 403 when disabled); Task 6 locks 1000 ms poll while tab selected + clear on leave; Task 7 documents Compose/host OSC routing (`host.docker.internal` / gateway); optional `/ready` block deferred
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`)
- Scope: connect AI Composer to one active Ardour session via Ardour OSC (+ documented Lua recipes + existing MIDI/WAV/MusicXML export). Never invent `composition.v5`. Never silently rewrite `composition.v2` from Ardour feedback. Predecessor: `docs/daw-interoperability.md` (file handoff only today), adaptive-engine process-memory session pattern.

## Roadmap Linkage
Milestone: "V5 Ardour Companion integration foundation"
Rationale: V3 DAW interop only covers SMF/MusicXML drag/download; there is no live Ardour transport/mixer companion. First incomplete roadmap items (performance conductor / spatial) are orthogonal. Implementation does **not** edit `ROADMAP.md`.

## Goal

Ship an **Ardour-only** companion so the Studio can:

1. Discover/configure connection: host, OSC port, feedback port, connection status.
2. Control (when explicitly permitted): play, stop, locate, record-arm query/control where OSC-safe, selected route/strip info, strip volume/pan/mute/solo.
3. **Consume Ardour OSC feedback** as the source of truth for transport/mixer state (never assume command success).
4. Show connection status in the UI with an explicit DAW-control permission gate.
5. Exchange musical assets via existing Standard MIDI / MusicXML / WAV export into Ardour (documented recipe).
6. Provide mocked OSC tests + Ardour setup documentation.

```text
composition.v2  (unchanged; export-only for asset handoff)
        │
        ▼
Export MIDI / MusicXML / WAV  ──import──►  Ardour session
                                              ▲
SPA Ardour tab ──HTTP──► FastAPI companion ──OSC UDP──┘
                         ▲ feedback UDP │
                         └──────────────┘
```

**Terminology lock:** Product generation is **V5**. The playable score stays **`composition.v2`**. There is no **`composition.v5`**. **Ardour Companion** is the Mukit-side session that speaks Ardour OSC — not a generic DAW bus. **Control permission** is the explicit user/API flag required before any mutating OSC command is sent. **Feedback-observed state** is the session snapshot updated only from inbound OSC feedback (or fake peer). **Accepted** means an OSC datagram was sent; **observed** means feedback updated the field. **Lua recipe** is an operator-installed Ardour Lua script for ops not cleanly available via OSC — ship-1 does not remote-inject or remote-execute Lua. **Asset exchange** means file export/import, not OSC note streaming. Predecessor: adaptive engine session memory, collaboration-style feature flags, `docs/daw-interoperability.md`.

## Approach Evaluation (locked)

### Part A — Integration mechanism stack

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Browser Web MIDI / WebRTC into Ardour** | SPA-only | No Ardour transport/mixer; browsers cannot speak Ardour OSC UDP | **Reject** as primary |
| **B. Generic multi-DAW abstraction (Reaper/Ableton/Ardour)** | Future-proof marketing | Wrong for “Ardour first”; invents unused ports/paths | **Reject** |
| **C. Ardour-only: OSC control+feedback, Lua recipes, file asset exchange; LV2 only if proven necessary** | Matches goal; testable with mock OSC | Ardour-specific | **Accepted** |
| **D. LV2 plugin as the control path** | In-graph | Heavy; not needed for transport/mixer; hosting outside Mukit | **Reject** for ship-1 (see LV2 audit) |

### Part B — Where OSC I/O lives

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Frontend UDP** | Low latency feel | Impossible in browsers | **Reject** |
| **B. Backend owns UDP send + feedback bind; SPA uses HTTP** | Matches stack; mockable; flaggable | Extra hop | **Accepted** |
| **C. Separate sidecar process** | Isolation | Ops complexity; Compose creep | **Defer** |

### Part C — Session durability

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. SQLite durable connection rows + Alembic** | Restart-safe hosts | Couples DAW IP to project DB; silent reconnect risk | **Reject** for ship-1 |
| **B. Process-memory session (adaptive-engine style) + env defaults + UI fields** | Explicit Connect; lost on restart (honest) | Must re-enter host | **Accepted** |
| **C. localStorage-only with no backend** | Tiny | Cannot UDP; no feedback truth | **Reject** |

### Part D — Command success model

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. HTTP 200 = Ardour did it** | Simple | Lies when Ardour OSC disabled/offline | **Reject** |
| **B. Commands return `accepted`; status fields update only from feedback (or timeout → `unknown`/`stale`)** | Matches requirement | Needs feedback enablement on connect | **Accepted** |
| **C. Block HTTP until matching feedback** | Strongest | Latency/hangs on missing feedback | **Reject** as default (optional wait param deferred) |

### Part E — Permission / safety

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Any client may OSC when backend is up** | Convenient | Dangerous on LAN | **Reject** |
| **B. `ARDOUR_COMPANION_ENABLED` off by default + connect body `control_permission: true` + private/loopback host allowlist** | Explicit; matches collab/scheduling flags | Extra click | **Accepted** |
| **C. Bearer token like adaptive engine** | Strong for remote | Overkill for local DAW companion | **Defer** (loopback SPA→API already local) |

### Part F — LV2 necessity audit (locked Reject)

Capabilities in acceptance (transport, locate, record-arm, strip select/name, fader/pan/mute/solo, feedback) are all covered by Ardour’s built-in OSC surface (`/transport_*`, `/locate`, `/rec_enable_toggle`, `/strip/*`, `/set_surface`, feedback bits). File exchange uses existing export. No in-graph DSP, MIDI effect, or meter tap is required for ship-1. **Therefore LV2 is not introduced.** Revisit only if a future capability cannot be done via OSC/Lua/files. Strip `/strip/recenable` is **out of ship-1** (master session arm only).

### Part G — Lua role

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Remote-execute Lua over OSC from Mukit** | Powerful | Security surface; version variance | **Reject** for ship-1 |
| **B. Ship example Lua under `backend/examples/ardour/` + docs; operator installs in Ardour for non-OSC session recipes** | Honest; no remote code exec | Manual step | **Accepted** |
| **C. No Lua at all** | Smaller | Leaves “session ops not cleanly OSC” gap empty | **Insufficient** vs architecture preference |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| DAW file handoff | `docs/daw-interoperability.md`, `frontend/src/components/ExportControls.jsx`, `/export/midi` `/export/musicxml` `/export/wav` | Document Ardour import recipe; no new proprietary writer |
| Session-in-memory pattern | `backend/app/services/adaptive_engine_service.py` | Clone process registry / clear-on-restart honesty |
| Lifespan teardown | `main.py` → `cancel_adaptive_engine_tasks()` | Same pattern for companion UDP close |
| Feature flags | `backend/app/collaboration_settings.py`, scheduling/execution-node flags | `ARDOUR_COMPANION_ENABLED` + unrecognized→off |
| Status-when-disabled | `GET /collaboration/status` → `200 {enabled:false}` | Same for companion status GET |
| Host/IP allow policy | `execution_node_schemas._ip_is_allowed` (`ipaddress`) | Same loopback/private/refuse link-local rules for OSC host |
| Thin tab UI | `frontend/src/components/SpatialScenePanel.jsx`, `ComposerWorkspace.jsx` `TABS` | Add `{ id: 'ardour', label: 'Ardour' }` |
| HTTP client pattern | `frontend/src/api/*Api.js` | New `ardourCompanionApi.js` |
| Router registration | `backend/app/main.py` `include_router` | Register `ardour_companion` router |
| Secret / logging rules | RULES / AGENTS | Never log full OSC payloads at INFO; never log composition events |
| Alembic head | `20261003_0027` spatial_scenes | **No migration** (session-only) |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Ardour OSC codec + path map | No UDP/OSC in repo today; lock concrete `/set_surface` args |
| Feedback listener + state reducer | Transport/strips/selected/record from feedback + heartbeat |
| Companion session service | Connect-replace/`set_surface`/commands/disconnect |
| Lifespan UDP teardown | Mirror adaptive-engine cancel on shutdown |
| Settings + host allowlist + fake mode | Env-driven; `ipaddress` policy |
| HTTP API + error codes | Closed DTOs; status GET always 200 |
| Thin Ardour tab + permission checkbox | 1000 ms poll; status from feedback |
| Lua examples + docs | Setup + Compose host routing + honesty limits |
| Mocked OSC tests | Fake peer, no real Ardour required |

### Coupling risks to avoid

1. Inventing `composition.v5` or writing notes from Ardour feedback.
2. Generic `DawClient` / Reaper/Ableton ports in ship-1.
3. Shipping or requiring an LV2 plugin for acceptance.
4. Assuming command success without feedback.
5. Auto-connecting on tab open or project open.
6. Allowing arbitrary public internet OSC targets by default.
7. Remote Lua execution / injecting scripts into Ardour from the API.
8. `ai_agents/` importing companion OSC/session modules.
9. Logging raw OSC blobs, API keys, or event arrays at INFO.
10. Writing `DATASET_ROOT` or treating Ardour sessions as training corpus.
11. Editing `ROADMAP.md` during implementation.
12. Blocking browser Tone transport on Ardour transport (orthogonal clocks in ship-1).
13. Blind `/rec_enable_toggle` when observed record state is unknown/stale.
14. Targeting `127.0.0.1` from a Docker backend when Ardour runs on the host (document Compose routing).

## Scope And Decisions

### In scope
- `ardour.companion.connect.v1` / `session.v1` / `status.v1` / command DTOs (`extra=forbid`).
- Settings: `ARDOUR_COMPANION_ENABLED` (default off), `ARDOUR_COMPANION_FAKE`, default host/ports, feedback bind, host allowlist via `ipaddress`, `ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS` (default off), command/feedback timeouts.
- Pure OSC encode/decode (stdlib sockets; **no** `python-osc` dependency).
- Ardour path map with locked `/set_surface` args (below).
- Process-memory companion session; feedback-driven status; connect-replace; lifespan teardown.
- HTTP: status (always 200), connect, disconnect, transport play/stop/locate, record-arm get/set (master only), strip volume/pan/mute/solo, strip list/selected (from feedback).
- Thin Ardour tab with explicit control permission and 1000 ms status poll.
- `backend/examples/ardour/` Lua recipes + `docs/ardour-companion.md` (incl. Compose host routing) + Ardour section in daw-interop.
- Mocked OSC + API (+ light frontend) tests; `.env.example` + AGENTS.md entry points.

### Out of scope
- Multi-DAW abstraction; Ableton Link; JACK patching UI.
- LV2 / VST plugin component.
- Remote Lua execution; bidirectional note sync over OSC.
- Auto-import Ardour sessions into `composition.v2`.
- Syncing Tone.js transport to Ardour playhead (future).
- Durable SQLite connection store; Alembic.
- Soft `/ready` companion block (deferred; never required for acceptance).
- Per-strip `/strip/recenable` (master session arm only in ship-1).
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Documents**

| Document | Role |
|----------|------|
| `ardour.companion.connect.v1` | Request: `host`, `osc_port`, `feedback_port`, `control_permission: true` required for mutating commands |
| `ardour.companion.session.v1` | Session id + config echo + `connection_state` + last error code |
| `ardour.companion.status.v1` | `enabled` + feedback-observed: transport playing/stopped, locate samples, record armed, selected strip, strips[{ssid,name,fader,pan,mute,solo}], `feedback_age_ms`, `stale` |
| Command bodies | Closed enums: `play`/`stop`/`locate`/`record_arm`/`strip_fader`/`strip_pan`/`strip_mute`/`strip_solo` |

**2. Ardour OSC path map (ship-1)**

| Capability | Outbound | Feedback / observe |
|------------|----------|--------------------|
| Surface setup | `/set_surface` with **locked args**: `bank_size=16`, `strip_types=159`, `feedback=8219` (= 1+2+**8**+16+8192: strip buttons, strip values, **heartbeat**, master, select), `gainmode=0` (position) | Heartbeat / strip refresh |
| Play | `/transport_play` | `/transport_play <state>` |
| Stop | `/transport_stop` | `/transport_stop <state>` |
| Locate | `/locate <samples> <roll>` | Position feedback when enabled; status may show last requested + observed samples |
| Record arm (session master) | `/rec_enable_toggle` **only if** observed arm ≠ `desired`; if observed unknown/stale → refuse `ardour_feedback_stale` (no blind toggle). **No** `/strip/recenable` in ship-1 | Master record-enable from feedback |
| Strip fader | `/strip/fader <ssid> <0..1>` | `/strip/fader …` |
| Strip pan | `/strip/pan_stereo_position <ssid> <0..1>` | matching feedback |
| Mute / solo | `/strip/mute`, `/strip/solo` | matching feedback |
| Selected / names | banking + select as Ardour sends | `/strip/name`, select-channel feedback |

Defaults: host `127.0.0.1`, OSC `3819`, feedback listen `8000` (never bind 3819).

**3. Connection state machine**

`disabled` → `disconnected` → `connecting` → `awaiting_feedback` → `connected` → `stale` (no feedback within timeout) → `disconnected` / `error`.

Mutating commands refused unless `enabled && control_permission && connection_state in {connected, awaiting_feedback}` with WARN when only awaiting. Record-arm additionally requires non-stale observed arm.

**Connect-replace:** A second `connect` while a session exists disconnects the previous session first, then connects (no 409). Process restart and FastAPI lifespan clear the session and close UDP sockets.

**4. Fake mode**

`ARDOUR_COMPANION_FAKE=1`: in-process mock peer records outbound paths and emits deterministic feedback. CI never requires Ardour.

**5. Host policy**

Use `ipaddress` (same spirit as `execution_node_schemas._ip_is_allowed`): allow loopback + private; refuse link-local, multicast, unspecified. Hostname targets are allowed only when DNS resolution yields an address that passes the same check. When `ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS` is on, public addresses are allowed. Refuse with stable code `ardour_host_refused`.

**6. Stable error codes (ship-1)**

| Code | When |
|------|------|
| `ardour_companion_disabled` | Flag off (mutating routes only) |
| `ardour_control_permission_required` | Mutating command without permission |
| `ardour_host_refused` | Host outside allowlist |
| `ardour_not_connected` | No session / disconnected |
| `ardour_feedback_stale` | Soft warning on general commands; **hard refuse** for record-arm when observed unknown/stale |
| `ardour_payload_invalid` | Schema / range refuse |
| `ardour_osc_send_failed` | UDP send failure |
| `ardour_bind_failed` | Feedback port bind failure |

## HTTP surface (locked)

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/ardour/companion/status` | Always **200**: `{ enabled, …status }` — when flag off: `enabled=false`, `connection_state=disabled` (collaboration-status pattern). Never 403 for this GET. |
| POST | `/ardour/companion/connect` | Body connect.v1; replace-if-connected; starts UDP + locked `/set_surface` |
| POST | `/ardour/companion/disconnect` | Tears down sockets |
| POST | `/ardour/companion/transport/play` | OSC play; returns accepted + current status |
| POST | `/ardour/companion/transport/stop` | OSC stop |
| POST | `/ardour/companion/transport/locate` | `{ samples, roll }` |
| GET/POST | `/ardour/companion/record` | GET observed arm; POST `{ desired: bool }` → toggle only if observed ≠ desired |
| GET | `/ardour/companion/strips` | Observed strips + selected |
| POST | `/ardour/companion/strips/{ssid}/fader` | `{ value: 0..1 }` |
| POST | `/ardour/companion/strips/{ssid}/pan` | `{ value: 0..1 }` |
| POST | `/ardour/companion/strips/{ssid}/mute` | `{ value: 0\|1 }` |
| POST | `/ardour/companion/strips/{ssid}/solo` | `{ value: 0\|1 }` |

Mutating routes when flag off → `403` / `ardour_companion_disabled`. Never write Composition.

## UI (locked)

- Tab `ardour` / label `Ardour` in `ComposerWorkspace.jsx`.
- Panel fields: host, osc port, feedback port, **Allow DAW control** checkbox, Connect/Disconnect.
- Status banner: connection_state, feedback_age, stale.
- Transport buttons + locate samples; strip table with fader/pan/mute/solo bound to observed values.
- Poll `GET /ardour/companion/status` every **1000 ms** while the Ardour tab is selected; clear interval on unmount / tab leave.
- Controls gated by `control_permission && connection_state in {connected, awaiting_feedback}`.
- When `enabled=false`: muted “Companion disabled (`ARDOUR_COMPANION_ENABLED`)”.
- Opening the tab never connects and never writes the score.
- Export stays on existing Export controls; panel links to Ardour import docs.

## Tests (locked)

- Codec round-trip + path map unit tests (incl. locked `/set_surface` args and feedback `8219`).
- Fake peer: connect → `/set_surface` sent; play → feedback flips playing; fader → observed value; record-arm refuses when stale; disconnect cleans sockets; connect-replace tears down prior session.
- Flag off → mutating 403, status GET 200 with `enabled=false`; connect without `control_permission` → mutating POST refused; public host refused when allowlist on.
- Lifespan/teardown closes fake listener (no leftover bind).
- API tests with `ARDOUR_COMPANION_FAKE=1`.
- Frontend: permission checkbox gates control buttons; status render; poll cleanup.
- No real Ardour process in CI.

## Docs (locked)

- New `docs/ardour-companion.md`: enable OSC in Ardour Preferences, ports, feedback, Mukit env, permission model, fake mode, Lua install, LV2 rejection rationale, honesty limits (no note sync), **Compose/host networking** (`host.docker.internal` / gateway IP — companion targets the Ardour host, not the SPA origin; troubleshooting when feedback never arrives).
- Extend `docs/daw-interoperability.md` with Ardour SMF/MusicXML/WAV import steps.
- `.env.example`, AGENTS.md entry points, short README pointer.

## Commit Plan

- **Commit 1** (tasks 1–3): `feat(ardour): add companion OSC codec, settings, and feedback state`
- **Commit 2** (tasks 4–5): `feat(ardour): expose companion session HTTP API`
- **Commit 3** (tasks 6–7): `feat(ardour): add Ardour tab, Lua examples, and setup docs`
- **Commit 4** (task 8): `test(ardour): add mocked OSC companion coverage`

## Tasks

### Phase 1: Contracts and OSC core

- [x] Task 1: Define `ardour.companion.connect.v1`, `session.v1`, `status.v1` (incl. `enabled`), and closed command DTOs (`extra=forbid`) with stable error codes. Implement `ardour_companion_settings.py`: `ARDOUR_COMPANION_ENABLED` (default off; unrecognized→off), `ARDOUR_COMPANION_FAKE`, default host `127.0.0.1`, OSC port `3819`, feedback port `8000`, feedback stale timeout, `ARDOUR_COMPANION_ALLOW_PUBLIC_HOSTS` (default off). Host allow helper via **`ipaddress`**: loopback + private allowed; refuse link-local/multicast/unspecified; hostname only if resolved address passes (unless public hosts allowed). Schema unit tests for refuse/round-trip + host policy cases.

  LOGGING: DEBUG settings load once per process `{enabled, fake, default_osc_port, default_feedback_port, allow_public_hosts}` — never log raw env dump; INFO unrecognized flag → off with code `ardour_companion_flag_unrecognized`; WARN host refuse with `ardour_host_refused` (host class only).

  Files: `backend/app/ardour_companion_schemas.py` (new), `backend/app/ardour_companion_settings.py` (new), `backend/tests/test_ardour_companion_schemas.py` (new), `backend/tests/test_ardour_companion_settings.py` (new)

- [x] Task 2: Pure OSC encode/decode over stdlib (`struct`) — **no** `python-osc` dependency. Ardour path map locking ship-1 paths and **concrete** `/set_surface` builder: `bank_size=16`, `strip_types=159`, `feedback=8219`, `gainmode=0`. Paths: `/transport_play`, `/transport_stop`, `/locate`, `/rec_enable_toggle` (master only), `/strip/fader|pan_stereo_position|mute|solo`, name/select feedback. Unit tests: round-trip args (int/float/string/blob), path builders, feedback bitmask constant `8219`, set_surface arg tuple freeze.

  LOGGING: DEBUG encode/decode `{path, arg_count}` only — never raw datagram bytes at INFO.

  Files: `backend/app/services/ardour_osc_codec.py` (new), `backend/app/services/ardour_osc_paths.py` (new), `backend/tests/test_ardour_osc_codec.py` (new)

- [x] Task 3: OSC transport + fake peer + feedback state reducer. Real mode: UDP send to host:osc_port; bind feedback listen port (refuse bind of 3819). Fake mode (`ARDOUR_COMPANION_FAKE=1`): in-process mock records outbound messages and emits deterministic feedback (transport, strips, names, heartbeat, master record). Feedback reducer updates observed transport/strips/selected/record; computes `feedback_age_ms` and `stale`. Connection states: `connecting` → `awaiting_feedback` → `connected` → `stale` / `error` / `disconnected`. Expose a clean close API for lifespan.

  LOGGING: INFO connect/disconnect `{host_class, osc_port, feedback_port, fake}` (host class = loopback|private|public — not full unexpected payloads); DEBUG inbound path names; WARN stale / bind failure codes; never raw OSC blobs at INFO.

  Files: `backend/app/services/ardour_osc_transport.py` (new), `backend/app/services/ardour_feedback_state.py` (new), `backend/app/services/ardour_osc_fake_peer.py` (new), `backend/tests/test_ardour_feedback_state.py` (new), `backend/tests/test_ardour_osc_transport.py` (new)

<!-- Commit checkpoint: tasks 1-3 -->

### Phase 2: Session + HTTP

- [x] Task 4: Process-memory companion session service (one session per process for ship-1). `connect` validates enabled + host allowlist + stores `control_permission`; **replace-if-connected** (disconnect then connect); sends locked `/set_surface`; returns `session.v1`. Commands return `{ accepted, status }` from feedback reducer — never claim success from send alone. Mutating commands refuse without permission (`ardour_control_permission_required`) or when disconnected. Record-arm POST `{ desired }` toggles only when observed ≠ desired; if observed unknown/stale → refuse `ardour_feedback_stale`. `disconnect` tears down sockets. Restart clears session (honest). Does not import SQLite, Composition, or `ai_agents/`. (depends on 1, 2, 3)

  LOGGING: INFO connect/disconnect/command `{command, accepted, connection_state}`; WARN permission/host/stale refusals with codes; DEBUG feedback-driven field updates `{field}` only.

  Files: `backend/app/services/ardour_companion_service.py` (new), `backend/tests/test_ardour_companion_service.py` (new)

- [x] Task 5: Router `ardour_companion.py` exposing locked HTTP surface. **`GET /ardour/companion/status` always 200** with `enabled` + status shell (`connection_state=disabled` when flag off). Mutating routes map `ardour_companion_disabled` → 403; payload/permission/host → 422; not connected → 409 as appropriate. Register router in `main.py`. **Wire lifespan shutdown** to disconnect/close companion sockets (mirror `cancel_adaptive_engine_tasks`). Document env keys in `.env.example`. API tests with fake mode: flag-off status 200, mutating 403, no Composition write, teardown closes listener. (depends on 3, 4)

  LOGGING: INFO route outcomes `{path, code, connection_state}`; INFO lifespan teardown; never request bodies with strip dumps at INFO.

  Files: `backend/app/routers/ardour_companion.py` (new), `backend/app/main.py`, `.env.example`, `backend/tests/test_ardour_companion_api.py` (new)

<!-- Commit checkpoint: tasks 4-5 -->

### Phase 3: UI + Lua + docs

- [x] Task 6: Frontend `ardourCompanionApi.js` + thin `ArdourCompanionPanel.jsx`; wire `{ id: 'ardour', label: 'Ardour' }` in `ComposerWorkspace.jsx` `TABS`. Fields: host, osc port, feedback port, **Allow DAW control** checkbox, Connect/Disconnect. Poll status every **1000 ms** while the Ardour tab is selected; **clear interval on unmount / tab leave**. Transport + locate + strip mixer controls gated by permission + `connection_state in {connected, awaiting_feedback}`. Status banner shows connection_state / feedback_age / stale. Opening the tab never connects and never writes the score. Disabled-flag empty state from `enabled=false`. Unit tests for permission gating + poll cleanup helpers. (depends on 5)

  LOGGING: frontend DEBUG/INFO via `createAppLogger('ardourCompanion')` with connection_state and command names only — never OSC payloads.

  Files: `frontend/src/api/ardourCompanionApi.js` (new), `frontend/src/components/ArdourCompanionPanel.jsx` (new), `frontend/src/components/ComposerWorkspace.jsx`, `frontend/src/utils/ardourCompanion/*.js` (new as needed), colocated `*.test.js`

- [x] Task 7: Ship `backend/examples/ardour/` Lua recipes for session ops not cleanly handled by OSC (e.g. add named location marker, list track count helper) with README stating operator install only — Mukit never remote-executes Lua. Write `docs/ardour-companion.md` (Ardour Preferences OSC enable, ports, feedback, Mukit env, permission model, fake mode, LV2 rejection rationale, honesty limits, **Compose/host networking**: companion targets the Ardour host — use `host.docker.internal` / gateway IP from containers; troubleshooting when feedback never arrives because `127.0.0.1` is the container itself). Extend `docs/daw-interoperability.md` with Ardour SMF/MusicXML/WAV import steps. Update `AGENTS.md` entry points + short README pointer. Mandatory `/aif-docs` checkpoint after implement. (depends on 1)

  LOGGING: n/a for docs; examples must not embed secrets.

  Files: `backend/examples/ardour/` (new), `docs/ardour-companion.md` (new), `docs/daw-interoperability.md`, `AGENTS.md`, `README.md` (short link)

<!-- Commit checkpoint: tasks 6-7 -->

### Phase 4: Acceptance tests

- [x] Task 8: Acceptance coverage: (1) codec round-trip + locked set_surface args/`8219`; (2) fake connect sends `/set_surface`; (3) play → feedback flips playing observed state; (4) fader → observed update from fake feedback; (5) flag off → status 200 `enabled=false`, mutating 403; (6) mutating without `control_permission` → refused; (7) public host refused when allowlist enforced; (8) disconnect clears session; (9) connect-replace tears down prior; (10) record-arm refuses when stale / toggles only when observed ≠ desired; (11) lifespan/teardown closes listener; (12) companion paths never mutate `projects.composition_json` / note events; (13) frontend permission gating + poll cleanup. No real Ardour process. (depends on 2–6)

  LOGGING: tests assert codes / observed fields only.

  Files: `backend/tests/test_ardour_companion_acceptance.py` (new), extend API/service/codec tests, frontend unit tests under ardour companion utils / panel helpers

<!-- Commit checkpoint: task 8 -->

## Acceptance Criteria

1. AI Composer can connect to Ardour (or fake peer) with configurable host, OSC port, and feedback port, and show connection status.
2. With explicit control permission, the Studio can play, stop, locate, query/control master record-arm safely, and adjust strip volume/pan/mute/solo.
3. Status follows **feedback-observed** state — commands report `accepted`, not assumed success; record-arm never blind-toggles when stale.
4. DAW control permission is explicit (env flag + connect checkbox/body); status GET remains readable when flag off.
5. Musical asset exchange uses existing MIDI/MusicXML/WAV export with documented Ardour import (incl. Compose host routing notes).
6. No LV2 component; no multi-DAW abstraction; no `composition.v5`; companion does not rewrite notes.
7. Mocked OSC tests pass without a real Ardour process; setup docs exist (`docs/ardour-companion.md`); lifespan closes UDP.

## Acceptance Criteria Mapping

| Criterion | Tasks |
|-----------|-------|
| Connect + host/OSC/feedback + status | 1, 3, 4, 5, 6 |
| Play/stop/locate/record/strip mixer | 2, 3, 4, 5, 6 |
| Feedback-observed (not assume success) | 3, 4, 8 |
| Explicit DAW control permission + status-when-disabled | 1, 4, 5, 6, 8 |
| File asset exchange + Ardour/Compose docs | 7 |
| No LV2 / no multi-DAW / no note rewrite | 1, 4, 7, 8 |
| Mocked OSC tests + setup docs + lifespan teardown | 2, 3, 5, 7, 8 |

## Implementation Notes for `/aif-implement`

1. Do not edit `ROADMAP.md`.
2. Prefer new `ardour_*` modules + thin wires into `main.py` / workspace; do not grow unrelated logic in `main.py` beyond router include + lifespan teardown.
3. Never invent `composition.v5`; never write notes from Ardour feedback.
4. Do not introduce a generic multi-DAW client or LV2 plugin in this milestone.
5. `ai_agents/` must not import companion OSC/session modules.
6. Keep default MIDI/MusicXML/WAV export unchanged; document Ardour import only.
7. After implementation, `/aif-docs` checkpoint is mandatory (`Docs: yes`).
8. Verbose logging with redaction; no OSC blob / event-array dumps at INFO.
9. Path map, `/set_surface` args, and feedback bitmask `8219` from Task 2 are locked — later tasks must not invent alternate Ardour path dialects without updating the plan.
10. Opening the Ardour tab must never auto-connect or auto-enable control permission.
11. Fake mode is required for CI; real Ardour is optional manual verification only.
12. Orthogonal to Tone.js transport, spatial scenes, and performance conductor — do not couple clocks in ship-1.
13. Do not add a soft `/ready` companion block in this milestone unless needed for a concrete bug; it was deferred in `/aif-improve`.
14. Reuse `ipaddress` allow semantics (execution-node spirit); do not invent a divergent host policy.
15. Docker: never assume container `127.0.0.1` reaches host Ardour — document gateway / `host.docker.internal`.
