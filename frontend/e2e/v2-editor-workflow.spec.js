import { expect, test } from '@playwright/test';

import {
  assertSchemaV2,
  enableEditorPerfOnPage,
  getEditorPerfSnapshot,
  getEditorWorkflowSnapshot,
  openPianoRollTab,
  seedLargeScoreEditorProject,
  waitForCompositionNotes,
} from './helpers.js';

/** Hardware-tolerant CI budgets (ms / node counts). Documented in docs/testing.md. */
const PERF = {
  maxRenderedNotesAtStart: 320,
  maxBoxSelectMs: 4_000,
  maxTransformMs: 4_000,
  maxNavigationMs: 3_000,
  maxDragMs: 4_000,
};

test.describe.configure({ mode: 'serial' });

function assertNoSensitiveConsolePayload(text) {
  expect(text).not.toMatch(/"events"\s*:\s*\[/);
  expect(text).not.toMatch(/clipboardNotes|editorClipboard\.notes/);
  expect(text).not.toMatch(/schema_version"\s*:\s*"composition\.v2"[\s\S]{200,}/);
}

async function openSeededPianoRoll(page, request, name) {
  const seeded = await seedLargeScoreEditorProject(request, { name });
  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: Math.min(50, seeded.noteCount), timeout: 90_000 });
  await openPianoRollTab(page);
  await enableEditorPerfOnPage(page);
  return seeded;
}

test('V2 editor workflow: select → transform → loop → play from cursor → undo/redo', async ({
  page,
  request,
}) => {
  test.info().annotations.push({
    type: 'journey',
    description: 'Direct pointer/keyboard multi-note editing on a large V2 score',
  });

  const consoleLines = [];
  page.on('console', (msg) => {
    const text = msg.text();
    consoleLines.push(text);
    assertNoSensitiveConsolePayload(text);
  });

  const seeded = await openSeededPianoRoll(page, request, 'Editor Workflow Acceptance');
  const opened = await getEditorWorkflowSnapshot(page);
  assertSchemaV2({ schemaVersion: opened.schemaVersion });
  expect(opened.barCount).toBe(100);
  expect(opened.eventCount).toBe(seeded.noteCount);
  expect(opened.renderedNoteDom).toBeLessThan(opened.eventCount);
  expect(opened.renderedNoteDom).toBeLessThanOrEqual(PERF.maxRenderedNotesAtStart);

  // Select several early notes via Ctrl+click across melody track.
  const notes = page.locator('[data-testid="piano-roll-note"][data-track-id="melody-1"]');
  await expect(notes.first()).toBeVisible();
  const noteCountVisible = await notes.count();
  expect(noteCountVisible).toBeGreaterThan(3);

  await notes.nth(0).click();
  await notes.nth(1).click({ modifiers: ['Control'] });
  await notes.nth(2).click({ modifiers: ['Control'] });
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.selectedCount).toBeGreaterThanOrEqual(3);

  // Shortcuts ignore role=button note focus — route keys through the application shell.
  await page.locator('[aria-label="Piano roll note grid"]').focus();
  const beforeCopy = await getEditorWorkflowSnapshot(page);
  await page.keyboard.press('Control+c');
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.clipboardReady).toBe(true);

  await page.getByTestId('piano-roll-duplicate').click();
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.eventCount)
    .toBeGreaterThan(beforeCopy.eventCount);

  await page.getByTestId('piano-roll-transpose-up').click();
  await page.getByTestId('piano-roll-velocity-delta-up').click();
  await page.getByTestId('piano-roll-quantize-apply').click();

  const afterTransforms = await getEditorWorkflowSnapshot(page);
  expect(afterTransforms.undoDepth).toBeGreaterThan(beforeCopy.undoDepth);

  // Loop from selection + play from cursor.
  await page.getByTestId('playback-set-loop').click();
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.playbackLoop?.enabled).toBe(true);
  const loop = (await getEditorWorkflowSnapshot(page)).playbackLoop;
  expect(loop.endTick).toBeGreaterThan(loop.startTick);

  await page.getByTestId('playback-from-cursor').click();
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.playbackStatus, {
    timeout: 20_000,
  }).toMatch(/playing|loading/);
  await page.getByTestId('playback-stop').click();
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.playbackStatus, {
    timeout: 15_000,
  }).toMatch(/idle|paused/);

  // Undo/redo the transform stack.
  const undoTarget = Math.max(1, afterTransforms.undoDepth - beforeCopy.undoDepth);
  for (let i = 0; i < undoTarget; i += 1) {
    await page.getByTestId('piano-roll-undo').click();
  }
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.eventCount)
    .toBe(beforeCopy.eventCount);

  await page.locator('[aria-label="Piano roll note grid"]').focus();
  await page.keyboard.press('Control+Shift+z');
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.eventCount)
    .toBeGreaterThan(beforeCopy.eventCount);

  // Focus guards: typing in an input must not steal selection via Delete.
  const selectedBeforeInput = (await getEditorWorkflowSnapshot(page)).selectedCount;
  await page.getByTestId('piano-roll-velocity-value').fill('96');
  await page.keyboard.press('Delete');
  expect((await getEditorWorkflowSnapshot(page)).selectedCount).toBe(selectedBeforeInput);

  // Articulation + dynamics.
  await notes.nth(0).click();
  await page.getByTestId('articulation-accent').click();
  await page.getByTestId('piano-roll-dynamic-upsert').click();

  // Navigation: next bar / section / zoom.
  const navStart = Date.now();
  await page.getByTestId('piano-roll-next-bar').click();
  await page.getByTestId('piano-roll-next-section').click();
  await page.getByTestId('piano-roll-zoom-fit').click();
  expect(Date.now() - navStart).toBeLessThan(PERF.maxNavigationMs);

  // Locked track: hide then lock bass and assert lock control exists.
  await page.getByTestId('piano-roll-track-lock-bass-1').click();
  await page.getByTestId('piano-roll-track-hide-harmony-1').click();
  const prefs = await getEditorWorkflowSnapshot(page);
  expect(prefs.lockedTrackCount).toBeGreaterThanOrEqual(1);
  expect(prefs.hiddenTrackCount).toBeGreaterThanOrEqual(1);

  for (const line of consoleLines) {
    assertNoSensitiveConsolePayload(line);
  }
});

