# Implementation Plan: V5 Adaptive Music Client SDKs

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-30

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- UI: terminal demo only (Python and TypeScript processes). The Adaptive tab and the rest of `frontend/` stay unchanged
- Refined: 2026-09-30 (`/aif-improve`). The demo start body registers the combat cue and a minimal context mapping. HTTP `200` with `disposition` `rejected` stays a result. Socket reads use `websockets.sync.client`, one reconnect at a time, and public frame bodies. The test gate keeps client pytest off of backend `--` args.
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing: yes`, `plan_logging: verbose`, `plan_docs: yes`, `plan_link_roadmap: true`, `plan_default_milestone: auto`). Every roadmap milestone is already checked, so this plan names the next client milestone and does not edit `ROADMAP.md`
- Scope: reference clients and one game-phase demo that speak only the published Adaptive Music Engine HTTP and WebSocket surface. They do not open the studio, author a score, or add a server route

## Roadmap Linkage
Milestone: "V5 Adaptive Music Client SDKs"
Rationale: The Adaptive Music Engine API is already checked. This plan is the Python and TypeScript clients a game calls, plus a terminal demo of exploration, danger, combat, and victory. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A developer can drive one already-stored adaptive score from an external process by following the client README and `docs/adaptive-music-client.md`. They do not need to read playback, musical-context, or score-authoring code.

Ship:

1. A small Python package, `mukit-adaptive`, with a synchronous client.
2. A small TypeScript package, `@mukit/adaptive-music`, with an async client.
3. Shared JSON fixtures for the public session, command result, acknowledgement, error, and the four game phases.
4. Reconnect handling for the events and status sockets.
5. An interactive terminal demo in each package that applies those four phases.
6. Package READMEs plus the contract page above.

Acceptance: both clients, against a fake transport in their own tests, can start a session, send state, intensity, a cue or stinger event, and a context sample, and apply one status snapshot and one `adaptive.engine.ack.v1`. A socket close `1013` reconnects, refreshes the snapshot with `GET`, and does not repeat the last POST. Closes `4401` and `4404` stop and do not open a new session. The shipped phase recipe maps `exploration`, `danger`, `combat`, and `victory` onto those calls. The demo process is a terminal loop, not a browser page and not a Unity or Unreal project. Client logs never contain the bearer token. Client source does not import `backend/app` or `frontend/src`. The backend fixture test parses the shared session JSON with `AdaptiveEngineSessionV1` and fails if the fixture gains `events`, `prompt`, `model_id`, `composition`, or `api_key`.

```text
Game loop / terminal demo
        ↓  documented client (no app.* import)
Python mukit_adaptive  |  TypeScript @mukit/adaptive-music
        ↓  Authorization: Bearer on HTTP and on the WS handshake
        ↓  ws/wss derived from the base URL; token never in the query
POST /adaptive/session
GET  /adaptive/session/{session_id}
POST .../state | /intensity | /event | /context
WS   /adaptive/events
WS   /adaptive/status
        ↓  abnormal close → backoff → GET snapshot → reopen sockets
        ↓  4401 / 4404 → stop; do not POST a replacement session
