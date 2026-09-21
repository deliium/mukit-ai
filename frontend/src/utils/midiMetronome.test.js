import assert from 'node:assert/strict';
import test from 'node:test';
import { createMidiMetronome } from './midiMetronome.js';

function createFakeTone() {
  const scheduled = [];
  return {
    context: { state: 'running' },
    async start() {},
    MembraneSynth: class {
      constructor() {
        this.volume = { value: 0 };
        this.disposed = false;
      }

      toDestination() {
        return this;
      }

      triggerAttackRelease() {}

      dispose() {
        this.disposed = true;
      }
    },
    Transport: {
      state: 'stopped',
      seconds: 0,
      schedule(callback, when) {
        const id = scheduled.length + 1;
        scheduled.push({ id, callback, when });
        return id;
      },
      clear(id) {
        const index = scheduled.findIndex((item) => item.id === id);
        if (index >= 0) scheduled.splice(index, 1);
      },
      start() {
        this.state = 'started';
      },
      _scheduled: scheduled,
    },
  };
}

test('midiMetronome schedules count-in clicks without Tone when unavailable', () => {
  const metro = createMidiMetronome({ Tone: null });
  const result = metro.schedule({
    composition: { tempo: 120, ticks_per_quarter: 480, time_signature: '4/4' },
    countInBars: 1,
  });
  assert.equal(result.delayMs, 2000);
  assert.equal(result.beatsPerBar, 4);
  metro.dispose();
});

test('midiMetronome schedules Tone Transport clicks for count-in', () => {
  const Tone = createFakeTone();
  const metro = createMidiMetronome({ Tone });
  const result = metro.schedule({
    composition: { tempo: 120, ticks_per_quarter: 480, time_signature: '4/4' },
    countInBars: 1,
    continueDuringRecord: false,
  });
  assert.equal(result.clickCount, 4);
  assert.equal(Tone.Transport._scheduled.length, 4);
  metro.clear();
  assert.equal(Tone.Transport._scheduled.length, 0);
  metro.dispose();
});
