# Adaptive Music Engine

A game or installation on the same machine, or on the local network with a
shared secret, can start one musical runtime and drive it without opening the
studio. The external session is `adaptive.engine.session.v1`. The stored graph
stays `adaptive.score.v1`. Playback stays `adaptive.playback.runtime.v1`.
Musical context stays `adaptive.musical_context.v1`.

This surface does not create scores, write `projects.composition_json`, or
write `adaptive_scores.body_json`. Generate, edit, agent, model, and
continuation routes are not part of it. Continuation lookahead stays on the
studio route.

The engine is one process, the same way playback is. More than one worker
would split the in-memory session. Do not put Redis in front of it.

A reverse proxy in front of this process must present
`Authorization: Bearer`. Loopback is decided from `request.client.host` after
the socket accept. `X-Forwarded-For` is ignored. A proxy that terminates TLS
is not loopback unless that peer really is `127.0.0.1` or `::1`.

## Routes

OpenAPI tag: `adaptive-engine`. Paths are not under `/projects` or `/ai`.

| Method | Path | Result |
|--------|------|--------|
| `POST` | `/adaptive/session` | `201` public snapshot |
| `GET` | `/adaptive/session/{session_id}` | Current snapshot. `404` `engine_session_missing` |
| `DELETE` | `/adaptive/session/{session_id}` | `204`. Stops the ticker. Stops playback and context only when this session started them |
| `POST` | `/adaptive/session/{session_id}/state` | `request_state` through the playback service |
| `POST` | `/adaptive/session/{session_id}/intensity` | `set_intensity` |
| `POST` | `/adaptive/session/{session_id}/event` | Stinger or named cue, then `request_state` |
| `POST` | `/adaptive/session/{session_id}/context` | `adaptive.context.external.v1` |
| `WS` | `/adaptive/events?session_id=` | `adaptive.engine.ack.v1` and `adaptive.engine.error.v1` |
| `WS` | `/adaptive/status?session_id=` | Throttled public snapshots |

There is no public `advance` or `observe` route. When this session starts
playback, `clock_owner` is `engine` and an in-process ticker calls `advance`
from the latched `composition.tempo`. When playback is already running,
`clock_owner` is `existing` and this session does not advance time.

`ADAPTIVE_ENGINE_TICKER=manual` leaves the task unstarted.
`advance_engine_clock` is a test seam on the service module, not an HTTP route.

## Auth

| Condition | Result |
|-----------|--------|
| `ADAPTIVE_ENGINE_TOKEN` unset and the peer is `127.0.0.1` or `::1` | Allowed |
| `ADAPTIVE_ENGINE_TOKEN` unset and the peer is anything else, including Starlette `testclient` | `401` `engine_unauthorized` |
| Token set and `Authorization: Bearer` matches | Allowed, including loopback and `testclient` |
| Header missing, not `Bearer`, or different | `401` `engine_unauthorized` |
| No peer (`request.client` missing) | `401` `engine_unauthorized` |
| Query `token` is present | `401` `engine_unauthorized`, even when the value is correct |

The compare hashes both strings with SHA-256 and then `hmac.compare_digest`.
The raw token is not stored on the session. WebSocket auth uses the same
header on the handshake. Auth failure closes with `4401`. An unknown session
closes with `4404`.

## Start

```json
{
  "project_id": "3f1c0a2e-7b14-4d0a-9c11-6b0e2a9d4f10",
  "score_id": "ascore_0123abcd4567ef90",
  "expected_document_revision": 1,
  "mapping": {
    "schema_version": "adaptive.context.mapping.v1",
    "baseline_state_id": "explore",
    "bindings": [],
    "state_rules": []
  },
  "cues": [
    {"name": "combat", "to_state_id": "tension", "transition_id": "tr-now"}
  ]
}
```

`201`:

```json
{
  "schema_version": "adaptive.engine.session.v1",
  "session_id": "aeng_0123abcd",
  "project_id": "3f1c0a2e-7b14-4d0a-9c11-6b0e2a9d4f10",
  "score_id": "ascore_0123abcd4567ef90",
  "clock_owner": "engine",
  "transport": "playing",
  "runtime_state_id": "explore",
  "bar": 1,
  "beat": 1,
  "intensity": 0.2,
  "phase": "bed",
  "active_stinger_id": null,
  "pending_transition_id": null,
  "pending_to_state_id": null,
  "warnings": [],
  "document_revision": 1,
  "context_attached": true,
  "telemetry": {
    "command_count": 0,
    "rejected_count": 0,
    "coalesced_count": 0,
    "dropped_context_count": 0,
    "ack_count": 0
  }
}
```

The snapshot has no note events, instructions, queue, prompt, model id, or
token. A second start for the same score is `409` `engine_session_exists`.

## Commands

State, intensity, and event share one token bucket. Past the burst they
return `429` `engine_rate_limited` with `retry_after_ms` and are not replaced.
A well-formed musical command is HTTP `200` even when `disposition` is
`rejected`. A bad state leaves transport `playing`.

The body is `session` (the snapshot above), plus:

```json
{
  "disposition": "committed",
  "request_id": "req_now1",
  "coalesced": false,
  "applied": true,
  "retry_after_ms": null
}
```

Event:

```json
{"kind": "stinger", "stinger_id": "stinger-hit", "transition_id": "tr-hit", "request_id": "req_sting1"}
```

```json
{"kind": "cue", "name": "combat", "request_id": "req_cue01"}
```

A stinger is a `request_state` on a transition whose realization already names
that stinger from the current runtime state. No such transition is `422`
`engine_stinger_unwired`. Cue names come from the start body. An unknown cue
is `422` `engine_event_unknown`.

Context past the burst is `202`, not `429`. The newest sample replaces the
single pending slot. It is not musical state until a later tick drains it.
The body is the same command result with:

```json
{
  "disposition": "queued",
  "request_id": null,
  "coalesced": true,
  "applied": false,
  "retry_after_ms": 50
}
```

No mapping on the session is `409` `engine_context_unmapped`.

## Acknowledgements

`WS /adaptive/events` sends `adaptive.engine.ack.v1` for the command and,
when a request was only queued, a later `finished` frame after the ticker
commits it.

```json
{
  "schema_version": "adaptive.engine.ack.v1",
  "session_id": "aeng_0123abcd",
  "request_id": "req_bar01",
  "kind": "state",
  "disposition": "finished",
  "runtime_state_id": "explore",
  "detail_code": null
}
```

A newer command replaces the open request. The replaced one is `rejected`
with `detail_code` `engine_superseded`. A full events queue drops the oldest
status frame first. It does not drop a queued acknowledgement or error. When
the queue is already full of those, the socket closes with `1013` after the
queued frames are sent.

`WS /adaptive/status` sends the
current snapshot on connect, then again when bar, beat, state, intensity,
transport, phase, stinger, or pending transition changes, and not more often
than `ADAPTIVE_ENGINE_STATUS_HZ`.

## Errors

`engine_state_rejected` is a `detail_code` on HTTP `200`, not its own status.
`engine_queue_full` is a public warning copied from playback
`playback_queue_full`. `engine_clock_not_owned` is logged when something asks
a bound session to advance time.

| Code | HTTP |
|------|------|
| `engine_unauthorized` | 401 |
| `engine_payload_invalid` | 422 |
| `engine_session_missing` | 404 |
| `engine_session_exists` | 409 |
| `engine_session_limit` | 429 |
| `engine_score_missing` | 404 |
| `engine_score_invalid` | 422 |
| `engine_revision_conflict` | 409 |
| `engine_context_unmapped` | 409 |
| `engine_context_busy` | 409, or a warning when start still binds |
| `engine_context_invalid` | 422 |
| `engine_stinger_unwired` | 422 |
| `engine_event_unknown` | 422 |
| `engine_rate_limited` | 429 |
| `engine_superseded` | ack detail |
| `engine_playback_stopped` | 409 |
| `engine_clock_not_owned` | not an HTTP command status |

`project_not_found` and `adaptive_score_not_found` become
`engine_score_missing`. `dangling_state_ref` on start becomes
`engine_score_invalid`. `adaptive_score_conflict` on start, and warning
`document_revision_conflict` after a command or sample, become
`engine_revision_conflict`.

## Environment

Invalid values log a warning with the key name and `invalid: true`, then keep
the default. See `.env.example`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `ADAPTIVE_ENGINE_TOKEN` | unset | When set, every peer must send `Authorization: Bearer`. When unset, only `127.0.0.1` and `::1` are allowed |
| `ADAPTIVE_ENGINE_TICK_MS` | 50 | Wall step. Floor 10, ceiling 1000 |
| `ADAPTIVE_ENGINE_TICKER` | `auto` | `auto` or `manual`. Anything else stays `auto` |
| `ADAPTIVE_ENGINE_CONTEXT_HZ` | 20 | Sustained context drains per second |
| `ADAPTIVE_ENGINE_CONTEXT_BURST` | 40 | Context bucket size |
| `ADAPTIVE_ENGINE_COMMAND_HZ` | 8 | State, intensity, and event |
| `ADAPTIVE_ENGINE_COMMAND_BURST` | 8 | Command bucket |
| `ADAPTIVE_ENGINE_STATUS_HZ` | 10 | Status socket publishes per second |
| `ADAPTIVE_ENGINE_MAX_SESSIONS` | 4 | Process cap |
| `ADAPTIVE_ENGINE_EVENT_QUEUE` | 32 | Outbound frames per socket |

## Logging

The logger name is the module name. Level follows `LOG_LEVEL`.

| Event | Level | Fields |
|-------|-------|--------|
| Auth refused | INFO | `peer_class` (`loopback` or `other`), `code` |
| Auth allowed | DEBUG | `peer_class` |
| Session start, bind, delete | INFO | `session_id`, `project_id`, `score_id`, `clock_owner` |
| Command | DEBUG | `session_id`, `kind`, `request_id`, `disposition` |
| Coalesce or rate limit | WARNING | `session_id`, `code`, `retry_after_ms` |
| Ticker failure | ERROR | `session_id`, exception class. The next step still runs |
| Socket accept and close | INFO | `session_id`, `close_code` |
| HTTP `4xx` | INFO | status and public `code` |

The bearer token, the Authorization header, command bodies, sample values,
mapping documents, and cue lists are never logged. Domain codes in the server
log are the code name only.
