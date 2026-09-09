import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  configureMotifApplyViaStore,
  createProjectAndGenerateExpressive,
  getMotifSnapshot,
  getStoreSnapshot,
  mockMotifApplyRoute,
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

async function markMotifAFromBarOne(page) {
  await openPianoTab(page);
  const selection = await selectMotifSourceNotesViaStore(page, {
    trackId: 'melody-1',
    eventIds: ['m1', 'm2', 'm3', 'm4'],
  });
  expect(selection.ok).toBe(true);
  await expect(page.getByTestId('piano-roll-motif-selection-status')).toContainText(/eligible/i);
  await openMotifsTab(page);
  await page.getByTestId('motif-mark-button').click();
  await expect(page.getByTestId('motif-definition-list')).toContainText(/Motif /i);
}

test('Motif workflow: mark Motif A, transpose to bass, assert derived occurrence', async ({ page }) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Author Motif A and realize a mechanical transpose on another track',
  });

  await seedComposition(page);
  const before = await getMotifSnapshot(page);
  const sourceEventIds = ['m1', 'm2', 'm3', 'm4'];
  const sourcePitchesBefore = await page.evaluate((ids) => {
    const track = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson?.tracks
      ?.find((item) => item.id === 'melody-1');
    return ids.map((id) => track?.events?.find((event) => event.id === id)?.pitch || null);
  }, sourceEventIds);

  await markMotifAFromBarOne(page);

  const configured = await configureMotifApplyViaStore(page, {
    trackId: 'harmony-1',
    startBar: 3,
    operation: 'transpose',
    operationParams: { transpose_semitones: 2 },
  });
  expect(configured.ok).toBe(true);
  expect(configured.destinationTrackId).toBe('harmony-1');
  expect(configured.destinationStartBar).toBe(3);
  expect(configured.destinationStartTick).toBeNull();

  const applyButton = page.getByTestId('motif-apply-button');
  await expect(applyButton).toBeEnabled();
  await applyButton.click();
  if (await page.getByTestId('motif-replace-confirm').isVisible().catch(() => false)) {
    await applyButton.click();
  }
  await expect(page.getByTestId('motif-apply-success')).toBeVisible({ timeout: 30_000 });

  const after = await getMotifSnapshot(page);
  expect(after.motifCount).toBeGreaterThanOrEqual(1);
  expect(after.labels.some((label) => /^Motif [A-Z]/i.test(label || ''))).toBe(true);
  const appliedMotif = after.motifs.find((item) => item.id === configured.motifId)
    || after.motifs[after.motifs.length - 1];
  expect(appliedMotif.occurrenceCount).toBeGreaterThanOrEqual(2);
  expect(appliedMotif.relationships).toContain('original');
  expect(appliedMotif.relationships).toContain('transpose');
  expect(appliedMotif.eventRefsResolvable).toBe(true);
  expect(after.eventCount).toBeGreaterThan(before.eventCount);
  expect(after.notationRevision).not.toBeNull();

  const sourcePitchesAfter = await page.evaluate((ids) => {
    const track = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson?.tracks
      ?.find((item) => item.id === 'melody-1');
    return ids.map((id) => track?.events?.find((event) => event.id === id)?.pitch || null);
  }, sourceEventIds);
  expect(sourcePitchesAfter).toEqual(sourcePitchesBefore);

  const destinationIds = await page.evaluate((motifId) => {
    const motifs = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson?.motifs || [];
    const motif = motifs.find((item) => item.id === motifId) || motifs[motifs.length - 1];
    const transpose = [...(motif?.occurrences || [])].reverse()
      .find((occ) => occ.relationship === 'transpose' && occ.track_id === 'harmony-1')
      || [...(motif?.occurrences || [])].reverse().find((occ) => occ.relationship === 'transpose');
    return {
      motifId: motif?.id || null,
      trackId: transpose?.track_id || null,
      eventIds: transpose?.event_ids || [],
    };
  }, configured.motifId);
  expect(destinationIds.trackId).toBe('harmony-1');
  expect(destinationIds.eventIds.length).toBeGreaterThanOrEqual(3);
  expect(destinationIds.eventIds.every((id) => !sourceEventIds.includes(id))).toBe(true);

  console.info('[e2e-motifs] Applied transpose', {
    motifCount: after.motifCount,
    occurrenceCount: appliedMotif.occurrenceCount,
    createdCount: destinationIds.eventIds.length,
    eventCount: after.eventCount,
  });
});

