import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  getNoteSequencesFromStore,
  getStoreSnapshot,
  loadRepoFixture,
  openHarmonyTab,
  seedV1ProjectViaApi,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

async function getHarmonySnapshot(page) {
  return page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    if (!state) {
      return null;
    }
    const composition = state.editedMusicJson;
    const melody = composition?.tracks?.find((track) => track.role === 'melody' || track.id === 'melody-1');
    return {
      saveStatus: state.saveStatus,
      compositionRevision: state.compositionRevision,
      reharmonizeStatus: state.reharmonizeStatus,
      reharmonizeError: state.reharmonizeError || '',
      harmonySelectionStartBar: state.harmonySelectionStartBar,
      harmonySelectionEndBar: state.harmonySelectionEndBar,
      targetTrackIds: state.reharmonizeTargetTrackIds,
      operation: state.reharmonizeOperation,
      contentPolicy: state.reharmonizeContentPolicy,
      harmonySpanCount: Array.isArray(composition?.harmony) ? composition.harmony.length : 0,
      melodyEventFingerprints: (melody?.events || []).map((event) => ([
        event.id ?? null,
        event.pitch,
        event.start_tick,
        event.duration_ticks,
        event.velocity,
      ].join('|'))),
      bassPitchesInRange: (composition?.tracks?.find((track) => track.id === 'bass-1')?.events || [])
        .filter((event) => event.start_tick >= 15360 && event.start_tick < 23040)
        .map((event) => event.pitch),
      harmonyChordsInRange: (composition?.harmony || [])
        .filter((span) => span.start_tick < 23040 && (span.start_tick + span.duration_ticks) > 15360)
        .map((span) => span.chord),
    };
  });
}

test('Harmony workflow: bars 9-12 increase_tension preview is non-mutating then apply/undo', async ({
  page,
  request,
}) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Select bars 9-12, preview increased tension with melody preserved, apply once, undo',
  });

  const v1Fixture = loadRepoFixture('backend/tests/fixtures/composition_v1_16bar_multitrack.json');
  const seeded = await seedV1ProjectViaApi(request, {
    name: 'Harmony Reharmonize Acceptance',
    fixture: v1Fixture,
  });

  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });

  const opened = await getStoreSnapshot(page);
  assertSchemaV2(opened);
  expect(opened.barCount).toBeGreaterThanOrEqual(12);

  await openHarmonyTab(page);
  await expect(page.getByTestId('harmony-timeline')).toBeVisible();

  await page.getByTestId('harmony-select-9-12').click();
  await expect(page.getByTestId('harmony-bar-start')).toHaveValue('9');
  await expect(page.getByTestId('harmony-bar-end')).toHaveValue('12');

  await page.evaluate(() => {
    const store = window.__MUKIT_MUSIC_STORE__.getState();
    store.setHarmonySelection(9, 12);
    store.setReharmonizeControls({
      operation: 'increase_tension',
      contentPolicy: 'preserve_melody_adapt_harmony',
      engine: 'deterministic',
      targetTrackIds: ['bass-1', 'harmony-1'],
      instruction: 'make the harmony more tense while keeping the melody',
    });
  });

  const beforePreview = await getHarmonySnapshot(page);
  expect(beforePreview.harmonySelectionStartBar).toBe(9);
  expect(beforePreview.harmonySelectionEndBar).toBe(12);
  expect(beforePreview.targetTrackIds).toEqual(['bass-1', 'harmony-1']);
  const sequencesBefore = await getNoteSequencesFromStore(page);
  const revisionBefore = beforePreview.compositionRevision;
  const saveBefore = beforePreview.saveStatus;

  await page.getByTestId('harmony-preview-btn').click();
  await expect.poll(async () => (await getHarmonySnapshot(page))?.reharmonizeStatus, {
    timeout: 60_000,
  }).toBe('ready');

  const afterPreview = await getHarmonySnapshot(page);
  expect(afterPreview.saveStatus).toBe(saveBefore);
  expect(afterPreview.compositionRevision).toBe(revisionBefore);
  expect(afterPreview.melodyEventFingerprints).toEqual(beforePreview.melodyEventFingerprints);
  expect(await getNoteSequencesFromStore(page)).toEqual(sequencesBefore);
  await expect(page.getByTestId('harmony-preview-status')).toContainText(/ready/i);

  const applyBtn = page.getByTestId('harmony-apply-btn');
  await applyBtn.click();
  await expect(applyBtn).toContainText(/Confirm apply/i);
  await applyBtn.click();
  await expect.poll(async () => (await getHarmonySnapshot(page))?.reharmonizeStatus, {
    timeout: 30_000,
  }).toBe('idle');

  const afterApply = await getHarmonySnapshot(page);
  expect(afterApply.melodyEventFingerprints).toEqual(beforePreview.melodyEventFingerprints);
  expect(afterApply.compositionRevision).not.toBe(revisionBefore);
  expect(afterApply.harmonyChordsInRange.join('|')).not.toBe(beforePreview.harmonyChordsInRange.join('|'));
  expect(afterApply.bassPitchesInRange.join('|')).not.toBe(beforePreview.bassPitchesInRange.join('|'));

  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().undoNoteEdit());
  const afterUndo = await getHarmonySnapshot(page);
  expect(afterUndo.melodyEventFingerprints).toEqual(beforePreview.melodyEventFingerprints);
  expect(afterUndo.harmonyChordsInRange).toEqual(beforePreview.harmonyChordsInRange);
  expect(afterUndo.bassPitchesInRange).toEqual(beforePreview.bassPitchesInRange);
  expect(await getNoteSequencesFromStore(page)).toEqual(sequencesBefore);
});

test('Harmony tab local replace preserves note events and supports mobile width', async ({
  page,
  request,
}) => {
  const v1Fixture = loadRepoFixture('backend/tests/fixtures/composition_v1_16bar_multitrack.json');
  const seeded = await seedV1ProjectViaApi(request, {
    name: 'Harmony Local Edit',
    fixture: v1Fixture,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });
  await openHarmonyTab(page);

  const before = await getNoteSequencesFromStore(page);
  const replaced = await page.evaluate(() => {
    const store = window.__MUKIT_MUSIC_STORE__.getState();
    return store.replaceHarmonyRange({
      start_tick: 15360,
      duration_ticks: 7680,
      spans: [
        { start_tick: 15360, duration_ticks: 3840, chord: 'E7(b9)' },
        { start_tick: 19200, duration_ticks: 3840, chord: 'A7' },
      ],
    });
  });
  expect(replaced).toBe(true);
  expect(await getNoteSequencesFromStore(page)).toEqual(before);

  const overflow = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="harmony-panel"]');
    return {
      panelScrollWidth: panel?.scrollWidth || 0,
      panelClientWidth: panel?.clientWidth || 0,
      docScrollWidth: document.documentElement.scrollWidth,
      docClientWidth: document.documentElement.clientWidth,
    };
  });
  expect(overflow.panelScrollWidth).toBeLessThanOrEqual(overflow.panelClientWidth + 2);
  expect(overflow.docScrollWidth).toBeLessThanOrEqual(overflow.docClientWidth + 2);
});
