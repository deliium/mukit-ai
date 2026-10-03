/**
 * Continuous co-performance MIDI stream — Transport-synced ticks.
 *
 * Session-only ring buffer. Does **not** reuse wall-clock ticksFromElapsedMs
 * from midiPerformanceCapture. Callers supply getTick() from liveClock /
 * engine position. Never writes composition.v2.
 */

import { createAppLogger } from './appLogger.js';
import {
  clampMidiVelocity,
  MIDI_MESSAGE_KINDS,
} from './midiInputMessages.js';
import { adaptIncomingMidi } from './midiExpressive/adaptMessage.js';
import { EXPRESSIVE_EVENT_KINDS } from './midiExpressive/constants.js';
import { degradeVelocityU16ToMidi7 } from './midiExpressive/velocity.js';
import {
  LIVE_MIDI_PHASE_EXCLUSION,
  createIdleLiveSession,
} from './liveSessionContracts.js';

const log = createAppLogger('liveMidi');

export const DEFAULT_LIVE_MIDI_RING_CAPACITY = 512;

/** MIDI record-take phases that conflict with co-performance arm/start. */
export const MIDI_CAPTURE_PHASES = Object.freeze([
  'armed',
  'counting_in',
  'recording',
  'stopping',
]);

/**
 * @param {string|null|undefined} midiPhase
 * @returns {boolean}
 */
export function isMidiCapturePhase(midiPhase) {
  return MIDI_CAPTURE_PHASES.includes(String(midiPhase || ''));
}

/**
 * @param {string|null|undefined} livePhase
 * @returns {boolean}
 */
export function isLiveSessionBlockingMidiCapture(livePhase) {
  const phase = String(livePhase || 'idle');
  return phase === 'arming' || phase === 'running' || phase === 'degraded' || phase === 'stopping';
}

/**
 * @param {{
 *   capacity?: number,
 *   getTick?: () => number,
 *   sessionId?: string,
 *   expressiveEnv?: Record<string, unknown>,
 *   mpeMappingEnabled?: boolean,
 * }} [options]
 */
