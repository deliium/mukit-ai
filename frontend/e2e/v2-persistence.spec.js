import { expect, test } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  assertSchemaV2,
  createProjectAndGenerateExpressive,
  editArticulationViaStore,
  getExpressiveMetadataFromStore,
  getStoreSnapshot,
} from './helpers.js';

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

test('V2 persistence: expressive metadata survives save, restart, and reopen', async ({ page }) => {
  test.skip(
    process.env.RUN_PLAYWRIGHT_DOCKER_RESTART !== '1',
    'Set RUN_PLAYWRIGHT_DOCKER_RESTART=1 to restart Compose from Playwright (destructive to local stack)',
  );

  const snapshot = await createProjectAndGenerateExpressive(page);
  assertSchemaV2(snapshot);
  expect(snapshot?.projectId).toBeTruthy();
  const projectId = snapshot.projectId;

  const beforeExpressive = await getExpressiveMetadataFromStore(page);
  expect(beforeExpressive?.schemaVersion).toBe('composition.v2');

  const articulationEdit = await editArticulationViaStore(page, 'tenuto');
  expect(articulationEdit.ok).toBeTruthy();
  expect(articulationEdit.articulations).toContain('tenuto');

  const editedEvents = (await getStoreSnapshot(page)).eventCount;
  const editedPitch = await page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson;
    return composition?.tracks?.find((track) => track.id === 'melody-1')?.events?.[0]?.pitch ?? null;
  });

  await page.getByTestId('save-project').click();
  await expect.poll(async () => (await getStoreSnapshot(page))?.saveStatus).toBe('saved');

  console.info('[v2-persistence] Restarting compose; projectId=%s events=%s', projectId, editedEvents);
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
  assertSchemaV2(reopened);
  expect(reopened.projectId).toBe(projectId);
  expect(reopened.eventCount).toBe(editedEvents);

  const melodyPitch = await page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson;
    return composition?.tracks?.find((track) => track.id === 'melody-1')?.events?.[0]?.pitch ?? null;
  });
  expect(melodyPitch).toBe(editedPitch);

  const afterExpressive = await getExpressiveMetadataFromStore(page);
  expect(afterExpressive?.schemaVersion).toBe('composition.v2');
  expect(afterExpressive?.firstNoteArticulations).toContain('tenuto');
  if (beforeExpressive?.tempoChangeCount > 0) {
    expect(afterExpressive?.tempoChangeCount).toBe(beforeExpressive.tempoChangeCount);
  }
});
