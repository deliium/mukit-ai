/**
 * Deterministic local accompaniment pattern + degradation ladder.
 *
 * Hot path only — never awaits AI. When horizon underruns:
 * 1) continue last pattern adapted to active harmony
 * 2) arpeggiate / pad chord tones
 * 3) hold last voicing
 * Never stops Transport. Emits live_degraded_pattern_continue.
 */

import { createAppLogger } from './appLogger.js';
import {
  chordTonePitchClasses,
  pitchClassesToMidi,
} from './liveChordTones.js';
import {
  LIVE_DEGRADED_PATTERN_CONTINUE,
  LIVE_HARMONY_EMPTY,
} from './liveSessionContracts.js';

const log = createAppLogger('liveAccompaniment');

/**
 * @typedef {{
 *   active: boolean,
 *   code: string|null,
 *   count: number,
 * }} DegradationState
 */

/**
 * @param {{
 *   ticksPerBeat?: number,
 *   density?: number,
 *   octaveBase?: number,
 * }} [options]
 */
export function createLivePatternEngine(options = {}) {
  const ticksPerBeat = Math.max(1, Math.round(Number(options.ticksPerBeat) || 480));
  let density = clamp01(options.density ?? 0.5);
  const octaveBase = Number.isFinite(Number(options.octaveBase))
    ? Math.round(Number(options.octaveBase))
    : 48;

  /** @type {number[]} */
  let lastMidiVoicing = [];
  /** @type {'block'|'arp'|'hold'|null} */
  let lastPatternKind = null;
  let stepIndex = 0;

  /** @type {DegradationState} */
  let degradation = { active: false, code: null, count: 0 };

  function setDensity(next) {
    density = clamp01(next);
  }

  function getDegradation() {
    return { ...degradation };
  }

  function clearDegradation() {
    if (degradation.active) {
      log.info('recover-from-degraded', { previousCount: degradation.count });
    }
    degradation = { active: false, code: null, count: degradation.count };
  }

  function activateDegradation(code = LIVE_DEGRADED_PATTERN_CONTINUE) {
    degradation = {
      active: true,
      code,
      count: degradation.count + 1,
    };
    log.warn('degradation activation', {
      code: degradation.code,
      count: degradation.count,
    });
  }

  /**
   * Generate accompaniment events covering [fromTick, toTick).
   * @param {{
   *   fromTick: number,
   *   toTick: number,
   *   harmonySymbol: string|null,
   *   degraded?: boolean,
   * }} args
   */
  function generateWindow({ fromTick, toTick, harmonySymbol, degraded = false }) {
    const start = Math.max(0, Math.round(Number(fromTick) || 0));
    const end = Math.max(start, Math.round(Number(toTick) || 0));
    if (end <= start) {
      return { events: [], degradation: getDegradation() };
    }

    if (degraded) {
      activateDegradation(LIVE_DEGRADED_PATTERN_CONTINUE);
    }

    const pcs = chordTonePitchClasses(harmonySymbol);
    let midi = pitchClassesToMidi(pcs, { octaveBase });

    if (midi.length === 0) {
      // Ladder step 3: hold last voicing; else soft rest (no events).
      if (lastMidiVoicing.length > 0) {
        midi = lastMidiVoicing.slice();
        lastPatternKind = 'hold';
        if (!degradation.active) {
          activateDegradation(LIVE_HARMONY_EMPTY);
        }
        return {
          events: buildHold(start, end, midi),
          degradation: getDegradation(),
          patternKind: 'hold',
        };
      }
      if (!degradation.active) {
        activateDegradation(LIVE_HARMONY_EMPTY);
      }
      return { events: [], degradation: getDegradation(), patternKind: null };
    }

    // Prefer continue last pattern kind when degrading.
    let kind = lastPatternKind || (density >= 0.55 ? 'arp' : 'block');
    if (degraded && lastPatternKind) {
      kind = lastPatternKind === 'hold' ? 'arp' : lastPatternKind;
    } else if (degraded && !lastPatternKind) {
      kind = 'arp';
    }

    let events;
    if (kind === 'arp') {
      events = buildArp(start, end, midi);
    } else if (kind === 'hold') {
      events = buildHold(start, end, midi);
    } else {
      events = buildBlock(start, end, midi);
    }

    lastPatternKind = kind;
    lastMidiVoicing = midi.slice(0, 4);
    if (!degraded && degradation.active && events.length > 0) {
      clearDegradation();
    }

    return { events, degradation: getDegradation(), patternKind: kind };
  }

  function buildBlock(start, end, midi) {
    const step = Math.max(ticksPerBeat, Math.round(ticksPerBeat * (density < 0.35 ? 2 : 1)));
    const events = [];
    for (let t = start; t < end; t += step) {
      const dur = Math.min(step - Math.floor(ticksPerBeat / 8), end - t);
      if (dur < 1) continue;
      for (const pitch of midi.slice(0, 3)) {
        events.push({
          pitch,
          start_tick: t,
          duration_ticks: dur,
          velocity: 62,
          track_role: 'accompaniment',
        });
      }
    }
    return events;
  }

  function buildArp(start, end, midi) {
    const step = Math.max(
      Math.floor(ticksPerBeat / 2),
      Math.round(ticksPerBeat * (0.25 + (1 - density) * 0.5)),
    );
    const events = [];
    const tones = midi.length ? midi : lastMidiVoicing;
    if (!tones.length) return events;
    for (let t = start; t < end; t += step) {
      const pitch = tones[stepIndex % tones.length];
      stepIndex += 1;
      const dur = Math.min(step - 1, end - t);
      if (dur < 1) continue;
      events.push({
        pitch,
        start_tick: t,
        duration_ticks: Math.max(1, dur),
        velocity: 70,
        track_role: 'accompaniment',
      });
    }
    return events;
  }

  function buildHold(start, end, midi) {
    const dur = Math.max(1, end - start);
    return midi.slice(0, 3).map((pitch) => ({
      pitch,
      start_tick: start,
      duration_ticks: dur,
      velocity: 48,
      track_role: 'accompaniment',
    }));
  }

  return {
    generateWindow,
    setDensity,
    getDegradation,
    clearDegradation,
    activateDegradation,
    getLastVoicing: () => lastMidiVoicing.slice(),
    getLastPatternKind: () => lastPatternKind,
  };
}

function clamp01(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0.5;
  return Math.max(0, Math.min(1, n));
}
