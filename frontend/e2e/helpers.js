/** Shared helpers for V1/V2 Playwright journeys. */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  buildLargeScoreEditorFixture,
  largeScoreNoteCount,
  toCanonicalLargeScore,
} from './fixtures/largeScoreEditor.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '../..');

export const DEFAULT_BACKEND_URL = process.env.PLAYWRIGHT_BACKEND_URL || 'http://127.0.0.1:8888';

export function backendBaseUrl() {
  return DEFAULT_BACKEND_URL;
}

/** Ordered per-track note fingerprints used for V1→V2 migration equality checks. */
export function extractNoteSequences(composition) {
  if (!composition?.tracks?.length) {
    return {};
  }
  const sequences = {};
  for (const track of composition.tracks) {
    sequences[track.id] = (track.events || [])
      .filter((event) => event?.type === 'note')
      .map((event) => ({
        id: event.id ?? null,
        pitch: event.pitch,
        start_tick: event.start_tick,
        duration_ticks: event.duration_ticks,
        velocity: event.velocity,
        staff: event.staff ?? null,
        voice: event.voice ?? null,
      }));
  }
  return sequences;
}

/** True when note ids match, or left is null and right was hydration-assigned for UI editing. */
export function noteIdsCompatible(sourceId, openedId) {
  if (sourceId === openedId) {
    return true;
  }
  const sourceMissing = sourceId == null || sourceId === '';
  const openedPresent = openedId != null && openedId !== '';
  return sourceMissing && openedPresent;
}

export function compareNoteSequences(left, right) {
  const leftIds = Object.keys(left || {}).sort();
  const rightIds = Object.keys(right || {}).sort();
  if (leftIds.length !== rightIds.length) {
    return false;
  }
  for (let i = 0; i < leftIds.length; i += 1) {
    if (leftIds[i] !== rightIds[i]) {
      return false;
    }
    const trackId = leftIds[i];
    const leftNotes = left[trackId];
    const rightNotes = right[trackId];
    if (leftNotes.length !== rightNotes.length) {
      return false;
    }
    for (let j = 0; j < leftNotes.length; j += 1) {
      const a = leftNotes[j];
      const b = rightNotes[j];
      if (
        !noteIdsCompatible(a.id, b.id)
        || a.pitch !== b.pitch
        || a.start_tick !== b.start_tick
        || a.duration_ticks !== b.duration_ticks
        || a.velocity !== b.velocity
        || a.staff !== b.staff
        || a.voice !== b.voice
      ) {
        return false;
      }
    }
  }
  return true;
}

export function assertSchemaV2(snapshot) {
  if (!snapshot || snapshot.schemaVersion !== 'composition.v2') {
    throw new Error(`Expected composition.v2, got ${snapshot?.schemaVersion ?? 'missing'}`);
  }
}

export function loadRepoFixture(relativePath) {
  const absolute = path.join(repoRoot, relativePath);
  return JSON.parse(fs.readFileSync(absolute, 'utf8'));
}

/**
 * Create a project and attach a V1 composition fixture via HTTP.
 * Backend normalizes to V2 on PATCH; note sequences from the V1 fixture are returned for equality checks.
 */
export async function seedV1ProjectViaApi(request, { name = 'V1 Upgrade Seed', fixture = null } = {}) {
  const composition = fixture || loadRepoFixture('backend/tests/fixtures/composition_v1_minimal.json');
  const backendUrl = backendBaseUrl();
  const create = await request.post(`${backendUrl}/projects`, { data: { name } });
  if (!create.ok()) {
    throw new Error(`Failed to create project: ${create.status()} ${await create.text()}`);
  }
  const { id: projectId } = await create.json();
  const sourceNoteSequences = extractNoteSequences(composition);
  const patch = await request.patch(`${backendUrl}/projects/${projectId}`, {
    data: { composition },
  });
  if (!patch.ok()) {
    throw new Error(`Failed to seed V1 composition: ${patch.status()} ${await patch.text()}`);
  }
  return { projectId, sourceNoteSequences, fixtureSchemaVersion: composition.schema_version };
}

export async function getStoreSnapshot(page) {
  return page.evaluate(() => {
    const store = window.__MUKIT_MUSIC_STORE__;
    if (!store) {
      return null;
    }
    const state = store.getState();
    const composition = state.editedMusicJson;
    const eventCount = Array.isArray(composition?.tracks)
      ? composition.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0)
      : 0;
    return {
      projectId: state.currentProjectId,
      currentProjectId: state.currentProjectId,
      projectName: state.currentProjectName,
      schemaVersion: composition?.schema_version ?? null,
      barCount: composition?.bar_count ?? null,
      trackCount: composition?.tracks?.length ?? 0,
      eventCount,
      playbackStatus: state.playbackStatus,
      saveStatus: state.saveStatus,
      selectedProvider: state.selectedProvider,
      selectedModel: state.selectedModel,
      melodyFirstPitch: composition?.tracks?.find((t) => t.id === 'melody-1')?.events?.[0]?.pitch ?? null,
      tempoChangeCount: Array.isArray(composition?.tempo_changes) ? composition.tempo_changes.length : 0,
      importStatus: state.importStatus,
      importError: state.importError,
      importReport: state.importReport,
      generationMeta: state.generationMeta,
      editedMusicJson: composition,
    };
  });
}

export async function getNoteSequencesFromStore(page) {
  return page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson;
    if (!composition?.tracks?.length) {
      return {};
    }
    const sequences = {};
    for (const track of composition.tracks) {
      sequences[track.id] = (track.events || [])
        .filter((event) => event?.type === 'note')
        .map((event) => ({
          id: event.id ?? null,
          pitch: event.pitch,
          start_tick: event.start_tick,
          duration_ticks: event.duration_ticks,
          velocity: event.velocity,
          staff: event.staff ?? null,
          voice: event.voice ?? null,
        }));
    }
    return sequences;
  });
}

export async function waitForCompositionNotes(page, { minEvents = 1, timeout = 60_000 } = {}) {
  await page.waitForFunction(
    (minimum) => {
      const store = window.__MUKIT_MUSIC_STORE__;
      if (!store) return false;
      const composition = store.getState().editedMusicJson;
      if (!composition?.tracks?.length) return false;
      const events = composition.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0);
      return events >= minimum;
    },
    minEvents,
    { timeout },
  );
}

