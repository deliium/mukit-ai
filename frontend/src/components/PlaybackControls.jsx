import React, { useEffect, useMemo, useRef, useState } from 'react';
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
import {
  PLAYBACK_MIXER_SCOPE_ARRANGEMENT,
  PLAYBACK_MIXER_SCOPE_DEVELOPMENT,
  PLAYBACK_MIXER_SCOPE_PREVIEW,
  PLAYBACK_MIXER_SCOPE_VERSION,
  PLAYBACK_MIXER_SCOPE_WORKING,
} from '../utils/playbackSource.js';
import { sanitizeMixerLogMeta } from '../utils/playbackMixerControls.js';
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
  const developmentCandidateTrackControls = useMusicStore((state) => state.developmentCandidateTrackControls);
  const previewTrackControls = useMusicStore((state) => state.previewTrackControls);
  const versionAuditionActive = useMusicStore((state) => state.versionAuditionActive);
  const versionSelectedRevisionId = useMusicStore((state) => state.versionSelectedRevisionId);
  const versionRevisionDetails = useMusicStore((state) => state.versionRevisionDetails);
  const versionAuditionTrackControls = useMusicStore((state) => state.versionAuditionTrackControls);
  const generationAuditionActive = useMusicStore((state) => state.generationAuditionActive);
  const generationCandidate = useMusicStore((state) => state.generationCandidate);
  const aiEditAuditionActive = useMusicStore((state) => state.aiEditAuditionActive);
  const aiEditCandidate = useMusicStore((state) => state.aiEditCandidate);
  const motifAuditionActive = useMusicStore((state) => state.motifAuditionActive);
  const motifCandidate = useMusicStore((state) => state.motifCandidate);
  const reharmonizeAuditionActive = useMusicStore((state) => state.reharmonizeAuditionActive);
  const reharmonizeCandidate = useMusicStore((state) => state.reharmonizeCandidate);
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
  const playbackBar = useMusicStore((state) => state.playbackBar);
  const playbackLoop = useMusicStore((state) => state.playbackLoop);
  const playbackTransportIntent = useMusicStore((state) => state.playbackTransportIntent);
  const editCursorTick = useMusicStore((state) => state.editCursorTick);
  const trackControls = useMusicStore((state) => state.trackControls);
  const setPlaybackStatus = useMusicStore((state) => state.setPlaybackStatus);
  const setPlaybackPosition = useMusicStore((state) => state.setPlaybackPosition);
  const setPlaybackSourceKey = useMusicStore((state) => state.setPlaybackSourceKey);
  const setPlaybackOperationEpoch = useMusicStore((state) => state.setPlaybackOperationEpoch);
  const setPlaybackActivity = useMusicStore((state) => state.setPlaybackActivity);
  const resetPlaybackActivity = useMusicStore((state) => state.resetPlaybackActivity);
  const setUiError = useMusicStore((state) => state.setUiError);
  const playFromCursor = useMusicStore((state) => state.playFromCursor);
  const setLoopFromSelection = useMusicStore((state) => state.setLoopFromSelection);
  const clearPlaybackLoop = useMusicStore((state) => state.clearPlaybackLoop);
  const setPlaybackLoopEnabled = useMusicStore((state) => state.setPlaybackLoopEnabled);
  const updateMixerTrackControl = useMusicStore((state) => state.updateMixerTrackControl);
  const playbackActivity = useMusicStore((state) => state.playbackActivity);
  const syncTrackControlsFromComposition = useMusicStore((state) => state.syncTrackControlsFromComposition);
  const syncArrangementCandidateTrackControls = useMusicStore(
    (state) => state.syncArrangementCandidateTrackControls,
  );
  const syncDevelopmentCandidateTrackControls = useMusicStore(
    (state) => state.syncDevelopmentCandidateTrackControls,
  );
  const syncPreviewTrackControls = useMusicStore((state) => state.syncPreviewTrackControls);
  const syncVersionAuditionTrackControls = useMusicStore(
    (state) => state.syncVersionAuditionTrackControls,
  );

  const arrangementCandidateAudition = arrangementAuditionMode === ARRANGEMENT_AUDITION_CANDIDATE;

  const playbackResolved = useMemo(() => resolvePlaybackSource(
    {
      editedMusicJson,
      arrangementAuditionMode,
      arrangementCandidates,
      arrangementSelectedCandidateId,
      generationAuditionActive,
      generationCandidate,
      aiEditAuditionActive,
      aiEditCandidate,
      motifAuditionActive,
      motifCandidate,
      reharmonizeAuditionActive,
      reharmonizeCandidate,
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
    aiEditAuditionActive,
    aiEditCandidate,
    arrangementAuditionMode,
    arrangementCandidates,
    arrangementSelectedCandidateId,
    developmentAuditionActive,
    developmentCandidates,
    developmentSelectedCandidateId,
    editedMusicJson,
    generationAuditionActive,
    generationCandidate,
    motifAuditionActive,
    motifCandidate,
    reharmonizeAuditionActive,
    reharmonizeCandidate,
    versionAuditionActive,
    versionRevisionDetails,
    versionSelectedRevisionId,
  ]);
  const playbackComposition = playbackResolved.composition;
  const playbackSource = playbackResolved.source;
  const playbackSourceKey = playbackResolved.sourceKey;
  const playbackMixerScope = playbackResolved.mixerScope;
  const versionAudition = playbackSource === 'version';

  const activeTrackControls = (() => {
    switch (playbackMixerScope) {
      case PLAYBACK_MIXER_SCOPE_ARRANGEMENT:
        return arrangementCandidateTrackControls;
      case PLAYBACK_MIXER_SCOPE_VERSION:
        return versionAuditionTrackControls;
      case PLAYBACK_MIXER_SCOPE_DEVELOPMENT:
        return developmentCandidateTrackControls;
      case PLAYBACK_MIXER_SCOPE_PREVIEW:
        return previewTrackControls;
      case PLAYBACK_MIXER_SCOPE_WORKING:
      default:
        return trackControls;
    }
  })();

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
  const [instrumentStatuses, setInstrumentStatuses] = useState({});
  const [mixerCollapsed, setMixerCollapsed] = useState(false);

  useEffect(() => {
    if (!playbackComposition) {
      return;
    }
    // Candidate / version / preview audition must never prune working mixer controls.
    if (playbackMixerScope === PLAYBACK_MIXER_SCOPE_ARRANGEMENT) {
      syncArrangementCandidateTrackControls(playbackComposition);
      return;
    }
    if (playbackMixerScope === PLAYBACK_MIXER_SCOPE_VERSION) {
      syncVersionAuditionTrackControls(playbackComposition);
      return;
    }
    if (playbackMixerScope === PLAYBACK_MIXER_SCOPE_DEVELOPMENT) {
      syncDevelopmentCandidateTrackControls(playbackComposition);
      return;
    }
    if (playbackMixerScope === PLAYBACK_MIXER_SCOPE_PREVIEW) {
      syncPreviewTrackControls(playbackComposition);
      return;
    }
    syncTrackControlsFromComposition(playbackComposition);
  }, [
    playbackComposition,
    playbackMixerScope,
    syncArrangementCandidateTrackControls,
    syncDevelopmentCandidateTrackControls,
    syncPreviewTrackControls,
    syncTrackControlsFromComposition,
    syncVersionAuditionTrackControls,
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
      resetPlaybackActivity();
      setInstrumentStatuses({});
      setPlaybackStatus('idle');
      setPlaybackPosition({ seconds: 0, bar: 1 });
    };
  }, [resetPlaybackActivity, setPlaybackPosition, setPlaybackStatus]);

  useEffect(() => {
    setPlaybackSourceKey(playbackSourceKey);
  }, [playbackSourceKey, setPlaybackSourceKey]);

  useEffect(() => {
    if (!scheduledRevisionRef.current) {
      return undefined;
    }
    if (scheduledRevisionRef.current === playbackRevision) {
      return undefined;
    }
    // Physically stop whenever audible revision / source identity changes,
    // regardless of the Zustand status string.
    logger.info('Active playback stopped because playback source changed', {
      previousRevision: scheduledRevisionRef.current.slice(0, 48),
      currentRevision: String(playbackRevision).slice(0, 48),
      sourceKey: playbackSourceKey,
      mixerScope: playbackMixerScope,
      developmentAudition: developmentAuditionActive,
      arrangementAudition: arrangementCandidateAudition,
    });
    if (engineRef.current?.invalidateSource) {
      engineRef.current.invalidateSource({
        nextSourceKey: playbackSourceKey,
        reason: 'source_or_revision_change',
      });
    }
    stopEverything({
      engineRef,
      legacySynthRef,
      positionTimerRef,
      scheduledRevisionRef,
      setPlaybackStatus,
      setPlaybackPosition,
    });
    resetPlaybackActivity();
    return undefined;
  }, [
    playbackRevision,
    playbackSourceKey,
    playbackMixerScope,
    developmentAuditionActive,
    arrangementCandidateAudition,
    resetPlaybackActivity,
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
    logger.debug('Applying live track control overrides', sanitizeMixerLogMeta({
      mixerScope: playbackMixerScope,
      sourceKey: playbackSourceKey,
      trackCount: Object.keys(activeTrackControls || {}).length,
    }));
    engineRef.current.applyTrackOverrides(activeTrackControls);
    return undefined;
  }, [
    activeTrackControls,
    playbackComposition,
    playbackMixerScope,
    playbackSourceKey,
    playbackStatus,
  ]);

  // Poll activity meters at ~12.5 Hz; store updates only on material change.
  useEffect(() => {
    if (!engineRef.current || playbackStatus !== 'playing') {
      return undefined;
    }
    const timer = window.setInterval(() => {
      const snapshot = engineRef.current?.getActivitySnapshot?.();
      if (snapshot) {
        setPlaybackActivity(snapshot);
      }
      const epoch = engineRef.current?.getOperationEpoch?.();
      if (epoch != null) {
        setPlaybackOperationEpoch(epoch);
      }
    }, 80);
    return () => window.clearInterval(timer);
  }, [playbackStatus, setPlaybackActivity, setPlaybackOperationEpoch]);

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
          setInstrumentStatuses,
          startTick,
          loop: playbackLoop,
          sourceKey: playbackSourceKey,
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
          : playbackResolved.sourceKind === 'ai_edit'
            ? ' · auditioning AI edit'
            : playbackResolved.sourceKind === 'motif'
              ? ' · auditioning motif'
              : playbackResolved.sourceKind === 'reharmonize'
                ? ' · auditioning reharmonize'
                : playbackSource === 'generation'
                  ? ' · auditioning generation candidate'
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
          instrumentStatuses={instrumentStatuses}
          activityLevels={playbackActivity?.tracks || {}}
          activityClipped={Boolean(playbackActivity?.clipped)}
          disabled={!playbackComposition}
          collapsed={mixerCollapsed}
          onCollapsedChange={setMixerCollapsed}
          ariaLabel={
            playbackMixerScope === PLAYBACK_MIXER_SCOPE_ARRANGEMENT
              ? 'Arrangement candidate mixer'
              : playbackMixerScope === PLAYBACK_MIXER_SCOPE_VERSION
                ? 'Version audition mixer'
                : playbackMixerScope === PLAYBACK_MIXER_SCOPE_DEVELOPMENT
                  ? 'Development candidate mixer'
                  : playbackMixerScope === PLAYBACK_MIXER_SCOPE_PREVIEW
                    ? 'Preview mixer'
                    : 'Track mixer'
          }
          hint={
            playbackMixerScope === PLAYBACK_MIXER_SCOPE_WORKING
              ? 'Tracks / mixer (canonical composition.v2; mute/solo are UI-only)'
              : `${playbackMixerScope} mixer (ephemeral; does not change working track controls)`
          }
          onMuteToggle={(trackId) => {
            const current = activeTrackControls[trackId] || {};
            logger.debug('User mute toggle', sanitizeMixerLogMeta({
              trackId,
              mixerScope: playbackMixerScope,
              muted: !current.muted,
            }));
            updateMixerTrackControl(playbackMixerScope, trackId, { muted: !current.muted });
          }}
          onSoloToggle={(trackId) => {
            const current = activeTrackControls[trackId] || {};
            logger.debug('User solo toggle', sanitizeMixerLogMeta({
              trackId,
              mixerScope: playbackMixerScope,
              solo: !current.solo,
            }));
            updateMixerTrackControl(playbackMixerScope, trackId, { solo: !current.solo });
          }}
          onTrimChange={(trackId, trimDb) => {
            logger.debug('User trim change', sanitizeMixerLogMeta({
              trackId,
              mixerScope: playbackMixerScope,
              trimDb,
            }));
            updateMixerTrackControl(playbackMixerScope, trackId, { trimDb });
          }}
          onPanChange={(trackId, panOffset) => {
            logger.debug('User pan change', sanitizeMixerLogMeta({
              trackId,
              mixerScope: playbackMixerScope,
              panOffset,
            }));
            updateMixerTrackControl(playbackMixerScope, trackId, { panOffset });
          }}
          onSendChange={(trackId, reverbSend) => {
            logger.debug('User send change', sanitizeMixerLogMeta({
              trackId,
              mixerScope: playbackMixerScope,
              reverbSend,
            }));
            updateMixerTrackControl(playbackMixerScope, trackId, { reverbSend });
          }}
          onVolumeChange={(trackId, volumeMidi) => {
            logger.debug('User volume change', sanitizeMixerLogMeta({
              trackId,
              mixerScope: playbackMixerScope,
              volumeMidi,
            }));
            updateMixerTrackControl(playbackMixerScope, trackId, { volumeMidi });
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
  setInstrumentStatuses = null,
  startTick = null,
  loop = null,
  sourceKey = null,
}) {
  logger.info('Using canonical composition playback path', {
    startTick: startTick == null ? null : Number(startTick),
    loopEnabled: Boolean(loop?.enabled),
    revisionPrefix: audibleRevisionKey(playbackComposition).slice(0, 48),
    sourceKey,
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
  await engineRef.current.prepare({
    tracks: playbackComposition.tracks,
    schedule,
    composition: playbackComposition,
    tempo: playbackComposition.tempo,
    trackOverrides: trackControls,
    startTick: startTick == null ? null : Number(startTick),
    loop,
    sourceKey,
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

  if (typeof setInstrumentStatuses === 'function') {
    setInstrumentStatuses(engineRef.current.getInstrumentStatuses?.() || {});
  }

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
    let position;
    if (isCanonical && engineRef.current?.getPlaybackPosition) {
      position = engineRef.current.getPlaybackPosition();
    } else {
      const seconds = isCanonical && engineRef.current
        ? engineRef.current.getPositionSeconds()
        : Number(Tone.Transport.seconds) || 0;
      position = secondsToPlaybackPosition(seconds, {
        tempo: playbackComposition?.tempo,
        ticksPerQuarter: playbackComposition?.ticks_per_quarter,
        timeSignature: playbackComposition?.time_signature,
        composition: playbackComposition,
      });
    }
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
