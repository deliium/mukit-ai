import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';

import {
  backendBaseUrl,
  createProjectAndGenerateExpressive,
  openTransportTab,
} from './helpers.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');
const FIXTURE_WAV = path.join(
  repoRoot,
  'backend/tests/fixtures/audio/recovery/mixed_melody_bass.wav',
);

/**
 * Probe whether the backend will run audio recovery in fake mode.
 * Skips cleanly when AUDIO_RECOVERY_FAKE_MODE is off or the fixture is missing.
 */
async function audioRecoveryFakeReady(request) {
  if (!fs.existsSync(FIXTURE_WAV)) {
    return { ready: false, reason: `fixture missing: ${FIXTURE_WAV}` };
  }
  const bytes = fs.readFileSync(FIXTURE_WAV);
  const response = await request.post(`${backendBaseUrl()}/audio-recovery/jobs`, {
    multipart: {
      file: {
        name: 'mixed_melody_bass.wav',
        mimeType: 'audio/wav',
        buffer: bytes,
      },
      disable_separation: 'false',
    },
  });
  if (!response.ok()) {
    const body = await response.text();
    return {
      ready: false,
      reason: `POST /audio-recovery/jobs → ${response.status()} ${body.slice(0, 180)}`,
    };
  }
  const job = await response.json();
  if (job?.status !== 'complete') {
    return {
      ready: false,
      reason: `job status=${job?.status || 'unknown'} error=${job?.error_code || 'none'}`,
    };
  }
  if (!job?.fake) {
    // Clean up non-fake probe job best-effort so we do not leave durable orphans.
    if (job?.id) {
      await request.delete(`${backendBaseUrl()}/audio-recovery/jobs/${job.id}`).catch(() => {});
    }
    return {
      ready: false,
      reason: 'Set AUDIO_RECOVERY_FAKE_MODE=1 for this smoke (job.fake=false)',
    };
  }
  if (job?.id) {
    await request.delete(`${backendBaseUrl()}/audio-recovery/jobs/${job.id}`).catch(() => {});
  }
  return { ready: true, reason: 'ok' };
}

/**
 * V4 audio recovery smoke: fixture upload → review confidence → Apply→Bind →
 * HTMLAudio + bound piano-roll overlay. Requires AUDIO_RECOVERY_FAKE_MODE=1.
 */
test.describe('Audio recovery (fake)', () => {
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

    await openTransportTab(page);
    await expect(page.getByTestId('audio-recovery-panel')).toBeVisible({ timeout: 60_000 });
    micCalled = await page.evaluate(() => Boolean(window.__MUKIT_GET_USER_MEDIA_CALLED__));
    expect(micCalled).toBe(false);

    await expect(page.getByTestId('audio-recovery-upload-button')).toBeVisible();
    await expect(page.getByTestId('audio-recovery-record-toggle')).toBeVisible();
  });

  test('upload fixture → review → Apply→Bind → overlay + HTMLAudio', async ({
    page,
    request,
  }) => {
    const probe = await audioRecoveryFakeReady(request);
    test.skip(
      !probe.ready,
      `Set AUDIO_RECOVERY_FAKE_MODE=1 for this smoke (${probe.reason})`,
    );

    const snapshot = await createProjectAndGenerateExpressive(page);
    expect(snapshot?.schemaVersion).toBe('composition.v2');

    await openTransportTab(page);
    await expect(page.getByTestId('audio-recovery-panel')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('audio-recovery-phase-status')).toContainText(/Phase:\s*idle/i);

    const enqueueResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes('/audio-recovery/jobs')
        && response.request().method() === 'POST'
        && !response.url().includes('/bind'),
      { timeout: 90_000 },
    );

    await page.getByTestId('audio-recovery-file-input').setInputFiles(FIXTURE_WAV);
    const enqueueResponse = await enqueueResponsePromise;
    const enqueueBodyText = await enqueueResponse.text();
    expect(enqueueResponse.status(), enqueueBodyText).toBeLessThan(300);
    const job = JSON.parse(enqueueBodyText);
    expect(job?.fake).toBe(true);
    expect(job?.status).toBe('complete');
    expect(job?.mutates_composition).toBe(false);
    expect(job?.preview?.notes?.length).toBeGreaterThan(0);

    await expect(page.getByTestId('audio-recovery-phase-status')).toContainText(/Phase:\s*review/i, {
      timeout: 30_000,
    });
    await expect(page.getByTestId('audio-recovery-scaffolding')).toBeVisible();
    await expect(page.getByTestId('audio-recovery-tempo-badge')).toContainText(/tempo/i);
    await expect(page.getByTestId('audio-recovery-note-list')).toBeVisible();
    await expect(page.getByTestId('audio-recovery-source-audio')).toBeVisible();

    const provisionalCount = await page.getByTestId('piano-roll-recovery-provisional-note').count();
    expect(provisionalCount).toBeGreaterThan(0);

    const noteEventsBefore = await page.evaluate(() => {
      const composition = window.__MUKIT_MUSIC_STORE__?.getState?.()?.editedMusicJson;
      return (composition?.tracks || []).reduce(
        (sum, track) => sum + (track.events || []).filter((e) => e?.type === 'note').length,
        0,
      );
    });

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
    expect(bindBody?.source_audio_asset_id).toBeTruthy();
    expect(bindBody?.result_asset_id).toBeTruthy();

    await expect(page.getByTestId('audio-recovery-phase-status')).toContainText(/Phase:\s*bound/i, {
      timeout: 30_000,
    });
    await expect(page.getByTestId('audio-recovery-source-audio')).toBeVisible();
    await expect
      .poll(async () => page.getByTestId('piano-roll-recovery-bound-note').count(), {
        timeout: 15_000,
      })
      .toBeGreaterThan(0);

    const after = await page.evaluate(() => {
      const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
      const composition = state?.editedMusicJson;
      const noteCount = (composition?.tracks || []).reduce(
        (sum, track) => sum + (track.events || []).filter((e) => e?.type === 'note').length,
        0,
      );
      const hasConfidenceOnEvents = (composition?.tracks || []).some((track) =>
        (track.events || []).some((event) => Object.prototype.hasOwnProperty.call(event || {}, 'confidence')),
      );
      return {
        noteCount,
        hasConfidenceOnEvents,
        recoveryPhase: state?.recoveryPhase,
        overlayLen: Array.isArray(state?.recoveryOverlay) ? state.recoveryOverlay.length : 0,
      };
    });
    expect(after.recoveryPhase).toBe('bound');
    expect(after.hasConfidenceOnEvents).toBe(false);
    expect(after.overlayLen).toBeGreaterThan(0);
    expect(after.noteCount).toBeGreaterThan(noteEventsBefore);

    console.info('[e2e-audio-recovery] Fake recovery smoke complete', {
      jobId: job.id,
      notesApplied: after.noteCount - noteEventsBefore,
      overlayEntries: after.overlayLen,
      sourceAssetId: bindBody.source_audio_asset_id,
    });
  });
});
