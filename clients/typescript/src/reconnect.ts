/** Pure reconnect delay. No jitter. */

export const INITIAL_DELAY_MS = 200
export const MAX_DELAY_MS = 5000
export const DEFAULT_MAX_ATTEMPTS = 8

export function delayMsForAttempt(attempt: number): number {
  if (attempt < 1) throw new Error("attempt must be at least 1")
  return Math.min(INITIAL_DELAY_MS * 2 ** (attempt - 1), MAX_DELAY_MS)
}

export class ReconnectCounter {
  attempt = 0

  constructor(readonly maxAttempts = DEFAULT_MAX_ATTEMPTS) {
    if (maxAttempts < 1) throw new Error("maxAttempts must be at least 1")
  }

  nextDelayMs(): number | null {
    this.attempt += 1
    if (this.attempt > this.maxAttempts) return null
    return delayMsForAttempt(this.attempt)
  }

  reset(): void {
    this.attempt = 0
  }
}
