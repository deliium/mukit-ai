/** Public wire documents. Extra keys are rejected. */

import { AdaptiveClientError } from "./errors.js"

export const SESSION_SCHEMA = "adaptive.engine.session.v1"
export const ACK_SCHEMA = "adaptive.engine.ack.v1"
export const ERROR_SCHEMA = "adaptive.engine.error.v1"
export const CONTEXT_SCHEMA = "adaptive.context.external.v1"
export const PHASES_SCHEMA = "adaptive.client.phases.v1"

const SESSION_ID = /^aeng_[0-9a-f]{8}$/
const REQUEST_ID = /^req_[a-z0-9]{1,32}$/
const CONTEXT_KEY = /^[A-Za-z][A-Za-z0-9_.]{0,63}$/

const SESSION_FIELDS = [
  "schema_version", "session_id", "project_id", "score_id", "clock_owner", "transport",
  "runtime_state_id", "bar", "beat", "intensity", "phase", "active_stinger_id",
  "pending_transition_id", "pending_to_state_id", "warnings", "document_revision",
  "context_attached", "telemetry",
]
const TELEMETRY_FIELDS = ["command_count", "rejected_count", "coalesced_count", "dropped_context_count", "ack_count"]
const ACK_FIELDS = ["schema_version", "session_id", "request_id", "kind", "disposition", "runtime_state_id", "detail_code"]
const ERROR_FIELDS = ["schema_version", "code", "message", "session_id", "retry_after_ms", "details"]
const COMMAND_FIELDS = ["session", "disposition", "request_id", "coalesced", "applied", "retry_after_ms"]

export interface EngineTelemetry {
  command_count: number
  rejected_count: number
  coalesced_count: number
  dropped_context_count: number
  ack_count: number
}

export interface EngineSession {
  schema_version: string
  session_id: string
  project_id: string
  score_id: string
  clock_owner: "engine" | "existing"
  transport: "playing" | "held" | "stopped"
  runtime_state_id: string
  bar: number
  beat: number
  intensity: number
  phase: "bed" | "phrase" | "stinger"
  active_stinger_id: string | null
  pending_transition_id: string | null
  pending_to_state_id: string | null
  warnings: string[]
  document_revision: number
  context_attached: boolean
  telemetry: EngineTelemetry
}

export interface EngineAck {
  schema_version: string
  session_id: string
  request_id: string | null
  kind: "state" | "intensity" | "stinger" | "cue" | "context"
  disposition: "committed" | "queued" | "rejected" | "finished"
  runtime_state_id: string
  detail_code: string | null
}

export interface EngineCommandResult {
  session: EngineSession
  disposition: "committed" | "queued" | "rejected"
  request_id: string | null
  coalesced: boolean
  applied: boolean
  retry_after_ms: number | null
}

export function requestIdOk(value: string): boolean {
  return REQUEST_ID.test(value)
}

export function sessionIdOk(value: string): boolean {
  return SESSION_ID.test(value)
}

export function parseSession(data: unknown): EngineSession {
  const body = requireObject(data, SESSION_FIELDS, "session")
  if (body.schema_version !== SESSION_SCHEMA) invalid("schema_version")
  const sessionId = requireText(body.session_id, "session_id")
  if (!sessionIdOk(sessionId)) invalid("session_id")
  const pendingTransition = optionalText(body.pending_transition_id, "pending_transition_id")
  const pendingState = optionalText(body.pending_to_state_id, "pending_to_state_id")
  if ((pendingTransition === null) !== (pendingState === null)) invalid("pending_transition_id")
  if (!Array.isArray(body.warnings) || body.warnings.length > 8) invalid("warnings")
  const warnings = body.warnings.map((item) => {
    if (typeof item !== "string" || item.length === 0) invalid("warnings")
    return item
  })
  const session: EngineSession = {
    schema_version: SESSION_SCHEMA,
    session_id: sessionId,
    project_id: requireText(body.project_id, "project_id"),
    score_id: requireText(body.score_id, "score_id"),
    clock_owner: requireChoice(body.clock_owner, ["engine", "existing"], "clock_owner"),
    transport: requireChoice(body.transport, ["playing", "held", "stopped"], "transport"),
    runtime_state_id: requireText(body.runtime_state_id, "runtime_state_id"),
    bar: requireInt(body.bar, "bar", 1),
    beat: requireInt(body.beat, "beat", 1),
    intensity: requireUnit(body.intensity, "intensity"),
    phase: requireChoice(body.phase, ["bed", "phrase", "stinger"], "phase"),
    active_stinger_id: optionalText(body.active_stinger_id, "active_stinger_id"),
    pending_transition_id: pendingTransition,
    pending_to_state_id: pendingState,
    warnings,
    document_revision: requireInt(body.document_revision, "document_revision", 1),
    context_attached: requireBool(body.context_attached, "context_attached"),
    telemetry: parseTelemetry(body.telemetry),
  }
  return Object.freeze(session)
}

