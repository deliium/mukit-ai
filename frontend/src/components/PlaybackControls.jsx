import React, { useEffect, useRef } from 'react';
import * as Tone from 'tone';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const Controls = styled.div`
  display: flex;
  gap: 12px;
  margin-top: 20px;

  @media (max-width: 480px) {
    flex-direction: column;
  }
`;

const Button = styled.button`
  flex: 1;
  background: ${(props) => (props.$variant === 'stop' ? '#ef4444' : '#10b981')};
  color: white;
  border: none;
  padding: 12px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 600;

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }
`;

const ErrorText = styled.div`
  margin-top: 10px;
  color: #991b1b;
  font-size: 0.9rem;
`;

const PlaybackControls = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const setPlaybackStatus = useMusicStore((state) => state.setPlaybackStatus);
  const setUiError = useMusicStore((state) => state.setUiError);
  const synthRef = useRef(null);

  useEffect(() => () => stopPlayback(synthRef, setPlaybackStatus), [setPlaybackStatus]);

  const handlePlay = async () => {
    if (!editedMusicJson) {
      setUiError('Generate or edit music JSON before playback.');
      return;
    }

    try {
      setPlaybackStatus('loading');
      console.debug('[PlaybackControls] Audio initialization started');
      await Tone.start();
      stopPlayback(synthRef, setPlaybackStatus);

      const synth = new Tone.PolySynth(Tone.Synth).toDestination();
      synthRef.current = synth;
      Tone.Transport.bpm.value = editedMusicJson.tempo || 100;
      Tone.Transport.cancel();
      Tone.Transport.position = 0;

      const events = buildPlaybackEvents(editedMusicJson);
      console.debug('[PlaybackControls] Playback schedule summary', {
        eventCount: events.length,
        tempo: editedMusicJson.tempo,
        transportState: Tone.Transport.state,
      });

      if (!events.length) {
        console.warn('[PlaybackControls] No playable note events found');
        setUiError('No playable note events found in the current JSON.');
        setPlaybackStatus('error');
        return;
      }

      events.forEach((event) => {
        Tone.Transport.schedule((time) => {
          synth.triggerAttackRelease(event.notes, event.duration, time);
        }, event.position);
      });

      Tone.Transport.scheduleOnce(() => {
        stopPlayback(synthRef, setPlaybackStatus);
      }, playbackEndPosition(events));

      Tone.Transport.start();
      setPlaybackStatus('playing');
      setUiError('');
    } catch (error) {
      console.error('[PlaybackControls] Audio initialization/playback failed', { message: error.message });
      setUiError('Playback failed. Check browser audio permissions and generated JSON.');
      setPlaybackStatus('error');
    }
  };

  return (
    <div>
      <h4>Playback</h4>
      <Controls>
        <Button type="button" onClick={handlePlay} disabled={!editedMusicJson || playbackStatus === 'loading'}>
          {playbackStatus === 'loading' ? 'Preparing Audio...' : 'Play'}
        </Button>
        <Button type="button" $variant="stop" onClick={() => stopPlayback(synthRef, setPlaybackStatus)} disabled={playbackStatus !== 'playing'}>
          Stop
        </Button>
      </Controls>
      {playbackStatus === 'error' && <ErrorText>Playback is unavailable for the current JSON.</ErrorText>}
    </div>
  );
};

function stopPlayback(synthRef, setPlaybackStatus) {
  Tone.Transport.stop();
  Tone.Transport.cancel();
  if (synthRef.current) {
    synthRef.current.dispose();
    synthRef.current = null;
  }
  console.debug('[PlaybackControls] Transport stopped', { transportState: Tone.Transport.state });
  setPlaybackStatus('idle');
}

function buildPlaybackEvents(musicJson) {
  if (Array.isArray(musicJson.notes) && musicJson.notes.length) {
    const beatsPerMeasure = measureQuarterLength(musicJson.time_signature);
    const quarterSeconds = 60 / Number(musicJson.tempo || 100);
    const events = musicJson.notes
      .map((item) => {
        const bar = Number(item.bar);
        const beat = Number(item.beat || 1);
        const duration = Number(item.duration);
        const pitch = normalizePitch(item.pitch);
        if (!Number.isFinite(bar) || bar < 1 || !Number.isFinite(beat) || beat < 1 || !Number.isFinite(duration) || duration <= 0 || !pitch) {
          console.warn('[PlaybackControls] Invalid note event skipped', { note: item });
          return null;
        }
        return {
          bar,
          beat,
          notes: [pitch],
          duration: duration * quarterSeconds,
          position: positionForNote(bar, beat, beatsPerMeasure, quarterSeconds),
          stopPosition: positionForNote(bar, beat + duration, beatsPerMeasure, quarterSeconds),
        };
      })
      .filter(Boolean)
      .sort((left, right) => left.bar - right.bar || left.beat - right.beat);

    if (events.length) {
      return events;
    }
  }

  if (!Array.isArray(musicJson.harmony)) {
    console.warn('[PlaybackControls] Harmony data skipped because it is not an array');
    return [];
  }

  return musicJson.harmony
    .map((item) => {
      const notes = chordToNotes(item.chord);
      if (!notes.length) {
        console.warn('[PlaybackControls] Unsupported chord data skipped', { chord: item.chord });
        return null;
      }
      const bar = Number(item.bar);
      return {
        bar,
        beat: 1,
        notes,
        duration: '1m',
        position: `${bar - 1}:0:0`,
        stopPosition: `${bar}:0:0`,
      };
    })
    .filter((event) => event && Number.isFinite(event.bar) && event.bar > 0);
}

function normalizePitch(pitch) {
  if (!pitch || typeof pitch !== 'string') {
    return '';
  }
  return pitch.trim();
}

function measureQuarterLength(timeSignature) {
  if (!timeSignature || typeof timeSignature !== 'string') {
    return 4;
  }
  const [numerator, denominator] = timeSignature.split('/').map(Number);
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator === 0) {
    return 4;
  }
  return numerator * (4 / denominator);
}

function positionForNote(bar, beat, beatsPerMeasure, quarterSeconds) {
  const absoluteQuarterOffset = (bar - 1) * beatsPerMeasure + (beat - 1);
  return absoluteQuarterOffset * quarterSeconds;
}

function playbackEndPosition(events) {
  if (typeof events[0].stopPosition === 'number') {
    return Math.max(...events.map((event) => event.stopPosition));
  }
  return events[events.length - 1].stopPosition;
}

function chordToNotes(chordName) {
  if (!chordName || typeof chordName !== 'string') {
    return [];
  }

  const match = chordName.trim().match(/^([A-G])([#b]?)(.*)$/);
  if (!match) {
    return [];
  }

  const [, rootName, accidental, suffix] = match;
  const root = `${rootName}${accidental}`;
  const rootMidi = NOTE_TO_MIDI[root];
  if (rootMidi === undefined) {
    return [];
  }

  const isMinor = suffix.toLowerCase().startsWith('m') && !suffix.toLowerCase().startsWith('maj');
  const intervals = isMinor ? [0, 3, 7] : [0, 4, 7];
  return intervals.map((interval) => Tone.Frequency(rootMidi + interval, 'midi').toNote());
}

const NOTE_TO_MIDI = {
  C: 60,
  'C#': 61,
  Db: 61,
  D: 62,
  'D#': 63,
  Eb: 63,
  E: 64,
  F: 65,
  'F#': 66,
  Gb: 66,
  G: 67,
  'G#': 68,
  Ab: 68,
  A: 69,
  'A#': 70,
  Bb: 70,
  B: 71,
};

export default PlaybackControls;
