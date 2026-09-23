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
  isSustainControlChange,
  MIDI_MESSAGE_KINDS,
  parseMidiMessage,
} from './midiInputMessages.js';
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
  /** @type {'idle'|'arming'|'running'|'degraded'|'stopping'|'cancelled'} */
  let phase = 'idle';
  /** @type {Array<{ kind: string, tick: number, note?: number, velocity?: number, channel?: number, controller?: number, value?: number }>} */
  const ring = [];
  /** @type {Map<string, { note: number, channel: number, velocity: number, startTick: number }>} */
  const openNotes = new Map();
  let noteOnCount = 0;
  let noteOffCount = 0;
  let ignoredCount = 0;
  let droppedCount = 0;

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
   * @param {Iterable<number> | ArrayLike<number> | null | undefined} data
   * @param {{ tick?: number }} [meta]
   */
  function pushMessage(data, meta = {}) {
    if (phase !== 'running' && phase !== 'degraded') {
      return { ok: false, phase };
    }
    const parsed = parseMidiMessage(data);
    const tick =
      meta.tick != null && Number.isFinite(Number(meta.tick))
        ? Math.max(0, Math.round(Number(meta.tick)))
        : Math.max(0, Math.round(Number(getTick()) || 0));

    if (parsed.kind === MIDI_MESSAGE_KINDS.NOTE_ON) {
      const velocity = clampMidiVelocity(parsed.velocity);
      const key = noteKey(parsed.channel, parsed.note);
      // Retrigger: close previous at same tick.
      if (openNotes.has(key)) {
        pushRing({
          kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
          tick,
          note: parsed.note,
          velocity: 0,
          channel: parsed.channel,
        });
        noteOffCount += 1;
      }
      openNotes.set(key, {
        note: parsed.note,
        channel: parsed.channel,
        velocity,
        startTick: tick,
      });
      pushRing({
        kind: MIDI_MESSAGE_KINDS.NOTE_ON,
        tick,
        note: parsed.note,
        velocity,
        channel: parsed.channel,
      });
      noteOnCount += 1;
      log.debug('note on', { noteOnCount, tick });
      return { ok: true, kind: parsed.kind, tick };
    }

    if (parsed.kind === MIDI_MESSAGE_KINDS.NOTE_OFF) {
      const key = noteKey(parsed.channel, parsed.note);
      if (openNotes.has(key)) {
        openNotes.delete(key);
      }
      pushRing({
        kind: MIDI_MESSAGE_KINDS.NOTE_OFF,
        tick,
        note: parsed.note,
        velocity: 0,
        channel: parsed.channel,
      });
      noteOffCount += 1;
      log.debug('note off', { noteOffCount, tick });
      return { ok: true, kind: parsed.kind, tick };
    }

    if (
      parsed.kind === MIDI_MESSAGE_KINDS.CONTROL_CHANGE
      && isSustainControlChange(parsed)
    ) {
      pushRing({
        kind: MIDI_MESSAGE_KINDS.CONTROL_CHANGE,
        tick,
        controller: parsed.controller,
        value: parsed.value,
        channel: parsed.channel,
      });
      return { ok: true, kind: parsed.kind, tick };
    }

    ignoredCount += 1;
    return { ok: true, kind: MIDI_MESSAGE_KINDS.IGNORED, tick };
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
