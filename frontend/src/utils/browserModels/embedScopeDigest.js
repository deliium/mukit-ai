/**
 * JS twin of backend `embed_scope_digest` (first 32 hex of SHA-256 of sorted JSON).
 * Payload shape mirrors Pydantic `model_dump(mode="json")` including null optionals.
 */

import { createAppLogger } from '../appLogger.js';

const log = createAppLogger('browserModels');

/**
 * Normalize scope to the same keys Python serializers emit.
 * @param {Record<string, unknown>} scope
 */
export function canonicalizeEmbedScope(scope) {
  const kind = typeof scope?.kind === 'string' ? scope.kind : 'composition';
  if (kind === 'composition') {
    return { kind: 'composition' };
  }
  if (kind === 'section') {
    return {
      expected_bar_count: scope.expected_bar_count ?? null,
      expected_start_bar: scope.expected_start_bar ?? null,
      kind: 'section',
      section_id: scope.section_id ?? null,
      section_index: Number(scope.section_index),
    };
  }
  if (kind === 'motif') {
    return {
      kind: 'motif',
      motif_id: scope.motif_id,
      occurrence_id: scope.occurrence_id ?? null,
    };
  }
  if (kind === 'bar_range') {
    return {
      end_bar: Number(scope.end_bar),
      kind: 'bar_range',
      start_bar: Number(scope.start_bar),
      track_id: scope.track_id ?? null,
    };
  }
  return { ...scope, kind };
}

function sortKeysDeep(value) {
  if (Array.isArray(value)) {
    return value.map(sortKeysDeep);
  }
  if (value && typeof value === 'object') {
    const out = {};
    for (const key of Object.keys(value).sort()) {
      out[key] = sortKeysDeep(value[key]);
    }
    return out;
  }
  return value;
}

/**
 * @param {Record<string, unknown>} scope
 * @returns {Promise<string>}
 */
export async function embedScopeDigest(scope) {
  const payload = canonicalizeEmbedScope(scope || {});
  const compact = JSON.stringify(sortKeysDeep(payload));
  const digestBuf = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(compact),
  );
  const hex = Array.from(new Uint8Array(digestBuf))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
  const digest = hex.slice(0, 32);
  log.debug('Computed embed scope digest', {
    scope_kind: payload.kind,
    scope_digest_prefix: digest.slice(0, 12),
  });
  return digest;
}
