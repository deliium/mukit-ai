import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  createProjectAndGenerateExpressive,
  getStoreSnapshot,
  openMotifsTab,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

async function seedComposition(page) {
  const snapshot = await createProjectAndGenerateExpressive(page);
  assertSchemaV2(snapshot);
  expect(snapshot.eventCount).toBeGreaterThan(3);
  return snapshot;
}

test('Motifs tab: navigation, controls, disabled reasons, responsive layout', async ({ page }) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Motifs tab UI wiring with piano-roll selection helpers',
  });

  await seedComposition(page);
  await page.getByTestId('composer-tab-piano').click();
  await page.getByTestId('piano-roll-grid').waitFor({ state: 'visible', timeout: 30_000 });

  await openMotifsTab(page);
  await expect(page.getByTestId('motif-panel')).toBeVisible();
  await expect(page.getByTestId('motif-mark-button')).toBeDisabled();
  await expect(page.getByTestId('motif-apply-disabled-reason')).toContainText(/Select a source motif/i);

  await page.getByTestId('composer-tab-piano').click();
  await page.evaluate(() => {
    const store = window.__MUKIT_MUSIC_STORE__;
    const state = store.getState();
    const track = state.editedMusicJson?.tracks?.find((item) => item.id === 'melody-1')
      || state.editedMusicJson?.tracks?.[0];
    const events = (track?.events || []).slice(0, 3).map((event) => event.id);
    store.getState().selectPianoRollTrack(track.id);
    events.forEach((id, index) => {
      store.getState().selectPianoRollNote(id, { extend: index > 0 });
    });
  });

  await expect(page.getByTestId('piano-roll-motif-selection-status')).toContainText(/eligible/i);
  await expect(page.getByTestId('piano-roll-mark-motif')).toBeEnabled();

  await openMotifsTab(page);
  await page.getByTestId('motif-mark-button').click();
  await expect(page.getByTestId('motif-definition-list')).toContainText(/Motif A/i);

  await page.getByTestId('motif-destination-track').selectOption({ index: 1 });
  await page.getByTestId('motif-destination-start-bar').fill('3');
  await page.getByTestId('motif-operation').selectOption('repeat');
  await expect(page.getByTestId('motif-apply-button')).toBeEnabled();

  await page.getByTestId('composer-tab-motifs').focus();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByTestId('composer-tab-piano')).toHaveAttribute('aria-selected', 'true');

  await page.setViewportSize({ width: 390, height: 844 });
  await openMotifsTab(page);
  const overflow = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="motif-panel"]');
    if (!panel) {
      return { ok: false };
    }
    return {
      ok: true,
      panelScrollWidth: panel.scrollWidth,
      panelClientWidth: panel.clientWidth,
      docScrollWidth: document.documentElement.scrollWidth,
      docClientWidth: document.documentElement.clientWidth,
    };
  });
  expect(overflow.ok).toBe(true);
  expect(overflow.panelScrollWidth).toBeLessThanOrEqual(overflow.panelClientWidth + 2);
  expect(overflow.docScrollWidth).toBeLessThanOrEqual(overflow.docClientWidth + 2);

  const store = await getStoreSnapshot(page);
  expect(store.editedMusicJson?.motifs?.length).toBeGreaterThan(0);
  await waitForCompositionNotes(page, { minEvents: 3 });
});
