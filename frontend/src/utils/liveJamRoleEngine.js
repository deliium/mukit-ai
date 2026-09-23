/**
 * Multi-role local AI Jam generators (hot path).
 *
 * Emits role-tagged ephemeral events for AI roles only from belief + jam
 * controls + jamContext snapshot. Never awaits AI; never mutates composition.v2.
 *
 * Styles: block / arp / alberti / pad. Density/complexity modulate step and
 * non-chord tone allowance. Degradation ladder: continue → arp → hold (role-aware).
 */

import { createAppLogger } from './appLogger.js';
import {
  chordTonePitchClasses,
  pitchClassesToMidi,
} from './liveChordTones.js';
import {
  createDefaultJamControls,
  isJamMode,
  resolveJamRolePartition,
} from './liveJamContracts.js';
import {
  LIVE_DEGRADED_PATTERN_CONTINUE,
  LIVE_HARMONY_EMPTY,
} from './liveSessionContracts.js';

const log = createAppLogger('liveJam');

/** Approximate MIDI register bases per role. */
export const JAM_ROLE_OCTAVE_BASE = Object.freeze({
  bass: 36,
  accompaniment: 48,
  harmony: 48,
  melody: 60,
  texture: 72,
});

const DENSITY_FACTOR = Object.freeze({
  low: 0.35,
  medium: 0.55,
  high: 0.8,
});

/**
 * @typedef {{
 *   active: boolean,
 *   code: string|null,
 *   count: number,
 *   role?: string|null,
 * }} DegradationState
 */

/**
 * @param {{
 *   ticksPerBeat?: number,
 * }} [options]
 */