/** Wait until AI region edit finishes successfully (fails on error/timeout). */
export async function waitForAiEditSuccess(page, { timeout = 60_000 } = {}) {
  const { expect } = await import('@playwright/test');
  await expect.poll(async () => {
    const snapshot = await page.evaluate(() => {
      const store = window.__MUKIT_MUSIC_STORE__;
      if (!store) {
        return { aiEditStatus: 'missing', aiEditError: 'store missing' };
      }
      const state = store.getState();
      return {
        aiEditStatus: state.aiEditStatus,
        aiEditError: state.aiEditError || '',
        warningCount: Array.isArray(state.aiEditWarnings) ? state.aiEditWarnings.length : 0,
      };
    });
    if (snapshot.aiEditStatus === 'error') {
      throw new Error(`AI edit failed: ${snapshot.aiEditError || 'unknown'}`);
    }
    return snapshot.aiEditStatus;
  }, { timeout }).toBe('success');
  const snapshot = await page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__.getState();
    return {
      aiEditStatus: state.aiEditStatus,
      aiEditError: state.aiEditError,
      warningCount: Array.isArray(state.aiEditWarnings) ? state.aiEditWarnings.length : 0,
    };
  });
  console.info('[FIX:e2e-ai-edit] AI region edit succeeded', snapshot);
  return snapshot;
}

async function pickFakeProvider(page) {
  const options = page.getByTestId('llm-model-select').locator('option');
  const values = await options.evaluateAll((nodes) => nodes.map((node) => node.value));
  const fake = values.find((value) => value.startsWith('fake:'));
  if (fake) {
    await page.getByTestId('llm-model-select').selectOption(fake);
  }
}

/**
 * Prepare prompt fields so fake LLM fixture selection and constraint gates succeed.
 * Default UI sections (sum 20) + instruments including "strings" 422 / fail fixtures.
 * Empty sections => sections_user_specified=false (required for 4-bar expressive fixture).
 */
export async function prepareFakeLlmPrompt(page, {
  durationBars = 16,
  instruments = 'piano,bass',
  sections = '',
} = {}) {
  await page.locator('#instruments').fill(instruments);
  await page.locator('#sections').fill(sections);
  await page.locator('#durationBars').fill(String(durationBars));
  console.info('[FIX:e2e-fake-generate] Prepared fake LLM prompt', {
    durationBars,
    instruments,
    sections: sections === '' ? '(empty)' : sections,
  });
}

export async function createProjectAndGenerate(page, {
  durationBars = 16,
  minEvents = 10,
  instruments = 'piano,bass',
  sections = '',
} = {}) {
  await page.goto('/');
  await page.getByTestId('new-project').click();
  await page.getByTestId('llm-model-select').waitFor({ state: 'visible', timeout: 30_000 });
  await pickFakeProvider(page);
  await prepareFakeLlmPrompt(page, { durationBars, instruments, sections });
  await page.getByTestId('generate-music').click();

  // Generation is preview-first: Apply must install the candidate into editedMusicJson.
  await page.getByTestId('generation-candidate-panel').waitFor({ state: 'visible', timeout: 90_000 });
  console.info('[FIX:e2e-fake-generate] Generation candidate ready; applying to working composition', {
    durationBars,
    instruments,
  });
  await page.getByTestId('generation-apply').click();

  try {
    await waitForCompositionNotes(page, { minEvents, timeout: 90_000 });
  } catch (error) {
    const debug = await page.evaluate(() => {
      const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
      return {
        hasCandidate: Boolean(state?.generationCandidate),
        candidateStatus: state?.generationCandidate?.status || null,
        eventCount: Array.isArray(state?.editedMusicJson?.tracks)
          ? state.editedMusicJson.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0)
          : 0,
        uiError: state?.uiError || null,
      };
    });
    console.error('[FIX:e2e-fake-generate] Apply did not install working notes', debug);
    throw error;
  }

  const snapshot = await getStoreSnapshot(page);
  console.info('[FIX:e2e-fake-generate] Generate+Apply completed', {
    durationBars,
    schemaVersion: snapshot?.schemaVersion,
    barCount: snapshot?.barCount,
    eventCount: snapshot?.eventCount,
    tempoChangeCount: snapshot?.tempoChangeCount,
  });
  return snapshot;
}

/** Fake LLM serves native V2 expressive fixture at 4 bars / 4-4 when sections are unspecified. */
export async function createProjectAndGenerateExpressive(page) {
  return createProjectAndGenerate(page, {
    durationBars: 4,
    minEvents: 5,
    instruments: 'piano,bass',
    sections: '',
  });
}

/** Piano-roll note edit via the same Zustand actions the UI uses (avoids brittle grid hit-testing). */
export async function editMelodyNoteViaStore(page) {
  return page.evaluate(() => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const track = (state.editedMusicJson?.tracks || []).find((item) => item.id === 'melody-1')
      || (state.editedMusicJson?.tracks || [])[0];
    if (!track?.events?.length) {
      return { ok: false, reason: 'no melody events' };
    }
    const note = track.events[0];
    const nextPitch = note.pitch === 'C5' ? 'D5' : 'C5';
    const updated = state.updateNote(track.id, note.id, { pitch: nextPitch });
    const after = api.getState();
    return {
      ok: Boolean(updated),
      trackId: track.id,
      noteId: note.id,
      beforePitch: note.pitch,
      afterPitch: after.editedMusicJson?.tracks
        ?.find((item) => item.id === track.id)
        ?.events?.find((event) => String(event.id) === String(note.id))
        ?.pitch,
      eventCount: (after.editedMusicJson?.tracks || []).reduce(
        (sum, item) => sum + (item.events?.length || 0),
        0,
      ),
    };
  });
}

