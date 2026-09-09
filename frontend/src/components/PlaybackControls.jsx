import React, { useEffect, useMemo, useRef } from 'react';
import * as Tone from 'tone';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { compilePlaybackSchedule } from '../utils/playbackEvents.js';
import { buildLegacyPlaybackEvents } from '../utils/legacyPlaybackEvents.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { audibleRevisionKey } from '../utils/compositionCanonical.js';
import { findDevelopmentCandidateById } from '../utils/compositionCandidates.js';
import { secondsToPlaybackPosition } from '../utils/playbackPosition.js';
import { createPlaybackEngine } from '../utils/tonePlaybackEngine.js';
import TrackPlaybackControls from './TrackPlaybackControls.jsx';

const Controls = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 20px;

  @media (max-width: 480px) {
    flex-direction: column;
  }
`;

const Button = styled.button`
  flex: 1;
  min-width: 120px;
  background: ${(props) => {
    if (props.$variant === 'stop') return '#ef4444';
    if (props.$variant === 'secondary') return '#374151';
    return '#10b981';
  }};
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

const StatusRow = styled.div`
  margin-top: 12px;
  color: #374151;
  font-size: 0.9rem;
`;

const ErrorText = styled.div`
  margin-top: 10px;
  color: #991b1b;
  font-size: 0.9rem;
`;

