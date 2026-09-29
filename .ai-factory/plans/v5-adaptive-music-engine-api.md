# Implementation Plan: V5 Adaptive Music Engine API

Branch: none (`git.create_branches: false`; plan authored on `main`)
Created: 2026-09-29

## Settings
- Testing: yes
- Logging: verbose
- Docs: yes
- Planning depth: ultra (post `/aif-improve` 2026-09-29)
- UI: no (the external client is HTTP and WebSocket; the Adaptive tab stays unchanged)
- Default prefs source: `.ai-factory/config.yaml` (`plan_testing` / `plan_logging` / `plan_docs` / `plan_link_roadmap`) plus the request for a versioned external session, state, intensity, events, context backpressure, local/network auth, runtime status, transition acknowledgements, and an integration client that never opens the frontend
- Scope: a versioned external control surface for one already-stored `adaptive.score.v1`. The stored graph stays `adaptive.score.v1`. Playback stays `adaptive.playback.runtime.v1`. Musical context stays `adaptive.musical_context.v1`. There is no `composition.v5`, no `adaptive.score.v2`, and no write to `projects.composition_json` or `adaptive_scores.body_json` from this surface.

## Roadmap Linkage
Milestone: "V5 Adaptive Music Engine API"
Rationale: The adaptive score graph, authoring, transition scheduler, intensity layers, playback clock, musical-context stream, and symbolic continuation are already checked on the roadmap. This plan is the external session those games and installations call. `ROADMAP.md` does not list this heading yet; `/aif-roadmap` owns adding it. This plan does not edit `ROADMAP.md`.

## Goal

A game, installation, or other process on the same machine or local network can start a musical runtime and drive it without opening the studio frontend.

Ship:

1. Versioned public documents for the session snapshot, command acknowledgements, and errors.
2. A bearer-or-loopback gate that never logs the secret and never trusts `X-Forwarded-For`.
3. A per-session token bucket and a one-slot context coalescer so high-frequency samples cannot grow a queue.
4. HTTP routes that start and stop a session and send state, intensity, stinger/cue, and context commands through the existing playback and musical-context services.
5. Two WebSocket feeds: one for acknowledgements and errors, one for throttled runtime status.
6. An integration test client that seeds a score through the existing project API, then speaks only `/adaptive/*`.

Acceptance: with `ADAPTIVE_ENGINE_TOKEN` set, `fastapi.testclient.TestClient` (no browser and no Adaptive-tab code) creates a project and score only as fixture setup, then `POST /adaptive/session` returns `201` with `transport` `playing` and `clock_owner` `engine`. `POST /adaptive/state` for the fixture's immediate transition returns `disposition` `committed` and the public `runtime_state_id` of the target state. The adaptive-score `document_revision` and the composition row are unchanged. `WS /adaptive/events` delivers one `adaptive.engine.ack.v1` whose `request_id` matches the POST. `POST /adaptive/intensity` with `0.8` echoes intensity `0.8`. With `ADAPTIVE_ENGINE_TICKER=manual`, a burst of context samples past the configured burst returns `202` with `coalesced` true and `applied` false, and the burst itself does not call the context sampler. `advance_engine_clock` then drains one sample. `DELETE /adaptive/session/{session_id}` returns `204`, and a later state POST returns `404` `engine_session_missing`. The session JSON has no `events`, `prompt`, `model_id`, `composition`, or `api_key` field. A request without the bearer token returns `401`. The token string never appears in captured logs.

```text
Game / installation / TestClient
        ↓  Authorization: Bearer, direct peer only
POST /adaptive/session
        ↓
services/adaptive_engine_service.py
        ↓  start or bind playback; never write the score
adaptive.playback.runtime.v1
        ↓
POST /state | /intensity | /event | /context
        ↓  context over rate → one-slot coalesce
existing command / sample functions
        ↓
WS /adaptive/events   (acks and errors)
WS /adaptive/status   (throttled snapshot)
```

**Terminology lock:** Product generation is **V5**. An **engine session** is `adaptive.engine.session.v1` in process memory. It is not `adaptive.musical_context.v1` and not the continuation memory. **Clock owner** `engine` means this session started playback in `simulation` and is the only advancer. **Clock owner** `existing` means playback was already running; this session sends commands and does not advance time. **Disposition** is the immediate result of one POST: `committed`, `queued`, or `rejected`. **Ack** is `adaptive.engine.ack.v1`, once on the HTTP response and again on the events socket when a later tick commits or finishes work that was only queued. **Coalesce** means the newest unread context sample replaces the previous one. **Peer** is `request.client.host` after the socket accept. A reverse-proxy address is not a peer. **Stinger fire** is a `request_state` on an existing transition whose realization already names that stinger. There is no new playback op.

