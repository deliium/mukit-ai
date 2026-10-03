/**
 * Async client for the public adaptive music engine.
 * HTTP calls and snapshot writes share one queue. Callbacks run after that queue releases.
 */

import { AdaptiveClientError } from "./errors.js"
import { logEvent } from "./log.js"
import {
  CONTEXT_SCHEMA,
  type EngineCommandResult,
  type EngineSession,
  parseAck,
  parseCommandResult,
  parseErrorDocument,
  parseSession,
  requestIdOk,
  sessionIdOk,
  validateContextValues,
} from "./models.js"
import { DEFAULT_MAX_ATTEMPTS, ReconnectCounter } from "./reconnect.js"

const RATE_LIMIT_CAP_MS = 1000

export interface HttpResponse {
  status: number
  body: string
}

export interface HttpInit {
  method: string
  headers: Record<string, string>
  body?: string
}

export type HttpTransport = (url: string, init: HttpInit) => Promise<HttpResponse>

export interface ClientSocket {
  addEventListener(type: "open" | "message" | "close" | "error", listener: (event: { data?: unknown; code?: number }) => void): void
  close(code?: number, reason?: string): void
}

export type SocketOpener = (url: string, headers: Record<string, string>) => ClientSocket

type Frame =
  | { type: "message"; feed: "events" | "status"; data: string }
  | { type: "close"; feed: "events" | "status"; code: number }
  | { type: "reconnect" }

interface Pair {
  events: ClientSocket
  status: ClientSocket
  sessionId: string
  inbox: AsyncQueue<Frame>
}

export class MukitAdaptiveClient {
  readonly httpBase: string
  readonly wsBase: string
  private readonly token: string | null
  private readonly transport: HttpTransport
  private readonly opener: SocketOpener
  private readonly sleeper: (delayMs: number) => Promise<void>
  private readonly clock: () => number
  private readonly counter: ReconnectCounter
  private tail: Promise<unknown> = Promise.resolve()
  private snapshotValue: EngineSession | null = null
  private requestCounter = 0
  private intentional = false
  private giveUp = false
  private reconnectRequested = false
  private terminalCode: string | null = null
  private socketOpens = 0
  private openWaiters: Array<() => void> = []
  private pair: Pair | null = null
  private onAck: ((ack: unknown) => void) | null = null
  private onStatus: ((session: EngineSession) => void) | null = null
  private onError: ((error: AdaptiveClientError) => void) | null = null
  private loopDone: Promise<void> = Promise.resolve()
  private resolveStarted: (() => void) | null = null
  private inLoop = false
  private cancelSleep: (() => void) | null = null

  constructor(
    baseUrl = "http://127.0.0.1:8000",
    token: string | null = null,
    options: {
      transport?: HttpTransport
      opener?: SocketOpener
      sleeper?: (delayMs: number) => Promise<void>
      clock?: () => number
      maxReconnectAttempts?: number
    } = {},
  ) {
    logEvent("DEBUG", "client_init")
    this.httpBase = normalizeBaseUrl(baseUrl)
    this.wsBase = websocketBase(this.httpBase)
    this.token = token || null
    this.transport = options.transport ?? defaultTransport
    this.opener = options.opener ?? openDefaultSocket
    this.sleeper = options.sleeper ?? ((delayMs) => this.interruptibleSleep(delayMs))
    this.clock = options.clock ?? (() => Date.now())
    this.counter = new ReconnectCounter(options.maxReconnectAttempts ?? DEFAULT_MAX_ATTEMPTS)
    logEvent("DEBUG", "client_init_exit", { ws_scheme: new URL(this.wsBase).protocol.replace(":", "") })
  }

  get snapshot(): EngineSession | null {
    return this.snapshotValue
  }

  get terminal_code(): string | null {
    return this.terminalCode
  }

  get socket_opens(): number {
    return this.socketOpens
  }

  next_request_id(): string {
    this.requestCounter += 1
    const requestId = `req_${String(this.requestCounter).padStart(8, "0")}`
    logEvent("DEBUG", "next_request_id", { request_id: requestId })
    return requestId
  }

