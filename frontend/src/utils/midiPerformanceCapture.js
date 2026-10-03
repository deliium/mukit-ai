/**
 * Session performance take buffer for live MIDI / QWERTY input.
 * Consumes midi.expressive.event.v1; injectMessage(bytes) remains a MIDI 1.0 convenience.
 * Never writes composition.v2 — callers commit via midiTakeApply.
 */

import { createAppLogger } from './appLogger.js';
import { MIDI_CC_SUSTAIN } from './midiInputMessages.js';
import { adaptIncomingMidi } from './midiExpressive/adaptMessage.js';
import {
  EXPRESSIVE_EVENT_KINDS,
  MIDI_PERFORMANCE_TAKE_SCHEMA,
  NOTE_PERFORMANCE_MAX_CONTROLLER_IDS,
  NOTE_PERFORMANCE_MAX_CURVE_POINTS,
} from './midiExpressive/constants.js';
import { resolveMpeEventRouting } from './midiExpressive/mpeZone.js';
import {
  degradeVelocityU16ToMidi7,
  promoteMidi1VelocityToU16,
} from './midiExpressive/velocity.js';
import { midiToPitch } from './pianoRollEvents.js';

const log = createAppLogger('midiCapture');

/**
 * Convert elapsed wall time to ticks using root tempo (live capture clock).
 * Past composition end continues at the same tempo — timeline extend happens on commit.
 *
 * @param {{ tempo?: number, ticks_per_quarter?: number }} composition
 * @param {number} originTick
 * @param {number} elapsedMs
 * @returns {number}
 */
export function ticksFromElapsedMs(composition, originTick, elapsedMs) {
  const tempo = Number(composition?.tempo) || 100;
  const tpq = Number(composition?.ticks_per_quarter) || 480;
  const safeElapsed = Math.max(0, Number(elapsedMs) || 0);
  const ticksPerMs = (tempo / 60) * tpq / 1000;
  return Math.max(0, Math.round(Number(originTick) || 0) + Math.round(safeElapsed * ticksPerMs));
}

/**
 * @param {Array<{ tick_offset: number, cents?: number, value?: number, controller?: number }>} points
 * @param {number} max
 */
function truncatePoints(points, max) {
  if (points.length <= max) {
    return points;
  }
  log.warn('Performance curve truncated', {
    code: 'performance_curve_truncated',
    kept: max,
    dropped: points.length - max,
  });
  return points.slice(0, max);
}

/**
 * @param {{
 *   now?: () => number,
 *   composition?: object,
 *   originTick?: number,
 *   mpeMappingEnabled?: boolean,
 *   mpeZone?: { master_channel: number, member_channel_low: number, member_channel_high: number },
 *   expressiveEnv?: Record<string, unknown>,
 *   transport?: string,
 * }} [options]
 */