export function createLiveJamRoleEngine(options = {}) {
  const ticksPerBeat = Math.max(1, Math.round(Number(options.ticksPerBeat) || 480));

  /** @type {Map<string, number[]>} */
  const lastVoicingByRole = new Map();
  /** @type {Map<string, string>} */
  const lastKindByRole = new Map();
  /** @type {Map<string, number>} */
  const stepIndexByRole = new Map();

  /** @type {DegradationState} */
  let degradation = { active: false, code: null, count: 0, role: null };

  function getDegradation() {
    return { ...degradation };
  }

  function clearDegradation() {
    if (degradation.active) {
      log.info('recover-from-degraded', {
        previousCount: degradation.count,
        role: degradation.role,
      });
    }
    degradation = {
      active: false,
      code: null,
      count: degradation.count,
      role: null,
    };
  }

  function activateDegradation(code = LIVE_DEGRADED_PATTERN_CONTINUE, role = null) {
    degradation = {
      active: true,
      code,
      count: degradation.count + 1,
      role: role || null,
    };
    log.warn('degradation activation', {
      code: degradation.code,
      count: degradation.count,
      role: degradation.role,
    });
  }

  /**
   * Generate AI-role events covering [fromTick, toTick).
   * @param {{
   *   fromTick: number,
   *   toTick: number,
   *   belief?: { symbol?: string|null, confidence?: number, held?: boolean }|null,
   *   jamMode: string,
   *   controls?: object,
   *   jamContext?: { belief_symbol?: string|null, planned_window?: object[] }|null,
   *   roleMask?: string[]|null,
   *   degraded?: boolean,
   * }} args
   */
  function generateWindow({
    fromTick,
    toTick,
    belief = null,
    jamMode,
    controls = null,
    jamContext = null,
    roleMask = null,
    degraded = false,
  }) {
    const start = Math.max(0, Math.round(Number(fromTick) || 0));
    const end = Math.max(start, Math.round(Number(toTick) || 0));
    if (end <= start) {
      return { events: [], degradation: getDegradation(), roleCounts: {} };
    }
    if (!isJamMode(jamMode)) {
      return { events: [], degradation: getDegradation(), roleCounts: {} };
    }

    const clamped = normalizeControls(controls);
    const partition = resolveJamRolePartition(jamMode, clamped.complexity);
    const aiRoles = filterRoles(partition.ai_roles, roleMask);

    if (degraded) {
      activateDegradation(LIVE_DEGRADED_PATTERN_CONTINUE, aiRoles[0] || null);
    }

    const symbol = resolveBeliefSymbol(belief, jamContext);
    const pcs = chordTonePitchClasses(symbol);
    const densityNum = DENSITY_FACTOR[clamped.density] ?? 0.55;
    const allowPassing = clamped.complexity === 'high'
      || (clamped.complexity === 'medium' && densityNum >= 0.55);

    /** @type {object[]} */
    const events = [];
    /** @type {Record<string, number>} */
    const roleCounts = {};

    for (const role of aiRoles) {
      const roleEvents = generateRoleWindow({
        role,
        start,
        end,
        pcs,
        style: clamped.style,
        densityNum,
        allowPassing,
        degraded,
      });
      roleCounts[role] = roleEvents.length;
      for (const ev of roleEvents) {
        events.push(ev);
      }
    }

    if (!degraded && degradation.active && events.length > 0) {
      clearDegradation();
    }

    log.info('role fill', {
      jamMode,
      style: clamped.style,
      density: clamped.density,
      complexity: clamped.complexity,
      roles: aiRoles,
      roleCounts,
      eventCount: events.length,
      degraded: Boolean(degraded || degradation.active),
    });

    return {
      events,
      degradation: getDegradation(),
      roleCounts,
      style: clamped.style,
    };
  }

  /**
   * @param {{
   *   role: string,
   *   start: number,
   *   end: number,
   *   pcs: number[],
   *   style: string,
   *   densityNum: number,
   *   allowPassing: boolean,
   *   degraded: boolean,
   * }} args
   */
  function generateRoleWindow({
    role,
    start,
    end,
    pcs,
    style,
    densityNum,
    allowPassing,
    degraded,
  }) {
    const octaveBase = JAM_ROLE_OCTAVE_BASE[role] ?? 48;
    let midi = pitchClassesToMidi(pcs, { octaveBase });

    if (allowPassing && midi.length > 0 && densityNum >= 0.7) {
      midi = addPassingTone(midi, octaveBase);
    }

    if (midi.length === 0) {
      const held = lastVoicingByRole.get(role) || [];
      if (held.length > 0) {
        lastKindByRole.set(role, 'hold');
        if (!degradation.active) {
          activateDegradation(LIVE_HARMONY_EMPTY, role);
        }
        return buildHold(start, end, held, role);
      }
      if (!degradation.active) {
        activateDegradation(LIVE_HARMONY_EMPTY, role);
      }
      return [];
    }

    let kind = resolveStyleKind(style, role, densityNum);
    if (degraded) {
      const last = lastKindByRole.get(role);
      if (last === 'hold' || !last) {
        kind = 'arp';
      } else {
        kind = last;
      }
    }

    let events;
    if (kind === 'arp') {
      events = buildArp(start, end, midi, role, densityNum);
    } else if (kind === 'alberti') {
      events = buildAlberti(start, end, midi, role, densityNum);
    } else if (kind === 'pad' || kind === 'hold') {
      events = buildHold(start, end, midi, role);
      kind = kind === 'hold' ? 'hold' : 'pad';
    } else {
      events = buildBlock(start, end, midi, role, densityNum);
      kind = 'block';
    }

    lastKindByRole.set(role, kind);
    lastVoicingByRole.set(role, midi.slice(0, 4));
    log.debug('style apply', { role, kind, density: densityNum, pitches: midi.length });
    return events;
  }

  function buildBlock(start, end, midi, role, densityNum) {
    const step = Math.max(
      ticksPerBeat,
      Math.round(ticksPerBeat * (densityNum < 0.4 ? 2 : densityNum < 0.7 ? 1 : 0.5)),
    );
    const events = [];
    const tones = role === 'bass' ? midi.slice(0, 1) : midi.slice(0, 3);
    for (let t = start; t < end; t += step) {
      const dur = Math.min(step - Math.floor(ticksPerBeat / 8), end - t);
      if (dur < 1) continue;
      for (const pitch of tones) {
        events.push(makeEvent(pitch, t, dur, velocityForRole(role, 62), role));
      }
    }
    return events;
  }

  function buildArp(start, end, midi, role, densityNum) {
    const step = Math.max(
      Math.floor(ticksPerBeat / 2),
      Math.round(ticksPerBeat * (0.25 + (1 - densityNum) * 0.5)),
    );
    const events = [];
    const tones = midi.length ? midi : (lastVoicingByRole.get(role) || []);
    if (!tones.length) return events;
    let idx = stepIndexByRole.get(role) || 0;
    for (let t = start; t < end; t += step) {
      const pitch = tones[idx % tones.length];
      idx += 1;
      const dur = Math.min(step - 1, end - t);
      if (dur < 1) continue;
      events.push(makeEvent(pitch, t, Math.max(1, dur), velocityForRole(role, 70), role));
    }
    stepIndexByRole.set(role, idx);
    return events;
  }

  function buildAlberti(start, end, midi, role, densityNum) {
    // Classic low-high-mid-high pattern over chord tones.
    const tones = ensureAlbertiTones(midi);
    const step = Math.max(
      Math.floor(ticksPerBeat / 2),
      Math.round(ticksPerBeat * (0.35 + (1 - densityNum) * 0.4)),
    );
    const pattern = [0, 2, 1, 2];
    const events = [];
    let idx = stepIndexByRole.get(role) || 0;
    for (let t = start; t < end; t += step) {
      const pitch = tones[pattern[idx % pattern.length] % tones.length];
      idx += 1;
      const dur = Math.min(step - 1, end - t);
      if (dur < 1) continue;
      events.push(makeEvent(pitch, t, Math.max(1, dur), velocityForRole(role, 66), role));
    }
    stepIndexByRole.set(role, idx);
    return events;
  }

  function buildHold(start, end, midi, role) {
    const dur = Math.max(1, end - start);
    const tones = role === 'bass' ? midi.slice(0, 1) : midi.slice(0, 3);
    return tones.map((pitch) => makeEvent(pitch, start, dur, velocityForRole(role, 48), role));
  }

  return {
    generateWindow,
    getDegradation,
    clearDegradation,
    activateDegradation,
    getLastVoicing: (role) => (lastVoicingByRole.get(role) || []).slice(),
    getLastKind: (role) => lastKindByRole.get(role) || null,
  };
}

