import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import {
  ARRANGEMENT_AUDITION_CANDIDATE,
  ARRANGEMENT_AUDITION_SOURCE,
  useMusicStore,
} from './musicStore.js';
import { compositionEditFingerprint } from '../utils/compositionCandidates.js';
import {
  ARRANGEMENT_ALGORITHM_VERSION,
  ARRANGEMENT_CATALOG_VERSION,
  ARRANGEMENT_RANGE_POLICY_VERSION,
  cacheArrangementCatalog,
  clearArrangementCatalogCache,
  normalizeArrangementCatalog,
} from '../utils/compositionArrangementCandidates.js';
import { SUPPORTED_TRACK_ROLES } from '../utils/musicJsonValidation.js';
import { setAppLogLevelForTests } from '../utils/appLogger.js';

function installAxiosStub(handler) {
  const previous = axios.defaults.adapter;
  axios.defaults.adapter = handler;
  return () => {
    axios.defaults.adapter = previous;
  };
}

function note(id, pitch, start, duration = 480) {
  return { id, pitch, start_tick: start, duration_ticks: duration, velocity: 80 };
}

function track(partial) {
  return {
    name: partial.id,
    instrument: 'piano',
    role: 'melody',
    midi_program: 0,
    channel: 1,
    is_drum: false,
    volume: 100,
    pan: 0,
    expression: 127,
    events: [],
    dynamic_marks: [],
    sustain_pedals: [],
    automation: [],
    ...partial,
  };
}

function arrangementSource() {
  return {
    schema_version: 'composition.v2',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 3840,
    bar_count: 2,
    sections: [{
      id: 'a',
      type: 'verse',
      label: 'A',
      start_bar: 1,
      bar_count: 2,
      start_tick: 0,
      duration_ticks: 3840,
    }],
    tracks: [
      track({
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [note('m1', 'C4', 0), note('m2', 'E4', 1920)],
      }),
      track({
        id: 'harmony-1',
        name: 'Accomp',
        instrument: 'piano',
        role: 'harmony',
        midi_program: 0,
        channel: 2,
        events: [note('h1', 'C3', 0, 1920), note('h2', 'G3', 1920, 1920)],
      }),
      track({
        id: 'bass-1',
        name: 'Bass',
        instrument: 'acoustic_bass',
        role: 'bass',
        midi_program: 32,
        channel: 3,
        events: [note('b1', 'C2', 0, 3840)],
      }),
    ],
    harmony: [{ chord: 'C', start_tick: 0, duration_ticks: 3840 }],
    tempo_changes: [],
    time_signature_changes: [],
    key_changes: [],
    markers: [],
    motifs: [],
  };
}

function inventoryFrom(composition) {
  return (composition.tracks || []).map((item) => ({
    track_id: item.id,
    instrument: item.instrument,
    role: item.role,
    midi_program: item.midi_program ?? null,
    event_count: (item.events || []).length,
    part_id: null,
  }));
}

function sampleCatalog(fingerprint = 'f'.repeat(64)) {
  return normalizeArrangementCatalog({
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    fingerprint,
    source_path_category: 'packaged',
    instruments: [
      {
        instrument_id: 'acoustic_grand_piano',
        display_name: 'Acoustic Grand Piano',
        aliases: ['piano'],
        midi_program: 0,
        gm_family: 'piano',
        compatibility_identity: 'piano',
        compatibility_family: 'keyboard',
        is_drum: false,
        range_policy: 'absolute',
        playable_low: 21,
        playable_high: 108,
        preferred_low: 36,
        preferred_high: 96,
        suggested_roles: ['melody', 'harmony'],
        fingerprint: 'p'.repeat(64),
      },
      {
        instrument_id: 'cello',
        display_name: 'Cello',
        aliases: [],
        midi_program: 42,
        gm_family: 'strings',
        compatibility_identity: 'cello',
        compatibility_family: 'strings',
        is_drum: false,
        range_policy: 'absolute',
        playable_low: 36,
        playable_high: 84,
        preferred_low: 36,
        preferred_high: 72,
        suggested_roles: ['melody', 'bass', 'harmony'],
        fingerprint: 'c'.repeat(64),
      },
      {
        instrument_id: 'acoustic_bass',
        display_name: 'Acoustic Bass',
        aliases: ['bass'],
        midi_program: 32,
        gm_family: 'bass',
        compatibility_identity: 'acoustic_bass',
        compatibility_family: 'bass',
        is_drum: false,
        range_policy: 'absolute',
        playable_low: 28,
        playable_high: 60,
        preferred_low: 28,
        preferred_high: 55,
        suggested_roles: ['bass'],
        fingerprint: 'b'.repeat(64),
      },
    ],
    track_roles: [...SUPPORTED_TRACK_ROLES],
  }).catalog;
}

