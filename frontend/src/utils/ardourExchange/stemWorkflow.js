/**
 * SPA-only Ardour stem/render workflow.
 *
 * Enqueues a neural stem-set via musicApi, polls until a stem is complete
 * (or fails/times out), then prepares an outbound exchange package with
 * working composition + stem_id. No new exchange HTTP route.
 */

import { createAppLogger } from '../appLogger.js';
import {
  enqueueNeuralAudioStemSet,
  getNeuralAudioStemSet,
} from '../../api/musicApi.js';
import { prepareArdourExchange } from '../../api/ardourExchangeApi.js';

const logger = createAppLogger('ardourExchange.stemWorkflow');

export const STEM_WORKFLOW_HONESTY =
  'Stems are optional and not sample-locked to MIDI. MIDI round-trip remains the hard acceptance path.';

export const DEFAULT_STEM_POLL_MS = 500;
export const DEFAULT_STEM_TIMEOUT_MS = 30_000;

function truncateId(value, max = 12) {
  if (value == null) return null;
  const text = String(value);
  return text.length <= max ? text : text.slice(0, max);
}

function findCompleteStem(stemSet) {
  const stems = Array.isArray(stemSet?.stems) ? stemSet.stems : [];
  return stems.find((stem) => stem && stem.status === 'complete' && stem.id) || null;
}

function isTerminalFailure(stemSet) {
  const status = stemSet?.status;
  if (status === 'failed' || status === 'error' || status === 'cancelled') {
    return true;
  }
  const stems = Array.isArray(stemSet?.stems) ? stemSet.stems : [];
  if (stems.length === 0) return false;
  return stems.every((stem) => stem && ['failed', 'error', 'cancelled'].includes(stem.status));
}

/**
 * @param {{
 *   composition: object,
 *   projectId?: string|null,
 *   sourceRevisionId?: string|null,
 *   stemRoles?: string[]|null,
 *   barRange?: object|null,
 *   pollMs?: number,
 *   timeoutMs?: number,
 *   enqueueStemSet?: Function,
 *   getStemSet?: Function,
 *   prepareExchange?: Function,
 *   sleep?: Function,
 * }} options
 */
export async function runArdourStemWorkflow(options = {}) {
  const {
    composition,
    projectId = null,
    sourceRevisionId = null,
    stemRoles = null,
    barRange = null,
    pollMs = DEFAULT_STEM_POLL_MS,
    timeoutMs = DEFAULT_STEM_TIMEOUT_MS,
    enqueueStemSet = enqueueNeuralAudioStemSet,
    getStemSet = getNeuralAudioStemSet,
    prepareExchange = prepareArdourExchange,
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  } = options;

  if (!composition || composition.schema_version !== 'composition.v2') {
    logger.warn('stem workflow refused: missing working composition');
    return {
      ok: false,
      error: 'Working composition.v2 is required for stem prepare.',
      honesty: STEM_WORKFLOW_HONESTY,
    };
  }

  let stemSet;
  try {
    stemSet = await enqueueStemSet({
      composition,
      project_id: projectId,
      source_revision_id: sourceRevisionId,
      stem_roles: stemRoles,
      bar_range: barRange,
      engine: 'neural',
    });
  } catch (error) {
    const code = error?.code || error?.status || null;
    const message = error?.message || 'Neural stem enqueue failed';
    logger.warn('neural disabled or enqueue failed', {
      code,
      message: String(message).slice(0, 160),
    });
    return {
      ok: false,
      error: message,
      code,
      honesty: STEM_WORKFLOW_HONESTY,
    };
  }

  const stemSetId = stemSet?.id || stemSet?.stem_set_id;
  if (!stemSetId) {
    logger.warn('stem incomplete: missing stem-set id');
    return {
      ok: false,
      error: 'Stem-set enqueue returned no id.',
      honesty: STEM_WORKFLOW_HONESTY,
    };
  }

  logger.info('stem-set enqueued', { stem_set_id: truncateId(stemSetId) });

  const resolvedTimeout = Number(timeoutMs);
  const deadline = Date.now() + (
    Number.isFinite(resolvedTimeout) && resolvedTimeout > 0
      ? resolvedTimeout
      : DEFAULT_STEM_TIMEOUT_MS
  );
  let latest = stemSet;
  let completeStem = findCompleteStem(latest);

  while (!completeStem) {
    if (isTerminalFailure(latest)) {
      logger.warn('stem incomplete', { stem_set_id: truncateId(stemSetId) });
      return {
        ok: false,
        error: 'Stem-set failed before any stem completed.',
        stem_set_id: stemSetId,
        honesty: STEM_WORKFLOW_HONESTY,
      };
    }
    if (Date.now() >= deadline) {
      logger.warn('poll timeout', { stem_set_id: truncateId(stemSetId) });
      return {
        ok: false,
        error: 'Timed out waiting for a complete stem.',
        stem_set_id: stemSetId,
        honesty: STEM_WORKFLOW_HONESTY,
      };
    }
    await sleep(Math.max(50, Number(pollMs) || DEFAULT_STEM_POLL_MS));
    latest = await getStemSet(stemSetId);
    completeStem = findCompleteStem(latest);
  }

  const stemId = completeStem.id;
  const prepared = await prepareExchange({
    composition,
    stem_id: stemId,
    use_preview_alignment: true,
  });

  logger.info('stem prepare complete', {
    stem_set_id: truncateId(stemSetId),
    stem_id: truncateId(stemId),
    package_id: truncateId(prepared?.package_id, 20),
  });

  return {
    ok: true,
    stem_set_id: stemSetId,
    stem_id: stemId,
    prepare: prepared,
    honesty: STEM_WORKFLOW_HONESTY,
  };
}