  async start(
    projectId: string,
    scoreId: string,
    expectedDocumentRevision: number,
    mapping: Record<string, unknown> | null = null,
    cues: Array<Record<string, unknown>> | null = null,
    adoptExisting = false,
  ): Promise<EngineSession> {
    logEvent("DEBUG", "start_enter", { project_id: projectId, score_id: scoreId })
    const body: Record<string, unknown> = {
      project_id: requireCallerText(projectId, "project_id"),
      score_id: requireCallerText(scoreId, "score_id"),
      expected_document_revision: requireRevision(expectedDocumentRevision),
    }
    if (mapping !== null) {
      if (!isRecord(mapping)) throw new AdaptiveClientError("engine_payload_invalid", "invalid mapping")
      body.mapping = mapping
    }
    if (cues !== null) {
      if (!Array.isArray(cues)) throw new AdaptiveClientError("engine_payload_invalid", "invalid cues")
      body.cues = cues
    }
    try {
      const session = await this.exclusive(() => this.readSession("POST", "/adaptive/session", body))
      logEvent("INFO", "session_start", { session_id: session.session_id, clock_owner: session.clock_owner })
      logEvent("DEBUG", "start_exit", { session_id: session.session_id })
      return session
    } catch (error) {
      if (!(error instanceof AdaptiveClientError) || !adoptExisting || error.status !== 409 || error.code !== "engine_session_exists") {
        throw error
      }
      const existing = existingSessionId(error)
      if (!existing) throw error
      const session = await this.attach(existing)
      logEvent("DEBUG", "start_exit", { session_id: session.session_id })
      return session
    }
  }

  async attach(sessionId: string): Promise<EngineSession> {
    logEvent("DEBUG", "attach_enter", { session_id: sessionId })
    if (!sessionIdOk(sessionId)) throw new AdaptiveClientError("engine_payload_invalid", "invalid session_id")
    const session = await this.exclusive(() => this.readSession("GET", `/adaptive/session/${sessionId}`, null))
    logEvent("INFO", "session_attach", { session_id: session.session_id, clock_owner: session.clock_owner })
    logEvent("DEBUG", "attach_exit", { session_id: session.session_id })
    return session
  }

  async stop(): Promise<void> {
    const session = this.snapshotValue
    if (!session) throw new AdaptiveClientError("engine_session_missing", "No engine session is attached.")
    logEvent("DEBUG", "stop_enter", { session_id: session.session_id })
    await this.exclusive(async () => {
      const result = await this.exchange("DELETE", `/adaptive/session/${session.session_id}`, null)
      if (result.status !== 204) throw this.failure(result)
      this.snapshotValue = null
    })
    logEvent("INFO", "session_stop", { session_id: session.session_id, clock_owner: session.clock_owner })
    await this.close()
    logEvent("DEBUG", "stop_exit", { session_id: session.session_id })
  }

  async set_state(toStateId: string, transitionId: string | null = null, requestId: string | null = null): Promise<EngineCommandResult> {
    logEvent("DEBUG", "set_state_enter")
    const body: Record<string, unknown> = {
      to_state_id: requireCallerText(toStateId, "to_state_id"),
      request_id: this.resolveRequestId(requestId),
    }
    if (transitionId !== null) body.transition_id = requireCallerText(transitionId, "transition_id")
    const result = await this.command("state", "state", body)
    logEvent("DEBUG", "set_state_exit", { request_id: result.request_id, disposition: result.disposition })
    return result
  }

  async set_intensity(intensity: number, requestId: string | null = null): Promise<EngineCommandResult> {
    logEvent("DEBUG", "set_intensity_enter")
    if (typeof intensity !== "number" || !Number.isFinite(intensity)) {
      throw new AdaptiveClientError("engine_payload_invalid", "invalid intensity")
    }
    const result = await this.command("intensity", "intensity", {
      intensity,
      request_id: this.resolveRequestId(requestId),
    })
    logEvent("DEBUG", "set_intensity_exit", { request_id: result.request_id, disposition: result.disposition })
    return result
  }

