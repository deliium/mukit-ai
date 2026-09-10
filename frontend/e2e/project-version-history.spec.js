import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  backendBaseUrl,
  getStoreSnapshot,
  openComposerTab,
  openDevelopTab,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

function buildChorusVaryFixture() {
  const eventsMelody = [];
  const eventsBass = [];
  for (let bar = 0; bar < 16; bar += 1) {
    const start = bar * 1920;
    eventsMelody.push({
      id: `m-${bar}`,
      type: 'note',
      pitch: bar % 2 === 0 ? 'C4' : 'E4',
      start_tick: start,
      duration_ticks: 480,
      velocity: 80,
    });
    eventsBass.push({
      id: `b-${bar}`,
      type: 'note',
      pitch: 'C2',
      start_tick: start,
      duration_ticks: 1920,
      velocity: 70,
    });
  }
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 16,
    duration_ticks: 16 * 1920,
    sections: [
      {
        id: 'verse',
        type: 'verse',
        start_bar: 1,
        bar_count: 12,
        start_tick: 0,
        duration_ticks: 12 * 1920,
      },
      {
        id: 'chorus',
        type: 'chorus',
        start_bar: 13,
        bar_count: 4,
        start_tick: 12 * 1920,
        duration_ticks: 4 * 1920,
      },
    ],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: eventsMelody,
      },
      {
        id: 'bass-1',
        name: 'Bass',
        instrument: 'bass',
        role: 'bass',
        midi_program: 32,
        channel: 2,
        events: eventsBass,
      },
    ],
    harmony: [],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
  };
}

async function seedChorusVaryProject(request, name) {
  const backendUrl = backendBaseUrl();
  const composition = buildChorusVaryFixture();
  const create = await request.post(`${backendUrl}/projects`, {
    data: { name, composition },
  });
  if (!create.ok()) {
    throw new Error(`Failed to create project: ${create.status()} ${await create.text()}`);
  }
  const body = await create.json();
  return { projectId: body.id, composition };
}

async function openVersionsTab(page) {
  await page.getByTestId('open-versions').click();
  await page.getByTestId('project-versions-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-versions] Opened Versions tab');
}

async function getVersionHistorySnapshot(page) {
  return page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    if (!state) {
      return null;
    }
    const composition = state.editedMusicJson;
    const chorusNotes = (composition?.tracks || []).flatMap((track) =>
      (track.events || [])
        .filter((event) => Number(event.start_tick) >= 12 * 1920 && Number(event.start_tick) < 16 * 1920)
        .map((event) => [track.id, event.id ?? null, event.pitch, event.start_tick, event.duration_ticks].join('|')),
    );
    return {
      projectId: state.currentProjectId,
      saveStatus: state.saveStatus,
      compositionRevision: state.compositionRevision,
      barCount: composition?.bar_count ?? null,
      activeBranchId: state.activeBranchId,
      activeBranchName: state.activeBranchName,
      currentRevisionId: state.currentRevisionId,
      workingVersion: state.workingVersion,
      workingFingerprint: state.workingFingerprint,
      developmentStatus: state.developmentStatus,
      candidateCount: Array.isArray(state.developmentCandidates) ? state.developmentCandidates.length : 0,
      selectedCandidateId: state.developmentSelectedCandidateId,
      auditionActive: state.developmentAuditionActive,
      undoDepth: Array.isArray(state.compositionEditUndoStack) ? state.compositionEditUndoStack.length : 0,
      redoDepth: Array.isArray(state.compositionEditRedoStack) ? state.compositionEditRedoStack.length : 0,
      versionRevisionsCount: Array.isArray(state.versionRevisions) ? state.versionRevisions.length : 0,
      versionBranches: (state.versionBranches || []).map((branch) => ({
        id: branch.id,
        name: branch.name,
        is_active: Boolean(branch.is_active),
      })),
      chorusNotes,
    };
  });
}

