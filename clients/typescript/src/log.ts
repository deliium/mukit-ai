/**
 * Logger for the adaptive client.
 * Level follows MUKIT_ADAPTIVE_LOG_LEVEL, then LOG_LEVEL, then INFO.
 * The bearer token, Authorization header, context values, mappings, and cues are never written.
 */

const RANKS: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 }

export function currentLevel(): string {
  const selected = process.env.MUKIT_ADAPTIVE_LOG_LEVEL || process.env.LOG_LEVEL || "INFO"
  const name = selected.toUpperCase()
  return name in RANKS ? name : "INFO"
}

export function logEvent(level: "DEBUG" | "INFO" | "WARNING" | "ERROR", event: string, fields: Record<string, unknown> = {}): void {
  if (RANKS[level] < RANKS[currentLevel()]) return
  const pairs = Object.keys(fields)
    .sort()
    .map((key) => `${key}=${String(fields[key])}`)
    .join(" ")
  const line = pairs ? `${event} ${pairs}` : event
  const write = level === "ERROR" ? console.error : console.log
  write(`${level} mukit_adaptive ${line}`)
}