  async fire_stinger(stingerId: string, transitionId: string | null = null, requestId: string | null = null): Promise<EngineCommandResult> {
    logEvent("DEBUG", "fire_stinger_enter")
    const body: Record<string, unknown> = {
      kind: "stinger",
      stinger_id: requireCallerText(stingerId, "stinger_id"),
      request_id: this.resolveRequestId(requestId),
    }
    if (transitionId !== null) body.transition_id = requireCallerText(transitionId, "transition_id")
    const result = await this.command("stinger", "event", body)
    logEvent("DEBUG", "fire_stinger_exit", { request_id: result.request_id })
    return result
  }

  async fire_cue(name: string, requestId: string | null = null): Promise<EngineCommandResult> {
    logEvent("DEBUG", "fire_cue_enter")
    const result = await this.command("cue", "event", {
      kind: "cue",
      name: requireCallerText(name, "name"),
      request_id: this.resolveRequestId(requestId),
    })
    logEvent("DEBUG", "fire_cue_exit", { request_id: result.request_id })
    return result
  }

  async send_context(
    values: Record<string, unknown>,
    sourceId: string | null = null,
    observedAtMs: number | null = null,
  ): Promise<EngineCommandResult> {
    logEvent("DEBUG", "send_context_enter")
    const body: Record<string, unknown> = {
      schema_version: CONTEXT_SCHEMA,
      values: validateContextValues(values),
    }
    if (sourceId !== null) body.source_id = requireCallerText(sourceId, "source_id")
    if (observedAtMs !== null) {
      if (!Number.isInteger(observedAtMs) || observedAtMs < 0) {
        throw new AdaptiveClientError("engine_payload_invalid", "invalid observed_at_ms")
      }
      body.observed_at_ms = observedAtMs
    }
    const result = await this.command("context", "context", body)
    logEvent("DEBUG", "send_context_exit", { request_id: result.request_id, coalesced: result.coalesced })
    return result
  }

