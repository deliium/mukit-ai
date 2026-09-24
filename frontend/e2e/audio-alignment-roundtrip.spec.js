import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import {
  backendBaseUrl,
  createProjectAndGenerateExpressive,
  editMelodyNoteViaStore,
  getStoreSnapshot,
} from './helpers.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');
const FIXTURE_WAV = path.join(
  repoRoot,
  'backend/tests/fixtures/audio/recovery/mixed_melody_bass.wav',
);

/**
 * Round-trip AC (fake modes):
 * Bind → alignment → bar seek → reopen hydrate → edit → stale neural → new render →
 * source sha unchanged.
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

test.describe('Audio-symbolic alignment round-trip (fake)', () => {
  test('bind → seek → reopen → edit → stale → re-render; source sha stable', async ({
    page,
    request,
  }) => {
    test.skip(!fs.existsSync(FIXTURE_WAV), 'recovery fixture WAV missing');

    const probe = await request.post(`${backendBaseUrl()}/audio-recovery/jobs`, {
      multipart: {
        file: {
          name: 'mixed_melody_bass.wav',
          mimeType: 'audio/wav',
          buffer: fs.readFileSync(FIXTURE_WAV),
        },
        disable_separation: 'false',
      },
    });
    if (!probe.ok()) {
      test.skip(true, `recovery probe failed: ${probe.status()}`);
    }
    const probeJob = await probe.json();
    test.skip(!probeJob?.fake, 'Set AUDIO_RECOVERY_FAKE_MODE=1');
    if (probeJob?.id) {
      await request.delete(`${backendBaseUrl()}/audio-recovery/jobs/${probeJob.id}`).catch(() => {});
    }

    const neuralProbe = await fakeNeuralAudioReady(request);
    test.skip(
      !neuralProbe.ready,
      `Set NEURAL_AUDIO_FAKE_MODE=1 for round-trip stale AC (${neuralProbe.reason})`,
    );

    const snapshot = await createProjectAndGenerateExpressive(page);
    const projectId = snapshot?.projectId || snapshot?.currentProjectId;
    expect(projectId).toBeTruthy();

    await page.getByTestId('audio-recovery-panel').scrollIntoViewIfNeeded();
    await page.getByTestId('audio-recovery-file-input').setInputFiles(FIXTURE_WAV);
    await expect(page.getByTestId('audio-recovery-apply-bind')).toBeVisible({ timeout: 60000 });

    const bindResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes('/audio-recovery/jobs/')
        && response.url().includes('/bind')
        && response.request().method() === 'POST',
      { timeout: 60_000 },
    );
    await page.getByTestId('audio-recovery-apply-bind').click();
    const bindResponse = await bindResponsePromise;
    const bindBodyText = await bindResponse.text();
    expect(bindResponse.status(), bindBodyText).toBeLessThan(300);
    const bindBody = JSON.parse(bindBodyText);
    expect(bindBody?.alignment_asset_id).toBeTruthy();
    expect(bindBody?.source_audio_asset_id).toBeTruthy();
    const sourceShaPrefix = bindBody?.roundtrip_provenance?.source_sha256_prefix || null;
    expect(sourceShaPrefix).toBeTruthy();

    await expect(page.getByTestId('audio-recovery-source-audio')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('audio-alignment-quality')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('audio-alignment-waveform')).toBeVisible({ timeout: 15000 });

    // Bound discovery for reopen hydrate.
    const bound = await request.get(
      `${backendBaseUrl()}/audio-recovery/projects/${encodeURIComponent(projectId)}/bound`,
    );
    expect(bound.ok()).toBeTruthy();
    const discovery = await bound.json();
    expect(discovery.bound).toBe(true);
    expect(discovery.latest?.alignment_asset_id).toBeTruthy();
    expect(discovery.latest?.source_audio_asset_id).toBe(bindBody.source_audio_asset_id);
    if (discovery.latest?.source_sha256_prefix) {
      expect(discovery.latest.source_sha256_prefix).toBe(sourceShaPrefix);
    }

    // Bar seek (expressive fixture is 4 bars — use last bar as AC proxy for “bar 10”).
    const seekResult = await page.evaluate(() => {
      const store = window.__MUKIT_MUSIC_STORE__;
      if (!store?.getState) {
        return { ok: false, reason: 'store missing' };
      }
      const state = store.getState();
      const barCount = Number(state.editedMusicJson?.bar_count) || 4;
      const targetBar = Math.min(10, barCount);
      const request = state.seekSourceAudioToBar(targetBar, { reason: 'e2e-bar-seek' });
      const after = store.getState();
      return {
        ok: Boolean(request),
        targetBar,
        seekSeconds: request?.seconds ?? null,
        sourcePlayheadTick: after.sourcePlayheadTick,
        sourceSeekRequestId: after.sourceSeekRequest?.id ?? null,
        hasAlignment: Boolean(after.alignmentDocument),
      };
    });
    expect(seekResult.ok, JSON.stringify(seekResult)).toBe(true);
    expect(seekResult.hasAlignment).toBe(true);
    expect(seekResult.seekSeconds).not.toBeNull();

    await expect
      .poll(async () => page.evaluate(() => {
        const el = document.querySelector('[data-testid="audio-recovery-source-audio"]');
        return el ? Number(el.currentTime) : null;
      }), { timeout: 10_000 })
      .toBeCloseTo(Number(seekResult.seekSeconds), 1);

    // Reopen hydrate: clear session recovery state via openProject.
    await page.evaluate(async (id) => {
      const store = window.__MUKIT_MUSIC_STORE__;
      await store.getState().openProject(id);
    }, projectId);

    await expect
      .poll(async () => page.evaluate(() => {
        const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
        return Boolean(state?.alignmentDocument && state?.recoverySourceObjectUrl);
      }), { timeout: 30_000 })
      .toBe(true);
    await expect(page.getByTestId('audio-alignment-waveform')).toBeVisible({ timeout: 15000 });

    // Neural render of current composition, then edit → soft-stale, re-render, source sha stable.
    await page.getByTestId('neural-audio-render-panel').scrollIntoViewIfNeeded();
    const modelSelect = page.getByTestId('neural-audio-model');
    await expect
      .poll(async () => modelSelect.locator('option[value="fake:neural-audio"]').count(), {
        timeout: 30_000,
      })
      .toBe(1);
    await modelSelect.selectOption('fake:neural-audio');
    await page.getByTestId('neural-audio-adapter').selectOption('text_prompt');
    await page.getByTestId('neural-audio-instructions').fill('alignment round-trip fake render');

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

    const jobItem = page.getByTestId(`neural-audio-job-${job.id}`);
    await expect(jobItem).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(async () => jobItem.getAttribute('data-status'), { timeout: 60_000 })
      .toBe('complete');
    await expect(jobItem).toHaveAttribute('data-stale', 'false');

    const edit = await editMelodyNoteViaStore(page);
    expect(edit.ok, JSON.stringify(edit)).toBe(true);

    await expect(jobItem).toHaveAttribute('data-stale', 'true', { timeout: 15_000 });
    await expect(page.getByTestId(`neural-audio-job-stale-${job.id}`)).toBeVisible();
    await expect(page.getByTestId(`neural-audio-stale-banner-${job.id}`)).toBeVisible();

    const reEnqueuePromise = page.waitForResponse(
      (response) =>
        response.url().includes('/neural-audio/renders')
        && response.request().method() === 'POST',
      { timeout: 60_000 },
    );
    await page.getByTestId(`neural-audio-render-again-${job.id}`).click();
    const reEnqueueResponse = await reEnqueuePromise;
    const reEnqueueText = await reEnqueueResponse.text();
    expect(reEnqueueResponse.status(), reEnqueueText).toBeLessThan(300);
    const job2 = JSON.parse(reEnqueueText);
    expect(job2?.id).toBeTruthy();
    expect(job2.id).not.toBe(job.id);

    const job2Item = page.getByTestId(`neural-audio-job-${job2.id}`);
    await expect
      .poll(async () => job2Item.getAttribute('data-status'), { timeout: 60_000 })
      .toBe('complete');

    // Source asset sha must be unchanged after edits + new render.
    const boundAfter = await request.get(
      `${backendBaseUrl()}/audio-recovery/projects/${encodeURIComponent(projectId)}/bound`,
    );
    expect(boundAfter.ok()).toBeTruthy();
    const discoveryAfter = await boundAfter.json();
    expect(discoveryAfter.latest?.source_audio_asset_id).toBe(bindBody.source_audio_asset_id);
    if (discoveryAfter.latest?.source_sha256_prefix) {
      expect(discoveryAfter.latest.source_sha256_prefix).toBe(sourceShaPrefix);
    }

    const afterSnapshot = await getStoreSnapshot(page);
    expect(afterSnapshot?.schemaVersion).toBe('composition.v2');
  });
});