Predecessors: `.ai-factory/plans/v5-adaptive-score-domain-model.md`, `.ai-factory/plans/v5-adaptive-score-authoring.md`, `.ai-factory/plans/v5-adaptive-score-transition-engine.md`, `.ai-factory/plans/v5-adaptive-score-intensity-layers.md`, `.ai-factory/plans/v5-adaptive-score-playback-runtime.md`, `.ai-factory/plans/v5-runtime-musical-context.md`, and `.ai-factory/plans/v5-runtime-symbolic-continuation.md`. Do not reopen their decisions (reference-only score material, playback is process memory, invalid playback state requests leave transport playing, context samples emit existing playback commands, continuation is a session buffer, no note events on the score, no `composition.v5`).

## Approach Evaluation (locked)

### Part A — What the external client is allowed to call

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Publish the existing `/projects/{id}/adaptive-scores/...` routes as the game API** | Already implemented | Those routes expose graph commands, layer previews, continuation buffers, and collaboration actor checks. A game can PUT a score | **Reject** |
| **B. New `/adaptive/*` routes that copy the clock into a second runtime** | Isolation from the studio | Two clocks diverge. Transition rules would be reimplemented | **Reject** |
| **C. New `/adaptive/*` routes that only call the existing playback and musical-context services** | One musical runtime. The public JSON is a projection | The engine must refuse graph writes and model routes explicitly | **Accepted** |

### Part B — Who advances time when the frontend is absent

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Require the studio SPA to keep sending `advance`** | No new ticker | The acceptance client has no frontend, so bars never move | **Reject** |
| **B. Expose `advance` and `observe` on the public API** | Games can sync to their audio thread | Those payloads are the internal clock. A mistaken client seeks the session | **Reject** |
| **C. When this session starts playback, an in-process ticker issues `advance` from the latched tempo. When playback already exists, do not tick** | The test client hears state commits. A live SPA is not double-advanced | A bar-quantized transition waits for the ticker. Immediate transitions still commit inside the current command via `_consume_due` | **Accepted** |

### Part C — How a stinger is fired

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Add `request_stinger` to `AdaptivePlaybackCommand`** | One call | Changes the locked playback command union | **Reject** |
| **B. POST the raw score and hope the studio notices** | No playback change | Writes the graph and does not play | **Reject** |
| **C. Resolve one transition from the current runtime state whose realization `kind` is `stinger` and whose `stinger_id` matches, then send `request_state`** | Uses the scheduler, interrupt policy, and quantization already on the score | A stinger with no incoming transition returns `engine_stinger_unwired` | **Accepted** |

### Part D — Authentication for a local or LAN host

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Reuse `X-Actor-Id` and collaboration roles** | Already enforced on studio routes | Games are not project members. The flag is off by default, which would leave the route open | **Reject** |
| **B. Always require a bearer token, including a fresh checkout** | Strong | A missing env breaks the local acceptance test unless every developer sets a secret | **Reject** |
| **C. Token if `ADAPTIVE_ENGINE_TOKEN` is set, including loopback. If unset, accept only the direct loopback peer** | LAN use needs an explicit secret. A laptop without a secret is not a network API | A proxy that terminates TLS must not be treated as loopback unless the peer really is | **Accepted** |

### Part E — High-frequency context

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **A. Unbounded queue drained by the ticker** | No dropped samples | A 1 kHz sender grows memory for the life of the session | **Reject** |
| **B. `429` on every sample over the burst, dropping the body** | Simple | The newest danger value is the one that matters, and it is the one dropped | **Reject** |
| **C. Token bucket. Past the burst, replace a single pending sample and return `202` `coalesced`. State, intensity, and event past their bucket return `429` and are not replaced** | Musical commands stay explicit. Context stays bounded | A coalesced sample is not musical state until the ticker drains it | **Accepted** |

## Audit Summary (current state)

### What already exists (reuse)

