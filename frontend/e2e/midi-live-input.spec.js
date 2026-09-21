import { expect, test } from '@playwright/test';

import { createProjectAndGenerateExpressive } from './helpers.js';

/**
 * Live MIDI input smoke — no real Web MIDI required.
 * Uses the Test input (QWERTY) path and asserts the panel mounts without
 * requesting MIDI access on home paint or after opening a workspace.
 */
test.describe('MIDI live input panel', () => {
  test('panel mounts and does not require Web MIDI at startup', async ({ page }) => {
    await page.addInitScript(() => {
      const proto = typeof Navigator !== 'undefined' ? Navigator.prototype : null;
      if (proto && typeof proto.requestMIDIAccess === 'function') {
        const original = proto.requestMIDIAccess;
        proto.requestMIDIAccess = function patchedRequestMIDIAccess(...args) {
          window.__MUKIT_MIDI_ACCESS_CALLED__ = true;
          return original.apply(this, args);
        };
      }
    });

    await page.goto('/');
    await expect(page.getByTestId('new-project')).toBeVisible({ timeout: 60_000 });
    let requestMidiCalled = await page.evaluate(() => Boolean(window.__MUKIT_MIDI_ACCESS_CALLED__));
    expect(requestMidiCalled).toBe(false);

    const snapshot = await createProjectAndGenerateExpressive(page);
    expect(snapshot?.schemaVersion).toBe('composition.v2');

    await expect(page.getByTestId('midi-input-panel')).toBeVisible({ timeout: 60_000 });
    requestMidiCalled = await page.evaluate(() => Boolean(window.__MUKIT_MIDI_ACCESS_CALLED__));
    expect(requestMidiCalled).toBe(false);

    await page.getByLabel('Test input (QWERTY)').check();
    await expect(page.getByTestId('midi-record-button')).toBeVisible();
  });
});