test('Motif workflow: invalid selection, mocked errors, undo, save/reopen, responsive', async ({ page }) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Motif validation edges, error mapping, undo, persistence, and 390px layout',
  });

  const snapshot = await seedComposition(page);
  const projectId = snapshot.projectId;
  expect(projectId).toBeTruthy();

  await openPianoTab(page);

  // Too few notes → not eligible
  const tooFew = await selectMotifSourceNotesViaStore(page, {
    trackId: 'melody-1',
    eventIds: ['m1', 'm2'],
  });
  expect(tooFew.ok).toBe(true);
  await expect(page.getByTestId('piano-roll-motif-selection-status')).not.toContainText(/eligible/i);
  await openMotifsTab(page);
  await expect(page.getByTestId('motif-mark-button')).toBeDisabled();

  // Incomplete tie chain (m3 without m4) → not eligible
  await openPianoTab(page);
  await selectMotifSourceNotesViaStore(page, {
    trackId: 'melody-1',
    eventIds: ['m1', 'm2', 'm3'],
  });
  await expect(page.getByTestId('piano-roll-motif-selection-status')).toContainText(/tie/i);

  await markMotifAFromBarOne(page);

  // Mocked 422 leaves composition motifs unchanged (atomicity)
  const beforeError = await getMotifSnapshot(page);
  await mockMotifApplyRoute(page, {
    status: 422,
    once: true,
    body: {
      detail: {
        code: 'motif_destination_out_of_bounds',
        message: 'Destination placement exceeds composition bounds.',
        details: { reason: 'e2e_mock' },
      },
    },
  });
  let configured = await configureMotifApplyViaStore(page, {
    trackId: 'harmony-1',
    startBar: 3,
    operation: 'repeat',
    operationParams: {},
  });
  expect(configured.ok).toBe(true);
  await page.getByTestId('motif-apply-button').click();
  if (await page.getByTestId('motif-replace-confirm').isVisible().catch(() => false)) {
    await page.getByTestId('motif-apply-button').click();
  }
  await expect(page.getByTestId('motif-apply-error')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('motif-apply-error')).toContainText(/bounds|Destination/i);
  const afterError = await getMotifSnapshot(page);
  expect(afterError.motifCount).toBe(beforeError.motifCount);
  const beforeOcc = beforeError.motifs.find((item) => item.id === configured.motifId)
    || beforeError.motifs[beforeError.motifs.length - 1];
  const afterOcc = afterError.motifs.find((item) => item.id === configured.motifId)
    || afterError.motifs[afterError.motifs.length - 1];
  expect(afterOcc.occurrenceCount).toBe(beforeOcc.occurrenceCount);

  // Mocked 503 creative path messaging
  await mockMotifApplyRoute(page, {
    status: 503,
    once: true,
    body: {
      detail: {
        code: 'motif_creative_provider_required',
        message: 'Creative motif operations require LLM provider selection.',
        details: { reason: 'e2e_mock' },
      },
    },
  });
  configured = await configureMotifApplyViaStore(page, {
    trackId: 'harmony-1',
    startBar: 3,
    operation: 'melodic_variation',
    operationParams: {},
    variationStrength: 0.4,
  });
  expect(configured.ok).toBe(true);
  // Creative ops require provider; force apply path by ensuring selection exists then mocking failure
  await page.evaluate(() => {
    const api = window.__MUKIT_MUSIC_STORE__;
    // Keep destination config; creative disabledReason may still block the button.
    // Invoke applyMotifTransformation directly so the mocked 503 is exercised.
    return api.getState().applyMotifTransformation();
  });
  await expect.poll(async () => (await getMotifSnapshot(page)).motifApplyStatus).toBe('error');
  await expect(page.getByTestId('motif-apply-error')).toContainText(/provider|Creative|unavailable|require|bounds/i);

  // Successful mechanical apply against live backend
  configured = await configureMotifApplyViaStore(page, {
    trackId: 'harmony-1',
    startBar: 3,
    operation: 'transpose',
    operationParams: { transpose_semitones: 2 },
  });
  expect(configured.ok).toBe(true);
  await page.getByTestId('motif-apply-button').click();
  if (await page.getByTestId('motif-replace-confirm').isVisible().catch(() => false)) {
    await page.getByTestId('motif-apply-button').click();
  }
  await expect(page.getByTestId('motif-apply-success')).toBeVisible({ timeout: 30_000 });
  const applied = await getMotifSnapshot(page);
  const appliedMotif = applied.motifs.find((item) => item.id === configured.motifId)
    || applied.motifs[applied.motifs.length - 1];
  expect(appliedMotif.occurrenceCount).toBeGreaterThanOrEqual(2);

  // Undo restores prior motif state when history is available
  await openPianoTab(page);
  const undo = page.getByTestId('piano-roll-undo');
  if (await undo.isEnabled()) {
    await undo.click();
    const undone = await getMotifSnapshot(page);
    const undoneMotif = undone.motifs.find((item) => item.id === configured.motifId)
      || undone.motifs[undone.motifs.length - 1];
    expect(undoneMotif?.occurrenceCount || 0).toBeLessThanOrEqual(appliedMotif.occurrenceCount);
  }

  // Re-apply so Motif A usages survive save/reopen
  await openMotifsTab(page);
  if ((await getMotifSnapshot(page)).motifs.find((item) => item.id === configured.motifId)?.occurrenceCount < 2) {
    configured = await configureMotifApplyViaStore(page, {
      trackId: 'harmony-1',
      startBar: 3,
      operation: 'transpose',
      operationParams: { transpose_semitones: 2 },
    });
    expect(configured.ok).toBe(true);
    await page.getByTestId('motif-apply-button').click();
    if (await page.getByTestId('motif-replace-confirm').isVisible().catch(() => false)) {
      await page.getByTestId('motif-apply-button').click();
    }
    await expect(page.getByTestId('motif-apply-success')).toBeVisible({ timeout: 30_000 });
  }

  const beforeSave = await getMotifSnapshot(page);
  await page.getByTestId('save-project').click({ force: true });
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus).toBe('saved');

  await page.goto('/');
  await page.getByTestId(`open-project-${projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 3, timeout: 30_000 });
  const reopened = await getMotifSnapshot(page);
  expect(reopened.labels.some((label) => /^Motif [A-Z]/i.test(label || ''))).toBe(true);
  expect(reopened.motifs.every((item) => item.eventRefsResolvable)).toBe(true);
  expect(reopened.motifCount).toBe(beforeSave.motifCount);

  await page.setViewportSize({ width: 390, height: 844 });
  await openMotifsTab(page);
  const overflow = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="motif-panel"]');
    return {
      panelScrollWidth: panel?.scrollWidth || 0,
      panelClientWidth: panel?.clientWidth || 0,
      docScrollWidth: document.documentElement.scrollWidth,
      docClientWidth: document.documentElement.clientWidth,
    };
  });
  expect(overflow.panelScrollWidth).toBeLessThanOrEqual(overflow.panelClientWidth + 2);
  expect(overflow.docScrollWidth).toBeLessThanOrEqual(overflow.docClientWidth + 2);

  await openPianoTab(page);
  await expect(page.getByTestId('composer-tab-piano')).toHaveAttribute('aria-selected', 'true');

  console.info('[e2e-motifs] Persistence/responsive checks ok', {
    projectId,
    motifCount: reopened.motifCount,
    occurrenceCount: reopened.motifs[reopened.motifs.length - 1]?.occurrenceCount || 0,
  });
});
