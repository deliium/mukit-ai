[← Adaptive Music Engine](adaptive-music-engine.md) · [Back to README](../README.md)

# Adaptive Music Client

A game or installation drives one already-stored adaptive score from its own process. The Python package is `mukit-adaptive`. The TypeScript package is `@mukit/adaptive-music`. Both call only the public HTTP and WebSocket routes in [Adaptive Music Engine](adaptive-music-engine.md). They do not create a project, edit the score graph, or change the studio Adaptive tab.

The score must already exist. State ids and cue names in the phase recipe are names on that score, not a schema this client invents. Unity and Unreal packages are not included.

## Install

Python 3.12 or newer, from `clients/python`:

```bash
pip install -e ".[dev]"
```

Node.js 22 or newer, from `clients/typescript`:

```bash
npm install
npm run build
```

`npm install` is local. The quality gate does not run it. If `clients/typescript/node_modules` is missing while the TypeScript suite is selected, `scripts/run_tests.sh` exits with that install command and the path `clients/typescript/README.md`. Neither package is published.

## Environment

| Variable | Required | Role |
|----------|----------|------|
| `MUKIT_ADAPTIVE_BASE_URL` | no | Default `http://127.0.0.1:8000` |
| `MUKIT_ADAPTIVE_PROJECT_ID` | yes | Stored project |
| `MUKIT_ADAPTIVE_SCORE_ID` | yes | Stored adaptive score |
| `MUKIT_ADAPTIVE_DOCUMENT_REVISION` | yes | Expected score revision, integer `>= 1` |
| `MUKIT_ADAPTIVE_TOKEN` | no | Sent as `Authorization: Bearer` on HTTP and on the WebSocket handshake. Omit it when the server allows loopback |
| `MUKIT_ADAPTIVE_PHASES` | no | Recipe path. Otherwise the demo walks up to `clients/fixtures/game-phases.v1.json` |

The socket URL is the base URL with `http` replaced by `ws` or `https` by `wss`, plus `/adaptive/events` or `/adaptive/status` and `session_id`. The token is never appended to that URL.

A browser cannot set `Authorization` on the WebSocket handshake. This package does not put the token in the query string. The server closes a query `token` with `4401`. The shipped demo is a terminal process in each language.

## Start

`clients/fixtures/game-phases.v1.json` is the shipped recipe (`schema_version` `adaptive.client.phases.v1`). Its placeholder ids are `explore`, `danger`, `combat_state`, and `victory`. The cue name is `combat`. Those strings must already exist on the stored score. Pass `start.mapping` and `start.cues` into `start()` so the server can attach context and resolve `fire_cue`.

Python:

```python
from mukit_adaptive import MukitAdaptiveClient, load_phase_recipe

recipe = load_phase_recipe("clients/fixtures/game-phases.v1.json")
client = MukitAdaptiveClient(base_url, token=token)
session = client.start(
    project_id,
    score_id,
    expected_document_revision,
    mapping=recipe["start"]["mapping"],
    cues=recipe["start"]["cues"],
)
client.listen(on_ack, on_status, on_error)
```

TypeScript, from `clients/typescript` after `npm run build`:

```typescript
import { MukitAdaptiveClient, loadPhaseRecipe } from "./dist/index.js"

const recipe = loadPhaseRecipe("clients/fixtures/game-phases.v1.json")
const client = new MukitAdaptiveClient(baseUrl, token)
const session = await client.start(
  projectId,
  scoreId,
  expectedDocumentRevision,
  recipe.start?.mapping ?? null,
  recipe.start?.cues ?? null,
)
await client.listen(onAck, onStatus, onError)
```

`start(..., adopt_existing=False)` is the default. A `409` `engine_session_exists` is raised. Set the flag only when this process should attach to `detail.session_id`.

HTTP `200` with `disposition` `rejected` is a returned result. The client replaces `snapshot` and does not raise. Context `202` is success when `coalesced` is true and `applied` is false. The client does not poll for the drain. One HTTP `429` sleeps for `retry_after_ms`, capped at 1000 ms, and retries that call once. A second `429` raises. `stop()` treats `204` as success and does not decode a body. HTTP does not follow redirects.

## Phase keys

The demo and `apply_phase` / `applyPhase` use the recipe, in this order:

| Phase | Calls |
|-------|--------|
| exploration | `set_state` to the recipe state, `set_intensity(0.2)`, `send_context` `threat` `0.1` |
| danger | `set_state`, `set_intensity(0.55)`, `send_context` `threat` `0.6` |
| combat | `fire_cue("combat")`, `set_intensity(0.9)`, `send_context` `threat` `0.95` |
| victory | `set_state`, `set_intensity(0.35)`, `send_context` `threat` `0` |

If the recipe names `stinger_id`, `fire_stinger` runs after the state call. If `snapshot.context_attached` is false, context is skipped and the client logs INFO `context_skipped`.

| Key | Action |
|-----|--------|
| `1` | exploration |
| `2` | danger |
| `3` | combat |
| `4` | victory |
| `s` | print `runtime_state_id`, `bar`, `beat`, `intensity`, `transport`, `phase` |
| `r` | `reconnect_now()` for the same session |
| `q` | `close()` and exit. The server session stays so a later process can attach |

`--stop` calls `stop()` on quit instead, which `DELETE`s the session.

```bash
python -m mukit_adaptive.demo
node dist/demo.js
```

## Sockets

Status text is `adaptive.engine.session.v1` and replaces `snapshot`, including the first frame after the socket is accepted. Events text is `adaptive.engine.ack.v1` or `adaptive.engine.error.v1`. An error frame calls `on_error` and leaves `snapshot` unchanged. Socket text is that public document. The in-process server queue wrapper `{frame, body}` is not on the wire.

| Close or failure | Client |
|------------------|--------|
| `1000` after local `close()` or `stop()` | Stay stopped |
| `4401` | Stop with `engine_unauthorized`. Do not open a new session |
| `4404` | Stop with `engine_session_missing`. Do not `POST /adaptive/session` |
| `1013`, `1006`, `1001`, any other abnormal close, or a connect error | Back off, `GET` the same session, reopen both sockets |
| `GET` during reconnect returns `401` or `404` | Stop with that public code |
| Eight attempts | Stop with local code `engine_reconnect_exhausted` |

Backoff starts at 200 ms, doubles, and caps at 5000 ms. One reconnect runs at a time. A second feed closing during that reconnect does not start another cycle. Reconnect does not resend the last state, intensity, event, or context body. Press the phase key again when the player still wants a transition that was only queued.

## Logging

Python logger name `mukit_adaptive`. Level follows `LOG_LEVEL` (default `INFO`). TypeScript uses `MUKIT_ADAPTIVE_LOG_LEVEL`, then `LOG_LEVEL`, then `INFO`.

| Event | Level | Fields |
|-------|-------|--------|
| Session start, attach, stop | INFO | `session_id`, `clock_owner` |
| Command result | DEBUG | `session_id`, `kind`, `request_id`, `disposition`, `coalesced` |
| Snapshot warnings | DEBUG | `session_id` and the warning codes, when the list is non-empty |
| Rate limit retry | WARNING | `session_id`, `code`, `retry_after_ms` |
| Socket open and close | INFO | `session_id`, `feed` (`events` or `status`), `close_code` |
| Reconnect attempt | INFO | `session_id`, `attempt`, `delay_ms` |
| Reconnect stopped | ERROR | `session_id`, `code` |
| Phase applied | INFO | `phase`, `session_id`, `runtime_state_id` |
| Context skipped | INFO | `session_id`, `phase` |
| Dependency missing | ERROR | exception class, local code `engine_client_dependency_missing` |

The bearer token is never logged. The Authorization header, context `values`, mapping documents, cue lists, and the full snapshot JSON are never logged.

## Tests

Client tests use an injected transport. They do not bind a port.

| Command | Python client | TypeScript client |
|---------|---------------|-------------------|
| `./scripts/run_tests.sh` | yes | yes |
| `--backend-only` | yes | no |
| `--frontend-only` | no | yes |
| `--skip-backend` | no | yes, with the frontend step |
| `--skip-frontend` | yes, with the backend step | no |
| `--lint-only` | no | no |

Arguments after `--` go only to backend pytest. The Python client suite is a separate command and does not receive them. Example: `./scripts/run_tests.sh --backend-only -- tests/test_adaptive_client_fixtures.py` still runs the full `clients/python` suite afterward.
