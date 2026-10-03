import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildAssetPackBrief,
  canGenerateAssetPack,
  canPreviewAssetPackPlan,
  canRegenerateAssetPackSlots,
  canSaveAssetPack,
  generateRequestBody,
  planSlotRows,
  regenerateRequestBody,
} from './assetPackUi.js';

test('builds a game preset brief without generating', () => {
  const brief = buildAssetPackBrief({
    title: 'Forest Pack',
    profileId: 'prof_abc',
    profileStrength: 'normal',
    masterTarget: 'cinematic',
    slotIds: ['main_theme', 'menu'],
  });
  assert.equal(brief.schema_version, 'asset.pack.brief.v1');
  assert.equal(brief.preset, 'game_soundtrack_v1');
  assert.deepEqual(brief.slots, [{ slot_id: 'main_theme' }, { slot_id: 'menu' }]);
  assert.equal(brief.composer_profile_id, 'prof_abc');
  assert.equal(brief.production.master_target, 'cinematic');
  assert.equal(brief.production.guarantee, false);
  assert.ok(brief.production.catalog_instrument_ids.length >= 1);
  assert.equal(brief.include_rendering, false);
});

test('gates preview / save / generate / regenerate', () => {
  const brief = buildAssetPackBrief({ title: 'X' });
  assert.equal(canPreviewAssetPackPlan(brief), true);
  assert.equal(canPreviewAssetPackPlan({}), false);

  const plan = {
    schema_version: 'asset.pack.plan.v1',
    plan_digest: 'a'.repeat(64),
    slots: [{ slot_id: 'main_theme', label: 'Main' }],
  };
  assert.equal(canSaveAssetPack(plan), true);

  const pack = { id: 'apack_1', document_revision: 2, status: 'planned' };
  assert.equal(canGenerateAssetPack(pack, plan), true);
  assert.deepEqual(generateRequestBody(pack, plan), {
    expected_plan_digest: plan.plan_digest,
    expected_revision: 2,
  });
  assert.equal(canGenerateAssetPack({ ...pack, status: 'generating' }, plan), false);

  assert.equal(canRegenerateAssetPackSlots(pack, ['menu']), true);
  assert.deepEqual(regenerateRequestBody(pack, ['menu', 'menu']), {
    slot_ids: ['menu'],
    expected_revision: 2,
  });
  assert.equal(canRegenerateAssetPackSlots(pack, []), false);
});

test('maps plan slots for display', () => {
  const rows = planSlotRows({
    slots: [
      {
        slot_id: 'menu',
        label: 'Menu',
        adaptive_label: 'menu',
        duration_seconds: 60,
        density: 'sparse',
      },
    ],
  });
  assert.deepEqual(rows, [
    {
      slotId: 'menu',
      label: 'Menu',
      adaptiveLabel: 'menu',
      durationSeconds: 60,
      density: 'sparse',
    },
  ]);
});
