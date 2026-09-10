import React, { useEffect, useMemo, useRef } from 'react';
import * as Tone from 'tone';
import styled from 'styled-components';
import {
  ARRANGEMENT_AUDITION_CANDIDATE,
  resolvePlaybackSource,
  useMusicStore,
} from '../store/musicStore.js';
import { compilePlaybackSchedule } from '../utils/playbackEvents.js';
import { buildLegacyPlaybackEvents } from '../utils/legacyPlaybackEvents.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { audibleRevisionKey } from '../utils/compositionCanonical.js';
import { findDevelopmentCandidateById } from '../utils/compositionCandidates.js';
import { findArrangementCandidateById } from '../utils/compositionArrangementCandidates.js';
import { secondsToPlaybackPosition, ticksToPlaybackSeconds } from '../utils/playbackPosition.js';
import { createPlaybackEngine } from '../utils/tonePlaybackEngine.js';
import { createAppLogger } from '../utils/appLogger.js';
import TrackPlaybackControls from './TrackPlaybackControls.jsx';

const logger = createAppLogger('PlaybackControls');

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
  const arrangementAuditionMode = useMusicStore((state) => state.arrangementAuditionMode);
  const arrangementCandidates = useMusicStore((state) => state.arrangementCandidates);
  const arrangementSelectedCandidateId = useMusicStore((state) => state.arrangementSelectedCandidateId);
  const arrangementCandidateTrackControls = useMusicStore((state) => state.arrangementCandidateTrackControls);
  const versionAuditionActive = useMusicStore((state) => state.versionAuditionActive);
  const versionSelectedRevisionId = useMusicStore((state) => state.versionSelectedRevisionId);
  const versionRevisionDetails = useMusicStore((state) => state.versionRevisionDetails);
  const versionAuditionTrackControls = useMusicStore((state) => state.versionAuditionTrackControls);
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
  const playbackBar = useMusicStore((state) => state.playbackBar);
  const playbackLoop = useMusicStore((state) => state.playbackLoop);
  const playbackTransportIntent = useMusicStore((state) => state.playbackTransportIntent);
  const editCursorTick = useMusicStore((state) => state.editCursorTick);
  const trackControls = useMusicStore((state) => state.trackControls);
  const setPlaybackStatus = useMusicStore((state) => state.setPlaybackStatus);
  const setPlaybackPosition = useMusicStore((state) => state.setPlaybackPosition);
  const setUiError = useMusicStore((state) => state.setUiError);
  const playFromCursor = useMusicStore((state) => state.playFromCursor);
  const setLoopFromSelection = useMusicStore((state) => state.setLoopFromSelection);
  const clearPlaybackLoop = useMusicStore((state) => state.clearPlaybackLoop);
  const setPlaybackLoopEnabled = useMusicStore((state) => state.setPlaybackLoopEnabled);
  const toggleTrackMute = useMusicStore((state) => state.toggleTrackMute);
  const toggleTrackSolo = useMusicStore((state) => state.toggleTrackSolo);
  const setTrackVolume = useMusicStore((state) => state.setTrackVolume);
  const syncTrackControlsFromComposition = useMusicStore((state) => state.syncTrackControlsFromComposition);
  const syncArrangementCandidateTrackControls = useMusicStore(
    (state) => state.syncArrangementCandidateTrackControls,
  );
  const syncVersionAuditionTrackControls = useMusicStore(
    (state) => state.syncVersionAuditionTrackControls,
  );
  const toggleArrangementCandidateMute = useMusicStore((state) => state.toggleArrangementCandidateMute);
  const toggleArrangementCandidateSolo = useMusicStore((state) => state.toggleArrangementCandidateSolo);
  const setArrangementCandidateVolume = useMusicStore((state) => state.setArrangementCandidateVolume);
  const toggleVersionAuditionMute = useMusicStore((state) => state.toggleVersionAuditionMute);
  const toggleVersionAuditionSolo = useMusicStore((state) => state.toggleVersionAuditionSolo);
  const setVersionAuditionVolume = useMusicStore((state) => state.setVersionAuditionVolume);

  const arrangementCandidateAudition = arrangementAuditionMode === ARRANGEMENT_AUDITION_CANDIDATE;

  const playbackResolved = useMemo(() => resolvePlaybackSource(
    {
      editedMusicJson,
      arrangementAuditionMode,
      arrangementCandidates,
      arrangementSelectedCandidateId,
      versionAuditionActive,
      versionSelectedRevisionId,
      versionRevisionDetails,
      developmentAuditionActive,
      developmentCandidates,
      developmentSelectedCandidateId,
    },
    {
      findArrangementCandidateById,
      findDevelopmentCandidateById,
      arrangementCandidateMode: ARRANGEMENT_AUDITION_CANDIDATE,
    },
  ), [
    arrangementAuditionMode,
    arrangementCandidates,
    arrangementSelectedCandidateId,
    developmentAuditionActive,
    developmentCandidates,
    developmentSelectedCandidateId,
    editedMusicJson,
    versionAuditionActive,
    versionRevisionDetails,
    versionSelectedRevisionId,
  ]);
  const playbackComposition = playbackResolved.composition;
  const playbackSource = playbackResolved.source;
  const versionAudition = playbackSource === 'version';

  const activeTrackControls = arrangementCandidateAudition
    ? arrangementCandidateTrackControls
    : versionAudition
      ? versionAuditionTrackControls
      : trackControls;

  const playbackRevision = useMemo(
    () => (playbackComposition ? audibleRevisionKey(playbackComposition) : 'empty'),
    [playbackComposition],
  );

  const engineRef = useRef(null);
  const legacySynthRef = useRef(null);
  const scheduledRevisionRef = useRef('');
  const positionTimerRef = useRef(null);
  const lastTransportSeqRef = useRef(0);
  const playbackStatusRef = useRef(playbackStatus);
  playbackStatusRef.current = playbackStatus;

  useEffect(() => {
    if (!playbackComposition) {
      return;
    }
    // Candidate / version audition must never prune source mixer controls.
    if (arrangementCandidateAudition) {
      syncArrangementCandidateTrackControls(playbackComposition);
      return;
    }
    if (versionAudition) {
      syncVersionAuditionTrackControls(playbackComposition);
      return;
    }
    syncTrackControlsFromComposition(playbackComposition);
  }, [
    arrangementCandidateAudition,
    playbackComposition,
    syncArrangementCandidateTrackControls,
    syncTrackControlsFromComposition,
    syncVersionAuditionTrackControls,
    versionAudition,
  ]);

  useEffect(() => {
    if (!engineRef.current) {
      engineRef.current = createPlaybackEngine({ Tone, logger });
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
      logger.info('Active playback stopped because playback source changed', {
        previousRevision: scheduledRevisionRef.current.slice(0, 48),
        currentRevision: String(playbackRevision).slice(0, 48),
        developmentAudition: developmentAuditionActive,
        arrangementAudition: arrangementCandidateAudition,
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
    arrangementCandidateAudition,
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
    logger.debug('Applying live track control overrides');
    engineRef.current.applyTrackOverrides(activeTrackControls);
    return undefined;
  }, [activeTrackControls, playbackComposition, playbackStatus]);

  // Sync loop enable/bounds into the running engine without restarting transport.
  useEffect(() => {
    if (!engineRef.current || !isCanonicalComposition(playbackComposition)) {
      return undefined;
    }
    if (playbackStatus !== 'playing' && playbackStatus !== 'paused') {
      return undefined;
    }
    engineRef.current.setLoop(playbackLoop);
    return undefined;
  }, [playbackLoop, playbackComposition, playbackStatus]);

  const handlePlay = async ({ startTick = null } = {}) => {
    if (!playbackComposition) {
      setUiError('Generate or edit music JSON before playback.');
      logger.warn('Play ignored; no composition loaded');
      return;
    }

    const validation = validateMusicJson(playbackComposition);
    if (!validation.valid && isCanonicalComposition(playbackComposition)) {
      logger.warn('Rejected unsupported/invalid canonical playback JSON', {
        message: validation.message,
      });
      setUiError(validation.message || 'Canonical composition JSON is invalid for playback.');
      setPlaybackStatus('error');
      return;
    }

    try {
      setPlaybackStatus('loading');
      logger.info('User play action', {
        path: isCanonicalComposition(playbackComposition) ? 'canonical' : 'legacy',
        startTick: startTick == null ? null : Number(startTick),
        loopEnabled: Boolean(playbackLoop?.enabled),
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
          trackControls: activeTrackControls,
          engineRef,
          scheduledRevisionRef,
          positionTimerRef,
          setPlaybackStatus,
          setPlaybackPosition,
          setUiError,
          startTick,
          loop: playbackLoop,
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
        startTick,
      });
    } catch (error) {
      logger.error('Audio initialization/playback failed', { message: error.message });
      setUiError('Playback failed. Check browser audio permissions and generated JSON.');
      setPlaybackStatus('error');
    }
  };

  const handlePause = () => {
    if (playbackStatusRef.current !== 'playing') {
      logger.warn('Pause ignored because playback is not active', {
        playbackStatus: playbackStatusRef.current,
      });
      return;
    }
    logger.info('User pause action');
    if (isCanonicalComposition(playbackComposition) && engineRef.current) {
      engineRef.current.pause();
    } else {
      Tone.Transport.pause();
    }
    clearPositionTimer(positionTimerRef);
    setPlaybackStatus('paused');
  };

  const handleResume = async () => {
    if (playbackStatusRef.current !== 'paused') {
      logger.warn('Resume ignored because playback is not paused', {
        playbackStatus: playbackStatusRef.current,
      });
      return;
    }
    logger.info('User resume action');
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
    logger.info('User stop action');
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
    logger.info('User seek-to-start action');
    if (isCanonicalComposition(playbackComposition) && engineRef.current) {
      engineRef.current.seek(0);
    } else {
      Tone.Transport.position = 0;
    }
    setPlaybackPosition({ seconds: 0, bar: 1 });
  };

  // Consume store transport intents (Space, Play From Cursor, etc.).
  useEffect(() => {
    if (!playbackTransportIntent || !playbackTransportIntent.seq) {
      return undefined;
    }
    if (playbackTransportIntent.seq === lastTransportSeqRef.current) {
      return undefined;
    }
    lastTransportSeqRef.current = playbackTransportIntent.seq;
    const { type, startTick } = playbackTransportIntent;
    logger.debug('Consuming transport intent', {
      type,
      startTick: startTick ?? null,
      seq: playbackTransportIntent.seq,
    });

    if (type === 'pause') {
      handlePause();
      return undefined;
    }
    if (type === 'resume') {
      void handleResume();
      return undefined;
    }
    if (type === 'stop') {
      handleStop();
      return undefined;
    }
    if (type === 'toggle') {
      const status = playbackStatusRef.current;
      if (status === 'playing') {
        handlePause();
      } else if (status === 'paused') {
        void handleResume();
      } else {
        void handlePlay({ startTick: null });
      }
      return undefined;
    }
    // type === 'play' (ordinary or from cursor)
    void handlePlay({ startTick });
    return undefined;
    // Intentional: only re-run when a new intent arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playbackTransportIntent]);

  const canonical = isCanonicalComposition(playbackComposition);
  const tracks = canonical && Array.isArray(playbackComposition?.tracks) ? playbackComposition.tracks : [];
  const loopLabel = playbackLoop
    ? `loop ${playbackLoop.startTick}–${playbackLoop.endTick}${playbackLoop.enabled ? '' : ' (off)'}`
    : 'loop off';

  return (
    <div>
      <h4>Playback</h4>
      <Controls>
        {playbackStatus === 'paused' ? (
          <Button type="button" onClick={handleResume} data-testid="playback-resume">Resume</Button>
        ) : (
          <Button
            type="button"
            data-testid="playback-play"
            onClick={() => handlePlay({ startTick: null })}
            disabled={!playbackComposition || playbackStatus === 'loading'}
          >
            {playbackStatus === 'loading' ? 'Preparing Audio...' : 'Play'}
          </Button>
        )}
        <Button
          type="button"
          $variant="secondary"
          data-testid="playback-from-cursor"
          onClick={() => playFromCursor()}
          disabled={!playbackComposition || playbackStatus === 'loading'}
        >
          Play From Cursor
        </Button>
        <Button
          type="button"
          $variant="secondary"
          onClick={handlePause}
          disabled={playbackStatus !== 'playing'}
          data-testid="playback-pause"
        >
          Pause
        </Button>
        <Button
          type="button"
          $variant="stop"
          onClick={handleStop}
          disabled={playbackStatus !== 'playing' && playbackStatus !== 'paused' && playbackStatus !== 'loading'}
          data-testid="playback-stop"
        >
          Stop
        </Button>
        <Button
          type="button"
          $variant="secondary"
          onClick={handleSeekToStart}
          disabled={!playbackComposition}
          data-testid="playback-seek-start"
        >
          Seek Start
        </Button>
        <Button
          type="button"
          $variant="secondary"
          data-testid="playback-set-loop"
          onClick={() => setLoopFromSelection()}
          disabled={!playbackComposition}
        >
          Set Loop Selection
        </Button>
        <Button
          type="button"
          $variant="secondary"
          data-testid="playback-toggle-loop"
          onClick={() => setPlaybackLoopEnabled(!playbackLoop?.enabled)}
          disabled={!playbackLoop}
        >
          {playbackLoop?.enabled ? 'Disable Loop' : 'Enable Loop'}
        </Button>
        <Button
          type="button"
          $variant="secondary"
          data-testid="playback-clear-loop"
          onClick={() => clearPlaybackLoop()}
          disabled={!playbackLoop}
        >
          Clear Loop
        </Button>
      </Controls>
      <StatusRow data-testid="playback-status">
        Status: {playbackStatus} · {playbackSeconds.toFixed(2)}s · bar {playbackBar}
        {canonical ? ' · composition.v2' : playbackComposition ? ' · legacy' : ''}
        {arrangementCandidateAudition
          ? ' · auditioning arrangement candidate'
          : versionAudition
            ? ' · auditioning version'
            : developmentAuditionActive
              ? ' · auditioning candidate'
              : ''}
        {' · '}
        <span data-testid="playback-loop-status">{loopLabel}</span>
        {' · '}
        cursor tick {editCursorTick}
      </StatusRow>
      {canonical ? (
        <TrackPlaybackControls
          tracks={tracks}
          trackControls={activeTrackControls}
          disabled={!playbackComposition}
          ariaLabel={
            arrangementCandidateAudition
              ? 'Arrangement candidate mixer'
              : versionAudition
                ? 'Version audition mixer'
                : 'Track mixer'
          }
          hint={
            arrangementCandidateAudition
              ? 'Candidate mixer (ephemeral; does not change source track controls)'
              : versionAudition
                ? 'Version mixer (ephemeral; does not change working track controls)'
                : 'Tracks / mixer (canonical composition.v2; mute/solo are UI-only)'
          }
          onMuteToggle={(trackId) => {
            logger.info('User mute toggle', {
              trackId,
              arrangementAudition: arrangementCandidateAudition,
              versionAudition,
            });
            if (arrangementCandidateAudition) {
              toggleArrangementCandidateMute(trackId);
              return;
            }
            if (versionAudition) {
              toggleVersionAuditionMute(trackId);
              return;
            }
            toggleTrackMute(trackId);
          }}
          onSoloToggle={(trackId) => {
            logger.info('User solo toggle', {
              trackId,
              arrangementAudition: arrangementCandidateAudition,
              versionAudition,
            });
            if (arrangementCandidateAudition) {
              toggleArrangementCandidateSolo(trackId);
              return;
            }
            if (versionAudition) {
              toggleVersionAuditionSolo(trackId);
              return;
            }
            toggleTrackSolo(trackId);
          }}
          onVolumeChange={(trackId, volumeMidi) => {
            logger.info('User volume change', {
              trackId,
              volumeMidi,
              arrangementAudition: arrangementCandidateAudition,
              versionAudition,
            });
            if (arrangementCandidateAudition) {
              setArrangementCandidateVolume(trackId, volumeMidi);
              return;
            }
            if (versionAudition) {
              setVersionAuditionVolume(trackId, volumeMidi);
              return;
            }
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
  startTick = null,
  loop = null,
}) {
  logger.info('Using canonical composition playback path', {
    startTick: startTick == null ? null : Number(startTick),
    loopEnabled: Boolean(loop?.enabled),
    revisionPrefix: audibleRevisionKey(playbackComposition).slice(0, 48),
  });
  const schedule = compilePlaybackSchedule(playbackComposition);
  logger.debug('Canonical schedule summary', schedule?.summary ?? {});

  if (!schedule?.logicalNotes?.length) {
    logger.warn('Canonical composition has no playable track events');
    setUiError('No playable note events found in the current composition.');
    setPlaybackStatus('error');
    return;
  }

  // Never inspect harmony for audible content on the canonical path.
  engineRef.current.prepare({
    tracks: playbackComposition.tracks,
    schedule,
    composition: playbackComposition,
    tempo: playbackComposition.tempo,
    trackOverrides: trackControls,
    startTick: startTick == null ? null : Number(startTick),
    loop,
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
  startTick = null,
}) {
  logger.info('Using legacy playback path');
  const synth = new Tone.PolySynth(Tone.Synth).toDestination();
  legacySynthRef.current = synth;
  Tone.Transport.bpm.value = playbackComposition.tempo || 100;
  Tone.Transport.cancel();

  const startSeconds = startTick == null
    ? 0
    : ticksToPlaybackSeconds(startTick, {
      tempo: playbackComposition.tempo,
      ticksPerQuarter: playbackComposition.ticks_per_quarter || 480,
    });
  Tone.Transport.position = startSeconds;

  const events = buildLegacyPlaybackEvents(playbackComposition, {
    frequencyToNote: (midi, unit) => Tone.Frequency(midi, unit).toNote(),
  });
  logger.debug('Legacy schedule summary', {
    eventCount: events.length,
    tempo: playbackComposition.tempo,
    startSeconds,
  });

  if (!events.length) {
    logger.warn('Rejected harmony-only or unsupported legacy playback JSON');
    setUiError('No playable note events found in the current JSON.');
    setPlaybackStatus('error');
    disposeLegacySynth(legacySynthRef);
    return;
  }

  events.forEach((event) => {
    if (event.position < startSeconds) {
      return;
    }
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
  logger.debug('Transport stopped', { transportState: Tone.Transport.state });
}

function disposeLegacySynth(legacySynthRef) {
  if (legacySynthRef?.current) {
    try {
      legacySynthRef.current.dispose();
    } catch (error) {
      logger.error('Legacy synth dispose failed', { message: error.message });
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
      logger.debug('Playback position update', {
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
