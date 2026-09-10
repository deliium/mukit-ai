/**
 * Pure keyboard shortcut normalization and command resolution for the V2 editor.
 * Clipboard commands use the Zustand store clipboard — never the browser Clipboard API.
 * Keep log-free; callers emit DEBUG command IDs / selection counts via appLogger.
 */

/** @typedef {'copy'|'cut'|'paste'|'duplicate'|'selectAll'|'clearSelection'|'delete'|'undo'|'redo'|'nudgeLeft'|'nudgeRight'|'nudgeUp'|'nudgeDown'|'transposeOctaveUp'|'transposeOctaveDown'|'transportToggle'|'escape'|'navPrevBar'|'navNextBar'|'navPrevSection'|'navNextSection'|'zoomIn'|'zoomOut'|'zoomToSelection'|'zoomToFit'} EditorShortcutCommand */

export const SHORTCUT_COMMAND = Object.freeze({
  COPY: 'copy',
  CUT: 'cut',
  PASTE: 'paste',
  DUPLICATE: 'duplicate',
  SELECT_ALL: 'selectAll',
  CLEAR_SELECTION: 'clearSelection',
  DELETE: 'delete',
  UNDO: 'undo',
  REDO: 'redo',
  NUDGE_LEFT: 'nudgeLeft',
  NUDGE_RIGHT: 'nudgeRight',
  NUDGE_UP: 'nudgeUp',
  NUDGE_DOWN: 'nudgeDown',
  TRANSPOSE_OCTAVE_UP: 'transposeOctaveUp',
  TRANSPOSE_OCTAVE_DOWN: 'transposeOctaveDown',
  TRANSPORT_TOGGLE: 'transportToggle',
  ESCAPE: 'escape',
  NAV_PREV_BAR: 'navPrevBar',
  NAV_NEXT_BAR: 'navNextBar',
  NAV_PREV_SECTION: 'navPrevSection',
  NAV_NEXT_SECTION: 'navNextSection',
  ZOOM_IN: 'zoomIn',
  ZOOM_OUT: 'zoomOut',
  ZOOM_TO_SELECTION: 'zoomToSelection',
  ZOOM_TO_FIT: 'zoomToFit',
});

const EDITABLE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

/**
 * Normalize a KeyboardEvent (or compatible plain object) for platform-stable matching.
 * `modKey` is Ctrl on non-Apple platforms and Meta (Cmd) on Apple.
 * @returns {{
 *   key: string,
 *   code: string,
 *   ctrlKey: boolean,
 *   metaKey: boolean,
 *   altKey: boolean,
 *   shiftKey: boolean,
 *   modKey: boolean,
 *   repeat: boolean,
 * }}
 */
export function normalizeShortcutEvent(event) {
  const keyRaw = event?.key == null ? '' : String(event.key);
  const codeRaw = event?.code == null ? '' : String(event.code);
  const ctrlKey = Boolean(event?.ctrlKey);
  const metaKey = Boolean(event?.metaKey);
  const altKey = Boolean(event?.altKey);
  const shiftKey = Boolean(event?.shiftKey);
  const isApple = detectApplePlatform(event);
  return {
    key: keyRaw.length === 1 ? keyRaw.toLowerCase() : keyRaw,
    code: codeRaw,
    ctrlKey,
    metaKey,
    altKey,
    shiftKey,
    modKey: isApple ? metaKey : ctrlKey,
    repeat: Boolean(event?.repeat),
  };
}

function detectApplePlatform(event) {
  if (event && typeof event.isApplePlatform === 'boolean') {
    return event.isApplePlatform;
  }
  if (typeof navigator !== 'undefined' && typeof navigator.platform === 'string') {
    return /Mac|iPhone|iPad|iPod/i.test(navigator.platform);
  }
  if (typeof navigator !== 'undefined' && typeof navigator.userAgentData?.platform === 'string') {
    return /mac|ios/i.test(navigator.userAgentData.platform);
  }
  return false;
}

/**
 * Whether the event target should suppress editor shortcuts.
 * Transport may still be allowed when `allowTransport` is true and the target is not
 * a text-entry control (so Space does not insert characters / activate buttons wrongly).
 *
 * @param {EventTarget|Element|null|undefined} target
 * @param {{ allowTransport?: boolean, command?: string|null }} [options]
 * @returns {boolean}
 */
export function shouldIgnoreShortcutTarget(target, options = {}) {
  const allowTransport = Boolean(options.allowTransport);
  const command = options.command ?? null;
  const el = resolveElement(target);
  if (!el) {
    return false;
  }

  if (el.closest?.('[data-json-editor="true"], [data-testid="prompt-json-editor"], .cm-editor, .monaco-editor')) {
    // JSON editor: ignore all editor shortcuts; transport stays scoped out of JSON tooling.
    return true;
  }

  const tag = (el.tagName || '').toUpperCase();
  const isContentEditable = el.isContentEditable
    || el.getAttribute?.('contenteditable') === 'true'
    || Boolean(el.closest?.('[contenteditable="true"]'));
  const isTextEntry = EDITABLE_TAGS.has(tag) || isContentEditable;
  const isButton = tag === 'BUTTON'
    || el.getAttribute?.('role') === 'button'
    || Boolean(el.closest?.('button, [role="button"]'));

  if (allowTransport && command === SHORTCUT_COMMAND.TRANSPORT_TOGGLE) {
    // Allow Space transport outside text fields and buttons (buttons use Space natively).
    return isTextEntry || isButton;
  }

  if (isTextEntry || isButton || EDITABLE_TAGS.has(tag)) {
    return true;
  }
  return false;
}

