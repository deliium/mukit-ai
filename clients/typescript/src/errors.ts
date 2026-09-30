/** Client errors. The message does not include a bearer token. */

export class AdaptiveClientError extends Error {
  readonly code: string
  readonly status: number | null
  readonly sessionId: string | null
  readonly retryAfterMs: number | null
  readonly details: Record<string, unknown> | null

  constructor(
    code: string,
    message: string,
    options: {
      status?: number | null
      sessionId?: string | null
      retryAfterMs?: number | null
      details?: Record<string, unknown> | null
    } = {},
  ) {
    super(message)
    this.name = "AdaptiveClientError"
    this.code = code
    this.status = options.status ?? null
    this.sessionId = options.sessionId ?? null
    this.retryAfterMs = options.retryAfterMs ?? null
    this.details = options.details ?? null
  }
}