/**
 * @param {unknown} controls
 */
function normalizeControls(controls) {
  const defaults = createDefaultJamControls();
  if (!controls || typeof controls !== 'object') return defaults;
  const src = /** @type {Record<string, unknown>} */ (controls);
  return {
    complexity: pickLevel(src.complexity, defaults.complexity),
    density: pickLevel(src.density, defaults.density),
    style: ['block', 'arp', 'alberti', 'pad'].includes(/** @type {string} */ (src.style))
      ? /** @type {string} */ (src.style)
      : defaults.style,
    responsiveness: pickLevel(src.responsiveness, defaults.responsiveness),
  };
}

function pickLevel(value, fallback) {
  if (value === 'low' || value === 'medium' || value === 'high') return value;
  return fallback;
}

/**
 * @param {string[]} aiRoles
 * @param {string[]|null|undefined} roleMask
 */
function filterRoles(aiRoles, roleMask) {
  if (!Array.isArray(roleMask) || roleMask.length === 0) {
    return aiRoles.slice();
  }
  const allowed = new Set(roleMask);
  return aiRoles.filter((r) => allowed.has(r));
}

function resolveBeliefSymbol(belief, jamContext) {
  if (belief && typeof belief.symbol === 'string' && belief.symbol.trim()) {
    return belief.symbol.trim().slice(0, 64);
  }
  if (jamContext && typeof jamContext.belief_symbol === 'string' && jamContext.belief_symbol.trim()) {
    return jamContext.belief_symbol.trim().slice(0, 64);
  }
  // Planned window at playhead (first entry) as soft fallback.
  const window = jamContext?.planned_window;
  if (Array.isArray(window) && window.length > 0) {
    const sym = window[0]?.symbol;
    if (typeof sym === 'string' && sym.trim()) return sym.trim().slice(0, 64);
  }
  return null;
}

function resolveStyleKind(style, role, densityNum) {
  if (role === 'bass') {
    // Bass prefers sparse root motion; pad → hold; arp stays arp.
    if (style === 'pad') return 'hold';
    if (style === 'arp' || style === 'alberti') return 'arp';
    return densityNum >= 0.7 ? 'arp' : 'block';
  }
  if (role === 'melody') {
    return style === 'pad' ? 'arp' : (style === 'alberti' ? 'alberti' : style === 'block' ? 'block' : 'arp');
  }
  if (role === 'texture') {
    return style === 'block' ? 'pad' : (style === 'alberti' ? 'arp' : style);
  }
  // accompaniment / harmony
  if (style === 'alberti') return 'alberti';
  if (style === 'pad') return 'pad';
  if (style === 'arp') return 'arp';
  return 'block';
}

function ensureAlbertiTones(midi) {
  if (midi.length >= 3) return midi.slice(0, 3);
  if (midi.length === 2) return [midi[0], midi[1], midi[0] + 12 > 127 ? midi[0] : midi[0] + 12];
  if (midi.length === 1) {
    const root = midi[0];
    return [root, Math.min(127, root + 7), Math.min(127, root + 12)];
  }
  return [48, 55, 60];
}

function addPassingTone(midi, octaveBase) {
  if (midi.length < 2) return midi;
  const a = midi[0];
  const b = midi[1];
  const mid = Math.round((a + b) / 2);
  if (mid === a || mid === b) return midi;
  const next = midi.slice();
  next.splice(1, 0, Math.max(octaveBase, Math.min(127, mid)));
  return next.slice(0, 5);
}

function velocityForRole(role, base) {
  if (role === 'bass') return Math.max(40, base - 8);
  if (role === 'melody') return Math.min(100, base + 10);
  if (role === 'texture') return Math.max(30, base - 16);
  return base;
}

function makeEvent(pitch, startTick, durationTicks, velocity, role) {
  return {
    pitch: Math.max(0, Math.min(127, Math.round(pitch))),
    start_tick: Math.max(0, Math.round(startTick)),
    duration_ticks: Math.max(1, Math.round(durationTicks)),
    velocity: Math.max(1, Math.min(127, Math.round(velocity))),
    track_role: role,
  };
}
