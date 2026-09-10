/**
 * Ephemeral playback loop bounds helpers.
 * Loop state is UI/transport only — never persisted in Composition V2.
 */

/**
 * @typedef {{ startTick: number, endTick: number, enabled: boolean }} PlaybackLoop
 */

/**
 * Normalize a playback loop against composition duration.
 * Returns null when the range is missing or endTick <= startTick.
 *
 * @param {Partial<PlaybackLoop>|null|undefined} loop
 * @param {object|null} [composition]
 * @returns {PlaybackLoop|null}
 */
export function normalizePlaybackLoop(loop, composition = null) {
  if (!loop || typeof loop !== 'object') {
    return null;
  }
  let startTick = Number(loop.startTick);
  let endTick = Number(loop.endTick);
  if (!Number.isFinite(startTick) || !Number.isFinite(endTick)) {
    return null;
  }
  startTick = Math.round(startTick);
  endTick = Math.round(endTick);
  if (startTick < 0 || endTick <= startTick) {
    return null;
  }

  const duration = Number(composition?.duration_ticks);
  if (Number.isFinite(duration) && duration >= 0) {
    startTick = Math.max(0, Math.min(startTick, duration));
    endTick = Math.max(0, Math.min(endTick, duration));
    if (endTick <= startTick) {
      return null;
    }
  }

  return {
    startTick,
    endTick,
    enabled: Boolean(loop.enabled),
  };
}

/**
 * Clamp an existing loop after composition edits/replacement.
 * Clears (returns null) when the window collapses.
 *
 * @param {Partial<PlaybackLoop>|null|undefined} loop
 * @param {object|null} composition
 * @returns {PlaybackLoop|null}
 */
export function reconcilePlaybackLoop(loop, composition) {
  if (!loop) {
    return null;
  }
  return normalizePlaybackLoop(loop, composition);
}

/**
 * Derive loop bounds from selected notes or an inclusive AI bar range.
 * Prefers note selection when it yields a valid tick window; otherwise uses bars.
 *
 * @returns {{ startTick: number, endTick: number, source: 'notes'|'bars' }|null}
 */
export function deriveLoopRangeFromSelection({
  composition = null,
  noteRange = null,
  startBar = null,
  endBar = null,
  barRangeResolver = null,
} = {}) {
  const noteStart = Number(noteRange?.startTick);
  const noteEnd = Number(noteRange?.endTick);
  if (Number.isFinite(noteStart) && Number.isFinite(noteEnd) && noteEnd > noteStart) {
    const normalized = normalizePlaybackLoop(
      { startTick: noteStart, endTick: noteEnd, enabled: true },
      composition,
    );
    if (normalized) {
      return {
        startTick: normalized.startTick,
        endTick: normalized.endTick,
        source: 'notes',
      };
    }
  }

  const startBarNum = Number(startBar);
  const endBarNum = Number(endBar);
  if (
    typeof barRangeResolver === 'function'
    && startBar != null
    && endBar != null
    && Number.isInteger(startBarNum)
    && Number.isInteger(endBarNum)
    && startBarNum >= 1
    && endBarNum >= startBarNum
  ) {
    const barBounds = barRangeResolver(startBarNum, endBarNum, composition);
    const barStart = Number(barBounds?.startTick);
    const barEnd = Number(barBounds?.endTick);
    if (Number.isFinite(barStart) && Number.isFinite(barEnd) && barEnd > barStart) {
      const normalized = normalizePlaybackLoop(
        { startTick: barStart, endTick: barEnd, enabled: true },
        composition,
      );
      if (normalized) {
        return {
          startTick: normalized.startTick,
          endTick: normalized.endTick,
          source: 'bars',
        };
      }
    }
  }

  return null;
}
