import React, { useEffect, useMemo, useRef } from 'react';
import * as Tone from 'tone';
import styled from 'styled-components';
import { MIDI_PHASES, useMusicStore } from '../store/musicStore.js';
import { createComputerKeyboardMidi } from '../utils/computerKeyboardMidi.js';
import { createAppLogger } from '../utils/appLogger.js';

const log = createAppLogger('midiInput');

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
  border: 1px solid ${(props) => (props.$active ? '#b91c1c' : '#cbd5e1')};
  background: ${(props) => {
    if (props.$variant === 'record') return props.$active ? '#fecaca' : '#fee2e2';
    if (props.$variant === 'primary') return '#e0e7ff';
    return '#fff';
  }};
  color: ${(props) => (props.$variant === 'record' ? '#7f1d1d' : '#1e293b')};
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

const ActiveKeys = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 24px;
`;

const KeyChip = styled.span`
  padding: 2px 6px;
  border-radius: 4px;
  background: #c7d2fe;
  color: #312e81;
  font-size: 0.75rem;
  font-weight: 600;
`;

const MidiInputPanel = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const midiSupport = useMusicStore((state) => state.midiSupport);
  const midiPhase = useMusicStore((state) => state.midiPhase);
  const midiAccessStatus = useMusicStore((state) => state.midiAccessStatus);
  const midiInputs = useMusicStore((state) => state.midiInputs);
  const midiSelectedInputId = useMusicStore((state) => state.midiSelectedInputId);
  const midiDestinationTrackId = useMusicStore((state) => state.midiDestinationTrackId);
  const midiArmed = useMusicStore((state) => state.midiArmed);
  const midiMetronomeEnabled = useMusicStore((state) => state.midiMetronomeEnabled);
  const midiCountInBars = useMusicStore((state) => state.midiCountInBars);
  const midiQuantizeAfterRecord = useMusicStore((state) => state.midiQuantizeAfterRecord);
  const midiTestKeyboardEnabled = useMusicStore((state) => state.midiTestKeyboardEnabled);
  const midiActiveNotes = useMusicStore((state) => state.midiActiveNotes);
  const midiErrorCode = useMusicStore((state) => state.midiErrorCode);
  const midiErrorMessage = useMusicStore((state) => state.midiErrorMessage);
  const midiTakeSummary = useMusicStore((state) => state.midiTakeSummary);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);

  const probeMidiSupport = useMusicStore((state) => state.probeMidiSupport);
  const enableMidiAccess = useMusicStore((state) => state.enableMidiAccess);
  const selectMidiInput = useMusicStore((state) => state.selectMidiInput);
  const setMidiDestinationTrackId = useMusicStore((state) => state.setMidiDestinationTrackId);
  const setMidiMetronomeEnabled = useMusicStore((state) => state.setMidiMetronomeEnabled);
  const setMidiCountInBars = useMusicStore((state) => state.setMidiCountInBars);
  const setMidiQuantizeAfterRecord = useMusicStore((state) => state.setMidiQuantizeAfterRecord);
  const setMidiTestKeyboardEnabled = useMusicStore((state) => state.setMidiTestKeyboardEnabled);
  const startMidiRecording = useMusicStore((state) => state.startMidiRecording);
  const stopMidiRecording = useMusicStore((state) => state.stopMidiRecording);
  const discardMidiTake = useMusicStore((state) => state.discardMidiTake);
  const commitPendingMidiTake = useMusicStore((state) => state.commitPendingMidiTake);
  const panicMidiNotes = useMusicStore((state) => state.panicMidiNotes);
  const injectMidiMessage = useMusicStore((state) => state.injectMidiMessage);

  const keyboardRef = useRef(null);

  useEffect(() => {
    if (!midiSupport) {
      probeMidiSupport();
    }
  }, [midiSupport, probeMidiSupport]);

  useEffect(() => {
    const kb = createComputerKeyboardMidi({
      enabled: false,
      onMessage: (bytes) => {
        injectMidiMessage(bytes);
      },
    });
    keyboardRef.current = kb;
    const detach = kb.attach(window);
    return () => {
      detach();
      keyboardRef.current = null;
    };
  }, [injectMidiMessage]);

  useEffect(() => {
    keyboardRef.current?.setEnabled(midiTestKeyboardEnabled);
  }, [midiTestKeyboardEnabled]);

  const tracks = useMemo(
    () => (Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : []),
    [editedMusicJson],
  );

  const recording = midiPhase === MIDI_PHASES.RECORDING || midiPhase === MIDI_PHASES.COUNTING_IN;
  const canRecord = Boolean(editedMusicJson)
    && (midiPhase === MIDI_PHASES.READY
      || midiPhase === MIDI_PHASES.ARMED
      || midiTestKeyboardEnabled);
  const unsupported = midiSupport && !midiSupport.supported && !midiTestKeyboardEnabled;

  const onEnable = async () => {
    log.debug('Enable MIDI clicked');
    await enableMidiAccess();
  };

  const onRecordToggle = () => {
    if (recording) {
      stopMidiRecording({ commit: true });
      return;
    }
    startMidiRecording({ Tone, skipCountIn: false });
  };

  return (
    <Panel data-testid="midi-input-panel">
      <Title>MIDI input</Title>
      <Status>
        Phase: {midiPhase}
        {midiAccessStatus !== 'idle' ? ` · access: ${midiAccessStatus}` : ''}
        {midiArmed ? ' · armed' : ''}
      </Status>

      {unsupported ? (
        <Status>
          Web MIDI unavailable ({midiSupport?.reason || 'unsupported'}). Use Test input or enable MIDI in a supported browser.
        </Status>
      ) : null}

      <Row>
        <Button
          type="button"
          $variant="primary"
          onClick={onEnable}
          disabled={midiPhase === MIDI_PHASES.ENABLING}
        >
          Enable MIDI
        </Button>
        <Select
          aria-label="MIDI input device"
          value={midiSelectedInputId || ''}
          onChange={(event) => selectMidiInput(event.target.value || null)}
          disabled={!midiInputs.length}
        >
          <option value="">Select device…</option>
          {midiInputs.map((input) => (
            <option key={input.id} value={input.id}>
              {input.name}
            </option>
          ))}
        </Select>
        <Select
          aria-label="MIDI destination track"
          value={midiDestinationTrackId || pianoRollTrackId || ''}
          onChange={(event) => setMidiDestinationTrackId(event.target.value || null)}
          disabled={!tracks.length}
        >
          {tracks.map((track) => (
            <option key={track.id} value={track.id}>
              {track.name || track.id}
            </option>
          ))}
        </Select>
      </Row>

      <Row>
        <Label>
          Count-in
          <Select
            aria-label="Count-in bars"
            value={String(midiCountInBars)}
            onChange={(event) => setMidiCountInBars(Number(event.target.value))}
          >
            <option value="0">0</option>
            <option value="1">1</option>
            <option value="2">2</option>
          </Select>
        </Label>
        <Label>
          <input
            type="checkbox"
            checked={midiMetronomeEnabled}
            onChange={(event) => setMidiMetronomeEnabled(event.target.checked)}
          />
          Metronome
        </Label>
        <Label>
          <input
            type="checkbox"
            checked={midiQuantizeAfterRecord}
            onChange={(event) => setMidiQuantizeAfterRecord(event.target.checked)}
          />
          Quantize after
        </Label>
        <Label>
          <input
            type="checkbox"
            checked={midiTestKeyboardEnabled}
            onChange={(event) => setMidiTestKeyboardEnabled(event.target.checked)}
          />
          Test input (QWERTY)
        </Label>
      </Row>

      <Row>
        <Button
          type="button"
          $variant="record"
          $active={recording}
          onClick={onRecordToggle}
          disabled={!canRecord && !recording}
          data-testid="midi-record-button"
        >
          {recording ? 'Stop & commit' : 'Record'}
        </Button>
        <Button type="button" onClick={() => discardMidiTake()} disabled={!recording && !midiTakeSummary}>
          Discard
        </Button>
        <Button
          type="button"
          onClick={() => commitPendingMidiTake()}
          disabled={!midiTakeSummary?.partial}
        >
          Commit partial
        </Button>
        <Button type="button" onClick={() => panicMidiNotes()}>
          Panic
        </Button>
      </Row>

      <ActiveKeys aria-label="Active MIDI notes">
        {(midiActiveNotes || []).map((pitch) => (
          <KeyChip key={pitch}>{pitch}</KeyChip>
        ))}
      </ActiveKeys>

      {midiTakeSummary ? (
        <Status>
          Last take: {midiTakeSummary.noteCount || 0} notes
          {midiTakeSummary.pedalCount ? `, ${midiTakeSummary.pedalCount} pedals` : ''}
          {midiTakeSummary.partial ? ' (partial — commit or discard)' : ''}
        </Status>
      ) : null}

      {midiErrorCode ? (
        <ErrorText>
          {midiErrorCode}
          {midiErrorMessage ? `: ${midiErrorMessage}` : ''}
        </ErrorText>
      ) : null}
    </Panel>
  );
};

export default MidiInputPanel;