export function createLiveMidiStream(options = {}) {
  const capacity = Math.max(
    16,
    Math.min(4096, Math.round(Number(options.capacity) || DEFAULT_LIVE_MIDI_RING_CAPACITY)),
  );
  let getTick =
    typeof options.getTick === 'function'
      ? options.getTick
      : () => 0;
  let sessionId = String(options.sessionId || `live-${Date.now()}`);
  let expressiveEnv = options.expressiveEnv || null;
  let mpeMappingEnabled = options.mpeMappingEnabled === true;
  /** @type {'idle'|'arming'|'running'|'degraded'|'stopping'|'cancelled'} */
  let phase = 'idle';
  /** @type {Array<{ kind: string, tick: number, note?: number, velocity?: number, channel?: number, controller?: number, value?: number, bend?: number, pressure?: number }>} */
  const ring = [];
  /** @type {Map<string, { note: number, channel: number, velocity: number, startTick: number }>} */
  const openNotes = new Map();
  let noteOnCount = 0;
  let noteOffCount = 0;
  let ignoredCount = 0;
  let droppedCount = 0;
  let expressiveEventCount = 0;

  function noteKey(channel, note) {
    return `${channel}:${note}`;
  }

  function pushRing(entry) {
    ring.push(entry);
    while (ring.length > capacity) {
      ring.shift();
      droppedCount += 1;
    }
  }

  function setGetTick(fn) {
    if (typeof fn === 'function') {
      getTick = fn;
    }
  }

  function start({ sessionId: nextId, getTick: nextGetTick } = {}) {
    if (phase === 'running' || phase === 'degraded' || phase === 'arming') {
      log.info('stream start ignored — already active', { phase });
      return { ok: false, code: 'live_session_already_active', phase };
    }
    if (typeof nextGetTick === 'function') {
      getTick = nextGetTick;
    }
    if (nextId) {
      sessionId = String(nextId);
    }
    ring.length = 0;
    openNotes.clear();
    noteOnCount = 0;
    noteOffCount = 0;
    ignoredCount = 0;
    droppedCount = 0;
    phase = 'running';
    log.info('stream start', { sessionId: sessionId.slice(0, 12), capacity });
    return { ok: true, sessionId, phase };
  }

  function stop({ reason = 'stop' } = {}) {
    if (phase === 'idle' || phase === 'cancelled') {
      log.info('stream stop ignored', { phase, reason });
      return { ok: false, phase };
    }
    // Close open notes at current tick without auto-commit.
    const tick = Math.max(0, Math.round(Number(getTick()) || 0));
    for (const [key, open] of openNotes.entries()) {
      pushRing({
        kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
        tick,
        note: open.note,
        velocity: 0,
        channel: open.channel,
      });
      openNotes.delete(key);
      noteOffCount += 1;
    }
    phase = 'stopping';
    log.info('stream stop', {
      reason,
      noteOnCount,
      noteOffCount,
      ringSize: ring.length,
    });
    phase = 'idle';
    return { ok: true, phase, reason, snapshot: getSnapshot() };
  }

  function cancel({ reason = 'cancel' } = {}) {
    ring.length = 0;
    openNotes.clear();
    noteOnCount = 0;
    noteOffCount = 0;
    ignoredCount = 0;
    phase = 'cancelled';
    log.info('stream cancel', { reason, sessionId: sessionId.slice(0, 12) });
    phase = 'idle';
    return { ok: true, phase, reason };
  }

  /**
   * Ingest a transport-agnostic expressive event into the ring (features only — no Jam Commit redesign).
   * @param {import('./midiExpressive/schemas.js').MidiExpressiveEventV1} event
   * @param {{ tick?: number }} [meta]
   */
  function pushExpressiveEvent(event, meta = {}) {
    if (phase !== 'running' && phase !== 'degraded') {
      return { ok: false, phase };
    }
    const tick =
      meta.tick != null && Number.isFinite(Number(meta.tick))
        ? Math.max(0, Math.round(Number(meta.tick)))
        : Math.max(0, Math.round(Number(getTick()) || 0));
    expressiveEventCount += 1;

    if (event.kind === EXPRESSIVE_EVENT_KINDS.NOTE_ON) {
      const velocity = clampMidiVelocity(degradeVelocityU16ToMidi7(event.velocity_u16) || 1);
      const key = noteKey(event.channel, event.note);
      if (openNotes.has(key)) {
        pushRing({
          kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
          tick,
          note: event.note,
          velocity: 0,
          channel: event.channel,
        });
        noteOffCount += 1;
      }
      openNotes.set(key, {
        note: event.note,
        channel: event.channel,
        velocity,
        startTick: tick,
      });
      pushRing({
        kind: MIDI_MESSAGE_KINDS.NOTE_ON,
        tick,
        note: event.note,
        velocity,
        channel: event.channel,
      });
      noteOnCount += 1;
      log.debug('expressive note on', { noteOnCount, tick });
      return { ok: true, kind: event.kind, tick };
    }

    if (event.kind === EXPRESSIVE_EVENT_KINDS.NOTE_OFF) {
      const key = noteKey(event.channel, event.note);
      if (openNotes.has(key)) {
        openNotes.delete(key);
      }
      pushRing({
        kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
        tick,
        note: event.note,
        velocity: 0,
        channel: event.channel,
      });
      noteOffCount += 1;
      return { ok: true, kind: event.kind, tick };
    }

    if (event.kind === EXPRESSIVE_EVENT_KINDS.CONTROL_CHANGE) {
      const value = (Number(event.value_u32) >>> 25) & 0x7f;
      pushRing({
        kind: MIDI_MESSAGE_KINDS.CONTROL_CHANGE,
        tick,
        controller: event.controller,
        value,
        channel: event.channel,
      });
      return { ok: true, kind: event.kind, tick };
    }

    if (event.kind === EXPRESSIVE_EVENT_KINDS.PITCH_BEND) {
      pushRing({
        kind: 'pitch_bend',
        tick,
        bend: event.bend,
        channel: event.channel,
      });
      return { ok: true, kind: event.kind, tick };
    }

    if (event.kind === EXPRESSIVE_EVENT_KINDS.PRESSURE) {
      pushRing({
        kind: 'pressure',
        tick,
        pressure: event.value,
        note: event.note,
        channel: event.channel,
      });
      return { ok: true, kind: event.kind, tick };
    }

    ignoredCount += 1;
    return { ok: true, kind: MIDI_MESSAGE_KINDS.IGNORED, tick };
  }

  /**
   * @param {Iterable<number> | ArrayLike<number> | null | undefined} data
   * @param {{ tick?: number, umpWords?: ArrayLike<number> }} [meta]
   */
  function pushMessage(data, meta = {}) {
    if (phase !== 'running' && phase !== 'degraded') {
      return { ok: false, phase };
    }
    const tick =
      meta.tick != null && Number.isFinite(Number(meta.tick))
        ? Math.max(0, Math.round(Number(meta.tick)))
        : Math.max(0, Math.round(Number(getTick()) || 0));

    const adapted = adaptIncomingMidi({
      data,
      umpWords: meta.umpWords,
      expressiveEnv,
      mpeMappingEnabled,
      transport: meta.umpWords ? 'ump_experimental' : 'midi1_bytes',
    });

    let last = { ok: true, kind: MIDI_MESSAGE_KINDS.IGNORED, tick };
    for (const event of adapted.events) {
      if (event.kind === EXPRESSIVE_EVENT_KINDS.IGNORED) {
        ignoredCount += 1;
        last = { ok: true, kind: MIDI_MESSAGE_KINDS.IGNORED, tick };
        continue;
      }
      last = pushExpressiveEvent(event, { tick });
    }
    return last;
  }

  function getSnapshot() {
    return {
      sessionId,
      phase,
      capacity,
      ringSize: ring.length,
      openNoteCount: openNotes.size,
      noteOnCount,
      noteOffCount,
      ignoredCount,
      droppedCount,
      expressiveEventCount,
      events: ring.slice(),
    };
  }

  /**
   * Bounded ring window for warm-path analysis (never dump full ring into predict).
   * Returns events with tick >= fromTick, capped to maxEvents (newest preferred).
   * @param {{ fromTick?: number, maxEvents?: number }} [opts]
   */
  function getRecentEvents(opts = {}) {
    const fromTick = Math.max(0, Math.round(Number(opts.fromTick) || 0));
    const maxEvents = Math.max(
      1,
      Math.min(capacity, Math.round(Number(opts.maxEvents) || capacity)),
    );
    const filtered = [];
    for (let i = 0; i < ring.length; i += 1) {
      const entry = ring[i];
      if (entry.tick >= fromTick) {
        filtered.push(entry);
      }
    }
    if (filtered.length > maxEvents) {
      return filtered.slice(filtered.length - maxEvents);
    }
    return filtered;
  }

  function getClosedNotes() {
    /** Build note spans from ring for optional Commit (Task 9). */
    const open = new Map();
    const closed = [];
    for (const entry of ring) {
      if (entry.kind === MIDI_MESSAGE_KINDS.NOTE_ON) {
        const key = noteKey(entry.channel ?? 0, entry.note);
        open.set(key, entry);
      } else if (entry.kind === MIDI_MESSAGE_KINDS.NOTE_OFF) {
        const key = noteKey(entry.channel ?? 0, entry.note);
        const start = open.get(key);
        if (start) {
          open.delete(key);
          closed.push({
            midi: start.note,
            channel: start.channel ?? 0,
            start_tick: start.tick,
            duration_ticks: Math.max(1, entry.tick - start.tick),
            velocity: start.velocity ?? 80,
          });
        }
      }
    }
    return closed;
  }

  function isActive() {
    return phase === 'running' || phase === 'degraded' || phase === 'arming';
  }

  function getPhase() {
    return phase;
  }

  function markDegraded() {
    if (phase === 'running') {
      phase = 'degraded';
    }
  }

  function clearDegraded() {
    if (phase === 'degraded') {
      phase = 'running';
    }
  }

  function toSessionEcho(horizon) {
    const idle = createIdleLiveSession(sessionId, { horizon });
    return {
      ...idle,
      phase,
      transport: {
        ...idle.transport,
        tick: Math.max(0, Math.round(Number(getTick()) || 0)),
      },
    };
  }

  return {
    start,
    stop,
    cancel,
    pushMessage,
    pushExpressiveEvent,
    setGetTick,
    getSnapshot,
    getRecentEvents,
    getClosedNotes,
    isActive,
    getPhase,
    markDegraded,
    clearDegraded,
    toSessionEcho,
    getSessionId: () => sessionId,
  };
}

/**
 * Guard: reject live arm/start when MIDI capture phases are active.
 * @param {string|null|undefined} midiPhase
 */
export function assertLiveAllowedForMidiPhase(midiPhase) {
  if (isMidiCapturePhase(midiPhase)) {
    log.info('live arm rejected — midiPhase exclusion', {
      code: LIVE_MIDI_PHASE_EXCLUSION,
      midiPhase,
    });
    return { ok: false, code: LIVE_MIDI_PHASE_EXCLUSION, midiPhase };
  }
  return { ok: true };
}

/**
 * Guard: reject MIDI record arm when live session is active.
 * @param {string|null|undefined} livePhase
 */
export function assertMidiCaptureAllowedForLivePhase(livePhase) {
  if (isLiveSessionBlockingMidiCapture(livePhase)) {
    log.info('MIDI arm rejected — live session exclusion', {
      code: LIVE_MIDI_PHASE_EXCLUSION,
      livePhase,
    });
    return { ok: false, code: LIVE_MIDI_PHASE_EXCLUSION, livePhase };
  }
  return { ok: true };
}