const PlaybackControls = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const developmentAuditionActive = useMusicStore((state) => state.developmentAuditionActive);
  const developmentCandidates = useMusicStore((state) => state.developmentCandidates);
  const developmentSelectedCandidateId = useMusicStore((state) => state.developmentSelectedCandidateId);
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
  const playbackBar = useMusicStore((state) => state.playbackBar);
  const trackControls = useMusicStore((state) => state.trackControls);
  const setPlaybackStatus = useMusicStore((state) => state.setPlaybackStatus);
  const setPlaybackPosition = useMusicStore((state) => state.setPlaybackPosition);
  const setUiError = useMusicStore((state) => state.setUiError);
  const toggleTrackMute = useMusicStore((state) => state.toggleTrackMute);
  const toggleTrackSolo = useMusicStore((state) => state.toggleTrackSolo);
  const setTrackVolume = useMusicStore((state) => state.setTrackVolume);
  const syncTrackControlsFromComposition = useMusicStore((state) => state.syncTrackControlsFromComposition);

  const playbackComposition = useMemo(() => {
    if (developmentAuditionActive) {
      const candidate = findDevelopmentCandidateById(
        developmentCandidates,
        developmentSelectedCandidateId,
      );
      if (candidate?.composition) {
        return candidate.composition;
      }
    }
    return editedMusicJson;
  }, [
    developmentAuditionActive,
    developmentCandidates,
    developmentSelectedCandidateId,
    editedMusicJson,
  ]);

  const playbackRevision = useMemo(
    () => (playbackComposition ? audibleRevisionKey(playbackComposition) : 'empty'),
    [playbackComposition],
  );

  const engineRef = useRef(null);
  const legacySynthRef = useRef(null);
  const scheduledRevisionRef = useRef('');
  const positionTimerRef = useRef(null);

  useEffect(() => {
    syncTrackControlsFromComposition(playbackComposition);
  }, [playbackComposition, syncTrackControlsFromComposition]);

  useEffect(() => {
    if (!engineRef.current) {
      engineRef.current = createPlaybackEngine({ Tone, logger: console });
    }
    return () => {
      clearPositionTimer(positionTimerRef);
      if (engineRef.current) {
        engineRef.current.dispose();
        engineRef.current = null;
      }
      disposeLegacySynth(legacySynthRef);
      setPlaybackStatus('idle');
      setPlaybackPosition({ seconds: 0, bar: 1 });
    };
  }, [setPlaybackPosition, setPlaybackStatus]);

  useEffect(() => {
    if (!scheduledRevisionRef.current) {
      return undefined;
    }
    if (scheduledRevisionRef.current === playbackRevision) {
      return undefined;
    }
    if (playbackStatus === 'playing' || playbackStatus === 'paused' || playbackStatus === 'loading') {
      console.info('[PlaybackControls] Active playback stopped because playback source changed', {
        previousRevision: scheduledRevisionRef.current.slice(0, 48),
        currentRevision: String(playbackRevision).slice(0, 48),
        audition: developmentAuditionActive,
      });
      stopEverything({
        engineRef,
        legacySynthRef,
        positionTimerRef,
        scheduledRevisionRef,
        setPlaybackStatus,
        setPlaybackPosition,
      });
    }
    return undefined;
  }, [
    playbackRevision,
    playbackStatus,
    developmentAuditionActive,
    setPlaybackPosition,
    setPlaybackStatus,
  ]);

  useEffect(() => {
    if (!engineRef.current || !isCanonicalComposition(playbackComposition)) {
      return undefined;
    }
    if (playbackStatus !== 'playing' && playbackStatus !== 'paused') {
      return undefined;
    }
    console.debug('[PlaybackControls] Applying live track control overrides');
    engineRef.current.applyTrackOverrides(trackControls);
    return undefined;
  }, [trackControls, playbackComposition, playbackStatus]);

  const handlePlay = async () => {
    if (!playbackComposition) {
      setUiError('Generate or edit music JSON before playback.');
      console.warn('[PlaybackControls] Play ignored; no composition loaded');
      return;
    }

    const validation = validateMusicJson(playbackComposition);
    if (!validation.valid && isCanonicalComposition(playbackComposition)) {
      console.warn('[PlaybackControls] Rejected unsupported/invalid canonical playback JSON', {
        message: validation.message,
      });
      setUiError(validation.message || 'Canonical composition JSON is invalid for playback.');
      setPlaybackStatus('error');
      return;
    }

    try {
      setPlaybackStatus('loading');
      console.info('[PlaybackControls] User play action', {
        path: isCanonicalComposition(playbackComposition) ? 'canonical' : 'legacy',
      });
      await Tone.start();

      stopEverything({
        engineRef,
        legacySynthRef,
        positionTimerRef,
        scheduledRevisionRef,
        setPlaybackStatus,
        setPlaybackPosition,
        preserveStatus: true,
      });

      if (isCanonicalComposition(playbackComposition)) {
        await startCanonicalPlayback({
          playbackComposition,
          trackControls,
          engineRef,
          scheduledRevisionRef,
          positionTimerRef,
          setPlaybackStatus,
          setPlaybackPosition,
          setUiError,
        });
        return;
      }

      await startLegacyPlayback({
        playbackComposition,
        legacySynthRef,
        scheduledRevisionRef,
        positionTimerRef,
        setPlaybackStatus,
        setPlaybackPosition,
        setUiError,
      });
    } catch (error) {
      console.error('[PlaybackControls] Audio initialization/playback failed', { message: error.message });
      setUiError('Playback failed. Check browser audio permissions and generated JSON.');
      setPlaybackStatus('error');
    }
  };

  const handlePause = () => {
    if (playbackStatus !== 'playing') {
      console.warn('[PlaybackControls] Pause ignored because playback is not active', { playbackStatus });
      return;
    }
    console.info('[PlaybackControls] User pause action');
    if (isCanonicalComposition(playbackComposition) && engineRef.current) {
      engineRef.current.pause();
    } else {
      Tone.Transport.pause();
    }
    clearPositionTimer(positionTimerRef);
    setPlaybackStatus('paused');
  };

  const handleResume = async () => {
    if (playbackStatus !== 'paused') {
      console.warn('[PlaybackControls] Resume ignored because playback is not paused', { playbackStatus });
      return;
    }
    console.info('[PlaybackControls] User resume action');
    await Tone.start();
    if (isCanonicalComposition(playbackComposition) && engineRef.current) {
      engineRef.current.resume();
    } else {
      Tone.Transport.start();
    }
    startPositionTimer({
      playbackComposition,
      engineRef,
      positionTimerRef,
      setPlaybackPosition,
      isCanonical: isCanonicalComposition(playbackComposition),
    });
    setPlaybackStatus('playing');
  };

  const handleStop = () => {
    console.info('[PlaybackControls] User stop action');
    stopEverything({
      engineRef,
      legacySynthRef,
      positionTimerRef,
      scheduledRevisionRef,
      setPlaybackStatus,
      setPlaybackPosition,
    });
  };

  const handleSeekToStart = () => {
    console.info('[PlaybackControls] User seek-to-start action');
    if (isCanonicalComposition(playbackComposition) && engineRef.current) {
      engineRef.current.seek(0);
    } else {
      Tone.Transport.position = 0;
    }
    setPlaybackPosition({ seconds: 0, bar: 1 });
  };

  const canonical = isCanonicalComposition(playbackComposition);
  const tracks = canonical && Array.isArray(playbackComposition?.tracks) ? playbackComposition.tracks : [];

  return (
    <div>
      <h4>Playback</h4>
      <Controls>
        {playbackStatus === 'paused' ? (
          <Button type="button" onClick={handleResume}>Resume</Button>
        ) : (
          <Button
            type="button"
            data-testid="playback-play"
            onClick={handlePlay}
            disabled={!playbackComposition || playbackStatus === 'loading'}
          >
            {playbackStatus === 'loading' ? 'Preparing Audio...' : 'Play'}
          </Button>
        )}
        <Button
          type="button"
          $variant="secondary"
          onClick={handlePause}
          disabled={playbackStatus !== 'playing'}
        >
          Pause
        </Button>
        <Button
          type="button"
          $variant="stop"
          onClick={handleStop}
          disabled={playbackStatus !== 'playing' && playbackStatus !== 'paused' && playbackStatus !== 'loading'}
        >
          Stop
        </Button>
        <Button
          type="button"
          $variant="secondary"
          onClick={handleSeekToStart}
          disabled={!playbackComposition}
        >
          Seek Start
        </Button>
      </Controls>
      <StatusRow data-testid="playback-status">
        Status: {playbackStatus} · {playbackSeconds.toFixed(2)}s · bar {playbackBar}
        {canonical ? ' · composition.v2' : playbackComposition ? ' · legacy' : ''}
        {developmentAuditionActive ? ' · auditioning candidate' : ''}
      </StatusRow>
      {canonical ? (
        <TrackPlaybackControls
          tracks={tracks}
          trackControls={trackControls}
          disabled={!playbackComposition}
          onMuteToggle={(trackId) => {
            console.info('[PlaybackControls] User mute toggle', { trackId });
            toggleTrackMute(trackId);
          }}
          onSoloToggle={(trackId) => {
            console.info('[PlaybackControls] User solo toggle', { trackId });
            toggleTrackSolo(trackId);
          }}
          onVolumeChange={(trackId, volumeMidi) => {
            console.info('[PlaybackControls] User volume change', { trackId, volumeMidi });
            setTrackVolume(trackId, volumeMidi);
          }}
        />
      ) : (
        <StatusRow>Track mute/solo/volume controls require a canonical composition.</StatusRow>
      )}
      {playbackStatus === 'error' && <ErrorText>Playback is unavailable for the current JSON.</ErrorText>}
    </div>
  );
};

