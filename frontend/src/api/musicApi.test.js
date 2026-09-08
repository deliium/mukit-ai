import assert from 'node:assert/strict';
import test from 'node:test';

import axios from 'axios';
import { migrateV1ToV2 } from '../utils/compositionVersion.js';
import { editCompositionRegion, generateLlmMusicJson, importMidi, importMusicXml, ImportApiError, parseProjectionHeaders, projectionWarningsFromHeaders } from './musicApi.js';

function canonicalV1Composition() {
  return {
    schema_version: 'composition.v1',
    tempo: 100,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 2,
    duration_ticks: 3840,
    sections: [{ type: 'intro', start_bar: 1, bar_count: 2, start_tick: 0, duration_ticks: 3840 }],
    tracks: [
      {
        id: 'melody-1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        events: [
          { type: 'note', pitch: 'C4', start_tick: 0, duration_ticks: 480, velocity: 90 },
          { type: 'note', pitch: 'E4', start_tick: 1920, duration_ticks: 480, velocity: 90 },
        ],
      },
    ],
    harmony: [{ bar: 1, chord: 'C' }],
  };
}

test('parseProjectionHeaders handles missing and malformed values safely', () => {
  assert.deepEqual(parseProjectionHeaders({}), {
    status: 'exact',
    issues: [],
    exactCount: 0,
    approximatedCount: 0,
    omittedCount: 0,
    failedCount: 0,
    hasIssues: false,
  });
  const parsed = parseProjectionHeaders({
    'X-Mukit-Projection-Status': 'omitted',
    'X-Mukit-Projection-Issues': 'automation_omitted_from_notation,sustain_projected',
    'X-Mukit-Projection-Omitted-Count': '2',
    'X-Mukit-Projection-Approximated-Count': 'bad',
  });
  assert.equal(parsed.status, 'omitted');
  assert.deepEqual(parsed.issues, ['automation_omitted_from_notation', 'sustain_projected']);
  assert.equal(parsed.omittedCount, 2);
  assert.equal(parsed.approximatedCount, 0);
  assert.equal(projectionWarningsFromHeaders({
    'X-Mukit-Projection-Issues': 'automation_omitted_from_notation',
  }).length, 1);
});

test('generateLlmMusicJson normalizes v1 responses to v2', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      music: canonicalV1Composition(),
      musicxml: '<score/>',
      warnings: [],
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const response = await generateLlmMusicJson({});
  assert.equal(response.music.schema_version, 'composition.v2');
});

test('editCompositionRegion validates response composition and patch', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/llm/edit-composition-region');
    const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data;
    assert.equal(payload.composition.schema_version, 'composition.v2');
    return {
      data: {
        composition: canonicalV1Composition(),
        patch: {
          schema_version: 'composition.v2',
          operation: 'replace_region',
          start_bar: 1,
          end_bar: 1,
          target_track_ids: ['melody-1'],
          replace_tracks: [],
          added_tracks: [],
          warnings: [],
        },
        provider: 'openai',
        model: 'test-model',
        musicxml: '<score/>',
        warnings: ['ok'],
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const response = await editCompositionRegion({
    composition: canonicalV1Composition(),
    edit: {
      instruction: 'make this phrase more dramatic but keep the harmony',
      selection: { start_bar: 1, end_bar: 1, track_ids: ['melody-1'] },
    },
    selection: { provider: 'openai', model: 'test-model' },
  });

  assert.equal(response.composition.schema_version, 'composition.v2');
  assert.equal(response.patch.operation, 'replace_region');
  assert.equal(response.warnings.length, 1);
});

test('editCompositionRegion rejects missing replace_region patch', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => ({
    data: {
      composition: migrateV1ToV2(canonicalV1Composition()),
      patch: null,
      provider: 'openai',
      model: 'test-model',
      warnings: [],
    },
    status: 200,
    statusText: 'OK',
    headers: {},
    config: {},
  });
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  await assert.rejects(
    () => editCompositionRegion({
      composition: canonicalV1Composition(),
      edit: {
        instruction: 'edit',
        selection: { start_bar: 1, end_bar: 1, track_ids: ['melody-1'] },
      },
    }),
    /replace_region patch/,
  );
});

test('importMidi posts FormData without forcing multipart boundary', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async (config) => {
    assert.equal(String(config.method || 'get').toLowerCase(), 'post');
    assert.equal(config.url, '/imports/midi');
    assert.ok(config.data instanceof FormData);
    const contentType = String(
      config.headers?.['Content-Type']
      ?? config.headers?.['content-type']
      ?? '',
    );
    assert.notEqual(contentType, 'application/x-www-form-urlencoded');
    if (contentType) {
      assert.ok(
        contentType === 'false'
        || contentType.includes('multipart/form-data')
        || contentType === 'undefined',
        `unexpected Content-Type for FormData upload: ${contentType}`,
      );
    }
    return {
      data: {
        composition: migrateV1ToV2(canonicalV1Composition()),
        musicxml: '<score-partwise/>',
        import_report: {
          status: 'exact',
          issues: [],
          summary: {
            detected_format: 'midi',
            display_filename: 'demo.mid',
            input_bytes: 12,
            target_ppq: 480,
            source_track_count: 1,
            result_track_count: 1,
            source_note_count: 2,
            result_note_count: 2,
            bar_count: 2,
            duration_ticks: 3840,
          },
        },
        notation_report: { status: 'exact', issue_codes: [] },
      },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    };
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const file = { name: 'demo.mid', size: 4 };
  const response = await importMidi(file);
  assert.equal(response.composition.schema_version, 'composition.v2');
  assert.equal(response.import_report.status, 'exact');
  assert.equal(response.notation_report.status, 'exact');
  assert.match(response.musicxml, /score-partwise/);
});

test('importMusicXml preserves structured error code and status', async (t) => {
  const previousAdapter = axios.defaults.adapter;
  axios.defaults.adapter = async () => {
    const error = new Error('Request failed');
    error.response = {
      status: 415,
      data: {
        detail: {
          code: 'import_unsupported_media_type',
          message: 'Content signature does not match MusicXML import endpoint',
          details: { detected: 'midi' },
        },
      },
    };
    throw error;
  };
  t.after(() => {
    axios.defaults.adapter = previousAdapter;
  });

  const file = { name: 'bad.musicxml', size: 4 };
  await assert.rejects(
    () => importMusicXml(file),
    (error) => {
      assert.ok(error instanceof ImportApiError);
      assert.equal(error.status, 415);
      assert.equal(error.code, 'import_unsupported_media_type');
      assert.match(error.message, /Content signature/);
      assert.equal(error.details?.detected, 'midi');
      return true;
    },
  );
});
