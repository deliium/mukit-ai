/**
 * Build deterministic Tone-ready playback events from Composition V1 track events.
 * Preserves tick timing, velocity, polyphony, and track metadata without reading harmony.
 */

export function buildCanonicalPlaybackEvents(musicJson) {
  if (!Array.isArray(musicJson?.tracks)) {
    console.warn('[playbackEvents] Canonical playback rejected because tracks is not an array', {
      schemaVersion: musicJson?.schema_version,
    });
    return [];
  }

  const tempo = Number(musicJson.tempo || 100);
  const ticksPerQuarter = Number(musicJson.ticks_per_quarter || 480);
  if (!Number.isFinite(tempo) || tempo <= 0 || !Number.isFinite(ticksPerQuarter) || ticksPerQuarter <= 0) {
    console.warn('[playbackEvents] Canonical playback rejected because timing metadata is invalid', {
      tempo: musicJson.tempo,
      ticksPerQuarter: musicJson.ticks_per_quarter,
    });
    return [];
  }

  const secondsPerTick = 60 / tempo / ticksPerQuarter;
  const built = [];

  musicJson.tracks.forEach((track, trackIndex) => {
    const trackId = String(track?.id ?? `track-${trackIndex}`);
    const trackName = typeof track?.name === 'string' && track.name.trim() ? track.name.trim() : trackId;
    const instrument = typeof track?.instrument === 'string' ? track.instrument : '';
    const role = typeof track?.role === 'string' ? track.role : '';
    const midiProgram = normalizeMidiProgram(track?.midi_program);
    const channel = normalizeChannel(track?.channel);
    const isDrum = Boolean(track?.is_drum);
    const trackVolume = normalizeTrackVolume(track?.volume);
    const pan = normalizePan(track?.pan);

    if (!Array.isArray(track?.events) || !track.events.length) {
      console.debug('[playbackEvents] Canonical track has no playback events', {
        trackId,
        instrument,
      });
      return;
    }

    track.events.forEach((event, eventIndex) => {
      const startTick = Number(event.start_tick);
      const durationTicks = Number(event.duration_ticks);
      const velocityMidi = Number(event.velocity);
      const pitch = normalizePitch(event.pitch);
      if (
        !pitch ||
        !Number.isFinite(startTick) ||
        startTick < 0 ||
        !Number.isFinite(durationTicks) ||
        durationTicks <= 0 ||
        !Number.isFinite(velocityMidi) ||
        velocityMidi < 1 ||
        velocityMidi > 127
      ) {
        console.warn('[playbackEvents] Invalid canonical note event skipped', {
          trackId,
          pitch: event.pitch,
          startTick: event.start_tick,
          durationTicks: event.duration_ticks,
          velocity: event.velocity,
        });
        return;
      }

      const position = startTick * secondsPerTick;
      const duration = durationTicks * secondsPerTick;
      built.push({
        trackId,
        trackName,
        instrument,
        role,
        midiProgram,
        channel,
        isDrum,
        trackVolume,
        pan,
        pitch,
        notes: [pitch],
        startTick,
        durationTicks,
        velocityMidi,
        velocity: velocityMidi / 127,
        position,
        duration,
        stopPosition: position + duration,
        originalIndex: eventIndex,
      });
    });
  });

  const events = built.sort((left, right) => {
    if (left.position !== right.position) {
      return left.position - right.position;
    }
    const trackCompare = String(left.trackId).localeCompare(String(right.trackId));
    if (trackCompare !== 0) {
      return trackCompare;
    }
    return left.originalIndex - right.originalIndex;
  }).map(({ originalIndex, ...event }) => event);

  console.debug('[playbackEvents] Canonical playback events built', {
    schemaVersion: musicJson.schema_version,
    tempo,
    ticksPerQuarter,
    trackCount: musicJson.tracks.length,
    eventCount: events.length,
  });
  if (!events.length) {
    console.warn('[playbackEvents] Canonical composition has no playable track events', {
      trackCount: musicJson.tracks.length,
    });
  }
  return events;
}

function normalizePitch(pitch) {
  if (!pitch || typeof pitch !== 'string') {
    return '';
  }
  return pitch.trim();
}

function normalizeMidiProgram(value) {
  const program = Number(value);
  if (!Number.isInteger(program) || program < 0 || program > 127) {
    return 0;
  }
  return program;
}

function normalizeChannel(value) {
  const channel = Number(value);
  if (!Number.isInteger(channel) || channel < 1 || channel > 16) {
    return 1;
  }
  return channel;
}

function normalizeTrackVolume(value) {
  const volume = Number(value);
  if (!Number.isFinite(volume)) {
    return 100;
  }
  return Math.max(0, Math.min(127, volume));
}

function normalizePan(value) {
  const pan = Number(value);
  if (!Number.isFinite(pan)) {
    return 0;
  }
  return Math.max(-64, Math.min(63, pan));
}