function resolveElement(target) {
  if (!target) {
    return null;
  }
  if (typeof target.closest === 'function') {
    return target;
  }
  if (target.parentElement) {
    return target.parentElement;
  }
  return null;
}

/**
 * Map a normalized shortcut event to a command id, or null when unmatched.
 * @param {ReturnType<typeof normalizeShortcutEvent>|KeyboardEvent|object} eventOrNormalized
 * @param {{ target?: EventTarget|null, allowTransport?: boolean }} [options]
 * @returns {EditorShortcutCommand|null}
 */
export function resolveShortcutCommand(eventOrNormalized, options = {}) {
  const normalized = isNormalizedShortcut(eventOrNormalized)
    ? eventOrNormalized
    : normalizeShortcutEvent(eventOrNormalized);

  const target = options.target
    ?? eventOrNormalized?.target
    ?? null;

  const command = matchCommand(normalized);
  if (!command) {
    return null;
  }

  if (shouldIgnoreShortcutTarget(target, {
    allowTransport: options.allowTransport !== false,
    command,
  })) {
    return null;
  }

  return command;
}

function isNormalizedShortcut(value) {
  return Boolean(
    value
    && typeof value === 'object'
    && typeof value.modKey === 'boolean'
    && typeof value.key === 'string'
    && Object.prototype.hasOwnProperty.call(value, 'code'),
  );
}

/**
 * @param {ReturnType<typeof normalizeShortcutEvent>} n
 * @returns {EditorShortcutCommand|null}
 */
function matchCommand(n) {
  const key = n.key;
  const code = n.code;
  const mod = n.modKey;
  const shift = n.shiftKey;
  const alt = n.altKey;

  if (key === 'Escape' || code === 'Escape') {
    return SHORTCUT_COMMAND.ESCAPE;
  }

  if (key === ' ' || key === 'Spacebar' || code === 'Space') {
    if (!mod && !alt && !shift) {
      return SHORTCUT_COMMAND.TRANSPORT_TOGGLE;
    }
    return null;
  }

  if ((key === 'Delete' || key === 'Backspace' || code === 'Delete' || code === 'Backspace') && !mod) {
    return SHORTCUT_COMMAND.DELETE;
  }

  if (mod && !alt) {
    const letter = key.length === 1 ? key.toLowerCase() : '';
    if (letter === 'c' && !shift) {
      return SHORTCUT_COMMAND.COPY;
    }
    if (letter === 'x' && !shift) {
      return SHORTCUT_COMMAND.CUT;
    }
    if (letter === 'v' && !shift) {
      return SHORTCUT_COMMAND.PASTE;
    }
    if (letter === 'd' && !shift) {
      return SHORTCUT_COMMAND.DUPLICATE;
    }
    if (letter === 'a' && !shift) {
      return SHORTCUT_COMMAND.SELECT_ALL;
    }
    if (letter === 'z' && !shift) {
      return SHORTCUT_COMMAND.UNDO;
    }
    if (letter === 'z' && shift) {
      return SHORTCUT_COMMAND.REDO;
    }
    if (letter === 'y' && !shift) {
      return SHORTCUT_COMMAND.REDO;
    }
    if ((key === '=' || key === '+' || code === 'Equal' || code === 'NumpadAdd') && !shift) {
      return SHORTCUT_COMMAND.ZOOM_IN;
    }
    if ((key === '-' || key === '_' || code === 'Minus' || code === 'NumpadSubtract') && !shift) {
      return SHORTCUT_COMMAND.ZOOM_OUT;
    }
    if (letter === '0' && !shift) {
      return SHORTCUT_COMMAND.ZOOM_TO_FIT;
    }
    if (letter === '9' && !shift) {
      return SHORTCUT_COMMAND.ZOOM_TO_SELECTION;
    }
  }

  // Octave transpose: Ctrl/Cmd+Shift+Arrow Up/Down, or Ctrl/Cmd+Up/Down
  if (mod && (key === 'ArrowUp' || code === 'ArrowUp')) {
    return SHORTCUT_COMMAND.TRANSPOSE_OCTAVE_UP;
  }
  if (mod && (key === 'ArrowDown' || code === 'ArrowDown')) {
    return SHORTCUT_COMMAND.TRANSPOSE_OCTAVE_DOWN;
  }

  if (!mod && !alt) {
    if (key === 'ArrowLeft' || code === 'ArrowLeft') {
      return shift ? SHORTCUT_COMMAND.NAV_PREV_BAR : SHORTCUT_COMMAND.NUDGE_LEFT;
    }
    if (key === 'ArrowRight' || code === 'ArrowRight') {
      return shift ? SHORTCUT_COMMAND.NAV_NEXT_BAR : SHORTCUT_COMMAND.NUDGE_RIGHT;
    }
    if (key === 'ArrowUp' || code === 'ArrowUp') {
      return shift ? SHORTCUT_COMMAND.NAV_PREV_SECTION : SHORTCUT_COMMAND.NUDGE_UP;
    }
    if (key === 'ArrowDown' || code === 'ArrowDown') {
      return shift ? SHORTCUT_COMMAND.NAV_NEXT_SECTION : SHORTCUT_COMMAND.NUDGE_DOWN;
    }
  }

  return null;
}

/**
 * Convenience: normalize + resolve in one step for keydown handlers.
 * @returns {{ command: EditorShortcutCommand|null, normalized: ReturnType<typeof normalizeShortcutEvent> }}
 */
export function dispatchShortcutEvent(event, options = {}) {
  const normalized = normalizeShortcutEvent(event);
  const command = resolveShortcutCommand(normalized, {
    ...options,
    target: options.target ?? event?.target,
  });
  return { command, normalized };
}
