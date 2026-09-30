import assert from "node:assert/strict"
import { test } from "node:test"

import { MukitAdaptiveClient } from "../src/client.js"
import { runDemo } from "../src/demo.js"
import { commandDocument, RecordingFetch, captureLogs, sessionDocument } from "./fakes.js"

function environ(extra: Record<string, string> = {}): Record<string, string> {
  const document = sessionDocument()
  return {
    MUKIT_ADAPTIVE_BASE_URL: "http://127.0.0.1:8000",
    MUKIT_ADAPTIVE_PROJECT_ID: String(document.project_id),
    MUKIT_ADAPTIVE_SCORE_ID: String(document.score_id),
    MUKIT_ADAPTIVE_DOCUMENT_REVISION: "1",
    ...extra,
  }
}

function startedClient(): { transport: RecordingFetch; client: MukitAdaptiveClient } {
  const transport = new RecordingFetch()
  transport.push(201, sessionDocument())
  const client = new MukitAdaptiveClient("http://127.0.0.1:8000", "token-super-unique-zz9", { transport: transport.fetch })
  return { transport, client }
}

test("start sends the recipe mapping and the combat cue", async () => {
  const previous = process.env.MUKIT_ADAPTIVE_LOG_LEVEL
  process.env.MUKIT_ADAPTIVE_LOG_LEVEL = "DEBUG"
  const logs = captureLogs()
  try {
    const { transport, client } = startedClient()
    const code = await runDemo({
      argv: [],
      env: environ({ MUKIT_ADAPTIVE_TOKEN: "token-super-unique-zz9" }),
      lines: [],
      client,
      listen: false,
    })
    assert.equal(code, 0)
    const body = JSON.parse(transport.calls[0].body ?? "{}") as {
      mapping: { schema_version: string; baseline_state_id: string }
      cues: Array<{ name: string; to_state_id: string }>
    }
    assert.equal(body.mapping.schema_version, "adaptive.context.mapping.v1")
    assert.equal(body.mapping.baseline_state_id, "explore")
    assert.deepEqual(body.cues, [{ name: "combat", to_state_id: "combat_state" }])
    assert.equal(logs.text().includes("baseline_state_id"), false)
    assert.equal(logs.text().includes("combat_state"), false)
    assert.equal(logs.text().includes("token-super-unique-zz9"), false)
    assert.equal(transport.calls.filter((call) => call.url.endsWith("/adaptive/session")).length, 1)
  } finally {
    logs.restore()
    if (previous === undefined) delete process.env.MUKIT_ADAPTIVE_LOG_LEVEL
    else process.env.MUKIT_ADAPTIVE_LOG_LEVEL = previous
  }
})

test("combat key fires the cue and intensity without a second session", async () => {
  const { transport, client } = startedClient()
  for (let index = 0; index < 3; index += 1) transport.push(200, commandDocument())
  const code = await runDemo({ argv: [], env: environ(), lines: ["3", "q"], client, listen: false })
  assert.equal(code, 0)
  const sessionPosts = transport.calls.filter((call) => call.method === "POST" && call.url.endsWith("/adaptive/session"))
  assert.equal(sessionPosts.length, 1)
  const bodies = transport.calls
    .filter((call) => call.body)
    .map((call) => JSON.parse(call.body ?? "{}") as { kind?: string; name?: string; intensity?: number })
  const cue = bodies.find((body) => body.kind === "cue")
  const intensity = bodies.find((body) => body.intensity !== undefined)
  assert.equal(cue?.name, "combat")
  assert.equal(intensity?.intensity, 0.9)
  assert.equal(transport.calls.some((call) => call.method === "DELETE"), false)
})

test("q does not delete and --stop does", async () => {
  const quiet = startedClient()
  const quietCode = await runDemo({ argv: [], env: environ(), lines: ["q"], client: quiet.client, listen: false })
  assert.equal(quietCode, 0)
  assert.equal(quiet.transport.calls.some((call) => call.method === "DELETE"), false)

  const stopping = startedClient()
  stopping.transport.push(204, "")
  const stopCode = await runDemo({ argv: ["--stop"], env: environ(), lines: ["q"], client: stopping.client, listen: false })
  assert.equal(stopCode, 0)
  assert.equal(
    stopping.transport.calls.some((call) => call.method === "DELETE" && call.url.includes("/adaptive/session/")),
    true,
  )
})

test("missing env logs the variable name only", async () => {
  const previous = process.env.MUKIT_ADAPTIVE_LOG_LEVEL
  process.env.MUKIT_ADAPTIVE_LOG_LEVEL = "DEBUG"
  const logs = captureLogs()
  try {
    const token = "token-super-unique-zz9"
    const code = await runDemo({ argv: [], env: { MUKIT_ADAPTIVE_TOKEN: token }, lines: [], listen: false })
    assert.equal(code, 1)
    const text = logs.text()
    assert.equal(text.includes("MUKIT_ADAPTIVE_PROJECT_ID"), true)
    assert.equal(text.includes("MUKIT_ADAPTIVE_SCORE_ID"), true)
    assert.equal(text.includes("MUKIT_ADAPTIVE_DOCUMENT_REVISION"), true)
    assert.equal(text.includes(token), false)
    assert.equal(text.includes("baseline_state_id"), false)
    assert.equal(text.includes("adaptive.context.mapping.v1"), false)
  } finally {
    logs.restore()
    if (previous === undefined) delete process.env.MUKIT_ADAPTIVE_LOG_LEVEL
    else process.env.MUKIT_ADAPTIVE_LOG_LEVEL = previous
  }
})
