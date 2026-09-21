/**
 * Unit tests for audio transcription apply helpers and support probe.
 */

import assert from 'node:assert/strict';
import test from 'node:test';
import {
  applyAudioTranscriptionToComposition,
  defaultSelectedProvisionalIds,
  previewNotesToTakeNotes,
  quantizeProvisionalNotes,
  selectPreviewNotesForApply,
} from './audioTranscriptionApply.js';
import { probeAudioInputSupport } from './audioInputSupport.js';
import { encodeWavPcm } from './audioRecorder.js';
import { migrateV1ToV2 } from './compositionVersion.js';

function baseComposition() {
  return migrateV1ToV2({
    schema_version: 'composition.v1',
    tempo: 120,
    key: 'C major',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    bar_count: 4,
    duration_ticks: 7680,
    sections: [
      { id: 'verse', type: 'verse', start_bar: 1, bar_count: 4, start_tick: 0, duration_ticks: 7680 },
    ],
    tracks: [
      {
        id: 't1',
        name: 'Melody',
        instrument: 'piano',
        role: 'melody',
        midi_program: 0,
        channel: 1,
        is_drum: false,
        volume: 100,
        events: [],
      },
    ],
    harmony: [],
  });
}

const preview = {
  schema_version: 'transcription.preview.v1',
  notes: [
    {
      provisional_id: 'a1',
      pitch: 60,
      start_tick: 10,
      duration_ticks: 240,
      velocity: 90,
      confidence: 0.95,
    },
    {
      provisional_id: 'a2',
      pitch: 64,
      start_tick: 250,
      duration_ticks: 240,
      velocity: 80,
      confidence: 0.3,
    },
  ],
  summary: {
    note_count: 2,
    low_confidence_count: 1,
    excluded_low_confidence_count: 1,
    include_threshold: 0.5,
  },
  timing: {
    tempo_bpm: 120,
    ticks_per_quarter: 480,
    origin_tick: 0,
    tempo_source: 'provided',
    meter: '4/4',
  },
  engine: { id: 'fake:audio-mono', version: '1', fake: true },
  issues: [],
};

test('probeAudioInputSupport does not call getUserMedia', () => {
  let called = false;
  const support = probeAudioInputSupport({
    isSecureContext: true,
    MediaRecorder: function MediaRecorder() {},
    navigator: {
      mediaDevices: {
        getUserMedia: async () => {
          called = true;
          throw new Error('should not run');
        },
      },
    },
  });
  assert.equal(called, false);
  assert.equal(support.supported, true);
  assert.equal(support.reason, 'available');
});

test('probeAudioInputSupport reports insecure context', () => {
  const support = probeAudioInputSupport({
    isSecureContext: false,
    MediaRecorder: function MediaRecorder() {},
    navigator: { mediaDevices: { getUserMedia: async () => ({}) } },
  });
  assert.equal(support.supported, false);
  assert.equal(support.reason, 'insecure_context');
});

test('defaultSelectedProvisionalIds excludes low confidence', () => {
  const ids = defaultSelectedProvisionalIds(preview, 0.5);
  assert.deepEqual(ids, ['a1']);
});

test('selectPreviewNotesForApply respects includeLowConfidence', () => {
  const excluded = selectPreviewNotesForApply(preview, { includeLowConfidence: false });
  assert.equal(excluded.notes.length, 1);
  assert.equal(excluded.excludedLow, 1);
  const included = selectPreviewNotesForApply(preview, { includeLowConfidence: true });
  assert.equal(included.notes.length, 2);
});

test('previewNotesToTakeNotes converts midi ints to pitch strings', () => {
  const notes = previewNotesToTakeNotes(preview.notes);
  assert.equal(notes[0].pitch, 'C4');
  assert.equal(notes[1].pitch, 'E4');
  assert.ok(!('confidence' in notes[0]));
});

test('quantizeProvisionalNotes snaps start ticks', () => {
  const quantized = quantizeProvisionalNotes(preview.notes, baseComposition(), {
    snapValue: '1/8',
    strength: 100,
  });
  // 1/8 at 480 ppq = 240 ticks; 10 → 0
  assert.equal(quantized[0].start_tick, 0);
});

test('applyAudioTranscriptionToComposition commits without confidence fields', () => {
  const result = applyAudioTranscriptionToComposition(baseComposition(), {
    trackId: 't1',
    preview,
    selectedIds: ['a1'],
    quantize: false,
  });
  assert.equal(result.ok, true);
  assert.equal(result.noteRefs.length, 1);
  const event = result.composition.tracks[0].events[0];
  assert.equal(event.pitch, 'C4');
  assert.equal(event.confidence, undefined);
});

test('apply rejects locked track', () => {
  const result = applyAudioTranscriptionToComposition(baseComposition(), {
    trackId: 't1',
    preview,
    selectedIds: ['a1'],
    lockedTrackIds: ['t1'],
  });
  assert.equal(result.ok, false);
  assert.equal(result.code, 'audio_track_locked');
});

test('encodeWavPcm writes RIFF/WAVE header', () => {
  const pcm = new Float32Array([0, 0.5, -0.5, 0]);
  const buf = encodeWavPcm(pcm, 22050);
  const bytes = new Uint8Array(buf);
  assert.equal(String.fromCharCode(...bytes.slice(0, 4)), 'RIFF');
  assert.equal(String.fromCharCode(...bytes.slice(8, 12)), 'WAVE');
});
