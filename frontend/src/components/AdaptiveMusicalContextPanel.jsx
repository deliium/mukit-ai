import React from 'react';
import styled from 'styled-components';

import { useMusicStore } from '../store/musicStore.js';
import { STREAM_A, STREAM_B } from '../utils/adaptiveMusicalContext.js';

const Section = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`;

const Button = styled.button`
  min-height: 44px;
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid #4f46e5;
  background: #4f46e5;
  color: #fff;
  cursor: pointer;
`;

const Secondary = styled(Button)`
  background: #fff;
  color: #312e81;
`;

function shownIntensity(snapshot) {
  if (typeof snapshot?.intensity === 'number') {
    return String(snapshot.intensity);
  }
  const emitted = Array.isArray(snapshot?.emitted)
    ? snapshot.emitted.find((item) => item?.op === 'set_intensity')
    : null;
  return typeof emitted?.intensity === 'number' ? String(emitted.intensity) : '—';
}

export default function AdaptiveMusicalContextPanel() {
  const snapshot = useMusicStore((state) => state.adaptiveMusicalContext);
  const error = useMusicStore((state) => state.adaptiveMusicalContextError);
  const scoreId = useMusicStore((state) => state.adaptiveScoreId);
  const startAdaptiveMusicalContext = useMusicStore((state) => state.startAdaptiveMusicalContext);
  const sendAdaptiveContextSeries = useMusicStore((state) => state.sendAdaptiveContextSeries);
  const warningCodes = Array.isArray(snapshot?.warnings)
    ? snapshot.warnings.map((item) => item.code).filter(Boolean).join(', ')
    : '';

  return (
    <Section data-testid="adaptive-musical-context">
      <strong>Musical context</strong>
      <Row>
        <Button
          type="button"
          data-testid="adaptive-context-arm"
          disabled={!scoreId}
          onClick={() => startAdaptiveMusicalContext()}
        >
          Arm example
        </Button>
        <Secondary
          type="button"
          data-testid="adaptive-context-flap"
          disabled={!scoreId}
          onClick={() => sendAdaptiveContextSeries(STREAM_A)}
        >
          Flap
        </Secondary>
        <Secondary
          type="button"
          data-testid="adaptive-context-sustain"
          disabled={!scoreId}
          onClick={() => sendAdaptiveContextSeries(STREAM_B)}
        >
          Sustain
        </Secondary>
      </Row>
      <span data-testid="adaptive-context-state">
        Held state: {snapshot?.musical_state_id || '—'}
      </span>
      <span data-testid="adaptive-context-intensity">
        Intensity: {shownIntensity(snapshot)}
      </span>
      <span data-testid="adaptive-context-dwell">
        Dwell: {snapshot ? snapshot.dwell_count : '—'}
      </span>
      <span data-testid="adaptive-context-warnings">
        {warningCodes || 'Warnings: none'}
      </span>
      {error ? <p data-testid="adaptive-context-error">{error}</p> : null}
    </Section>
  );
}
