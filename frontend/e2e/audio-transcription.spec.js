import { expect, test } from '@playwright/test';

import { createProjectAndGenerateExpressive } from './helpers.js';

/**
 * Audio transcription panel smoke — no real microphone required.
 * Asserts the panel mounts after workspace open without calling getUserMedia
 * on paint. File-upload → apply path is covered by unit tests + manual checklist
 * (requires AUDIO_FAKE_MODE or optional engines).
 */
test.describe('Audio transcription panel', () => {
  test('panel mounts without requesting microphone at startup', async ({ page }) => {
    await page.addInitScript(() => {
      const proto = typeof Navigator !== 'undefined' ? Navigator.prototype : null;
      if (proto?.mediaDevices?.getUserMedia) {
        const original = proto.mediaDevices.getUserMedia.bind(proto.mediaDevices);
        proto.mediaDevices.getUserMedia = function patchedGetUserMedia(...args) {
          window.__MUKIT_GET_USER_MEDIA_CALLED__ = true;
          return original(...args);
        };
      }
    });

    await page.goto('/');
    await expect(page.getByTestId('new-project')).toBeVisible({ timeout: 60_000 });
    let micCalled = await page.evaluate(() => Boolean(window.__MUKIT_GET_USER_MEDIA_CALLED__));
    expect(micCalled).toBe(false);

    const snapshot = await createProjectAndGenerateExpressive(page);
    expect(snapshot?.schemaVersion).toBe('composition.v2');

    await expect(page.getByTestId('audio-input-panel')).toBeVisible({ timeout: 60_000 });
    micCalled = await page.evaluate(() => Boolean(window.__MUKIT_GET_USER_MEDIA_CALLED__));
    expect(micCalled).toBe(false);

    await expect(page.getByTestId('audio-upload-button')).toBeVisible();
    await expect(page.getByTestId('audio-record-toggle')).toBeVisible();
  });
});
