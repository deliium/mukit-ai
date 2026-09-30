import assert from "node:assert/strict"
import { test } from "node:test"

import { MukitAdaptiveClient } from "../src/client.js"
import { AdaptiveClientError } from "../src/errors.js"
import { RecordingFetch, assertPackageHasNoStudioImport, captureLogs, errorBody, sessionDocument } from "./fakes.js"

function client(transport: RecordingFetch, token: string | null = null): MukitAdaptiveClient {
  return new MukitAdaptiveClient("http://127.0.0.1:8000", token, { transport: transport.fetch })
}

test("start 201 stores the snapshot", async () => {
  const transport = new RecordingFetch()
  const document = sessionDocument()
  transport.push(201, document)
  const adaptive = client(transport)
  const session = await adaptive.start(String(document.project_id), String(document.score_id), 1)
  assert.equal(session.session_id, "aeng_0123abcd")
  assert.equal(adaptive.snapshot?.clock_owner, "engine")
  assert.equal(transport.calls[0].method, "POST")
  assert.ok(transport.calls[0].url.endsWith("/adaptive/session"))
})

test("missing token omits Authorization", async () => {
  const transport = new RecordingFetch()
  transport.push(201, sessionDocument())
  await client(transport).start("project", "score", 1)
  assert.equal(transport.calls[0].headers.Authorization, undefined)
})

test("token sends Bearer", async () => {
  const transport = new RecordingFetch()
  transport.push(201, sessionDocument())
  await client(transport, "header-token").start("project", "score", 1)
  assert.equal(transport.calls[0].headers.Authorization, "Bearer header-token")
})

test("401 raises engine_unauthorized", async () => {
  const transport = new RecordingFetch()
  transport.push(401, errorBody("engine_unauthorized", "The engine request was not authorized."))
  await assert.rejects(
    () => client(transport, "header-token").start("project", "score", 1),
    (error: unknown) => error instanceof AdaptiveClientError && error.code === "engine_unauthorized" && error.status === 401,
  )
})

test("409 without adopt raises and does not GET", async () => {
  const transport = new RecordingFetch()
  transport.push(409, errorBody("engine_session_exists", "An engine session already exists for this score.", {
    session_id: "aeng_0123abcd",
    details: { session_id: "aeng_0123abcd" },
  }))
  await assert.rejects(() => client(transport).start("project", "score", 1), (error: unknown) => {
    return error instanceof AdaptiveClientError && error.code === "engine_session_exists"
  })
  assert.equal(transport.calls.length, 1)
})

test("409 with adopt calls GET", async () => {
  const transport = new RecordingFetch()
  transport.push(409, errorBody("engine_session_exists", "An engine session already exists for this score.", {
    session_id: "aeng_0123abcd",
    details: { session_id: "aeng_0123abcd" },
  }))
  transport.push(200, sessionDocument())
  const session = await client(transport).start("project", "score", 1, null, null, true)
  assert.equal(session.session_id, "aeng_0123abcd")
  assert.equal(transport.calls[1].method, "GET")
  assert.ok(transport.calls[1].url.endsWith("/adaptive/session/aeng_0123abcd"))
})

test("DELETE 204 empty body returns", async () => {
  const transport = new RecordingFetch()
  const document = sessionDocument()
  transport.push(201, document)
  transport.push(204, "")
  const adaptive = client(transport)
  await adaptive.start(String(document.project_id), String(document.score_id), 1)
  await adaptive.stop()
  assert.equal(adaptive.snapshot, null)
  assert.equal(transport.calls[1].method, "DELETE")
})

test("redirect raises and does not issue a second request", async () => {
  const transport = new RecordingFetch()
  transport.push(302, "")
  await assert.rejects(() => client(transport).start("project", "score", 1), (error: unknown) => {
    return error instanceof AdaptiveClientError && error.status === 302
  })
  assert.equal(transport.calls.length, 1)
})

test("logs omit the bearer token", async () => {
  process.env.MUKIT_ADAPTIVE_LOG_LEVEL = "DEBUG"
  const logs = captureLogs()
  try {
    const token = "token-super-unique-zz9"
    const transport = new RecordingFetch()
    transport.push(201, sessionDocument())
    await client(transport, token).start("project", "score", 1)
    assert.equal(logs.text().includes(token), false)
    assert.equal(logs.text().includes("Authorization"), false)
    assert.match(logs.text(), /session_start/)
    assert.match(logs.text(), /aeng_0123abcd/)
  } finally {
    logs.restore()
    delete process.env.MUKIT_ADAPTIVE_LOG_LEVEL
  }
})

test("package source does not import the studio", () => {
  assertPackageHasNoStudioImport()
})
