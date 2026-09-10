/**
 * Browser instrument adapters for Tone.js synth and Tone.Sampler voices.
 *
 * Technology decision (Task 4 spike): use Tone.Sampler — not smplr —
 * for sampled profiles. Reasons:
 * - Attack/release are scheduled on the existing Tone.Transport timeline
 *   with absolute seconds (no parallel sequencer).
 * - Note-id aware polyphony matches PolySynth triggerAttack/Release.
 * - Output connects into the Tone gain/pan graph used by the mixer.
 * - Deterministic unit tests can inject a fake Tone.Sampler.
 * smplr remains unused; its CDN/built-in instruments are out of scope.
 */

import { createAppLogger } from './appLogger.js';
import { resolveSynthFallbackPreset, validateInstrumentAdapter } from './playbackInstrumentPresets.js';
import { selectInstrumentStrategy } from './playbackTracks.js';

const logger = createAppLogger('playbackInstrumentAdapters');

/**
 * Create a Tone synth adapter from a track strategy / preset.
 * @param {{ Tone: object, track: object, output?: object, profileId?: string }} options
 */
export function createToneSynthAdapter({
  Tone,
  track,
  output = null,
  profileId = null,
} = {}) {
  if (!Tone) {
    throw new Error('Tone is required for synth adapter');
  }
  const resolved = resolveSynthFallbackPreset(track || {});
  const strategy = resolved.strategy || selectInstrumentStrategy(track || {});
  const id = profileId || resolved.presetId || strategy.presetId || 'piano_keyboard';

  let synth = null;
  let ready = false;
  let disposed = false;
  let destination = output;
  const activeNotes = new Set();

  function buildSynth() {
    if (strategy.synth === 'MembraneSynth') {
      return new Tone.MembraneSynth(strategy.options || {});
    }
    if (strategy.synth === 'MonoSynth') {
      return new Tone.MonoSynth(strategy.options || {});
    }
    const Voice = Tone[strategy.voice || 'Synth'] || Tone.Synth;
    const poly = new Tone.PolySynth(Voice, strategy.options?.voice || {});
    if (strategy.options?.maxPolyphony && poly.maxPolyphony !== undefined) {
      poly.maxPolyphony = strategy.options.maxPolyphony;
    }
    return poly;
  }

  const adapter = {
    async prepare() {
      if (disposed) {
        return adapter.getStatus();
      }
      if (!synth) {
        synth = buildSynth();
        if (destination) {
          synth.connect(destination);
        }
      }
      ready = true;
      logger.debug('Synth adapter prepared', { profileId: id, strategy: strategy.id });
      return adapter.getStatus();
    },
    attack(noteId, pitch, velocity, time) {
      if (!ready || disposed || !synth) {
        return;
      }
      const idKey = String(noteId || '');
      const vel = Number.isFinite(Number(velocity)) ? Number(velocity) : 0.8;
      if (synth.triggerAttack) {
        synth.triggerAttack(pitch, time, vel);
      } else if (synth.triggerAttackRelease) {
        synth.triggerAttackRelease(pitch, 3600, time, vel);
      }
      if (idKey) {
        activeNotes.add(idKey);
      }
    },
    release(noteId, time, pitch = null) {
      if (!synth || disposed) {
        return;
      }
      const idKey = String(noteId || '');
      if (synth.triggerRelease) {
        if (typeof synth.maxPolyphony === 'number' && pitch) {
          synth.triggerRelease(pitch, time);
        } else if (typeof time === 'number') {
          synth.triggerRelease(time);
        } else {
          synth.triggerRelease();
        }
      }
      activeNotes.delete(idKey);
    },
    releaseAll(time) {
      activeNotes.clear();
      if (!synth) {
        return;
      }
      if (typeof synth.releaseAll === 'function') {
        synth.releaseAll(time);
      } else if (synth.triggerRelease) {
        synth.triggerRelease(time);
      }
    },
    connect(nextDestination) {
      destination = nextDestination || null;
      if (synth && destination) {
        synth.disconnect?.();
        synth.connect(destination);
      }
    },
    getStatus() {
      return {
        ready: ready && !disposed,
        fallback: true,
        profileId: id,
        reasonCode: disposed ? 'disposed' : (ready ? 'synth_ready' : 'not_prepared'),
        engine: 'tone_synth',
      };
    },
    dispose() {
      activeNotes.clear();
      try {
        synth?.dispose?.();
      } catch (error) {
        logger.warn('Synth dispose failed', { profileId: id, message: error?.message });
      }
      synth = null;
      ready = false;
      disposed = true;
    },
    getToneNode() {
      return synth;
    },
  };

  const validation = validateInstrumentAdapter(adapter);
  if (!validation.ok) {
    logger.error('Synth adapter contract invalid', { missing: validation.missing });
  }
  return adapter;
}

