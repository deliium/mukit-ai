/**
 * Optional editor performance counters for Playwright / local diagnosis.
 *
 * Inert unless `window.__MUKIT_EDITOR_PERF_ENABLE__` is truthy (or DEV auto-enable).
 * Never logs compositions, event arrays, or clipboard payloads — only aggregated counts.
 */

import { createAppLogger } from './appLogger.js';

const logger = createAppLogger('editor.perf');

const EMPTY_SNAPSHOT = Object.freeze({
  enabled: false,
  commitCount: 0,
  noteLayerRenderCount: 0,
  lastNoteLayerNoteCount: 0,
  dragCommitCount: 0,
  marks: Object.freeze({}),
});

function isDevBuild() {
  try {
    return Boolean(import.meta.env?.DEV);
  } catch {
    return false;
  }
}

function enabled() {
  if (typeof window === 'undefined') {
    return false;
  }
  if (window.__MUKIT_EDITOR_PERF_ENABLE__) {
    return true;
  }
  return isDevBuild() && Boolean(window.__MUKIT_EDITOR_PERF_AUTO__);
}

function ensureState() {
  if (typeof window === 'undefined') {
    return null;
  }
  if (!window.__MUKIT_EDITOR_PERF__) {
    window.__MUKIT_EDITOR_PERF__ = {
      commitCount: 0,
      noteLayerRenderCount: 0,
      lastNoteLayerNoteCount: 0,
      dragCommitCount: 0,
      dragActive: false,
      marks: {},
      markStarts: {},
    };
  }
  return window.__MUKIT_EDITOR_PERF__;
}

/** Enable counters (Playwright calls this before interactions). */
export function enableEditorPerf() {
  if (typeof window === 'undefined') {
    return;
  }
  window.__MUKIT_EDITOR_PERF_ENABLE__ = true;
  ensureState();
}

export function resetEditorPerf() {
  const state = ensureState();
  if (!state) {
    return;
  }
  state.commitCount = 0;
  state.noteLayerRenderCount = 0;
  state.lastNoteLayerNoteCount = 0;
  state.dragCommitCount = 0;
  state.dragActive = false;
  state.marks = {};
  state.markStarts = {};
}

export function getEditorPerfSnapshot() {
  if (!enabled()) {
    return { ...EMPTY_SNAPSHOT };
  }
  const state = ensureState();
  return {
    enabled: true,
    commitCount: state.commitCount,
    noteLayerRenderCount: state.noteLayerRenderCount,
    lastNoteLayerNoteCount: state.lastNoteLayerNoteCount,
    dragCommitCount: state.dragCommitCount,
    marks: { ...state.marks },
  };
}

export function recordCompositionCommit(action = 'unknown') {
  if (!enabled()) {
    return;
  }
  const state = ensureState();
  state.commitCount += 1;
  if (state.dragActive) {
    state.dragCommitCount += 1;
  }
  if (typeof performance !== 'undefined' && performance.mark) {
    try {
      performance.mark(`mukit-commit:${action}:${state.commitCount}`);
    } catch {
      // ignore mark quota / unsupported names
    }
  }
}

export function recordNoteLayerRender(noteCount) {
  if (!enabled()) {
    return;
  }
  const state = ensureState();
  state.noteLayerRenderCount += 1;
  state.lastNoteLayerNoteCount = Number(noteCount) || 0;
}

export function beginDragPerf() {
  if (!enabled()) {
    return;
  }
  const state = ensureState();
  state.dragActive = true;
  state.dragCommitCount = 0;
  markStart('drag');
}

export function endDragPerf({ committed = false } = {}) {
  if (!enabled()) {
    return;
  }
  const state = ensureState();
  state.dragActive = false;
  const elapsed = markEnd('drag');
  logger.debug('Drag perf aggregate', {
    committed: Boolean(committed),
    commitsDuringDrag: state.dragCommitCount,
    elapsedMs: elapsed,
  });
}

export function markStart(name) {
  if (!enabled() || !name) {
    return;
  }
  const state = ensureState();
  const now = typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now();
  state.markStarts[name] = now;
  if (typeof performance !== 'undefined' && performance.mark) {
    try {
      performance.mark(`mukit-start:${name}`);
    } catch {
      // ignore
    }
  }
}

export function markEnd(name) {
  if (!enabled() || !name) {
    return null;
  }
  const state = ensureState();
  const started = state.markStarts[name];
  const now = typeof performance !== 'undefined' && performance.now
    ? performance.now()
    : Date.now();
  const elapsed = started != null ? Math.round(now - started) : null;
  if (elapsed != null) {
    state.marks[name] = elapsed;
  }
  delete state.markStarts[name];
  if (typeof performance !== 'undefined' && performance.mark && performance.measure) {
    try {
      performance.mark(`mukit-end:${name}`);
      performance.measure(`mukit:${name}`, `mukit-start:${name}`, `mukit-end:${name}`);
    } catch {
      // ignore
    }
  }
  return elapsed;
}

if (typeof window !== 'undefined') {
  window.__MUKIT_EDITOR_PERF_API__ = {
    enable: enableEditorPerf,
    reset: resetEditorPerf,
    snapshot: getEditorPerfSnapshot,
    markStart,
    markEnd,
  };
}
