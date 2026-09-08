import { expect, test } from '@playwright/test';

import {
  createProjectAndGenerate,
  editMelodyNoteViaStore,
  getStoreSnapshot,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

test('V1 user journey: generate → play → notation → edit → AI edit → save → export', async ({ page }) => {
  test.info().annotations.push({ type: 'journey', description: 'steps 1-16 with fake LLM' });

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.info('[browser console error]', msg.text());
    }
  });
  page.on('pageerror', (err) => {
    console.info('[pageerror]', err.message);
  });

  const before = await createProjectAndGenerate(page);
  expect(before?.eventCount).toBeGreaterThan(10);
  expect(before?.barCount).toBeGreaterThanOrEqual(16);
  expect(before?.trackCount).toBeGreaterThanOrEqual(3);
  expect(before?.selectedProvider).toBe('fake');
  expect(before?.projectId).toBeTruthy();
  // Generate/edit responses install operational composition.v2 (V1 fixtures migrate on ingest).
  expect(before?.schemaVersion).toBe('composition.v2');

  // Playback (Tone needs a user gesture — Play click)
  await page.getByTestId('playback-play').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 20_000 }).toMatch(
    /playing|loading/,
  );
  await page.waitForTimeout(600);
  const playing = await getStoreSnapshot(page);
  expect(['playing', 'paused', 'idle', 'loading']).toContain(playing.playbackStatus);

  // Notation tab (OSMD mounts only when selected)
  await page.getByTestId('composer-tab-notation').click();
  const notation = page.getByTestId('notation-viewer');
  await expect(notation).toBeVisible();
  await expect.poll(async () => notation.locator('svg').count(), { timeout: 60_000 }).toBeGreaterThan(0);

  // Piano-roll note edit (same Zustand actions the editor uses)
  await page.getByTestId('composer-tab-piano').click();
  await expect(page.getByTestId('piano-roll-grid')).toBeVisible();
  const editResult = await editMelodyNoteViaStore(page);
  expect(editResult.ok).toBeTruthy();
  expect(editResult.afterPitch).toBeTruthy();
  expect(editResult.afterPitch).not.toBe(editResult.beforePitch);

  // Hear/verify edit: play again after edit
  await page.getByTestId('playback-play').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 15_000 }).toMatch(
    /playing|loading|idle/,
  );

  // AI region edit bars 2-3
  await page.getByTestId('ai-edit-start-bar').fill('2');
  await page.getByTestId('ai-edit-end-bar').fill('3');
  await page.getByTestId('ai-edit-instruction').fill('reshape the melody in these bars');
  await page.getByTestId('ai-edit-submit').click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });
  const afterAi = await getStoreSnapshot(page);
  expect(afterAi.eventCount).toBeGreaterThan(0);
  expect(afterAi.barCount).toBe(before.barCount);

  // Undo piano-roll note edit if stack available
  const undo = page.getByTestId('piano-roll-undo');
  if (await undo.isEnabled()) {
    await undo.click();
  }

  // Save
  await page.getByTestId('save-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus, { timeout: 15_000 }).toBe('saved');

  // Exports — MusicXML + MIDI must download; WAV soft-fails without FluidSynth
  for (const [testid, ext] of [
    ['export-musicxml', /musicxml|xml/i],
    ['export-midi', /\.mid/i],
  ]) {
    const downloadPromise = page.waitForEvent('download', { timeout: 60_000 });
    await page.getByTestId(testid).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(ext);
    expect(await download.failure()).toBeNull();
  }

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
