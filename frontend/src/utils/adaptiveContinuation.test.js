import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildContinuationStartBody,
  continuationEventsToSchedule,
  continuousDisabledReason,
  summarizeMusicState,
} from './adaptiveContinuation.js';
import { compileTimeline, tickToSeconds } from './compositionTimeline.js';

const note = (start) => ({ start_tick: start, duration_ticks: 480, pitch: 60 });

test('a reuse-loop buffer and an inaudible model buffer schedule nothing', () => {
  const reuse = continuationEventsToSchedule(
    { audible: false, source: 'fallback', fallback_kind: 'reuse_loop' },
    { events: [note(9600)] },
    0,
  );
  const outside = continuationEventsToSchedule(
    { audible: false, source: 'model', fallback_kind: 'reuse_loop' },
    { events: [note(9600), note(11520)] },
    0,
  );
  assert.deepEqual(reuse, []);
  assert.deepEqual(outside, []);
});

test('an audible model buffer keeps ticks at or after the playhead', () => {
  const kept = continuationEventsToSchedule(
    { audible: true, source: 'model', fallback_kind: 'accompaniment' },
    { events: [note(4800), note(9600), note(11520)] },
    9600,
  );
  assert.deepEqual(kept.map((event) => event.start_tick), [9600, 11520]);
});

test('buildContinuationStartBody latches continuous only when true', () => {
  assert.deepEqual(
    buildContinuationStartBody({ expectedDocumentRevision: 3, continuous: true }),
    {
      expected_document_revision: 3,
      mode: 'continuation',
      continuous: true,
    },
  );
  assert.equal(
    buildContinuationStartBody({ expectedDocumentRevision: 1, continuous: 'yes' }).continuous,
    false,
  );
  assert.equal(
    buildContinuationStartBody({ expectedDocumentRevision: 1 }).continuous,
    false,
  );
});

test('summarizeMusicState exposes inspect fields without digests', () => {
  const summary = summarizeMusicState({
    schema_version: 'adaptive.runtime.music_state.v1',
    active_theme_ids: ['theme-a', 'theme-b'],
    harmony_trajectory: ['I', 'V', 'vi'],
    guard_flags: ['repetition_pressure'],
    virtual_bar: 12,
    summary_digest: '0123456789abcdef',
  });
  assert.deepEqual(summary, {
    virtualBar: 12,
    themeCount: 2,
    trajectoryTail: 'vi',
    guardFlags: ['repetition_pressure'],
  });
  assert.equal(summarizeMusicState(null), null);
  assert.equal(summarizeMusicState({ schema_version: 'other' }), null);
});

test('continuousDisabledReason maps the flag-off refuse code', () => {
  assert.equal(continuousDisabledReason('adaptive_continuous_disabled'), 'adaptive_continuous_disabled');
  assert.equal(continuousDisabledReason('continuation_not_running'), '');
});

test('tickToSeconds maps past durationTicks with constant tempo', () => {
  const timeline = compileTimeline({
    schema_version: 'composition.v2',
    tempo: 120,
    key: 'C',
    time_signature: '4/4',
    ticks_per_quarter: 480,
    duration_ticks: 1920,
    bar_count: 1,
    sections: [{ type: 'verse', start_bar: 1, bar_count: 1, start_tick: 0, duration_ticks: 1920 }],
    tracks: [{ id: 't1', name: 'Piano', instrument: 'piano', midi_channel: 0, midi_program: 0, events: [] }],
    harmony: [],
    markers: [],
    key_changes: [],
    motifs: [],
  });
  assert.ok(timeline);
  const atEnd = tickToSeconds(timeline, 1920);
  const past = tickToSeconds(timeline, 1920 + 480);
  // 120 bpm, 480 tpq → 0.5 s per quarter; 1920 ticks = 2 s
  assert.equal(atEnd, 2);
  assert.equal(past, 2.5);
  assert.equal(tickToSeconds(timeline, -1), null);
});
