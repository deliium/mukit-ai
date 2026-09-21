/**
 * UI labels and disclaimers for neural audio fidelity classes.
 * Keep copy aligned with backend NEURAL_AUDIO_FIDELITY_LABELS.
 */

export const NEURAL_AUDIO_FIDELITY_DISCLAIMERS = Object.freeze({
  generative: 'Generative AI — not note-perfect. Composition V2 remains the authoritative score.',
  neural_instrument:
    'Neural instrument — approximate notes/expression, not FluidSynth-equivalent fidelity.',
  deterministic: 'Deterministic path (FluidSynth) is available via Export WAV above.',
});

export function neuralAudioFidelityDisclaimer(fidelityClass) {
  return (
    NEURAL_AUDIO_FIDELITY_DISCLAIMERS[fidelityClass]
    || NEURAL_AUDIO_FIDELITY_DISCLAIMERS.generative
  );
}

export function isNeuralAudioDownloadReady(job) {
  return Boolean(job && job.status === 'complete' && job.id);
}
