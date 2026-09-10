/**
 * Thin Tone.js playback engine for composition.v2 multi-track scheduling.
 * Accepts injected Tone + logger for deterministic tests.
 *
 * Transport/loop state is ephemeral engine state — never written into Composition V2.
 */

import { audibleRevisionKey } from './compositionCanonical.js';
import {
  buildTrackPlaybackStates,
  createTrackPlaybackState,
  midiPanToStereo,
  midiVolumeToGain,
  resolveEffectiveTrackGains,
} from './playbackTracks.js';
import {
  compilePlaybackSchedule,
  controllerStateAtTick,
  logicalNotesSoundingAtTick,
} from './playbackEvents.js';
import { prepareTrackInstrumentAdapters } from './playbackInstrumentLoader.js';
import { secondsToPlaybackPosition, ticksToPlaybackSeconds } from './playbackPosition.js';
import { clampSeekSecondsToLoop, normalizePlaybackLoop } from './playbackLoop.js';

const RELOCATION_FADE_SECONDS = 0.02;
/** Disable per-track meters above this route count (CPU budget). */
const METER_TRACK_BUDGET = 24;

/** Relative output calibration per strategy/preset family (browser only). */
const PROFILE_OUTPUT_GAIN = Object.freeze({
  piano_keyboard: 0.82,
  bass: 0.92,
  bass_synth: 0.92,
  strings_pad: 0.72,
  guitar_pluck: 0.8,
  brass: 0.78,
  lead_synth: 0.75,
  woodwind_lead: 0.75,
  mallet: 0.85,
  drums: 0.88,
  drums_basic: 0.88,
});

/**
 * Soft velocity curve — preserves extremes without rewriting MIDI velocity.
 * @param {number} velocity 0–1
 * @returns {number}
 */
