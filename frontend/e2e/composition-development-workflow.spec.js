import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  getStoreSnapshot,
  loadRepoFixture,
  openDevelopTab,
  seedV1ProjectViaApi,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

async function getDevelopmentSnapshot(page) {
  return page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    if (!state) {
      return null;
    }
    const composition = state.editedMusicJson;
    const prefixEvents = (composition?.tracks || []).map((track) => ({
      id: track.id,
      events: (track.events || [])
        .filter((event) => Number(event.start_tick) < 16 * 1920)
        .map((event) => [event.id ?? null, event.pitch, event.start_tick, event.duration_ticks].join('|')),
    }));
    return {
      saveStatus: state.saveStatus,
      compositionRevision: state.compositionRevision,
      barCount: composition?.bar_count ?? null,
      durationTicks: composition?.duration_ticks ?? null,
      developmentStatus: state.developmentStatus,
      developmentError: state.developmentError || '',
      candidateCount: Array.isArray(state.developmentCandidates) ? state.developmentCandidates.length : 0,
      selectedCandidateId: state.developmentSelectedCandidateId,
      auditionActive: state.developmentAuditionActive,
      undoDepth: Array.isArray(state.compositionEditUndoStack) ? state.compositionEditUndoStack.length : 0,
      prefixEvents,
      sectionTypes: (composition?.sections || []).map((section) => section.type),
    };
  });
}

test('Develop workflow: 16+8 continuation preview non-mutating, select second candidate, apply', async ({
  page,
  request,
}) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Generate three 8-bar continuations, prove non-mutation, apply non-first candidate to 24 bars',
  });

  const v1Fixture = loadRepoFixture('backend/tests/fixtures/composition_v1_16bar_multitrack.json');
  const seeded = await seedV1ProjectViaApi(request, {
    name: 'Composition Development Acceptance',
    fixture: v1Fixture,
  });

  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });

  const opened = await getStoreSnapshot(page);
  assertSchemaV2(opened);
  expect(opened.barCount).toBe(16);

  await openDevelopTab(page);
  await expect(page.getByTestId('develop-panel')).toBeVisible();

  await page.getByTestId('develop-operation').selectOption('continue');
  await page.getByTestId('develop-output-bars').fill('8');
  await page.getByTestId('develop-candidate-count').selectOption('3');
  await page.getByTestId('develop-strength').selectOption('balanced');

  const before = await getDevelopmentSnapshot(page);
  await page.getByTestId('develop-generate').click();
  await expect(page.getByTestId('develop-status')).toContainText(/candidate\(s\) ready/i, {
    timeout: 120_000,
  });

  const afterPreview = await getDevelopmentSnapshot(page);
  expect(afterPreview.developmentStatus).toBe('ready');
  expect(afterPreview.candidateCount).toBe(3);
  expect(afterPreview.barCount).toBe(16);
  expect(afterPreview.compositionRevision).toBe(before.compositionRevision);
  expect(afterPreview.prefixEvents).toEqual(before.prefixEvents);
  expect(afterPreview.undoDepth).toBe(before.undoDepth);

  await page.getByTestId('develop-candidate-1').click();
  await expect(page.getByTestId('develop-candidate-1')).toHaveAttribute('aria-selected', 'true');

  await page.getByTestId('develop-audition').click();
  await expect(page.getByTestId('develop-audition-active')).toBeVisible();
  const auditioning = await getDevelopmentSnapshot(page);
  expect(auditioning.auditionActive).toBe(true);
  expect(auditioning.barCount).toBe(16);

  await page.getByTestId('develop-apply').click();
  await expect.poll(async () => (await getDevelopmentSnapshot(page)).barCount).toBe(24);

  const applied = await getDevelopmentSnapshot(page);
  expect(applied.barCount).toBe(24);
  expect(applied.durationTicks).toBe(24 * 1920);
  expect(applied.prefixEvents).toEqual(before.prefixEvents);
  expect(applied.undoDepth).toBe(before.undoDepth + 1);
  expect(applied.candidateCount).toBe(0);
});

test('Develop tab keyboard focus and 390px layout', async ({ page, request }) => {
  const v1Fixture = loadRepoFixture('backend/tests/fixtures/composition_v1_16bar_multitrack.json');
  const seeded = await seedV1ProjectViaApi(request, {
    name: 'Develop Mobile Layout',
    fixture: v1Fixture,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });

  await page.getByTestId('composer-tab-develop').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('develop-panel')).toBeVisible();

  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth > doc.clientWidth + 1;
  });
  expect(overflow).toBe(false);
});
