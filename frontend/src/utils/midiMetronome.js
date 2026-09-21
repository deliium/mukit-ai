/**
 * Ephemeral Tone.js metronome / count-in clicks for MIDI recording.
 * Never writes into composition.v2 — clicks are transport-only audio.
 */

import { createAppLogger } from './appLogger.js';
import { barDurationTicks } from './compositionTimeline.js';

const log = createAppLogger('midiInput');

/**
 * @param {{
 *   Tone?: object,
 *   composition?: object,
 *   countInBars?: number,
 *   continueDuringRecord?: boolean,
 *   maxBars?: number,
 *   onCountInComplete?: () => void,
 * }} options
 */
export function createMidiMetronome(options = {}) {
  const Tone = options.Tone || null;
  /** @type {object | null} */
  let synth = null;
  /** @type {Array<number|string>} */
  const scheduledIds = [];
  let disposed = false;
  let clickCount = 0;

  function ensureSynth() {
    if (!Tone || disposed) {
      return null;
    }
    if (synth) {
      return synth;
    }
    try {
      if (typeof Tone.MembraneSynth === 'function') {
        synth = new Tone.MembraneSynth({
          pitchDecay: 0.008,
          octaves: 2,
          oscillator: { type: 'sine' },
          envelope: { attack: 0.001, decay: 0.15, sustain: 0, release: 0.05 },
        }).toDestination();
        synth.volume.value = -12;
      } else if (typeof Tone.Synth === 'function') {
        synth = new Tone.Synth().toDestination();
        synth.volume.value = -18;
      }
    } catch (error) {
      log.warn('Metronome synth create failed', {
        code: 'midi_metronome_synth_failed',
        message: error instanceof Error ? error.message : 'unknown',
      });
      return null;
    }
    return synth;
  }

  function clear() {
    if (Tone?.Transport && typeof Tone.Transport.clear === 'function') {
      for (const id of scheduledIds) {
        try {
          Tone.Transport.clear(id);
        } catch {
          // ignore
        }
      }
    }
    scheduledIds.length = 0;
  }

  /**
   * Schedule count-in (+ optional continuing clicks) and invoke onCountInComplete
   * after count-in bars. Returns delayMs until capture should start.
   */
  function schedule({
    composition = options.composition,
    countInBars = options.countInBars ?? 1,
    continueDuringRecord = options.continueDuringRecord ?? false,
    maxBars = options.maxBars ?? 64,
    onCountInComplete = options.onCountInComplete,
  } = {}) {
    clear();
    clickCount = 0;
    const tempo = Number(composition?.tempo) || 100;
    const tpq = Number(composition?.ticks_per_quarter) || 480;
    const meter = composition?.time_signature || '4/4';
    const barTicks = barDurationTicks(meter, tpq) || (tpq * 4);
    const beatsPerBar = Math.max(1, Math.round(barTicks / tpq));
    const secondsPerBeat = 60 / tempo;
    const countIn = Math.max(0, Math.min(2, Math.round(Number(countInBars) || 0)));
    const countInBeats = countIn * beatsPerBar;
    const totalBeats = continueDuringRecord
      ? Math.max(countInBeats, Math.min(maxBars, 64) * beatsPerBar)
      : countInBeats;

    log.info('Metronome schedule', {
      bpm: tempo,
      countInBars: countIn,
      beatsPerBar,
      continueDuringRecord: Boolean(continueDuringRecord),
      totalBeats,
    });

    if (!Tone || totalBeats <= 0) {
      const delayMs = countIn * beatsPerBar * secondsPerBeat * 1000;
      if (typeof onCountInComplete === 'function' && countIn > 0) {
        // Caller still owns the timer when Tone is unavailable.
      }
      return { delayMs, clickCount: 0, secondsPerBeat, beatsPerBar };
    }

    const clickSynth = ensureSynth();
    if (!clickSynth) {
      return {
        delayMs: countIn * beatsPerBar * secondsPerBeat * 1000,
        clickCount: 0,
        secondsPerBeat,
        beatsPerBar,
      };
    }

    try {
      if (Tone.context?.state === 'suspended' && typeof Tone.start === 'function') {
        Tone.start().catch((error) => {
          log.warn('Audio context blocked for metronome', {
            code: 'midi_metronome_audio_blocked',
            message: error instanceof Error ? error.message : 'unknown',
          });
        });
      }
    } catch (error) {
      log.warn('Audio context blocked for metronome', {
        code: 'midi_metronome_audio_blocked',
        message: error instanceof Error ? error.message : 'unknown',
      });
    }

    const transport = Tone.Transport;
    const startAt = typeof transport.seconds === 'number' ? transport.seconds : 0;

    for (let beat = 0; beat < totalBeats; beat += 1) {
      const when = startAt + beat * secondsPerBeat;
      const isDownbeat = beat % beatsPerBar === 0;
      const id = transport.schedule((time) => {
        clickCount += 1;
        try {
          clickSynth.triggerAttackRelease(isDownbeat ? 'C5' : 'G4', 0.05, time, isDownbeat ? 0.9 : 0.5);
        } catch {
          // ignore click failures
        }
        if (beat + 1 === countInBeats && typeof onCountInComplete === 'function') {
          onCountInComplete();
        }
      }, when);
      scheduledIds.push(id);
    }

    if (transport.state !== 'started' && typeof transport.start === 'function') {
      transport.start();
    }

    log.debug('Metronome clicks scheduled', { scheduled: scheduledIds.length });
    return {
      delayMs: countIn * beatsPerBar * secondsPerBeat * 1000,
      clickCount: scheduledIds.length,
      secondsPerBeat,
      beatsPerBar,
    };
  }

  function dispose() {
    disposed = true;
    clear();
    if (synth && typeof synth.dispose === 'function') {
      try {
        synth.dispose();
      } catch {
        // ignore
      }
    }
    synth = null;
    log.debug('Metronome disposed', { clickCount });
  }

  return {
    schedule,
    clear,
    dispose,
    getClickCount: () => clickCount,
  };
}
