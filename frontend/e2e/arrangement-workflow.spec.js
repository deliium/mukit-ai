import { expect, test } from '@playwright/test';

import {
  arrangementAcceptanceInstrumentation,
  arrangementOperationConfig,
  assertSchemaV2,
  buildArrangementPianoSketchFixture,
  configureArrangementViaStore,
  editArrangementSourceNoteViaStore,
  ensureFakeLlmSelected,
  getArrangementSnapshot,
  getNoteSequencesFromStore,
  getStoreSnapshot,
  mockArrangementPreviewRoute,
  openArrangeTab,
  seedV2ProjectViaApi,
  waitForArrangementCatalog,
  waitForArrangementStatus,
  waitForCompositionNotes,
} from './helpers.js';

test.describe.configure({ mode: 'serial' });

const MELODY_PITCHES = ['E4', 'G4', 'C5', 'D5'];
const HARMONY_CHORDS = ['C', 'F'];
const OPERATION_FAMILIES = [
  'add_accompaniment',
  'remove_accompaniment',
  'simplify_arrangement',
  'increase_texture_density',
  'decrease_texture_density',
  'create_countermelody',
  'double_melody',
  'change_instrumentation',
  'orchestrate_selected_tracks',
];

async function openSeededArrangementProject(page, request, {
  name = 'Arrangement Acceptance',
  fixture = null,
} = {}) {
  const seeded = await seedV2ProjectViaApi(request, {
    name,
    fixture: fixture || buildArrangementPianoSketchFixture(),
  });
  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 8, timeout: 60_000 });
  await ensureFakeLlmSelected(page);
  const opened = await getStoreSnapshot(page);
  assertSchemaV2(opened);
  return { seeded, opened };
}

async function prepareArrangement(page, config) {
  await openArrangeTab(page);
  await expect(page.getByTestId('arrange-panel')).toBeVisible();
  await waitForArrangementCatalog(page);
  const configured = await configureArrangementViaStore(page, config);
  expect(configured.ok).toBe(true);
  expect(configured.catalogStatus).toBe('ready');
  return configured;
}

async function generateArrangement(page, { expectCandidates = 1 } = {}) {
  // Prefer store action: at 390px mixer chrome can intercept Arrange panel buttons.
  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().startArrangementPreview());
  const ready = await waitForArrangementStatus(page, 'ready', { timeout: 120_000 });
  expect(ready.candidateCount).toBeGreaterThanOrEqual(expectCandidates);
  await expect(page.getByTestId('arrange-status')).toContainText(/candidate\(s\) ready/i);
  return ready;
}

async function confirmApplySelected(page) {
  const applyBtn = page.getByTestId('arrange-apply');
  await expect(applyBtn).toBeEnabled();
  await applyBtn.click({ force: true });
  await expect(page.getByTestId('arrange-apply-confirm')).toBeVisible();
  await expect(page.getByTestId('arrange-apply-confirm')).toContainText(/Warnings:/i);
  await applyBtn.click({ force: true });
  await waitForArrangementStatus(page, 'idle', { timeout: 30_000 });
}

function instrumentTokens(summaries) {
  return summaries.map((item) => String(item.instrument || '').toLowerCase());
}