export async function selectMelodyNoteViaStore(page, { noteIndex = 0, trackId = 'melody-1' } = {}) {
  return page.evaluate(({ idx, preferredTrackId }) => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const track = (state.editedMusicJson?.tracks || []).find((item) => item.id === preferredTrackId)
      || (state.editedMusicJson?.tracks || [])[0];
    if (!track?.events?.length) {
      return { ok: false, reason: 'no events' };
    }
    const note = track.events[idx] || track.events[0];
    if (state.selectPianoRollTrack) {
      state.selectPianoRollTrack(track.id);
    }
    state.selectPianoRollNote(note.id);
    return { ok: true, trackId: track.id, noteId: note.id, pitch: note.pitch };
  }, { idx: noteIndex, preferredTrackId: trackId });
}

export async function editArticulationViaStore(page, articulation, { noteIndex = 0, trackId = 'melody-1' } = {}) {
  const selected = await selectMelodyNoteViaStore(page, { noteIndex, trackId });
  if (!selected.ok) {
    return { ok: false, reason: selected.reason || 'selection failed' };
  }
  return page.evaluate(({ art, tid, nid }) => {
    const api = window.__MUKIT_MUSIC_STORE__;
    const beforeNote = api.getState().editedMusicJson?.tracks
      ?.find((item) => item.id === tid)
      ?.events?.find((event) => String(event.id) === String(nid));
    const before = Array.isArray(beforeNote?.articulations) ? beforeNote.articulations : [];
    if (before.includes(art)) {
      console.info('[FIX:e2e-articulation] Articulation already present; skip toggle', {
        trackId: tid,
        noteId: nid,
        articulation: art,
      });
      return { ok: true, articulations: before, alreadyPresent: true };
    }
    const note = api.getState().toggleNoteArticulation(tid, nid, art);
    const after = api.getState().editedMusicJson?.tracks
      ?.find((item) => item.id === tid)
      ?.events?.find((event) => String(event.id) === String(nid));
    console.info('[FIX:e2e-articulation] Applied articulation', {
      trackId: tid,
      noteId: nid,
      articulation: art,
      articulations: after?.articulations || [],
      ok: Boolean(note),
    });
    return {
      ok: Boolean(note),
      articulations: after?.articulations || [],
      alreadyPresent: false,
    };
  }, { art: articulation, tid: selected.trackId, nid: selected.noteId });
}

export async function getExpressiveMetadataFromStore(page) {
  return page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson;
    if (!composition) {
      return null;
    }
    const melody = composition.tracks?.find((track) => track.id === 'melody-1') || composition.tracks?.[0];
    const firstNote = melody?.events?.[0];
    return {
      schemaVersion: composition.schema_version,
      tempoChangeCount: composition.tempo_changes?.length || 0,
      tempoChanges: composition.tempo_changes || [],
      firstNoteArticulations: firstNote?.articulations || [],
      dynamicMarkCount: melody?.dynamic_marks?.length || 0,
      sustainPedalCount: melody?.sustain_pedals?.length || 0,
      automationLaneCount: melody?.automation?.length || 0,
      markerCount: composition.markers?.length || 0,
    };
  });
}

/** Snapshot of analysis sidecar UI/store state (no full composition/report dump). */
export async function getAnalysisSnapshot(page) {
  return page.evaluate(() => {
    const store = window.__MUKIT_MUSIC_STORE__;
    if (!store) {
      return null;
    }
    const state = store.getState();
    const freshness = typeof state.getAnalysisFreshness === 'function'
      ? state.getAnalysisFreshness()
      : null;
    const warnings = Array.isArray(state.analysisWarnings) ? state.analysisWarnings : [];
    const result = state.analysisResult;
    return {
      analysisScope: state.analysisScope,
      analysisStatus: state.analysisStatus,
      analysisError: state.analysisError || '',
      analysisTabVisible: state.analysisTabVisible,
      sectionKey: state.analysisSelectedSectionKey,
      trackId: state.pianoRollTrackId,
      warningCount: warnings.length,
      warningCodes: warnings.map((item) => item?.code).filter(Boolean).slice(0, 32),
      hasResult: Boolean(result),
      resultStatus: result?.status ?? null,
      algorithmVersion: result?.algorithm_version ?? null,
      resolvedScopeKind: result?.resolved_scope?.kind ?? null,
      fingerprintPrefix: typeof result?.source_fingerprint === 'string'
        ? result.source_fingerprint.slice(0, 12)
        : null,
      isCurrent: freshness?.isCurrent ?? null,
      isStale: freshness?.isStale ?? null,
      isLoading: freshness?.isLoading ?? null,
      schemaVersion: state.editedMusicJson?.schema_version ?? null,
      eventCount: Array.isArray(state.editedMusicJson?.tracks)
        ? state.editedMusicJson.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0)
        : 0,
    };
  });
}

/** Wait until analysis store reports a successful current result. */
export async function waitForAnalysisSuccess(page, { timeout = 60_000, scopeKind = null } = {}) {
  const { expect } = await import('@playwright/test');
  await expect.poll(async () => {
    const snapshot = await getAnalysisSnapshot(page);
    if (!snapshot) {
      return 'missing-store';
    }
    if (snapshot.analysisStatus === 'error') {
      throw new Error(`Analysis failed: ${snapshot.analysisError || 'unknown'}`);
    }
    if (scopeKind && snapshot.resolvedScopeKind && snapshot.resolvedScopeKind !== scopeKind) {
      return `scope:${snapshot.resolvedScopeKind}`;
    }
    if (snapshot.analysisStatus === 'success' && snapshot.hasResult && snapshot.isCurrent) {
      return 'ready';
    }
    return `${snapshot.analysisStatus}:${snapshot.isStale ? 'stale' : 'fresh'}`;
  }, { timeout }).toBe('ready');
  return getAnalysisSnapshot(page);
}

/** Wait until retained analysis is marked stale after a relevant edit. */
export async function waitForAnalysisStale(page, { timeout = 30_000 } = {}) {
  const { expect } = await import('@playwright/test');
  await expect.poll(async () => {
    const snapshot = await getAnalysisSnapshot(page);
    return Boolean(snapshot?.hasResult && snapshot?.isStale);
  }, { timeout }).toBe(true);
  return getAnalysisSnapshot(page);
}

export async function openAnalysisTab(page) {
  await page.getByTestId('composer-tab-analysis').click();
  await page.getByTestId('composition-analysis-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-analysis] Opened Analysis tab');
}

