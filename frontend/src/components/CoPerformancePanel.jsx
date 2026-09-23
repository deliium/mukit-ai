import React from 'react';
import styled from 'styled-components';
import { MIDI_PHASES, useMusicStore } from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import { LIVE_ENGINE_UNAVAILABLE, LIVE_MIDI_PHASE_EXCLUSION } from '../utils/liveSessionContracts.js';

const log = createAppLogger('liveTransport');

const Panel = styled.div`
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 8px;
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
  border: 1px solid #cbd5e1;
  background: ${(props) => (props.$primary ? '#e0e7ff' : '#fff')};
  color: #1e293b;
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

const Meta = styled.div`
  font-size: 0.8rem;
  color: #64748b;
`;

const Badge = styled.span`
  font-size: 0.75rem;
  font-weight: 600;
  color: ${(props) => (props.$warn ? '#b45309' : '#334155')};
`;

const ErrorText = styled.div`
  color: #991b1b;
  font-size: 0.85rem;
`;

const CoPerformancePanel = () => {
  const livePhase = useMusicStore((s) => s.livePhase);
  const liveSessionSnapshot = useMusicStore((s) => s.liveSessionSnapshot);
  const liveErrorCode = useMusicStore((s) => s.liveErrorCode);
  const liveErrorMessage = useMusicStore((s) => s.liveErrorMessage);
  const liveHorizonBars = useMusicStore((s) => s.liveHorizonBars);
  const liveHorizonMs = useMusicStore((s) => s.liveHorizonMs);
  const liveStreamNoteOnCount = useMusicStore((s) => s.liveStreamNoteOnCount);
  const midiPhase = useMusicStore((s) => s.midiPhase);
  const midiDestinationTrackId = useMusicStore((s) => s.midiDestinationTrackId);
  const pianoRollTrackId = useMusicStore((s) => s.pianoRollTrackId);
  const editedMusicJson = useMusicStore((s) => s.editedMusicJson);
  const startLiveCoPerformance = useMusicStore((s) => s.startLiveCoPerformance);
  const stopLiveCoPerformance = useMusicStore((s) => s.stopLiveCoPerformance);
  const cancelLiveCoPerformance = useMusicStore((s) => s.cancelLiveCoPerformance);
  const commitLiveCoPerformance = useMusicStore((s) => s.commitLiveCoPerformance);
  const setLiveHorizon = useMusicStore((s) => s.setLiveHorizon);
  const setMidiDestinationTrackId = useMusicStore((s) => s.setMidiDestinationTrackId);

  const running = livePhase === 'running' || livePhase === 'degraded';
  const midiBlocking =
    midiPhase === MIDI_PHASES.ARMED
    || midiPhase === MIDI_PHASES.COUNTING_IN
    || midiPhase === MIDI_PHASES.RECORDING
    || midiPhase === MIDI_PHASES.STOPPING;

  const tracks = Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : [];
  const dest = midiDestinationTrackId || pianoRollTrackId || tracks[0]?.id || '';

  const snap = liveSessionSnapshot;
  const deg = snap?.degradation;
  const clock = snap?.transport;
  const harmony = snap?.active_harmony;
  const latency = snap?.latency_ms;

  const onStart = () => {
    const result = startLiveCoPerformance();
    log.info('UI start live', { ok: result?.ok, code: result?.code });
  };

  const onStop = () => {
    stopLiveCoPerformance({ reason: 'ui-stop' });
  };

  const onCancel = () => {
    cancelLiveCoPerformance({ reason: 'ui-cancel' });
  };

  const onCommit = () => {
    const result = commitLiveCoPerformance({
      trackId: dest,
      includeStream: true,
      includeAccompaniment: true,
    });
    log.info('UI commit live', { ok: result?.ok, code: result?.code });
  };

  return (
    <Panel data-testid="co-performance-panel">
      <Title>Co-performance</Title>
      <Row>
        <Button type="button" $primary disabled={running || midiBlocking} onClick={onStart}>
          Start
        </Button>
        <Button type="button" disabled={!running} onClick={onStop}>
          Stop
        </Button>
        <Button type="button" disabled={livePhase === 'idle'} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" disabled={livePhase === 'idle' || !dest} onClick={onCommit}>
          Commit
        </Button>
      </Row>
      <Row>
        <label>
          Horizon bars
          <input
            type="number"
            min={1}
            max={2}
            step={1}
            value={liveHorizonBars}
            disabled={running}
            onChange={(e) => setLiveHorizon({ bars: Number(e.target.value) })}
            style={{ width: 56, marginLeft: 6 }}
          />
        </label>
        <label>
          Horizon ms
          <input
            type="number"
            min={250}
            max={8000}
            step={250}
            value={liveHorizonMs}
            disabled={running}
            onChange={(e) => setLiveHorizon({ ms: Number(e.target.value) })}
            style={{ width: 72, marginLeft: 6 }}
          />
        </label>
        <label>
          Dest track
          <select
            value={dest || ''}
            onChange={(e) => setMidiDestinationTrackId(e.target.value || null)}
            style={{ marginLeft: 6 }}
          >
            {tracks.map((t) => (
              <option key={t.id} value={t.id}>{t.name || t.id}</option>
            ))}
          </select>
        </label>
      </Row>
      <Meta>
        Phase: {livePhase}
        {deg?.active ? (
          <Badge $warn>
            {' '}
            degraded ({deg.code || 'pattern'}) ×{deg.count || 0}
          </Badge>
        ) : null}
      </Meta>
      <Meta>
        Clock: bar {clock?.bar ?? '—'} beat {clock?.beat ?? '—'} tick {clock?.tick ?? '—'}
        {' · '}
        Harmony: {harmony?.symbol || '—'}
        {' · '}
        Stream ons: {liveStreamNoteOnCount}
      </Meta>
      <Meta>
        Latency ms — midi:
        {' '}
        {latency?.midi_input ?? '—'}
        {' / analysis: '}
        {latency?.analysis ?? '—'}
        {' / gen: '}
        {latency?.generation ?? '—'}
        {' / sched: '}
        {latency?.scheduling ?? '—'}
      </Meta>
      {midiBlocking ? (
        <ErrorText>
          MIDI record is active — stop recording before co-performance (
          {LIVE_MIDI_PHASE_EXCLUSION}
          ).
        </ErrorText>
      ) : null}
      {liveErrorCode === LIVE_ENGINE_UNAVAILABLE ? (
        <ErrorText>Playback engine unavailable — open transport playback first.</ErrorText>
      ) : null}
      {liveErrorMessage ? <ErrorText>{liveErrorMessage}</ErrorText> : null}
    </Panel>
  );
};

export default CoPerformancePanel;