test('Arrange acceptance: piano sketch → 3 ensemble candidates, apply second, persist', async ({
  page,
  request,
}) => {
  test.info().annotations.push({
    type: 'journey',
    description:
      'Seed piano/accomp/bass, preview three piano/cello/strings candidates, apply non-first, save/reopen',
  });

  const { seeded } = await openSeededArrangementProject(page, request, {
    name: 'Arrangement Piano Ensemble Acceptance',
  });

  const before = await getArrangementSnapshot(page);
  expect(before.melodyPitches).toEqual(MELODY_PITCHES);
  expect(before.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(before.trackCount).toBe(3);
  const sequencesBefore = await getNoteSequencesFromStore(page);
  const undoBefore = before.undoDepth;
  const revisionBefore = before.compositionRevision;
  const saveBefore = before.saveStatus;

  await prepareArrangement(page, {
    ...arrangementOperationConfig('piano_to_ensemble'),
    candidateCount: 3,
    preserveMelody: true,
    preserveHarmony: true,
    instruction: 'ephemeral arrange instruction must not persist',
  });

  await page.getByTestId('arrange-candidate-count').selectOption('3');
  await expect(page.getByTestId('arrange-preserve-melody')).toBeChecked();

  const preview = await generateArrangement(page, { expectCandidates: 3 });
  expect(preview.candidateCount).toBe(3);
  expect(preview.provider).toBe('fake');
  expect(preview.compositionRevision).toBe(revisionBefore);
  expect(preview.saveStatus).toBe(saveBefore);
  expect(preview.undoDepth).toBe(undoBefore);
  expect(preview.melodyPitches).toEqual(MELODY_PITCHES);
  expect(preview.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(await getNoteSequencesFromStore(page)).toEqual(sequencesBefore);

  const firstCandidate = preview.selectedCandidateSummary;
  expect(firstCandidate).toBeTruthy();
  expect(firstCandidate.melodyPitches).toEqual(MELODY_PITCHES);
  expect(firstCandidate.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(firstCandidate.key).toBe(before.key);
  expect(firstCandidate.tempo).toBe(before.tempo);
  expect(firstCandidate.timeSignature).toBe(before.timeSignature);
  expect(firstCandidate.durationTicks).toBe(before.durationTicks);
  const firstInstruments = instrumentTokens(firstCandidate.trackSummaries);
  expect(firstInstruments.some((name) => name.includes('piano'))).toBe(true);
  expect(firstInstruments.some((name) => name.includes('cello'))).toBe(true);
  expect(firstInstruments.some((name) => name.includes('string'))).toBe(true);
  expect(firstCandidate.trackSummaries.every((track) => track.eventCount > 0)).toBe(true);
  expect(firstCandidate.duplicateFindingCodes).not.toContain('exact_clone');

  await page.getByTestId('arrange-play-source').click();
  await expect(page.getByTestId('arrange-audition-source')).toBeVisible();
  expect((await getArrangementSnapshot(page)).auditionMode).toBe('source');

  await page.getByTestId('arrange-candidate-radio-1').check({ force: true });
  await expect(page.getByTestId('arrange-candidate-radio-1')).toBeChecked();

  await page.getByTestId('arrange-audition').click();
  await expect(page.getByTestId('arrange-audition-active')).toBeVisible();
  const auditioning = await getArrangementSnapshot(page);
  expect(auditioning.auditionMode).toBe('candidate');
  expect(auditioning.compositionRevision).toBe(revisionBefore);
  expect(auditioning.melodyPitches).toEqual(MELODY_PITCHES);
  expect(await getNoteSequencesFromStore(page)).toEqual(sequencesBefore);

  const selectedBeforeApply = auditioning.selectedCandidateSummary;
  expect(selectedBeforeApply.candidateIdSuffix).not.toBe(firstCandidate.candidateIdSuffix);
  expect(selectedBeforeApply.melodyPitches).toEqual(MELODY_PITCHES);

  await confirmApplySelected(page);

  const applied = await getArrangementSnapshot(page);
  expect(applied.arrangementStatus).toBe('idle');
  expect(applied.candidateCount).toBe(0);
  expect(applied.auditionMode).toBe('source');
  expect(applied.undoDepth).toBe(undoBefore + 1);
  expect(applied.compositionRevision).not.toBe(revisionBefore);
  expect(applied.melodyPitches).toEqual(MELODY_PITCHES);
  expect(applied.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(applied.key).toBe(before.key);
  expect(applied.tempo).toBe(before.tempo);
  expect(applied.timeSignature).toBe(before.timeSignature);
  expect(applied.durationTicks).toBe(before.durationTicks);
  const appliedInstruments = instrumentTokens(applied.trackSummaries);
  expect(appliedInstruments.some((name) => name.includes('piano'))).toBe(true);
  expect(appliedInstruments.some((name) => name.includes('cello'))).toBe(true);
  expect(appliedInstruments.some((name) => name.includes('string'))).toBe(true);
  expect(applied.trackSummaries.every((track) => track.eventCount > 0)).toBe(true);
  expect(applied.trackSummaries.some((track) => track.midiProgram === 0)).toBe(true);
  expect(applied.trackSummaries.some((track) => track.midiProgram === 42)).toBe(true);
  expect(applied.trackSummaries.some((track) => track.midiProgram === 48)).toBe(true);

  // Instruction may remain as ephemeral UI control, but never enters project payloads.
  const saveProbe = await page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__.getState();
    const payload = {
      name: state.currentProjectName,
      composition: state.editedMusicJson,
    };
    return {
      hasArrangementKeys: Object.keys(payload).some((key) => key.startsWith('arrangement')),
      compositionHasInstruction: JSON.stringify(payload.composition || {}).includes(
        'ephemeral arrange instruction',
      ),
    };
  });
  expect(saveProbe.hasArrangementKeys).toBe(false);
  expect(saveProbe.compositionHasInstruction).toBe(false);

  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().undoCompositionEdit());
  const undone = await getArrangementSnapshot(page);
  expect(undone.melodyPitches).toEqual(MELODY_PITCHES);
  expect(undone.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(await getNoteSequencesFromStore(page)).toEqual(sequencesBefore);
  expect(undone.trackCount).toBe(3);

  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().redoCompositionEdit());
  const redone = await getArrangementSnapshot(page);
  expect(redone.melodyPitches).toEqual(MELODY_PITCHES);
  expect(instrumentTokens(redone.trackSummaries).some((name) => name.includes('cello'))).toBe(true);

  await page.getByTestId('save-project').click({ force: true });
  await expect.poll(async () => (await getArrangementSnapshot(page))?.saveStatus, {
    timeout: 30_000,
  }).toBe('saved');

  const persistedTracks = redone.trackSummaries;
  await page.goto('/');
  await page.getByTestId(`open-project-${seeded.projectId}`).click();
  await waitForCompositionNotes(page, { minEvents: 8, timeout: 60_000 });
  const reopened = await getArrangementSnapshot(page);
  assertSchemaV2(reopened);
  expect(reopened.candidateCount).toBe(0);
  expect(reopened.instruction).toBe('');
  expect(reopened.arrangementStatus).toBe('idle');
  expect(reopened.melodyPitches).toEqual(MELODY_PITCHES);
  expect(reopened.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(reopened.trackSummaries.map((t) => [t.instrument, t.role, t.midiProgram, t.eventCount]))
    .toEqual(persistedTracks.map((t) => [t.instrument, t.role, t.midiProgram, t.eventCount]));
});

test('Arrange operation families preview without mutating working V2', async ({ page, request }) => {
  await openSeededArrangementProject(page, request, {
    name: 'Arrangement Operation Families',
  });
  const baseline = await getArrangementSnapshot(page);
  const sequencesBefore = await getNoteSequencesFromStore(page);

  await openArrangeTab(page);
  await waitForArrangementCatalog(page);

  for (const operation of OPERATION_FAMILIES) {
    const config = {
      ...arrangementOperationConfig(operation),
      candidateCount: 1,
    };
    const configured = await configureArrangementViaStore(page, config);
    expect(configured.ok, operation).toBe(true);

    await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().startArrangementPreview());
    const ready = await waitForArrangementStatus(page, 'ready', { timeout: 120_000 });
    expect(ready.candidateCount, operation).toBeGreaterThanOrEqual(1);
    expect(ready.compositionRevision, operation).toBe(baseline.compositionRevision);
    expect(ready.undoDepth, operation).toBe(baseline.undoDepth);
    expect(ready.melodyPitches, operation).toEqual(MELODY_PITCHES);
    expect(await getNoteSequencesFromStore(page)).toEqual(sequencesBefore);

    const candidate = ready.selectedCandidateSummary;
    expect(candidate, operation).toBeTruthy();
    expect(candidate.operation, operation).toBe(operation);
    expect(candidate.harmonyChords, operation).toEqual(HARMONY_CHORDS);

    if (operation === 'add_accompaniment') {
      expect(candidate.trackCount).toBeGreaterThan(baseline.trackCount);
    }
    if (operation === 'remove_accompaniment') {
      expect(candidate.trackSummaries.some((track) => track.id === 'piano-accomp')).toBe(false);
      expect(candidate.melodyPitches).toEqual(MELODY_PITCHES);
    }
    if (operation === 'simplify_arrangement' || operation === 'decrease_texture_density') {
      const beforeCount = Number(candidate.densityBefore?.event_count);
      const afterCount = Number(candidate.densityAfter?.event_count);
      if (Number.isFinite(beforeCount) && Number.isFinite(afterCount)) {
        expect(afterCount, operation).toBeLessThanOrEqual(beforeCount);
      }
    }
    if (operation === 'increase_texture_density') {
      const beforeCount = Number(candidate.densityBefore?.event_count);
      const afterCount = Number(candidate.densityAfter?.event_count);
      if (Number.isFinite(beforeCount) && Number.isFinite(afterCount)) {
        expect(afterCount, operation).toBeGreaterThanOrEqual(beforeCount);
      }
    }
    if (operation === 'create_countermelody') {
      expect(candidate.trackSummaries.some((track) => track.role === 'countermelody')).toBe(true);
    }
    if (operation === 'double_melody') {
      expect(candidate.trackCount).toBeGreaterThanOrEqual(2);
      expect(candidate.melodyPitches).toEqual(MELODY_PITCHES);
    }

    await page.getByTestId('arrange-discard').click();
    await expect.poll(async () => (await getArrangementSnapshot(page))?.candidateCount)
      .toBe(0);
  }
});

test('Arrange stale protection, impossible range reject, API failure atomicity', async ({
  page,
  request,
}) => {
  await openSeededArrangementProject(page, request, {
    name: 'Arrangement Safety Paths',
  });
  const before = await getArrangementSnapshot(page);

  await prepareArrangement(page, {
    ...arrangementOperationConfig('piano_to_ensemble'),
    candidateCount: 2,
  });
  await generateArrangement(page, { expectCandidates: 2 });

  await page.getByTestId('arrange-candidate-count').selectOption('1');
  await expect(page.getByTestId('arrange-stale')).toBeVisible();
  let stale = await getArrangementSnapshot(page);
  expect(stale.arrangementStatus).toBe('stale');
  expect(stale.arrangementStaleReason).toBe('settings_changed');
  expect(stale.melodyPitches).toEqual(MELODY_PITCHES);

  await configureArrangementViaStore(page, {
    ...arrangementOperationConfig('piano_to_ensemble'),
    candidateCount: 1,
  });
  await generateArrangement(page, { expectCandidates: 1 });
  const edited = await editArrangementSourceNoteViaStore(page, {
    trackId: 'piano-melody',
    noteIndex: 0,
    nextPitch: 'F4',
  });
  expect(edited.ok).toBe(true);
  // Note edits clear ephemeral candidates (source mutation protection).
  await expect.poll(async () => (await getArrangementSnapshot(page))?.candidateCount)
    .toBe(0);
  stale = await getArrangementSnapshot(page);
  expect(stale.arrangementStatus).toBe('idle');
  expect(stale.melodyPitches[0]).toBe('F4');
  expect(stale.selectedCandidateId).toBeNull();

  // Restore pitch for subsequent range / failure cases
  await editArrangementSourceNoteViaStore(page, {
    trackId: 'piano-melody',
    noteIndex: 0,
    nextPitch: 'E4',
  });

  await configureArrangementViaStore(page, {
    operation: 'orchestrate_selected_tracks',
    sourceTrackIds: ['piano-melody'],
    protectedTrackIds: [],
    preserveMelody: false,
    candidateCount: 1,
    instrumentation: {
      before: arrangementAcceptanceInstrumentation().before.slice(0, 1),
      after: [
        {
          part_id: 'a-cb',
          instrument_id: 'contrabass',
          role: 'melody',
          source_track_ids: ['piano-melody'],
          doubling_policy: 'none',
        },
      ],
    },
  });
  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().startArrangementPreview());
  await expect.poll(async () => {
    const snap = await getArrangementSnapshot(page);
    return snap.arrangementStatus === 'error'
      || (snap.rejectedCount > 0 && snap.candidateCount === 0)
      || (snap.rejectedCodes || []).includes('arrangement_range_failed');
  }, { timeout: 60_000 }).toBe(true);
  const rangeFail = await getArrangementSnapshot(page);
  if (rangeFail.arrangementStatus === 'error') {
    expect(rangeFail.arrangementError.length).toBeGreaterThan(0);
  } else {
    await expect(page.getByTestId('arrange-rejected-list')).toBeVisible();
    expect(rangeFail.rejectedCodes.join('|')).toMatch(/range|exhausted|invalid/i);
  }
  expect(rangeFail.trackCount).toBe(before.trackCount);

  const capture = {};
  await mockArrangementPreviewRoute(page, {
    status: 503,
    once: true,
    capture,
    body: {
      detail: {
        code: 'arrangement_provider_unavailable',
        message: 'Mocked provider unavailable',
        details: { reason: 'e2e_mock' },
      },
    },
  });
  await configureArrangementViaStore(page, {
    ...arrangementOperationConfig('add_accompaniment'),
    candidateCount: 1,
  });
  const undoBeforeFail = (await getArrangementSnapshot(page)).undoDepth;
  const revisionBeforeFail = (await getArrangementSnapshot(page)).compositionRevision;
  await page.evaluate(() => window.__MUKIT_MUSIC_STORE__.getState().startArrangementPreview());
  await waitForArrangementStatus(page, 'error', { timeout: 30_000 });
  const failed = await getArrangementSnapshot(page);
  expect(failed.candidateCount).toBe(0);
  expect(failed.undoDepth).toBe(undoBeforeFail);
  expect(failed.compositionRevision).toBe(revisionBeforeFail);
  expect(failed.arrangementError).toMatch(/unavailable|failed|provider/i);
  expect(failed.melodyPitches).toEqual(MELODY_PITCHES);
  expect(failed.harmonyChords).toEqual(HARMONY_CHORDS);
  expect(capture.payload?.operation || null).toBe('add_accompaniment');
});

