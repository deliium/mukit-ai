/**
 * Session performance take buffer for live MIDI / QWERTY input.
 * Stores raw (unquantized) note and sustain spans relative to an origin tick.
 * Never writes composition.v2 — callers commit via midiTakeApply.
 */

import { createAppLogger } from './appLogger.js';
import {
  clampMidiVelocity,
  isSustainControlChange,
  MIDI_MESSAGE_KINDS,
  parseMidiMessage,
} from './midiInputMessages.js';
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
 * @param {{
 *   now?: () => number,
 *   composition?: object,
 *   originTick?: number,
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

  /** @type {Map<string, { midi: number, channel: number, velocity: number, startTick: number, pitch: string }>} */
  const openNotes = new Map();
  /** @type {Array<{ pitch: string, midi: number, channel: number, start_tick: number, duration_ticks: number, velocity: number }>} */
  const closedNotes = [];
  /** @type {{ startTick: number } | null} */
  let openPedal = null;
  /** @type {Array<{ start_tick: number, duration_ticks: number }>} */
  const closedPedals = [];
  let ignoredCount = 0;
  let noteOpenCount = 0;
  let noteCloseCount = 0;

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

  function closeNote(channel, midi, endTick) {
    const key = noteKey(channel, midi);
    const open = openNotes.get(key);
    if (!open) {
      return false;
    }
    openNotes.delete(key);
    const duration = Math.max(1, endTick - open.startTick);
    closedNotes.push({
      pitch: open.pitch,
      midi: open.midi,
      channel: open.channel,
      start_tick: open.startTick,
      duration_ticks: duration,
      velocity: open.velocity,
    });
    noteCloseCount += 1;
    log.debug('Note closed', {
      noteCloseCount,
      durationTicks: duration,
    });
    return true;
  }

  function openNote(channel, midi, velocity, startTick) {
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
      velocity: clampMidiVelocity(velocity),
      startTick,
      pitch,
    });
    noteOpenCount += 1;
    log.debug('Note opened', { noteOpenCount });
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
   * @param {Iterable<number> | ArrayLike<number>} data
   * @param {{ atMs?: number }} [meta]
   */
  function injectMessage(data, meta = {}) {
    if (!capturing) {
      return { accepted: false, reason: 'not_capturing' };
    }
    const atMs = meta.atMs != null ? Number(meta.atMs) : nowFn();
    const tick = currentTick(atMs);
    const parsed = parseMidiMessage(data);

    if (parsed.kind === MIDI_MESSAGE_KINDS.NOTE_ON) {
      openNote(parsed.channel, parsed.note, parsed.velocity, tick);
      return { accepted: true, kind: parsed.kind, tick, pitch: midiToPitch(parsed.note).pitch };
    }
    if (parsed.kind === MIDI_MESSAGE_KINDS.NOTE_OFF) {
      closeNote(parsed.channel, parsed.note, tick);
      return { accepted: true, kind: parsed.kind, tick };
    }
    if (isSustainControlChange(parsed)) {
      if (parsed.sustain) {
        if (!openPedal) {
          openPedal = { startTick: tick };
        }
      } else {
        closePedal(tick);
      }
      return { accepted: true, kind: parsed.kind, tick, sustain: parsed.sustain };
    }
    ignoredCount += 1;
    return { accepted: false, reason: 'ignored', kind: parsed.kind };
  }

  function start({ originTick: nextOrigin, composition: nextComposition, atMs } = {}) {
    composition = nextComposition || composition;
    originTick = Number.isInteger(Number(nextOrigin))
      ? Math.max(0, Math.round(Number(nextOrigin)))
      : originTick;
    startedAtMs = atMs != null ? Number(atMs) : nowFn();
    capturing = true;
    openNotes.clear();
    closedNotes.length = 0;
    openPedal = null;
    closedPedals.length = 0;
    ignoredCount = 0;
    noteOpenCount = 0;
    noteCloseCount = 0;
    log.info('Capture started', { originTick, tempo: composition?.tempo ?? null });
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
      pedalCount: summary.pedalCount,
      originTick: summary.originTick,
      endTick: summary.endTick,
      ignoredCount,
    });
    return summary;
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
    return {
      notes: closedNotes.map((note) => ({ ...note })),
      sustainPedals: closedPedals.map((pedal) => ({ ...pedal })),
      originTick,
      endTick,
      noteCount: closedNotes.length,
      pedalCount: closedPedals.length,
      openNoteCount: openNotes.size,
      ignoredCount,
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
    closedNotes.length = 0;
    openPedal = null;
    closedPedals.length = 0;
    log.info('Capture discarded', { code: 'midi_take_discarded' });
  }

  return {
    start,
    stop,
    discard,
    injectMessage,
    currentTick,
    getActiveMidiNotes,
    isCapturing,
    getSnapshot: () => finalizeSummary(),
  };
}
