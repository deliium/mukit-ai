import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  configureMotifApplyViaStore,
  createProjectAndGenerateExpressive,
  getStoreSnapshot,
  openMotifsTab,
  openPianoTab,
  selectMotifSourceNotesViaStore,
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
  await openPianoTab(page);

  await openMotifsTab(page);
  await expect(page.getByTestId('motif-panel')).toBeVisible();
  await expect(page.getByTestId('motif-mark-button')).toBeDisabled();
  await expect(page.getByTestId('motif-apply-disabled-reason')).toContainText(/Select a source motif/i);

  await openPianoTab(page);
  const selection = await selectMotifSourceNotesViaStore(page, {
    trackId: 'melody-1',
    eventIds: ['m1', 'm2', 'm3', 'm4'],
  });
  expect(selection.ok).toBe(true);

  await expect(page.getByTestId('piano-roll-motif-selection-status')).toContainText(/eligible/i);
  await expect(page.getByTestId('piano-roll-mark-motif')).toBeEnabled();

  await openMotifsTab(page);
  await page.getByTestId('motif-mark-button').click();
  await expect(page.getByTestId('motif-definition-list')).toContainText(/Motif /i);

  const configured = await configureMotifApplyViaStore(page, {
    trackId: 'harmony-1',
    startBar: 3,
    operation: 'repeat',
    operationParams: {},
  });
  expect(configured.ok).toBe(true);
  await expect(page.getByTestId('motif-apply-button')).toBeEnabled();

  await openPianoTab(page);
  await expect(page.getByTestId('composer-tab-piano')).toHaveAttribute('aria-selected', 'true');
  await openMotifsTab(page);
  await expect(page.getByTestId('composer-tab-motifs')).toHaveAttribute('aria-selected', 'true');

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
