import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ARDOUR_STATUS_POLL_MS,
  canControlArdour,
  formatArdourStatusBanner,
  startArdourStatusPoll,
} from './controls.js';

test('canControlArdour requires permission and ready connection state', () => {
  assert.equal(canControlArdour({ controlPermission: true, connectionState: 'connected' }), true);
  assert.equal(canControlArdour({ controlPermission: true, connectionState: 'awaiting_feedback' }), true);
  assert.equal(canControlArdour({ controlPermission: false, connectionState: 'connected' }), false);
  assert.equal(canControlArdour({ controlPermission: true, connectionState: 'disconnected' }), false);
  assert.equal(canControlArdour({ controlPermission: true, connectionState: 'stale' }), false);
  // Session without permission stays gated even if UI checkbox were later checked locally.
  assert.equal(canControlArdour({ controlPermission: false, connectionState: 'awaiting_feedback' }), false);
});

test('startArdourStatusPoll polls every 1000 ms and clears on leave', () => {
  let calls = 0;
  const fetchStatus = () => {
    calls += 1;
  };
  const timers = new Map();
  let nextId = 1;
  const setIntervalFn = (fn, ms) => {
    assert.equal(ms, ARDOUR_STATUS_POLL_MS);
    const id = nextId;
    nextId += 1;
    timers.set(id, fn);
    return id;
  };
  const clearIntervalFn = (id) => {
    timers.delete(id);
  };
  const stop = startArdourStatusPoll(fetchStatus, { setIntervalFn, clearIntervalFn });
  assert.equal(timers.size, 1);
  const [[, tick]] = timers;
  tick();
  assert.equal(calls, 1);
  stop();
  assert.equal(timers.size, 0);
});

test('formatArdourStatusBanner renders disabled and live banners', () => {
  assert.match(formatArdourStatusBanner({ enabled: false }), /Companion disabled/);
  assert.match(
    formatArdourStatusBanner({
      enabled: true,
      connection_state: 'connected',
      feedback_age_ms: 12,
      stale: false,
    }),
    /state=connected/,
  );
});
