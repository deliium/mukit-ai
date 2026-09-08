import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  createProjectAndGenerate,
  createProjectAndGenerateExpressive,
  editMelodyNoteViaStore,
  getAnalysisSnapshot,
  mockAnalysisRoute,
  openAnalysisTab,
  sampleAnalysisReportForE2e,
  waitForAnalysisStale,
  waitForAnalysisSuccess,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

async function seedComposition(page) {
  let snapshot = await createProjectAndGenerateExpressive(page);
  if ((snapshot?.eventCount || 0) < 4) {
    snapshot = await createProjectAndGenerate(page, { durationBars: 16, minEvents: 8 });
  }
  assertSchemaV2(snapshot);
  expect(snapshot.eventCount).toBeGreaterThan(3);
  return snapshot;
}

test('Analysis panel: whole composition, section, track, stale recompute, responsive', async ({
  page,
}) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Analysis tab with real fake-backend analysis endpoint',
  });

  const consoleHits = [];
  page.on('console', (msg) => {
    const text = msg.text();
    if (/Composition analysis|musicStore.*Analysis|CompositionAnalysisPanel/i.test(text)) {
      consoleHits.push(text.slice(0, 240));
    }
    if (msg.type() === 'error') {
      console.info('[browser console error]', text);
    }
  });

  await seedComposition(page);
  await openAnalysisTab(page);

  const whole = await waitForAnalysisSuccess(page, { scopeKind: 'composition' });
  expect(whole.analysisScope).toBe('composition');
  expect(whole.algorithmVersion).toBeTruthy();
  expect(whole.fingerprintPrefix).toBeTruthy();
  await expect(page.getByTestId('analysis-card-tonality')).toBeVisible();
  await expect(page.getByTestId('analysis-card-harmony')).toBeVisible();
  await expect(page.getByTestId('analysis-card-phrase')).toBeVisible();
  await expect(page.getByTestId('analysis-card-density')).toBeVisible();
  await expect(page.getByTestId('analysis-status')).toContainText(/current|ready|Analysis/i);

  await page.getByTestId('analysis-scope-section').click();
  await expect(page.getByTestId('analysis-section-select')).toBeVisible();
  const sectionSelect = page.getByTestId('analysis-section-select');
  const sectionOptions = sectionSelect.locator('option');
  const sectionCount = await sectionOptions.count();
  expect(sectionCount).toBeGreaterThan(0);
  if (sectionCount > 1) {
    const value = await sectionOptions.nth(Math.min(1, sectionCount - 1)).getAttribute('value');
    if (value) {
      await sectionSelect.selectOption(value);
    }
  }
  const section = await waitForAnalysisSuccess(page, { scopeKind: 'section' });
  expect(section.resolvedScopeKind).toBe('section');
  await expect(page.getByTestId('analysis-card-tonality')).toBeVisible();
  await expect(page.getByTestId('analysis-card-harmony')).toBeVisible();
  await expect(page.getByTestId('analysis-card-phrase')).toBeVisible();
  await expect(page.getByTestId('analysis-card-density')).toBeVisible();

  await page.getByTestId('analysis-scope-track').click();
  await expect(page.getByTestId('analysis-track-select')).toBeVisible();
  const trackSelect = page.getByTestId('analysis-track-select');
  const trackOptions = trackSelect.locator('option');
  const trackCount = await trackOptions.count();
  expect(trackCount).toBeGreaterThan(0);
  const trackValue = await trackOptions.first().getAttribute('value');
  if (trackValue) {
    await trackSelect.selectOption(trackValue);
  }
  const track = await waitForAnalysisSuccess(page, { scopeKind: 'track' });
  expect(track.resolvedScopeKind).toBe('track');
  await expect(page.getByTestId('analysis-metric-grid')).toBeVisible();

  await page.getByTestId('analysis-scope-composition').click();
  await waitForAnalysisSuccess(page, { scopeKind: 'composition' });

  const editResult = await editMelodyNoteViaStore(page);
  expect(editResult.ok).toBeTruthy();
  await waitForAnalysisStale(page);
  const recomputed = await waitForAnalysisSuccess(page, { scopeKind: 'composition', timeout: 90_000 });
  expect(recomputed.isStale).toBe(false);
  expect(recomputed.isCurrent).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId('composition-analysis-panel')).toBeVisible();
  const overflow = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="composition-analysis-panel"]');
    if (!panel) {
      return { ok: false, reason: 'panel missing' };
    }
    const root = document.documentElement;
    const body = document.body;
    return {
      ok: true,
      panelScrollWidth: panel.scrollWidth,
      panelClientWidth: panel.clientWidth,
      docScrollWidth: root.scrollWidth,
      docClientWidth: root.clientWidth,
      bodyScrollWidth: body.scrollWidth,
    };
  });
  expect(overflow.ok).toBeTruthy();
  expect(overflow.panelScrollWidth).toBeLessThanOrEqual(overflow.panelClientWidth + 2);
  expect(overflow.docScrollWidth).toBeLessThanOrEqual(overflow.docClientWidth + 2);

  // Diagnostics should mention scope/status without dumping full composition JSON.
  expect(consoleHits.some((line) => /scope|status|warning/i.test(line))).toBeTruthy();
  expect(consoleHits.some((line) => /"tracks"\s*:\s*\[/.test(line))).toBeFalsy();
});

test('Analysis panel: mocked warning and error/retry UI', async ({ page }) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Route interception for deterministic analysis warning/error UI',
  });

  await seedComposition(page);
  await openAnalysisTab(page);
  await waitForAnalysisSuccess(page, { scopeKind: 'composition' });

  await mockAnalysisRoute(page, {
    status: 200,
    body: sampleAnalysisReportForE2e(),
  });
  await page.getByTestId('analysis-refresh').click();
  await waitForAnalysisSuccess(page, { scopeKind: 'composition' });
  await expect(page.getByTestId('analysis-warnings')).toBeVisible();
  await expect(page.getByTestId('analysis-warnings')).toContainText('empty_analysis_scope');
  const warned = await getAnalysisSnapshot(page);
  expect(warned.warningCodes).toContain('empty_analysis_scope');

  await page.unroute('**/analysis/composition');
  await mockAnalysisRoute(page, {
    status: 422,
    body: {
      detail: {
        code: 'analysis_invalid_composition',
        message: 'Mocked analysis failure for retry',
        details: { reason: 'e2e_mock' },
      },
    },
  });
  await page.getByTestId('analysis-refresh').click();
  await expect.poll(async () => (await getAnalysisSnapshot(page))?.analysisStatus, {
    timeout: 30_000,
  }).toBe('error');
  await expect(page.getByTestId('analysis-error')).toContainText(/Mocked analysis failure/i);
  await expect(page.getByTestId('analysis-retry')).toBeVisible();
  // Prior successful report remains available for stale display.
  const failed = await getAnalysisSnapshot(page);
  expect(failed.hasResult).toBe(true);

  await page.unroute('**/analysis/composition');
  await mockAnalysisRoute(page, {
    status: 200,
    body: sampleAnalysisReportForE2e({
      warnings: [],
      source_fingerprint: 'retry-ok-fingerprint-0123456789ab',
    }),
  });
  await page.getByTestId('analysis-retry').click();
  const recovered = await waitForAnalysisSuccess(page, { scopeKind: 'composition' });
  expect(recovered.analysisStatus).toBe('success');
  expect(recovered.analysisError).toBe('');
  await waitForCompositionNotes(page, { minEvents: 1, timeout: 15_000 });
});
