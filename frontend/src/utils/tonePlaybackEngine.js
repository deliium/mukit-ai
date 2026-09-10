/**
 * Thin Tone.js playback engine for composition.v2 multi-track scheduling.
 * Accepts injected Tone + logger for deterministic tests.
 *
 * Transport/loop state is ephemeral engine state — never written into Composition V2.
 */

import { audibleRevisionKey } from './compositionCanonical.js';
import {
  buildTrackPlaybackStates,
  midiPanToStereo,
  midiVolumeToGain,
  resolveEffectiveTrackGains,
} from './playbackTracks.js';
import { compilePlaybackSchedule } from './playbackEvents.js';
import { ticksToPlaybackSeconds } from './playbackPosition.js';
import { normalizePlaybackLoop } from './playbackLoop.js';

export function createPlaybackEngine({ Tone, logger = console } = {}) {
  if (!Tone) {
    throw new Error('Tone dependency is required to create a playback engine');
  }

  let trackNodes = new Map();
  let scheduledEventIds = [];
  let endEventId = null;
  let activeNotes = new Map();
  let currentSchedule = null;
  let currentTracks = [];
  let currentTrackOverrides = {};
  let currentComposition = null;
  let currentOnComplete = null;
  let currentEndPosition = 0;
  let currentLoop = null;
  /** Resolved loop window in seconds (null when disabled/invalid). */
  let currentLoopSeconds = null;
  let disposed = false;

  function log(level, message, context = {}) {
    const method = typeof logger[level] === 'function' ? logger[level].bind(logger) : logger.log?.bind(logger);
    if (method) {
      method(`[tonePlaybackEngine] ${message}`, context);
    }
  }

  function noteKey(trackId, pitch) {
    return `${trackId}:${pitch}`;
  }

  function revisionPrefix(composition = currentComposition) {
    if (!composition) {
      return 'empty';
    }
    try {
      return String(audibleRevisionKey(composition)).slice(0, 48);
    } catch {
      return 'unknown';
    }
  }

  function resolveSecondsFromTick(tick, composition = currentComposition, tempo = null) {
    const safeTick = Math.max(0, Number(tick) || 0);
    if (composition) {
      return ticksToPlaybackSeconds(safeTick, {
        composition,
        tempo: composition.tempo,
        ticksPerQuarter: composition.ticks_per_quarter,
      });
    }
    const safeTempo = Number(tempo) || 100;
    const tpq = 480;
    return (safeTick / tpq) * (60 / safeTempo);
  }

  function resolveStartSeconds({
    startSeconds = null,
    startTick = null,
    composition = currentComposition,
    tempo = null,
  } = {}) {
    if (startSeconds != null && Number.isFinite(Number(startSeconds)) && Number(startSeconds) >= 0) {
      return Number(startSeconds);
    }
    if (startTick != null && Number.isFinite(Number(startTick)) && Number(startTick) >= 0) {
      return resolveSecondsFromTick(startTick, composition, tempo);
    }
    return 0;
  }

  function syncLoopSeconds(loop = currentLoop, composition = currentComposition) {
    const normalized = normalizePlaybackLoop(loop, composition);
    currentLoop = normalized;
    if (!normalized || !normalized.enabled) {
      currentLoopSeconds = null;
      return currentLoopSeconds;
    }
    currentLoopSeconds = {
      startSeconds: resolveSecondsFromTick(normalized.startTick, composition),
      endSeconds: resolveSecondsFromTick(normalized.endTick, composition),
      startTick: normalized.startTick,
      endTick: normalized.endTick,
      enabled: true,
    };
    if (!(currentLoopSeconds.endSeconds > currentLoopSeconds.startSeconds)) {
      log('warn', 'Loop seconds window invalid after tick conversion', {
        startTick: normalized.startTick,
        endTick: normalized.endTick,
        startSeconds: currentLoopSeconds.startSeconds,
        endSeconds: currentLoopSeconds.endSeconds,
      });
      currentLoopSeconds = null;
    }
    return currentLoopSeconds;
  }

  function disposeTrackNodes() {
    const beforeCount = trackNodes.size;
    let disposedCount = 0;
    trackNodes.forEach((node, trackId) => {
      try {
        node.synth?.dispose?.();
        node.expressionGain?.dispose?.();
        node.volumeGain?.dispose?.();
        node.uiGain?.dispose?.();
        node.panner?.dispose?.();
        disposedCount += 1;
      } catch (error) {
        log('error', 'Failed to dispose track node', {
          trackId,
          message: error?.message,
        });
      }
    });
    trackNodes = new Map();
    activeNotes = new Map();
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
    activeNotes = new Map();
    log('debug', 'Cleared scheduled events', { clearedCount: beforeCount });
    return beforeCount;
  }

  function releaseActiveNotes(time) {
    const entries = Array.from(activeNotes.values());
    entries.forEach((note) => {
      const node = trackNodes.get(note.trackId);
      if (!node) {
        return;
      }
      try {
        scheduleRelease(node, { trackId: note.trackId, pitch: note.pitch }, time);
      } catch (error) {
        log('error', 'Active note release failed', {
          trackId: note.trackId,
          message: error?.message,
        });
      }
    });
    activeNotes = new Map();
  }

  function buildTrackRoutes(tracks, trackOverrides = {}) {
    disposeTrackNodes();
    currentTracks = tracks || [];
    currentTrackOverrides = trackOverrides;
    const states = resolveEffectiveTrackGains(buildTrackPlaybackStates(tracks, trackOverrides));
    const routes = new Map();

    states.forEach((state) => {
      const sourceTrack = (tracks || []).find((track) => String(track?.id ?? '') === state.trackId);
      const persistedVolume = Number.isFinite(Number(sourceTrack?.volume))
        ? Math.max(0, Math.min(127, Number(sourceTrack.volume)))
        : state.volumeMidi;

      let synth;
      let expressionGain;
      let volumeGain;
      let uiGain;
      let panner;
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

        expressionGain = new Tone.Gain(1);
        volumeGain = new Tone.Gain(midiVolumeToGain(persistedVolume));
        // Mute/solo via live effective gain so unmute during play hears already-scheduled notes.
        uiGain = new Tone.Gain(state.effectiveGain);
        panner = new Tone.Panner(state.panStereo || 0);

        synth.connect(expressionGain);
        expressionGain.connect(volumeGain);
        volumeGain.connect(uiGain);
        uiGain.connect(panner);
        panner.toDestination();
      } catch (error) {
        log('error', 'Track route creation failed', {
          trackId: state.trackId,
          strategy: state.strategy?.id,
          message: error?.message,
        });
        synth?.dispose?.();
        expressionGain?.dispose?.();
        volumeGain?.dispose?.();
        uiGain?.dispose?.();
        panner?.dispose?.();
        return;
      }

      const node = { synth, expressionGain, volumeGain, uiGain, panner, state };
      trackNodes.set(state.trackId, node);
      routes.set(state.trackId, node);
    });

    log('info', 'Track routes built', {
      trackCount: routes.size,
      nodeCount: trackNodes.size,
    });
    return routes;
  }

  function applyControllerStateAtTime(node, item, time) {
    if (!node || item.time == null) {
      return;
    }
    if (item.parameter === 'volume' && node.volumeGain?.gain) {
      const target = midiVolumeToGain(item.value);
      if (item.interpolation === 'linear' && typeof node.volumeGain.gain.linearRampTo === 'function') {
        node.volumeGain.gain.linearRampTo(target, Math.max(0, item.time - time));
      } else {
        node.volumeGain.gain.value = target;
      }
    } else if (item.parameter === 'pan' && node.panner?.pan) {
      const target = midiPanToStereo(item.value);
      if (item.interpolation === 'linear' && typeof node.panner.pan.linearRampTo === 'function') {
        node.panner.pan.linearRampTo(target, Math.max(0, item.time - time));
      } else {
        node.panner.pan.value = target;
      }
    } else if (item.parameter === 'expression' && node.expressionGain?.gain) {
      const target = item.value / 127;
      if (item.interpolation === 'linear' && typeof node.expressionGain.gain.linearRampTo === 'function') {
        node.expressionGain.gain.linearRampTo(target, Math.max(0, item.time - time));
      } else {
        node.expressionGain.gain.value = target;
      }
    }
  }

  function scheduleAttack(node, item, time) {
    const pitch = item.pitch;
    const velocity = Number.isFinite(item.velocity) ? item.velocity : (item.velocityMidi || 80) / 127;
    if (node.synth.triggerAttack) {
      node.synth.triggerAttack(pitch, time, velocity);
    } else if (node.synth.triggerAttackRelease) {
      node.synth.triggerAttackRelease(pitch, 3600, time, velocity);
    }
    activeNotes.set(noteKey(item.trackId, pitch), { trackId: item.trackId, pitch, attackTime: item.time });
  }

  function scheduleRelease(node, item, time) {
    const key = noteKey(item.trackId, item.pitch);
    if (!activeNotes.has(key)) {
      return;
    }
    if (typeof time !== 'number' || !Number.isFinite(time)) {
      log('warn', '[FIX:tone-release] Skipping release with invalid time', {
        trackId: item.trackId,
        pitch: item.pitch,
        time,
      });
      activeNotes.delete(key);
      return;
    }
    if (node.synth.triggerRelease) {
      // PolySynth: triggerRelease(note, time). MonoSynth/MembraneSynth: triggerRelease(time).
      // Passing a pitch string as the MonoSynth time arg yields cancelAndHoldAtTime(null).
      if (typeof node.synth.maxPolyphony === 'number') {
        node.synth.triggerRelease(item.pitch, time);
      } else {
        node.synth.triggerRelease(time);
      }
    }
    activeNotes.delete(key);
  }

  function resolveWindowEnd(startSeconds) {
    const total = currentSchedule?.totalDurationSeconds || 0;
    const loopWindow = currentLoopSeconds;
    if (
      loopWindow
      && loopWindow.enabled
      && Number.isFinite(loopWindow.endSeconds)
      && loopWindow.endSeconds > loopWindow.startSeconds
      && startSeconds < loopWindow.endSeconds
    ) {
      return {
        endPosition: loopWindow.endSeconds,
        looping: true,
        loopStartSeconds: loopWindow.startSeconds,
      };
    }
    return { endPosition: total, looping: false, loopStartSeconds: null };
  }

  function handleLoopWrap() {
    const loopWindow = currentLoopSeconds;
    if (!loopWindow?.enabled) {
      if (typeof currentOnComplete === 'function') {
        currentOnComplete();
      }
      return;
    }
    const wrapStart = loopWindow.startSeconds;
    log('info', 'Loop wrap', {
      startTick: loopWindow.startTick,
      endTick: loopWindow.endTick,
      startSeconds: wrapStart,
      endSeconds: loopWindow.endSeconds,
      revisionPrefix: revisionPrefix(),
    });
    releaseActiveNotes(Tone.now?.() ?? 0);
    clearScheduledEvents();
    Tone.Transport.position = wrapStart;
    scheduleFromTime(currentSchedule, wrapStart);
  }

  function scheduleFromTime(schedule, startSeconds = 0) {
    const rebuildStarted = (
      typeof performance !== 'undefined' && performance.now
        ? performance.now()
        : Date.now()
    );
    if (!schedule) {
      return { scheduledCount: 0, endPosition: 0, perTrack: {}, looping: false };
    }

    const byTrack = new Map();
    let scheduledCount = 0;
    const window = resolveWindowEnd(startSeconds);
    const endPosition = window.endPosition;

    schedule.items.forEach((item) => {
      if (item.time < startSeconds) {
        if (item.kind === 'controller') {
          const node = trackNodes.get(item.trackId);
          if (node) {
            applyControllerStateAtTime(node, item, startSeconds);
          }
        }
        return;
      }

      // When looping, do not schedule past the loop end (exclusive).
      if (window.looping && item.time >= endPosition) {
        return;
      }

      const node = trackNodes.get(item.trackId);
      // Schedule every routed track; mute/solo is live uiGain, not schedule omission.
      if (!node) {
        return;
      }

      const eventId = Tone.Transport.schedule((time) => {
        try {
          if (item.kind === 'attack') {
            scheduleAttack(node, item, time);
          } else if (item.kind === 'release') {
            scheduleRelease(node, item, time);
          } else if (item.kind === 'controller') {
            applyControllerStateAtTime(node, item, time);
          }
        } catch (error) {
          log('error', 'Schedule callback failed', {
            kind: item.kind,
            trackId: item.trackId,
            pitch: item.pitch,
            message: error?.message,
          });
        }
      }, item.time);

      scheduledEventIds.push(eventId);
      scheduledCount += 1;
      byTrack.set(item.trackId, (byTrack.get(item.trackId) || 0) + 1);
    });

    currentEndPosition = endPosition;
    if (endPosition > startSeconds) {
      endEventId = Tone.Transport.scheduleOnce(() => {
        if (window.looping && currentLoopSeconds?.enabled) {
          handleLoopWrap();
          return;
        }
        if (typeof currentOnComplete === 'function') {
          currentOnComplete();
        }
      }, endPosition);
    } else if (!window.looping && typeof currentOnComplete === 'function') {
      // Already at/ past end with nothing to schedule.
      currentOnComplete();
    }

    const rebuildMs = (
      typeof performance !== 'undefined' && performance.now
        ? performance.now()
        : Date.now()
    ) - rebuildStarted;

    log('debug', 'Schedule summary', {
      scheduledCount,
      endPosition,
      startSeconds,
      looping: window.looping,
      rebuildMs: Math.round(rebuildMs),
      perTrack: Object.fromEntries(byTrack),
    });

    return {
      scheduledCount,
      endPosition,
      perTrack: Object.fromEntries(byTrack),
      looping: window.looping,
    };
  }

  function scheduleEvents(eventsOrSchedule, { onComplete, startSeconds = 0 } = {}) {
    clearScheduledEvents();
    currentOnComplete = onComplete;

    const schedule = Array.isArray(eventsOrSchedule)
      ? {
          items: eventsOrSchedule.flatMap((event) => ([
            {
              kind: 'attack',
              time: event.position,
              trackId: event.trackId,
              pitch: event.pitch,
              velocity: event.velocity,
              velocityMidi: event.velocityMidi,
            },
            {
              kind: 'release',
              time: event.stopPosition,
              trackId: event.trackId,
              pitch: event.pitch,
            },
          ])),
          totalDurationSeconds: eventsOrSchedule.reduce(
            (max, event) => Math.max(max, Number(event.stopPosition) || 0),
            0,
          ),
        }
      : eventsOrSchedule;

    currentSchedule = schedule;
    return scheduleFromTime(schedule, startSeconds);
  }

  function applyTrackOverrides(trackOverrides = {}) {
    currentTrackOverrides = { ...currentTrackOverrides, ...trackOverrides };
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
      if (node.uiGain?.gain) {
        node.uiGain.gain.value = state.effectiveGain;
      }
    });
  }

  function prepare({
    tracks,
    events,
    schedule: incomingSchedule,
    composition,
    tempo,
    trackOverrides = {},
    onComplete,
    startSeconds = null,
    startTick = null,
    loop = null,
  } = {}) {
    if (disposed) {
      throw new Error('Playback engine has been disposed');
    }

    let resolvedSchedule = incomingSchedule;
    if (!resolvedSchedule && composition) {
      resolvedSchedule = compilePlaybackSchedule(composition);
    }
    if (!resolvedSchedule && Array.isArray(events) && events.length) {
      resolvedSchedule = {
        items: events.flatMap((event) => ([
          {
            kind: 'attack',
            time: event.position,
            trackId: event.trackId,
            pitch: event.pitch,
            velocity: event.velocity,
            velocityMidi: event.velocityMidi,
          },
          {
            kind: 'release',
            time: event.stopPosition,
            trackId: event.trackId,
            pitch: event.pitch,
          },
        ])),
        totalDurationSeconds: events.reduce(
          (max, event) => Math.max(max, Number(event.stopPosition) || 0),
          0,
        ),
      };
    }

    currentComposition = composition || null;
    if (loop !== undefined) {
      syncLoopSeconds(loop, currentComposition);
    } else {
      syncLoopSeconds(currentLoop, currentComposition);
    }

    const resolvedStart = resolveStartSeconds({
      startSeconds,
      startTick,
      composition: currentComposition,
      tempo: tempo ?? composition?.tempo,
    });

    log('info', 'Preparing playback', {
      trackCount: Array.isArray(tracks) ? tracks.length : 0,
      attackCount: resolvedSchedule?.summary?.attackCount ?? (Array.isArray(events) ? events.length : 0),
      totalDurationSeconds: resolvedSchedule?.totalDurationSeconds ?? 0,
      tempo,
      startSeconds: resolvedStart,
      startTick: Number.isFinite(Number(startTick)) ? Number(startTick) : null,
      loopEnabled: Boolean(currentLoopSeconds?.enabled),
      loopStartTick: currentLoop?.startTick ?? null,
      loopEndTick: currentLoop?.endTick ?? null,
      revisionPrefix: revisionPrefix(currentComposition),
    });

    clearScheduledEvents();
    buildTrackRoutes(tracks || [], trackOverrides);
    if (Number.isFinite(tempo) && tempo > 0) {
      Tone.Transport.bpm.value = tempo;
    }
    currentOnComplete = onComplete;
    Tone.Transport.position = resolvedStart;
    return scheduleEvents(resolvedSchedule, { onComplete, startSeconds: resolvedStart });
  }

  async function start() {
    try {
      await Tone.start();
      Tone.Transport.start();
      log('info', 'Playback started', {
        transportState: Tone.Transport.state,
        position: Tone.Transport.seconds,
        loopEnabled: Boolean(currentLoopSeconds?.enabled),
        revisionPrefix: revisionPrefix(),
      });
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
    currentSchedule = null;
    currentTracks = [];
    currentTrackOverrides = {};
    currentComposition = null;
    currentOnComplete = null;
    currentEndPosition = 0;
    // Keep loop tick bounds across stop; seconds re-resolve on next prepare.
    currentLoopSeconds = null;
    log('info', 'Playback stopped', {
      transportState: Tone.Transport.state,
      clearedEvents: cleared,
      disposedNodes: disposedCount,
      seekToStart,
      loopEnabled: Boolean(currentLoop?.enabled),
      revisionPrefix: 'cleared',
    });
  }

  function seekToStart() {
    return seek(0);
  }

  function seek(seconds = 0) {
    const target = Math.max(0, Number(seconds) || 0);
    clearScheduledEvents();
    Tone.Transport.position = target;
    if (currentSchedule && trackNodes.size > 0) {
      scheduleFromTime(currentSchedule, target);
    }
    log('info', 'Seeked transport', { position: target, endPosition: currentEndPosition });
    return target;
  }

  function seekToTick(tick = 0) {
    const seconds = resolveSecondsFromTick(tick);
    return seek(seconds);
  }

  /**
   * Enable/disable or replace loop while paused or playing.
   * Pass null to clear. `{ enabled: false }` keeps bounds but disables wrap.
   */
  function setLoop(loop) {
    const previous = currentLoop;
    if (loop == null) {
      currentLoop = null;
      currentLoopSeconds = null;
      log('info', 'Loop cleared', { revisionPrefix: revisionPrefix() });
    } else {
      syncLoopSeconds(loop, currentComposition);
      if (!currentLoop) {
        log('warn', 'Rejected invalid playback loop', {
          startTick: loop?.startTick,
          endTick: loop?.endTick,
          enabled: loop?.enabled,
        });
      } else {
        log('info', 'Loop updated', {
          startTick: currentLoop.startTick,
          endTick: currentLoop.endTick,
          enabled: currentLoop.enabled,
          startSeconds: currentLoopSeconds?.startSeconds ?? null,
          endSeconds: currentLoopSeconds?.endSeconds ?? null,
          revisionPrefix: revisionPrefix(),
          previousEnabled: previous?.enabled ?? false,
        });
      }
    }

    if (currentSchedule && trackNodes.size > 0) {
      const pos = Math.max(0, Number(Tone.Transport.seconds) || 0);
      clearScheduledEvents();
      return scheduleFromTime(currentSchedule, pos);
    }
    return null;
  }

  function getLoop() {
    return currentLoop
      ? { ...currentLoop }
      : null;
  }

  function rebuildSchedule({ startSeconds = Tone.Transport.seconds } = {}) {
    if (!currentSchedule || !currentTracks.length) {
      return null;
    }
    clearScheduledEvents();
    buildTrackRoutes(currentTracks, currentTrackOverrides);
    return scheduleFromTime(currentSchedule, Math.max(0, Number(startSeconds) || 0));
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

  function getEndPositionSeconds() {
    return currentEndPosition;
  }

  function getTrackEffectiveGain(trackId) {
    const node = trackNodes.get(String(trackId));
    return node?.uiGain?.gain?.value;
  }

  function dispose() {
    stop({ seekToStart: true });
    currentLoop = null;
    currentLoopSeconds = null;
    disposed = true;
    log('debug', 'Playback engine disposed');
  }

  return {
    prepare,
    start,
    pause,
    resume,
    stop,
    seek,
    seekToTick,
    seekToStart,
    setLoop,
    getLoop,
    rebuildSchedule,
    applyTrackOverrides,
    getPositionSeconds,
    getTransportState,
    getScheduledEventCount,
    getTrackNodeCount,
    getEndPositionSeconds,
    getTrackEffectiveGain,
    dispose,
  };
}
