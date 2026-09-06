import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createProjectAndGenerate, editMelodyNoteViaStore, getStoreSnapshot } from './helpers.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');

function compose(...args) {
  const project = process.env.COMPOSE_PROJECT_NAME || 'mukit-ai';
  execFileSync('docker', ['compose', '-p', project, ...args], {
    cwd: repoRoot,
    env: { ...process.env, LLM_FAKE_MODE: '1' },
    stdio: 'inherit',
  });
}

test.describe.configure({ mode: 'serial' });

test('V1 persistence: save, restart Compose, reopen same project notes', async ({ page }) => {
  test.skip(
    process.env.RUN_PLAYWRIGHT_DOCKER_RESTART !== '1',
    'Set RUN_PLAYWRIGHT_DOCKER_RESTART=1 to restart Compose from Playwright (destructive to local stack)',
  );

  const snapshot = await createProjectAndGenerate(page);
  expect(snapshot?.projectId).toBeTruthy();
  const projectId = snapshot.projectId;

  const editResult = await editMelodyNoteViaStore(page);
  expect(editResult.ok).toBeTruthy();
  const editedPitch = editResult.afterPitch;
  const editedEvents = (await getStoreSnapshot(page)).eventCount;

  await page.getByTestId('save-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus).toBe('saved');

  console.info('[v1-persistence] Restarting compose; projectId=%s events=%s', projectId, editedEvents);
  compose('restart');
  await expect
    .poll(
      async () => {
        try {
          const res = await fetch(process.env.PLAYWRIGHT_BACKEND_URL || 'http://127.0.0.1:8888/health');
          return res.ok;
        } catch {
          return false;
        }
      },
      { timeout: 120_000 },
    )
    .toBe(true);

  await page.goto('/');
  await page.getByTestId(`open-project-${projectId}`).click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.eventCount, { timeout: 30_000 }).toBe(editedEvents);
  const reopened = await getStoreSnapshot(page);
  expect(reopened.projectId).toBe(projectId);
  expect(reopened.eventCount).toBe(editedEvents);
  const melody = await page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson;
    return composition?.tracks?.find((track) => track.id === 'melody-1')?.events?.[0]?.pitch ?? null;
  });
  expect(melody).toBe(editedPitch);
});