| Area | Today | Reuse |
|------|-------|-------|
| Playback | `start_adaptive_playback`, `command_adaptive_playback`, `get_adaptive_playback`, `stop_adaptive_playback` | The only clock. Commands stay `request_state`, `set_intensity`, `advance`, `stop` |
| Immediate commit | `_request_state` calls `_consume_due` when `boundary_tick` is already due | An `immediate` transition can commit inside the POST. A `bar` transition only stores `pending` and leaves `last_event` unchanged |
| Revision after start | `command_adaptive_playback` and `sample_adaptive_musical_context` return the snapshot with warning `document_revision_conflict` and do not raise | The engine maps that warning to `409`. Start-time mismatch still raises `adaptive_score_conflict` before the registry write |
| Simulation advance | `_advance` warns `advance_ignored` unless mode is `simulation` | The ticker and `advance_engine_clock` run only when `clock_owner` is `engine` |
| Test client peer | Starlette `TestClient` uses host `testclient` | That host is not loopback. Integration tests send the bearer token |
| Invalid state | `request_rejected` leaves transport `playing` unless already `held` | Public disposition `rejected` with the same transport rule |
| Context sample | `adaptive.context.external.v1` and `sample_adaptive_musical_context` | The only context write. Mapping stays `adaptive.context.mapping.v1` |
| Stingers | `AdaptiveScoreStingerV1` plus transition realization `kind: stinger` | Lookup only. No new graph entity |
| Layer map | Playback already maps intensity to layers | Do not add a public layer route |
| Error map | `AdaptiveScoreError` and `map_adaptive_score_error_to_http` | Translate to the public code list below. Do not return the internal body unchanged when it contains a field path into a score document |
| Process memory | Playback, context, and continuation registries keyed by `(project_id, score_id)` | Engine registry is a fourth sibling. Restart drops it |
| HTTP app | `app.include_router` in `backend/app/main.py` | Add one router. Do not mount it under `/ai` or `/llm` |
| WebSocket stack | FastAPI/Starlette plus `uvicorn[standard]` | No new dependency |
| Test client | `fastapi.testclient.TestClient`, including `websocket_connect` | Integration tests |
| Import audit style | `ast.parse` guards in `backend/tests/test_adaptive_musical_context.py` | Same style for the engine modules |

### Gaps (must build)

| Gap | Notes |
|-----|-------|
| Public session | Nothing binds a non-studio client to playback under `/adaptive/*` |
| Auth | No route compares a bearer secret or refuses a non-loopback peer |
| Backpressure | Context samples are accepted on every POST |
| Server clock | Nothing calls `advance` unless a client does |
| Acks | `last_event` is on the playback snapshot. Nothing correlates a caller `request_id` to a later commit |
| Public projection | Playback snapshots include instructions a game does not need. Continuation buffers include notes. Neither may be the external body |
| Contract doc | `docs/` has no external engine page |

### Coupling risks to avoid

1. Importing `ai_runtime`, `ai_agents`, `fake_llm`, `llm_chat_client`, `music_transformer`, `symbolic_composition_generate`, or any `/llm` handler from the engine router, service, auth, or backpressure modules.
2. Importing continuation modules or calling `maintain_adaptive_runtime_continuation`. Lookahead stays on the studio route.
3. Calling `apply_adaptive_score_command`, `replace_adaptive_score`, `create_adaptive_score`, or `schedule_adaptive_transition` directly. State changes go through `command_adaptive_playback`.
4. Adding an op to `AdaptivePlaybackCommand`.
5. Putting note events, prompts, model ids, or the bearer token on the public snapshot, the ack, or a log record.
6. Trusting `X-Forwarded-For`, query-string tokens, or a WebSocket subprotocol for the secret.
7. Letting `ai_agents/` import the new engine modules.
8. Editing `ROADMAP.md`.
9. Changing `horizon_end_tick`, continuation windows, or musical-context hysteresis.
10. Starting, seeking, or stopping Tone. This plan does not touch `frontend/`.
11. Writing `DATASET_ROOT` or `PROJECT_DB_PATH`.
12. Using `random` to choose states, intensities, or stingers.

## Scope And Decisions

### In scope
- Public schemas, settings, auth, backpressure, the session service, the HTTP router, both sockets, integration tests, and the doc updates in Task 8.

### Out of scope
- A studio panel, a React client, or changes under `frontend/`.
- Persisting engine sessions in SQLite. No Alembic revision.
- Creating or editing scores, states, transitions, layers, or compositions.
- Multi-tenant accounts, OAuth, or per-project share links. The token is a host secret for this process.
- Calling a symbolic model, an LLM, continuation maintain, or Fill ahead.
- Proxy protocol support. Document that a reverse proxy in front of this process must present the token and must not rely on loopback inference.
- Editing `ROADMAP.md`.

### Architecture decisions (locked)

**1. Layering**

```text
HTTP/WS routers/adaptive_engine.py
        ↓  auth + public error mapping
services/adaptive_engine_service.py
        ↓  read score to resolve a stinger transition; never write it
        ↓  start/command/get/stop playback
        ↓  start/sample/get/stop musical context
services/adaptive_engine_auth.py          (pure compare + peer class)
services/adaptive_engine_backpressure.py  (pure bucket + one slot)
```

`ai_agents/` does not import these modules. The auth and backpressure modules do not import FastAPI, SQLite, playback, or an LLM client. The service may import the playback service, the musical-context service, and `get_adaptive_score`. It may not import `adaptive_playback.py` (the pure clock), continuation modules, or score command appliers.

**2. Routes**

