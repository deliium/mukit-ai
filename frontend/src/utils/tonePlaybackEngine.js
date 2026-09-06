/**
 * Thin Tone.js playback engine for Composition V1 multi-track scheduling.
 * Accepts injected Tone + logger for deterministic tests.
 */

import {
  buildTrackPlaybackStates,
  midiVolumeToGain,
  resolveEffectiveTrackGains,
} from './playbackTracks.js';

export function createPlaybackEngine({ Tone, logger = console } = {}) {
  if (!Tone) {
    throw new Error('Tone dependency is required to create a playback engine');
  }

  let trackNodes = new Map();
  let scheduledEventIds = [];
  let endEventId = null;
  let disposed = false;

  function log(level, message, context = {}) {
    const method = typeof logger[level] === 'function' ? logger[level].bind(logger) : logger.log?.bind(logger);
    if (method) {
      method(`[tonePlaybackEngine] ${message}`, context);
    }
  }

  function disposeTrackNodes() {
    const beforeCount = trackNodes.size;
    let disposedCount = 0;
    trackNodes.forEach((node, trackId) => {
      try {
        node.synth?.dispose?.();
        node.panner?.dispose?.();
        node.gain?.dispose?.();
        disposedCount += 1;
      } catch (error) {
        log('error', 'Failed to dispose track node', {
          trackId,
          message: error?.message,
        });
      }
    });
    trackNodes = new Map();
    log('debug', 'Disposed track nodes', { beforeCount, disposedCount });
    return disposedCount;
  }

  function clearScheduledEvents() {
    const beforeCount = scheduledEventIds.length + (endEventId == null ? 0 : 1);
    try {
      Tone.Transport.cancel();
    } catch (error) {
      log('warn', 'Transport.cancel anomaly during cleanup', { message: error?.message });
    }
    scheduledEventIds = [];
    endEventId = null;
    log('debug', 'Cleared scheduled events', { clearedCount: beforeCount });
    return beforeCount;
  }

  function createSynthForStrategy(strategy) {
    if (strategy.synth === 'MembraneSynth') {
      return new Tone.MembraneSynth(strategy.options || {}).toDestination();
    }
    if (strategy.synth === 'MonoSynth') {
      return new Tone.MonoSynth(strategy.options || {}).toDestination();
    }
    const Voice = Tone[strategy.voice || 'Synth'] || Tone.Synth;
    return new Tone.PolySynth(Voice, strategy.options?.voice || {}).toDestination();
  }

  function buildTrackRoutes(tracks, trackOverrides = {}) {
    disposeTrackNodes();
    const states = resolveEffectiveTrackGains(buildTrackPlaybackStates(tracks, trackOverrides));
    const routes = new Map();

    states.forEach((state) => {
      const gain = new Tone.Gain(state.effectiveGain);
      const panner = new Tone.Panner(state.panStereo || 0);
      let synth;
      try {
        const strategy = state.strategy;
        if (strategy.synth === 'MembraneSynth') {
          synth = new Tone.MembraneSynth(strategy.options || {});
        } else if (strategy.synth === 'MonoSynth') {
          synth = new Tone.MonoSynth(strategy.options || {});
        } else {
          const Voice = Tone[strategy.voice || 'Synth'] || Tone.Synth;
          synth = new Tone.PolySynth(Voice, strategy.options?.voice || {});
          if (strategy.options?.maxPolyphony && synth.maxPolyphony !== undefined) {
            synth.maxPolyphony = strategy.options.maxPolyphony;
          }
        }
        synth.connect(gain);
        gain.connect(panner);
        panner.toDestination();
      } catch (error) {
        log('error', 'Track route creation failed', {
          trackId: state.trackId,
          strategy: state.strategy?.id,
          message: error?.message,
        });
        synth?.dispose?.();
        gain?.dispose?.();
        panner?.dispose?.();
        return;
      }

      const node = { synth, gain, panner, state };
      trackNodes.set(state.trackId, node);
      routes.set(state.trackId, node);
      log('debug', 'Created track route', {
        trackId: state.trackId,
        strategy: state.strategy?.id,
        fallback: Boolean(state.strategy?.fallback),
        effectiveGain: state.effectiveGain,
        audible: state.audible,
      });
    });

    log('info', 'Track routes built', {
      trackCount: routes.size,
      nodeCount: trackNodes.size,
    });
    return routes;
  }

  function applyTrackOverrides(trackOverrides = {}) {
    const states = resolveEffectiveTrackGains(
      Array.from(trackNodes.values()).map((node) => {
        const override = trackOverrides[node.state.trackId] || {};
        const volumeMidi = override.volumeMidi !== undefined
          ? Number(override.volumeMidi)
          : node.state.volumeMidi;
        return {
          ...node.state,
          muted: override.muted !== undefined ? Boolean(override.muted) : node.state.muted,
          solo: override.solo !== undefined ? Boolean(override.solo) : node.state.solo,
          volumeMidi,
          gain: midiVolumeToGain(volumeMidi),
        };
      }),
    );

    states.forEach((state) => {
      const node = trackNodes.get(state.trackId);
      if (!node) {
        log('warn', 'Missing node handle while applying track overrides', { trackId: state.trackId });
        return;
      }
      node.state = state;
      if (node.gain?.gain) {
        node.gain.gain.value = state.effectiveGain;
      }
      log('debug', 'Applied effective track state', {
        trackId: state.trackId,
        muted: state.muted,
        solo: state.solo,
        volumeMidi: state.volumeMidi,
        effectiveGain: state.effectiveGain,
        audible: state.audible,
      });
    });
  }

  function scheduleEvents(events, { onComplete } = {}) {
    clearScheduledEvents();
    const byTrack = new Map();

    events.forEach((event, index) => {
      const node = trackNodes.get(event.trackId);
      if (!node || !node.state?.audible) {
        return;
      }
      const eventId = Tone.Transport.schedule((time) => {
        try {
          const velocity = Number.isFinite(event.velocity) ? event.velocity : (event.velocityMidi || 80) / 127;
          const notes = event.notes || [event.pitch];
          if (node.synth.triggerAttackRelease) {
            node.synth.triggerAttackRelease(notes.length === 1 ? notes[0] : notes, event.duration, time, velocity);
          }
        } catch (error) {
          log('error', 'Note schedule callback failed', {
            trackId: event.trackId,
            pitch: event.pitch,
            message: error?.message,
          });
        }
      }, event.position);

      scheduledEventIds.push(eventId);
      byTrack.set(event.trackId, (byTrack.get(event.trackId) || 0) + 1);
      log('debug', 'Scheduled note event', {
        eventIndex: index,
        eventId,
        trackId: event.trackId,
        pitch: event.pitch,
        position: event.position,
        duration: event.duration,
        velocityMidi: event.velocityMidi,
      });
    });

    const endPosition = events.reduce((max, event) => Math.max(max, Number(event.stopPosition) || 0), 0);
    if (endPosition > 0) {
      endEventId = Tone.Transport.scheduleOnce(() => {
        if (typeof onComplete === 'function') {
          onComplete();
        }
      }, endPosition);
    }

    log('debug', 'Schedule summary', {
      scheduledCount: scheduledEventIds.length,
      endPosition,
      perTrack: Object.fromEntries(byTrack),
    });

    return {
      scheduledCount: scheduledEventIds.length,
      endPosition,
      perTrack: Object.fromEntries(byTrack),
    };
  }

  function prepare({ tracks, events, tempo, trackOverrides = {}, onComplete } = {}) {
    if (disposed) {
      throw new Error('Playback engine has been disposed');
    }
    log('debug', 'Preparing playback engine', {
      trackCount: Array.isArray(tracks) ? tracks.length : 0,
      eventCount: Array.isArray(events) ? events.length : 0,
      tempo,
    });

    clearScheduledEvents();
    buildTrackRoutes(tracks || [], trackOverrides);
    Tone.Transport.bpm.value = Number(tempo) || 100;
    Tone.Transport.position = 0;
    const schedule = scheduleEvents(events || [], { onComplete });
    return schedule;
  }

  async function start() {
    try {
      await Tone.start();
      Tone.Transport.start();
      log('info', 'Playback started', { transportState: Tone.Transport.state, position: Tone.Transport.seconds });
      return Tone.Transport.state;
    } catch (error) {
      log('error', 'Playback start failed', { message: error?.message });
      throw error;
    }
  }

  function pause() {
    Tone.Transport.pause();
    log('info', 'Playback paused', { transportState: Tone.Transport.state, position: Tone.Transport.seconds });
  }

  function resume() {
    Tone.Transport.start();
    log('info', 'Playback resumed', { transportState: Tone.Transport.state, position: Tone.Transport.seconds });
  }

  function stop({ seekToStart = true } = {}) {
    const cleared = clearScheduledEvents();
    try {
      Tone.Transport.stop();
    } catch (error) {
      log('warn', 'Transport.stop anomaly', { message: error?.message });
    }
    if (seekToStart) {
      Tone.Transport.position = 0;
    }
    const disposedCount = disposeTrackNodes();
    log('info', 'Playback stopped', {
      transportState: Tone.Transport.state,
      clearedEvents: cleared,
      disposedNodes: disposedCount,
      seekToStart,
    });
  }

  function seekToStart() {
    Tone.Transport.position = 0;
    log('info', 'Seeked transport to start', { position: Tone.Transport.seconds });
  }

  function getPositionSeconds() {
    return Number(Tone.Transport.seconds) || 0;
  }

  function getTransportState() {
    return Tone.Transport.state;
  }

  function getScheduledEventCount() {
    return scheduledEventIds.length;
  }

  function getTrackNodeCount() {
    return trackNodes.size;
  }

  function dispose() {
    stop({ seekToStart: true });
    disposed = true;
    log('debug', 'Playback engine disposed');
  }

  // Keep createSynthForStrategy referenced for tests/introspection.
  return {
    prepare,
    start,
    pause,
    resume,
    stop,
    seekToStart,
    applyTrackOverrides,
    getPositionSeconds,
    getTransportState,
    getScheduledEventCount,
    getTrackNodeCount,
    dispose,
    _createSynthForStrategy: createSynthForStrategy,
  };
}