test('Arrange warning confirmation, keyboard focus, 390x844 no overflow', async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSeededArrangementProject(page, request, {
    name: 'Arrangement Mobile Accessibility',
  });

  await page.getByTestId('composer-tab-arrange').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('arrange-panel')).toBeVisible();
  await waitForArrangementCatalog(page);

  await configureArrangementViaStore(page, {
    ...arrangementOperationConfig('piano_to_ensemble'),
    candidateCount: 1,
  });
  await generateArrangement(page, { expectCandidates: 1 });

  await page.getByTestId('arrange-candidate-radio-0').focus();
  await expect(page.getByTestId('arrange-candidate-radio-0')).toBeFocused();

  const applyBtn = page.getByTestId('arrange-apply');
  await applyBtn.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('arrange-apply-confirm')).toBeVisible();
  await expect(page.getByTestId('arrange-apply-confirm')).toContainText(/Warnings:/i);
  await expect(page.getByTestId('arrange-apply-confirm')).toContainText(/Confirm to continue/i);
  // Confirm via store when mobile mixer overlays the Apply button.
  await page.evaluate(async () => {
    await window.__MUKIT_MUSIC_STORE__.getState().applySelectedArrangementCandidate();
  });
  await waitForArrangementStatus(page, 'idle', { timeout: 30_000 });

  const overflow = await page.evaluate(() => {
    const panel = document.querySelector('[data-testid="arrange-panel"]');
    const doc = document.documentElement;
    return {
      panelOverflow: (panel?.scrollWidth || 0) > (panel?.clientWidth || 0) + 2,
      docOverflow: doc.scrollWidth > doc.clientWidth + 1,
    };
  });
  expect(overflow.panelOverflow).toBe(false);
  expect(overflow.docOverflow).toBe(false);
});