| Method | Path | Role |
|--------|------|------|
| `POST` | `/adaptive/session` | Start or refuse. `201` with the public snapshot |
| `GET` | `/adaptive/session/{session_id}` | Poll snapshot. `404` `engine_session_missing` |
| `DELETE` | `/adaptive/session/{session_id}` | `204`. Stops the ticker. Stops playback and context only if this session started them |
| `POST` | `/adaptive/session/{session_id}/state` | `request_state` |
| `POST` | `/adaptive/session/{session_id}/intensity` | `set_intensity` |
| `POST` | `/adaptive/session/{session_id}/event` | Stinger or named cue |
| `POST` | `/adaptive/session/{session_id}/context` | `adaptive.context.external.v1` |
| `WS` | `/adaptive/events?session_id=` | Acks and errors |
| `WS` | `/adaptive/status?session_id=` | Throttled snapshots |

The paths are not nested under `/projects` or `/ai`. OpenAPI tag is `adaptive-engine`.

**3. Auth**

| Condition | Result |
|-----------|--------|
| `ADAPTIVE_ENGINE_TOKEN` unset and peer is `127.0.0.1` or `::1` | Allowed |
| `ADAPTIVE_ENGINE_TOKEN` unset and peer is anything else | `401` `engine_unauthorized` |
| Token set and `Authorization: Bearer` matches by SHA-256 then `hmac.compare_digest` | Allowed, including loopback |
| Token set and header missing or different | `401` `engine_unauthorized` |
| Header is not `Bearer` | `401` `engine_unauthorized` |
| `request.client` is missing, so there is no peer | `401` `engine_unauthorized` |
| Peer is `testclient` and the token is unset | `401` `engine_unauthorized` |

`127.0.0.1` and `::1` are the only loopback peers. `testclient` is the Starlette test host and uses the same rule as any other non-loopback peer. Compare hashes so a length mismatch is not a shortcut. Never store the raw token on the session. Never log the header. WebSocket auth is the same header on the handshake. HTTP middleware does not run for WebSocket, so the endpoint checks auth itself. Query `token` is rejected with `401` even if the value is correct.

**4. Session**

`AdaptiveEngineSessionV1` is `extra="forbid"`. `schema_version` is `adaptive.engine.session.v1`.

| Field | Rule |
|-------|------|
| `session_id` | `^aeng_[0-9a-f]{8}$`, minted once |
| `project_id` / `score_id` | Copied from the start body. The score must already exist |
| `clock_owner` | `engine` or `existing` |
| `transport` | Copied from playback: `playing`, `held`, `stopped` |
| `runtime_state_id` | From playback |
| `bar` / `beat` | From playback. Ints `>= 1` |
| `intensity` | Float `0..1` from playback |
| `phase` | `bed`, `phrase`, or `stinger` |
| `active_stinger_id` | Or null |
| `pending_transition_id` / `pending_to_state_id` | Or both null |
| `warnings` | Public codes only, max 8, first-seen order |
| `document_revision` | Echo of the score at bind. Start compares it before any registry write and `start_adaptive_playback` raises `adaptive_score_conflict` on mismatch. A later command sees warning `document_revision_conflict` on the returned snapshot, because the playback and context services do not raise. Map that warning to `409` `engine_revision_conflict` and do not read `last_event` for disposition |
| `context_attached` | Bool |
| `telemetry` | `command_count`, `rejected_count`, `coalesced_count`, `dropped_context_count`, `ack_count` |

The snapshot has no `events`, `instructions`, `queue`, buffer, mapping document, or harmony.

Start body `AdaptiveEngineStartV1`, `extra="forbid"`:

| Field | Rule |
|-------|------|
| `project_id` | Existing project |
| `score_id` | Existing score on that project |
| `expected_document_revision` | Int `>= 1` |
| `mapping` | Optional `adaptive.context.mapping.v1`. Parsed with the existing parser |
| `cues` | Optional list, max 32. Each item is `name` (1..64, `^[a-z0-9_-]+$`), `to_state_id`, optional `transition_id` |

One engine session per `(project_id, score_id)`. A second start returns `409` `engine_session_exists` and the existing `session_id`. `ADAPTIVE_ENGINE_MAX_SESSIONS` default `4` beyond the cap returns `429` `engine_session_limit`.

Start sequence:

1. Load the score. Revision mismatch is `409` before any registry write.
2. If playback is absent, `start_adaptive_playback` with `mode: simulation` and `clock_owner: engine`.
3. If playback is present, bind it, set `clock_owner: existing`, and do not start a ticker.
4. If `mapping` is present and a context session is absent, start it. If one is already running, set `context_attached` false and add warning `engine_context_busy`.
5. Mint the session. Start the ticker task only when `clock_owner` is `engine` and `ADAPTIVE_ENGINE_TICKER` is `auto`. `manual` leaves the task unstarted. `advance_engine_clock` is the only stepper in that mode.

