import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SHORTCUT_COMMAND,
  dispatchShortcutEvent,
  normalizeShortcutEvent,
  resolveShortcutCommand,
  shouldIgnoreShortcutTarget,
} from './editorShortcuts.js';

function fakeEvent(partial = {}) {
  return {
    key: 'a',
    code: 'KeyA',
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    repeat: false,
    isApplePlatform: false,
    target: { tagName: 'DIV', closest: () => null, isContentEditable: false },
    ...partial,
  };
}

test('normalizeShortcutEvent lowercases single-char keys and sets modKey for Ctrl on non-Apple', () => {
  const n = normalizeShortcutEvent(fakeEvent({ key: 'C', ctrlKey: true, isApplePlatform: false }));
  assert.equal(n.key, 'c');
  assert.equal(n.modKey, true);
  assert.equal(n.metaKey, false);
});

test('normalizeShortcutEvent uses Meta as modKey on Apple platforms', () => {
  const n = normalizeShortcutEvent(fakeEvent({ key: 'c', metaKey: true, isApplePlatform: true }));
  assert.equal(n.modKey, true);
  assert.equal(n.ctrlKey, false);
});

test('shouldIgnoreShortcutTarget ignores input textarea select button contenteditable and JSON editor', () => {
  assert.equal(shouldIgnoreShortcutTarget({ tagName: 'INPUT', closest: () => null }), true);
  assert.equal(shouldIgnoreShortcutTarget({ tagName: 'TEXTAREA', closest: () => null }), true);
  assert.equal(shouldIgnoreShortcutTarget({ tagName: 'SELECT', closest: () => null }), true);
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'BUTTON',
    closest: () => null,
    getAttribute: () => null,
  }), true);
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'DIV',
    isContentEditable: true,
    closest: () => null,
    getAttribute: () => 'true',
  }), true);
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'DIV',
    isContentEditable: false,
    closest: (sel) => (String(sel).includes('data-json-editor') ? {} : null),
    getAttribute: () => null,
  }), true);
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'DIV',
    isContentEditable: false,
    closest: () => null,
    getAttribute: () => null,
  }), false);
});

test('transport Space is ignored in text fields and buttons but allowed on plain div', () => {
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'INPUT',
    closest: () => null,
  }, { allowTransport: true, command: SHORTCUT_COMMAND.TRANSPORT_TOGGLE }), true);
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'BUTTON',
    closest: () => null,
    getAttribute: () => null,
  }, { allowTransport: true, command: SHORTCUT_COMMAND.TRANSPORT_TOGGLE }), true);
  assert.equal(shouldIgnoreShortcutTarget({
    tagName: 'DIV',
    closest: () => null,
    isContentEditable: false,
    getAttribute: () => null,
  }, { allowTransport: true, command: SHORTCUT_COMMAND.TRANSPORT_TOGGLE }), false);
});

test('resolveShortcutCommand maps clipboard undo redo select-all delete and duplicate', () => {
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'c', ctrlKey: true })),
    SHORTCUT_COMMAND.COPY,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'x', metaKey: true, isApplePlatform: true })),
    SHORTCUT_COMMAND.CUT,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'v', ctrlKey: true })),
    SHORTCUT_COMMAND.PASTE,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'd', ctrlKey: true })),
    SHORTCUT_COMMAND.DUPLICATE,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'a', ctrlKey: true })),
    SHORTCUT_COMMAND.SELECT_ALL,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'z', ctrlKey: true })),
    SHORTCUT_COMMAND.UNDO,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'z', ctrlKey: true, shiftKey: true })),
    SHORTCUT_COMMAND.REDO,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'y', ctrlKey: true })),
    SHORTCUT_COMMAND.REDO,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'Delete' })),
    SHORTCUT_COMMAND.DELETE,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'Backspace' })),
    SHORTCUT_COMMAND.DELETE,
  );
});

test('resolveShortcutCommand maps nudge octave transport escape and navigation', () => {
  assert.equal(resolveShortcutCommand(fakeEvent({ key: 'ArrowLeft' })), SHORTCUT_COMMAND.NUDGE_LEFT);
  assert.equal(resolveShortcutCommand(fakeEvent({ key: 'ArrowRight' })), SHORTCUT_COMMAND.NUDGE_RIGHT);
  assert.equal(resolveShortcutCommand(fakeEvent({ key: 'ArrowUp' })), SHORTCUT_COMMAND.NUDGE_UP);
  assert.equal(resolveShortcutCommand(fakeEvent({ key: 'ArrowDown' })), SHORTCUT_COMMAND.NUDGE_DOWN);
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'ArrowUp', ctrlKey: true })),
    SHORTCUT_COMMAND.TRANSPOSE_OCTAVE_UP,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'ArrowDown', metaKey: true, isApplePlatform: true })),
    SHORTCUT_COMMAND.TRANSPOSE_OCTAVE_DOWN,
  );
  assert.equal(resolveShortcutCommand(fakeEvent({ key: ' ' })), SHORTCUT_COMMAND.TRANSPORT_TOGGLE);
  assert.equal(resolveShortcutCommand(fakeEvent({ key: 'Escape' })), SHORTCUT_COMMAND.ESCAPE);
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'ArrowLeft', shiftKey: true })),
    SHORTCUT_COMMAND.NAV_PREV_BAR,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'ArrowRight', shiftKey: true })),
    SHORTCUT_COMMAND.NAV_NEXT_BAR,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'ArrowUp', shiftKey: true })),
    SHORTCUT_COMMAND.NAV_PREV_SECTION,
  );
  assert.equal(
    resolveShortcutCommand(fakeEvent({ key: 'ArrowDown', shiftKey: true })),
    SHORTCUT_COMMAND.NAV_NEXT_SECTION,
  );
});

test('resolveShortcutCommand returns null for ignored targets', () => {
  assert.equal(
    resolveShortcutCommand(fakeEvent({
      key: 'c',
      ctrlKey: true,
      target: { tagName: 'INPUT', closest: () => null },
    })),
    null,
  );
});

test('dispatchShortcutEvent returns command and normalized payload', () => {
  const { command, normalized } = dispatchShortcutEvent(fakeEvent({ key: 'c', ctrlKey: true }));
  assert.equal(command, SHORTCUT_COMMAND.COPY);
  assert.equal(normalized.modKey, true);
});
