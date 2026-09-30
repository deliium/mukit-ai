import assert from "node:assert/strict"
import { test } from "node:test"

import { MukitAdaptiveClient } from "../src/client.js"
import { AdaptiveClientError } from "../src/errors.js"
import { applyPhase, loadPhaseRecipe } from "../src/phases.js"
import {
  RecordingFetch,
  assertPackageHasNoStudioImport,
  captureLogs,
  commandDocument,
  errorBody,
  sessionDocument,
} from "./fakes.js"

async function started(transport: RecordingFetch, attached = true): Promise<MukitAdaptiveClient> {
  transport.push(201, sessionDocument({ context_attached: attached }))
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, { transport: transport.fetch })
  await adaptive.start("project", "score", 1)
  return adaptive
}

function queueCommands(transport: RecordingFetch, count: number, attached: boolean, stateId: string): void {
  for (let index = 0; index < count; index += 1) {
    const document = commandDocument()
    document.session = sessionDocument({ context_attached: attached, runtime_state_id: stateId })
    transport.push(200, document)
  }
}

test("set_state body fields", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport)
  transport.push(200, commandDocument())
  await adaptive.set_state("danger", "tr-now")
  const body = JSON.parse(transport.calls.at(-1)?.body ?? "{}") as Record<string, unknown>
  assert.ok(transport.calls.at(-1)?.url.endsWith("/state"))
  assert.equal(body.to_state_id, "danger")
  assert.equal(body.transition_id, "tr-now")
  assert.equal(body.request_id, "req_00000001")
})

test("set_intensity sends 0.8", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport)
  transport.push(200, commandDocument())
  await adaptive.set_intensity(0.8)
  const body = JSON.parse(transport.calls.at(-1)?.body ?? "{}") as Record<string, unknown>
  assert.equal(body.intensity, 0.8)
})

test("cue and stinger discriminators", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport)
  transport.push(200, commandDocument())
  transport.push(200, commandDocument())
  await adaptive.fire_cue("combat")
  await adaptive.fire_stinger("stinger-hit", "tr-hit")
  const cue = JSON.parse(transport.calls.at(-2)?.body ?? "{}") as Record<string, unknown>
  const stinger = JSON.parse(transport.calls.at(-1)?.body ?? "{}") as Record<string, unknown>
  assert.deepEqual(cue, { kind: "cue", name: "combat", request_id: "req_00000001" })
  assert.equal(stinger.kind, "stinger")
  assert.equal(stinger.stinger_id, "stinger-hit")
  assert.equal(stinger.transition_id, "tr-hit")
})

test("context 202 does not issue a second request", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport)
  transport.push(202, commandDocument({
    disposition: "queued",
    request_id: null,
    coalesced: true,
    applied: false,
    retry_after_ms: 50,
  }))
  const result = await adaptive.send_context({ threat: 0.6 })
  assert.equal(result.coalesced, true)
  assert.equal(result.applied, false)
  const contextCalls = transport.calls.filter((call) => call.url.endsWith("/context"))
  assert.equal(contextCalls.length, 1)
  const body = JSON.parse(contextCalls[0].body ?? "{}") as { values: { threat: number } }
  assert.equal(body.values.threat, 0.6)
})

test("HTTP 200 rejected does not raise", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport)
  const rejected = commandDocument({ disposition: "rejected" })
  rejected.session = sessionDocument({ runtime_state_id: "danger" })
  transport.push(200, rejected)
  const result = await adaptive.set_state("danger")
  assert.equal(result.disposition, "rejected")
  assert.equal(adaptive.snapshot?.runtime_state_id, "danger")
})

