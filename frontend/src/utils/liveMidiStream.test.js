import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  assertLiveAllowedForMidiPhase,
  assertMidiCaptureAllowedForLivePhase,
  createLiveMidiStream,
  isLiveSessionBlockingMidiCapture,
  isMidiCapturePhase,
} from './liveMidiStream.js';
import { LIVE_MIDI_PHASE_EXCLUSION } from './liveSessionContracts.js';
import { MIDI_MESSAGE_KINDS } from './midiInputMessages.js';

describe('liveMidiStream', () => {
  it('timestamps notes from Transport getTick, not wall clock', () => {
    let tick = 480;
    const stream = createLiveMidiStream({
      getTick: () => tick,
      sessionId: 'sess-a',
    });
    assert.equal(stream.start().ok, true);
    stream.pushMessage([0x90, 60, 100]);
    tick = 960;
    stream.pushMessage([0x80, 60, 0]);
    const snap = stream.getSnapshot();
    assert.equal(snap.events[0].tick, 480);
    assert.equal(snap.events[1].tick, 960);
    assert.equal(snap.noteOnCount, 1);
    assert.equal(snap.noteOffCount, 1);
    const closed = stream.getClosedNotes();
    assert.equal(closed.length, 1);
    assert.equal(closed[0].duration_ticks, 480);
  });

  it('stop preserves ring; cancel discards', () => {
    const stream = createLiveMidiStream({ getTick: () => 0 });
    stream.start();
    stream.pushMessage([0x90, 64, 80]);
    const stopped = stream.stop({ reason: 'user-stop' });
    assert.equal(stopped.ok, true);
    assert.ok(stopped.snapshot.ringSize >= 1);

    stream.start();
    stream.pushMessage([0x90, 65, 80]);
    stream.cancel({ reason: 'user-cancel' });
    assert.equal(stream.getSnapshot().ringSize, 0);
  });

  it('enforces midiPhase / live mutual exclusion helpers', () => {
    assert.equal(isMidiCapturePhase('recording'), true);
    assert.equal(isMidiCapturePhase('ready'), false);
    assert.equal(isLiveSessionBlockingMidiCapture('running'), true);
    assert.equal(isLiveSessionBlockingMidiCapture('idle'), false);

    const liveBlocked = assertLiveAllowedForMidiPhase('recording');
    assert.equal(liveBlocked.ok, false);
    assert.equal(liveBlocked.code, LIVE_MIDI_PHASE_EXCLUSION);

    const midiBlocked = assertMidiCaptureAllowedForLivePhase('running');
    assert.equal(midiBlocked.ok, false);
    assert.equal(midiBlocked.code, LIVE_MIDI_PHASE_EXCLUSION);

    assert.equal(assertLiveAllowedForMidiPhase('ready').ok, true);
  });

  it('ignores messages when idle', () => {
    const stream = createLiveMidiStream({ getTick: () => 0 });
    const result = stream.pushMessage([0x90, 60, 100]);
    assert.equal(result.ok, false);
    assert.equal(result.phase, 'idle');
    assert.equal(MIDI_MESSAGE_KINDS.NOTE_ON, 'note_on');
  });
});