existing adaptive.engine.* server (unchanged)
```

**Terminology lock:** Product generation is **V5**. The wire documents stay `adaptive.engine.session.v1`, `adaptive.engine.ack.v1`, `adaptive.engine.error.v1`, and `adaptive.context.external.v1`. **Phase recipe** `adaptive.client.phases.v1` is a client file. It is not a server schema and not a score. **Reconnect** means reopening the two sockets for the same `session_id` after `GET` succeeds. It does not mean minting a session. **Adopt** means, only when the caller sets it, attaching to `detail.session_id` from `409` `engine_session_exists`. The default is to surface the conflict. **Game phase** is one of `exploration`, `danger`, `combat`, `victory`.

Predecessor: `.ai-factory/plans/v5-adaptive-music-engine-api.md`. Do not reopen its decisions (public routes, bearer-or-loopback auth, query `token` refused, context coalesce, no `advance` on the public API, no score writes, no `composition.v5`, no frontend Adaptive-tab changes). This plan does not change `backend/app/routers/adaptive_engine.py` or the engine services except the fixture-parse test and the docs rows named in Task 8.

## Approach Evaluation (locked)

### Part A — Where the clients live

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Helpers inside `frontend/src` and `backend/app`** | Short path to existing tests | A game would import studio modules, stores, or FastAPI | **Reject** |
| **B. Generated OpenAPI client as the only artifact** | Matches the wire models | Reconnect, phase recipes, and the demo are not in OpenAPI | **Reject** as the sole deliverable |
| **C. Hand-written packages under `clients/` that speak the public JSON and share fixtures** | Readable from the README. Fixtures are checked by one backend parse | Two implementations stay in step via fixtures, not via a shared runtime | **Accepted** |

### Part B — How the demo authenticates the sockets

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Browser page using the TypeScript client** | Familiar game UI | Browsers cannot set `Authorization` on the WebSocket handshake. The server closes query `token` with `4401` | **Reject** |
| **B. Change the server to accept a query token for the demo** | Would unlock a page | Reopens the locked auth rule | **Reject** |
| **C. Python and Node terminal processes, which can set the header** | Matches the server. No engine SDK | No mouse UI | **Accepted** |

### Part C — What reconnect is allowed to do

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Retry every close, including `4401` and `4404`, and POST `/adaptive/session` again** | The process keeps running | It hides a bad token and can hit `409` `engine_session_exists` or start a second score | **Reject** |
| **B. Replay the last state, stinger, or context POST after the socket returns** | The game might catch up | A stinger or cue would fire twice. A coalesced sample is not musical state until the server drains it | **Reject** |
| **C. On `1013`, `1006`, `1001`, or a failed connect: backoff, `GET` the same session, reopen both sockets, and leave commands to the caller. On `4401` or `4404`, or on `GET` `401`/`404`, stop** | Safe for stingers. The demo can send the current phase again | A bar transition that was only queued is observed on the next status frame, not by repeating the POST | **Accepted** |

### Part D — Who creates the score

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. The client calls `/projects` and adaptive-score commands** | A demo can bootstrap itself | The integrator must learn studio routes | **Reject** |
| **B. The caller passes `project_id`, `score_id`, `expected_document_revision`, and phase state ids** | The README stays on `/adaptive/*` | The score is authored beforehand | **Accepted** |

### Part E — Python call style

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Async-only client** | Matches `websockets` | A simple game loop must own an event loop | **Reject** |
| **B. Synchronous methods. Socket reads run on one background thread. `websockets` is imported only when listening starts** | `set_intensity(0.8)` is a straight call. HTTP tests need no extra package | Callbacks must return promptly | **Accepted** |

The default Python socket opener is `websockets.sync.client.connect` from `websockets` >= 13, imported only when `listen` uses that opener. The TypeScript client is async because `fetch` and `WebSocket` are async. Node 22 or newer is the documented runtime because it provides a global `WebSocket`.

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Wire contract | `docs/adaptive-music-engine.md` and `backend/app/adaptive_engine_schemas.py` | Clients copy those field names. They do not import the module |
| Routes | `POST/GET/DELETE /adaptive/session`, `POST .../state`, `.../intensity`, `.../event`, `.../context`, `WS /adaptive/events`, `WS /adaptive/status` | The only calls |
| Auth | `Authorization: Bearer` on HTTP and on the socket handshake. Query `token` is `4401` | Header only. Omit the header when the caller has no token |
| Errors | HTTP body `{"detail": AdaptiveEngineErrorV1}`. Socket errors are the same document | One parser |
| `409` `engine_session_exists` | `detail.session_id` and `detail.details.session_id` | Optional adopt |
| Context | Body is `adaptive.context.external.v1`. `202` when `coalesced` and not `applied`; otherwise `200` | Client returns that result. It does not wait for the ticker |
| Command rate | `429` `engine_rate_limited` with `retry_after_ms` | One honor of that delay, then raise |
| Studio | Adaptive tab, score CRUD, continuation | Untouched |
| Quality gate | `scripts/run_tests.sh` runs ESLint, pytest, and frontend `node --test` | Add the two client suites |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| External package | Nothing under `clients/` speaks `/adaptive/*` |
| Reconnect | The server closes sockets. No client backoff exists |
| Game demo | No exploration / danger / combat / victory driver |
| Fixture lock | Public examples live in the doc only. A client can drift from `AdaptiveEngineSessionV1` |
| Package docs | README has no client install path |

### Coupling risks to avoid

1. Importing `app`, `backend.app`, or anything under `frontend/src` from `clients/`.
2. Importing `mukit_adaptive` or `@mukit/adaptive-music` from `backend/app` or `frontend/src`. The fixture test reads JSON files only.
3. Adding Unity, Unreal, Godot, or other engine packages.
4. Adding a browser demo or a query-string token.
5. Calling `/projects`, `/ai`, `/llm`, continuation, or adaptive-score graph routes from the client.
6. Logging the token, the Authorization header, context `values`, mapping documents, or cue lists.
7. Replaying a POST after reconnect.
8. Editing `ROADMAP.md` or the engine router.
9. Choosing states, intensities, or stingers with `random`. Request ids are a counter, not a musical choice.
10. Writing `DATASET_ROOT` or `PROJECT_DB_PATH`.

## Scope And Decisions

### In scope
- `clients/python`, `clients/typescript`, `clients/fixtures`, client tests, one backend fixture-parse test, `scripts/run_tests.sh` so those tests run in the local gate, and the doc updates in Task 8.

### Out of scope
- New or changed `/adaptive/*` behavior.
- Score authoring, project CRUD, and studio panels.
- Unity, Unreal, and any other game-engine package.
- A browser page. Document why the handshake cannot carry `Authorization` there.
- Publishing to PyPI or npm.
- Persisting client sessions.
- `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layout**

```text
clients/fixtures/          # shared public JSON; both packages and one backend test read these files
clients/python/            # pyproject package mukit-adaptive, import mukit_adaptive
clients/typescript/        # private package @mukit/adaptive-music
```

`clients/python` uses a `src/` layout. Its runtime dependency is `websockets` (minimum 13). The default opener calls `websockets.sync.client.connect` and imports it only when `listen` uses that opener. HTTP uses `urllib.request`. Do not add `websockets` to `backend/requirements.txt`.

`clients/typescript` depends on `typescript` as a devDependency only. It uses global `fetch` and global `WebSocket`. It does not depend on the studio `frontend/package.json`.

**2. Python surface**

`MukitAdaptiveClient` methods:

| Method | Wire |
|--------|------|
| `start(project_id, score_id, expected_document_revision, mapping=None, cues=None)` | `POST /adaptive/session` |
| `attach(session_id)` | `GET /adaptive/session/{session_id}` |
| `stop()` | `DELETE` the current session, then stop sockets |
| `set_state(to_state_id, transition_id=None, request_id=None)` | `POST .../state` |
| `set_intensity(intensity, request_id=None)` | `POST .../intensity` |
| `fire_stinger(stinger_id, transition_id=None, request_id=None)` | `POST .../event` `kind: stinger` |
| `fire_cue(name, request_id=None)` | `POST .../event` `kind: cue` |
| `send_context(values, source_id=None, observed_at_ms=None)` | `POST .../context` |
| `listen(on_ack, on_status, on_error)` | Open both sockets |
| `reconnect_now()` | Same path as an abnormal close, for the demo key |
| `close()` | Close sockets. Does not `DELETE` unless `stop()` was used |

`snapshot` is the last `adaptive.engine.session.v1`. Command results and status frames replace it. `next_request_id()` returns `req_` plus a zero-padded lowercase counter (`^req_[a-z0-9]{1,32}$`). Callers may pass their own id if it matches that pattern.

`start(..., adopt_existing=False)`. When the flag is true and the status is `409` with code `engine_session_exists`, call `attach` on `detail.session_id`. Otherwise raise.

HTTP `429` with `retry_after_ms`: sleep once, capped at 1000 ms, and retry that call once. A second `429` raises. Tests inject the sleeper and do not wait on a wall clock.

HTTP `200` and context `202` return the command result and replace `snapshot` for `disposition` `committed`, `queued`, or `rejected`. `rejected` is not an exception. Raise on HTTP `4xx` and `5xx` only. Context `202` is success with `coalesced` true and `applied` false. The client does not poll for the drain.

`stop()` treats `204` as success and does not decode a body. HTTP calls do not follow redirects.

One lock covers HTTP calls and `snapshot` writes. Callbacks run after the snapshot is replaced, outside that lock.

**3. TypeScript surface**

The same operations, returning `Promise`s. `listen` registers listeners and starts both sockets. Names match the Python methods so the two READMEs tell one story.

**4. Models the client accepts**

Parse with extra keys rejected in Python (`extra` check) and with a known-key check in TypeScript. Required session fields are the ones in `AdaptiveEngineSessionV1`: `schema_version`, `session_id` (`^aeng_[0-9a-f]{8}$`), `project_id`, `score_id`, `clock_owner` (`engine` or `existing`), `transport` (`playing`, `held`, `stopped`), `runtime_state_id`, `bar`, `beat`, `intensity`, `phase` (`bed`, `phrase`, `stinger`), `active_stinger_id`, `pending_transition_id`, `pending_to_state_id`, `warnings`, `document_revision`, `context_attached`, `telemetry` (`command_count`, `rejected_count`, `coalesced_count`, `dropped_context_count`, `ack_count`).

Ack fields: `schema_version` `adaptive.engine.ack.v1`, `session_id`, `request_id`, `kind` (`state`, `intensity`, `stinger`, `cue`, `context`), `disposition` (`committed`, `queued`, `rejected`, `finished`), `runtime_state_id`, `detail_code`.

Error fields: `schema_version` `adaptive.engine.error.v1`, `code`, `message`, `session_id`, `retry_after_ms`, `details`. HTTP transport looks under `detail` for that object. Socket text is the public document itself. The server queue wrapper `{frame, body}` stays inside the process. `routers/adaptive_engine.py` sends `item["body"]`. Status text is `adaptive.engine.session.v1`, including the snapshot queued when the status socket is accepted. Events text is `adaptive.engine.ack.v1` or `adaptive.engine.error.v1`. Dispatch on `schema_version`. An error frame calls `on_error` and leaves `snapshot` unchanged.

Command result fields: `session`, `disposition` (`committed`, `queued`, `rejected`), `request_id`, `coalesced`, `applied`, `retry_after_ms`.

Context body sent by the client:

```json
{
  "schema_version": "adaptive.context.external.v1",
  "values": {"threat": 0.6}
}
```

`values` keys match `^[A-Za-z][A-Za-z0-9_.]{0,63}$`. Values are bool, finite number, string of length 1..80, or a list of 1..32 such strings. The client rejects a nested object before the request.

**5. Reconnect**

| Close or failure | Client |
|------------------|--------|
| `1000` after local `close()` or `stop()` | Stay stopped |
| `4401` | Stop. Code `engine_unauthorized`. Do not retry |
| `4404` | Stop. Code `engine_session_missing`. Do not `POST /adaptive/session` |
| `1013`, `1006`, `1001`, other abnormal, or a connect error | Count an attempt |
| `GET` during reconnect returns `401` or `404` | Stop with that public code |
| `GET` returns `200` | Replace `snapshot`, open both sockets, reset the attempt count |

Backoff starts at 200 ms, doubles, and caps at 5000 ms. Default max attempts is 8. The clock and sleeper are injected. No jitter. After the cap, stop with code `engine_reconnect_exhausted`. That code is local to the client. It is not a server code.

`close()` and `stop()` set an intentional-close flag before the sockets close. A following `1006` from that close stays stopped. One reconnect runs at a time. A second feed closing during that reconnect does not start another cycle. After `GET` `200`, both sockets open again.

Reconnect never resends the last state, intensity, event, or context body.

**6. Phase recipe**

`clients/fixtures/game-phases.v1.json` uses `schema_version` `adaptive.client.phases.v1`. Placeholder state ids are `explore`, `danger`, `combat_state`, and `victory`. The cue name for combat is `combat`. The README says these strings must already exist on the stored score. The demo reads this file or `MUKIT_ADAPTIVE_PHASES`.

The recipe `start` object is what the demo passes to `start()`:

```json
{
  "mapping": {
    "schema_version": "adaptive.context.mapping.v1",
    "baseline_state_id": "explore",
    "bindings": [],
    "state_rules": []
  },
  "cues": [
    {"name": "combat", "to_state_id": "combat_state"}
  ]
}
```

The server sets `context_attached` only when start includes `mapping`, and `fire_cue` resolves names only from the start `cues` list (`adaptive_engine_service.py`).

| Phase | Calls, in order |
|-------|-----------------|
| `exploration` | `set_state` to the recipe state, `set_intensity(0.2)`, `send_context` `threat` `0.1` |
| `danger` | `set_state` to the recipe state, `set_intensity(0.55)`, `send_context` `threat` `0.6` |
| `combat` | `fire_cue("combat")`, `set_intensity(0.9)`, `send_context` `threat` `0.95` |
| `victory` | `set_state` to the recipe state, `set_intensity(0.35)`, `send_context` `threat` `0` |

If the recipe names `stinger_id`, `fire_stinger` runs after the state call. If `snapshot.context_attached` is false, skip `send_context` and log INFO `context_skipped`. Do not call it and catch `409`.

`apply_phase(client, recipe, phase_name)` is the tested function. The input loop only maps keys.

**7. Terminal demo**

Both packages ship the same keys: `1` exploration, `2` danger, `3` combat, `4` victory, `s` print the current snapshot fields a player needs (`runtime_state_id`, `bar`, `beat`, `intensity`, `transport`, `phase`), `r` call `reconnect_now()`, `q` `close()` then exit. `q` does not `DELETE` the server session, so the integrator can attach again. A separate documented flag `--stop` calls `stop()` on quit.

`start()` sends the recipe `start.mapping` and `start.cues` with the project id, score id, and expected revision.

Required environment: `MUKIT_ADAPTIVE_BASE_URL` (default `http://127.0.0.1:8000`), `MUKIT_ADAPTIVE_PROJECT_ID`, `MUKIT_ADAPTIVE_SCORE_ID`, `MUKIT_ADAPTIVE_DOCUMENT_REVISION`. Optional `MUKIT_ADAPTIVE_TOKEN`. The WebSocket URL is the base URL with `http` replaced by `ws` or `https` by `wss`, plus the path. The token is never appended to that URL.

Python entry: `python -m mukit_adaptive.demo`. TypeScript entry: `node dist/demo.js` after `tsc`.

**8. Logging**

Python logger name `mukit_adaptive`. Level follows `LOG_LEVEL` (default INFO). TypeScript uses `MUKIT_ADAPTIVE_LOG_LEVEL`, then `LOG_LEVEL`, then INFO.

| Event | Level | Fields |
|-------|-------|--------|
| Session start, attach, stop | INFO | `session_id`, `clock_owner` |
| Command result | DEBUG | `session_id`, `kind`, `request_id`, `disposition`, `coalesced` |
| Snapshot warnings | DEBUG | `session_id` and the warning codes, when the list is non-empty |
| Rate limit retry | WARNING | `session_id`, `code`, `retry_after_ms` |
| Socket open and close | INFO | `session_id`, `feed` (`events` or `status`), `close_code` |
| Reconnect attempt | INFO | `session_id`, `attempt`, `delay_ms` |
| Reconnect stopped | ERROR | `session_id`, `code` |
| Phase applied | INFO | `phase` name, `session_id`, `runtime_state_id` |
| Context skipped | INFO | `session_id`, `phase` |
| Dependency missing (`websockets`) | ERROR | exception class, local code `engine_client_dependency_missing` |

Never log the token, the Authorization value, `values`, mapping, cues, or the full snapshot JSON.

**9. Tests**

Client tests use an injected transport. They do not bind a port and they do not import FastAPI.

`backend/tests/test_adaptive_client_fixtures.py` loads `clients/fixtures/session.v1.json`, `command-result.v1.json`, `ack.v1.json`, and `error.v1.json` and validates them with the existing Pydantic models. It asserts the session object has none of `events`, `prompt`, `model_id`, `composition`, `api_key`. It also validates `game-phases.v1.json` `start.cues` with `AdaptiveEngineCueV1` and `start.mapping` with the existing mapping parser. Resolve the fixtures directory from the repo root (`backend/tests` → parent → parent). Pytest cwd is `backend/`.

Each client test file scans its own package source and fails on `from app`, `import app`, `backend.app`, or `frontend/src`.

`scripts/run_tests.sh` runs the Python client pytest as its own command, with that package's `pythonpath` and with no forwarded pytest args, whenever the backend step runs (default and `--backend-only`). It skips that suite on `--frontend-only`, `--lint-only`, and `--skip-backend`. It runs `npm test` in `clients/typescript` whenever the frontend step runs (default and `--frontend-only`), and skips it on `--backend-only` and `--skip-frontend`. If that TypeScript step runs and `node_modules` is missing, the script exits with the install command `npm install` in that directory. It does not install during the gate. `./scripts/run_tests.sh --backend-only -- tests/test_arrangement_schemas.py` still passes those args only to backend pytest.

## Tasks

### Phase 1: Shared contract and Python client

- [x] Task 1: Public fixtures and phase recipe
  - Add `clients/fixtures/session.v1.json` matching the session example in `docs/adaptive-music-engine.md` (`schema_version` `adaptive.engine.session.v1`, `session_id` `aeng_0123abcd`, `clock_owner` `engine`, `transport` `playing`, intensity `0.2`, empty warnings, telemetry counters at `0`, `context_attached` true).
  - Add `clients/fixtures/command-result.v1.json` with `disposition` `committed`, `request_id` `req_now1`, `coalesced` false, `applied` true, and a nested session.
  - Add `clients/fixtures/ack.v1.json` (`adaptive.engine.ack.v1`, `kind` `state`, `disposition` `finished`).
  - Add `clients/fixtures/error.v1.json` (`adaptive.engine.error.v1`, `code` `engine_session_missing`).
  - Add `clients/fixtures/game-phases.v1.json` for the four phases and the `start` object in decision 6 (`mapping` plus the `combat` cue). State ids and the cue name are data in that file.
  - Add `backend/tests/test_adaptive_client_fixtures.py` that validates the first four files with `AdaptiveEngineSessionV1`, `AdaptiveEngineCommandResultV1`, `AdaptiveEngineAckV1`, and `AdaptiveEngineErrorV1`, and rejects forbidden session keys. Validate `start.cues` with `AdaptiveEngineCueV1` and `start.mapping` with the existing mapping parser. Load files from the repo root derived from the test file, because pytest cwd is `backend/`.
  - LOGGING: the fixture test does not log payloads. No client logger yet.
  - Files: `clients/fixtures/*.json`, `backend/tests/test_adaptive_client_fixtures.py`

- [x] Task 2: Python session client
  - Add `clients/python/pyproject.toml` (package `mukit-adaptive`, src layout, requires Python 3.12+, dependency `websockets>=13`) and `clients/python/src/mukit_adaptive/` (`client.py`, `models.py`, `errors.py`, `log.py`).
  - Implement `start`, `attach`, `stop`, base URL and token configuration, `adopt_existing`, and typed errors from HTTP `detail.code`. Derive the WebSocket base URL without putting the token in it. `stop()` accepts `204` with an empty body and does not decode JSON. HTTP does not follow redirects.
  - Unit tests with a fake HTTP transport: `201` stores the snapshot; missing token omits `Authorization`; present token sends `Bearer`; `401` raises `engine_unauthorized`; `409` without adopt raises and with adopt calls `GET`; `DELETE` `204` with an empty body returns without a JSON error. A `3xx` response raises and does not issue a second request.
  - Assert the package source does not import `app` or `frontend`.
  - LOGGING: INFO on start, attach, and stop with `session_id` and `clock_owner`. DEBUG must not include the token. A test sets the token to a unique string and asserts that string is absent from captured logs.
  - Files: `clients/python/pyproject.toml`, `clients/python/src/mukit_adaptive/`, `clients/python/tests/test_session.py`
  - Depends on Task 1 for the session fixture.

- [x] Task 3: Python commands and phase apply
  - Add `set_state`, `set_intensity`, `fire_stinger`, `fire_cue`, `send_context`, and `next_request_id`.
  - Map HTTP `200` and context `202` onto the command result for `disposition` `committed`, `queued`, and `rejected`. `rejected` returns the result and replaces `snapshot`. Raise on HTTP `4xx` and `5xx` only. Honor one `429` through an injected sleeper, then raise. Reject a nested context object locally.
  - Add `apply_phase` in `mukit_adaptive/phases.py` using decision 6, including the `context_attached` skip.
  - Tests: state body fields, intensity `0.8`, cue and stinger discriminators, context `202` does not issue a second request, HTTP `200` with `disposition` `rejected` does not raise, phase `combat` order is cue then intensity then context, and a false `context_attached` skips context.
  - LOGGING: DEBUG per command with `kind`, `request_id`, `disposition`, `coalesced`. DEBUG the warning codes when `snapshot.warnings` is non-empty. WARNING on the rate-limit retry with `retry_after_ms`. INFO on phase apply and on `context_skipped`. Tests assert `values` contents are absent from logs.
  - Files: `clients/python/src/mukit_adaptive/client.py`, `clients/python/src/mukit_adaptive/phases.py`, `clients/python/tests/test_commands.py`, `clients/python/tests/test_phases.py`
  - Depends on Task 2.

- [x] Task 4: Python sockets and reconnect
  - Add `clients/python/src/mukit_adaptive/reconnect.py` as a pure attempt counter and delay function (200 ms, double, cap 5000 ms, max 8).
  - `listen` starts one background thread. The default opener imports `websockets.sync.client.connect` lazily and raises `engine_client_dependency_missing` if it is missing. Tests inject a fake socket pair. HTTP calls and `snapshot` writes share one lock.
  - Implement decision 5, including `reconnect_now`, the intentional-close flag, and a single in-flight reconnect. Status text with `schema_version` `adaptive.engine.session.v1` replaces `snapshot`, including the first frame. Events text with `adaptive.engine.ack.v1` calls `on_ack`. Events text with `adaptive.engine.error.v1` calls `on_error` and leaves `snapshot` unchanged. Do not resend a stored POST body. A test closes both feeds at once and records one `GET`. Confirm the test double records no command calls during reconnect.
  - LOGGING: INFO on open, close (`feed`, `close_code`), and reconnect (`attempt`, `delay_ms`). ERROR when attempts are exhausted or the close is `4401` or `4404`, with `code` only.
  - Files: `clients/python/src/mukit_adaptive/reconnect.py`, `clients/python/src/mukit_adaptive/client.py`, `clients/python/tests/test_reconnect.py`
  - Depends on Task 2. Command tests in Task 3 stay HTTP-only.

### Phase 2: TypeScript client and demos

- [x] Task 5: TypeScript client
  - Add `clients/typescript/package.json` (private `@mukit/adaptive-music`, `type: module`), `tsconfig.json`, and `src/` modules mirroring the Python methods in decision 3. Compile with `tsc` to `dist/`. Tests run with `node --test` on the compiled test files, or with `node --experimental-strip-types` if that matches the Node on the machine and stays documented in the package README. Target Node 22+.
  - Cover the same cases as Tasks 2–4 with an injected `fetch` and an injected WebSocket fake: session, `204` stop, commands, HTTP `200` with `disposition` `rejected`, context `202`, phase order, status `adaptive.engine.session.v1` replacing `snapshot`, an events error frame that leaves `snapshot` unchanged, reconnect `1013` then one `GET` then both sockets reopened, a second close during that reconnect that does not start another cycle, and terminal `4401` / `4404`.
  - Package source must not import `frontend` or `backend`.
  - LOGGING: the same events and field lists as decision 8. The token fixture string is absent from captured lines. Context values are absent.
  - Files: `clients/typescript/package.json`, `clients/typescript/tsconfig.json`, `clients/typescript/src/`, `clients/typescript/test/`
  - Depends on Tasks 1 and 3 so the phase recipe and command order are already fixed.

- [x] Task 6: Terminal demos
  - Add `python -m mukit_adaptive.demo` and `clients/typescript/src/demo.ts`.
  - Read the environment from decision 7, load the phase recipe, `start` with `start.mapping` and `start.cues` from that file, `listen`, and apply keys `1`–`4`, `s`, `r`, and `q`. `--stop` calls `stop()` before exit. Default quit only `close()`s sockets.
  - Tests: the start call receives the recipe mapping and the `combat` cue. A fake stdin line for `combat` asserts `fire_cue` and `set_intensity` and no second session POST. A separate case asserts `q` does not call `stop` and `--stop` does.
  - LOGGING: INFO when a key applies a phase (`phase`, `session_id`). Do not log the token env var, the mapping document, or the cue list. If required env is missing, ERROR with the variable name only.
  - Files: `clients/python/src/mukit_adaptive/demo.py`, `clients/python/tests/test_demo.py`, `clients/typescript/src/demo.ts`, `clients/typescript/test/demo.test.ts`
  - Depends on Tasks 3, 4, and 5.

### Phase 3: Gate and documentation

- [x] Task 7: Wire client tests into the local gate
  - Extend `scripts/run_tests.sh` per decision 9. Python client pytest is a separate command with no forwarded pytest args. It runs with the backend step and skips with `--skip-backend`, `--frontend-only`, and `--lint-only`. TypeScript `npm test` runs with the frontend step and skips with `--skip-frontend` and `--backend-only`. Update the usage text.
  - Missing `node_modules` fails only when the TypeScript step is selected, with a one-line install hint, and does not run `npm install`.
  - Do not add `websockets` to `backend/requirements.txt`. Document the client install in the package README in Task 8, and have this task print that path from the script hint.
  - LOGGING: the script already prints START/OK lines. Add a START line per client suite. No token output.
  - Files: `scripts/run_tests.sh`
  - Depends on Tasks 4 and 5.

- [x] Task 8: Package and contract documentation
  - Add `clients/python/README.md`, `clients/typescript/README.md`, and `docs/adaptive-music-client.md`.
  - The contract page leads with install, the four environment variables, one start example in each language that passes the recipe `mapping` and `cues`, the phase keys, reconnect behavior from decision 5 (one reconnect at a time, no command replay), and the statement that the score must already exist. Link to `docs/adaptive-music-engine.md` for route and error tables. State that HTTP `200` with `disposition` `rejected` is a returned result. State that socket text is the public document, not the server queue wrapper. State that a browser cannot send the handshake header and that this package does not put the token in the query string. State that Unity and Unreal packages are not included. Mention which `scripts/run_tests.sh` flags include each client suite.
  - Add a README docs-table row, an `AGENTS.md` tree and entry-point note for `clients/`, a short `.ai-factory/ARCHITECTURE.md` row that client packages must not import `app` and `app` must not import `mukit_adaptive`, a `docs/CODEBASE_MAP.md` sentence, and a `docs/testing.md` subsection for the client suites.
  - Comment the demo variables in `.env.example` without a real token value.
  - Do not edit `ROADMAP.md`. Do not change engine routes.
  - LOGGING: the doc lists the fields from decision 8 and says the bearer token is never logged.
  - Files: `clients/python/README.md`, `clients/typescript/README.md`, `docs/adaptive-music-client.md`, `README.md`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, `docs/CODEBASE_MAP.md`, `docs/testing.md`, `.env.example`
  - Depends on Tasks 6 and 7 so commands and script names match the docs.

## Commit Plan
- **Commit 1** (after tasks 1–2): `feat: add adaptive engine client fixtures and the Python session client`
- **Commit 2** (after tasks 3–4): `feat: send adaptive engine commands and reconnect the Python client`
- **Commit 3** (after tasks 5–6): `feat: add the TypeScript adaptive client and terminal game-phase demos`
- **Commit 4** (after tasks 7–8): `docs: document the adaptive music client SDKs and run their tests`

## Risks
- A dropped socket during a queued bar transition is healed by the next status snapshot, not by repeating `set_state`. The demo README says to press the phase key again when the player still wants that transition.
- `adopt_existing` can attach to a session the process does not own. The default stays off.
- The TypeScript suite needs a local `npm install` before `scripts/run_tests.sh` can pass. Task 7 makes that failure explicit.
- `websockets.sync.client` is imported only when `listen` uses the default opener, so HTTP unit tests run without the wheel. The demo does need the install.