  /** POST …/continuous/maintain. Returns continuation snapshot JSON; does not replace the engine session snapshot. */
  async maintain_continuous(): Promise<Record<string, unknown>> {
    logEvent("DEBUG", "maintain_continuous_enter")
    const payload = await this.exclusive(async () => {
      const session = this.snapshotValue
      if (!session) throw new AdaptiveClientError("engine_session_missing", "No engine session is attached.")
      const result = await this.exchange("POST", `/adaptive/session/${session.session_id}/continuous/maintain`, null)
      if (result.status !== 200) throw this.failure(result)
      return decodeJson(result)
    })
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new AdaptiveClientError("engine_payload_invalid", "invalid continuous snapshot")
    }
    const body = payload as Record<string, unknown>
    logEvent("DEBUG", "maintain_continuous_exit", {
      job_status: body.job_status,
      continuous: body.continuous,
    })
    return body
  }

  /** GET …/continuous/buffer. null when the server returns 204. */
  async get_continuous_buffer(): Promise<Record<string, unknown> | null> {
    logEvent("DEBUG", "get_continuous_buffer_enter")
    const payload = await this.exclusive(async () => {
      const session = this.snapshotValue
      if (!session) throw new AdaptiveClientError("engine_session_missing", "No engine session is attached.")
      const result = await this.exchange("GET", `/adaptive/session/${session.session_id}/continuous/buffer`, null)
      if (result.status === 204) return null
      if (result.status !== 200) throw this.failure(result)
      return decodeJson(result)
    })
    if (payload === null) {
      logEvent("DEBUG", "get_continuous_buffer_exit", { empty: true })
      return null
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new AdaptiveClientError("engine_payload_invalid", "invalid continuous buffer")
    }
    const body = payload as Record<string, unknown>
    const events = Array.isArray(body.events) ? body.events : []
    logEvent("DEBUG", "get_continuous_buffer_exit", { event_count: events.length })
    return body
  }

  async listen(
    onAck: ((ack: unknown) => void) | null,
    onStatus: ((session: EngineSession) => void) | null,
    onError: ((error: AdaptiveClientError) => void) | null,
  ): Promise<void> {
    logEvent("DEBUG", "listen_enter")
    if (this.inLoop) {
      logEvent("DEBUG", "listen_exit", { already_running: true })
      return
    }
    this.onAck = onAck
    this.onStatus = onStatus
    this.onError = onError
    this.intentional = false
    this.giveUp = false
    this.terminalCode = null
    this.reconnectRequested = false
    this.counter.reset()
    const started = new Promise<void>((resolve) => {
      this.resolveStarted = resolve
    })
    this.loopDone = this.socketLoop()
    await started
    logEvent("DEBUG", "listen_exit", { already_running: false })
  }

  reconnect_now(): void {
    logEvent("DEBUG", "reconnect_now_enter")
    this.reconnectRequested = true
    this.pair?.inbox.push({ type: "reconnect" })
    this.wakeSockets()
    logEvent("DEBUG", "reconnect_now_exit")
  }

  async close(): Promise<void> {
    logEvent("DEBUG", "close_enter")
    this.intentional = true
    this.reconnectRequested = false
    this.cancelSleep?.()
    this.dropCurrent()
    if (!this.inLoop) await this.loopDone
    logEvent("DEBUG", "close_exit")
  }

  waitForSockets(count: number): Promise<void> {
    if (this.socketOpens >= count) return Promise.resolve()
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("sockets did not open")), 2000)
      const check = () => {
        if (this.socketOpens >= count) {
          clearTimeout(timer)
          resolve()
        }
      }
      this.openWaiters.push(check)
    })
  }

  private async command(kind: string, suffix: string, body: Record<string, unknown>): Promise<EngineCommandResult> {
    const parsed = await this.exclusive(async () => {
      const session = this.snapshotValue
      if (!session) throw new AdaptiveClientError("engine_session_missing", "No engine session is attached.")
      const result = await this.exchange("POST", `/adaptive/session/${session.session_id}/${suffix}`, body)
      if (result.status !== 200 && result.status !== 202) throw this.failure(result)
      const command = parseCommandResult(decodeJson(result))
      this.snapshotValue = command.session
      return command
    })
    logEvent("DEBUG", "command_result", {
      session_id: parsed.session.session_id,
      kind,
      request_id: parsed.request_id,
      disposition: parsed.disposition,
      coalesced: parsed.coalesced,
    })
    logWarnings(parsed.session)
    return parsed
  }

  private async readSession(method: string, path: string, body: Record<string, unknown> | null): Promise<EngineSession> {
    const result = await this.exchange(method, path, body)
    if (result.status !== 200 && result.status !== 201) throw this.failure(result)
    const session = parseSession(decodeJson(result))
    this.snapshotValue = session
    logWarnings(session)
    return session
  }

  private async exchange(method: string, path: string, body: Record<string, unknown> | null, allowRetry = true): Promise<HttpResponse> {
    const headers = this.authHeaders(body !== null)
    logEvent("DEBUG", "http_request", { method, path })
    const result = await this.transport(this.httpBase + path, {
      method,
      headers,
      body: body === null ? undefined : JSON.stringify(body),
    })
    logEvent("DEBUG", "http_response", { method, path, status: result.status })
    if (result.status >= 300 && result.status < 400) {
      throw new AdaptiveClientError("engine_payload_invalid", "HTTP redirect is not followed", { status: result.status })
    }
    if (result.status === 0) {
      throw new AdaptiveClientError("engine_payload_invalid", "HTTP redirect is not followed", { status: result.status })
    }
    if (result.status === 429 && allowRetry) {
      const error = this.failure(result)
      const delay = error.retryAfterMs === null ? 0 : Math.min(error.retryAfterMs, RATE_LIMIT_CAP_MS)
      const sessionId = this.snapshotValue?.session_id ?? "none"
      logEvent("WARNING", "rate_limited", {
        session_id: sessionId,
        code: error.code,
        retry_after_ms: error.retryAfterMs ?? 0,
      })
      await this.sleeper(delay)
      return this.exchange(method, path, body, false)
    }
    if (result.status >= 400) throw this.failure(result)
    return result
  }

  private failure(result: HttpResponse): AdaptiveClientError {
    if (!result.body) return new AdaptiveClientError("engine_payload_invalid", "invalid error", { status: result.status })
    let payload: unknown
    try {
      payload = JSON.parse(result.body)
    } catch (error) {
      throw new AdaptiveClientError("engine_payload_invalid", "invalid error", { status: result.status })
    }
    if (!isRecord(payload) || !isRecord(payload.detail)) {
      throw new AdaptiveClientError("engine_payload_invalid", "invalid error", { status: result.status })
    }
    return parseErrorDocument(payload.detail, result.status)
  }

  private authHeaders(jsonBody: boolean): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json" }
    if (jsonBody) headers["Content-Type"] = "application/json"
    if (this.token) headers.Authorization = `Bearer ${this.token}`
    return headers
  }

  private resolveRequestId(requestId: string | null): string {
    if (requestId === null) return this.next_request_id()
    if (!requestIdOk(requestId)) throw new AdaptiveClientError("engine_payload_invalid", "invalid request_id")
    return requestId
  }

  private async socketLoop(): Promise<void> {
    this.inLoop = true
    logEvent("DEBUG", "socket_loop_enter")
    try {
      while (!this.intentional && !this.giveUp) {
        let pair: Pair
        try {
          pair = await this.openPair()
        } catch (error) {
          const clientError = error instanceof AdaptiveClientError ? error : null
          logEvent("DEBUG", "socket_open_failed", {
            code: clientError?.code ?? "connect",
            error_class: error instanceof Error ? error.name : "Error",
          })
          if (clientError && (clientError.code === "engine_client_dependency_missing" || clientError.code === "engine_session_missing")) {
            this.fail(clientError.code, clientError.sessionId)
            return
          }
          this.signalStarted()
          if (!(await this.refresh())) return
          continue
        }
        this.counter.reset()
        const outcome = await this.pump(pair)
        this.drop(pair)
        if (this.intentional || this.giveUp || outcome === "stop" || outcome === "fatal") return
        if (!(await this.refresh())) return
      }
    } finally {
      this.inLoop = false
      this.signalStarted()
      logEvent("DEBUG", "socket_loop_exit")
    }
  }

  private async openPair(): Promise<Pair> {
    const session = this.snapshotValue
    if (!session) throw new AdaptiveClientError("engine_session_missing", "No engine session is attached.")
    const headers = this.authHeaders(false)
    const inbox = new AsyncQueue<Frame>()
    const eventsUrl = socketUrl(this.wsBase, "events", session.session_id)
    const statusUrl = socketUrl(this.wsBase, "status", session.session_id)
    rejectTokenInUrl(eventsUrl, this.token)
    rejectTokenInUrl(statusUrl, this.token)
    const events = await this.openOne(eventsUrl, headers)
    let status: ClientSocket
    try {
      status = await this.openOne(statusUrl, { ...headers })
    } catch (error) {
      events.close()
      throw error
    }
    this.bind(events, "events", inbox)
    this.bind(status, "status", inbox)
    const pair: Pair = { events, status, sessionId: session.session_id, inbox }
    this.pair = pair
    this.socketOpens += 2
    for (const waiter of this.openWaiters) waiter()
    logEvent("INFO", "socket_open", { session_id: session.session_id, feed: "events" })
    logEvent("INFO", "socket_open", { session_id: session.session_id, feed: "status" })
    this.signalStarted()
    return pair
  }

  private openOne(url: string, headers: Record<string, string>): Promise<ClientSocket> {
    const socket = this.opener(url, headers)
    return new Promise((resolve, reject) => {
      const fail = () => reject(new AdaptiveClientError("connect", "socket failed to open"))
      socket.addEventListener("open", () => resolve(socket))
      socket.addEventListener("error", fail)
    })
  }

  private bind(socket: ClientSocket, feed: "events" | "status", inbox: AsyncQueue<Frame>): void {
    socket.addEventListener("message", (event) => {
      inbox.push({ type: "message", feed, data: typeof event.data === "string" ? event.data : "" })
    })
    socket.addEventListener("close", (event) => {
      inbox.push({ type: "close", feed, code: typeof event.code === "number" ? event.code : 1006 })
    })
  }

  private async pump(pair: Pair): Promise<"reconnect" | "stop" | "fatal"> {
    while (!this.intentional && !this.giveUp) {
      const first = await pair.inbox.shift()
      const batch = [first, ...pair.inbox.drain()]
      for (const frame of batch) {
        if (frame.type === "message") await this.dispatch(frame.feed, frame.data)
      }
      const closes = batch.filter((frame): frame is Extract<Frame, { type: "close" }> => frame.type === "close")
      for (const frame of closes) {
        logEvent("INFO", "socket_close", { session_id: pair.sessionId, feed: frame.feed, close_code: frame.code })
      }
      const forced = this.reconnectRequested || batch.some((frame) => frame.type === "reconnect")
      this.reconnectRequested = false
      if (closes.length === 0 && !forced) continue
      if (this.intentional) return "stop"
      const terminal = closes.find((frame) => frame.code === 4401 || frame.code === 4404)
      if (terminal) {
        const code = terminal.code === 4401 ? "engine_unauthorized" : "engine_session_missing"
        this.fail(code, pair.sessionId)
        return "fatal"
      }
      return "reconnect"
    }
    return "stop"
  }

  private async dispatch(feed: string, message: string): Promise<void> {
    logEvent("DEBUG", "socket_frame", { feed })
    let payload: unknown
    try {
      payload = JSON.parse(message)
    } catch {
      this.emitError(new AdaptiveClientError("engine_payload_invalid", "invalid socket text"))
      return
    }
    const schema = isRecord(payload) ? payload.schema_version : null
    try {
      if (schema === "adaptive.engine.session.v1") {
        const session = parseSession(payload)
        await this.exclusive(async () => {
          this.snapshotValue = session
        })
        logWarnings(session)
        this.onStatus?.(session)
        return
      }
      if (schema === "adaptive.engine.ack.v1") {
        this.onAck?.(parseAck(payload))
        return
      }
      if (schema === "adaptive.engine.error.v1") {
        this.emitError(parseErrorDocument(payload))
        return
      }
    } catch (error) {
      if (error instanceof AdaptiveClientError) this.emitError(error)
      else this.emitError(new AdaptiveClientError("engine_payload_invalid", "invalid socket text"))
      return
    }
    this.emitError(new AdaptiveClientError("engine_payload_invalid", "invalid socket text"))
  }

  private async refresh(): Promise<boolean> {
    while (!this.intentional && !this.giveUp) {
      const delay = this.counter.nextDelayMs()
      const sessionId = this.snapshotValue?.session_id ?? null
      if (delay === null) {
        this.fail("engine_reconnect_exhausted", sessionId)
        return false
      }
      const now = this.clock()
      logEvent("INFO", "reconnect_attempt", { session_id: sessionId ?? "none", attempt: this.counter.attempt, delay_ms: delay })
      logEvent("DEBUG", "reconnect_clock", { clock: now })
      await this.sleeper(delay)
      if (this.intentional || this.giveUp) return false
      if (!sessionId) {
        this.fail("engine_session_missing", null)
        return false
      }
      try {
        await this.attach(sessionId)
      } catch (error) {
        if (!(error instanceof AdaptiveClientError)) throw error
        logEvent("DEBUG", "reconnect_get_failed", { session_id: sessionId, code: error.code, status: error.status ?? 0 })
        if (error.status === 401 || error.status === 404 || error.code === "engine_unauthorized" || error.code === "engine_session_missing") {
          this.fail(error.code, sessionId)
          return false
        }
        continue
      }
      return true
    }
    return false
  }

  private fail(code: string, sessionId: string | null): void {
    if (this.giveUp) return
    this.giveUp = true
    this.terminalCode = code
    logEvent("ERROR", "reconnect_stopped", { session_id: sessionId ?? "none", code })
    this.emitError(new AdaptiveClientError(code, code, { sessionId }))
  }

  private emitError(error: AdaptiveClientError): void {
    if (!this.onError) return
    try {
      this.onError(error)
    } catch (caught) {
      logEvent("ERROR", "callback_failed", { error_class: caught instanceof Error ? caught.name : "Error" })
    }
  }

  private wakeSockets(): void {
    const pair = this.pair
    if (!pair) return
    safeClose(pair.events)
    safeClose(pair.status)
  }

  private dropCurrent(): void {
    const pair = this.pair
    this.pair = null
    if (pair) this.drop(pair)
  }

  private drop(pair: Pair): void {
    if (this.pair === pair) this.pair = null
    safeClose(pair.events)
    safeClose(pair.status)
  }

  private signalStarted(): void {
    const resolve = this.resolveStarted
    this.resolveStarted = null
    resolve?.()
  }

  private exclusive<T>(action: () => Promise<T>): Promise<T> {
    const run = this.tail.then(action, action)
    this.tail = run.then(() => undefined, () => undefined)
    return run
  }

  private interruptibleSleep(delayMs: number): Promise<void> {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.cancelSleep = null
        resolve()
      }, delayMs)
      this.cancelSleep = () => {
        clearTimeout(timer)
        this.cancelSleep = null
        resolve()
      }
    })
  }
}

