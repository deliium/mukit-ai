/**
 * V4 mixed audio recovery panel — sibling to AudioInputPanel (mono).
 * Source audition: HTMLAudioElement only (not playbackSource / Tone Transport).
 */

import React, { useEffect, useMemo, useRef } from 'react';
import styled from 'styled-components';
import { AUDIO_RECOVERY_PHASES, useMusicStore } from '../store/musicStore.js';
import { isAcceptedAudioFilename } from '../utils/audioInputSupport.js';
import { midiToPitch } from '../utils/pianoRollEvents.js';
import { createAppLogger } from '../utils/appLogger.js';
import AudioAlignmentWaveform from './AudioAlignmentWaveform.jsx';

const log = createAppLogger('audioRecoveryUi');

const Panel = styled.div`
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 10px;
`;

const Title = styled.h4`
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
  color: #334155;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.8rem;
  color: #64748b;
`;

const Meta = styled.div`
  font-size: 0.8rem;
  color: #475569;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  min-height: 36px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid ${(props) => (props.$active ? '#0f766e' : '#cbd5e1')};
  background: ${(props) => {
    if (props.$variant === 'record') return props.$active ? '#99f6e4' : '#ccfbf1';
    if (props.$variant === 'primary') return '#ccfbf1';
    if (props.$variant === 'danger') return '#fee2e2';
    return '#fff';
  }};
  color: ${(props) => (props.$variant === 'record' ? '#134e4a' : '#1e293b')};
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

const Label = styled.label`
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  color: #475569;
`;

const Status = styled.div`
  font-size: 0.85rem;
  color: #475569;
`;

const ErrorText = styled.div`
  font-size: 0.85rem;
  color: #991b1b;
`;

const WarnText = styled.div`
  font-size: 0.85rem;
  color: #92400e;
`;

const Badge = styled.span`
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.75rem;
  background: ${(props) => (props.$low ? '#fef3c7' : '#ecfdf5')};
  color: ${(props) => (props.$low ? '#92400e' : '#065f46')};
`;

const NoteList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 180px;
  overflow: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
`;

const NoteItem = styled.li`
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  font-size: 0.8rem;
  border-bottom: 1px solid #f1f5f9;
  background: ${(props) => (props.$low
    ? 'repeating-linear-gradient(45deg, #fffbeb, #fffbeb 6px, #fef3c7 6px, #fef3c7 12px)'
    : '#fff')};
  color: ${(props) => (props.$low ? '#92400e' : '#334155')};

  &:last-child {
    border-bottom: none;
  }
`;

