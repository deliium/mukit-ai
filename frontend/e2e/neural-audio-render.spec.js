import { expect, test } from '@playwright/test';

import {
  backendBaseUrl,
  createProjectAndGenerateExpressive,
  getStoreSnapshot,
} from './helpers.js';

/**
 * Optional neural-audio smoke: fake engine enqueue → complete → download.
 * Requires a running stack with NEURAL_AUDIO_FAKE_MODE=1 (LLM_FAKE_MODE=1 for generate).
 * Skips cleanly when fake:neural-audio is not registered/ready.
 */
async function fakeNeuralAudioReady(request) {
  const response = await request.get(`${backendBaseUrl()}/ai/models`, {
    params: { operation: 'audio_render' },
  });
  if (!response.ok()) {
    return { ready: false, reason: `GET /ai/models → ${response.status()}` };
  }
  const body = await response.json();
  const models = Array.isArray(body?.models) ? body.models : [];
  const fake = models.find((model) => model?.id === 'fake:neural-audio');
  if (!fake) {
    return { ready: false, reason: 'fake:neural-audio not in audio_render catalog' };
  }
  if (fake.status && fake.status !== 'ready') {
    return { ready: false, reason: `fake:neural-audio status=${fake.status}` };
  }
  return { ready: true, reason: 'ok' };
}

test.describe('Neural audio render (fake)', () => {
  test('enqueue → complete → download without mutating workspace composition', async ({
    page,
    request,
  }) => {
    const probe = await fakeNeuralAudioReady(request);
    test.skip(
      !probe.ready,
      `Set NEURAL_AUDIO_FAKE_MODE=1 for this smoke (${probe.reason})`,
    );

    const snapshot = await createProjectAndGenerateExpressive(page);
    expect(snapshot?.schemaVersion).toBe('composition.v2');

    await expect(page.getByTestId('export-wav')).toContainText(/deterministic/i);
    await expect(page.getByTestId('neural-audio-render-panel')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('neural-audio-disclaimer')).toContainText(/not note-perfect|approximate/i);

    const beforeFingerprint = await page.evaluate(() => {
      const composition = window.__MUKIT_MUSIC_STORE__?.getState?.()?.editedMusicJson;
      return composition ? JSON.stringify(composition) : null;
    });
    expect(beforeFingerprint).toBeTruthy();

    const modelSelect = page.getByTestId('neural-audio-model');
    await expect
      .poll(async () => modelSelect.locator('option[value="fake:neural-audio"]').count(), {
        timeout: 30_000,
        message: 'Vite must proxy /ai to the backend with NEURAL_AUDIO_FAKE_MODE=1',
      })
      .toBe(1);
    await modelSelect.selectOption('fake:neural-audio');
    await page.getByTestId('neural-audio-adapter').selectOption('text_prompt');
    await page.getByTestId('neural-audio-instructions').fill('soft chamber reverb, warm strings');

    const enqueueResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes('/neural-audio/renders')
        && response.request().method() === 'POST',
      { timeout: 60_000 },
    );
    await page.getByTestId('neural-audio-submit').click();
    const enqueueResponse = await enqueueResponsePromise;
    const enqueueBodyText = await enqueueResponse.text();
    expect(enqueueResponse.status(), enqueueBodyText).toBeLessThan(300);
    const job = JSON.parse(enqueueBodyText);
    expect(job?.id).toBeTruthy();
    expect(job?.mutates_composition).toBe(false);
    expect(job?.fidelity_class).toBeTruthy();

    const jobItem = page.getByTestId(`neural-audio-job-${job.id}`);
    await expect(jobItem).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () => jobItem.getAttribute('data-status'), { timeout: 60_000 })
      .toBe('complete');

    const downloadButton = page.getByTestId(`neural-audio-download-${job.id}`);
    await expect(downloadButton).toHaveAttribute('data-ready', 'true');

    const downloadPromise = page.waitForEvent('download', { timeout: 30_000 });
    await downloadButton.click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.wav$/i);
    expect(await download.failure()).toBeNull();

    const afterFingerprint = await page.evaluate(() => {
      const composition = window.__MUKIT_MUSIC_STORE__?.getState?.()?.editedMusicJson;
      return composition ? JSON.stringify(composition) : null;
    });
    expect(afterFingerprint).toBe(beforeFingerprint);

    const afterSnapshot = await getStoreSnapshot(page);
    expect(afterSnapshot?.schemaVersion).toBe('composition.v2');
    expect(afterSnapshot?.eventCount).toBe(snapshot.eventCount);

    console.info('[e2e-neural-audio] Fake render smoke complete', {
      renderId: job.id,
      status: 'complete',
      modelId: job.model_id,
      fidelityClass: job.fidelity_class,
    });
  });
});
