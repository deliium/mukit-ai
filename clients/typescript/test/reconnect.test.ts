import assert from "node:assert/strict"
import { test } from "node:test"

import { MukitAdaptiveClient, openDefaultSocket } from "../src/client.js"
import { AdaptiveClientError } from "../src/errors.js"
import { ReconnectCounter, delayMsForAttempt } from "../src/reconnect.js"
import {
  FakeSocket,
  RecordingFetch,
  ScriptedOpener,
  assertPackageHasNoStudioImport,
  sessionDocument,
  waitFor,
} from "./fakes.js"

test("delay doubles and caps", () => {
  const delays = [1, 2, 3, 4, 5, 6, 7, 8].map((attempt) => delayMsForAttempt(attempt))
  assert.deepEqual(delays, [200, 400, 800, 1600, 3200, 5000, 5000, 5000])
  const counter = new ReconnectCounter(8)
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7, 8].map(() => counter.nextDelayMs()), delays)
  assert.equal(counter.nextDelayMs(), null)
})

test("1013 reopens both sockets after one GET and does not repeat POST", async () => {
  const transport = new RecordingFetch()
  const document = sessionDocument()
  transport.push(201, document)
  transport.push(200, document)
  const opener = new ScriptedOpener()
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", "socket-token-unique-qq8", {
    transport: transport.fetch,
    opener: opener.open,
    sleeper: async () => undefined,
    clock: () => 0,
  })
  await adaptive.start(String(document.project_id), String(document.score_id), 1)
  await adaptive.listen(null, null, null)
  opener.sockets[0].emit("close", { code: 1013 })
  opener.sockets[1].emit("close", { code: 1013 })
  await adaptive.waitForSockets(4)
  const gets = transport.calls.filter((call) => call.method === "GET")
  const posts = transport.calls.filter((call) => call.method === "POST")
  assert.equal(gets.length, 1)
  assert.ok(gets[0].url.endsWith("/adaptive/session/aeng_0123abcd"))
  assert.equal(posts.length, 1)
  assert.equal(opener.sockets.length, 4)
  assert.ok(opener.sockets.every((socket) => !socket.url.includes("socket-token-unique-qq8")))
  assert.ok(opener.sockets.every((socket) => socket.headers.Authorization === "Bearer socket-token-unique-qq8"))
  await adaptive.close()
})

test("a second close during reconnect does not start another cycle", async () => {
  const document = sessionDocument()
  let release: () => void = () => undefined
  const gate = new Promise<void>((resolve) => {
    release = resolve
  })
  let gets = 0
  const calls: string[] = []
  const queued = [JSON.stringify(document), JSON.stringify(document)]
  const transport = async (url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    calls.push(init.method)
    if (init.method === "GET") {
      gets += 1
      await gate
    }
    const body = queued.shift()
    if (body === undefined) throw new Error(`unexpected ${init.method}`)
    return { status: init.method === "POST" ? 201 : 200, body }
  }
  const opener = new ScriptedOpener()
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
    transport,
    opener: opener.open,
    sleeper: async () => undefined,
    clock: () => 1,
  })
  await adaptive.start("project", "score", 1)
  await adaptive.listen(null, null, null)
  opener.sockets[0].emit("close", { code: 1013 })
  opener.sockets[1].emit("close", { code: 1013 })
  await waitFor(() => gets === 1)
  assert.equal(adaptive.socket_opens, 2)
  release()
  await adaptive.waitForSockets(4)
  assert.equal(gets, 1)
  assert.equal(calls.filter((method) => method === "POST").length, 1)
  await adaptive.close()
})

test("4401 and 4404 stop without a new session", async () => {
  for (const [code, publicCode] of [[4401, "engine_unauthorized"], [4404, "engine_session_missing"]] as const) {
    const transport = new RecordingFetch()
    const document = sessionDocument()
    transport.push(201, document)
    const opener = new ScriptedOpener()
    const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
      transport: transport.fetch,
      opener: opener.open,
      sleeper: async () => undefined,
    })
    await adaptive.start("project", "score", 1)
    await adaptive.listen(null, null, null)
    opener.sockets[0].emit("close", { code })
    opener.sockets[1].emit("close", { code })
    await waitFor(() => adaptive.terminal_code === publicCode)
    assert.equal(transport.calls.some((call) => call.method === "GET"), false)
    assert.equal(transport.calls.filter((call) => call.method === "POST").length, 1)
    await adaptive.close()
  }
})