Delete sequence: cancel the ticker, drop the coalescer, stop context only if this session started it, stop playback only if `clock_owner` was `engine`.

**5. Commands**

State body: `to_state_id`, optional `transition_id`, optional `request_id` matching `^req_[a-z0-9]{1,32}$`.

Intensity body: `intensity` float `0..1`, optional `request_id`. Bool is rejected.

Event body, discriminator `kind`:

| Kind | Fields | Behavior |
|------|--------|----------|
| `stinger` | `stinger_id`, optional `transition_id` | Find transitions whose `from_state_id` is the current runtime state and whose realization is that stinger. Zero matches: `422` `engine_stinger_unwired` and do not command playback. One match: `request_state` with that transition. Several matches require `transition_id`, and that id must be a member of the match set. An omitted or unknown id is `422` `engine_stinger_unwired` with `details.count`. A match the scheduler then rejects stays HTTP `200` with disposition `rejected` and transport `playing`. |
| `cue` | `name` | The name must be on the session cue list. Translate to `request_state`. Unknown name: `422` `engine_event_unknown`. Do not read `transition.cue_label`. That field is a rehearsal-marker label for quantization `cue`. |

Context body: pass through `parse_adaptive_context_external`. No mapping on the session: `409` `engine_context_unmapped`. A context snapshot that carries `document_revision_conflict` is `409` `engine_revision_conflict`. The context service already issues any playback commands inside `sample_adaptive_musical_context`. The engine does not run those emitted commands through the command bucket again.

HTTP command body is `AdaptiveEngineCommandResultV1`: the public session, `disposition`, `request_id`, `coalesced`, `applied`, and optional `retry_after_ms`.

Disposition compares the playback snapshot before and after the command. If the after-snapshot warnings include `document_revision_conflict`, return `409` and skip this table.

| After the command | `disposition` |
|-------------------|---------------|
| `last_event` changed to `state_committed`, `stinger_started`, or `intensity_changed` | `committed` |
| `last_event` changed to `request_queued`, or `pending` went from empty to set while `last_event` did not become a commit event | `queued` |
| `last_event` changed to `request_rejected` | `rejected`, `detail_code` `engine_state_rejected` |

A `bar` transition stores `pending` and leaves `last_event` at its previous value. That row is `queued`. Copy playback warning `playback_queue_full` to public warning `engine_queue_full`.

HTTP status for a well-formed musical command is `200` even when disposition is `rejected`, matching the clock rule that a bad state leaves transport playing. Malformed JSON is `422` `engine_payload_invalid`. A coalesced context POST is `202` with `coalesced` true and `applied` false. The sample stays in the one-slot until a later tick drains it.

**6. Ticker**

Settings in `adaptive_engine_settings.py`. Invalid env values log a warning and keep the default.

| Setting | Default | Meaning |
|---------|---------|---------|
| `ADAPTIVE_ENGINE_TICK_MS` | 50 | Wall step. Floor 10, ceiling 1000 |
| `ADAPTIVE_ENGINE_TICKER` | `auto` | `auto` starts `asyncio.create_task`. `manual` does not. Any other value logs a warning and keeps `auto` |
| `ADAPTIVE_ENGINE_CONTEXT_HZ` | 20 | Sustained context drains per second |
| `ADAPTIVE_ENGINE_CONTEXT_BURST` | 40 | Bucket size |
| `ADAPTIVE_ENGINE_COMMAND_HZ` | 8 | State, intensity, and event |
| `ADAPTIVE_ENGINE_COMMAND_BURST` | 8 | Command bucket |
| `ADAPTIVE_ENGINE_STATUS_HZ` | 10 | Max status-socket publishes per second |
| `ADAPTIVE_ENGINE_MAX_SESSIONS` | 4 | Process cap |
| `ADAPTIVE_ENGINE_EVENT_QUEUE` | 32 | Outbound ack queue per socket |

Latch `composition.tempo` (one int BPM) and `ticks_per_quarter` once at start. Each tick adds `tempo * ticks_per_quarter * tick_ms` into a remainder numerator over `60000`, commands `advance` with the floored ticks, and keeps the remainder. A zero floor waits for the next tick. The ticker never calls a model. It runs only when `clock_owner` is `engine`, because `advance` is ignored unless playback mode is `simulation`.

After each tick, if the context bucket allows and the one-slot holds a sample, call `sample_adaptive_musical_context` once and clear the slot.

`advance_engine_clock(session_id, steps)` runs that step function `steps` times with no sleep when `clock_owner` is `engine`. When the owner is `existing`, it returns the current snapshot and logs warning code `engine_clock_not_owned`. It is a test seam on the service module. It is not an HTTP route. The existing lifespan shutdown in `backend/app/main.py` calls a registry cancel so `auto` tasks end before the process, and before `TestClient`, exits.

