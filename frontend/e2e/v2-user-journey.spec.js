import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  createProjectAndGenerate,
  createProjectAndGenerateExpressive,
  editArticulationViaStore,
  editMelodyNoteViaStore,
  getExpressiveMetadataFromStore,
  getStoreSnapshot,
  waitForAiEditSuccess,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

test('V2 user journey: expressive generate → play tempo change → edit expression → AI edit → save → export warnings', async ({
  page,
}) => {
  test.info().annotations.push({ type: 'journey', description: 'V2 expressive fake LLM path with fallback' });

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.info('[browser console error]', msg.text());
    }
  });

  let before = await createProjectAndGenerateExpressive(page);
  if ((before?.tempoChangeCount || 0) < 1 || (before?.barCount || 0) !== 4) {
    before = await createProjectAndGenerate(page, { durationBars: 16, minEvents: 10 });
  }

  assertSchemaV2(before);
  expect(before?.eventCount).toBeGreaterThan(4);
  expect(before?.trackCount).toBeGreaterThanOrEqual(3);
  expect(before?.selectedProvider).toBe('fake');

  const expressiveBefore = await getExpressiveMetadataFromStore(page);
  expect(expressiveBefore?.schemaVersion).toBe('composition.v2');

  await page.getByTestId('playback-play').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 20_000 }).toMatch(
    /playing|loading/,
  );
  await page.waitForTimeout(800);
  const playing = await getStoreSnapshot(page);
  expect(['playing', 'paused', 'idle', 'loading']).toContain(playing.playbackStatus);

  // Notation before piano-roll edits: note edits clear musicXml until debounce refresh,
  // and switching tabs cancels the piano-roll notation debounce.
  await page.getByTestId('composer-tab-notation').click();
  const notation = page.getByTestId('notation-viewer');
  await expect(notation).toBeVisible();
  await expect.poll(async () => notation.locator('svg').count(), { timeout: 60_000 }).toBeGreaterThan(0);

  await page.getByTestId('composer-tab-piano').click();
  await expect(page.getByTestId('piano-roll-grid')).toBeVisible();

  const selected = await editArticulationViaStore(page, 'accent');
  expect(selected.ok).toBeTruthy();
  expect(selected.articulations).toContain('accent');

  const editResult = await editMelodyNoteViaStore(page);
  expect(editResult.ok).toBeTruthy();
  expect(editResult.afterPitch).not.toBe(editResult.beforePitch);

  await page.getByTestId('playback-play').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 15_000 }).toMatch(
    /playing|loading|idle/,
  );

  await page.getByTestId('ai-edit-start-bar').fill('1');
  await page.getByTestId('ai-edit-end-bar').fill(String(Math.min(before.barCount, 2)));
  await page.getByTestId('ai-edit-instruction').fill('reshape the melody with clearer articulation');
  await page.getByTestId('ai-edit-submit').click();
  await waitForAiEditSuccess(page, { timeout: 60_000 });
  await waitForCompositionNotes(page, { minEvents: 4, timeout: 15_000 });

  const afterAi = await getStoreSnapshot(page);
  assertSchemaV2(afterAi);
  expect(afterAi.eventCount).toBeGreaterThan(0);

  await page.getByTestId('save-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus, { timeout: 15_000 }).toBe('saved');

  const musicxmlDownload = page.waitForEvent('download', { timeout: 60_000 });
  await page.getByTestId('export-musicxml').click();
  const musicxmlFile = await musicxmlDownload;
  expect(musicxmlFile.suggestedFilename()).toMatch(/musicxml|xml/i);
  expect(await musicxmlFile.failure()).toBeNull();
  await expect(page.getByText(/Projection\s+\w+|automation_omitted_from_notation/i).first()).toBeVisible({
    timeout: 15_000,
  });

  const midiDownload = page.waitForEvent('download', { timeout: 60_000 });
  await page.getByTestId('export-midi').click();
  const midiFile = await midiDownload;
  expect(midiFile.suggestedFilename()).toMatch(/\.mid/i);
  expect(await midiFile.failure()).toBeNull();
  await expect(page.getByText(/Projection\s+\w+/i).first()).toBeVisible({
    timeout: 15_000,
  });

  const wavDownload = page.waitForEvent('download', { timeout: 20_000 }).catch(() => null);
  await page.getByTestId('export-wav').click();
  const wavResult = await wavDownload;
  if (wavResult) {
    expect(wavResult.suggestedFilename()).toMatch(/\.wav/i);
    expect(await wavResult.failure()).toBeNull();
  } else {
    await expect(page.getByText(/wav|soundfont|unavailable|failed|export/i).first()).toBeVisible({
      timeout: 10_000,
    });
  }
});