export function parseAck(data: unknown): EngineAck {
  const body = requireObject(data, ACK_FIELDS, "ack")
  if (body.schema_version !== ACK_SCHEMA) invalid("schema_version")
  const sessionId = requireText(body.session_id, "session_id")
  if (!sessionIdOk(sessionId)) invalid("session_id")
  let requestId: string | null = null
  if (body.request_id !== null) {
    requestId = requireText(body.request_id, "request_id")
    if (!requestIdOk(requestId)) invalid("request_id")
  }
  let detail: string | null = null
  if (body.detail_code !== null) detail = requireText(body.detail_code, "detail_code")
  return Object.freeze({
    schema_version: ACK_SCHEMA,
    session_id: sessionId,
    request_id: requestId,
    kind: requireChoice(body.kind, ["state", "intensity", "stinger", "cue", "context"], "kind"),
    disposition: requireChoice(body.disposition, ["committed", "queued", "rejected", "finished"], "disposition"),
    runtime_state_id: requireText(body.runtime_state_id, "runtime_state_id"),
    detail_code: detail,
  })
}

export function parseErrorDocument(data: unknown, status: number | null = null): AdaptiveClientError {
  const body = requireObject(data, ERROR_FIELDS, "error", ["schema_version", "code", "message"])
  if (body.schema_version !== ERROR_SCHEMA) invalid("schema_version")
  let sessionId: string | null = null
  if (body.session_id != null) sessionId = requireText(body.session_id, "session_id")
  let retryAfter: number | null = null
  if (body.retry_after_ms != null) retryAfter = requireInt(body.retry_after_ms, "retry_after_ms", 0)
  if (body.details != null && !isRecord(body.details)) invalid("details")
  return new AdaptiveClientError(requireText(body.code, "code"), requireText(body.message, "message"), {
    status,
    sessionId,
    retryAfterMs: retryAfter,
    details: body.details == null ? null : body.details,
  })
}

export function parseCommandResult(data: unknown): EngineCommandResult {
  const body = requireObject(data, COMMAND_FIELDS, "command")
  let requestId: string | null = null
  if (body.request_id !== null) {
    requestId = requireText(body.request_id, "request_id")
    if (!requestIdOk(requestId)) invalid("request_id")
  }
  let retryAfter: number | null = null
  if (body.retry_after_ms !== null) retryAfter = requireInt(body.retry_after_ms, "retry_after_ms", 0)
  return Object.freeze({
    session: parseSession(body.session),
    disposition: requireChoice(body.disposition, ["committed", "queued", "rejected"], "disposition"),
    request_id: requestId,
    coalesced: requireBool(body.coalesced, "coalesced"),
    applied: requireBool(body.applied, "applied"),
    retry_after_ms: retryAfter,
  })
}

export function validateContextValues(values: unknown): Record<string, unknown> {
  if (!isRecord(values)) invalid("values")
  const cleaned: Record<string, unknown> = {}
  for (const [key, item] of Object.entries(values)) {
    if (!CONTEXT_KEY.test(key)) invalid("values")
    cleaned[key] = scalarValue(item)
  }
  return cleaned
}

function parseTelemetry(data: unknown): EngineTelemetry {
  const body = requireObject(data, TELEMETRY_FIELDS, "telemetry")
  return Object.freeze({
    command_count: requireInt(body.command_count, "command_count", 0),
    rejected_count: requireInt(body.rejected_count, "rejected_count", 0),
    coalesced_count: requireInt(body.coalesced_count, "coalesced_count", 0),
    dropped_context_count: requireInt(body.dropped_context_count, "dropped_context_count", 0),
    ack_count: requireInt(body.ack_count, "ack_count", 0),
  })
}

function scalarValue(item: unknown): unknown {
  if (typeof item === "boolean") return item
  if (typeof item === "number") {
    if (!Number.isFinite(item)) invalid("values")
    return item
  }
  if (typeof item === "string") {
    if (item.length < 1 || item.length > 80) invalid("values")
    return item
  }
  if (Array.isArray(item)) {
    if (item.length < 1 || item.length > 32) invalid("values")
    return item.map((entry) => {
      if (typeof entry !== "string" || entry.length < 1 || entry.length > 80) invalid("values")
      return entry
    })
  }
  invalid("values")
}

function requireObject(data: unknown, allowed: string[], field: string, required: string[] = allowed): Record<string, unknown> {
  if (!isRecord(data)) invalid(field)
  const keys = Object.keys(data)
  const extra = keys.filter((key) => !allowed.includes(key))
  const missing = required.filter((key) => !keys.includes(key))
  if (extra.length > 0 || missing.length > 0) invalid(field)
  return data
}

function requireText(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) invalid(field)
  return value
}

function optionalText(value: unknown, field: string): string | null {
  if (value === null) return null
  return requireText(value, field)
}

function requireChoice<T extends string>(value: unknown, allowed: readonly T[], field: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) invalid(field)
  return value as T
}

function requireBool(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") invalid(field)
  return value
}

function requireInt(value: unknown, field: string, minimum: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) invalid(field)
  return value
}

function requireUnit(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) invalid(field)
  return value
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function invalid(field: string): never {
  throw new AdaptiveClientError("engine_payload_invalid", `invalid ${field}`)
}
