/** Pure helpers for Ardour companion UI gating and status polling. */

export const ARDOUR_STATUS_POLL_MS = 1000;

export const CONTROL_READY_STATES = new Set(['connected', 'awaiting_feedback']);

/**
 * Controls are enabled only with session control_permission (from status/connect)
 * and a live-ish connection_state. Do not pass a local unchecked→checked toggle
 * that was never sent on connect.
 */
export function canControlArdour({ controlPermission, connectionState }) {
  return Boolean(controlPermission) && CONTROL_READY_STATES.has(connectionState);
}

/**
 * Start a 1000 ms status poll. Returns a clear function (always safe to call).
 * Does not connect and does not write the score.
 */
export function startArdourStatusPoll(fetchStatus, { intervalMs = ARDOUR_STATUS_POLL_MS, setIntervalFn = setInterval, clearIntervalFn = clearInterval } = {}) {
  if (typeof fetchStatus !== 'function') {
    throw new TypeError('fetchStatus must be a function');
  }
  const id = setIntervalFn(() => {
    void fetchStatus();
  }, intervalMs);
  return () => {
    clearIntervalFn(id);
  };
}

export function formatArdourStatusBanner(status) {
  if (!status || status.enabled === false) {
    return 'Companion disabled (ARDOUR_COMPANION_ENABLED)';
  }
  const state = status.connection_state || 'disconnected';
  const age = status.feedback_age_ms == null ? 'n/a' : `${status.feedback_age_ms} ms`;
  const stale = status.stale ? 'yes' : 'no';
  return `state=${state} · feedback_age=${age} · stale=${stale}`;
}
