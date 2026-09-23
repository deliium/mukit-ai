/**
 * Schedules ephemeral accompaniment onto the shared playback engine.
 *
 * Uses engine.scheduleLiveAt / clearLiveScheduledEvents — never a second
 * createPlaybackEngine, never global Transport.cancel().
 */

import { createAppLogger } from './appLogger.js';
import { ticksToPlaybackSeconds } from './playbackPosition.js';
import {
  getLivePlaybackEngine,
  requireLivePlaybackEngine,
} from './livePlaybackEngineAccess.js';
import { createLiveAccompanimentBuffer } from './liveAccompanimentBuffer.js';
import { readLiveHorizonBounds } from './liveSessionContracts.js';

const log = createAppLogger('liveAccompaniment');

/**
 * @param {{
 *   buffer?: ReturnType<typeof createLiveAccompanimentBuffer>,
 *   onTrigger?: (event: object, audioTime: number) => void,
 * }} [options]
 */
export function createLiveAccompanimentScheduler(options = {}) {
  const buffer = options.buffer || createLiveAccompanimentBuffer();
  const onTrigger =
    typeof options.onTrigger === 'function'
      ? options.onTrigger
      : (event, _audioTime) => {
        log.debug('live note trigger (no voice adapter)', {
          pitch: event.pitch,
          start_tick: event.start_tick,
        });
      };

  let active = false;
  let composition = null;

  function setComposition(next) {
    composition = next || null;
  }

  function start() {
    const gate = requireLivePlaybackEngine();
    if (!gate.ok) {
      log.warn('scheduler start failed', { code: gate.code });
      return gate;
    }
    active = true;
    log.info('scheduler start', {
      engineSession: gate.engine.getSessionId?.() ?? null,
    });
    return { ok: true };
  }

  function stop({ clearBuffer = false, reason = 'stop' } = {}) {
    active = false;
    const engine = getLivePlaybackEngine();
    let cleared = 0;
    if (engine?.clearLiveScheduledEvents) {
      cleared = engine.clearLiveScheduledEvents();
      buffer.clearScheduleIds();
    }
    if (clearBuffer) {
      buffer.clear({ reason });
    }
    log.info('scheduler stop', { reason, clearedLiveIds: cleared, clearBuffer });
    return { ok: true, cleared };
  }

  function cancel({ reason = 'cancel' } = {}) {
    return stop({ clearBuffer: true, reason });
  }

  /**
   * Clear/rebuild future live IDs after seek/relocate.
   */
  function onTransportRelocate(playheadSeconds) {
    const engine = getLivePlaybackEngine();
    if (!engine?.clearLiveScheduledEvents) {
      return 0;
    }
    const cleared = engine.clearLiveScheduledEvents({
      afterSeconds: Math.max(0, Number(playheadSeconds) || 0),
    });
    buffer.clearScheduleIds();
    log.debug('owned live ID clear', { reason: 'relocate', clearedCount: cleared });
    if (active) {
      schedulePendingFromPlayhead(playheadSeconds);
    }
    return cleared;
  }

  /**
   * Schedule unscheduled buffer events from playhead through horizon.
   * @param {number} [playheadSeconds]
   */
  function schedulePendingFromPlayhead(playheadSeconds) {
    if (!active) {
      return { scheduled: 0 };
    }
    const engine = getLivePlaybackEngine();
    if (!engine?.scheduleLiveAt) {
      log.warn('schedule without engine', { code: 'live_engine_unavailable' });
      return { scheduled: 0, code: 'live_engine_unavailable' };
    }

    const posSeconds =
      playheadSeconds != null && Number.isFinite(Number(playheadSeconds))
        ? Number(playheadSeconds)
        : (engine.getPositionSeconds?.() ?? 0);

    const clockTick = (() => {
      const pos = engine.getPlaybackPosition?.();
      return pos?.tick != null ? Math.round(pos.tick) : 0;
    })();

    buffer.prunePast(clockTick);
    const pending = buffer.getUnscheduledEvents({ fromTick: clockTick });
    let scheduled = 0;

    for (const ev of pending) {
      const when = ticksToPlaybackSeconds(ev.start_tick, { composition });
      if (when < posSeconds - 0.01) {
        continue;
      }
      const id = engine.scheduleLiveAt((audioTime) => {
        onTrigger(ev, audioTime);
      }, when);
      if (id != null) {
        buffer.markScheduled(ev, id);
        scheduled += 1;
      }
    }

    const coverage = buffer.getCoverage();
    log.info('horizon coverage', {
      scheduled,
      coveredThroughTick: coverage.coveredThroughTick,
      eventCount: coverage.eventCount,
      unscheduledCount: coverage.unscheduledCount,
      liveIdCount: engine.getLiveScheduledEventCount?.() ?? null,
    });
    return { scheduled, coverage };
  }

  /**
   * Insert chunk and schedule onto Transport.
   */
  function ingestChunk(chunk) {
    const result = buffer.insertChunk(chunk);
    if (!result.ok) {
      return result;
    }
    const scheduled = schedulePendingFromPlayhead();
    return { ...result, ...scheduled };
  }

  function ingestEvents(events, meta) {
    const result = buffer.insertEvents(events, meta);
    if (!result.ok) {
      return result;
    }
    const scheduled = schedulePendingFromPlayhead();
    return { ...result, ...scheduled };
  }

  /**
   * Horizon end tick from playhead + configured bars/ms.
   */
  function computeHorizonEndTick(playheadTick, horizon = {}) {
    const bounds = readLiveHorizonBounds();
    const bars = Number(horizon.bars) || bounds.barsDefault;
    const ms = Number(horizon.ms) || bounds.msDefault;
    const tpq = Number(composition?.ticks_per_quarter) || 480;
    const tempo = Number(composition?.tempo) || 100;
    const barTicks = tpq * 4; // approximate 4/4; clock uses timeline when available
    const fromBars = Math.round(playheadTick + bars * barTicks);
    const ticksFromMs = Math.round((ms / 1000) * (tempo / 60) * tpq);
    const fromMs = playheadTick + ticksFromMs;
    return Math.max(fromBars, fromMs);
  }

  /**
   * Fill uncovered horizon from local pattern engine (degradation-aware).
   * @param {{
   *   playheadTick: number,
   *   harmonySymbol: string|null,
   *   horizon?: { bars?: number, ms?: number },
   *   patternEngine: { generateWindow: Function, getDegradation: Function },
   *   scheduleSlackTicks?: number,
   * }} args
   */
  function maintainHorizonWithPattern({
    playheadTick,
    harmonySymbol,
    horizon = {},
    patternEngine,
    scheduleSlackTicks = 0,
  }) {
    if (!active || !patternEngine) {
      return { ok: false, filled: 0 };
    }
    const playhead = Math.max(0, Math.round(Number(playheadTick) || 0));
    const horizonEnd = computeHorizonEndTick(playhead, horizon);
    const slack = Math.max(0, Math.round(Number(scheduleSlackTicks) || 0));
    const coverage = buffer.uncoveredHorizon(playhead + slack, horizonEnd);
    if (!coverage.needsFill) {
      if (patternEngine.getDegradation?.().active) {
        patternEngine.clearDegradation?.();
        log.info('recover-from-degraded', { coveredThroughTick: coverage.coveredThroughTick });
      }
      return { ok: true, filled: 0, degraded: false, coverage };
    }

    const generated = patternEngine.generateWindow({
      fromTick: coverage.gapStart,
      toTick: coverage.horizonEnd,
      harmonySymbol,
      degraded: true,
    });
    if (!generated.events.length) {
      return {
        ok: true,
        filled: 0,
        degraded: true,
        degradation: generated.degradation,
        coverage,
      };
    }
    const ingested = ingestEvents(generated.events, {
      source: 'local_pattern',
      session_id: 'local-pattern',
      request_id: `degrade-${coverage.gapStart}`,
    });
    return {
      ok: ingested.ok,
      filled: ingested.inserted || 0,
      degraded: true,
      degradation: generated.degradation,
      coverage,
      scheduled: ingested.scheduled,
    };
  }

  return {
    start,
    stop,
    cancel,
    setComposition,
    onTransportRelocate,
    schedulePendingFromPlayhead,
    ingestChunk,
    ingestEvents,
    computeHorizonEndTick,
    maintainHorizonWithPattern,
    getBuffer: () => buffer,
    isActive: () => active,
  };
}
