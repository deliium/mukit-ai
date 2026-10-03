/**
 * UI plan-body validators (not a realize twin).
 * Refuse embeds of events / pitch / harmony before PUT.
 */

import {
  ACCENT_STRENGTH_MAX,
  ARTICULATION_BIAS_ABS_MAX,
  DYNAMICS_CONTRAST_MAX,
  DYNAMICS_STRENGTH_MAX,
  MICROTIMING_HUMANIZE_MAX,
  MICROTIMING_SWING_MAX,
  ORCHESTRAL_BALANCE_GAIN_MAX,
  ORCHESTRAL_BALANCE_GAIN_MIN,
  PEDALING_DEPTH_MAX,
  PHRASING_ARC_MAX,
  PHRASING_BREATH_GAP_MAX,
  PLAN_SCHEMA_VERSION,
  PRESET_IDS,
  TEMPO_RUBATO_DEPTH_MAX,
  TEMPO_RUBATO_RATE_MAX,
  VELOCITY_MAX,
  VELOCITY_MIN,
} from './constants.js';

/**


 * @param {unknown} payload
 * @param {string} [path]
 * @returns {{ ok: false, code: string, path: string } | null}
 */
function findForbiddenEmbed(payload, path = '') {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return null;
  }
  for (const [key, value] of Object.entries(payload)) {
    const childPath = path ? `${path}.${key}` : key;
    if (key === 'events' || key === 'notes' || key === 'midi_events' || key === 'note_performances') {
      return { ok: false, code: 'plan_embeds_events', path: childPath };
    }
    if (key === 'harmony' && Array.isArray(value)) {
      return { ok: false, code: 'plan_embeds_harmony', path: childPath };
    }
    if ((key === 'pitch' || key === 'pitches') && typeof value !== 'number') {
      return { ok: false, code: 'plan_embeds_pitch', path: childPath };
    }
    if (key === 'composition' || key === 'composition_json') {
      return { ok: false, code: 'plan_embeds_forbidden', path: childPath };
    }
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const nested = findForbiddenEmbed(value, childPath);
      if (nested) return nested;
    }
    if (Array.isArray(value)) {
      for (let i = 0; i < value.length; i += 1) {
        if (value[i] && typeof value[i] === 'object') {
          const nested = findForbiddenEmbed(value[i], `${childPath}[${i}]`);
          if (nested) return nested;
        }
      }
    }
  }
  return null;
}

function inRange(value, min, max) {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
}

/**
 * @param {unknown} plan
 * @returns {{ ok: true } | { ok: false, code: string, path?: string, message?: string }}
 */
export function validatePerformancePlanBody(plan) {
  if (!plan || typeof plan !== 'object' || Array.isArray(plan)) {
    return { ok: false, code: 'performance_plan_invalid', message: 'plan must be an object' };
  }
  const embed = findForbiddenEmbed(plan);
  if (embed) return embed;

  if (plan.schema_version !== PLAN_SCHEMA_VERSION) {
    return { ok: false, code: 'unsupported_schema_version' };
  }
  if (!PRESET_IDS.includes(plan.preset_id)) {
    return { ok: false, code: 'preset_unknown' };
  }
  if (typeof plan.name !== 'string' || !plan.name.trim()) {
    return { ok: false, code: 'performance_plan_invalid', path: 'name' };
  }
  if (typeof plan.source_composition_fingerprint !== 'string' || plan.source_composition_fingerprint.length < 8) {
    return { ok: false, code: 'performance_plan_invalid', path: 'source_composition_fingerprint' };
  }

  const dims = plan.dimensions;
  if (!dims || typeof dims !== 'object') {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions' };
  }
  const rubato = dims.tempo_rubato || {};
  if (!inRange(rubato.depth ?? 0, 0, TEMPO_RUBATO_DEPTH_MAX)
    || !inRange(rubato.rate ?? 0, 0, TEMPO_RUBATO_RATE_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.tempo_rubato' };
  }
  const dynamics = dims.dynamics || {};
  if (!inRange(dynamics.curve_strength ?? 0, 0, DYNAMICS_STRENGTH_MAX)
    || !inRange(dynamics.contrast ?? 0, 0, DYNAMICS_CONTRAST_MAX)
    || !inRange(dynamics.velocity_floor ?? VELOCITY_MIN, VELOCITY_MIN, VELOCITY_MAX)
    || !inRange(dynamics.velocity_ceiling ?? VELOCITY_MAX, VELOCITY_MIN, VELOCITY_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.dynamics' };
  }
  const phrasing = dims.phrasing || {};
  if (!inRange(phrasing.breath_gap_ticks ?? 0, 0, PHRASING_BREATH_GAP_MAX)
    || !inRange(phrasing.phrase_arc ?? 0, 0, PHRASING_ARC_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.phrasing' };
  }
  const articulation = dims.articulation || {};
  if (!inRange(articulation.legato_bias ?? 0, -ARTICULATION_BIAS_ABS_MAX, ARTICULATION_BIAS_ABS_MAX)
    || !inRange(articulation.staccato_bias ?? 0, -ARTICULATION_BIAS_ABS_MAX, ARTICULATION_BIAS_ABS_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.articulation' };
  }
  const pedaling = dims.pedaling || {};
  if (!inRange(pedaling.depth ?? 0, 0, PEDALING_DEPTH_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.pedaling' };
  }
  const micro = dims.microtiming || {};
  if (!inRange(micro.swing ?? 0, 0, MICROTIMING_SWING_MAX)
    || !inRange(micro.humanize ?? 0, 0, MICROTIMING_HUMANIZE_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.microtiming' };
  }
  const accent = dims.accent || {};
  if (!inRange(accent.downbeat ?? 0, 0, ACCENT_STRENGTH_MAX)
    || !inRange(accent.offbeat ?? 0, 0, ACCENT_STRENGTH_MAX)) {
    return { ok: false, code: 'performance_plan_invalid', path: 'dimensions.accent' };
  }
  const balance = dims.orchestral_balance || {};
  for (const [key, gain] of Object.entries(balance.role_gains || {})) {
    if (!inRange(gain, ORCHESTRAL_BALANCE_GAIN_MIN, ORCHESTRAL_BALANCE_GAIN_MAX)) {
      return { ok: false, code: 'performance_plan_invalid', path: `dimensions.orchestral_balance.role_gains.${key}` };
    }
  }
  for (const [key, gain] of Object.entries(balance.track_gains || {})) {
    if (!inRange(gain, ORCHESTRAL_BALANCE_GAIN_MIN, ORCHESTRAL_BALANCE_GAIN_MAX)) {
      return { ok: false, code: 'performance_plan_invalid', path: `dimensions.orchestral_balance.track_gains.${key}` };
    }
  }
  return { ok: true };
}