/**
 * Create a Tone.Sampler adapter for locally resolved sample maps.
 * Falls back to synth adapter when urls are empty or load fails.
 *
 * @param {{
 *   Tone: object,
 *   urls: Record<string, string>,
 *   baseUrl?: string,
 *   output?: object,
 *   profileId: string,
 *   fallbackTrack?: object,
 *   onFallback?: (reason: string) => void,
 * }} options
 */
export function createToneSamplerAdapter({
  Tone,
  urls = {},
  baseUrl = '',
  output = null,
  profileId,
  fallbackTrack = null,
  onFallback = null,
} = {}) {
  if (!Tone) {
    throw new Error('Tone is required for sampler adapter');
  }
  const id = String(profileId || 'sampled');
  const urlEntries = Object.entries(urls || {}).filter(([, url]) => typeof url === 'string' && url.length > 0);

  if (!urlEntries.length || typeof Tone.Sampler !== 'function') {
    logger.warn('Sampler unavailable; using synth fallback', {
      profileId: id,
      reason: !urlEntries.length ? 'empty_urls' : 'missing_tone_sampler',
    });
    onFallback?.(!urlEntries.length ? 'empty_urls' : 'missing_tone_sampler');
    return createToneSynthAdapter({
      Tone,
      track: fallbackTrack || { instrument: 'piano' },
      output,
      profileId: id,
    });
  }

  let sampler = null;
  let ready = false;
  let disposed = false;
  let fallbackAdapter = null;
  let destination = output;
  const activeNotes = new Map();

  const adapter = {
    async prepare() {
      if (disposed) {
        return adapter.getStatus();
      }
      if (fallbackAdapter) {
        return fallbackAdapter.prepare();
      }
      try {
        await new Promise((resolve, reject) => {
          let settled = false;
          sampler = new Tone.Sampler({
            urls: Object.fromEntries(urlEntries),
            baseUrl: baseUrl || '',
            onload: () => {
              if (settled) {
                return;
              }
              settled = true;
              resolve();
            },
            onerror: (error) => {
              if (settled) {
                return;
              }
              settled = true;
              reject(error || new Error('sampler_load_failed'));
            },
          });
          if (destination) {
            sampler.connect(destination);
          }
          // Some Tone builds resolve synchronously when buffers are already cached.
          if (sampler.loaded) {
            settled = true;
            resolve();
          }
        });
        ready = true;
        logger.info('Sampler adapter prepared', {
          profileId: id,
          sampleCount: urlEntries.length,
          reasonCode: 'sampler_ready',
        });
        return adapter.getStatus();
      } catch (error) {
        logger.warn('Sampler load failed; synth fallback', {
          profileId: id,
          reasonCode: 'sampler_load_failed',
          message: error?.message,
        });
        onFallback?.('sampler_load_failed');
        fallbackAdapter = createToneSynthAdapter({
          Tone,
          track: fallbackTrack || { instrument: 'piano' },
          output: destination,
          profileId: id,
        });
        sampler?.dispose?.();
        sampler = null;
        await fallbackAdapter.prepare();
        return adapter.getStatus();
      }
    },
    attack(noteId, pitch, velocity, time) {
      if (fallbackAdapter) {
        fallbackAdapter.attack(noteId, pitch, velocity, time);
        return;
      }
      if (!ready || disposed || !sampler) {
        return;
      }
      const idKey = String(noteId || '');
      const vel = Number.isFinite(Number(velocity)) ? Number(velocity) : 0.8;
      sampler.triggerAttack(pitch, time, vel);
      if (idKey) {
        activeNotes.set(idKey, pitch);
      }
    },
    release(noteId, time, pitch = null) {
      if (fallbackAdapter) {
        fallbackAdapter.release(noteId, time, pitch);
        return;
      }
      if (!sampler || disposed) {
        return;
      }
      const idKey = String(noteId || '');
      const releasePitch = pitch || activeNotes.get(idKey);
      if (releasePitch) {
        sampler.triggerRelease(releasePitch, time);
      }
      activeNotes.delete(idKey);
    },
    releaseAll(time) {
      if (fallbackAdapter) {
        fallbackAdapter.releaseAll(time);
        return;
      }
      activeNotes.clear();
      sampler?.releaseAll?.(time);
    },
    connect(nextDestination) {
      destination = nextDestination || null;
      if (fallbackAdapter) {
        fallbackAdapter.connect(destination);
        return;
      }
      if (sampler && destination) {
        sampler.disconnect?.();
        sampler.connect(destination);
      }
    },
    getStatus() {
      if (fallbackAdapter) {
        const status = fallbackAdapter.getStatus();
        return { ...status, fallback: true, engine: 'tone_synth_fallback' };
      }
      return {
        ready: ready && !disposed,
        fallback: false,
        profileId: id,
        reasonCode: disposed ? 'disposed' : (ready ? 'sampler_ready' : 'not_prepared'),
        engine: 'tone_sampler',
      };
    },
    dispose() {
      activeNotes.clear();
      fallbackAdapter?.dispose?.();
      fallbackAdapter = null;
      try {
        sampler?.dispose?.();
      } catch (error) {
        logger.warn('Sampler dispose failed', { profileId: id, message: error?.message });
      }
      sampler = null;
      ready = false;
      disposed = true;
    },
    getToneNode() {
      return fallbackAdapter?.getToneNode?.() || sampler;
    },
  };

  return adapter;
}