export function mapPlaybackVelocity(velocity) {
  const v = Math.max(0, Math.min(1, Number(velocity) || 0));
  // Mild concave curve so soft notes stay soft and forte remains distinct.
  return Number((v ** 1.15).toFixed(4));
}

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
  let operationEpoch = 0;
  let sessionId = 0;
  let sourceKey = null;
  let masterGain = null;
  let limiter = null;
  let sharedReverb = null;
  let effectsReady = false;
  let relocating = false;
  let metersEnabled = true;

  function log(level, message, context = {}) {
    const method = typeof logger[level] === 'function' ? logger[level].bind(logger) : logger.log?.bind(logger);
    if (method) {
      method(`[tonePlaybackEngine] ${message}`, context);
    }
  }

  function noteKey(item) {
    if (item?.noteId) {
      return String(item.noteId);
    }
    return `${item?.trackId}:${item?.pitch}`;
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

  function beginOperation(reason = 'op') {
    operationEpoch += 1;
    const opId = operationEpoch;
    log('debug', 'Begin operation', { opId, reason, sessionId });
    return opId;
  }

  function isCurrentOperation(opId) {
    return opId === operationEpoch && !disposed;
  }

  function ensureMasterGain() {
    if (masterGain) {
      return masterGain;
    }
    masterGain = new Tone.Gain(1);
    if (typeof Tone.Limiter === 'function') {
      try {
        limiter = new Tone.Limiter(-1.5);
        masterGain.connect(limiter);
        limiter.toDestination();
      } catch (error) {
        log('warn', 'Limiter unavailable; direct master output', { message: error?.message });
        limiter = null;
        masterGain.toDestination();
      }
    } else {
      masterGain.toDestination();
    }
    return masterGain;
  }

  async function ensureSharedEffects() {
    ensureMasterGain();
    if (effectsReady) {
      return;
    }
    if (typeof Tone.Reverb === 'function' && !sharedReverb) {
      try {
        sharedReverb = new Tone.Reverb({ decay: 2.2, preDelay: 0.015, wet: 1 });
        if (typeof sharedReverb.generate === 'function') {
          await sharedReverb.generate();
        }
        sharedReverb.connect(masterGain);
        log('info', 'Shared reverb ready', { sessionId });
      } catch (error) {
        log('warn', 'Shared reverb unavailable', { message: error?.message });
        try {
          sharedReverb?.dispose?.();
        } catch {
          // ignore
        }
        sharedReverb = null;
      }
    }
    effectsReady = true;
    log('info', 'Playback effects ready', {
      sessionId,
      hasReverb: Boolean(sharedReverb),
      hasLimiter: Boolean(limiter),
    });
  }

  function disposeSharedEffects({ clearMaster = false } = {}) {
    try {
      sharedReverb?.dispose?.();
    } catch (error) {
      log('warn', 'Shared reverb dispose failed', { message: error?.message });
    }
    sharedReverb = null;
    effectsReady = false;
    if (clearMaster) {
      try {
        limiter?.dispose?.();
      } catch (error) {
        log('warn', 'Limiter dispose failed', { message: error?.message });
      }
      limiter = null;
      try {
        masterGain?.dispose?.();
      } catch (error) {
        log('warn', 'Master gain dispose failed', { message: error?.message });
      }
      masterGain = null;
    }
  }

  function setMasterGainImmediate(value) {
    const node = ensureMasterGain();
    if (node.gain) {
      node.gain.value = value;
    }
  }

  function rampMasterGain(value, durationSeconds = RELOCATION_FADE_SECONDS) {
    const node = ensureMasterGain();
    if (node?.gain && typeof node.gain.linearRampTo === 'function') {
      node.gain.linearRampTo(value, Math.max(0, durationSeconds));
      return;
    }
    setMasterGainImmediate(value);
  }

  function rampParam(param, value, durationSeconds = 0.03) {
    if (!param) {
      return;
    }
    if (typeof param.linearRampTo === 'function') {
      param.linearRampTo(value, Math.max(0, durationSeconds));
      return;
    }
    param.value = value;
  }

  function disposeTrackNodes() {
    const beforeCount = trackNodes.size;
    let disposedCount = 0;
    trackNodes.forEach((node, trackId) => {
      try {
        node.adapter?.dispose?.();
        // Adapter dispose already disposes its Tone node when present.
        if (!node.adapter) {
          node.synth?.dispose?.();
        }
        node.profileGain?.dispose?.();
        node.expressionGain?.dispose?.();
        node.volumeGain?.dispose?.();
        node.uiGain?.dispose?.();
        node.panner?.dispose?.();
        node.sendGain?.dispose?.();
        node.meter?.dispose?.();
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

  /**
   * Clear only engine-owned Transport callbacks — never Tone.Transport.cancel().
   * Does not release or clear activeNotes (caller decides).
   */
  function clearOwnedTransportEvents() {
    const owned = [...scheduledEventIds];
    if (endEventId != null) {
      owned.push(endEventId);
    }
    const beforeCount = owned.length;
    owned.forEach((id) => {
      try {
        if (typeof Tone.Transport.clear === 'function') {
          Tone.Transport.clear(id);
        } else if (typeof Tone.Transport.cancel === 'function') {
          Tone.Transport.cancel(id);
        }
      } catch (error) {
        log('warn', 'Failed to clear owned transport event', {
          eventId: id,
          message: error?.message,
        });
      }
    });
    scheduledEventIds = [];
    endEventId = null;
    log('debug', 'Cleared owned scheduled events', { clearedCount: beforeCount, sessionId });
    return beforeCount;
  }

  function clearScheduledEvents() {
    const cleared = clearOwnedTransportEvents();
    activeNotes = new Map();
    return cleared;
  }

  function releaseActiveNotes(time) {
    const entries = Array.from(activeNotes.values());
    entries.forEach((note) => {
      const node = trackNodes.get(note.trackId);
      if (!node) {
        return;
      }
      try {
        scheduleRelease(node, { trackId: note.trackId, pitch: note.pitch, noteId: note.noteId }, time);
      } catch (error) {
        log('error', 'Active note release failed', {
          trackId: note.trackId,
          message: error?.message,
        });
      }
    });
    activeNotes = new Map();
  }

  function resolveProfileOutputGain(strategy) {
    const key = strategy?.presetId || strategy?.id;
    if (key && PROFILE_OUTPUT_GAIN[key] != null) {
      return PROFILE_OUTPUT_GAIN[key];
    }
    return 0.85;
  }

  function buildTrackRoutes(tracks, trackOverrides = {}, adaptersByTrackId = null) {
    disposeTrackNodes();
    currentTracks = tracks || [];
    currentTrackOverrides = trackOverrides;
    const states = resolveEffectiveTrackGains(buildTrackPlaybackStates(tracks, trackOverrides));
    const routes = new Map();
    const useMeters = metersEnabled && states.length <= METER_TRACK_BUDGET;
    if (metersEnabled && !useMeters) {
      log('warn', 'Track meters disabled over CPU budget', {
        trackCount: states.length,
        budget: METER_TRACK_BUDGET,
      });
    }

    states.forEach((state) => {
      const sourceTrack = (tracks || []).find((track) => String(track?.id ?? '') === state.trackId);
      const persistedVolume = Number.isFinite(Number(sourceTrack?.volume))
        ? Math.max(0, Math.min(127, Number(sourceTrack.volume)))
        : state.volumeMidi;

      let synth;
      let profileGain;
      let expressionGain;
      let volumeGain;
      let uiGain;
      let panner;
      let sendGain;
      let meter = null;
      let adapter = adaptersByTrackId?.get?.(state.trackId) || null;
      try {
        const profileOut = resolveProfileOutputGain(state.strategy);
        profileGain = new Tone.Gain(profileOut);
        expressionGain = new Tone.Gain(1);
        volumeGain = new Tone.Gain(midiVolumeToGain(persistedVolume));
        // Session mute/solo/trim only — canonical volume stays on volumeGain.
        uiGain = new Tone.Gain(state.effectiveGain);
        panner = new Tone.Panner(state.panStereo || 0);
        const initialSend = state.audible
          ? Math.max(0, Math.min(1, Number(state.reverbSend) || 0))
          : 0;
        sendGain = new Tone.Gain(initialSend);

        if (adapter?.getToneNode?.()) {
          synth = adapter.getToneNode();
          adapter.connect(profileGain);
        } else {
          adapter = null;
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
          synth.connect(profileGain);
        }

        profileGain.connect(expressionGain);
        expressionGain.connect(volumeGain);
        volumeGain.connect(uiGain);
        // Post-fader dry path
        uiGain.connect(panner);
        if (typeof Tone.Meter === 'function' && useMeters) {
          try {
            meter = new Tone.Meter({ normalRange: true, smoothing: 0.8 });
            panner.connect(meter);
          } catch (error) {
            log('warn', 'Track meter unavailable', {
              trackId: state.trackId,
              message: error?.message,
            });
            meter = null;
          }
        }
        panner.connect(ensureMasterGain());
        // Post-fader wet send (mute/solo also zeros send via applyTrackOverrides)
        uiGain.connect(sendGain);
        if (sharedReverb) {
          sendGain.connect(sharedReverb);
        }
      } catch (error) {
        log('error', 'Track route creation failed', {
          trackId: state.trackId,
          strategy: state.strategy?.id,
          message: error?.message,
        });
        adapter?.dispose?.();
        synth?.dispose?.();
        profileGain?.dispose?.();
        expressionGain?.dispose?.();
        volumeGain?.dispose?.();
        uiGain?.dispose?.();
        panner?.dispose?.();
        sendGain?.dispose?.();
        meter?.dispose?.();
        return;
      }

      const node = {
        synth,
        adapter,
        profileGain,
        expressionGain,
        volumeGain,
        uiGain,
        panner,
        sendGain,
        meter,
        state,
        instrumentStatus: adapter?.getStatus?.() || {
          ready: true,
          fallback: true,
          profileId: state.strategy?.presetId || state.strategy?.id,
          reasonCode: 'inline_synth',
          engine: 'tone_synth',
        },
      };
      trackNodes.set(state.trackId, node);
      routes.set(state.trackId, node);
    });

    log('debug', 'Track routes built', {
      trackCount: routes.size,
      nodeCount: trackNodes.size,
      adapterCount: adaptersByTrackId ? adaptersByTrackId.size : 0,
      metersEnabled: useMeters,
      hasSharedReverb: Boolean(sharedReverb),
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
    const rawVelocity = Number.isFinite(item.velocity) ? item.velocity : (item.velocityMidi || 80) / 127;
    const velocity = mapPlaybackVelocity(rawVelocity);
    if (node.adapter?.attack) {
      node.adapter.attack(item.noteId || noteKey(item), pitch, velocity, time);
    } else if (node.synth.triggerAttack) {
      node.synth.triggerAttack(pitch, time, velocity);
    } else if (node.synth.triggerAttackRelease) {
      node.synth.triggerAttackRelease(pitch, 3600, time, velocity);
    }
    activeNotes.set(noteKey(item), {
      trackId: item.trackId,
      pitch,
      noteId: item.noteId || null,
      attackTime: item.time,
    });
  }

  function scheduleRelease(node, item, time) {
    const key = noteKey(item);
    if (!activeNotes.has(key)) {
      return;
    }
    if (typeof time !== 'number' || !Number.isFinite(time)) {
      log('warn', '[FIX:tone-release] Skipping release with invalid time', {
        trackId: item.trackId,
        noteId: item.noteId || null,
        time,
      });
      activeNotes.delete(key);
      return;
    }
    if (node.adapter?.release) {
      node.adapter.release(item.noteId || key, time, item.pitch);
    } else if (node.synth.triggerRelease) {
      // PolySynth: triggerRelease(note, time). MonoSynth/MembraneSynth: triggerRelease(time).
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
    log('info', 'Loop wrap', {
      startTick: loopWindow.startTick,
      endTick: loopWindow.endTick,
      startSeconds: loopWindow.startSeconds,
      endSeconds: loopWindow.endSeconds,
      revisionPrefix: revisionPrefix(),
      sessionId,
    });
    relocate({
      targetSeconds: loopWindow.startSeconds,
      reason: 'loop_wrap',
      playing: Tone.Transport.state === 'started',
    });
  }

  /**
   * Single relocation path for start/seek/pause-resume window rebuild/loop wrap/live loop change.
   */
  function relocate({
    targetSeconds = 0,
    reason = 'relocate',
    playing = false,
    reconstructHeld = true,
  } = {}) {
    if (disposed || !currentSchedule) {
      return { scheduledCount: 0, endPosition: 0, looping: false };
    }
    if (relocating) {
      log('debug', 'Relocate skipped; already relocating', { reason });
      return { scheduledCount: 0, endPosition: currentEndPosition, looping: Boolean(currentLoopSeconds?.enabled) };
    }
    const opId = beginOperation(reason);
    relocating = true;
    let target = Math.max(0, Number(targetSeconds) || 0);
    if (currentLoopSeconds?.enabled) {
      const clamped = clampSeekSecondsToLoop(target, currentLoopSeconds);
      if (clamped !== target) {
        log('debug', 'Seek clamped into enabled loop', {
          requested: target,
          clamped,
          loopStart: currentLoopSeconds.startSeconds,
          loopEnd: currentLoopSeconds.endSeconds,
          opId,
        });
        target = clamped;
      }
    }

    log('debug', 'Relocate transport', {
      reason,
      target,
      opId,
      sessionId,
      ownedEventCount: scheduledEventIds.length,
      revisionPrefix: revisionPrefix(),
    });

    rampMasterGain(0, RELOCATION_FADE_SECONDS);
    releaseActiveNotes(Tone.now?.() ?? 0);
    clearScheduledEvents();
    if (!isCurrentOperation(opId)) {
      relocating = false;
      log('warn', 'Stale relocate aborted after clear', { opId, sessionId });
      return { scheduledCount: 0, endPosition: 0, looping: false };
    }

    Tone.Transport.position = target;
    restoreControllerStateAtSeconds(target);
    if (reconstructHeld) {
      reconstructHeldNotesAtSeconds(target);
    }
    const summary = scheduleFromTime(currentSchedule, target);
    if (playing || Tone.Transport.state === 'started') {
      rampMasterGain(1, RELOCATION_FADE_SECONDS);
    } else {
      setMasterGainImmediate(1);
    }
    relocating = false;
    return summary;
  }

  function secondsToTickOnAudition(seconds) {
    if (!currentComposition) {
      return 0;
    }
    const position = secondsToPlaybackPosition(seconds, {
      composition: currentComposition,
      tempo: currentComposition.tempo,
      ticksPerQuarter: currentComposition.ticks_per_quarter,
      timeSignature: currentComposition.time_signature,
    });
    return position.tick;
  }

  function restoreControllerStateAtSeconds(seconds) {
    if (!currentSchedule) {
      return;
    }
    const tick = secondsToTickOnAudition(seconds);
    trackNodes.forEach((node, trackId) => {
      const state = controllerStateAtTick(currentSchedule, trackId, tick);
      applyControllerStateAtTime(node, {
        parameter: 'volume',
        value: state.volume,
        time: seconds,
        interpolation: 'step',
      }, seconds);
      applyControllerStateAtTime(node, {
        parameter: 'pan',
        value: state.pan,
        time: seconds,
        interpolation: 'step',
      }, seconds);
      applyControllerStateAtTime(node, {
        parameter: 'expression',
        value: state.expression,
        time: seconds,
        interpolation: 'step',
      }, seconds);
    });
  }

  function reconstructHeldNotesAtSeconds(seconds) {
    if (!currentSchedule?.logicalNotes?.length) {
      return 0;
    }
    const tick = secondsToTickOnAudition(seconds);
    const held = logicalNotesSoundingAtTick(currentSchedule, tick);
    held.forEach((note) => {
      const node = trackNodes.get(note.trackId);
      if (!node) {
        return;
      }
      scheduleAttack(node, {
        trackId: note.trackId,
        pitch: note.pitch,
        noteId: note.noteId,
        velocity: note.velocity,
        velocityMidi: note.velocityMidi,
        time: seconds,
      }, Tone.now?.() ?? 0);
    });
    log('debug', 'Reconstructed held notes', {
      count: held.length,
      tick,
      seconds,
      sessionId,
    });
    return held.length;
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
    clearOwnedTransportEvents();
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
    // Rebuild session-only UI gains from canonical track + overrides (no double volume).
    const states = resolveEffectiveTrackGains(
      Array.from(trackNodes.values()).map((node) => {
        const sourceTrack = (currentTracks || []).find(
          (track) => String(track?.id ?? '') === node.state.trackId,
        ) || {
          id: node.state.trackId,
          volume: node.state.volumeMidi,
          pan: node.state.pan,
          instrument: node.state.instrument,
          role: node.state.role,
          midi_program: node.state.midiProgram,
          channel: node.state.channel,
          is_drum: node.state.isDrum,
        };
        const mergedOverrides = {
          ...(currentTrackOverrides[node.state.trackId] || {}),
          ...(trackOverrides[node.state.trackId] || {}),
        };
        return createTrackPlaybackState(sourceTrack, mergedOverrides);
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
        // Session mute/solo/trim — also silences wet send input.
        rampParam(node.uiGain.gain, state.effectiveGain, 0.04);
      }
      if (node.panner?.pan && Number.isFinite(state.panStereo)) {
        rampParam(node.panner.pan, state.panStereo, 0.04);
      }
      if (node.sendGain?.gain) {
        const send = state.audible ? Math.max(0, Math.min(1, Number(state.reverbSend) || 0)) : 0;
        rampParam(node.sendGain.gain, send, 0.04);
      }
    });
  }

  /**
   * Bounded activity snapshot for UI meters (call ≤ 10–20 Hz).
   * @returns {{ tracks: Record<string, number>, clipped: boolean }}
   */
  function getActivitySnapshot() {
    const tracks = {};
    let clipped = false;
    if (!metersEnabled) {
      return { tracks, clipped };
    }
    trackNodes.forEach((node, trackId) => {
      let level = 0;
      const meterValue = node.meter?.getValue?.();
      if (typeof meterValue === 'number') {
        level = Math.max(0, Math.min(1, meterValue));
      } else if (Array.isArray(meterValue) && meterValue.length) {
        level = Math.max(0, Math.min(1, Math.max(...meterValue.map((value) => Number(value) || 0))));
      } else if (node.uiGain?.gain) {
        level = node.state?.audible ? Math.min(1, Number(node.uiGain.gain.value) || 0) : 0;
      }
      tracks[trackId] = Number(level.toFixed(3));
      if (level >= 0.98) {
        clipped = true;
      }
    });
    return { tracks, clipped };
  }

  async function prepare({
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
    sourceKey: nextSourceKey = null,
    loadInstruments = true,
  } = {}) {
    if (disposed) {
      throw new Error('Playback engine has been disposed');
    }

    sessionId += 1;
    const opId = beginOperation('prepare');
    sourceKey = nextSourceKey != null ? String(nextSourceKey) : sourceKey;

    let resolvedSchedule = incomingSchedule;
    if (!resolvedSchedule && composition) {
      resolvedSchedule = compilePlaybackSchedule(composition);
    }
    if (!resolvedSchedule && Array.isArray(events) && events.length) {
      resolvedSchedule = {
        items: events.flatMap((event) => ([
          {
            kind: 'attack',
            noteId: event.noteId,
            time: event.position,
            trackId: event.trackId,
            pitch: event.pitch,
            velocity: event.velocity,
            velocityMidi: event.velocityMidi,
          },
          {
            kind: 'release',
            noteId: event.noteId,
            time: event.stopPosition,
            trackId: event.trackId,
            pitch: event.pitch,
          },
        ])),
        logicalNotes: [],
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

    let resolvedStart = resolveStartSeconds({
      startSeconds,
      startTick,
      composition: currentComposition,
      tempo: tempo ?? composition?.tempo,
    });
    if (currentLoopSeconds?.enabled) {
      resolvedStart = clampSeekSecondsToLoop(resolvedStart, currentLoopSeconds);
    }

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
      sourceKey,
      sessionId,
      opId,
      loadInstruments,
    });

    clearScheduledEvents();
    await ensureSharedEffects();
    if (!isCurrentOperation(opId)) {
      log('warn', 'Stale prepare aborted after effects load', { opId, sessionId });
      return { scheduledCount: 0, endPosition: 0, looping: false };
    }
    setMasterGainImmediate(1);

    let adapters = null;
    if (loadInstruments && Array.isArray(tracks) && tracks.length) {
      try {
        adapters = await prepareTrackInstrumentAdapters({ Tone, tracks });
      } catch (error) {
        log('error', 'Instrument adapter preparation failed; inline synth fallback', {
          message: error?.message,
          opId,
        });
        adapters = null;
      }
    }
    if (!isCurrentOperation(opId)) {
      log('warn', 'Stale prepare aborted after instrument load', { opId, sessionId });
      return { scheduledCount: 0, endPosition: 0, looping: false };
    }

    buildTrackRoutes(tracks || [], trackOverrides, adapters);
    if (Number.isFinite(tempo) && tempo > 0) {
      Tone.Transport.bpm.value = tempo;
    }
    currentOnComplete = onComplete;
    currentSchedule = resolvedSchedule;
    Tone.Transport.position = resolvedStart;
    if (!isCurrentOperation(opId)) {
      log('warn', 'Stale prepare aborted', { opId, sessionId });
      return { scheduledCount: 0, endPosition: 0, looping: false };
    }
    restoreControllerStateAtSeconds(resolvedStart);
    reconstructHeldNotesAtSeconds(resolvedStart);
    return scheduleEvents(resolvedSchedule, { onComplete, startSeconds: resolvedStart });
  }

  async function start() {
    const opId = beginOperation('start');
    try {
      await Tone.start();
      if (!isCurrentOperation(opId)) {
        log('warn', 'Stale Tone.start ignored', { opId, sessionId });
        return Tone.Transport.state;
      }
      setMasterGainImmediate(1);
      Tone.Transport.start();
      log('info', 'Playback started', {
        transportState: Tone.Transport.state,
        position: Tone.Transport.seconds,
        loopEnabled: Boolean(currentLoopSeconds?.enabled),
        revisionPrefix: revisionPrefix(),
        sourceKey,
        sessionId,
        opId,
      });
      return Tone.Transport.state;
    } catch (error) {
      log('error', 'Playback start failed', { message: error?.message, opId, sessionId });
      throw error;
    }
  }

  function pause() {
    Tone.Transport.pause();
    log('info', 'Playback paused', {
      transportState: Tone.Transport.state,
      position: Tone.Transport.seconds,
      sessionId,
    });
  }

  function resume() {
    const pos = Math.max(0, Number(Tone.Transport.seconds) || 0);
    relocate({
      targetSeconds: pos,
      reason: 'resume',
      playing: true,
      reconstructHeld: true,
    });
    Tone.Transport.start();
    log('info', 'Playback resumed', {
      transportState: Tone.Transport.state,
      position: Tone.Transport.seconds,
      sessionId,
    });
  }

  function stop({ seekToStart = true } = {}) {
    beginOperation('stop');
    rampMasterGain(0, RELOCATION_FADE_SECONDS);
    releaseActiveNotes(Tone.now?.() ?? 0);
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
    // Drop wet tails so the next prepare regenerates a clean shared reverb.
    disposeSharedEffects({ clearMaster: false });
    currentSchedule = null;
    currentTracks = [];
    currentTrackOverrides = {};
    currentComposition = null;
    currentOnComplete = null;
    currentEndPosition = 0;
    // Keep loop tick bounds across stop; seconds re-resolve on next prepare.
    currentLoopSeconds = null;
    if (masterGain) {
      setMasterGainImmediate(1);
    }
    log('info', 'Playback stopped', {
      transportState: Tone.Transport.state,
      clearedEvents: cleared,
      disposedNodes: disposedCount,
      seekToStart,
      loopEnabled: Boolean(currentLoop?.enabled),
      revisionPrefix: 'cleared',
      sourceKey,
      sessionId,
    });
  }

  function seekToStart() {
    return seek(0);
  }

  function seek(seconds = 0) {
    const playing = Tone.Transport.state === 'started';
    const summary = relocate({
      targetSeconds: seconds,
      reason: 'seek',
      playing,
      reconstructHeld: true,
    });
    const position = Math.max(0, Number(Tone.Transport.seconds) || 0);
    log('info', 'Seeked transport', {
      position,
      endPosition: currentEndPosition,
      scheduledCount: summary.scheduledCount,
      sessionId,
    });
    return position;
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
      log('info', 'Loop cleared', { revisionPrefix: revisionPrefix(), sessionId });
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
          sessionId,
        });
      }
    }

    if (currentSchedule && trackNodes.size > 0) {
      const pos = Math.max(0, Number(Tone.Transport.seconds) || 0);
      return relocate({
        targetSeconds: pos,
        reason: 'set_loop',
        playing: Tone.Transport.state === 'started',
        reconstructHeld: true,
      });
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
    buildTrackRoutes(currentTracks, currentTrackOverrides);
    return relocate({
      targetSeconds: Math.max(0, Number(startSeconds) || 0),
      reason: 'rebuild',
      playing: Tone.Transport.state === 'started',
      reconstructHeld: true,
    });
  }

  function getPositionSeconds() {
    return Number(Tone.Transport.seconds) || 0;
  }

  /**
   * Authoritative position against the auditioned composition timeline.
   */
  function getPlaybackPosition() {
    const seconds = getPositionSeconds();
    if (!currentComposition) {
      return { seconds, tick: 0, bar: 1 };
    }
    return secondsToPlaybackPosition(seconds, {
      composition: currentComposition,
      tempo: currentComposition.tempo,
      ticksPerQuarter: currentComposition.ticks_per_quarter,
      timeSignature: currentComposition.time_signature,
    });
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

  function getTrackSendGain(trackId) {
    const node = trackNodes.get(String(trackId));
    return node?.sendGain?.gain?.value;
  }

  function getSessionId() {
    return sessionId;
  }

  function getOperationEpoch() {
    return operationEpoch;
  }

  function getSourceKey() {
    return sourceKey;
  }

  /**
   * Physically stop when source identity or audible revision changes.
   */
  function invalidateSource({ nextSourceKey = null, reason = 'source_change' } = {}) {
    log('info', 'Playback source invalidated', {
      previousSourceKey: sourceKey,
      nextSourceKey,
      reason,
      sessionId,
    });
    sourceKey = nextSourceKey != null ? String(nextSourceKey) : null;
    stop({ seekToStart: true });
  }

  function dispose() {
    stop({ seekToStart: true });
    currentLoop = null;
    currentLoopSeconds = null;
    disposeSharedEffects({ clearMaster: true });
    disposed = true;
    log('debug', 'Playback engine disposed');
  }

  function getInstrumentStatuses() {
    const statuses = {};
    trackNodes.forEach((node, trackId) => {
      statuses[trackId] = node.instrumentStatus
        ? { ...node.instrumentStatus }
        : { ready: false, fallback: true, profileId: 'unknown', reasonCode: 'missing' };
    });
    return statuses;
  }

  function hasSharedReverb() {
    return Boolean(sharedReverb);
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
    getActivitySnapshot,
    getInstrumentStatuses,
    hasSharedReverb,
    getPositionSeconds,
    getPlaybackPosition,
    getTransportState,
    getScheduledEventCount,
    getTrackNodeCount,
    getEndPositionSeconds,
    getTrackEffectiveGain,
    getTrackSendGain,
    getSessionId,
    getOperationEpoch,
    getSourceKey,
    invalidateSource,
    dispose,
  };
}
