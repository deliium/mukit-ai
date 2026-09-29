import React, { useEffect } from 'react';
import styled from 'styled-components';

import { useMusicStore } from '../store/musicStore.js';
import { continuationEventsToSchedule } from '../utils/adaptiveContinuation.js';
import { getLivePlaybackEngine } from '../utils/livePlaybackEngineAccess.js';

const Block = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  border: 0;
  border-radius: 6px;
  background: #1d4ed8;
  color: white;
  padding: 6px 10px;
`;

function placeAheadNotes(snapshot, buffer, positionTick) {
  const engine = getLivePlaybackEngine();
  if (!engine?.scheduleContinuationAt) {
    return;
  }
  const events = continuationEventsToSchedule(snapshot, buffer, positionTick);
  engine.clearContinuationScheduled?.();
  events.forEach((event) => {
    const when = engine.secondsBetweenTicks?.(0, event.start_tick) ?? 0;
    try {
      // Records the ahead tick. Does not attack a synth.
      engine.scheduleContinuationAt(() => {}, when);
    } catch {
      // A schedule throw leaves the playback loop in place.
    }
  });
}

const AdaptiveContinuationPanel = () => {
  const playback = useMusicStore((state) => state.adaptivePlayback);
  const continuation = useMusicStore((state) => state.adaptiveContinuation);
  const continuationError = useMusicStore((state) => state.adaptiveContinuationError);
  const fillAdaptiveContinuation = useMusicStore((state) => state.fillAdaptiveContinuation);
  const maintainAdaptiveContinuation = useMusicStore((state) => state.maintainAdaptiveContinuation);
  const playing = playback?.transport === 'playing';
  const continuationId = continuation?.continuation_id || '';
  const buffer = useMusicStore((state) => state.adaptiveContinuationBuffer);

  useEffect(() => {
    if (!playing || !continuationId) {
      return undefined;
    }
    const timer = setInterval(() => {
      maintainAdaptiveContinuation();
    }, 1000);
    return () => clearInterval(timer);
  }, [playing, continuationId, maintainAdaptiveContinuation]);

  useEffect(() => {
    if (!continuation?.audible) {
      getLivePlaybackEngine()?.clearContinuationScheduled?.();
      return;
    }
    placeAheadNotes(continuation, buffer, playback?.position_tick ?? 0);
  }, [continuation, buffer, playback?.position_tick]);

  const target = continuation?.target_start_bar == null
    ? 'none'
    : `${continuation.target_start_bar}–${continuation.target_end_bar}`;
  const warnings = (continuation?.warnings || []).map((item) => item.code).join(', ');

  return (
    <Block data-testid="adaptive-continuation">
      <strong>Continuation</strong>
      <Row>
        <Button type="button" data-testid="adaptive-continuation-fill" onClick={() => fillAdaptiveContinuation()}>
          Fill ahead
        </Button>
        <span data-testid="adaptive-continuation-anchor">
          Anchor {continuation?.anchor_bar ?? '—'}
        </span>
        <span data-testid="adaptive-continuation-target">Target {target}</span>
        <span data-testid="adaptive-continuation-fallback">{continuation?.fallback_kind || 'none'}</span>
        <span data-testid="adaptive-continuation-source">{continuation?.source || 'none'}</span>
        <span data-testid="adaptive-continuation-status">{continuation?.job_status || 'idle'}</span>
        <span data-testid="adaptive-continuation-audible">{continuation?.audible ? 'audible' : 'quiet'}</span>
      </Row>
      {warnings ? <span data-testid="adaptive-continuation-warnings">{warnings}</span> : null}
      {continuationError ? <p data-testid="adaptive-continuation-error">{continuationError}</p> : null}
    </Block>
  );
};

export default AdaptiveContinuationPanel;