test('Version history: three chorus variations, apply-as-branch, restore original', async ({
  page,
  request,
}) => {
  test.info().annotations.push({
    type: 'journey',
    description:
      'Preview three chorus variations, reject one, apply another as Darker harmony, restore Original after reload',
  });

  const seeded = await seedChorusVaryProject(request, 'Version History Three Choruses');

  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });

  const opened = await getStoreSnapshot(page);
  assertSchemaV2(opened);
  expect(opened.barCount).toBe(16);

  await openVersionsTab(page);
  await expect(page.getByTestId('project-versions-panel')).toBeVisible();
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.activeBranchName).toBe('Original');
  const originalSnapshot = await getVersionHistorySnapshot(page);
  expect(originalSnapshot.currentRevisionId).toBeTruthy();
  const originalChorus = originalSnapshot.chorusNotes;
  const originalRevisionId = originalSnapshot.currentRevisionId;

  await openDevelopTab(page);
  await page.getByTestId('develop-operation').selectOption('vary_section');
  await page.getByTestId('develop-source-start').fill('13');
  await page.getByTestId('develop-source-end').fill('16');
  await page.getByTestId('develop-candidate-count').selectOption('3');
  await page.getByTestId('develop-strength').selectOption('balanced');
  await page.getByTestId('develop-intent').selectOption('develop');
  await page.getByTestId('develop-instruction').fill('darker harmony chorus');

  const beforePreview = await getVersionHistorySnapshot(page);
  await page.getByTestId('develop-generate').click();
  await expect(page.getByTestId('develop-status')).toContainText(/candidate\(s\) ready/i, {
    timeout: 120_000,
  });

  const afterPreview = await getVersionHistorySnapshot(page);
  expect(afterPreview.candidateCount).toBe(3);
  expect(afterPreview.compositionRevision).toBe(beforePreview.compositionRevision);
  expect(afterPreview.chorusNotes).toEqual(beforePreview.chorusNotes);
  expect(afterPreview.undoDepth).toBe(beforePreview.undoDepth);
  expect(afterPreview.activeBranchName).toBe('Original');

  await page.getByTestId('develop-candidate-0').click();
  await page.getByTestId('develop-compare').click();
  await expect(page.getByTestId('develop-compare-summary')).toBeVisible();

  await page.getByTestId('develop-audition').click();
  await expect(page.getByTestId('develop-audition-active')).toBeVisible();
  expect((await getVersionHistorySnapshot(page)).chorusNotes).toEqual(originalChorus);

  await page.getByTestId('develop-candidate-2').click();
  await page.getByTestId('develop-reject').click();
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.candidateCount).toBe(2);

  await page.getByTestId('develop-candidate-1').click();
  await page.getByTestId('develop-branch-name').fill('Darker harmony');
  await expect(page.getByTestId('develop-apply-as-branch')).toBeEnabled();
  await page.getByTestId('develop-apply-as-branch').click();
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.developmentStatus || 'idle', {
    timeout: 15_000,
  }).not.toBe('error');

  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.activeBranchName, {
    timeout: 60_000,
  }).toBe('Darker harmony');
  const afterBranch = await getVersionHistorySnapshot(page);
  expect(afterBranch.candidateCount).toBe(0);
  // Apply-as-branch activates a new branch (context switch) and clears session undo/redo.
  expect(afterBranch.undoDepth).toBe(0);
  expect(afterBranch.chorusNotes).not.toEqual(originalChorus);

  await openVersionsTab(page);
  const darkerBranches = await getVersionHistorySnapshot(page);
  const originalBranch = (darkerBranches.versionBranches || []).find((branch) => branch.name === 'Original');
  expect(originalBranch?.id).toBeTruthy();
  await page.getByTestId('version-branch-select').selectOption(originalBranch.id);
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.activeBranchName).toBe('Original');
  const backOnOriginal = await getVersionHistorySnapshot(page);
  expect(backOnOriginal.chorusNotes).toEqual(originalChorus);

  await page.reload();
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });
  await openVersionsTab(page);
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.activeBranchName).toBe('Original');
  expect((await getVersionHistorySnapshot(page)).chorusNotes).toEqual(originalChorus);

  const afterReload = await getVersionHistorySnapshot(page);
  const darkerBranch = (afterReload.versionBranches || []).find((branch) => branch.name === 'Darker harmony');
  expect(darkerBranch?.id).toBeTruthy();
  await page.getByTestId('version-branch-select').selectOption(darkerBranch.id);
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.activeBranchName).toBe('Darker harmony');
  const darkerAfterReload = await getVersionHistorySnapshot(page);
  expect(darkerAfterReload.chorusNotes).not.toEqual(originalChorus);

  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.versionRevisionsCount || 0, {
    timeout: 30_000,
  }).toBeGreaterThan(1);

  const restoreTargetId = await page.evaluate((headId) => {
    const revisions = window.__MUKIT_MUSIC_STORE__?.getState?.()?.versionRevisions || [];
    const parent = revisions.find((revision) => revision.id !== headId);
    return parent?.id || null;
  }, darkerAfterReload.currentRevisionId);
  expect(restoreTargetId).toBeTruthy();

  await expect(page.getByTestId(`version-revision-${restoreTargetId}`)).toBeVisible({ timeout: 30_000 });
  await page.getByTestId(`version-revision-${restoreTargetId}`).click();
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByTestId('version-restore').click();
  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.chorusNotes).toEqual(originalChorus);
  const restored = await getVersionHistorySnapshot(page);
  expect(restored.activeBranchName).toBe('Darker harmony');
  // Restore installs one undo entry on the active branch.
  expect(restored.undoDepth).toBeGreaterThan(0);

  await expect.poll(async () => (await getVersionHistorySnapshot(page))?.versionRevisionsCount || 0).toBeGreaterThan(0);
  const namedRevisionId = await page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    return state?.versionRevisions?.[0]?.id || state?.currentRevisionId || null;
  });
  expect(namedRevisionId).toBeTruthy();
  await page.getByTestId(`version-revision-${namedRevisionId}`).click();
  await page.getByLabel('Revision name').fill('Keep original chorus');
  await page.getByRole('button', { name: 'Name version' }).click();
  await expect(page.getByText('Keep original chorus')).toBeVisible({ timeout: 15_000 });
});
test('Version history keyboard focus and 390px layout', async ({ page, request }) => {
  const seeded = await seedChorusVaryProject(request, 'Versions Mobile Layout');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 10, timeout: 60_000 });

  await openComposerTab(page, 'versions');
  await expect(page.getByTestId('project-versions-panel')).toBeVisible();
  await page.getByTestId('version-branch-select').focus();
  await expect(page.getByTestId('version-branch-select')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('project-versions-panel')).toBeVisible();
});
