import assert from 'node:assert/strict';
import test from 'node:test';

import { applyAdaptivePlaybackInstructions } from './adaptivePlayback.js';

function fakeEngine() {
  const calls = [];
  return {
    calls,
    state: 'started',
    getTransportState() {
      return this.state;
    },
    getLoop() {
      return { enabled: true, startTick: 0, endTick: 30720 };
    },
    setLoop(loop) {
      calls.push(['setLoop', loop]);
    },
    seekToTick(tick) {
      calls.push(['seekToTick', tick]);
    },
    stop() {
      calls.push(['stop']);
    },
  };
}

function fakeMixer() {
  const touched = [];
  const gains = { 'track-pad': 0.4, 'track-bed': 1 };
  return {
    touched,
    gains,
    getTrackGain(id) {
      return gains[id];
    },
    rampTrack(id, target, seconds) {
      touched.push([id, target, seconds]);
      gains[id] = target;
    },
    rampMaster() {},
  };
}

test('a warning with stop false does not stop the transport', () => {
  const engine = fakeEngine();
  const mixer = fakeMixer();
  applyAdaptivePlaybackInstructions(engine, mixer, null, {
    position_tick: 2400,
    instructions: {
      stop: false,
      seek_tick: null,
      loop: { enabled: true, start_tick: 0, end_tick: 7680 },
      track_gains: [],
    },
    warnings: [{ code: 'dangling_state_ref' }],
  });
  assert.equal(engine.calls.some((call) => call[0] === 'stop'), false);
});

test('a disabled loop is applied before the seek at 7680', () => {
  const engine = fakeEngine();
  const mixer = fakeMixer();
  applyAdaptivePlaybackInstructions(engine, mixer, null, {
    position_tick: 5760,
    instructions: {
      stop: false,
      seek_tick: 7680,
      loop: { enabled: false, start_tick: 0, end_tick: 7680 },
      track_gains: [],
    },
  });
  assert.deepEqual(engine.calls.map((call) => call[0]), ['setLoop', 'seekToTick']);
  assert.deepEqual(engine.calls[0][1], { enabled: false, startTick: 0, endTick: 7680 });
  assert.equal(engine.calls[1][1], 7680);
});

test('seek runs only while the transport is started', () => {
  const engine = fakeEngine();
  engine.state = 'stopped';
  applyAdaptivePlaybackInstructions(engine, fakeMixer(), null, {
    instructions: {
      stop: false,
      seek_tick: 7680,
      loop: { enabled: true, start_tick: 0, end_tick: 7680 },
      track_gains: [],
    },
  });
  assert.equal(engine.calls.some((call) => call[0] === 'seekToTick'), false);
  engine.state = 'started';
  engine.calls.length = 0;
  applyAdaptivePlaybackInstructions(engine, fakeMixer(), { loop: null, gains: {} }, {
    instructions: {
      stop: false,
      seek_tick: 7680,
      loop: { enabled: true, start_tick: 0, end_tick: 7680 },
      track_gains: [],
    },
  });
  assert.equal(engine.calls.some((call) => call[0] === 'seekToTick'), true);
});

test('unnamed tracks and absent section rows stay untouched, and stop restores the remembered gains', () => {
  const engine = fakeEngine();
  const mixer = fakeMixer();
  const remembered = applyAdaptivePlaybackInstructions(engine, mixer, null, {
    position_tick: 0,
    instructions: {
      stop: false,
      seek_tick: null,
      loop: { enabled: true, start_tick: 0, end_tick: 7680 },
      track_gains: [{ track_id: 'track-pad', target_gain: 0, fade_end_tick: 0 }],
    },
  });
  assert.deepEqual(mixer.touched.map((row) => row[0]), ['track-pad']);
  assert.equal(mixer.gains['track-bed'], 1);
  assert.equal(remembered.gains['track-pad'], 0.4);

  const restored = applyAdaptivePlaybackInstructions(engine, mixer, remembered, {
    instructions: { stop: true, seek_tick: null, loop: remembered.loop, track_gains: [] },
  });
  assert.equal(restored.gains['track-pad'], 0.4);
  assert.equal(mixer.gains['track-pad'], 0.4);
  assert.equal(engine.calls.at(-1)[0], 'stop');
});
