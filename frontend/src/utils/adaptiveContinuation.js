/** Events that may be placed on the private continuation queue. */

export function continuationEventsToSchedule(snapshot, buffer, positionTick) {
  if (!snapshot || snapshot.audible !== true) {
    return [];
  }
  const kind = snapshot.fallback_kind;
  const allowed = snapshot.source === 'model'
    || kind === 'motif_variation'
    || kind === 'accompaniment';
  if (!allowed || kind === 'reuse_loop') {
    return [];
  }
  const playhead = Number(positionTick);
  const floor = Number.isFinite(playhead) ? playhead : 0;
  const events = Array.isArray(buffer?.events) ? buffer.events : [];
  return events.filter((event) => Number(event?.start_tick) >= floor);
}
