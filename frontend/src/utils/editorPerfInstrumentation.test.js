import assert from 'node:assert/strict';
import test from 'node:test';

import {
  enableEditorPerf,
  getEditorPerfSnapshot,
  recordCompositionCommit,
  beginDragPerf,
  endDragPerf,
  resetEditorPerf,
  recordNoteLayerRender,
} from './editorPerfInstrumentation.js';

test('editor perf counters stay inert until enabled', () => {
  // jsdom/node: window may exist in some runners; force clean slate.
  if (typeof globalThis.window === 'undefined') {
    globalThis.window = {};
  }
  delete globalThis.window.__MUKIT_EDITOR_PERF_ENABLE__;
  resetEditorPerf();
  recordCompositionCommit('x');
  const off = getEditorPerfSnapshot();
  assert.equal(off.enabled, false);
  assert.equal(off.commitCount, 0);

  enableEditorPerf();
  resetEditorPerf();
  beginDragPerf();
  recordCompositionCommit('drag-move');
  endDragPerf({ committed: true });
  recordNoteLayerRender(42);
  const on = getEditorPerfSnapshot();
  assert.equal(on.enabled, true);
  assert.equal(on.commitCount, 1);
  assert.equal(on.dragCommitCount, 1);
  assert.equal(on.lastNoteLayerNoteCount, 42);
});
