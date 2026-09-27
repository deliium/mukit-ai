import React, { useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import { downloadBlob } from '../utils/downloadFile.js';
import { buildAiCandidateEnvelope } from '../utils/compositionCandidateLifecycle.js';
import {
  assertPacketHasNoMetrics,
  judgmentFromWinner,
  packetLabels,
} from '../utils/workflowListening.js';

const logger = createAppLogger('workflowListening');

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
`;

const Title = styled.h4`
  margin: 0;
  color: #312e81;
  font-size: 1rem;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #64748b;
  line-height: 1.4;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

export default function WorkflowListeningPanel() {
  const [packet, setPacket] = useState(null);
  const [winner, setWinner] = useState('');
  const [comment, setComment] = useState('');
  const [error, setError] = useState('');
  const setGenerationAuditionActive = useMusicStore((state) => state.setGenerationAuditionActive);

  const labels = packet ? packetLabels(packet) : [];

  function onFile(event) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    const reader = new window.FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || ''));
        assertPacketHasNoMetrics(parsed);
        const nextLabels = packetLabels(parsed);
        logger.info('packet loaded', { label_count: nextLabels.length });
        setPacket(parsed);
        setWinner('');
        setError('');
      } catch (err) {
        logger.warn('packet rejected', { error_type: err?.name || 'Error' });
        setPacket(null);
        setError('This file is not a listening packet.');
      }
    };
    reader.readAsText(file);
  }

  function play(label) {
    const clip = packet?.clips?.find((item) => item.label === label);
    if (!clip?.composition) {
      return;
    }
    const candidate = buildAiCandidateEnvelope({
      candidateId: `workflow-listening-${label}`,
      operationType: 'listen',
      composition: clip.composition,
      sourceFingerprint: 'workflow-listening',
      candidateFingerprint: `workflow-listening-${label}`,
    });
    useMusicStore.setState({
      generationCandidate: candidate,
      generationAuditionActive: false,
    });
    setGenerationAuditionActive(true);
    logger.info('label audition', { label });
  }

  function downloadJudgment() {
    if (!packet || !winner) {
      return;
    }
    const judgment = judgmentFromWinner(packet, winner, comment);
    logger.info('judgment download', { winner });
    const blob = new Blob([JSON.stringify(judgment, null, 2)], { type: 'application/json' });
    downloadBlob(blob, 'workflow-listening-judgment.json');
  }

  return (
    <Panel data-testid="workflow-listening-panel">
      <Title>Listening</Title>
      <Hint>
        Load a blinded packet and audition A/B or A/B/C on the piano roll.
        The open project score stays in place.
      </Hint>
      <input
        type="file"
        accept="application/json,.json"
        onChange={onFile}
        aria-label="Listening packet file"
      />
      {error ? <Hint>{error}</Hint> : null}
      {labels.length > 0 ? (
        <Row>
          {labels.map((label) => (
            <label key={label}>
              <input
                type="radio"
                name="workflow-listening-label"
                checked={winner === label}
                onChange={() => setWinner(label)}
              />
              {` ${label}`}
              <button type="button" onClick={() => play(label)}>
                Play
              </button>
            </label>
          ))}
        </Row>
      ) : null}
      {packet ? (
        <Row>
          <input
            type="text"
            maxLength={240}
            value={comment}
            placeholder="Short comment"
            aria-label="Listening comment"
            onChange={(event) => setComment(event.target.value)}
          />
          <button type="button" onClick={downloadJudgment} disabled={!winner}>
            Download judgment
          </button>
        </Row>
      ) : null}
    </Panel>
  );
}
