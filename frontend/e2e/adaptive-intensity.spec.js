import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  createProjectAndGenerateExpressive,
  getStoreSnapshot,
} from './helpers.js';

test('Adaptive tab maps runtime intensity without rewriting the score', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const snapshot = await createProjectAndGenerateExpressive(page);
  assertSchemaV2(snapshot);
  const sectionId = snapshot.editedMusicJson?.sections?.find((section) => section.id)?.id
    || snapshot.editedMusicJson?.sections?.[0]?.id;
  const trackId = snapshot.editedMusicJson?.tracks?.[0]?.id;
  expect(snapshot.projectId).toBeTruthy();
  expect(trackId).toBeTruthy();

  const seeded = await page.evaluate(async ({ projectId, sectionId: section, trackId: track }) => {
    const material = section
      ? { kind: 'section', section_id: section }
      : { kind: 'bar_range', start_bar: 1, end_bar: 1 };
    const rows = [
      ['layer-pad', 'ambient', 0, 1, null, 0, 'cut'],
      ['layer-piano', 'harmony', 0, 1, null, 0, 'cut'],
      ['layer-bass', 'bass', 0.5, 1, null, 0, 'cut'],
      ['layer-strings', 'strings', 0.5, 1, null, 0, 'cut'],
      ['layer-perc', 'percussion', 0.8, 1, null, 0, 'cut'],
      ['layer-brass-hint', 'brass', 0.9, 1, 'orchestration', 0, 'cut'],
      ['layer-orch', 'brass', 1, 1, 'orchestration', 1, 'bar'],
    ];
    const response = await fetch(`/projects/${encodeURIComponent(projectId)}/adaptive-scores`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        is_default: true,
        score: {
          schema_version: 'adaptive.score.v1',
          name: 'Intensity cue',
          initial_state_id: 'state-exploration',
          default_state_id: 'state-exploration',
          states: [{
            id: 'state-exploration',
            name: 'Exploration',
            intensity: 0,
            material,
          }],
          layers: rows.map(([id, role, low, high, group, priority, policy]) => ({
            id,
            name: id,
            state_id: 'state-exploration',
            material: { kind: 'track_range', track_ids: [track] },
            intensity_min: low,
            intensity_max: high,
            mix_hint: 'bed',
            role,
            exclusive_group: group,
            priority,
            fade: { in_policy: policy, out_policy: 'cut' },
          })),
        },
      }),
    });
    const detail = await response.text();
    return { status: response.status, detail: detail.slice(0, 800), section, track };
  }, { projectId: snapshot.projectId, sectionId, trackId });
  expect(seeded.status, JSON.stringify(seeded)).toBe(201);

  await page.getByTestId('composer-tab-adaptive').click();
  await expect(page.getByTestId('adaptive-score-panel')).toBeVisible();
  await expect(page.getByTestId('adaptive-state-state-exploration')).toBeVisible();
  const beforeEvents = (await getStoreSnapshot(page)).eventCount;
  await page.getByTestId('adaptive-intensity').fill('0.5');
  await page.getByTestId('adaptive-map-layers').click();
  await expect(page.getByTestId('adaptive-layer-active-layer-bass')).toHaveText('active');
  await expect(page.getByTestId('adaptive-layer-active-layer-perc')).toHaveText('inactive');

  const roll = page.getByTestId('piano-roll-grid');
  if (await roll.count()) {
    await expect(roll).toBeVisible();
  }
  const after = await getStoreSnapshot(page);
  assertSchemaV2(after);
  expect(after.eventCount).toBe(beforeEvents);
});