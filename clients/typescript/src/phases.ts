/** Apply one game phase from adaptive.client.phases.v1. */

import { readFileSync } from "node:fs"

import { AdaptiveClientError } from "./errors.js"
import { logEvent } from "./log.js"
import { PHASES_SCHEMA } from "./models.js"
import type { MukitAdaptiveClient } from "./client.js"

const PHASE_NAMES = ["exploration", "danger", "combat", "victory"] as const

export interface PhaseSpec {
  state_id?: string
  cue?: string
  stinger_id?: string
  intensity: number
  threat: number | string
}

export interface PhaseRecipe {
  schema_version: string
  start?: { mapping?: Record<string, unknown>; cues?: Array<Record<string, unknown>> }
  phases: Record<string, PhaseSpec>
}

export function loadPhaseRecipe(path: string): PhaseRecipe {
  logEvent("DEBUG", "phase_recipe_load_enter", { filename: path.split("/").at(-1) ?? path })
  let loaded: unknown
  try {
    loaded = JSON.parse(readFileSync(path, "utf8"))
  } catch (error) {
    logEvent("ERROR", "phase_recipe_load_failed", { error_class: error instanceof Error ? error.name : "Error" })
    throw new AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
  }
  const recipe = validateRecipe(loaded)
  logEvent("DEBUG", "phase_recipe_load_exit", { filename: path.split("/").at(-1) ?? path })
  return recipe
}

export async function applyPhase(client: MukitAdaptiveClient, recipe: PhaseRecipe, phaseName: string): Promise<unknown[]> {
  logEvent("DEBUG", "apply_phase_enter", { phase: phaseName })
  const checked = validateRecipe(recipe)
  if (!PHASE_NAMES.includes(phaseName as (typeof PHASE_NAMES)[number])) {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid phase")
  }
  const spec = checked.phases[phaseName]
  const results: unknown[] = []
  if (spec.cue) {
    results.push(await client.fire_cue(spec.cue))
  } else {
    results.push(await client.set_state(spec.state_id ?? ""))
    if (spec.stinger_id) results.push(await client.fire_stinger(spec.stinger_id))
  }
  results.push(await client.set_intensity(spec.intensity))
  const snapshot = client.snapshot
  const sessionId = snapshot?.session_id ?? "none"
  if (snapshot?.context_attached) {
    results.push(await client.send_context({ threat: spec.threat }))
  } else {
    logEvent("INFO", "context_skipped", { session_id: sessionId, phase: phaseName })
  }
  const latest = client.snapshot
  logEvent("INFO", "phase_applied", {
    phase: phaseName,
    session_id: latest?.session_id ?? sessionId,
    runtime_state_id: latest?.runtime_state_id ?? "none",
  })
  logEvent("DEBUG", "apply_phase_exit", { phase: phaseName, calls: results.length })
  return results
}

function validateRecipe(loaded: unknown): PhaseRecipe {
  if (!isRecord(loaded) || loaded.schema_version !== PHASES_SCHEMA || !isRecord(loaded.phases)) {
    throw new AdaptiveClientError("engine_payload_invalid", "invalid phase recipe")
  }
  for (const name of PHASE_NAMES) {
    const spec = loaded.phases[name]
    if (!isRecord(spec) || !("intensity" in spec) || !("threat" in spec)) {
      throw new AdaptiveClientError("engine_payload_invalid", "invalid phase")
    }
    if (!spec.cue && !spec.state_id) throw new AdaptiveClientError("engine_payload_invalid", "invalid phase")
  }
  return loaded as unknown as PhaseRecipe
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}
