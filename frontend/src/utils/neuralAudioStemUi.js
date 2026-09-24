/**
 * Stem-aware neural audio UI helpers (sync honesty + soft-stale).
 */

import { isNeuralRenderStale } from './compositionSnapshotFingerprint.js';

export const NEURAL_AUDIO_STEM_ROLES = Object.freeze([
  'piano',
  'bass',
  'strings',
  'drums',
  'vocals',
  'other',
]);

export const NEURAL_AUDIO_STEM_SYNC_DISCLAIMERS = Object.freeze({
  deterministic_midi:
    'Deterministic MIDI→FluidSynth stem — timeline-aligned; not a generative punch-in.',
  timeline_aligned:
    'Timeline-aligned stem — shares tempo/origin with the stem set; not sample-accurate across siblings unless deterministic.',
  generative_independent:
    'Generative stem — not sample-locked to sibling stems; do not claim sample-accurate unchanged audio.',
});

export function neuralAudioStemSyncDisclaimer(syncClass) {
  return (
    NEURAL_AUDIO_STEM_SYNC_DISCLAIMERS[syncClass]
    || NEURAL_AUDIO_STEM_SYNC_DISCLAIMERS.generative_independent
  );
}

export function isNeuralAudioStemSetStale(stemSet, liveSnapshotFingerprint) {
  if (!stemSet || stemSet.status !== 'complete') {
    return false;
  }
  return isNeuralRenderStale(stemSet.source_fingerprint, liveSnapshotFingerprint);
}

/** Latest complete stem per role (superseding members win by created_at). */
export function latestStemsByRole(stemSet) {
  const map = new Map();
  const stems = Array.isArray(stemSet?.stems) ? stemSet.stems : [];
  for (const stem of stems) {
    if (!stem || stem.status !== 'complete') continue;
    const role = stem.stem_role;
    const prev = map.get(role);
    if (!prev || String(stem.created_at) > String(prev.created_at)) {
      map.set(role, stem);
    }
  }
  return map;
}