export function createMidiPerformanceCapture(options = {}) {
  const nowFn = typeof options.now === 'function' ? options.now : () => Date.now();
  let composition = options.composition || null;
  let originTick = Number.isInteger(Number(options.originTick))
    ? Math.max(0, Math.round(Number(options.originTick)))
    : 0;
  let startedAtMs = null;
  let capturing = false;
  let mpeMappingEnabled = options.mpeMappingEnabled === true;
  let mpeZone = options.mpeZone || null;
  let expressiveEnv = options.expressiveEnv || null;
  let transport = options.transport || 'midi1_bytes';

  /**
   * @type {Map<string, {
   *   midi: number,
   *   channel: number,
   *   velocity_u16: number,
   *   startTick: number,
   *   pitch: string,
   *   pitch_cents: Array<{ tick_offset: number, cents: number }>,
   *   pressure: Array<{ tick_offset: number, value: number }>,
   *   controllers: Array<{ tick_offset: number, controller: number, value: number }>,
   *   controllerIds: Set<number>,
   * }>}
   */
  const openNotes = new Map();
  /** @type {Array<import('./midiExpressive/schemas.js').MidiPerformanceTakeNote>} */
  const closedNotes = [];
  /** @type {{ startTick: number } | null} */
  let openPedal = null;
  /** @type {Array<{ start_tick: number, duration_ticks: number }>} */
  const closedPedals = [];
  let ignoredCount = 0;
  let noteOpenCount = 0;
  let noteCloseCount = 0;
  /** @type {Map<number, string>} channel → open note key for MPE member attach */
  const openKeyByChannel = new Map();

  function noteKey(channel, midi) {
    return `${channel}:${midi}`;
  }

  function currentTick(atMs) {
    if (!capturing || startedAtMs == null) {
      return originTick;
    }
    const elapsed = Math.max(0, (atMs ?? nowFn()) - startedAtMs);
    return ticksFromElapsedMs(composition, originTick, elapsed);
  }

  function pushCurvePoint(open, kind, point) {
    if (kind === 'pitch_cents') {
      open.pitch_cents.push(point);
      open.pitch_cents = truncatePoints(open.pitch_cents, NOTE_PERFORMANCE_MAX_CURVE_POINTS);
      return;
    }
    if (kind === 'pressure') {
      open.pressure.push(point);
      open.pressure = truncatePoints(open.pressure, NOTE_PERFORMANCE_MAX_CURVE_POINTS);
      return;
    }
    if (kind === 'controllers') {
      if (
        !open.controllerIds.has(point.controller)
        && open.controllerIds.size >= NOTE_PERFORMANCE_MAX_CONTROLLER_IDS
      ) {
        log.warn('Controller id cap reached', {
          code: 'performance_controller_cap',
          controller: point.controller,
        });
        return;
      }
      open.controllerIds.add(point.controller);
      open.controllers.push(point);
      open.controllers = truncatePoints(open.controllers, NOTE_PERFORMANCE_MAX_CURVE_POINTS);
    }
  }

  function attachToOpenOnChannel(channel, tick, attach) {
    const key = openKeyByChannel.get(channel);
    if (!key) {
      return false;
    }
    const open = openNotes.get(key);
    if (!open) {
      return false;
    }
    const tickOffset = tick - open.startTick;
    attach(open, tickOffset);
    return true;
  }

  function closeNote(channel, midi, endTick) {
    const key = noteKey(channel, midi);
    const open = openNotes.get(key);
    if (!open) {
      return false;
    }
    openNotes.delete(key);
    if (openKeyByChannel.get(channel) === key) {
      openKeyByChannel.delete(channel);
    }
    const duration = Math.max(1, endTick - open.startTick);
    const velocity = degradeVelocityU16ToMidi7(open.velocity_u16) || 1;
    /** @type {import('./midiExpressive/schemas.js').MidiPerformanceTakeNote} */
    const closed = {
      pitch: open.pitch,
      midi: open.midi,
      channel: open.channel,
      start_tick: open.startTick,
      duration_ticks: duration,
      velocity,
      velocity_u16: open.velocity_u16,
    };
    if (open.pitch_cents.length > 0) {
      closed.pitch_cents = open.pitch_cents.map((p) => ({ ...p }));
    }
    if (open.pressure.length > 0) {
      closed.pressure = open.pressure.map((p) => ({ ...p }));
    }
    if (open.controllers.length > 0) {
      closed.controllers = open.controllers.map((p) => ({ ...p }));
    }
    closedNotes.push(closed);
    noteCloseCount += 1;
    log.debug('Note closed', {
      noteCloseCount,
      durationTicks: duration,
      hasPerformance:
        Boolean(closed.pitch_cents?.length)
        || Boolean(closed.pressure?.length)
        || Boolean(closed.controllers?.length)
        || closed.velocity_u16 !== promoteMidi1VelocityToU16(velocity),
    });
    return true;
  }

  function openNote(channel, midi, velocityU16, startTick) {
    const key = noteKey(channel, midi);
    if (openNotes.has(key)) {
      closeNote(channel, midi, startTick);
    }
    const { pitch } = midiToPitch(midi);
    if (!pitch) {
      ignoredCount += 1;
      return false;
    }
    openNotes.set(key, {
      midi,
      channel,
      velocity_u16: Math.max(0, Math.round(Number(velocityU16) || 0)),
      startTick,
      pitch,
      pitch_cents: [],
      pressure: [],
      controllers: [],
      controllerIds: new Set(),
    });
    openKeyByChannel.set(channel, key);
    noteOpenCount += 1;
    log.debug('Note opened', { noteOpenCount, channel });
    return true;
  }

  function closePedal(endTick) {
    if (!openPedal) {
      return;
    }
    const duration = Math.max(1, endTick - openPedal.startTick);
    closedPedals.push({
      start_tick: openPedal.startTick,
      duration_ticks: duration,
    });
    openPedal = null;
  }

  /**
   * @param {import('./midiExpressive/schemas.js').MidiExpressiveEventV1} event
   * @param {number} tick
   */
  function handleExpressiveEvent(event, tick) {
    const mpe = resolveMpeEventRouting(event, {
      mappingEnabled: mpeMappingEnabled,
      zone: mpeZone || undefined,
    });

    if (event.kind === EXPRESSIVE_EVENT_KINDS.NOTE_ON) {
      openNote(event.channel, event.note, event.velocity_u16, tick);
      return { accepted: true, kind: event.kind, tick, pitch: midiToPitch(event.note).pitch };
    }
    if (event.kind === EXPRESSIVE_EVENT_KINDS.NOTE_OFF) {
      closeNote(event.channel, event.note, tick);
      return { accepted: true, kind: event.kind, tick };
    }
    if (event.kind === EXPRESSIVE_EVENT_KINDS.CONTROL_CHANGE) {
      if (event.controller === MIDI_CC_SUSTAIN && typeof event.sustain === 'boolean') {
        // Master / global sustain even under MPE.
        if (event.sustain) {
          if (!openPedal) {
            openPedal = { startTick: tick };
          }
        } else {
          closePedal(tick);
        }
        return { accepted: true, kind: event.kind, tick, sustain: event.sustain };
      }
      if (mpe.attachToOpenNoteOnChannel || (!mpeMappingEnabled && openKeyByChannel.has(event.channel))) {
        const attached = attachToOpenOnChannel(event.channel, tick, (open, tickOffset) => {
          const midi7 = (Number(event.value_u32) >>> 25) & 0x7f;
          pushCurvePoint(open, 'controllers', {
            tick_offset: tickOffset,
            controller: event.controller,
            value: midi7,
          });
        });
        if (attached) {
          return { accepted: true, kind: event.kind, tick };
        }
      }
      ignoredCount += 1;
      return { accepted: false, reason: 'cc_unattached', kind: event.kind };
    }
    if (event.kind === EXPRESSIVE_EVENT_KINDS.PITCH_BEND) {
      const channel = event.channel;
      const shouldAttach = mpe.attachToOpenNoteOnChannel
        || (!mpeMappingEnabled && openKeyByChannel.has(channel));
      if (shouldAttach) {
        const attached = attachToOpenOnChannel(channel, tick, (open, tickOffset) => {
          const cents = Math.round(Number(event.bend) * 200);
          pushCurvePoint(open, 'pitch_cents', { tick_offset: tickOffset, cents });
        });
        if (attached) {
          return { accepted: true, kind: event.kind, tick };
        }
      }
      ignoredCount += 1;
      return { accepted: false, reason: 'bend_unattached', kind: event.kind };
    }
    if (event.kind === EXPRESSIVE_EVENT_KINDS.PRESSURE) {
      const channel = event.channel;
      const note = event.note;
      if (event.scope === 'poly' && Number.isInteger(note)) {
        const key = noteKey(channel, note);
        const open = openNotes.get(key);
        if (open) {
          pushCurvePoint(open, 'pressure', {
            tick_offset: tick - open.startTick,
            value: Math.max(0, Math.min(1, Number(event.value) || 0)),
          });
          return { accepted: true, kind: event.kind, tick };
        }
      }
      const shouldAttach = mpe.attachToOpenNoteOnChannel
        || (!mpeMappingEnabled && openKeyByChannel.has(channel));
      if (shouldAttach) {
        const attached = attachToOpenOnChannel(channel, tick, (open, tickOffset) => {
          pushCurvePoint(open, 'pressure', {
            tick_offset: tickOffset,
            value: Math.max(0, Math.min(1, Number(event.value) || 0)),
          });
        });
        if (attached) {
          return { accepted: true, kind: event.kind, tick };
        }
      }
      ignoredCount += 1;
      return { accepted: false, reason: 'pressure_unattached', kind: event.kind };
    }

    ignoredCount += 1;
    return { accepted: false, reason: 'ignored', kind: event.kind };
  }

  /**
   * @param {import('./midiExpressive/schemas.js').MidiExpressiveEventV1} event
   * @param {{ atMs?: number, tick?: number }} [meta]
   */
  function injectExpressiveEvent(event, meta = {}) {
    if (!capturing) {
      return { accepted: false, reason: 'not_capturing' };
    }
    const atMs = meta.atMs != null ? Number(meta.atMs) : nowFn();
    const tick = meta.tick != null && Number.isFinite(Number(meta.tick))
      ? Math.max(0, Math.round(Number(meta.tick)))
      : currentTick(atMs);
    return handleExpressiveEvent(event, tick);
  }

  /**
   * @param {Iterable<number> | ArrayLike<number>} data
   * @param {{ atMs?: number, umpWords?: ArrayLike<number> }} [meta]
   */
  function injectMessage(data, meta = {}) {
    if (!capturing) {
      return { accepted: false, reason: 'not_capturing' };
    }
    const atMs = meta.atMs != null ? Number(meta.atMs) : nowFn();
    const tick = currentTick(atMs);
    const adapted = adaptIncomingMidi({
      data,
      umpWords: meta.umpWords,
      transport,
      expressiveEnv,
      mpeMappingEnabled,
      mpeZone: mpeZone || undefined,
      atMs,
    });
    let last = { accepted: false, reason: 'empty' };
    for (const event of adapted.events) {
      last = handleExpressiveEvent(event, tick);
    }
    return last;
  }

  function start({
    originTick: nextOrigin,
    composition: nextComposition,
    atMs,
    mpeMappingEnabled: nextMpe,
    mpeZone: nextZone,
    expressiveEnv: nextEnv,
    transport: nextTransport,
  } = {}) {
    composition = nextComposition || composition;
    originTick = Number.isInteger(Number(nextOrigin))
      ? Math.max(0, Math.round(Number(nextOrigin)))
      : originTick;
    if (typeof nextMpe === 'boolean') {
      mpeMappingEnabled = nextMpe;
    }
    if (nextZone) {
      mpeZone = nextZone;
    }
    if (nextEnv) {
      expressiveEnv = nextEnv;
    }
    if (nextTransport) {
      transport = nextTransport;
    }
    startedAtMs = atMs != null ? Number(atMs) : nowFn();
    capturing = true;
    openNotes.clear();
    openKeyByChannel.clear();
    closedNotes.length = 0;
    openPedal = null;
    closedPedals.length = 0;
    ignoredCount = 0;
    noteOpenCount = 0;
    noteCloseCount = 0;
    log.info('Capture started', {
      originTick,
      tempo: composition?.tempo ?? null,
      mpeMappingEnabled,
    });
  }

  function stop({ atMs, forceCloseOpen = true } = {}) {
    if (!capturing) {
      return finalizeSummary();
    }
    const endTick = currentTick(atMs != null ? Number(atMs) : nowFn());
    if (forceCloseOpen) {
      for (const open of [...openNotes.values()]) {
        closeNote(open.channel, open.midi, endTick);
      }
      closePedal(endTick);
    }
    capturing = false;
    const summary = finalizeSummary(endTick);
    log.info('Capture finalized', {
      noteCount: summary.noteCount,
      performanceNoteCount: summary.performanceNoteCount,
      pedalCount: summary.pedalCount,
      originTick: summary.originTick,
      endTick: summary.endTick,
      ignoredCount,
    });
    return summary;
  }

  function noteHasPerformance(note) {
    return Boolean(
      (note.pitch_cents && note.pitch_cents.length > 0)
      || (note.pressure && note.pressure.length > 0)
      || (note.controllers && note.controllers.length > 0)
      || (note.velocity_u16 != null
        && note.velocity_u16 !== promoteMidi1VelocityToU16(note.velocity)),
    );
  }

  function finalizeSummary(endTickOverride) {
    const noteEnd = closedNotes.reduce(
      (max, note) => Math.max(max, note.start_tick + note.duration_ticks),
      originTick,
    );
    const pedalEnd = closedPedals.reduce(
      (max, pedal) => Math.max(max, pedal.start_tick + pedal.duration_ticks),
      originTick,
    );
    const endTick = endTickOverride != null
      ? Math.max(endTickOverride, noteEnd, pedalEnd)
      : Math.max(noteEnd, pedalEnd);
    const notes = closedNotes.map((note) => ({ ...note }));
    const performanceNoteCount = notes.filter(noteHasPerformance).length;
    return {
      schema: MIDI_PERFORMANCE_TAKE_SCHEMA,
      notes,
      sustainPedals: closedPedals.map((pedal) => ({ ...pedal })),
      sustain_pedals: closedPedals.map((pedal) => ({ ...pedal })),
      originTick,
      endTick,
      noteCount: closedNotes.length,
      performanceNoteCount,
      performance_note_count: performanceNoteCount,
      pedalCount: closedPedals.length,
      openNoteCount: openNotes.size,
      ignoredCount,
      ignored_count: ignoredCount,
    };
  }

  function getActiveMidiNotes() {
    return [...openNotes.values()].map((open) => open.pitch);
  }

  function isCapturing() {
    return capturing;
  }

  function discard() {
    capturing = false;
    openNotes.clear();
    openKeyByChannel.clear();
    closedNotes.length = 0;
    openPedal = null;
    closedPedals.length = 0;
    log.info('Capture discarded', { code: 'midi_take_discarded' });
  }

  function setMpeMappingEnabled(enabled) {
    mpeMappingEnabled = enabled === true;
    log.debug('MPE mapping preference', { mpeMappingEnabled });
  }

  return {
    start,
    stop,
    discard,
    injectMessage,
    injectExpressiveEvent,
    currentTick,
    getActiveMidiNotes,
    isCapturing,
    getSnapshot: () => finalizeSummary(),
    setMpeMappingEnabled,
  };
}