**7. Sockets**

`/adaptive/status` sends the current snapshot on connect, then a new one when any of `bar`, `beat`, `runtime_state_id`, `intensity`, `transport`, `phase`, `active_stinger_id`, `pending_transition_id` changes, and not more often than `STATUS_HZ`.

`/adaptive/events` sends `adaptive.engine.ack.v1`:

| Field | Rule |
|-------|------|
| `schema_version` | `adaptive.engine.ack.v1` |
| `session_id` | Engine session |
| `request_id` | Echo, or null for ticker-originated finish events that still match the open request |
| `kind` | `state`, `intensity`, `stinger`, `cue`, `context` |
| `disposition` | `committed`, `queued`, `rejected`, `finished` |
| `runtime_state_id` | After the command |
| `detail_code` | Public code or null |

A follow-up ack with `finished` is sent when a later tick moves `last_event` to `state_committed`, `stinger_started`, or `stinger_finished` for a request that was `queued`. One in-flight request id is enough; a newer command replaces the open id and the replaced one gets `disposition` `rejected` and `detail_code` `engine_superseded`.

Errors on the socket use `adaptive.engine.error.v1`: `code`, `message` from the closed table, no stack, no token. Queue cap: drop oldest status duplicates first. Do not drop an undelivered ack or error. If the cap is full of those, close with `1013`.

Auth failure closes with `4401`. Unknown session closes with `4404`.

**8. Public error codes**

`engine_unauthorized`, `engine_payload_invalid`, `engine_session_missing`, `engine_session_exists`, `engine_session_limit`, `engine_score_missing`, `engine_score_invalid`, `engine_revision_conflict`, `engine_context_unmapped`, `engine_context_busy`, `engine_context_invalid`, `engine_stinger_unwired`, `engine_event_unknown`, `engine_rate_limited`, `engine_superseded`, `engine_playback_stopped`, `engine_clock_not_owned`.

`engine_state_rejected` is a `detail_code` on HTTP `200`, not its own status. `engine_queue_full` is a public warning copied from playback warning `playback_queue_full`.

| Internal code | Public result |
|---------------|---------------|
| `project_not_found`, `adaptive_score_not_found` | `404` `engine_score_missing` |
| `dangling_state_ref` on start | `422` `engine_score_invalid` |
| `adaptive_score_conflict` on start | `409` `engine_revision_conflict` |
| warning `document_revision_conflict` after a command or a sample | `409` `engine_revision_conflict` |

`engine_rate_limited` is HTTP `429` with `retry_after_ms` and applies to state, intensity, and event. Context over the burst is HTTP `202` with `coalesced` true and `applied` false. `AdaptiveEngineErrorV1` is the HTTP `detail` body and the socket error frame: `code`, `message`, optional `session_id`, optional `retry_after_ms`, optional `details`.

Internal adaptive codes stay in server logs at DEBUG with the code name only. The HTTP `detail.code` is always one of the public codes.

**9. Logging**

Logger name is the module name. Level follows `LOG_LEVEL`.

| Event | Level | Fields |
|-------|-------|--------|
| Auth allowed or refused | INFO for refuse, DEBUG for allow | `peer_class` `loopback` or `other`, `code`. No address string when it would be a user host on a WAN; `other` is enough |
| Session start, bind, delete | INFO | `session_id`, `project_id`, `score_id`, `clock_owner` |
| Command | DEBUG | `session_id`, `kind`, `request_id`, `disposition` |
| Coalesce or rate limit | WARNING | `session_id`, `code`, `retry_after_ms` |
| Ticker failure | ERROR | `session_id`, exception class. The ticker logs and continues the next step |
| Socket close | INFO | `session_id`, `close_code` |

Do not log bodies, sample values, mapping documents, cues, or the Authorization header.

## Tasks

### Phase 1: Contracts and pure gates

- [x] Task 1: Public schemas and settings
  - Add `backend/app/adaptive_engine_schemas.py` with `AdaptiveEngineStartV1`, `AdaptiveEngineSessionV1`, `AdaptiveEngineCommandResultV1` (`session`, `disposition`, `request_id`, `coalesced`, `applied`, optional `retry_after_ms`), state, intensity, event, and context request wrappers, `AdaptiveEngineAckV1`, and `AdaptiveEngineErrorV1`. `extra="forbid"`. `AdaptiveEngineErrorV1` is both the HTTP `detail` and the socket error frame. Reuse `parse_adaptive_context_external` and the existing mapping parser rather than a second mapping model.
  - Add `backend/app/adaptive_engine_settings.py` with the table in decision 6, including `ADAPTIVE_ENGINE_TICKER` (`auto` or `manual`, default `auto`). Invalid values log a warning and keep the default. Load once the same way `adaptive_musical_context_settings.py` does.
  - Public error codes and HTTP statuses live next to the schemas, including `engine_score_invalid`, `engine_clock_not_owned`, detail code `engine_state_rejected`, and warning `engine_queue_full`. There is no `engine_tempo_latched` code.
  - LOGGING: schema failures use the existing adaptive schema failure helper with the model name and field, not the body. Settings warnings use the key name and `invalid: true`.
  - Files: `backend/app/adaptive_engine_schemas.py`, `backend/app/adaptive_engine_settings.py`

