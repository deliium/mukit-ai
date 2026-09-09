/**
 * Environment-controlled frontend logger.
 *
 * Level comes from `VITE_LOG_LEVEL` (debug|info|warn|error|silent).
 * Production builds default to `warn` when unset so noisy DEBUG stays off
 * without source edits. Never pass compositions, instructions, event arrays,
 * or catalog payloads into meta — only bounded status metadata.
 */

const LEVEL_RANK = Object.freeze({
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
  silent: 100,
});

/** @type {string | null} */
let testLevelOverride = null;

function readEnv(name) {
  try {
    const viteEnv = typeof import.meta !== 'undefined' ? import.meta.env : undefined;
    if (viteEnv && viteEnv[name] != null && String(viteEnv[name]).length > 0) {
      return String(viteEnv[name]);
    }
  } catch {
    // import.meta.env unavailable outside Vite.
  }
  if (typeof process !== 'undefined' && process.env && process.env[name] != null) {
    const value = String(process.env[name]);
    if (value.length > 0) {
      return value;
    }
  }
  return undefined;
}

function isProductionBuild() {
  try {
    const viteEnv = typeof import.meta !== 'undefined' ? import.meta.env : undefined;
    if (viteEnv && typeof viteEnv.PROD === 'boolean') {
      return viteEnv.PROD;
    }
    if (viteEnv && typeof viteEnv.MODE === 'string') {
      return viteEnv.MODE === 'production';
    }
  } catch {
    // ignore
  }
  return readEnv('NODE_ENV') === 'production';
}

function normalizeLevel(raw) {
  if (typeof raw !== 'string') {
    return null;
  }
  const level = raw.trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(LEVEL_RANK, level) ? level : null;
}

/**
 * Resolve the active log level.
 * Explicit VITE_LOG_LEVEL / test override wins; otherwise production → warn, else debug.
 */
export function resolveAppLogLevel() {
  if (testLevelOverride) {
    return testLevelOverride;
  }
  const fromEnv = normalizeLevel(readEnv('VITE_LOG_LEVEL'));
  if (fromEnv) {
    return fromEnv;
  }
  return isProductionBuild() ? 'warn' : 'debug';
}

/** Test-only override. Pass null to clear. */
export function setAppLogLevelForTests(level) {
  testLevelOverride = level == null ? null : normalizeLevel(String(level));
}

function shouldEmit(level) {
  const active = resolveAppLogLevel();
  return LEVEL_RANK[level] >= LEVEL_RANK[active];
}

function emit(consoleMethod, namespace, level, message, meta) {
  if (!shouldEmit(level)) {
    return;
  }
  const prefix = namespace ? `[${namespace}]` : '[app]';
  if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
    consoleMethod(`${prefix} ${message}`, meta);
  } else {
    consoleMethod(`${prefix} ${message}`);
  }
}

/**
 * Create a namespaced logger gated by VITE_LOG_LEVEL.
 * @param {string} [namespace]
 */
export function createAppLogger(namespace = 'app') {
  const label = String(namespace || 'app');
  return {
    debug(message, meta) {
      emit(console.debug.bind(console), label, 'debug', message, meta);
    },
    info(message, meta) {
      emit(console.info.bind(console), label, 'info', message, meta);
    },
    warn(message, meta) {
      emit(console.warn.bind(console), label, 'warn', message, meta);
    },
    error(message, meta) {
      emit(console.error.bind(console), label, 'error', message, meta);
    },
    isEnabled(level) {
      const normalized = normalizeLevel(level) || 'debug';
      return shouldEmit(normalized);
    },
  };
}

export const appLogger = createAppLogger('app');