test('V2 editor performance: viewport culling and single-commit drag', async ({
  page,
  request,
}) => {
  test.info().annotations.push({
    type: 'perf',
    description: '100-bar culling + zero mid-drag commits',
  });

  const seeded = await openSeededPianoRoll(page, request, 'Editor Perf Budget');
  const start = await getEditorWorkflowSnapshot(page);
  expect(start.eventCount).toBe(seeded.noteCount);
  expect(start.renderedNoteDom).toBeLessThan(start.eventCount * 0.5);
  expect(start.renderedNoteDom).toBeLessThanOrEqual(PERF.maxRenderedNotesAtStart);

  // Scroll right — still culled vs full score.
  await page.getByTestId('piano-roll-grid').evaluate((el) => {
    el.scrollLeft = Math.min(el.scrollWidth, el.clientWidth * 8);
  });
  await page.waitForTimeout(200);
  const scrolled = await getEditorWorkflowSnapshot(page);
  expect(scrolled.renderedNoteDom).toBeLessThan(scrolled.eventCount);
  expect(scrolled.renderedNoteDom).toBeLessThanOrEqual(PERF.maxRenderedNotesAtStart * 1.5);

  // Drag: commits stay 0 until pointer-up, then exactly one.
  await page.getByTestId('piano-roll-track-select-melody-1').click();
  await page.getByTestId('piano-roll-zoom-in').click();
  await page.getByTestId('piano-roll-zoom-in').click();
  await enableEditorPerfOnPage(page);
  const target = page.locator('[data-testid="piano-roll-note"][data-track-id="melody-1"]').first();
  await expect(target).toBeVisible();
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  expect(box).toBeTruthy();

  const revisionBefore = (await getEditorWorkflowSnapshot(page))?.compositionRevision;
  await page.evaluate(() => window.__MUKIT_EDITOR_PERF_API__?.reset?.());

  const dragStarted = Date.now();
  await target.hover();
  await page.mouse.down();
  // Move far enough to cross snap + pitch row (avoid no-op commit).
  await page.mouse.move(box.x + 200, box.y - 60, { steps: 15 });
  const mid = await getEditorPerfSnapshot(page);
  expect(mid?.dragCommitCount ?? 0).toBe(0);
  expect(mid?.commitCount ?? 0).toBe(0);
  await page.mouse.up();
  expect(Date.now() - dragStarted).toBeLessThan(PERF.maxDragMs);

  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.compositionRevision)
    .not.toBe(revisionBefore);
  await expect.poll(async () => (await getEditorPerfSnapshot(page))?.commitCount ?? 0).toBe(1);
  const afterDrag = await getEditorPerfSnapshot(page);
  expect(afterDrag.dragCommitCount).toBeLessThanOrEqual(1);

  // Playback cursor should not thrash the note layer (bounded extra renders).
  const rendersAfterDrag = afterDrag.noteLayerRenderCount;
  await page.getByTestId('playback-play').click();
  await page.waitForTimeout(600);
  await page.getByTestId('playback-stop').click();
  const afterPlay = await getEditorPerfSnapshot(page);
  const extraRenders = (afterPlay?.noteLayerRenderCount ?? 0) - rendersAfterDrag;
  expect(extraRenders).toBeLessThan(40);

  // Alt+drag box select latency budget.
  const grid = page.getByTestId('piano-roll-grid');
  const gridBox = await grid.boundingBox();
  expect(gridBox).toBeTruthy();
  await page.evaluate(() => window.__MUKIT_EDITOR_PERF_API__?.reset?.());
  const boxStart = Date.now();
  await page.keyboard.down('Alt');
  await page.mouse.move(gridBox.x + 80, gridBox.y + 60);
  await page.mouse.down();
  await page.mouse.move(gridBox.x + 280, gridBox.y + 160, { steps: 6 });
  await page.mouse.up();
  await page.keyboard.up('Alt');
  expect(Date.now() - boxStart).toBeLessThan(PERF.maxBoxSelectMs);
  await expect.poll(async () => (await getEditorWorkflowSnapshot(page))?.selectedCount ?? 0)
    .toBeGreaterThan(0);

  const transformStart = Date.now();
  await page.getByTestId('piano-roll-transpose-down').click();
  expect(Date.now() - transformStart).toBeLessThan(PERF.maxTransformMs);
});

test('V2 editor mobile width remains usable', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeededPianoRoll(page, request, 'Editor Mobile Width');

  await expect(page.getByTestId('piano-roll-grid')).toBeVisible();
  await expect(page.getByTestId('piano-roll-selection-inspector')).toBeVisible();
  await expect(page.getByTestId('playback-from-cursor')).toBeVisible();

  const overflow = await page.evaluate(() => ({
    docScrollWidth: document.documentElement.scrollWidth,
    docClientWidth: document.documentElement.clientWidth,
  }));
  expect(overflow.docScrollWidth).toBeLessThanOrEqual(overflow.docClientWidth + 8);
});
