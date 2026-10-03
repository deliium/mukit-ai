/** Request shaping and action gates for the Agents-tab Asset Pack panel. */

const PROFILE_STRENGTHS = new Set(['off', 'light', 'normal', 'strong']);
const MASTER_TARGETS = new Set(['dynamic', 'streaming', 'cinematic', 'demo']);

export const GAME_SOUNDTRACK_SLOT_IDS = [
  'main_theme',
  'menu',
  'explore_forest',
  'explore_city',
  'combat_low',
  'combat_high',
  'boss',
  'victory',
  'defeat',
  'credits',
];

/**
 * Build `asset.pack.brief.v1` from panel fields.
 * Opening the panel must not call this into generate — callers decide.
 */
export function buildAssetPackBrief({
  title = '',
  useGamePreset = true,
  slotIds = null,
  profileId = '',
  profileStrength = 'normal',
  masterTarget = 'cinematic',
  loudnessGoalLufs = '',
  includeRendering = false,
  includeAdaptiveScaffolds = true,
  seed = 0,
} = {}) {
  const strength = PROFILE_STRENGTHS.has(profileStrength) ? profileStrength : 'normal';
  const master = MASTER_TARGETS.has(masterTarget) ? masterTarget : 'cinematic';
  const brief = {
    schema_version: 'asset.pack.brief.v1',
    title: String(title).trim() || 'Soundtrack Pack',
    seed_slot_id: 'main_theme',
    include_rendering: Boolean(includeRendering),
    include_adaptive_scaffolds: Boolean(includeAdaptiveScaffolds),
    seed: Number.isFinite(Number(seed)) ? Math.trunc(Number(seed)) : 0,
    production: {
      schema_version: 'asset.pack.production.v1',
      master_target: master,
      guarantee: false,
      catalog_instrument_ids: ['violin', 'cello', 'french_horn', 'harp'],
    },
  };

  const loudness = String(loudnessGoalLufs).trim();
  if (loudness !== '') {
    const parsed = Number(loudness);
    if (Number.isFinite(parsed)) {
      brief.production.loudness_goal_lufs = parsed;
    }
  }

  const profile = String(profileId).trim();
  if (profile) {
    brief.composer_profile_id = profile.slice(0, 80);
    brief.composer_profile_strength = strength;
  }

  if (useGamePreset) {
    brief.preset = 'game_soundtrack_v1';
    if (Array.isArray(slotIds) && slotIds.length > 0) {
      brief.slots = slotIds
        .map((id) => String(id).trim())
        .filter(Boolean)
        .map((slot_id) => ({ slot_id }));
    }
  } else if (Array.isArray(slotIds) && slotIds.length > 0) {
    brief.slots = slotIds
      .map((id) => String(id).trim())
      .filter(Boolean)
      .map((slot_id) => ({ slot_id }));
  } else {
    brief.slots = GAME_SOUNDTRACK_SLOT_IDS.map((slot_id) => ({ slot_id }));
  }

  return brief;
}

export function canPreviewAssetPackPlan(brief) {
  return Boolean(brief?.schema_version === 'asset.pack.brief.v1' && brief?.title);
}

export function canSaveAssetPack(plan) {
  return Boolean(plan?.schema_version === 'asset.pack.plan.v1' && plan?.plan_digest);
}

export function canGenerateAssetPack(pack, plan) {
  if (!pack?.id || !plan?.plan_digest) return false;
  if (!Number.isInteger(pack.document_revision) || pack.document_revision < 1) return false;
  if (pack.status === 'generating') return false;
  return true;
}

export function generateRequestBody(pack, plan) {
  if (!canGenerateAssetPack(pack, plan)) return null;
  return {
    expected_plan_digest: plan.plan_digest,
    expected_revision: pack.document_revision,
  };
}

export function canRegenerateAssetPackSlots(pack, slotIds) {
  if (!pack?.id) return false;
  if (!Number.isInteger(pack.document_revision) || pack.document_revision < 1) return false;
  if (pack.status === 'generating') return false;
  if (!Array.isArray(slotIds) || slotIds.length === 0) return false;
  return true;
}

export function regenerateRequestBody(pack, slotIds) {
  if (!canRegenerateAssetPackSlots(pack, slotIds)) return null;
  return {
    slot_ids: [...new Set(slotIds.map((id) => String(id).trim()).filter(Boolean))],
    expected_revision: pack.document_revision,
  };
}

export function planSlotRows(plan) {
  const slots = Array.isArray(plan?.slots) ? plan.slots : [];
  return slots.map((slot) => ({
    slotId: slot.slot_id,
    label: slot.label || slot.slot_id,
    adaptiveLabel: slot.adaptive_label,
    durationSeconds: slot.duration_seconds,
    density: slot.density,
  }));
}

export function propagateRows(plan) {
  const table = plan?.theme_policy?.propagate || {};
  return Object.entries(table).map(([slotId, op]) => ({
    slotId,
    operation: op?.operation || 'repeat',
    transposeSemitones: op?.transpose_semitones ?? null,
    startBar: op?.destination_start_bar ?? 1,
  }));
}
