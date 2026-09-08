import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  getStoreSnapshot,
  waitForAiEditSuccess,
  waitForCompositionNotes,
} from './helpers.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const MIDI_FIXTURE = path.resolve(
  __dirname,
  '../../backend/tests/fixtures/import/multitrack.mid',
);
const MUSICXML_FIXTURE = path.resolve(
  __dirname,
  '../../backend/tests/fixtures/import/multipart.musicxml',
);
const TRUNCATED_MIDI = path.resolve(
  __dirname,
  '../../backend/tests/fixtures/import/truncated.mid',
);

test.describe.configure({ mode: 'serial' });

function compositionFingerprint(composition) {
  return JSON.stringify({
    tracks: (composition?.tracks || []).map((track) => ({
      id: track.id,
      events: (track.events || []).map((event) => ({
        id: event.id,
        pitch: event.pitch,
        start_tick: event.start_tick,
        duration_ticks: event.duration_ticks,
      })),
    })),
  });
}

test('import user journey: MIDI import → play → notation → failed replace leaves music → AI edit → save → export', async ({
  page,
}) => {
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.info('[browser console error]', msg.text());
    }
  });

  await page.goto('/');
  await expect(page.getByTestId('new-project')).toBeVisible();

  // Project-browser import creates a project only after parse succeeds.
  await page.getByTestId('import-midi-input').first().setInputFiles(MIDI_FIXTURE);
  await expect.poll(async () => (await getStoreSnapshot(page))?.importStatus, { timeout: 60_000 }).toBe('success');
  await expect.poll(async () => (await getStoreSnapshot(page))?.currentProjectId, { timeout: 30_000 }).toBeTruthy();

  const imported = await getStoreSnapshot(page);
  assertSchemaV2(imported);
  expect(imported.eventCount).toBeGreaterThan(0);
  expect(imported.generationMeta).toBeNull();
  expect(imported.importReport?.summary?.detected_format).toBe('midi');
  await expect(page.getByTestId('import-report-summary').first()).toBeVisible();

  await page.getByTestId('playback-play').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 20_000 }).toMatch(
    /playing|loading|idle/,
  );

  await page.getByTestId('composer-tab-notation').click();
  await expect.poll(async () => page.getByTestId('notation-viewer').locator('svg').count(), {
    timeout: 60_000,
  }).toBeGreaterThan(0);

  await page.getByTestId('composer-tab-piano').click();
  await expect(page.getByTestId('piano-roll-grid')).toBeVisible();

  const fingerBefore = await page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__.getState().editedMusicJson;
    return JSON.stringify({
      tracks: composition.tracks.map((track) => ({
        id: track.id,
        events: track.events.map((event) => ({
          id: event.id,
          pitch: event.pitch,
          start_tick: event.start_tick,
          duration_ticks: event.duration_ticks,
        })),
      })),
    });
  });

  // Composer side-panel import: confirm replace, then truncated file fails and leaves music intact.
  await page.getByTestId('import-midi-input').last().setInputFiles(TRUNCATED_MIDI);
  await expect(page.getByTestId('import-replace-confirm')).toBeVisible();
  await page.getByRole('button', { name: 'Replace composition' }).click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.importStatus, { timeout: 30_000 }).toBe('error');
  const afterFail = await page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__.getState().editedMusicJson;
    return JSON.stringify({
      tracks: composition.tracks.map((track) => ({
        id: track.id,
        events: track.events.map((event) => ({
          id: event.id,
          pitch: event.pitch,
          start_tick: event.start_tick,
          duration_ticks: event.duration_ticks,
        })),
      })),
    });
  });
  expect(afterFail).toBe(fingerBefore);

  await page.getByTestId('ai-edit-start-bar').fill('1');
  await page.getByTestId('ai-edit-end-bar').fill('1');
  await page.getByTestId('ai-edit-instruction').fill('slightly vary the piano rhythm');
  await page.getByTestId('ai-edit-submit').click();
  await waitForAiEditSuccess(page, { timeout: 60_000 });
  await waitForCompositionNotes(page, { minEvents: 1, timeout: 15_000 });

  await page.getByTestId('save-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus, { timeout: 15_000 }).toBe('saved');

  const musicxmlDownload = page.waitForEvent('download', { timeout: 60_000 });
  await page.getByTestId('export-musicxml').click();
  expect(await (await musicxmlDownload).failure()).toBeNull();

  const midiDownload = page.waitForEvent('download', { timeout: 60_000 });
  await page.getByTestId('export-midi').click();
  expect(await (await midiDownload).failure()).toBeNull();
});

test('MusicXML import works without LLM provider', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('new-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.currentProjectId, { timeout: 30_000 }).toBeTruthy();

  await page.getByTestId('import-musicxml-input').last().setInputFiles(MUSICXML_FIXTURE);
  await expect.poll(async () => (await getStoreSnapshot(page))?.importStatus, { timeout: 60_000 }).toBe('success');
  const snap = await getStoreSnapshot(page);
  assertSchemaV2(snap);
  expect(snap.generationMeta).toBeNull();
  expect(snap.importReport?.summary?.detected_format).toMatch(/musicxml|mxl/);
  expect(compositionFingerprint(snap.editedMusicJson).length).toBeGreaterThan(10);
});
