import { expect, test } from '@playwright/test';

import { assertSchemaV2, createProjectAndGenerateExpressive, getStoreSnapshot } from './helpers.js';

test.describe.configure({ mode: 'serial' });

async function openAdaptive(page) {
  await page.getByTestId('composer-tab-adaptive').click();
  await expect(page.getByTestId('adaptive-score-panel')).toBeVisible();
}

async function addState(page, name) {
  await page.getByTestId('adaptive-state-name').fill(name);
  await page.getByTestId('adaptive-create-state').click();
  await expect(page.getByTestId('adaptive-graph')).toContainText(name);
}

test('Adaptive tab authors Exploration, Combat, and Victory', async ({ page }) => {
  const snapshot = await createProjectAndGenerateExpressive(page);
  assertSchemaV2(snapshot);
  const projectId = await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().currentProjectId);
  expect(projectId).toBeTruthy();

  await openAdaptive(page);
  await page.getByTestId('adaptive-new-score').click();
  await expect(page.getByTestId('adaptive-create-state')).toBeVisible();

  await addState(page, 'Exploration');
  await addState(page, 'Combat');
  await addState(page, 'Victory');

  await page.getByRole('button', { name: 'Exploration' }).click();
  await page.getByTestId('adaptive-material-kind').selectOption('section');
  await page.getByTestId('adaptive-material-section').selectOption('section-1');
  await page.getByTestId('adaptive-assign-material').click();
  await expect(page.getByTestId('adaptive-graph')).toContainText('Section section-1');

  await page.getByRole('button', { name: 'Combat' }).click();
  await page.getByTestId('adaptive-material-section').selectOption('section-2');
  await page.getByTestId('adaptive-assign-material').click();
  await expect(page.getByTestId('adaptive-graph')).toContainText('Section section-2');

  await page.getByRole('button', { name: 'Victory' }).click();
  await page.getByTestId('adaptive-material-kind').selectOption('bar_range');
  await page.getByTestId('adaptive-bar-start').fill('1');
  await page.getByTestId('adaptive-bar-end').fill('4');
  await page.getByTestId('adaptive-assign-material').click();
  await expect(page.getByTestId('adaptive-graph')).toContainText('Bars 1–4');

  await page.getByRole('button', { name: 'Exploration' }).click();
  await page.getByTestId('adaptive-transition-to').selectOption({ label: 'Combat' });
  await page.getByTestId('adaptive-create-transition').click();
  await page.getByRole('button', { name: 'Combat' }).click();
  await page.getByTestId('adaptive-transition-to').selectOption({ label: 'Victory' });
  await page.getByTestId('adaptive-create-transition').click();

  await page.getByTestId('adaptive-validate-button').click();
  await expect(page.getByTestId('adaptive-current-state')).toContainText('Current state:');
  await expect(page.locator('[data-testid^="adaptive-finding-"]')).toHaveCount(0);

  await page.reload();
  await page.getByTestId(`open-project-${projectId}`).click();
  await openAdaptive(page);
  await expect(page.getByTestId('adaptive-graph')).toContainText('Exploration');
  await expect(page.getByTestId('adaptive-graph')).toContainText('Combat');
  await expect(page.getByTestId('adaptive-graph')).toContainText('Victory');
  await expect(page.getByTestId('adaptive-graph')).toContainText('Bars 1–4');

  await page.getByRole('button', { name: 'Victory' }).click();
  await page.getByTestId('adaptive-loop-start').fill('4');
  await page.getByTestId('adaptive-loop-end').fill('2');
  await page.getByTestId('adaptive-assign-loop').click();
  await expect(page.getByTestId('adaptive-finding-loop_bounds')).toBeVisible();
  await expect(page.getByTestId('adaptive-graph')).toContainText('Exploration');
  await expect(page.getByTestId('adaptive-graph')).toContainText('Combat');
  await expect(page.getByTestId('adaptive-graph')).toContainText('Victory');

  await page.setViewportSize({ width: 390, height: 844 });
  await openAdaptive(page);
  await expect(page.getByTestId('adaptive-graph')).toContainText('Exploration');
  await expect(page.getByTestId('adaptive-graph')).toContainText('Victory');
  const reloaded = await getStoreSnapshot(page);
  assertSchemaV2(reloaded);
});
