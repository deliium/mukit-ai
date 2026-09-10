import { expect, test } from '@playwright/test';

import {
  createProjectAndGenerateExpressive,
  getStoreSnapshot,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

test('playback mixer: play, mute/solo/trim/pan/send, collapse, non-persistence', async ({ page }) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Expressive V2 mixer controls stay ephemeral and accessible',
  });

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      // Keep payloads out of CI logs; surface message text only.
      console.info('[browser console error]', msg.text().slice(0, 240));
    }
  });

  const before = await createProjectAndGenerateExpressive(page);
  expect(before?.schemaVersion).toBe('composition.v2');
  expect(before?.trackCount).toBeGreaterThanOrEqual(3);

  const mixer = page.getByTestId('playback-mixer');
  await expect(mixer).toBeVisible();
  await expect(page.getByTestId('playback-mixer-collapse')).toBeVisible();

  const firstTrackId = await page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    return state?.editedMusicJson?.tracks?.[0]?.id || null;
  });
  expect(firstTrackId).toBeTruthy();

  const mute = page.getByTestId(`playback-mixer-mute-${firstTrackId}`);
  const solo = page.getByTestId(`playback-mixer-solo-${firstTrackId}`);
  const trim = page.getByTestId(`playback-mixer-trim-${firstTrackId}`);
  const pan = page.getByTestId(`playback-mixer-pan-${firstTrackId}`);
  const send = page.getByTestId(`playback-mixer-send-${firstTrackId}`);

  await expect(mute).toHaveAttribute('aria-pressed', 'false');
  await mute.click();
  await expect(mute).toHaveAttribute('aria-pressed', 'true');
  await solo.click();
  await expect(solo).toHaveAttribute('aria-pressed', 'true');

  await trim.fill('3');
  await pan.fill('0.25');
  await send.fill('0.35');

  const mixerState = await page.evaluate((trackId) => {
    const state = window.__MUKIT_MUSIC_STORE__.getState();
    const control = state.trackControls[trackId];
    const trackVolume = state.editedMusicJson.tracks.find((track) => track.id === trackId)?.volume;
    return {
      muted: control?.muted,
      solo: control?.solo,
      trimDb: control?.trimDb,
      panOffset: control?.panOffset,
      reverbSend: control?.reverbSend,
      trackVolume,
      undoLen: state.compositionEditUndoStack.length,
      revision: state.compositionRevision,
    };
  }, firstTrackId);

  expect(mixerState.muted).toBe(true);
  expect(mixerState.solo).toBe(true);
  expect(mixerState.trimDb).toBeCloseTo(3, 1);
  expect(mixerState.panOffset).toBeCloseTo(0.25, 2);
  expect(mixerState.reverbSend).toBeCloseTo(0.35, 2);
  expect(mixerState.trackVolume).toBe(100);

  await page.getByTestId('playback-play').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 20_000 }).toMatch(
    /playing|loading/,
  );
  await page.waitForTimeout(500);
  await expect(page.getByTestId(`playback-mixer-level-${firstTrackId}`)).toBeVisible();
  await expect(page.getByTestId(`playback-mixer-profile-${firstTrackId}`)).toBeVisible();

  await page.getByTestId('playback-from-cursor').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 15_000 }).toMatch(
    /playing|loading|idle/,
  );

  await page.getByTestId('playback-mixer-collapse').click();
  await expect(page.getByTestId(`playback-mixer-mute-${firstTrackId}`)).toHaveCount(0);
  await page.getByTestId('playback-mixer-collapse').click();
  await expect(mute).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(mixer).toBeVisible();
  const overflowX = await mixer.evaluate((el) => el.scrollWidth > el.clientWidth + 2);
  expect(overflowX).toBe(false);

  await page.getByTestId('save-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus, { timeout: 15_000 }).toBe('saved');

  // Mixer session fields must not rewrite canonical track volume.
  const afterSave = await page.evaluate((trackId) => {
    const state = window.__MUKIT_MUSIC_STORE__.getState();
    const track = state.editedMusicJson.tracks.find((item) => item.id === trackId);
    return {
      volume: track?.volume,
      muted: state.trackControls[trackId]?.muted,
      revision: state.compositionRevision,
    };
  }, firstTrackId);
  expect(afterSave.volume).toBe(mixerState.trackVolume);
  expect(afterSave.muted).toBe(true);

  await page.getByTestId('playback-stop').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.playbackStatus, { timeout: 10_000 }).toBe('idle');
});
