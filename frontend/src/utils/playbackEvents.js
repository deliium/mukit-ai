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
  const events = musicJson.tracks.flatMap((track) => {
    if (!Array.isArray(track.events) || !track.events.length) {
      console.debug('[playbackEvents] Canonical track has no playback events', {
        trackId: track.id,
        instrument: track.instrument,
      });
      return [];
    }

    return track.events.map((event) => {
      const startTick = Number(event.start_tick);
      const durationTicks = Number(event.duration_ticks);
      const velocity = Number(event.velocity);
      const pitch = normalizePitch(event.pitch);
      if (
        !pitch ||
        !Number.isFinite(startTick) ||
        startTick < 0 ||
        !Number.isFinite(durationTicks) ||
        durationTicks <= 0 ||
        !Number.isFinite(velocity) ||
        velocity < 1 ||
        velocity > 127
      ) {
        console.warn('[playbackEvents] Invalid canonical note event skipped', {
          trackId: track.id,
          pitch: event.pitch,
          startTick: event.start_tick,
          durationTicks: event.duration_ticks,
          velocity: event.velocity,
        });
        return null;
      }

      return {
        trackId: track.id,
        notes: [pitch],
        duration: durationTicks * secondsPerTick,
        velocity: velocity / 127,
        position: startTick * secondsPerTick,
        stopPosition: (startTick + durationTicks) * secondsPerTick,
      };
    });
  }).filter(Boolean).sort((left, right) => left.position - right.position || left.trackId.localeCompare(right.trackId));

  console.debug('[playbackEvents] Canonical playback events built', {
    schemaVersion: musicJson.schema_version,
    trackCount: musicJson.tracks.length,
    eventCount: events.length,
    tempo,
    ticksPerQuarter,
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
