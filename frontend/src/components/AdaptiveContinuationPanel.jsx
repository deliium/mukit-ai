import React, { useEffect } from 'react';
import styled from 'styled-components';

import { useMusicStore } from '../store/musicStore.js';
import {
  continuationEventsToSchedule,
  continuousDisabledReason,
  summarizeMusicState,
} from '../utils/adaptiveContinuation.js';
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

const Honesty = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #475569;
`;

const Label = styled.label`
  display: inline-flex;
  gap: 6px;
  align-items: center;
  cursor: pointer;
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
  const continuousWanted = useMusicStore((state) => state.adaptiveContinuousEnabled);
  const setAdaptiveContinuousEnabled = useMusicStore((state) => state.setAdaptiveContinuousEnabled);
  const fillAdaptiveContinuation = useMusicStore((state) => state.fillAdaptiveContinuation);
  const maintainAdaptiveContinuation = useMusicStore((state) => state.maintainAdaptiveContinuation);
  const playing = playback?.transport === 'playing';
  const continuationId = continuation?.continuation_id || '';
  const buffer = useMusicStore((state) => state.adaptiveContinuationBuffer);
  const musicSummary = summarizeMusicState(continuation?.music_state);
  const disabledReason = continuousDisabledReason(continuationError);

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
        <Label data-testid="adaptive-continuous-toggle">
          <input
            type="checkbox"
            checked={continuousWanted === true}
            onChange={(event) => setAdaptiveContinuousEnabled(event.target.checked)}
          />
          Continuous
        </Label>
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
        <span data-testid="adaptive-continuation-continuous">
          {continuation?.continuous ? 'continuous' : 'clipped'}
        </span>
      </Row>
      {musicSummary ? (
        <Row data-testid="adaptive-music-state">
          <span data-testid="adaptive-music-state-virtual-bar">
            Virtual bar {musicSummary.virtualBar}
          </span>
          <span data-testid="adaptive-music-state-themes">
            Themes {musicSummary.themeCount}
          </span>
          <span data-testid="adaptive-music-state-trajectory">
            Trajectory {musicSummary.trajectoryTail || '—'}
          </span>
          <span data-testid="adaptive-music-state-guards">
            Guards {musicSummary.guardFlags.length ? musicSummary.guardFlags.join(', ') : 'none'}
          </span>
        </Row>
      ) : null}
      <Honesty data-testid="adaptive-continuous-honesty">
        Session buffer is not the durable score. Fallback may keep looping while a model is discarded.
      </Honesty>
      {disabledReason ? (
        <span data-testid="adaptive-continuous-disabled">{disabledReason}</span>
      ) : null}
      {warnings ? <span data-testid="adaptive-continuation-warnings">{warnings}</span> : null}
      {continuationError ? <p data-testid="adaptive-continuation-error">{continuationError}</p> : null}
    </Block>
  );
};

export default AdaptiveContinuationPanel;