export async function openComposerTab(page, tabId) {
  const tab = page.getByTestId(`composer-tab-${tabId}`);
  await tab.scrollIntoViewIfNeeded();
  await tab.evaluate((node) => {
    node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
  });
  await page.waitForFunction((id) => {
    const node = document.querySelector(`[data-testid="composer-tab-${id}"]`);
    return node?.getAttribute('aria-selected') === 'true';
  }, tabId, { timeout: 10_000 });
}

export async function openMotifsTab(page) {
  await openComposerTab(page, 'motifs');
  await page.getByTestId('motif-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-motifs] Opened Motifs tab');
}

export async function openHarmonyTab(page) {
  await openComposerTab(page, 'harmony');
  await page.getByTestId('harmony-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-harmony] Opened Harmony tab');
}

export async function openDevelopTab(page) {
  await openComposerTab(page, 'develop');
  await page.getByTestId('develop-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-develop] Opened Develop tab');
}

export async function openPianoTab(page) {
  await openComposerTab(page, 'piano');
  await page.getByTestId('piano-roll-grid').waitFor({ state: 'visible', timeout: 30_000 });
}

/**
 * Select ordered melody note IDs for motif authoring via store actions.
 * Defaults to the first four expressive-fixture notes (complete tie chain in bar 1).
 */
export async function selectMotifSourceNotesViaStore(page, {
  trackId = 'melody-1',
  eventIds = null,
  count = 4,
} = {}) {
  return page.evaluate(({ preferredTrackId, explicitIds, take }) => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const track = (state.editedMusicJson?.tracks || []).find((item) => item.id === preferredTrackId)
      || (state.editedMusicJson?.tracks || [])[0];
    if (!track?.events?.length) {
      return { ok: false, reason: 'no events' };
    }
    const ids = Array.isArray(explicitIds) && explicitIds.length
      ? explicitIds
      : track.events.slice(0, take).map((event) => event.id).filter(Boolean);
    if (!ids.length) {
      return { ok: false, reason: 'too_few_ids', ids };
    }
    state.selectPianoRollTrack(track.id);
    // Clear prior multi-select, then apply requested IDs.
    if (typeof state.clearPianoRollSelection === 'function') {
      state.clearPianoRollSelection();
    }
    ids.forEach((id, index) => {
      api.getState().selectPianoRollNote(id, { extend: index > 0 });
    });
    const after = api.getState();
    return {
      ok: true,
      trackId: track.id,
      eventIds: ids,
      selectedCount: after.pianoRollNoteIds?.length || 0,
    };
  }, { preferredTrackId: trackId, explicitIds: eventIds, take: count });
}