- [x] Task 2: Auth gate
  - Add `backend/app/services/adaptive_engine_auth.py`. Pure functions: hash compare, peer class from a host string, decision enum `allow` or `unauthorized`. No FastAPI import.
  - Unit tests: unset token plus loopback allows; unset plus `10.0.0.8` refuses; unset plus host `testclient` refuses; a missing host refuses; set token requires a match on loopback and on `testclient`; a correct query-string token is still refused when the function is given an empty header; wrong token refuses; the token fixture string is absent from `caplog.text`. Do not treat `testclient` as loopback.
  - LOGGING: one INFO line on refuse with `peer_class` and `code`; DEBUG on allow with `peer_class` only.
  - Files: `backend/app/services/adaptive_engine_auth.py`, `backend/tests/test_adaptive_engine_auth.py`
  - Depends on Task 1 only for the public code constant, which this module may duplicate as a literal to stay free of schema imports. Prefer the literal `engine_unauthorized` in this module.

- [x] Task 3: Backpressure
  - Add `backend/app/services/adaptive_engine_backpressure.py`. A token bucket plus a one-slot holder. `try_command(now_ms)` and `offer_context(sample, now_ms)` are pure relative to a small state object the service stores. No threads and no FastAPI.
  - `offer_context` accepts while tokens remain. After that it stores the sample, increments a dropped count when it replaces one, and returns `coalesced`. `drain_context(now_ms)` returns the sample or none.
  - Unit tests: a burst of size `N` accepts `N`, the next call coalesces, a newer sample replaces the slot, and one drain returns only the newest.
  - LOGGING: this module does not log sample contents. The service logs the warning when the result is coalesced or rate-limited.
  - Files: `backend/app/services/adaptive_engine_backpressure.py`, `backend/tests/test_adaptive_engine_backpressure.py`

### Phase 2: Session and transport

- [x] Task 4: Engine service
  - Add `backend/app/services/adaptive_engine_service.py` and a process registry beside the playback registry. Implement start, get, delete, state, intensity, event, context, and `advance_engine_clock`.
  - Disposition follows decision 5: record `last_event` and pending ids before `command_adaptive_playback`, then classify the after-snapshot. Warning `document_revision_conflict` on that snapshot, or the same warning on a context sample snapshot, returns `409` without using `last_event`.
  - Stinger resolution reads the score and calls `command_adaptive_playback` only. A supplied `transition_id` must be in the match set. A scheduler rejection after a match is disposition `rejected`. Cue names resolve from the in-memory session list and do not read `transition.cue_label`.
  - Tempo latch uses the single `composition.tempo` and `ticks_per_quarter` from the composition the playback service already loaded. Do not parse a second copy of the score body for notes.
  - The ticker task starts only for `clock_owner: engine` when the ticker setting is `auto`. `advance_engine_clock` steps only that same owner and otherwise returns the snapshot with `engine_clock_not_owned`.
  - Delete stops only the resources this session started. Expose a cancel-all used by the lifespan shutdown.
  - LOGGING: INFO on start, bind, and delete; DEBUG on each command with `kind`, `request_id`, and `disposition`; WARNING on coalesce and rate limit; ERROR with exception class if one tick raises, then the next step still runs.
  - Files: `backend/app/services/adaptive_engine_service.py`
  - Depends on Tasks 1 and 3.

- [x] Task 5: HTTP router
  - Add `backend/app/routers/adaptive_engine.py` and `include_router` it from `backend/app/main.py`. Declare `HTTPBearer` so OpenAPI shows the scheme.
  - Map domain errors with the table in decision 8. Do not call `enforce_current`. `project_not_found` and `adaptive_score_not_found` are `404` `engine_score_missing`. `dangling_state_ref` on start is `422` `engine_score_invalid`. A missing `request.client` is `401` `engine_unauthorized`.
  - Response models are the public schemas, including `AdaptiveEngineCommandResultV1` and `AdaptiveEngineErrorV1`, so OpenAPI matches the contract doc.
  - Call the engine registry cancel from the existing lifespan shutdown in `backend/app/main.py` so `auto` tick tasks end before the process exits.
  - LOGGING: the router logs the HTTP status and public code at INFO for `4xx`. It does not log the JSON body.
  - Files: `backend/app/routers/adaptive_engine.py`, `backend/app/main.py`
  - Depends on Tasks 2 and 4.