const AudioRecoveryPanel = () => {
  const fileRef = useRef(null);
  const audioRef = useRef(null);

  const recoveryPhase = useMusicStore((s) => s.recoveryPhase);
  const recoveryPreview = useMusicStore((s) => s.recoveryPreview);
  const recoverySelectedProvisionalIds = useMusicStore((s) => s.recoverySelectedProvisionalIds);
  const recoveryIncludeLowConfidence = useMusicStore((s) => s.recoveryIncludeLowConfidence);
  const recoveryDisableSeparation = useMusicStore((s) => s.recoveryDisableSeparation);
  const recoveryConfidenceThreshold = useMusicStore((s) => s.recoveryConfidenceThreshold);
  const recoveryErrorCode = useMusicStore((s) => s.recoveryErrorCode);
  const recoveryErrorMessage = useMusicStore((s) => s.recoveryErrorMessage);
  const recoveryBindWarning = useMusicStore((s) => s.recoveryBindWarning);
  const recoverySourceObjectUrl = useMusicStore((s) => s.recoverySourceObjectUrl);
  const recoveryJobStatus = useMusicStore((s) => s.recoveryJobStatus);
  const currentProjectId = useMusicStore((s) => s.currentProjectId);
  const alignmentDocument = useMusicStore((s) => s.alignmentDocument);
  const sourceSeekRequest = useMusicStore((s) => s.sourceSeekRequest);
  const audioWindowHighlight = useMusicStore((s) => s.audioWindowHighlight);
  const onSourceAudioTimeUpdate = useMusicStore((s) => s.onSourceAudioTimeUpdate);
  const setSourceAuditionMode = useMusicStore((s) => s.setSourceAuditionMode);

  const startRecoveryRecording = useMusicStore((s) => s.startRecoveryRecording);
  const stopRecoveryRecordingAndEnqueue = useMusicStore((s) => s.stopRecoveryRecordingAndEnqueue);
  const cancelRecoveryRecording = useMusicStore((s) => s.cancelRecoveryRecording);
  const enqueueRecoveryFile = useMusicStore((s) => s.enqueueRecoveryFile);
  const applyAudioRecovery = useMusicStore((s) => s.applyAudioRecovery);
  const discardAudioRecovery = useMusicStore((s) => s.discardAudioRecovery);
  const setRecoveryIncludeLowConfidence = useMusicStore((s) => s.setRecoveryIncludeLowConfidence);
  const setRecoveryDisableSeparation = useMusicStore((s) => s.setRecoveryDisableSeparation);
  const setRecoverySelectedProvisionalIds = useMusicStore((s) => s.setRecoverySelectedProvisionalIds);
  const setRecoveryInstallFlags = useMusicStore((s) => s.setRecoveryInstallFlags);
  const recoveryInstallFlags = useMusicStore((s) => s.recoveryInstallFlags);

  const selectedSet = useMemo(
    () => new Set((recoverySelectedProvisionalIds || []).map(String)),
    [recoverySelectedProvisionalIds],
  );

  const notes = Array.isArray(recoveryPreview?.notes) ? recoveryPreview.notes : [];
  const scaffolding = recoveryPreview?.scaffolding || null;
  const threshold = recoveryConfidenceThreshold
    || Number(recoveryPreview?.summary?.include_threshold)
    || 0.5;

  useEffect(() => {
    if (audioRef.current && recoverySourceObjectUrl) {
      audioRef.current.src = recoverySourceObjectUrl;
    }
  }, [recoverySourceObjectUrl]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el || !sourceSeekRequest) return;
    const seconds = Number(sourceSeekRequest.seconds);
    if (!Number.isFinite(seconds)) return;
    try {
      el.currentTime = Math.max(0, seconds);
      log.debug('Applied source seek', {
        seconds: Number(seconds.toFixed(3)),
        reason: sourceSeekRequest.reason,
      });
    } catch {
      log.warn('Source seek failed', { reason: sourceSeekRequest.reason });
    }
  }, [sourceSeekRequest]);

  const quality = alignmentDocument?.quality || null;

  const busy = recoveryPhase === AUDIO_RECOVERY_PHASES.UPLOADING
    || recoveryPhase === AUDIO_RECOVERY_PHASES.RUNNING
    || recoveryPhase === AUDIO_RECOVERY_PHASES.APPLYING
    || recoveryPhase === AUDIO_RECOVERY_PHASES.BINDING
    || recoveryPhase === AUDIO_RECOVERY_PHASES.REQUESTING_MIC;

  const inReview = recoveryPhase === AUDIO_RECOVERY_PHASES.REVIEW
    || recoveryPhase === AUDIO_RECOVERY_PHASES.BOUND;

  const onFileChange = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (!isAcceptedAudioFilename(file.name)) {
      log.warn('Rejected recovery filename', { name: file.name });
      return;
    }
    log.info('Recovery file selected', { basename: file.name, bytes: file.size });
    await enqueueRecoveryFile(file);
  };

  const toggleNote = (provisionalId) => {
    const id = String(provisionalId);
    const next = new Set(selectedSet);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setRecoverySelectedProvisionalIds(Array.from(next));
  };

  return (
    <Panel data-testid="audio-recovery-panel">
      <Title>Audio recovery (mixed)</Title>
      <Hint>
        Import a short demo → optional stem separation → estimated tempo/structure/harmony
        and confidence-gated notes. Confidence stays off the score. Source plays in an
        HTML audio element (composition audition stays on PlaybackControls).
      </Hint>

      <Row>
        <Button
          type="button"
          $variant="record"
          $active={recoveryPhase === AUDIO_RECOVERY_PHASES.RECORDING}
          disabled={busy && recoveryPhase !== AUDIO_RECOVERY_PHASES.RECORDING}
          data-testid="audio-recovery-record-toggle"
          onClick={() => {
            if (recoveryPhase === AUDIO_RECOVERY_PHASES.RECORDING) {
              stopRecoveryRecordingAndEnqueue();
            } else {
              startRecoveryRecording();
            }
          }}
        >
          {recoveryPhase === AUDIO_RECOVERY_PHASES.RECORDING ? 'Stop & recover' : 'Record'}
        </Button>
        {recoveryPhase === AUDIO_RECOVERY_PHASES.RECORDING ? (
          <Button type="button" $variant="danger" onClick={() => cancelRecoveryRecording()}>
            Cancel
          </Button>
        ) : null}
        <Button
          type="button"
          disabled={busy}
          data-testid="audio-recovery-upload-button"
          onClick={() => fileRef.current?.click()}
        >
          Upload WAV
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".wav,.flac,.ogg,.mp3,audio/*"
          hidden
          data-testid="audio-recovery-file-input"
          onChange={onFileChange}
        />
        <Label>
          <input
            type="checkbox"
            checked={recoveryDisableSeparation}
            onChange={(e) => setRecoveryDisableSeparation(e.target.checked)}
            disabled={busy || inReview}
            data-testid="audio-recovery-skip-separation"
          />
          Skip separation
        </Label>
      </Row>

      <Status data-testid="audio-recovery-phase-status">
        Phase: {recoveryPhase}
        {recoveryJobStatus ? ` · job ${recoveryJobStatus}` : ''}
        {!currentProjectId ? ' · open a project before Apply→Bind' : ''}
      </Status>

      {recoveryErrorMessage ? (
        <ErrorText data-testid="audio-recovery-error">
          {recoveryErrorMessage}{recoveryErrorCode ? ` (${recoveryErrorCode})` : ''}
        </ErrorText>
      ) : null}
      {recoveryBindWarning ? (
        <WarnText data-testid="audio-recovery-bind-warning">{recoveryBindWarning}</WarnText>
      ) : null}

      {scaffolding ? (
        <Row data-testid="audio-recovery-scaffolding">
          <Badge
            $low={Number(scaffolding.tempo_confidence) < threshold}
            data-testid="audio-recovery-tempo-badge"
          >
            tempo {scaffolding.tempo_bpm} bpm
            {' '}
            ({Math.round(Number(scaffolding.tempo_confidence) * 100)}%)
          </Badge>
          {scaffolding.key ? (
            <Badge $low={Number(scaffolding.key.confidence) < threshold}>
              key {scaffolding.key.tonic} {scaffolding.key.mode}
            </Badge>
          ) : null}
          <Badge>
            stems {recoveryPreview?.summary?.stem_count ?? recoveryPreview?.stems?.length ?? 0}
          </Badge>
          <Badge
            $low={(recoveryPreview?.summary?.low_confidence_count || 0) > 0}
            data-testid="audio-recovery-low-conf-badge"
          >
            low-conf {recoveryPreview?.summary?.low_confidence_count ?? 0}
          </Badge>
        </Row>
      ) : null}

      {inReview && scaffolding ? (
        <Row>
          <Label>
            <input
              type="checkbox"
              checked={Boolean(recoveryInstallFlags?.installTempo)}
              onChange={(e) => setRecoveryInstallFlags({
                ...(recoveryInstallFlags || {}),
                installTempo: e.target.checked,
              })}
            />
            Install tempo
          </Label>
          <Label>
            <input
              type="checkbox"
              checked={Boolean(recoveryInstallFlags?.installSections)}
              onChange={(e) => setRecoveryInstallFlags({
                ...(recoveryInstallFlags || {}),
                installSections: e.target.checked,
              })}
            />
            Install sections
          </Label>
          <Label>
            <input
              type="checkbox"
              checked={Boolean(recoveryInstallFlags?.installHarmony)}
              onChange={(e) => setRecoveryInstallFlags({
                ...(recoveryInstallFlags || {}),
                installHarmony: e.target.checked,
              })}
            />
            Install harmony (metadata)
          </Label>
          <Label>
            <input
              type="checkbox"
              checked={recoveryIncludeLowConfidence}
              onChange={(e) => setRecoveryIncludeLowConfidence(e.target.checked)}
              data-testid="audio-recovery-include-low-confidence"
            />
            Include low-confidence notes
          </Label>
        </Row>
      ) : null}

      {notes.length > 0 ? (
        <NoteList data-testid="audio-recovery-note-list">
          {notes.map((note) => {
            const id = String(note.provisional_id);
            const conf = Number(note.confidence);
            const low = conf < threshold;
            const { pitch } = midiToPitch(Math.round(Number(note.pitch)));
            return (
              <NoteItem key={id} $low={low} data-testid={`audio-recovery-note-${id}`}>
                <input
                  type="checkbox"
                  checked={selectedSet.has(id)}
                  onChange={() => toggleNote(id)}
                  disabled={recoveryPhase === AUDIO_RECOVERY_PHASES.BOUND}
                />
                <span>{note.stem || '?'}</span>
                <span>{pitch || note.pitch}</span>
                <span>@{note.start_tick}</span>
                <Badge $low={low}>{Math.round(conf * 100)}%</Badge>
              </NoteItem>
            );
          })}
        </NoteList>
      ) : null}

      {recoverySourceObjectUrl || recoveryPhase === AUDIO_RECOVERY_PHASES.BOUND ? (
        <>
          {quality ? (
            <Meta data-testid="audio-alignment-quality">
              Alignment: {Math.round((quality.overall_confidence || 0) * 100)}%
              {' · '}
              {quality.method || 'timeline_parametric'}
              {Array.isArray(quality.issues) && quality.issues.length
                ? ` · ${quality.issues.join(', ')}`
                : ''}
            </Meta>
          ) : null}
          <AudioAlignmentWaveform />
          <audio
            ref={audioRef}
            controls
            preload="metadata"
            style={{ width: '100%' }}
            data-testid="audio-recovery-source-audio"
            onTimeUpdate={(event) => {
              onSourceAudioTimeUpdate(event.currentTarget.currentTime);
            }}
            onPlay={() => setSourceAuditionMode('playing')}
            onPause={() => setSourceAuditionMode('idle')}
            onSeeking={() => setSourceAuditionMode('scrubbing')}
            onSeeked={() => {
              const el = audioRef.current;
              if (el && !el.paused) setSourceAuditionMode('playing');
              else setSourceAuditionMode('idle');
            }}
          >
            <track kind="captions" />
          </audio>
          {audioWindowHighlight ? (
            <Meta data-testid="audio-alignment-window">
              Window bars {audioWindowHighlight.startBar}–{audioWindowHighlight.endBar}
              {': '}
              {Number(audioWindowHighlight.startSeconds).toFixed(2)}s–
              {Number(audioWindowHighlight.endSeconds).toFixed(2)}s
            </Meta>
          ) : null}
        </>
      ) : null}

      {inReview ? (
        <Row>
          {recoveryPhase !== AUDIO_RECOVERY_PHASES.BOUND ? (
            <Button
              type="button"
              $variant="primary"
              disabled={busy || !currentProjectId}
              data-testid="audio-recovery-apply-bind"
              onClick={() => applyAudioRecovery()}
            >
              Apply → Bind
            </Button>
          ) : null}
          <Button
            type="button"
            $variant="danger"
            data-testid="audio-recovery-discard"
            onClick={() => discardAudioRecovery()}
          >
            Discard
          </Button>
        </Row>
      ) : null}
    </Panel>
  );
};

export default AudioRecoveryPanel;
