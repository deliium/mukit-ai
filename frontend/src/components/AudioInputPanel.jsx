import React, { useEffect, useMemo, useRef } from 'react';
import styled from 'styled-components';
import { AUDIO_PHASES, useMusicStore } from '../store/musicStore.js';
import { isAcceptedAudioFilename } from '../utils/audioInputSupport.js';
import { midiToPitch } from '../utils/pianoRollEvents.js';
import { createAppLogger } from '../utils/appLogger.js';

const log = createAppLogger('audioCapture');

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
  border: 1px solid ${(props) => (props.$active ? '#b45309' : '#cbd5e1')};
  background: ${(props) => {
    if (props.$variant === 'record') return props.$active ? '#fde68a' : '#fef3c7';
    if (props.$variant === 'primary') return '#e0e7ff';
    if (props.$variant === 'danger') return '#fee2e2';
    return '#fff';
  }};
  color: ${(props) => (props.$variant === 'record' ? '#78350f' : '#1e293b')};
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

const Select = styled.select`
  min-height: 36px;
  padding: 4px 8px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #1e293b;
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

const NoteList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 160px;
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
  background: ${(props) => (props.$low ? 'repeating-linear-gradient(45deg, #fffbeb, #fffbeb 6px, #fef3c7 6px, #fef3c7 12px)' : '#fff')};
  color: ${(props) => (props.$low ? '#92400e' : '#334155')};

  &:last-child {
    border-bottom: none;
  }
`;

