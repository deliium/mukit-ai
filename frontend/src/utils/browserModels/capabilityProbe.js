/**
 * Browser capability probe → `browser.capability.v1` (session-only; never on Composition).
 * Pure probe with injectable globals for tests. Does not request adapters unless
 * `requestAdapter` is explicitly allowed via options.
 */

import { createAppLogger } from '../appLogger.js';
import { BROWSER_MODEL_SCHEMA_CAPABILITY } from './constants.js';

const log = createAppLogger('browserModels');

export const CAPABILITY_REASONS = Object.freeze({
  WEBGPU_UNSUPPORTED: 'webgpu_unsupported',
  WEBGPU_INSECURE: 'webgpu_insecure_context',
  WEBGPU_ADAPTER_UNAVAILABLE: 'webgpu_adapter_unavailable',
  WEBGPU_READY: 'webgpu_ready',
  WASM_SIMD_UNKNOWN: 'wasm_simd_unknown',
  WASM_SIMD_LIKELY: 'wasm_simd_likely',
  CPU_AVAILABLE: 'cpu_available',
});

/**
 * @param {{
 *   navigator?: { gpu?: { requestAdapter?: Function } },
 *   isSecureContext?: boolean,
 *   deviceMemory?: number,
 *   requestAdapter?: boolean,
 * }} [env]
 * @returns {Promise<{
 *   schema_version: string,
 *   webgpu_adapter: string | null,
 *   webgpu_ready: boolean,
 *   wasm_simd: boolean | null,
 *   device_memory_hint_mb: number | null,
 *   reason_codes: string[],
 * }>}
 */
export async function probeBrowserCapability(env = {}) {
  const globalObj = typeof globalThis !== 'undefined' ? globalThis : undefined;
  const nav = env.navigator
    || (globalObj && globalObj.navigator)
    || undefined;
  const secure = typeof env.isSecureContext === 'boolean'
    ? env.isSecureContext
    : Boolean(globalObj && globalObj.isSecureContext);

  /** @type {string[]} */
  const reasonCodes = [CAPABILITY_REASONS.CPU_AVAILABLE];
  let webgpuReady = false;
  /** @type {string | null} */
  let webgpuAdapter = null;

  const hasGpu = Boolean(nav && nav.gpu && typeof nav.gpu.requestAdapter === 'function');
  if (!secure) {
    reasonCodes.push(CAPABILITY_REASONS.WEBGPU_INSECURE);
  } else if (!hasGpu) {
    reasonCodes.push(CAPABILITY_REASONS.WEBGPU_UNSUPPORTED);
  } else if (env.requestAdapter) {
    try {
      const adapter = await nav.gpu.requestAdapter();
      if (adapter) {
        webgpuReady = true;
        webgpuAdapter = typeof adapter.info?.device === 'string'
          ? adapter.info.device
          : (typeof adapter.name === 'string' ? adapter.name : 'webgpu');
        reasonCodes.push(CAPABILITY_REASONS.WEBGPU_READY);
      } else {
        reasonCodes.push(CAPABILITY_REASONS.WEBGPU_ADAPTER_UNAVAILABLE);
      }
    } catch {
      reasonCodes.push(CAPABILITY_REASONS.WEBGPU_ADAPTER_UNAVAILABLE);
    }
  } else {
    // Presence-only probe (default): treat GPU API as ready without requesting.
    webgpuReady = true;
    webgpuAdapter = 'webgpu';
    reasonCodes.push(CAPABILITY_REASONS.WEBGPU_READY);
  }

  let wasmSimd = null;
  try {
    // Best-effort: WebAssembly.validate of a tiny SIMD opcode sequence is not
    // portable across all engines; leave null when unsure.
    if (typeof WebAssembly !== 'undefined' && typeof WebAssembly.validate === 'function') {
      wasmSimd = null;
      reasonCodes.push(CAPABILITY_REASONS.WASM_SIMD_UNKNOWN);
    }
  } catch {
    wasmSimd = null;
    reasonCodes.push(CAPABILITY_REASONS.WASM_SIMD_UNKNOWN);
  }

  const memoryHint = typeof env.deviceMemory === 'number'
    ? env.deviceMemory
    : (typeof nav?.deviceMemory === 'number' ? nav.deviceMemory * 1024 : null);

  const snapshot = {
    schema_version: BROWSER_MODEL_SCHEMA_CAPABILITY,
    webgpu_adapter: webgpuAdapter,
    webgpu_ready: webgpuReady,
    wasm_simd: wasmSimd,
    device_memory_hint_mb: memoryHint,
    reason_codes: reasonCodes,
  };
  log.debug('Browser capability probe', {
    webgpu_ready: snapshot.webgpu_ready,
    webgpu_adapter: snapshot.webgpu_adapter,
    reason_codes: snapshot.reason_codes,
    device_memory_hint_mb: snapshot.device_memory_hint_mb,
  });
  return snapshot;
}
