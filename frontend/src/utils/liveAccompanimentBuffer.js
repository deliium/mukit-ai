/**
 * Ephemeral accompaniment buffer — never autosaved / never revision snapshot.
 *
 * Holds scheduled + pending note events for the prediction horizon.
 * Transport IDs are owned by liveAccompanimentScheduler / engine live list.
 */

import { createAppLogger } from './appLogger.js';
import {
  LIVE_ACCOMPANIMENT_CHUNK_SCHEMA,
  normalizeLiveAccompanimentChunk,
  readLiveHorizonBounds,
} from './liveSessionContracts.js';

const log = createAppLogger('liveAccompaniment');

/**
 * @typedef {{
 *   pitch: number,
 *   start_tick: number,
 *   duration_ticks: number,
 *   velocity: number,
 *   track_role?: string,
 *   source?: string,
 *   request_id?: string,
 *   scheduleId?: number|null,
 * }} LiveBufferEvent
 */

/**
 * @param {{ maxEvents?: number }} [options]
 */
export function createLiveAccompanimentBuffer(options = {}) {
  const bounds = readLiveHorizonBounds();
  const maxEvents = Math.max(
    1,
    Math.min(512, Math.round(Number(options.maxEvents) || bounds.maxEventsPerChunk * 4)),
  );

  /** @type {LiveBufferEvent[]} */
  let events = [];
  /** Highest tick covered (exclusive end of last scheduled note span). */
  let coveredThroughTick = 0;
  let insertCount = 0;
  let dropCount = 0;

  function clear({ reason = 'clear' } = {}) {
    const before = events.length;
    events = [];
    coveredThroughTick = 0;
    log.debug('buffer clear', { reason, clearedCount: before });
    return before;
  }

  /**
   * @param {unknown} chunk
   */
  function insertChunk(chunk) {
    const normalized = normalizeLiveAccompanimentChunk(chunk);
    if (!normalized.ok) {
      log.warn('chunk insert rejected', {
        code: normalized.code,
        details: normalized.details,
      });
      return { ok: false, code: normalized.code, inserted: 0 };
    }
    const { chunk: safe } = normalized;
    let inserted = 0;
    for (const ev of safe.events) {
      events.push({
        ...ev,
        source: safe.source,
        request_id: safe.request_id,
        scheduleId: null,
      });
      inserted += 1;
      const end = ev.start_tick + ev.duration_ticks;
      if (end > coveredThroughTick) {
        coveredThroughTick = end;
      }
    }
    insertCount += inserted;
    while (events.length > maxEvents) {
      events.shift();
      dropCount += 1;
    }
    log.info('schedule fill', {
      inserted,
      eventCount: events.length,
      coveredThroughTick,
      source: safe.source,
      schema: LIVE_ACCOMPANIMENT_CHUNK_SCHEMA,
    });
    return { ok: true, inserted, coveredThroughTick };
  }

  /**
   * @param {LiveBufferEvent[]} noteEvents
   * @param {{ source?: string }} [meta]
   */
  function insertEvents(noteEvents, meta = {}) {
    return insertChunk({
      request_id: meta.request_id || `local-${insertCount}`,
      session_id: meta.session_id || 'local',
      start_tick: noteEvents[0]?.start_tick ?? 0,
      events: noteEvents,
      source: meta.source || 'local_pattern',
      generated_at_ms: Date.now(),
    });
  }

  function getEvents() {
    return events.slice();
  }

  function getUnscheduledEvents({ fromTick = 0 } = {}) {
    const from = Math.max(0, Math.round(Number(fromTick) || 0));
    return events.filter(
      (ev) => ev.scheduleId == null && ev.start_tick + ev.duration_ticks > from,
    );
  }

  function markScheduled(indexOrEvent, scheduleId) {
    if (typeof indexOrEvent === 'number') {
      if (events[indexOrEvent]) {
        events[indexOrEvent].scheduleId = scheduleId;
      }
      return;
    }
    const target = indexOrEvent;
    const found = events.find(
      (ev) =>
        ev === target
        || (
          ev.pitch === target.pitch
          && ev.start_tick === target.start_tick
          && ev.duration_ticks === target.duration_ticks
          && ev.scheduleId == null
        ),
    );
    if (found) {
      found.scheduleId = scheduleId;
    }
  }

  function clearScheduleIds() {
    for (const ev of events) {
      ev.scheduleId = null;
    }
  }

  /**
   * Drop events that end at or before playhead (already sounded).
   */
  function prunePast(playheadTick) {
    const tick = Math.max(0, Math.round(Number(playheadTick) || 0));
    const before = events.length;
    events = events.filter((ev) => ev.start_tick + ev.duration_ticks > tick);
    return before - events.length;
  }

  function getCoverage() {
    return {
      coveredThroughTick,
      eventCount: events.length,
      unscheduledCount: events.filter((ev) => ev.scheduleId == null).length,
      insertCount,
      dropCount,
      maxEvents,
    };
  }

  /**
   * @param {number} playheadTick
   * @param {number} horizonEndTick
   */
  function uncoveredHorizon(playheadTick, horizonEndTick) {
    const playhead = Math.max(0, Math.round(Number(playheadTick) || 0));
    const horizonEnd = Math.max(playhead, Math.round(Number(horizonEndTick) || 0));
    const gapStart = Math.max(playhead, coveredThroughTick);
    return {
      needsFill: gapStart < horizonEnd,
      gapStart,
      horizonEnd,
      coveredThroughTick,
    };
  }

  return {
    clear,
    insertChunk,
    insertEvents,
    getEvents,
    getUnscheduledEvents,
    markScheduled,
    clearScheduleIds,
    prunePast,
    getCoverage,
    uncoveredHorizon,
    getCoveredThroughTick: () => coveredThroughTick,
  };
}