test("one 429 retries once and a second 429 raises", async () => {
  const slept: number[] = []
  const transport = new RecordingFetch()
  const adaptive = new MukitAdaptiveClient("http://127.0.0.1:8000", null, {
    transport: transport.fetch,
    sleeper: async (delay) => {
      slept.push(delay)
    },
  })
  transport.push(201, sessionDocument())
  await adaptive.start("project", "score", 1)
  const limited = errorBody("engine_rate_limited", "The engine command rate is exceeded.", {
    session_id: "aeng_0123abcd",
    retry_after_ms: 2500,
  })
  transport.push(429, limited)
  transport.push(200, commandDocument())
  await adaptive.set_intensity(0.4)
  assert.deepEqual(slept, [1000])
  transport.push(429, limited)
  transport.push(429, limited)
  await assert.rejects(() => adaptive.set_intensity(0.4), (error: unknown) => {
    return error instanceof AdaptiveClientError && error.code === "engine_rate_limited"
  })
  assert.equal(transport.calls.filter((call) => call.url.endsWith("/intensity")).length, 4)
})

test("nested context object is rejected locally", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport)
  const before = transport.calls.length
  await assert.rejects(() => adaptive.send_context({ threat: { nested: 1 } }), (error: unknown) => {
    return error instanceof AdaptiveClientError && error.code === "engine_payload_invalid"
  })
  assert.equal(transport.calls.length, before)
})

test("logs omit context values and include warnings", async () => {
  process.env.LOG_LEVEL = "DEBUG"
  delete process.env.MUKIT_ADAPTIVE_LOG_LEVEL
  const logs = captureLogs()
  try {
    const transport = new RecordingFetch()
    const adaptive = await started(transport)
    const warned = commandDocument()
    warned.session = sessionDocument({ warnings: ["engine_queue_full"] })
    transport.push(200, warned)
    await adaptive.send_context({ threat: "threat-marker-9f3c" })
    assert.equal(logs.text().includes("threat-marker-9f3c"), false)
    assert.match(logs.text(), /kind=context/)
    assert.match(logs.text(), /engine_queue_full/)
  } finally {
    logs.restore()
    delete process.env.LOG_LEVEL
  }
})

test("combat order is cue then intensity then context", async () => {
  const transport = new RecordingFetch()
  const adaptive = await started(transport, true)
  queueCommands(transport, 3, true, "combat_state")
  const recipe = loadPhaseRecipe(decodeURIComponent(new URL("../../../fixtures/game-phases.v1.json", import.meta.url).pathname))
  await applyPhase(adaptive, recipe, "combat")
  const posts = transport.calls.filter((call) => call.method === "POST").slice(1)
  assert.deepEqual(posts.map((call) => call.url.split("/").at(-1)), ["event", "intensity", "context"])
  const cue = JSON.parse(posts[0].body ?? "{}") as { kind: string; name: string }
  const intensity = JSON.parse(posts[1].body ?? "{}") as { intensity: number }
  const context = JSON.parse(posts[2].body ?? "{}") as { values: { threat: number } }
  assert.equal(cue.kind, "cue")
  assert.equal(cue.name, "combat")
  assert.equal(intensity.intensity, 0.9)
  assert.equal(context.values.threat, 0.95)
})

test("false context_attached skips context", async () => {
  process.env.MUKIT_ADAPTIVE_LOG_LEVEL = "INFO"
  const logs = captureLogs()
  try {
    const transport = new RecordingFetch()
    const adaptive = await started(transport, false)
    queueCommands(transport, 2, false, "explore")
    const recipe = loadPhaseRecipe(decodeURIComponent(new URL("../../../fixtures/game-phases.v1.json", import.meta.url).pathname))
    await applyPhase(adaptive, recipe, "exploration")
    assert.equal(transport.calls.some((call) => call.url.endsWith("/context")), false)
    assert.match(logs.text(), /context_skipped/)
    assert.match(logs.text(), /phase=exploration/)
    assert.equal(logs.text().includes("0.1"), false)
  } finally {
    logs.restore()
    delete process.env.MUKIT_ADAPTIVE_LOG_LEVEL
  }
})

test("package source does not import the studio", () => {
  assertPackageHasNoStudioImport()
})