- [x] Task 6: WebSocket feeds
  - On the same router, implement `/adaptive/events` and `/adaptive/status` with handshake auth.
  - The service publishes to in-memory subscriber lists. Slow subscribers follow decision 7.
  - An integration slice can live in Task 7; this task wires the sockets and a focused test that one ack arrives after an immediate state POST.
  - LOGGING: INFO on accept and close with `session_id` and `close_code`. No frame bodies.
  - Files: `backend/app/routers/adaptive_engine.py`, `backend/app/services/adaptive_engine_service.py`
  - Depends on Tasks 4 and 5.

### Phase 3: Proof and contract

- [x] Task 7: Integration tests and import boundary
  - Add `backend/tests/test_adaptive_engine_api.py` using `TestClient`. Set `ADAPTIVE_ENGINE_TOKEN` and `ADAPTIVE_ENGINE_TICKER=manual`. Send `Authorization` on HTTP and on `websocket_connect`. The peer host `testclient` is not loopback.
  - Fixture setup may call the existing project and adaptive-score routes. Every assertion about the external client uses `/adaptive/*` and the two sockets only.
  - Cover the acceptance list in Goal, plus: a request without the bearer is `401`; a second session for the same score is `409`; an unwired stinger is `422` `engine_stinger_unwired` and does not change `runtime_state_id`; with `clock_owner` `engine`, `advance_engine_clock` completes a `bar` transition and the events socket then shows `finished`; the same pump on a bound `clock_owner` `existing` session leaves the bar unchanged; the context burst returns `202` with `applied` false and a following pump drains one sample; `document_revision` is unchanged.
  - AST-scan `adaptive_engine_service.py`, `adaptive_engine.py`, `adaptive_engine_auth.py`, and `adaptive_engine_backpressure.py` and fail if they import `ai_runtime`, `ai_agents`, `fake_llm`, `llm_chat_client`, `music_transformer`, `symbolic_composition_generate`, `adaptive_runtime_continuation`, `adaptive_runtime_continuation_service`, or `adaptive_playback` (the pure module). Also scan `backend/app/ai_agents` and fail if it imports `adaptive_engine_service` or `adaptive_engine_schemas`.
  - Assert `frontend/src` contains no `/adaptive/session` string if a search is cheap; do not add frontend files.
  - LOGGING: the API test asserts the configured token value is absent from `caplog.text`.
  - Files: `backend/tests/test_adaptive_engine_api.py`
  - Depends on Tasks 5 and 6.

- [x] Task 8: Contract docs
  - Add `docs/adaptive-music-engine.md` with the route table, auth rules (loopback peers only, `testclient` included in the non-loopback case), the JSON examples (start, command result, event, ack, coalesced `202`), the error table from decision 8, `ADAPTIVE_ENGINE_TICKER`, and the statement that model, generate, agent, and continuation routes are not part of this surface.
  - Document env vars in `.env.example` next to the other `ADAPTIVE_*` keys.
  - Add a short entry point row to `AGENTS.md`, a layer row to `.ai-factory/ARCHITECTURE.md` including the `ai_agents/` import ban for these modules, and a navigation sentence to `docs/CODEBASE_MAP.md`.
  - Do not edit `ROADMAP.md`.
  - LOGGING: docs name the log fields from decision 9 and say the token is never logged.
  - Files: `docs/adaptive-music-engine.md`, `.env.example`, `AGENTS.md`, `.ai-factory/ARCHITECTURE.md`, `docs/CODEBASE_MAP.md`
  - Depends on Tasks 5 and 6 so the examples match the implemented models.

## Commit Plan
- **Commit 1** (after tasks 1–3): `feat: add adaptive engine contracts, auth, and backpressure`
- **Commit 2** (after tasks 4–6): `feat: expose the adaptive engine session over HTTP and WebSocket`
- **Commit 3** (after tasks 7–8): `test: cover the external adaptive engine client and document the contract`

## Risks
- A studio SPA that already sends `advance` must not share a ticker. `clock_owner: existing` is the guard. The integration test starts one session from idle and binds a second case to playback that is already running. `advance_engine_clock` moves the bar only for `clock_owner` `engine`.
- `TestClient` drives an event loop, so an `auto` ticker can drain the context slot during a burst. The API test sets `ADAPTIVE_ENGINE_TICKER=manual` and drains with `advance_engine_clock`.
- WebSocket frames can carry more than the HTTP body. The same public models serialize both. The test decodes one ack and one status frame and rejects unknown keys.
- `uvicorn` worker count greater than one would split the in-memory registry. Docs state the engine is single-process, same as playback. Do not add Redis.