/**
 * Deduplicated lazy loader for sampled profiles.
 */
export function createInstrumentProfileCache() {
  /** @type {Map<string, Promise<object>>} */
  const inflight = new Map();
  /** @type {Map<string, object>} */
  const readyAdapters = new Map();

  return {
    async load(cacheKey, factory) {
      const key = String(cacheKey || '');
      if (readyAdapters.has(key)) {
        logger.debug('Instrument cache hit', { cacheKey: key });
        return readyAdapters.get(key);
      }
      if (inflight.has(key)) {
        logger.debug('Instrument cache join inflight', { cacheKey: key });
        return inflight.get(key);
      }
      const started = (
        typeof performance !== 'undefined' && performance.now
          ? performance.now()
          : Date.now()
      );
      const promise = Promise.resolve()
        .then(() => factory())
        .then(async (adapter) => {
          await adapter.prepare();
          readyAdapters.set(key, adapter);
          inflight.delete(key);
          const elapsedMs = (
            typeof performance !== 'undefined' && performance.now
              ? performance.now()
              : Date.now()
          ) - started;
          logger.debug('Instrument cache load complete', {
            cacheKey: key,
            elapsedMs: Math.round(elapsedMs),
            reasonCode: adapter.getStatus()?.reasonCode,
          });
          return adapter;
        })
        .catch((error) => {
          inflight.delete(key);
          logger.error('Instrument cache load failed', {
            cacheKey: key,
            message: error?.message,
          });
          throw error;
        });
      inflight.set(key, promise);
      return promise;
    },
    get(cacheKey) {
      return readyAdapters.get(String(cacheKey || '')) || null;
    },
    clear() {
      readyAdapters.forEach((adapter) => {
        try {
          adapter.dispose?.();
        } catch {
          // ignore
        }
      });
      readyAdapters.clear();
      inflight.clear();
    },
    size() {
      return readyAdapters.size;
    },
  };
}
