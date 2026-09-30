# @mukit/adaptive-music

Async TypeScript client for one already-stored adaptive score. Method names match the Python package in [the adaptive music client contract](../../docs/adaptive-music-client.md). The score, its state ids, and the `combat` cue must already exist. This package does not author a score and does not open the studio.

Route and error tables: [Adaptive Music Engine](../../docs/adaptive-music-engine.md).

Node.js 22 or newer supplies global `fetch` and global `WebSocket`. The package is private and is not published to npm. It does not depend on the studio `frontend/` package.

## Install

```bash
cd clients/typescript
npm install
npm run build
```

`npm install` is required before `./scripts/run_tests.sh` can run this suite. The gate does not install it for you. If `node_modules` is missing, the script prints that command and points at this README.

## Environment

| Variable | Required | Role |
|----------|----------|------|
| `MUKIT_ADAPTIVE_BASE_URL` | no | Default `http://127.0.0.1:8000` |
| `MUKIT_ADAPTIVE_PROJECT_ID` | yes | Stored project |
| `MUKIT_ADAPTIVE_SCORE_ID` | yes | Stored adaptive score |
| `MUKIT_ADAPTIVE_DOCUMENT_REVISION` | yes | Expected score revision, integer `>= 1` |
| `MUKIT_ADAPTIVE_TOKEN` | no | Bearer token. Omit it when the server allows loopback |
| `MUKIT_ADAPTIVE_PHASES` | no | Phase recipe path. Default walks up to `clients/fixtures/game-phases.v1.json` |
| `MUKIT_ADAPTIVE_LOG_LEVEL` | no | Then `LOG_LEVEL`, then `INFO` |

The WebSocket URL replaces `http` with `ws` or `https` with `wss`. The token is never placed in that URL. A browser page cannot set `Authorization` on the handshake, so the demo is `node dist/demo.js`, not a page.

## Start

```typescript
import { MukitAdaptiveClient, loadPhaseRecipe } from "./dist/index.js"

const recipe = loadPhaseRecipe("clients/fixtures/game-phases.v1.json")
const client = new MukitAdaptiveClient("http://127.0.0.1:8000", null)
const session = await client.start(
  projectId,
  scoreId,
  expectedDocumentRevision,
  recipe.start?.mapping ?? null,
  recipe.start?.cues ?? null,
)
await client.listen(onAck, onStatus, onError)
```

Run that file with Node from `clients/typescript` after `npm run build`. HTTP `200` with `disposition` `rejected` is a returned result. It does not throw.

## Terminal demo

```bash
npm run build
node dist/demo.js
```

| Key | Action |
|-----|--------|
| `1` | exploration |
| `2` | danger |
| `3` | combat |
| `4` | victory |
| `s` | print `runtime_state_id`, `bar`, `beat`, `intensity`, `transport`, `phase` |
| `r` | reconnect the two sockets for the same session |
| `q` | close sockets and exit. The server session stays |

`node dist/demo.js --stop` calls `stop()` on quit, which deletes the server session.

Logs never include the bearer token, the Authorization header, context values, the mapping document, the cue list, or the full snapshot JSON.
