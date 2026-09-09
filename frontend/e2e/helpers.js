/** Shared helpers for V1/V2 Playwright journeys. */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

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
  await waitForCompositionNotes(page, { minEvents, timeout: 90_000 });
  const snapshot = await getStoreSnapshot(page);
  console.info('[FIX:e2e-fake-generate] Generate completed', {
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

export async function openMotifsTab(page) {
  await page.getByTestId('composer-tab-motifs').click();
  await page.getByTestId('motif-panel').waitFor({ state: 'visible', timeout: 30_000 });
  console.info('[e2e-motifs] Opened Motifs tab');
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