function baseParts() {
  return {
    before: [
      {
        part_id: 'p-melody',
        instrument_id: 'acoustic_grand_piano',
        role: 'melody',
        source_track_ids: ['melody-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-harmony',
        instrument_id: 'acoustic_grand_piano',
        role: 'harmony',
        source_track_ids: ['harmony-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-bass',
        instrument_id: 'acoustic_bass',
        role: 'bass',
        source_track_ids: ['bass-1'],
        doubling_policy: 'none',
      },
    ],
    after: [
      {
        part_id: 'p-melody',
        instrument_id: 'acoustic_grand_piano',
        role: 'melody',
        source_track_ids: ['melody-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-harmony',
        instrument_id: 'acoustic_grand_piano',
        role: 'harmony',
        source_track_ids: ['harmony-1'],
        doubling_policy: 'none',
      },
      {
        part_id: 'p-bass',
        instrument_id: 'cello',
        role: 'bass',
        source_track_ids: ['bass-1'],
        doubling_policy: 'none',
      },
    ],
  };
}

async function buildCandidate(source, {
  candidateId = 'arr-cand-aaaa1111',
  catalogFingerprint = 'f'.repeat(64),
  mutate,
} = {}) {
  const composition = structuredClone(source);
  if (typeof mutate === 'function') {
    mutate(composition);
  } else {
    const bass = composition.tracks.find((item) => item.id === 'bass-1');
    bass.instrument = 'cello';
    bass.midi_program = 42;
    bass.name = 'Cello';
  }
  const sourceFp = await compositionEditFingerprint(source);
  const candidateFp = await compositionEditFingerprint(composition);
  const removed = source.tracks
    .map((item) => item.id)
    .filter((id) => !composition.tracks.some((trackItem) => trackItem.id === id));
  const added = composition.tracks
    .map((item) => item.id)
    .filter((id) => !source.tracks.some((trackItem) => trackItem.id === id));
  const retained = composition.tracks
    .map((item) => item.id)
    .filter((id) => source.tracks.some((trackItem) => trackItem.id === id));
  const reinstrumented = [];
  for (const id of retained) {
    const before = source.tracks.find((item) => item.id === id);
    const after = composition.tracks.find((item) => item.id === id);
    if (
      before.instrument !== after.instrument
      || before.midi_program !== after.midi_program
    ) {
      reinstrumented.push(id);
    }
  }
  return {
    candidate_id: candidateId,
    candidate_fingerprint: candidateFp,
    edit_source_fingerprint: sourceFp,
    algorithm_version: ARRANGEMENT_ALGORITHM_VERSION,
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: catalogFingerprint,
    target_profile_fingerprints: [
      { instrument_id: 'cello', profile_fingerprint: 'c'.repeat(64) },
    ],
    operation: 'change_instrumentation',
    composition,
    provider: 'fake',
    model: 'fake-deterministic',
    before_inventory: inventoryFrom(source),
    after_inventory: inventoryFrom(composition),
    manifest: {
      retained_track_ids: retained.filter((id) => !reinstrumented.includes(id)),
      removed_track_ids: removed,
      added_track_ids: added,
      reordered_track_ids: [],
      reinstrumented_track_ids: reinstrumented,
      split_track_ids: [],
      merged_track_ids: [],
      source_to_target: [
        ...retained.map((id) => ({
          source_track_id: id,
          target_track_id: id,
          relationship: reinstrumented.includes(id) ? 'reinstrumented' : 'retained',
        })),
        ...removed.map((id) => ({
          source_track_id: id,
          target_track_id: id,
          relationship: 'removed',
        })),
      ],
    },
    event_counts: {
      copied: 5,
      moved: 0,
      generated: 0,
      removed: 0,
      octave_adjusted: 0,
      unchanged: 5,
    },
    density: null,
    range_findings: [],
    duplicate_findings: [],
    harmony_compatibility: null,
    assertions: [
      {
        kind: 'melody_preservation',
        satisfied: true,
        required: true,
        detail: 'ok',
        track_id: null,
      },
      {
        kind: 'topology_authorization',
        satisfied: true,
        required: true,
        detail: 'ok',
        track_id: null,
      },
    ],
    warning_codes: [],
  };
}

async function previewResponse(source, candidates, overrides = {}) {
  const sourceFp = await compositionEditFingerprint(source);
  return {
    edit_source_fingerprint: sourceFp,
    algorithm_version: ARRANGEMENT_ALGORITHM_VERSION,
    catalog_version: ARRANGEMENT_CATALOG_VERSION,
    range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
    catalog_fingerprint: 'f'.repeat(64),
    operation: 'change_instrumentation',
    requested_candidate_count: candidates.length,
    candidates,
    rejected_attempts: [],
    warning_codes: [],
    provider: 'fake',
    model: 'fake-deterministic',
    ...overrides,
  };
}

function resetStore(composition, extras = {}) {
  const catalog = sampleCatalog();
  clearArrangementCatalogCache();
  cacheArrangementCatalog(catalog);
  useMusicStore.setState({
    editedMusicJson: structuredClone(composition),
    compositionRevision: 'rev-base',
    notationRevision: 'notation-base',
    editCursorTick: 0,
    compositionEditUndoStack: [],
    compositionEditRedoStack: [],
    trackControls: {
      'melody-1': { muted: false, solo: false, volumeMidi: 100 },
      'harmony-1': { muted: true, solo: false, volumeMidi: 64 },
      'bass-1': { muted: false, solo: true, volumeMidi: 90 },
    },
    pianoRollTrackId: 'bass-1',
    pianoRollNoteId: 'b1',
    pianoRollNoteIds: ['b1'],
    currentProjectId: null,
    saveStatus: 'saved',
    arrangementOperation: 'change_instrumentation',
    arrangementSourceTrackIds: ['melody-1', 'harmony-1', 'bass-1'],
    arrangementProtectedTrackIds: [],
    arrangementInstrumentationBefore: baseParts().before,
    arrangementInstrumentationAfter: baseParts().after,
    arrangementAllowUnlistedAfter: false,
    arrangementPreserveMelody: true,
    arrangementPreserveHarmony: true,
    arrangementRangeAdjustment: 'reject',
    arrangementCandidateCount: 2,
    arrangementInstruction: '',
    arrangementCatalog: catalog,
    arrangementCatalogStatus: 'ready',
    arrangementCatalogError: '',
    arrangementCatalogFingerprint: catalog.fingerprint,
    arrangementStatus: 'idle',
    arrangementError: '',
    arrangementStaleReason: null,
    arrangementWarnings: [],
    arrangementRequestId: 0,
    arrangementBaseRevision: null,
    arrangementEditSourceFingerprint: null,
    arrangementResponseCatalogFingerprint: null,
    arrangementControlsFingerprint: null,
    arrangementCandidates: [],
    arrangementRejectedAttempts: [],
    arrangementSelectedCandidateId: null,
    arrangementAuditionMode: ARRANGEMENT_AUDITION_SOURCE,
    arrangementCandidateTrackControls: {},
    arrangementProvider: null,
    arrangementModel: null,
    developmentStatus: 'idle',
    developmentCandidates: [],
    developmentSelectedCandidateId: null,
    developmentAuditionActive: false,
    reharmonizeStatus: 'idle',
    reharmonizeCandidate: null,
    selectedProvider: 'fake',
    selectedModel: 'fake-deterministic',
    analysisStatus: 'idle',
    analysisResult: null,
    musicXml: 'old-xml',
    pianoRollNotationStatus: 'idle',
    ...extras,
  });
}

test('arrangement preview is ephemeral and ignores superseded races', async (t) => {
  const source = arrangementSource();
  const candA = await buildCandidate(source, { candidateId: 'arr-cand-one11111' });
  const candB = await buildCandidate(source, { candidateId: 'arr-cand-two22222' });
  let resolveFirst;
  const firstPromise = new Promise((resolve) => {
    resolveFirst = resolve;
  });
  let callCount = 0;
  const restore = installAxiosStub(async (config) => {
    assert.equal(config.url, '/composition/arrangement/preview');
    callCount += 1;
    if (callCount === 1) {
      await firstPromise;
      return {
        data: await previewResponse(source, [candA]),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return {
      data: await previewResponse(source, [candB]),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);
  resetStore(source);

  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const undoLen = useMusicStore.getState().compositionEditUndoStack.length;
  const first = useMusicStore.getState().startArrangementPreview();
  const secondOk = await useMusicStore.getState().startArrangementPreview();
  assert.equal(secondOk, true);
  assert.equal(useMusicStore.getState().arrangementSelectedCandidateId, 'arr-cand-two22222');
  resolveFirst();
  const firstOk = await first;
  assert.equal(firstOk, false);
  assert.equal(useMusicStore.getState().arrangementSelectedCandidateId, 'arr-cand-two22222');
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, undoLen);
  assert.equal(useMusicStore.getState().saveStatus, 'saved');
});

test('discard invalidates in-flight preview without mutating composition', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  let resolvePreview;
  const previewPromise = new Promise((resolve) => {
    resolvePreview = resolve;
  });
  const restore = installAxiosStub(async (config) => {
    await previewPromise;
    return {
      data: await previewResponse(source, [candidate]),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);
  resetStore(source);
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const pending = useMusicStore.getState().startArrangementPreview();
  useMusicStore.getState().discardArrangementCandidates();
  resolvePreview();
  const ok = await pending;
  assert.equal(ok, false);
  assert.equal(useMusicStore.getState().arrangementStatus, 'idle');
  assert.equal(useMusicStore.getState().arrangementCandidates.length, 0);
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
});

test('settings and catalog fingerprint changes mark retained previews stale', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  let catalogCalls = 0;
  const restore = installAxiosStub(async (config) => {
    if (config.url === '/composition/arrangement/instruments') {
      catalogCalls += 1;
      return {
        data: {
          catalog_version: ARRANGEMENT_CATALOG_VERSION,
          range_policy_version: ARRANGEMENT_RANGE_POLICY_VERSION,
          fingerprint: 'e'.repeat(64),
          source_path_category: 'packaged',
          instruments: sampleCatalog('e'.repeat(64)).instruments,
          track_roles: [...SUPPORTED_TRACK_ROLES],
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return {
      data: await previewResponse(source, [candidate]),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);
  resetStore(source);
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  assert.equal(useMusicStore.getState().arrangementStatus, 'ready');

  useMusicStore.getState().setArrangementControls({ candidateCount: 1 });
  assert.equal(useMusicStore.getState().arrangementStatus, 'stale');
  assert.equal(useMusicStore.getState().arrangementStaleReason, 'settings_changed');
  assert.equal(useMusicStore.getState().arrangementCandidates.length, 1);

  useMusicStore.setState({
    arrangementStatus: 'ready',
    arrangementStaleReason: null,
    arrangementError: '',
    arrangementResponseCatalogFingerprint: 'f'.repeat(64),
    arrangementCatalogFingerprint: 'f'.repeat(64),
  });
  clearArrangementCatalogCache();
  await useMusicStore.getState().loadArrangementCatalog({ forceRefresh: true });
  assert.ok(catalogCalls >= 1);
  assert.equal(useMusicStore.getState().arrangementStatus, 'stale');
  assert.equal(useMusicStore.getState().arrangementStaleReason, 'catalog_changed');
});

test('candidate selection and audition isolate mixer from source controls', async (t) => {
  const source = arrangementSource();
  const withExtra = await buildCandidate(source, {
    candidateId: 'arr-cand-extra999',
    mutate: (composition) => {
      composition.tracks = composition.tracks.filter((item) => item.id !== 'harmony-1');
      composition.tracks.push(track({
        id: 'pad-new',
        name: 'Pad',
        instrument: 'strings',
        role: 'pad',
        midi_program: 48,
        channel: 4,
        events: [note('p1', 'G3', 0, 3840)],
      }));
    },
  });
  const restore = installAxiosStub(async (config) => ({
    data: await previewResponse(source, [withExtra]),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);
  resetStore(source);
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  const sourceControlsBefore = structuredClone(useMusicStore.getState().trackControls);

  useMusicStore.getState().selectArrangementCandidate('arr-cand-extra999');
  assert.equal(useMusicStore.getState().arrangementAuditionMode, ARRANGEMENT_AUDITION_SOURCE);
  assert.ok(useMusicStore.getState().setArrangementAuditionMode(ARRANGEMENT_AUDITION_CANDIDATE));
  useMusicStore.getState().syncArrangementCandidateTrackControls(withExtra.composition);
  useMusicStore.getState().toggleArrangementCandidateMute('pad-new');
  useMusicStore.getState().setArrangementCandidateVolume('pad-new', 40);

  assert.deepEqual(useMusicStore.getState().trackControls, sourceControlsBefore);
  assert.equal(useMusicStore.getState().trackControls['harmony-1'].muted, true);
  assert.equal(
    useMusicStore.getState().arrangementCandidateTrackControls['pad-new'].muted,
    true,
  );
  assert.equal(
    useMusicStore.getState().arrangementCandidateTrackControls['pad-new'].volumeMidi,
    40,
  );
  assert.equal(useMusicStore.getState().editedMusicJson.tracks.length, 3);
});

test('fingerprint tamper rejection blocks apply without history mutation', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  candidate.candidate_fingerprint = '0'.repeat(64);
  const restore = installAxiosStub(async (config) => ({
    data: await previewResponse(source, [candidate]),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(restore);
  resetStore(source);
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const undoLen = useMusicStore.getState().compositionEditUndoStack.length;
  assert.equal(await useMusicStore.getState().applySelectedArrangementCandidate(), false);
  assert.equal(useMusicStore.getState().arrangementStatus, 'error');
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, undoLen);
});

test('apply/undo/redo restores topology and mixer; autosave only after apply', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source, {
    candidateId: 'arr-cand-apply0001',
    mutate: (composition) => {
      composition.tracks = composition.tracks.filter((item) => item.id !== 'harmony-1');
      const bass = composition.tracks.find((item) => item.id === 'bass-1');
      bass.instrument = 'cello';
      bass.midi_program = 42;
      composition.tracks.push(track({
        id: 'strings-new',
        name: 'Strings',
        instrument: 'strings',
        role: 'pad',
        midi_program: 48,
        channel: 4,
        events: [note('s1', 'E3', 0, 3840)],
      }));
    },
  });

  let patchCount = 0;
  const restore = installAxiosStub(async (config) => {
    if (config.url === '/composition/arrangement/preview') {
      return {
        data: await previewResponse(source, [candidate]),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.url || '').includes('/projects/')) {
      patchCount += 1;
      return {
        data: {
          id: 'proj-1',
          name: 'Demo',
          composition: useMusicStore.getState().editedMusicJson,
          updated_at: '2026-01-01T00:00:00Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.url || '').includes('/render') || String(config.url || '').includes('musicxml')) {
      return {
        data: { musicxml: '<score/>', warnings: [] },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return {
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  resetStore(source, {
    currentProjectId: 'proj-1',
    currentProjectName: 'Demo',
    lastSavedPersistRevision: 'saved-before',
    saveStatus: 'saved',
    developmentStatus: 'ready',
    developmentCandidates: [{ candidate_id: 'dev-x' }],
    pianoRollTrackId: 'harmony-1',
    pianoRollNoteId: 'h1',
    pianoRollNoteIds: ['h1'],
  });

  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  assert.equal(patchCount, 0);
  assert.equal(useMusicStore.getState().saveStatus, 'saved');

  const applied = await useMusicStore.getState().applySelectedArrangementCandidate();
  assert.equal(applied, true);
  assert.equal(useMusicStore.getState().compositionEditUndoStack.length, 1);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks.some((item) => item.id === 'harmony-1'), false);
  assert.ok(useMusicStore.getState().editedMusicJson.tracks.some((item) => item.id === 'strings-new'));
  assert.equal(useMusicStore.getState().pianoRollTrackId, 'melody-1');
  assert.equal(useMusicStore.getState().pianoRollNoteId, null);
  assert.equal(useMusicStore.getState().trackControls['harmony-1'], undefined);
  assert.ok(useMusicStore.getState().trackControls['strings-new']);
  assert.equal(useMusicStore.getState().trackControls['bass-1'].volumeMidi, 90);
  assert.equal(useMusicStore.getState().trackControls['bass-1'].solo, true);
  assert.equal(useMusicStore.getState().arrangementStatus, 'idle');
  assert.equal(useMusicStore.getState().developmentStatus, 'idle');
  assert.equal(useMusicStore.getState().saveStatus, 'unsaved');

  assert.equal(useMusicStore.getState().undoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks.length, 3);
  assert.ok(useMusicStore.getState().editedMusicJson.tracks.some((item) => item.id === 'harmony-1'));
  assert.equal(useMusicStore.getState().trackControls['harmony-1'].muted, true);
  assert.equal(useMusicStore.getState().trackControls['harmony-1'].volumeMidi, 64);
  assert.equal(useMusicStore.getState().trackControls['strings-new'], undefined);
  assert.equal(useMusicStore.getState().pianoRollTrackId, 'harmony-1');

  assert.equal(useMusicStore.getState().redoCompositionEdit(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks.some((item) => item.id === 'strings-new'), true);
  assert.ok(useMusicStore.getState().trackControls['strings-new']);
});

test('project hydration fully resets arrangement preview state', async (t) => {
  const source = arrangementSource();
  resetStore(source, {
    arrangementStatus: 'ready',
    arrangementCandidates: [{ candidate_id: 'arr-x' }],
    arrangementSelectedCandidateId: 'arr-x',
    arrangementAuditionMode: ARRANGEMENT_AUDITION_CANDIDATE,
    arrangementCandidateTrackControls: { x: { muted: true, solo: false, volumeMidi: 1 } },
    arrangementInstruction: 'temporary instruction',
  });
  const restore = installAxiosStub(async (config) => {
    assert.match(String(config.url || ''), /\/projects\/proj-hydrate/);
    return {
      data: {
        id: 'proj-hydrate',
        name: 'Hydrated',
        composition: source,
        updated_at: '2026-01-01T00:00:00Z',
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);
  await useMusicStore.getState().openProject('proj-hydrate');
  const state = useMusicStore.getState();
  assert.equal(state.arrangementStatus, 'idle');
  assert.equal(state.arrangementCandidates.length, 0);
  assert.equal(state.arrangementSelectedCandidateId, null);
  assert.equal(state.arrangementAuditionMode, ARRANGEMENT_AUDITION_SOURCE);
  assert.deepEqual(state.arrangementCandidateTrackControls, {});
  assert.equal(state.arrangementInstruction, '');
});

test('arrangement preview state is not included in project save payload', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  let savedPayload = null;
  const restore = installAxiosStub(async (config) => {
    if (config.url === '/composition/arrangement/preview') {
      return {
        data: await previewResponse(source, [candidate]),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.method || 'get').toLowerCase() === 'patch') {
      savedPayload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
      return {
        data: {
          id: 'proj-2',
          name: 'Save',
          composition: source,
          updated_at: '2026-01-01T00:00:00Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);
  resetStore(source, {
    currentProjectId: 'proj-2',
    lastSavedPersistRevision: 'dirty',
    saveStatus: 'unsaved',
    generationMeta: null,
  });
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  await useMusicStore.getState().saveCurrentProject({ reason: 'manual' });
  assert.ok(savedPayload);
  assert.equal(Object.prototype.hasOwnProperty.call(savedPayload, 'arrangementCandidates'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(savedPayload, 'arrangementStatus'), false);
  assert.ok(savedPayload.composition);
  assert.equal(savedPayload.composition.schema_version, 'composition.v2');
});

test('controlled logger omits musical payloads for arrangement lifecycle', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  const restore = installAxiosStub(async (config) => ({
    data: await previewResponse(source, [candidate]),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(() => {
    restore();
    setAppLogLevelForTests(null);
  });
  const lines = [];
  const originalInfo = console.info;
  const originalDebug = console.debug;
  console.info = (...args) => {
    lines.push(JSON.stringify(args));
  };
  console.debug = (...args) => {
    lines.push(JSON.stringify(args));
  };
  setAppLogLevelForTests('debug');
  resetStore(source);
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  useMusicStore.getState().selectArrangementCandidate(candidate.candidate_id);
  useMusicStore.getState().setArrangementAuditionMode(ARRANGEMENT_AUDITION_CANDIDATE);
  useMusicStore.getState().discardArrangementCandidates();
  console.info = originalInfo;
  console.debug = originalDebug;
  const joined = lines.join('\n');
  assert.equal(joined.includes('C4'), false);
  assert.equal(joined.includes('arrange this for cello'), false);
  assert.equal(joined.includes('"events"'), false);
});

test('arrangement logger silent gate emits nothing; debug permits metadata only', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  const restore = installAxiosStub(async (config) => ({
    data: await previewResponse(source, [candidate]),
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  }));
  t.after(() => {
    restore();
    setAppLogLevelForTests(null);
  });
  const lines = [];
  const originalInfo = console.info;
  const originalDebug = console.debug;
  const originalWarn = console.warn;
  const originalError = console.error;
  console.info = (...args) => { lines.push(JSON.stringify(args)); };
  console.debug = (...args) => { lines.push(JSON.stringify(args)); };
  console.warn = (...args) => { lines.push(JSON.stringify(args)); };
  console.error = (...args) => { lines.push(JSON.stringify(args)); };

  resetStore(source, { arrangementInstruction: 'do not leak this C4 instruction' });
  setAppLogLevelForTests('silent');
  lines.length = 0;
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  useMusicStore.getState().selectArrangementCandidate(candidate.candidate_id);
  const arrangementSilent = lines.filter((line) => line.includes('[musicStore.arrangement]'));
  assert.equal(arrangementSilent.length, 0);

  setAppLogLevelForTests('debug');
  lines.length = 0;
  useMusicStore.getState().setArrangementAuditionMode(ARRANGEMENT_AUDITION_CANDIDATE);
  useMusicStore.getState().discardArrangementCandidates();
  console.info = originalInfo;
  console.debug = originalDebug;
  console.warn = originalWarn;
  console.error = originalError;

  const arrangementLines = lines.filter((line) => line.includes('[musicStore.arrangement]'));
  assert.ok(arrangementLines.length > 0);
  const joined = arrangementLines.join('\n');
  assert.equal(joined.includes('do not leak'), false);
  assert.equal(joined.includes('C4'), false);
  assert.equal(joined.includes('"events"'), false);
  assert.match(joined, /operation|candidateCount|status|revision|requestId/i);
});

test('API 422/502/503 preview failures stay non-mutating', async () => {
  const source = arrangementSource();
  const cases = [
    { status: 422, code: 'arrangement_inventory_mismatch', message: 'Inventory mismatch' },
    { status: 502, code: 'arrangement_candidate_exhausted', message: 'No candidates' },
    { status: 503, code: 'arrangement_provider_unavailable', message: 'Provider unavailable' },
  ];

  for (const scenario of cases) {
    const restore = installAxiosStub(async () => {
      const error = new Error('Request failed');
      error.isAxiosError = true;
      error.response = {
        status: scenario.status,
        data: {
          detail: {
            code: scenario.code,
            message: scenario.message,
            details: { status: scenario.status },
          },
        },
      };
      throw error;
    });
    resetStore(source);
    const before = structuredClone(useMusicStore.getState().editedMusicJson);
    const undoLen = useMusicStore.getState().compositionEditUndoStack.length;
    const saveStatus = useMusicStore.getState().saveStatus;
    assert.equal(await useMusicStore.getState().startArrangementPreview(), false);
    assert.equal(useMusicStore.getState().arrangementStatus, 'error');
    assert.match(useMusicStore.getState().arrangementError, new RegExp(scenario.message.slice(0, 8)));
    assert.equal(useMusicStore.getState().arrangementCandidates.length, 0);
    assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
    assert.equal(useMusicStore.getState().compositionEditUndoStack.length, undoLen);
    assert.equal(useMusicStore.getState().saveStatus, saveStatus);
    restore();
  }
});

test('source revision change marks in-flight preview stale without apply', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source);
  let resolvePreview;
  const previewPromise = new Promise((resolve) => {
    resolvePreview = resolve;
  });
  const restore = installAxiosStub(async (config) => {
    await previewPromise;
    return {
      data: await previewResponse(source, [candidate]),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);
  resetStore(source);
  const before = structuredClone(useMusicStore.getState().editedMusicJson);
  const pending = useMusicStore.getState().startArrangementPreview();
  useMusicStore.setState({ compositionRevision: 'rev-changed-midflight' });
  resolvePreview();
  assert.equal(await pending, false);
  assert.equal(useMusicStore.getState().arrangementStatus, 'stale');
  assert.equal(useMusicStore.getState().arrangementStaleReason, 'source_changed');
  assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
});

test('generation and import fully clear arrangement candidate state', async () => {
  const source = arrangementSource();
  resetStore(source, {
    arrangementStatus: 'ready',
    arrangementCandidates: [{ candidate_id: 'arr-x', composition: source }],
    arrangementSelectedCandidateId: 'arr-x',
    arrangementAuditionMode: ARRANGEMENT_AUDITION_CANDIDATE,
    arrangementCandidateTrackControls: { x: { muted: true, solo: false, volumeMidi: 1 } },
    arrangementInstruction: 'temp',
  });

  await useMusicStore.getState().startGeneration();
  await useMusicStore.getState().completeGeneration({
    music: source,
    musicxml: '<score/>',
    warnings: [],
    provider: 'fake',
    model: 'fake',
  });
  await useMusicStore.getState().applyGenerationCandidate();
  let state = useMusicStore.getState();
  assert.equal(state.arrangementStatus, 'idle');
  assert.equal(state.arrangementCandidates.length, 0);
  assert.equal(state.arrangementSelectedCandidateId, null);
  assert.equal(state.arrangementInstruction, '');

  useMusicStore.setState({
    arrangementStatus: 'ready',
    arrangementCandidates: [{ candidate_id: 'arr-y' }],
    arrangementSelectedCandidateId: 'arr-y',
    arrangementInstruction: 'again',
  });
  assert.equal(useMusicStore.getState().completeImport({
    composition: source,
    musicxml: '<score/>',
    import_report: { status: 'ok', issues: [] },
  }), true);
  state = useMusicStore.getState();
  assert.equal(state.arrangementStatus, 'idle');
  assert.equal(state.arrangementCandidates.length, 0);
  assert.equal(state.arrangementSelectedCandidateId, null);
  assert.deepEqual(state.arrangementCandidateTrackControls, {});
});

test('apply refreshes notation and strips arrangement keys from save payloads', async (t) => {
  const source = arrangementSource();
  const candidate = await buildCandidate(source, {
    candidateId: 'arr-cand-notation01',
    mutate: (composition) => {
      const bass = composition.tracks.find((item) => item.id === 'bass-1');
      bass.instrument = 'cello';
      bass.midi_program = 42;
    },
  });
  let savedPayload = null;
  let renderCalls = 0;
  const restore = installAxiosStub(async (config) => {
    if (config.url === '/composition/arrangement/preview') {
      return {
        data: await previewResponse(source, [candidate]),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.url || '').includes('/render') || String(config.url || '').includes('musicxml')) {
      renderCalls += 1;
      return {
        data: '<score-applied/>',
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.method || 'get').toLowerCase() === 'patch') {
      savedPayload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
      return {
        data: {
          id: 'proj-notation',
          name: 'Notation',
          composition: useMusicStore.getState().editedMusicJson,
          updated_at: '2026-01-01T00:00:00Z',
        },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetStore(source, {
    currentProjectId: 'proj-notation',
    currentProjectName: 'Notation',
    lastSavedPersistRevision: 'dirty',
    saveStatus: 'unsaved',
    generationMeta: null,
    musicXml: 'old-xml',
  });
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  assert.equal(await useMusicStore.getState().applySelectedArrangementCandidate(), true);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(renderCalls >= 1);
  assert.equal(useMusicStore.getState().musicXml, '<score-applied/>');

  await useMusicStore.getState().saveCurrentProject({ reason: 'manual' });
  assert.ok(savedPayload);
  for (const key of [
    'arrangementCandidates',
    'arrangementStatus',
    'arrangementSelectedCandidateId',
    'arrangementAuditionMode',
    'arrangementCandidateTrackControls',
    'arrangementInstruction',
    'arrangementRejectedAttempts',
  ]) {
    assert.equal(Object.prototype.hasOwnProperty.call(savedPayload, key), false, key);
  }
  assert.equal(savedPayload.composition.schema_version, 'composition.v2');
  assert.equal(
    savedPayload.composition.tracks.find((item) => item.id === 'bass-1').instrument,
    'cello',
  );
});

test('all ten operations preview without mutating working composition', async (t) => {
  const source = arrangementSource();
  const catalog = sampleCatalog();
  const ops = [
    {
      operation: 'change_instrumentation',
      source_track_ids: ['bass-1'],
      protected_track_ids: ['melody-1'],
      instrumentation: {
        before: [baseParts().before[2]],
        after: [{
          part_id: 'p-bass',
          instrument_id: 'cello',
          role: 'bass',
          source_track_ids: ['bass-1'],
          doubling_policy: 'none',
        }],
      },
    },
    {
      operation: 'add_accompaniment',
      source_track_ids: ['harmony-1'],
      protected_track_ids: ['melody-1', 'bass-1'],
      instrumentation: {
        before: baseParts().before,
        after: [
          ...baseParts().before,
          {
            part_id: 'p-pad',
            instrument_id: 'acoustic_grand_piano',
            role: 'pad',
            source_track_ids: [],
            doubling_policy: 'none',
          },
        ],
      },
    },
    {
      operation: 'remove_accompaniment',
      source_track_ids: ['harmony-1'],
      protected_track_ids: ['melody-1', 'bass-1'],
      instrumentation: {
        before: [{
          part_id: 'p-harmony',
          instrument_id: 'acoustic_grand_piano',
          role: 'harmony',
          source_track_ids: ['harmony-1'],
          doubling_policy: 'none',
        }],
        after: [
          baseParts().before[0],
          baseParts().before[2],
        ],
      },
    },
    {
      operation: 'orchestrate_selected_tracks',
      source_track_ids: ['melody-1', 'harmony-1', 'bass-1'],
      protected_track_ids: [],
      instrumentation: {
        before: baseParts().before,
        after: [
          baseParts().before[0],
          {
            part_id: 'p-harmony',
            instrument_id: 'cello',
            role: 'harmony',
            source_track_ids: ['harmony-1'],
            doubling_policy: 'none',
          },
          baseParts().before[2],
        ],
      },
    },
    {
      operation: 'piano_to_ensemble',
      source_track_ids: ['melody-1', 'harmony-1', 'bass-1'],
      protected_track_ids: [],
      instrumentation: {
        before: baseParts().before,
        after: [
          baseParts().before[0],
          {
            part_id: 'p-cello',
            instrument_id: 'cello',
            role: 'bass',
            source_track_ids: ['bass-1'],
            doubling_policy: 'none',
          },
          {
            part_id: 'p-strings',
            instrument_id: 'acoustic_grand_piano',
            role: 'harmony',
            source_track_ids: ['harmony-1'],
            doubling_policy: 'none',
          },
        ],
      },
    },
    {
      operation: 'simplify_arrangement',
      source_track_ids: ['harmony-1'],
      protected_track_ids: ['melody-1'],
      instrumentation: {
        before: baseParts().before,
        after: baseParts().before.map((part) => ({ ...part })),
      },
    },
    {
      operation: 'increase_texture_density',
      source_track_ids: ['harmony-1'],
      protected_track_ids: ['melody-1'],
      instrumentation: {
        before: baseParts().before,
        after: baseParts().before.map((part) => ({ ...part })),
      },
    },
    {
      operation: 'decrease_texture_density',
      source_track_ids: ['harmony-1'],
      protected_track_ids: ['melody-1'],
      instrumentation: {
        before: baseParts().before,
        after: baseParts().before.map((part) => ({ ...part })),
      },
    },
    {
      operation: 'create_countermelody',
      source_track_ids: ['melody-1'],
      protected_track_ids: [],
      instrumentation: {
        before: baseParts().before,
        after: [
          ...baseParts().before,
          {
            part_id: 'p-counter',
            instrument_id: 'cello',
            role: 'countermelody',
            source_track_ids: [],
            doubling_policy: 'none',
          },
        ],
      },
    },
    {
      operation: 'double_melody',
      source_track_ids: ['melody-1'],
      protected_track_ids: [],
      instrumentation: {
        before: [baseParts().before[0]],
        after: [
          baseParts().before[0],
          {
            part_id: 'p-double',
            instrument_id: 'cello',
            role: 'melody',
            source_track_ids: ['melody-1'],
            doubling_policy: 'octave',
          },
        ],
      },
    },
  ];

  let postedOps = [];
  const restore = installAxiosStub(async (config) => {
    const body = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    postedOps.push(body.operation);
    const cand = await buildCandidate(source, {
      candidateId: `arr-cand-${body.operation}`.slice(0, 20).padEnd(20, '0'),
    });
    cand.operation = body.operation;
    return {
      data: await previewResponse(source, [cand], { operation: body.operation }),
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  });
  t.after(restore);

  for (const op of ops) {
    postedOps = [];
    resetStore(source, {
      arrangementOperation: op.operation,
      arrangementSourceTrackIds: op.source_track_ids,
      arrangementProtectedTrackIds: op.protected_track_ids,
      arrangementInstrumentationBefore: op.instrumentation.before,
      arrangementInstrumentationAfter: op.instrumentation.after,
      arrangementCatalog: catalog,
      arrangementCatalogFingerprint: catalog.fingerprint,
    });
    const before = structuredClone(useMusicStore.getState().editedMusicJson);
    const undoLen = useMusicStore.getState().compositionEditUndoStack.length;
    assert.equal(
      await useMusicStore.getState().startArrangementPreview(),
      true,
      op.operation,
    );
    assert.deepEqual(postedOps, [op.operation]);
    assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
    assert.equal(useMusicStore.getState().compositionEditUndoStack.length, undoLen);
    assert.equal(useMusicStore.getState().saveStatus, 'saved');
    assert.equal(useMusicStore.getState().arrangementStatus, 'ready');
    useMusicStore.getState().selectArrangementCandidate(
      useMusicStore.getState().arrangementSelectedCandidateId,
    );
    useMusicStore.getState().setArrangementAuditionMode(ARRANGEMENT_AUDITION_CANDIDATE);
    assert.deepEqual(useMusicStore.getState().editedMusicJson, before);
  }
});

test('empty harmony and motifs survive apply; selected track removal recovers piano roll', async (t) => {
  const source = arrangementSource();
  source.harmony = [];
  source.tracks[0].events = [
    note('m1', 'C4', 0),
    note('m2', 'D4', 480),
    note('m3', 'E4', 960),
  ];
  source.motifs = [{
    id: 'motif-a',
    label: 'Motif A',
    occurrences: [{
      id: 'occ-orig',
      track_id: 'melody-1',
      event_ids: ['m1', 'm2', 'm3'],
      relationship: 'original',
    }],
  }];
  const candidate = await buildCandidate(source, {
    candidateId: 'arr-cand-motif0001',
    mutate: (composition) => {
      composition.tracks = composition.tracks.filter((item) => item.id !== 'harmony-1');
      composition.motifs = structuredClone(source.motifs);
    },
  });
  const restore = installAxiosStub(async (config) => {
    if (config.url === '/composition/arrangement/preview') {
      return {
        data: await previewResponse(source, [candidate]),
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    if (String(config.url || '').includes('/render') || String(config.url || '').includes('musicxml')) {
      return {
        data: { musicxml: '<score/>', warnings: [] },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
      };
    }
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config };
  });
  t.after(restore);

  resetStore(source, {
    pianoRollTrackId: 'harmony-1',
    pianoRollNoteId: 'h1',
    pianoRollNoteIds: ['h1'],
  });
  assert.equal(await useMusicStore.getState().startArrangementPreview(), true);
  assert.equal(await useMusicStore.getState().applySelectedArrangementCandidate(), true);
  assert.equal(useMusicStore.getState().editedMusicJson.harmony.length, 0);
  assert.equal(useMusicStore.getState().editedMusicJson.motifs.length, 1);
  assert.equal(useMusicStore.getState().pianoRollTrackId, 'melody-1');
  assert.equal(useMusicStore.getState().pianoRollNoteId, null);
  assert.equal(useMusicStore.getState().editedMusicJson.tracks.some((item) => item.id === 'harmony-1'), false);
});