async function startCanonicalPlayback({
  playbackComposition,
  trackControls,
  engineRef,
  scheduledRevisionRef,
  positionTimerRef,
  setPlaybackStatus,
  setPlaybackPosition,
  setUiError,
}) {
  console.info('[PlaybackControls] Using canonical composition playback path');
  const schedule = compilePlaybackSchedule(playbackComposition);
  console.debug('[PlaybackControls] Canonical schedule summary', schedule?.summary ?? {});

  if (!schedule?.logicalNotes?.length) {
    console.warn('[PlaybackControls] Canonical composition has no playable track events');
    setUiError('No playable note events found in the current composition.');
    setPlaybackStatus('error');
    return;
  }

  // Never inspect harmony for audible content on the canonical path.
  engineRef.current.prepare({
    tracks: playbackComposition.tracks,
    schedule,
    tempo: playbackComposition.tempo,
    trackOverrides: trackControls,
    onComplete: () => {
      stopEverything({
        engineRef,
        legacySynthRef: { current: null },
        positionTimerRef,
        scheduledRevisionRef,
        setPlaybackStatus,
        setPlaybackPosition,
      });
    },
  });

  scheduledRevisionRef.current = audibleRevisionKey(playbackComposition);
  await engineRef.current.start();
  startPositionTimer({
    playbackComposition,
    engineRef,
    positionTimerRef,
    setPlaybackPosition,
    isCanonical: true,
  });
  setUiError('');
  setPlaybackStatus('playing');
}

