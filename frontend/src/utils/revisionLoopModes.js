/**
 * Product revision modes for multi-agent preview (mirrors backend caps).
 */

export const REVISION_MODE_MAX_PASSES = Object.freeze({
  off: 0,
  fast: 1,
  balanced: 2,
  thorough: 3,
});

export function normalizeRevisionMode(mode) {
  const key = String(mode || 'off').trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(REVISION_MODE_MAX_PASSES, key) ? key : 'off';
}

export function maxPassesForRevisionMode(mode) {
  return REVISION_MODE_MAX_PASSES[normalizeRevisionMode(mode)];
}
