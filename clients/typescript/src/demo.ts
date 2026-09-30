/** Terminal loop for exploration, danger, combat, and victory.

Quit with `q` closes sockets and leaves the server session. `--stop`
deletes that session on quit.
*/

import { existsSync } from "node:fs"
import { dirname, join } from "node:path"

import { MukitAdaptiveClient } from "./client.js"
import { AdaptiveClientError } from "./errors.js"
import { logEvent } from "./log.js"
import { applyPhase, loadPhaseRecipe, type PhaseRecipe } from "./phases.js"

const REQUIRED_ENV = [
  "MUKIT_ADAPTIVE_PROJECT_ID",
  "MUKIT_ADAPTIVE_SCORE_ID",
  "MUKIT_ADAPTIVE_DOCUMENT_REVISION",
] as const

const PHASE_KEYS: Record<string, string> = {
  "1": "exploration",
  "2": "danger",
  "3": "combat",
  "4": "victory",
}

const SNAPSHOT_FIELDS = ["runtime_state_id", "bar", "beat", "intensity", "transport", "phase"] as const

export interface DemoEnv {
  [name: string]: string | undefined
}

export interface DemoOptions {
  argv?: string[]
  env?: DemoEnv
  lines?: string[]
  client?: MukitAdaptiveClient
  listen?: boolean
  write?: (line: string) => void
}

interface DemoConfig {
  baseUrl: string
  projectId: string
  scoreId: string
  revision: number
  token: string | null
}

export async function main(argv: string[] = process.argv.slice(1)): Promise<number> {
  const lines: string[] = []
  let pending = ""
  process.stdin.setEncoding("utf8")
  for await (const chunk of process.stdin) {
    pending += chunk
    const parts = pending.split("\n")
    pending = parts.pop() ?? ""
    lines.push(...parts)
  }
  if (pending) lines.push(pending)
  return runDemo({ argv, lines })
}

export async function runDemo(options: DemoOptions = {}): Promise<number> {
  const argv = options.argv ?? []
  const env = options.env ?? process.env
  const listen = options.listen ?? true
  const write = options.write ?? ((line) => process.stdout.write(`${line}\n`))
  logEvent("DEBUG", "demo_enter", { stop_on_quit: argv.includes("--stop") })
  const config = readConfig(env)
  if (!config) {
    logEvent("DEBUG", "demo_exit", { status: 1 })
    return 1
  }
  let recipe: PhaseRecipe
  let mapping: Record<string, unknown>
  let cues: Array<Record<string, unknown>>
  try {
    recipe = loadPhaseRecipe(recipePath(env))
    const parts = startParts(recipe)
    mapping = parts.mapping
    cues = parts.cues
  } catch (error) {
    logEvent("ERROR", "demo_recipe_failed", { code: error instanceof AdaptiveClientError ? error.code : "engine_payload_invalid" })
    logEvent("DEBUG", "demo_exit", { status: 1 })
    return 1
  }
  const owned = options.client === undefined
  const client = options.client ?? new MukitAdaptiveClient(config.baseUrl, config.token)
  try {
    await client.start(config.projectId, config.scoreId, config.revision, mapping, cues)
    if (listen) await client.listen(null, null, null)
    for (const raw of options.lines ?? []) {
      if (await handleLine(client, recipe, raw, argv.includes("--stop"), write)) {
        logEvent("DEBUG", "demo_exit", { status: 0 })
        return 0
      }
    }
    await quit(client, argv.includes("--stop"))
  } catch (error) {
    logEvent("ERROR", "demo_failed", { code: error instanceof AdaptiveClientError ? error.code : "engine_payload_invalid" })
    if (owned) await client.close()
    logEvent("DEBUG", "demo_exit", { status: 1 })
    return 1
  }
  logEvent("DEBUG", "demo_exit", { status: 0 })
  return 0
}

export function formatSnapshot(snapshot: {
  runtime_state_id: string
  bar: number
  beat: number
  intensity: number
  transport: string
  phase: string
} | null): string {
  if (!snapshot) return SNAPSHOT_FIELDS.map((name) => `${name}=none`).join(" ")
  return SNAPSHOT_FIELDS.map((name) => `${name}=${String(snapshot[name])}`).join(" ")
}

function readConfig(env: DemoEnv): DemoConfig | null {
  let missing = false
  const values: Record<string, string> = {}
  for (const name of REQUIRED_ENV) {
    const raw = (env[name] ?? "").trim()
    if (!raw) {
      logEvent("ERROR", "demo_env_missing", { variable: name })
      missing = true
      continue
    }
    values[name] = raw
  }
  if (missing) return null
  const revision = parseRevision(values.MUKIT_ADAPTIVE_DOCUMENT_REVISION)
  if (revision === null) {
    logEvent("ERROR", "demo_env_missing", { variable: "MUKIT_ADAPTIVE_DOCUMENT_REVISION" })
    return null
  }
  const token = (env.MUKIT_ADAPTIVE_TOKEN ?? "").trim()
  return {
    baseUrl: (env.MUKIT_ADAPTIVE_BASE_URL ?? "").trim() || "http://127.0.0.1:8000",
    projectId: values.MUKIT_ADAPTIVE_PROJECT_ID,
    scoreId: values.MUKIT_ADAPTIVE_SCORE_ID,
    revision,
    token: token || null,
  }
}

function parseRevision(raw: string): number | null {
  if (!/^[1-9][0-9]*$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) ? value : null
}

function recipePath(env: DemoEnv): string {
  const explicit = (env.MUKIT_ADAPTIVE_PHASES ?? "").trim()
  if (explicit) return explicit
  let directory = decodeURIComponent(new URL(".", import.meta.url).pathname)
  for (let depth = 0; depth < 8; depth += 1) {
    for (const relative of ["clients/fixtures/game-phases.v1.json", "fixtures/game-phases.v1.json"]) {
      const candidate = join(directory, relative)
      if (existsSync(candidate)) return candidate
    }
    const parent = dirname(directory)
    if (parent === directory) break
    directory = parent
  }
  throw new AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
}

function startParts(recipe: PhaseRecipe): { mapping: Record<string, unknown>; cues: Array<Record<string, unknown>> } {
  const start = recipe.start
  if (!start || !start.mapping || !start.cues) {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
  }
  return { mapping: start.mapping, cues: start.cues }
}

async function handleLine(
  client: MukitAdaptiveClient,
  recipe: PhaseRecipe,
  raw: string,
  stopOnQuit: boolean,
  write: (line: string) => void,
): Promise<boolean> {
  const key = raw.trim()
  const phaseName = PHASE_KEYS[key]
  if (phaseName) {
    try {
      await applyPhase(client, recipe, phaseName)
    } catch (error) {
      logEvent("ERROR", "demo_command_failed", {
        code: error instanceof AdaptiveClientError ? error.code : "engine_payload_invalid",
        phase: phaseName,
        session_id: client.snapshot?.session_id ?? "none",
      })
    }
    return false
  }
  if (key === "s") {
    write(formatSnapshot(client.snapshot))
    return false
  }
  if (key === "r") {
    client.reconnect_now()
    return false
  }
  if (key === "q") {
    await quit(client, stopOnQuit)
    return true
  }
  logEvent("DEBUG", "demo_key_ignored")
  return false
}

async function quit(client: MukitAdaptiveClient, stopOnQuit: boolean): Promise<void> {
  if (stopOnQuit) {
    await client.stop()
    return
  }
  await client.close()
}

const entry = process.argv[1] ?? ""
if (entry.endsWith("/demo.js") || entry.endsWith("\\demo.js")) {
  main().then((status) => {
    process.exitCode = status
  })
}