class AsyncQueue<T> {
  private items: T[] = []
  private waiters: Array<(value: T) => void> = []

  push(value: T): void {
    const waiter = this.waiters.shift()
    if (waiter) waiter(value)
    else this.items.push(value)
  }

  shift(): Promise<T> {
    const next = this.items.shift()
    if (next !== undefined) return Promise.resolve(next)
    return new Promise((resolve) => this.waiters.push(resolve))
  }

  drain(): T[] {
    const pending = this.items
    this.items = []
    return pending
  }
}

export function openDefaultSocket(url: string, headers: Record<string, string>): ClientSocket {
  if (typeof WebSocket === "undefined") {
    logEvent("ERROR", "websocket_dependency_missing", {
      code: "engine_client_dependency_missing",
      error_class: "ReferenceError",
    })
    throw new AdaptiveClientError("engine_client_dependency_missing", "WebSocket is not available")
  }
  logEvent("DEBUG", "websocket_open_enter", { feed: url.includes("/adaptive/status") ? "status" : "events" })
  return new WebSocket(url, { headers })
}

async function defaultTransport(url: string, init: HttpInit): Promise<HttpResponse> {
  logEvent("DEBUG", "http_transport_enter", { method: init.method })
  const response = await fetch(url, { method: init.method, headers: init.headers, body: init.body, redirect: "manual" })
  const body = await response.text()
  logEvent("DEBUG", "http_transport_exit", { method: init.method, status: response.status })
  return { status: response.status, body }
}

