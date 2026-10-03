import assert from 'node:assert/strict';
import test from 'node:test';

import {
  listActiveStemSpatialPlayers,
  prepareStemSpatialPlayer,
  teardownAllStemSpatialPlayers,
  teardownStemSpatialPlayer,
} from './stemSpatialPlayer.js';

test('stemSpatialPlayer teardown is safe when empty', () => {
  teardownAllStemSpatialPlayers();
  teardownStemSpatialPlayer('missing');
  assert.deepEqual(listActiveStemSpatialPlayers(), []);
});

test('prepareStemSpatialPlayer requires destination and fetch path', async () => {
  teardownAllStemSpatialPlayers();
  const missing = await prepareStemSpatialPlayer({
    Tone: {},
    stemId: 's1',
  });
  assert.equal(missing.ok, false);
  assert.equal(missing.reason, 'missing_args');
});

test('prepareStemSpatialPlayer schedules with mocked Tone + fetcher', async () => {
  teardownAllStemSpatialPlayers();
  const disposed = [];
  const connected = [];
  const fakeBuffer = { duration: 1.5 };
  const Tone = {
    getContext: () => ({
      rawContext: {
        decodeAudioData: async () => fakeBuffer,
      },
    }),
    Player: class {
      constructor(buffer) {
        this.buffer = buffer;
      }

      connect(dest) {
        connected.push(dest);
        return this;
      }

      sync() { return this; }

      start() { return this; }

      unsync() {}

      stop() {}

      dispose() { disposed.push('player'); }
    },
  };
  const destination = { id: 'dest' };
  const extra = {
    disconnect() {},
    dispose() { disposed.push('extra'); },
  };
  const result = await prepareStemSpatialPlayer({
    Tone,
    stemId: 'stem-a',
    destination,
    extraNodes: [extra],
    fetchArrayBuffer: async () => new ArrayBuffer(8),
  });
  assert.equal(result.ok, true);
  assert.equal(result.reason, 'scheduled');
  assert.deepEqual(listActiveStemSpatialPlayers(), ['stem-a']);
  assert.equal(connected[0], destination);
  teardownStemSpatialPlayer('stem-a');
  assert.deepEqual(listActiveStemSpatialPlayers(), []);
  assert.ok(disposed.includes('player'));
  assert.ok(disposed.includes('extra'));
});