/** Configure motif apply destination/operation via store (avoids brittle controlled-input races). */
export async function configureMotifApplyViaStore(page, {
  trackId = 'bass-1',
  startBar = 3,
  sectionId = null,
  operation = 'transpose',
  operationParams = { transpose_semitones: 5 },
  variationStrength = 0.5,
} = {}) {
  return page.evaluate((config) => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const motifs = state.editedMusicJson?.motifs || [];
    const motif = (config.motifLabel
      ? motifs.find((item) => item.label === config.motifLabel)
      : null)
      || motifs[motifs.length - 1]
      || motifs.find((item) => item.label === 'Motif A')
      || motifs[0];
    const occurrence = motif?.occurrences?.find((item) => item.relationship === 'original')
      || motif?.occurrences?.[0];
    if (!motif || !occurrence) {
      return { ok: false, reason: 'motif_missing', motifCount: motifs.length };
    }
    const destinationPatch = {
      trackId: config.trackId,
      startBar: config.startBar,
      startTick: null,
    };
    if (config.sectionId != null) {
      destinationPatch.sectionId = config.sectionId;
    }
    state.selectMotif(motif.id);
    state.configureMotifDestination(destinationPatch);
    state.configureMotifTransformation({
      operation: config.operation,
      operationParams: config.operationParams,
      variationStrength: config.variationStrength,
    });
    const after = api.getState();
    return {
      ok: true,
      motifId: after.motifSelectedMotifId,
      occurrenceId: after.motifSelectedOccurrenceId,
      destinationTrackId: after.motifDestinationTrackId,
      destinationStartBar: after.motifDestinationStartBar,
      destinationStartTick: after.motifDestinationStartTick,
      operation: after.motifOperation,
      params: after.motifOperationParams,
    };
  }, {
    trackId,
    startBar,
    sectionId,
    operation,
    operationParams,
    variationStrength,
  });
}
export async function mockMotifApplyRoute(page, {
  body = null,
  status = 200,
  once = false,
  capture = null,
} = {}) {
  const handler = async (route) => {
    const request = route.request();
    if (capture && typeof capture === 'object') {
      try {
        capture.payload = request.postDataJSON();
      } catch {
        capture.raw = request.postData();
      }
    }
    if (status >= 400) {
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body || {
          detail: {
            code: 'motif_source_unresolved',
            message: 'Mocked motif apply failure',
            details: { reason: 'e2e_mock' },
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  };
  if (once) {
    await page.route('**/motifs/apply', async (route) => {
      await handler(route);
      await page.unroute('**/motifs/apply');
    });
  } else {
    await page.route('**/motifs/apply', handler);
  }
}

/** Bounded motif snapshot for assertions (IDs/counts only — never full event arrays). */
export async function getMotifSnapshot(page) {
  return page.evaluate(() => {
    const composition = window.__MUKIT_MUSIC_STORE__?.getState()?.editedMusicJson;
    const motifs = Array.isArray(composition?.motifs) ? composition.motifs : [];
    const eventIds = new Set();
    for (const track of composition?.tracks || []) {
      for (const event of track.events || []) {
        if (event?.id) {
          eventIds.add(event.id);
        }
      }
    }
    return {
      motifCount: motifs.length,
      labels: motifs.map((motif) => motif.label || null),
      motifs: motifs.map((motif) => ({
        id: motif.id,
        label: motif.label,
        occurrenceCount: (motif.occurrences || []).length,
        relationships: (motif.occurrences || []).map((occ) => occ.relationship),
        occurrenceIds: (motif.occurrences || []).map((occ) => occ.id),
        eventRefsResolvable: (motif.occurrences || []).every(
          (occ) => Array.isArray(occ.event_ids) && occ.event_ids.every((id) => eventIds.has(id)),
        ),
      })),
      eventCount: eventIds.size,
      notationRevision: window.__MUKIT_MUSIC_STORE__?.getState()?.notationRevision ?? null,
      motifApplyStatus: window.__MUKIT_MUSIC_STORE__?.getState()?.motifApplyStatus || 'idle',
      motifApplyError: window.__MUKIT_MUSIC_STORE__?.getState()?.motifApplyError || '',
    };
  });
}

/** Intercept POST /analysis/composition with a deterministic JSON body or status. */
export async function mockAnalysisRoute(page, {
  body = null,
  status = 200,
  once = false,
} = {}) {
  const handler = async (route) => {
    if (status >= 400) {
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body || {
          detail: {
            code: 'analysis_invalid_composition',
            message: 'Mocked analysis failure',
            details: { reason: 'e2e_mock' },
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  };
  if (once) {
    await page.route('**/analysis/composition', async (route) => {
      await handler(route);
      await page.unroute('**/analysis/composition');
    });
  } else {
    await page.route('**/analysis/composition', handler);
  }
}

export function sampleAnalysisReportForE2e(overrides = {}) {
  return {
    schema_version: 'composition.analysis.v1',
    algorithm_version: 'native-v1',
    source_schema_version: 'composition.v2',
    source_fingerprint: 'e2e-fingerprint-0123456789abcdef',
    status: 'ok',
    resolved_scope: {
      kind: 'composition',
      start_tick: 0,
      end_tick: 7680,
      start_bar: 1,
      end_bar_exclusive: 5,
    },
    warnings: [
      {
        code: 'empty_analysis_scope',
        severity: 'warning',
        category: 'data_quality',
        message: 'Mock empty scope warning',
        locator: null,
        details: {},
      },
    ],
    section_summaries: [],
    tonality: {
      status: 'ok',
      global: { label: 'C major', confidence: 0.9 },
    },
    harmony: {
      status: 'ok',
      chord_change_rate: 0.5,
    },
    melody: {
      status: 'ok',
      phrases: [{ cadence: 'authentic' }],
    },
    density: {
      status: 'ok',
      attacks_per_bar: 2,
    },
    ...overrides,
  };
}

/** Acceptance baseline: piano melody + piano accompaniment + bass (composition.v2). */
export function buildArrangementPianoSketchFixture(overrides = {}) {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 7680,
    bar_count: 4,
    sections: [
      {
        id: 'a-section',
        type: 'verse',
        label: 'A',
        start_bar: 1,
        bar_count: 4,
        start_tick: 0,
        duration_ticks: 7680,
      },
    ],
    tracks: [
      {
        id: 'piano-melody',
        name: 'Piano Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          { id: 'm1', pitch: 'E4', start_tick: 0, duration_ticks: 480, velocity: 84 },
          { id: 'm2', pitch: 'G4', start_tick: 480, duration_ticks: 480, velocity: 82 },
          { id: 'm3', pitch: 'C5', start_tick: 960, duration_ticks: 960, velocity: 88 },
          { id: 'm4', pitch: 'D5', start_tick: 1920, duration_ticks: 480, velocity: 80 },
        ],
      },
      {
        id: 'piano-accomp',
        name: 'Piano Accompaniment',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 2,
        events: [
          { id: 'a1', pitch: 'C3', start_tick: 0, duration_ticks: 960, velocity: 64 },
          { id: 'a2', pitch: 'E3', start_tick: 0, duration_ticks: 960, velocity: 60 },
          { id: 'a3', pitch: 'G3', start_tick: 0, duration_ticks: 960, velocity: 60 },
          { id: 'a4', pitch: 'F3', start_tick: 1920, duration_ticks: 960, velocity: 62 },
          { id: 'a5', pitch: 'A3', start_tick: 1920, duration_ticks: 960, velocity: 58 },
        ],
      },
      {
        id: 'bass-1',
        name: 'Bass',
        instrument: 'acoustic bass',
        role: 'bass',
        midi_program: 32,
        channel: 3,
        events: [
          { id: 'b1', pitch: 'C2', start_tick: 0, duration_ticks: 1920, velocity: 70 },
          { id: 'b2', pitch: 'F2', start_tick: 1920, duration_ticks: 1920, velocity: 68 },
        ],
      },
    ],
    harmony: [
      { start_tick: 0, duration_ticks: 1920, chord: 'C' },
      { start_tick: 1920, duration_ticks: 1920, chord: 'F' },
    ],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
    ...overrides,
  };
}

export function arrangementPart(
  partId,
  instrumentId,
  {
    role = null,
    sourceTrackIds = [],
    doublingPolicy = 'none',
  } = {},
) {
  return {
    part_id: partId,
    instrument_id: instrumentId,
    role,
    source_track_ids: sourceTrackIds,
    doubling_policy: doublingPolicy,
  };
}

/** Piano → piano/cello/string-ensemble before/after inventory for acceptance. */
export function arrangementAcceptanceInstrumentation() {
  return {
    before: [
      arrangementPart('b-melody', 'acoustic_grand_piano', {
        role: 'melody',
        sourceTrackIds: ['piano-melody'],
      }),
      arrangementPart('b-accomp', 'acoustic_grand_piano', {
        role: 'harmony',
        sourceTrackIds: ['piano-accomp'],
      }),
      arrangementPart('b-bass', 'acoustic_bass', {
        role: 'bass',
        sourceTrackIds: ['bass-1'],
      }),
    ],
    after: [
      arrangementPart('a-piano', 'acoustic_grand_piano', { role: 'melody' }),
      arrangementPart('a-cello', 'cello', { role: 'bass' }),
      arrangementPart('a-strings', 'string_ensemble_1', { role: 'harmony' }),
    ],
  };
}

/**
 * Per-operation store configs matching fake-provider backend acceptance builders.
 * Returns { operation, sourceTrackIds, protectedTrackIds, instrumentation, allowUnlistedAfter? }.
 */
export function arrangementOperationConfig(operation) {
  const acceptance = arrangementAcceptanceInstrumentation();
  const builders = {
    change_instrumentation: {
      sourceTrackIds: ['piano-melody'],
      protectedTrackIds: [],
      instrumentation: {
        before: [
          arrangementPart('b-melody', 'acoustic_grand_piano', {
            role: 'melody',
            sourceTrackIds: ['piano-melody'],
          }),
        ],
        after: [
          arrangementPart('a-melody', 'violin', { role: 'melody' }),
        ],
      },
    },
    add_accompaniment: {
      sourceTrackIds: ['piano-melody', 'piano-accomp', 'bass-1'],
      protectedTrackIds: [],
      instrumentation: {
        before: acceptance.before,
        after: [
          ...acceptance.before,
          arrangementPart('a-pad', 'synth_pad_new_age', { role: 'pad' }),
        ],
      },
    },
    remove_accompaniment: {
      sourceTrackIds: ['piano-accomp'],
      protectedTrackIds: ['piano-melody'],
      allowUnlistedAfter: true,
      instrumentation: {
        before: [
          arrangementPart('b-accomp', 'acoustic_grand_piano', {
            role: 'harmony',
            sourceTrackIds: ['piano-accomp'],
          }),
        ],
        after: [
          arrangementPart('a-kept', 'acoustic_grand_piano', { role: 'melody' }),
        ],
      },
    },
    orchestrate_selected_tracks: {
      sourceTrackIds: ['piano-melody', 'piano-accomp', 'bass-1'],
      protectedTrackIds: [],
      instrumentation: {
        before: acceptance.before,
        after: [
          arrangementPart('a-v', 'violin', { role: 'melody' }),
          arrangementPart('a-c', 'cello', { role: 'bass' }),
          arrangementPart('a-s', 'string_ensemble_1', { role: 'harmony' }),
        ],
      },
    },
    piano_to_ensemble: {
      sourceTrackIds: ['piano-melody', 'piano-accomp', 'bass-1'],
      protectedTrackIds: [],
      instrumentation: acceptance,
    },
    simplify_arrangement: {
      sourceTrackIds: ['piano-accomp'],
      protectedTrackIds: ['piano-melody'],
      instrumentation: {
        before: [
          arrangementPart('b-a', 'acoustic_grand_piano', {
            role: 'harmony',
            sourceTrackIds: ['piano-accomp'],
          }),
        ],
        after: [
          arrangementPart('a-a', 'acoustic_grand_piano', { role: 'harmony' }),
        ],
      },
    },
    increase_texture_density: {
      sourceTrackIds: ['piano-accomp'],
      protectedTrackIds: [],
      instrumentation: {
        before: [
          arrangementPart('b-a', 'acoustic_grand_piano', {
            role: 'harmony',
            sourceTrackIds: ['piano-accomp'],
          }),
        ],
        after: [
          arrangementPart('a-a', 'acoustic_grand_piano', { role: 'harmony' }),
        ],
      },
    },
    decrease_texture_density: {
      sourceTrackIds: ['piano-accomp'],
      protectedTrackIds: ['piano-melody'],
      instrumentation: {
        before: [
          arrangementPart('b-a', 'acoustic_grand_piano', {
            role: 'harmony',
            sourceTrackIds: ['piano-accomp'],
          }),
        ],
        after: [
          arrangementPart('a-a', 'acoustic_grand_piano', { role: 'harmony' }),
        ],
      },
    },
    create_countermelody: {
      sourceTrackIds: ['piano-melody'],
      protectedTrackIds: [],
      instrumentation: {
        before: [
          arrangementPart('b1', 'acoustic_grand_piano', {
            role: 'melody',
            sourceTrackIds: ['piano-melody'],
          }),
        ],
        after: [
          arrangementPart('a-mel', 'acoustic_grand_piano', { role: 'melody' }),
          arrangementPart('a-cm', 'flute', { role: 'countermelody' }),
        ],
      },
    },
    double_melody: {
      sourceTrackIds: ['piano-melody'],
      protectedTrackIds: [],
      instrumentation: {
        before: [
          arrangementPart('b-m', 'acoustic_grand_piano', {
            role: 'melody',
            sourceTrackIds: ['piano-melody'],
          }),
        ],
        after: [
          arrangementPart('a-m', 'acoustic_grand_piano', { role: 'melody' }),
          arrangementPart('a-d', 'violin', {
            role: 'melody',
            sourceTrackIds: ['piano-melody'],
            doublingPolicy: 'octave',
          }),
        ],
      },
    },
  };
  const config = builders[operation];
  if (!config) {
    throw new Error(`Unknown arrangement operation: ${operation}`);
  }
  return { operation, ...config };
}

/** Create a project and attach a V2 composition fixture via HTTP. */
export async function seedV2ProjectViaApi(request, {
  name = 'Arrangement Seed',
  fixture = null,
} = {}) {
  const composition = fixture || buildArrangementPianoSketchFixture();
  const backendUrl = backendBaseUrl();
  const create = await request.post(`${backendUrl}/projects`, { data: { name } });
  if (!create.ok()) {
    throw new Error(`Failed to create project: ${create.status()} ${await create.text()}`);
  }
  const { id: projectId } = await create.json();
  const patch = await request.patch(`${backendUrl}/projects/${projectId}`, {
    data: { composition },
  });
  if (!patch.ok()) {
    throw new Error(`Failed to seed V2 composition: ${patch.status()} ${await patch.text()}`);
  }
  return {
    projectId,
    fixtureSchemaVersion: composition.schema_version,
    sourceNoteSequences: extractNoteSequences(composition),
    composition,
  };
}

export {
  buildLargeScoreEditorFixture,
  toCanonicalLargeScore,
  largeScoreNoteCount,
};

/** Seed a 100-bar dense score for editor culling / workflow acceptance. */
export async function seedLargeScoreEditorProject(request, {
  name = 'Large Score Editor',
  barCount = 100,
  notesPerBar = 4,
} = {}) {
  const raw = buildLargeScoreEditorFixture({ barCount, notesPerBar });
  const composition = toCanonicalLargeScore(raw);
  const seeded = await seedV2ProjectViaApi(request, { name, fixture: composition });
  return {
    ...seeded,
    noteCount: largeScoreNoteCount(composition),
    barCount: composition.bar_count,
  };
}

export async function enableEditorPerfOnPage(page) {
  await page.evaluate(() => {
    window.__MUKIT_EDITOR_PERF_ENABLE__ = true;
    window.__MUKIT_EDITOR_PERF_API__?.enable?.();
    window.__MUKIT_EDITOR_PERF_API__?.reset?.();
  });
}

export async function getEditorPerfSnapshot(page) {
  return page.evaluate(() => window.__MUKIT_EDITOR_PERF_API__?.snapshot?.() || null);
}

export async function getEditorWorkflowSnapshot(page) {
  return page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    if (!state) {
      return null;
    }
    const composition = state.editedMusicJson;
    const layer = document.querySelector('[data-testid="piano-roll-note-layer"]');
    const renderedAttr = layer?.getAttribute('data-rendered-note-count');
    const renderedDom = document.querySelectorAll('[data-testid="piano-roll-note"]').length;
    return {
      schemaVersion: composition?.schema_version ?? null,
      barCount: composition?.bar_count ?? null,
      eventCount: Array.isArray(composition?.tracks)
        ? composition.tracks.reduce((sum, track) => sum + (track.events?.length || 0), 0)
        : 0,
      compositionRevision: state.compositionRevision,
      selectedCount: Array.isArray(state.editorSelectionRefs) ? state.editorSelectionRefs.length : 0,
      clipboardReady: Boolean(state.editorClipboard),
      editCursorTick: state.editCursorTick,
      playbackStatus: state.playbackStatus,
      playbackLoop: state.playbackLoop
        ? {
          startTick: state.playbackLoop.startTick,
          endTick: state.playbackLoop.endTick,
          enabled: Boolean(state.playbackLoop.enabled),
        }
        : null,
      undoDepth: Array.isArray(state.compositionEditUndoStack)
        ? state.compositionEditUndoStack.length
        : 0,
      redoDepth: Array.isArray(state.compositionEditRedoStack)
        ? state.compositionEditRedoStack.length
        : 0,
      hiddenTrackCount: Array.isArray(state.hiddenTrackIds) ? state.hiddenTrackIds.length : 0,
      lockedTrackCount: Array.isArray(state.lockedTrackIds) ? state.lockedTrackIds.length : 0,
      renderedNoteAttr: renderedAttr == null ? null : Number(renderedAttr),
      renderedNoteDom: renderedDom,
      currentBarValue: document.querySelector('[data-testid="piano-roll-current-bar"]')?.value || null,
    };
  });
}

export async function openPianoRollTab(page) {
  await openComposerTab(page, 'piano');
  await page.getByTestId('piano-roll-grid').waitFor({ state: 'visible', timeout: 30_000 });
}

export async function ensureFakeLlmSelected(page) {
  await page.getByTestId('llm-model-select').waitFor({ state: 'visible', timeout: 30_000 });
  await pickFakeProvider(page);
}

export async function openArrangeTab(page) {
  await openComposerTab(page, 'arrange');
  await page.getByTestId('arrange-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-arrangement] Opened Arrange tab');
}

export async function waitForArrangementCatalog(page, { timeout = 30_000 } = {}) {
  const { expect } = await import('@playwright/test');
  await expect.poll(async () => {
    const snapshot = await getArrangementSnapshot(page);
    return snapshot?.catalogStatus || 'missing';
  }, { timeout }).toBe('ready');
  return getArrangementSnapshot(page);
}

/** Bounded arrangement snapshot (counts/ids/status only — no event arrays). */
export async function getArrangementSnapshot(page) {
  return page.evaluate(() => {
    const state = window.__MUKIT_MUSIC_STORE__?.getState?.();
    if (!state) {
      return null;
    }
    const composition = state.editedMusicJson;
    const tracks = Array.isArray(composition?.tracks) ? composition.tracks : [];
    const melody = tracks.find((track) => track.role === 'melody')
      || tracks.find((track) => track.id === 'piano-melody');
    const selected = (state.arrangementCandidates || [])
      .find((item) => item.candidate_id === state.arrangementSelectedCandidateId) || null;
    const selectedTracks = selected?.composition?.tracks || [];
    const selectedMelody = selectedTracks.find((track) => track.role === 'melody');
    return {
      saveStatus: state.saveStatus,
      compositionRevision: state.compositionRevision,
      schemaVersion: composition?.schema_version ?? null,
      barCount: composition?.bar_count ?? null,
      key: composition?.key ?? null,
      tempo: composition?.tempo ?? null,
      timeSignature: composition?.time_signature ?? null,
      durationTicks: composition?.duration_ticks ?? null,
      trackCount: tracks.length,
      trackSummaries: tracks.map((track) => ({
        id: track.id,
        instrument: track.instrument,
        role: track.role,
        midiProgram: track.midi_program ?? null,
        channel: track.channel ?? null,
        eventCount: Array.isArray(track.events) ? track.events.length : 0,
      })),
      harmonyChords: (composition?.harmony || []).map((span) => span.chord),
      melodyPitches: (melody?.events || []).map((event) => event.pitch),
      undoDepth: Array.isArray(state.compositionEditUndoStack) ? state.compositionEditUndoStack.length : 0,
      redoDepth: Array.isArray(state.compositionEditRedoStack) ? state.compositionEditRedoStack.length : 0,
      arrangementStatus: state.arrangementStatus,
      arrangementError: state.arrangementError || '',
      arrangementStaleReason: state.arrangementStaleReason,
      arrangementWarnings: Array.isArray(state.arrangementWarnings) ? state.arrangementWarnings : [],
      candidateCount: Array.isArray(state.arrangementCandidates)
        ? state.arrangementCandidates.length
        : 0,
      rejectedCount: Array.isArray(state.arrangementRejectedAttempts)
        ? state.arrangementRejectedAttempts.length
        : 0,
      selectedCandidateId: state.arrangementSelectedCandidateId,
      selectedCandidateSuffix: state.arrangementSelectedCandidateId
        ? String(state.arrangementSelectedCandidateId).slice(-8)
        : null,
      auditionMode: state.arrangementAuditionMode,
      instruction: state.arrangementInstruction || '',
      operation: state.arrangementOperation,
      candidateCountControl: state.arrangementCandidateCount,
      catalogStatus: state.arrangementCatalogStatus,
      catalogError: state.arrangementCatalogError || '',
      catalogVersion: state.arrangementCatalog?.catalog_version || null,
      catalogFingerprintPrefix: typeof state.arrangementCatalogFingerprint === 'string'
        ? state.arrangementCatalogFingerprint.slice(0, 12)
        : null,
      provider: state.arrangementProvider,
      selectedCandidateSummary: selected
        ? {
          candidateIdSuffix: String(selected.candidate_id || '').slice(-8),
          operation: selected.operation,
          trackCount: selectedTracks.length,
          trackSummaries: selectedTracks.map((track) => ({
            id: track.id,
            instrument: track.instrument,
            role: track.role,
            midiProgram: track.midi_program ?? null,
            eventCount: Array.isArray(track.events) ? track.events.length : 0,
          })),
          melodyPitches: (selectedMelody?.events || []).map((event) => event.pitch),
          harmonyChords: (selected.composition?.harmony || []).map((span) => span.chord),
          key: selected.composition?.key ?? null,
          tempo: selected.composition?.tempo ?? null,
          timeSignature: selected.composition?.time_signature ?? null,
          durationTicks: selected.composition?.duration_ticks ?? null,
          warningCodes: selected.warning_codes || [],
          densityBefore: selected.density?.before || null,
          densityAfter: selected.density?.after || null,
          manifestAdded: (selected.manifest?.added_track_ids || []).length,
          manifestRemoved: (selected.manifest?.removed_track_ids || []).length,
          duplicateFindingCodes: (selected.duplicate_findings || [])
            .map((item) => item.code)
            .filter(Boolean)
            .slice(0, 16),
          assertionCodes: (selected.assertions || [])
            .map((item) => item.code || item.name)
            .filter(Boolean)
            .slice(0, 24),
        }
        : null,
      candidateIds: (state.arrangementCandidates || []).map((item) => item.candidate_id),
      rejectedCodes: (state.arrangementRejectedAttempts || [])
        .flatMap((attempt) => attempt.codes || [])
        .slice(0, 24),
    };
  });
}

export async function waitForArrangementStatus(page, status, { timeout = 120_000 } = {}) {
  const { expect } = await import('@playwright/test');
  await expect.poll(async () => {
    const snapshot = await getArrangementSnapshot(page);
    if (snapshot?.arrangementStatus === 'error' && status !== 'error') {
      throw new Error(
        `Arrangement preview failed: ${snapshot.arrangementError || 'unknown'}`,
      );
    }
    return snapshot?.arrangementStatus || 'missing';
  }, { timeout }).toBe(status);
  return getArrangementSnapshot(page);
}

/** Configure arrangement request via store (avoids brittle multi-field UI races). */
export async function configureArrangementViaStore(page, config) {
  return page.evaluate((cfg) => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const patch = {
      operation: cfg.operation,
      sourceTrackIds: cfg.sourceTrackIds || [],
      protectedTrackIds: cfg.protectedTrackIds || [],
      instrumentationBefore: cfg.instrumentation?.before || [],
      instrumentationAfter: cfg.instrumentation?.after || [],
      preserveMelody: cfg.preserveMelody !== false,
      preserveHarmony: cfg.preserveHarmony !== false,
      rangeAdjustment: cfg.rangeAdjustment || 'reject',
      candidateCount: cfg.candidateCount || 1,
      instruction: cfg.instruction != null ? cfg.instruction : '',
    };
    if (cfg.allowUnlistedAfter != null) {
      patch.allowUnlistedAfter = Boolean(cfg.allowUnlistedAfter);
    }
    const ok = state.setArrangementControls(patch);
    const after = api.getState();
    return {
      ok: Boolean(ok),
      operation: after.arrangementOperation,
      sourceCount: after.arrangementSourceTrackIds.length,
      afterPartCount: after.arrangementInstrumentationAfter.length,
      candidateCount: after.arrangementCandidateCount,
      catalogStatus: after.arrangementCatalogStatus,
    };
  }, config);
}

/** Intercept POST /composition/arrangement/preview with a deterministic body or error. */
export async function mockArrangementPreviewRoute(page, {
  body = null,
  status = 200,
  once = false,
  capture = null,
} = {}) {
  const handler = async (route) => {
    const request = route.request();
    if (capture && typeof capture === 'object') {
      try {
        capture.payload = request.postDataJSON();
      } catch {
        capture.raw = request.postData();
      }
    }
    if (status >= 400) {
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body || {
          detail: {
            code: 'arrangement_provider_unavailable',
            message: 'Mocked arrangement preview failure',
            details: { reason: 'e2e_mock' },
          },
        }),
      });
      return;
    }
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  };
  if (once) {
    await page.route('**/composition/arrangement/preview', async (route) => {
      await handler(route);
      await page.unroute('**/composition/arrangement/preview');
    });
  } else {
    await page.route('**/composition/arrangement/preview', handler);
  }
}

export async function editArrangementSourceNoteViaStore(page, {
  trackId = 'piano-melody',
  noteIndex = 0,
  nextPitch = 'F4',
} = {}) {
  return page.evaluate(({ preferredTrackId, idx, pitch }) => {
    const api = window.__MUKIT_MUSIC_STORE__;
    if (!api) {
      return { ok: false, reason: 'store missing' };
    }
    const state = api.getState();
    const track = (state.editedMusicJson?.tracks || []).find((item) => item.id === preferredTrackId)
      || (state.editedMusicJson?.tracks || [])[0];
    if (!track?.events?.length) {
      return { ok: false, reason: 'no events' };
    }
    const note = track.events[idx] || track.events[0];
    const updated = state.updateNote(track.id, note.id, { pitch });
    return {
      ok: Boolean(updated),
      trackId: track.id,
      noteId: note.id,
      beforePitch: note.pitch,
      afterPitch: pitch,
      compositionRevision: api.getState().compositionRevision,
    };
  }, { preferredTrackId: trackId, idx: noteIndex, pitch: nextPitch });
}