function socketUrl(wsBase: string, feed: string, sessionId: string): string {
  const query = new URLSearchParams({ session_id: sessionId }).toString()
  return `${wsBase}/adaptive/${feed}?${query}`
}

function normalizeBaseUrl(baseUrl: string): string {
  let url: URL
  try {
    url = new URL(baseUrl)
  } catch {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid base URL")
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password || url.search || url.hash) {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid base URL")
  }
  url.pathname = url.pathname.replace(/\/$/, "")
  return url.toString().replace(/\/$/, "")
}

function websocketBase(httpBase: string): string {
  const url = new URL(httpBase)
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  return url.toString().replace(/\/$/, "")
}

function rejectTokenInUrl(url: string, token: string | null): void {
  const query = new URL(url).search
  if (query.includes("token=") || (token && query.includes(token))) {
    throw new AdaptiveClientError("engine_payload_invalid", "token must not be in the socket URL")
  }
}

function existingSessionId(error: AdaptiveClientError): string | null {
  if (error.sessionId && sessionIdOk(error.sessionId)) return error.sessionId
  const nested = error.details?.session_id
  if (typeof nested === "string" && sessionIdOk(nested)) return nested
  return null
}

function decodeJson(result: HttpResponse): unknown {
  if (!result.body) throw new AdaptiveClientError("engine_payload_invalid", "invalid body", { status: result.status })
  try {
    return JSON.parse(result.body)
  } catch (error) {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid body", { status: result.status })
  }
}

function logWarnings(session: EngineSession): void {
  if (session.warnings.length === 0) return
  logEvent("DEBUG", "snapshot_warnings", { session_id: session.session_id, warnings: session.warnings.join(",") })
}

function safeClose(socket: ClientSocket): void {
  try {
    socket.close()
  } catch (error) {
    logEvent("DEBUG", "socket_close_failed", { error_class: error instanceof Error ? error.name : "Error" })
  }
}

function requireCallerText(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) throw new AdaptiveClientError("engine_payload_invalid", `invalid ${field}`)
  return value
}

function requireRevision(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid expected_document_revision")
  }
  return value
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
