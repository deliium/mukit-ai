# mukit-adaptive

Synchronous Python client for one already-stored adaptive score. It speaks only the public routes in [the adaptive music client contract](../../docs/adaptive-music-client.md). The score, its state ids, and the `combat` cue must already exist. This package does not author a score and does not open the studio.

Route and error tables: [Adaptive Music Engine](../../docs/adaptive-music-engine.md).

## Install

Python 3.12 or newer. From this directory:

```bash
pip install -e ".[dev]"
```

`websockets` 13 or newer is the socket dependency. HTTP uses the standard library and does not follow redirects. The package is not published to PyPI.

## Environment

| Variable | Required | Role |
|----------|----------|------|
| `MUKIT_ADAPTIVE_BASE_URL` | no | Default `http://127.0.0.1:8000` |
| `MUKIT_ADAPTIVE_PROJECT_ID` | yes | Stored project |
| `MUKIT_ADAPTIVE_SCORE_ID` | yes | Stored adaptive score |
| `MUKIT_ADAPTIVE_DOCUMENT_REVISION` | yes | Expected score revision, integer `>= 1` |
| `MUKIT_ADAPTIVE_TOKEN` | no | Bearer token. Omit it when the server allows loopback |
| `MUKIT_ADAPTIVE_PHASES` | no | Phase recipe path. Default walks up to `clients/fixtures/game-phases.v1.json` |
| `LOG_LEVEL` | no | Logger `mukit_adaptive`. Default `INFO` |

The WebSocket URL replaces `http` with `ws` or `https` with `wss`. The token is never placed in that URL.

## Start

```python
from mukit_adaptive import MukitAdaptiveClient, load_phase_recipe

recipe = load_phase_recipe("clients/fixtures/game-phases.v1.json")
client = MukitAdaptiveClient("http://127.0.0.1:8000", token=None)
session = client.start(
    project_id,
    score_id,
    expected_document_revision,
    mapping=recipe["start"]["mapping"],
    cues=recipe["start"]["cues"],
)
client.listen(on_ack, on_status, on_error)
```

HTTP `200` with `disposition` `rejected` is a returned result. It does not raise.

## Terminal demo

```bash
python -m mukit_adaptive.demo
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

`python -m mukit_adaptive.demo --stop` calls `stop()` on quit, which deletes the server session.

Logs never include the bearer token, the Authorization header, context values, the mapping document, the cue list, or the full snapshot JSON.
