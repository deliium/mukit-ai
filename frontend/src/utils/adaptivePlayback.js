/**
 * Apply an adaptive.playback.runtime.v1 instruction block to the working transport.
 * Boundaries and layer ids stay on the server. This module only seeks, loops, and ramps gain.
 */

import { createAppLogger } from './appLogger.js';

const log = createAppLogger('adaptivePlayback');

function engineLoop(loop) {
  if (!loop) {
    return null;
  }
  return {
    enabled: Boolean(loop.enabled),
    startTick: loop.start_tick ?? loop.startTick ?? 0,
    endTick: loop.end_tick ?? loop.endTick ?? 0,
  };
}

function fadeSeconds(engine, fromTick, toTick) {
  const start = Number(fromTick);
  const end = Number(toTick);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return 0;
  }
  if (typeof engine.secondsBetweenTicks === 'function') {
    return Math.max(0, Number(engine.secondsBetweenTicks(start, end)) || 0);
  }
  return 0;
}

function captureMemory(engine, mixer, instructions) {
  const gains = {};
  for (const row of instructions.track_gains || []) {
    if (!row?.track_id || typeof mixer.getTrackGain !== 'function') {
      continue;
    }
    gains[row.track_id] = mixer.getTrackGain(row.track_id);
  }
  return {
    loop: typeof engine.getLoop === 'function' ? engine.getLoop() : null,
    gains,
  };
}

function restoreMemory(engine, mixer, memory) {
  if (memory?.loop) {
    engine.setLoop?.(memory.loop);
  } else {
    engine.setLoop?.(null);
  }
  for (const [trackId, gain] of Object.entries(memory?.gains || {})) {
    mixer.rampTrack?.(trackId, gain, 0);
  }
  mixer.rampMaster?.(1, 0);
}

/**
 * Remembered loop and per-track gains from before playback, or the same memory on later steps.
 * `stop: true` restores that memory and stops the transport. `stop: false` never calls `engine.stop`.
 *
 * @param {object} engine
 * @param {{ getTrackGain?: Function, rampTrack?: Function, rampMaster?: Function }} mixer
 * @param {{ loop: object|null, gains: Record<string, number> }|null} previous
 * @param {{ instructions?: object, position_tick?: number }} snapshot
 */
export function applyAdaptivePlaybackInstructions(engine, mixer, previous, snapshot) {
  const instructions = snapshot?.instructions || {};
  const memory = previous
    ? { loop: previous.loop, gains: { ...previous.gains } }
    : captureMemory(engine, mixer, instructions);

  if (instructions.stop === true) {
    restoreMemory(engine, mixer, memory);
    engine.stop?.();
    return memory;
  }

  if (instructions.loop) {
    engine.setLoop?.(engineLoop(instructions.loop));
  }
  const seekTick = instructions.seek_tick;
  if (seekTick != null && engine.getTransportState?.() === 'started') {
    engine.seekToTick?.(seekTick);
  }

  const position = Number(snapshot?.position_tick);
  const now = Number.isFinite(position) ? position : 0;
  for (const row of instructions.track_gains || []) {
    if (!row?.track_id) {
      continue;
    }
    mixer.rampTrack?.(
      row.track_id,
      row.target_gain,
      fadeSeconds(engine, now, row.fade_end_tick),
    );
  }
  if (instructions.fade_start_tick != null) {
    mixer.rampMaster?.(1, fadeSeconds(engine, instructions.fade_start_tick, now));
  }
  return memory;
}

export function noteAdaptivePlayback(snapshot) {
  if (!snapshot) {
    return;
  }
  log.info('adaptive playback', {
    last_event: snapshot.telemetry?.last_event ?? null,
    position_tick: snapshot.position_tick ?? null,
    runtime_state_id: snapshot.runtime_state_id ?? null,
  });
}
