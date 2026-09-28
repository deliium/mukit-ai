import { expect, test } from '@playwright/test';

import { assertSchemaV2, createProjectAndGenerateExpressive, getStoreSnapshot } from './helpers.js';

async function openAdaptive(page) {
  await page.getByTestId('composer-tab-adaptive').click();
  await expect(page.getByTestId('adaptive-score-panel')).toBeVisible();
}

async function addState(page, name) {
  await page.getByTestId('adaptive-state-name').fill(name);
  await page.getByTestId('adaptive-create-state').click();
  await expect(page.getByTestId('adaptive-graph')).toContainText(name);
}

test('Adaptive tab previews a bar-aligned schedule without changing the score', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const snapshot = await createProjectAndGenerateExpressive(page);
  assertSchemaV2(snapshot);
  const barCount = snapshot.barCount;

  await openAdaptive(page);
  await page.getByTestId('adaptive-new-score').click();
  await addState(page, 'Exploration');
  await addState(page, 'Combat');
  await page.getByRole('button', { name: 'Exploration' }).click();
  await page.getByTestId('adaptive-transition-to').selectOption({ label: 'Combat' });
  await page.getByTestId('adaptive-create-transition').click();
  await expect(page.getByTestId('adaptive-transition-to-combat').or(page.getByTestId('adaptive-graph'))).toBeVisible();

  await page.getByRole('button', { name: 'Exploration' }).click();
  await page.getByTestId('adaptive-schedule-to').selectOption({ label: 'Combat' });
  await page.getByTestId('adaptive-schedule-position').fill('1');
  await page.getByTestId('adaptive-transition-schedule').click();
  const latency = page.getByTestId('adaptive-transition-latency');
  await expect(latency).toBeVisible();
  const firstLatency = await latency.textContent();
  const status = page.getByTestId('adaptive-scheduled-status');
  await expect(status).toContainText('bar');
  const firstBoundary = await status.textContent();
  expect(firstBoundary).toMatch(/boundary\s+(1|1920|3840|5760|7680)/);

  await page.getByTestId('adaptive-transition-schedule').click();
  await expect(latency).toHaveText(firstLatency || '');
  await expect(status).toContainText(firstBoundary?.match(/boundary\s+\d+/)?.[0] || 'boundary');

  await page.getByTestId('adaptive-transition-cancel').click();
  await expect(latency).toHaveCount(0);

  const after = await getStoreSnapshot(page);
  assertSchemaV2(after);
  expect(after.barCount).toBe(barCount);

  await page.setViewportSize({ width: 390, height: 844 });
  await openAdaptive(page);
  await page.getByRole('button', { name: 'Exploration' }).click();
  await page.getByTestId('adaptive-schedule-to').selectOption({ label: 'Combat' });
  await page.getByTestId('adaptive-schedule-position').fill('1');
  await page.getByTestId('adaptive-transition-schedule').click();
  await expect(page.getByTestId('adaptive-transition-latency')).toBeVisible();
  await page.getByTestId('adaptive-transition-latency').scrollIntoViewIfNeeded();
});