const AudioInputPanel = () => {
  const fileRef = useRef(null);
  const editedMusicJson = useMusicStore((s) => s.editedMusicJson);
  const audioSupport = useMusicStore((s) => s.audioSupport);
  const audioPhase = useMusicStore((s) => s.audioPhase);
  const audioPreview = useMusicStore((s) => s.audioPreview);
  const audioSelectedProvisionalIds = useMusicStore((s) => s.audioSelectedProvisionalIds);
  const audioIncludeLowConfidence = useMusicStore((s) => s.audioIncludeLowConfidence);
  const audioQuantizeOnApply = useMusicStore((s) => s.audioQuantizeOnApply);
  const audioDestinationTrackId = useMusicStore((s) => s.audioDestinationTrackId);
  const audioErrorCode = useMusicStore((s) => s.audioErrorCode);
  const audioErrorMessage = useMusicStore((s) => s.audioErrorMessage);
  const audioConfidenceThreshold = useMusicStore((s) => s.audioConfidenceThreshold);
  const pianoRollTrackId = useMusicStore((s) => s.pianoRollTrackId);

  const probeAudioSupport = useMusicStore((s) => s.probeAudioSupport);
  const setAudioDestinationTrackId = useMusicStore((s) => s.setAudioDestinationTrackId);
  const setAudioIncludeLowConfidence = useMusicStore((s) => s.setAudioIncludeLowConfidence);
  const setAudioQuantizeOnApply = useMusicStore((s) => s.setAudioQuantizeOnApply);
  const toggleAudioProvisionalId = useMusicStore((s) => s.toggleAudioProvisionalId);
  const startAudioRecording = useMusicStore((s) => s.startAudioRecording);
  const stopAudioRecordingAndTranscribe = useMusicStore((s) => s.stopAudioRecordingAndTranscribe);
  const cancelAudioRecording = useMusicStore((s) => s.cancelAudioRecording);
  const transcribeAudioFile = useMusicStore((s) => s.transcribeAudioFile);
  const applyAudioTranscription = useMusicStore((s) => s.applyAudioTranscription);
  const discardAudioTranscription = useMusicStore((s) => s.discardAudioTranscription);

  useEffect(() => {
    if (!audioSupport) {
      probeAudioSupport();
    }
  }, [audioSupport, probeAudioSupport]);

  const tracks = useMemo(
    () => (Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : []),
    [editedMusicJson],
  );

  const destination = audioDestinationTrackId || pianoRollTrackId || tracks[0]?.id || '';
  const recording = audioPhase === AUDIO_PHASES.RECORDING
    || audioPhase === AUDIO_PHASES.REQUESTING_MIC;
  const busy = audioPhase === AUDIO_PHASES.UPLOADING
    || audioPhase === AUDIO_PHASES.TRANSCRIBING
    || audioPhase === AUDIO_PHASES.APPLYING;
  const inReview = audioPhase === AUDIO_PHASES.REVIEW && audioPreview;
  const selectedSet = useMemo(
    () => new Set((audioSelectedProvisionalIds || []).map(String)),
    [audioSelectedProvisionalIds],
  );

  const onPickFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) {
      return;
    }
    if (!isAcceptedAudioFilename(file.name) && !String(file.type || '').startsWith('audio/')) {
      log.warn('Rejected audio file extension', { name: file.name });
      return;
    }
    log.info('Audio file selected for transcription', { byteCount: file.size });
    await transcribeAudioFile(file);
  };

  if (!editedMusicJson) {
    return null;
  }

  return (
    <Panel data-testid="audio-input-panel">
      <Title>Melody transcription (mono)</Title>
      <Hint>
        Hum, whistle, or play a single-line melody. Review confidence before Apply —
        this is not polyphonic song transcription or AI composition.
      </Hint>
      <Row>
        <Label>
          Destination
          <Select
            value={destination || ''}
            onChange={(event) => setAudioDestinationTrackId(event.target.value || null)}
            disabled={busy || recording}
            data-testid="audio-destination-track"
          >
            {tracks.map((track) => (
              <option key={track.id} value={track.id}>
                {track.name || track.id}
              </option>
            ))}
          </Select>
        </Label>
        <Button
          type="button"
          $variant="record"
          $active={recording}
          disabled={busy || (audioSupport && !audioSupport.supported && !recording)}
          onClick={() => {
            if (recording) {
              stopAudioRecordingAndTranscribe();
            } else {
              startAudioRecording();
            }
          }}
          data-testid="audio-record-toggle"
        >
          {recording ? 'Stop & transcribe' : 'Record mic'}
        </Button>
        {recording ? (
          <Button type="button" $variant="danger" onClick={() => cancelAudioRecording()}>
            Cancel
          </Button>
        ) : null}
        <Button
          type="button"
          disabled={busy || recording}
          onClick={() => fileRef.current?.click()}
          data-testid="audio-upload-button"
        >
          Upload audio
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".wav,.flac,.ogg,.oga,.mp3,audio/*"
          hidden
          onChange={onPickFile}
          data-testid="audio-file-input"
        />
      </Row>
      <Status data-testid="audio-phase-status">
        Phase: {audioPhase}
        {audioSupport ? ` · mic: ${audioSupport.reason}` : ''}
        {audioPreview?.engine?.id ? ` · engine: ${audioPreview.engine.id}` : ''}
      </Status>
      {audioErrorCode ? (
        <ErrorText data-testid="audio-error">
          {audioErrorMessage || audioErrorCode}
        </ErrorText>
      ) : null}

      {inReview ? (
        <>
          <Row>
            <Label>
              <input
                type="checkbox"
                checked={audioIncludeLowConfidence}
                onChange={(event) => setAudioIncludeLowConfidence(event.target.checked)}
                data-testid="audio-include-low-confidence"
              />
              Include low-confidence (&lt; {audioConfidenceThreshold})
            </Label>
            <Label>
              <input
                type="checkbox"
                checked={audioQuantizeOnApply}
                onChange={(event) => setAudioQuantizeOnApply(event.target.checked)}
                data-testid="audio-quantize-on-apply"
              />
              Quantize on Apply
            </Label>
            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>
              {audioQuantizeOnApply ? 'Grid snap' : 'Expressive timing'}
            </span>
          </Row>
          <NoteList data-testid="audio-preview-note-list">
            {(audioPreview.notes || []).map((note) => {
              const low = Number(note.confidence) < audioConfidenceThreshold;
              const pitch = midiToPitch(note.pitch).pitch || String(note.pitch);
              const checked = selectedSet.has(String(note.provisional_id));
              return (
                <NoteItem
                  key={note.provisional_id}
                  $low={low}
                  data-low-confidence={low ? 'true' : 'false'}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleAudioProvisionalId(note.provisional_id)}
                    aria-label={`Include ${pitch}`}
                  />
                  <span>
                    {pitch} · tick {note.start_tick}+{note.duration_ticks} · conf{' '}
                    {Number(note.confidence).toFixed(2)}
                    {low ? ' (low)' : ''}
                  </span>
                </NoteItem>
              );
            })}
          </NoteList>
          <Row>
            <Button
              type="button"
              $variant="primary"
              disabled={busy || selectedSet.size === 0}
              onClick={() => applyAudioTranscription()}
              data-testid="audio-apply-button"
            >
              Apply to track
            </Button>
            <Button
              type="button"
              onClick={() => discardAudioTranscription()}
              data-testid="audio-discard-button"
            >
              Discard
            </Button>
          </Row>
        </>
      ) : null}
    </Panel>
  );
};

export default AudioInputPanel;