test("status replaces the snapshot and an error frame leaves it", async () => {
  const transport = new RecordingFetch()
  const document = sessionDocument()
  transport.push(201, document)
  const opener = new ScriptedOpener()
  let ackDisposition = ""
  let errorCode = ""
  let snapshotDuringError = ""
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
    transport: transport.fetch,
    opener: opener.open,
  })
  await adaptive.start("project", "score", 1)
  await adaptive.listen(
    (ack) => {
      ackDisposition = String((ack as { disposition: string }).disposition)
    },
    null,
    (error) => {
      snapshotDuringError = adaptive.snapshot?.runtime_state_id ?? ""
      errorCode = error.code
    },
  )
  const status = opener.sockets.find((socket) => socket.url.includes("/adaptive/status")) as FakeSocket
  const events = opener.sockets.find((socket) => socket.url.includes("/adaptive/events")) as FakeSocket
  status.emit("message", { data: JSON.stringify(sessionDocument({ runtime_state_id: "danger" })) })
  await waitFor(() => adaptive.snapshot?.runtime_state_id === "danger")
  events.emit("message", { data: JSON.stringify({
    schema_version: "adaptive.engine.ack.v1",
    session_id: "aeng_0123abcd",
    request_id: "req_bar01",
    kind: "state",
    disposition: "finished",
    runtime_state_id: "explore",
    detail_code: null,
  }) })
  events.emit("message", { data: JSON.stringify({
    schema_version: "adaptive.engine.error.v1",
    code: "engine_session_missing",
    message: "No engine session exists for that id.",
    session_id: null,
    retry_after_ms: null,
    details: null,
  }) })
  await waitFor(() => errorCode === "engine_session_missing")
  assert.equal(ackDisposition, "finished")
  assert.equal(snapshotDuringError, "danger")
  assert.equal(adaptive.snapshot?.runtime_state_id, "danger")
  await adaptive.close()
})

test("status callback can send a command", async () => {
  const transport = new RecordingFetch()
  const document = sessionDocument()
  transport.push(201, document)
  transport.push(200, {
    session: document,
    disposition: "committed",
    request_id: "req_now1",
    coalesced: false,
    applied: true,
    retry_after_ms: null,
  })
  const opener = new ScriptedOpener()
  let done = false
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
    transport: transport.fetch,
    opener: opener.open,
  })
  await adaptive.start("project", "score", 1)
  await adaptive.listen(null, () => {
    void adaptive.set_intensity(0.3).then(() => {
      done = true
    })
  }, null)
  const status = opener.sockets.find((socket) => socket.url.includes("/adaptive/status")) as FakeSocket
  status.emit("message", { data: JSON.stringify(sessionDocument({ bar: 2 })) })
  await waitFor(() => done)
  await adaptive.close()
})

test("local close stays stopped", async () => {
  const transport = new RecordingFetch()
  transport.push(201, sessionDocument())
  const opener = new ScriptedOpener()
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
    transport: transport.fetch,
    opener: opener.open,
    sleeper: async () => undefined,
  })
  await adaptive.start("project", "score", 1)
  await adaptive.listen(null, null, null)
  await adaptive.close()
  assert.equal(transport.calls.some((call) => call.method === "GET"), false)
  assert.equal(adaptive.terminal_code, null)
})

test("reconnect_now uses one GET", async () => {
  const transport = new RecordingFetch()
  const document = sessionDocument()
  transport.push(201, document)
  transport.push(200, document)
  const opener = new ScriptedOpener()
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
    transport: transport.fetch,
    opener: opener.open,
    sleeper: async () => undefined,
    clock: () => 5,
  })
  await adaptive.start("project", "score", 1)
  await adaptive.listen(null, null, null)
  adaptive.reconnect_now()
  await adaptive.waitForSockets(4)
  assert.equal(transport.calls.filter((call) => call.method === "GET").length, 1)
  await adaptive.close()
})

test("missing WebSocket reports the dependency code", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "WebSocket")
  Object.defineProperty(globalThis, "WebSocket", { value: undefined, configurable: true })
  try {
    assert.throws(() => openDefaultSocket("ws://127.0.0.1:8000/adaptive/events", {}), (error: unknown) => {
      return error instanceof AdaptiveClientError && error.code === "engine_client_dependency_missing"
    })
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "WebSocket", descriptor)
  }
})

test("package source does not import the studio", () => {
  assertPackageHasNoStudioImport()
})