async function startLegacyPlayback({
  playbackComposition,
  legacySynthRef,
  scheduledRevisionRef,
  positionTimerRef,
  setPlaybackStatus,
  setPlaybackPosition,
  setUiError,
}) {
  console.info('[PlaybackControls] Using legacy playback path');
  const synth = new Tone.PolySynth(Tone.Synth).toDestination();
  legacySynthRef.current = synth;
  Tone.Transport.bpm.value = playbackComposition.tempo || 100;
  Tone.Transport.cancel();
  Tone.Transport.position = 0;

  const events = buildLegacyPlaybackEvents(playbackComposition, {
    frequencyToNote: (midi, unit) => Tone.Frequency(midi, unit).toNote(),
  });
  console.debug('[PlaybackControls] Legacy schedule summary', {
    eventCount: events.length,
    tempo: playbackComposition.tempo,
  });

  if (!events.length) {
    console.warn('[PlaybackControls] Rejected harmony-only or unsupported legacy playback JSON');
    setUiError('No playable note events found in the current JSON.');
    setPlaybackStatus('error');
    disposeLegacySynth(legacySynthRef);
    return;
  }

  events.forEach((event) => {
    Tone.Transport.schedule((time) => {
      synth.triggerAttackRelease(event.notes, event.duration, time, event.velocity);
    }, event.position);
  });

  const endPosition = playbackEndPosition(events);
  Tone.Transport.scheduleOnce(() => {
    stopEverything({
      engineRef: { current: null },
      legacySynthRef,
      positionTimerRef,
      scheduledRevisionRef,
      setPlaybackStatus,
      setPlaybackPosition,
    });
  }, endPosition);

  scheduledRevisionRef.current = audibleRevisionKey(playbackComposition);
  Tone.Transport.start();
  startPositionTimer({
    playbackComposition,
    engineRef: { current: null },
    positionTimerRef,
    setPlaybackPosition,
    isCanonical: false,
  });
  setUiError('');
  setPlaybackStatus('playing');
}

function stopEverything({
  engineRef,
  legacySynthRef,
  positionTimerRef,
  scheduledRevisionRef,
  setPlaybackStatus,
  setPlaybackPosition,
  preserveStatus = false,
}) {
  clearPositionTimer(positionTimerRef);
  if (engineRef?.current) {
    engineRef.current.stop({ seekToStart: true });
  } else {
    Tone.Transport.stop();
    Tone.Transport.cancel();
    Tone.Transport.position = 0;
  }
  disposeLegacySynth(legacySynthRef);
  scheduledRevisionRef.current = '';
  setPlaybackPosition({ seconds: 0, bar: 1 });
  if (!preserveStatus) {
    setPlaybackStatus('idle');
  }
  console.debug('[PlaybackControls] Transport stopped', { transportState: Tone.Transport.state });
}

function disposeLegacySynth(legacySynthRef) {
  if (legacySynthRef?.current) {
    try {
      legacySynthRef.current.dispose();
    } catch (error) {
      console.error('[PlaybackControls] Legacy synth dispose failed', { message: error.message });
    }
    legacySynthRef.current = null;
  }
}

function clearPositionTimer(positionTimerRef) {
  if (positionTimerRef?.current) {
    clearInterval(positionTimerRef.current);
    positionTimerRef.current = null;
  }
}

function startPositionTimer({
  playbackComposition,
  engineRef,
  positionTimerRef,
  setPlaybackPosition,
  isCanonical,
}) {
  clearPositionTimer(positionTimerRef);
  let lastLoggedSecond = -1;
  positionTimerRef.current = setInterval(() => {
    const seconds = isCanonical && engineRef.current
      ? engineRef.current.getPositionSeconds()
      : Number(Tone.Transport.seconds) || 0;
    const position = secondsToPlaybackPosition(seconds, {
      tempo: playbackComposition?.tempo,
      ticksPerQuarter: playbackComposition?.ticks_per_quarter,
      timeSignature: playbackComposition?.time_signature,
      composition: playbackComposition,
    });
    setPlaybackPosition({ seconds: position.seconds, bar: position.bar });
    const wholeSecond = Math.floor(position.seconds);
    if (wholeSecond !== lastLoggedSecond) {
      lastLoggedSecond = wholeSecond;
      console.debug('[PlaybackControls] Playback position update', {
        seconds: Number(position.seconds.toFixed(3)),
        bar: position.bar,
      });
    }
  }, 100);
}

function playbackEndPosition(events) {
  if (typeof events[0].stopPosition === 'number') {
    return Math.max(...events.map((event) => event.stopPosition));
  }
  return events[events.length - 1].stopPosition;
}

export default PlaybackControls;
