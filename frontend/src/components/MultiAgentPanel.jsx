import React from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition } from '../utils/musicJsonValidation.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
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
`;

const Button = styled.button`
  border: 1px solid #c7d2fe;
  background: ${(p) => (p.$primary ? '#4338ca' : '#fff')};
  color: ${(p) => (p.$primary ? '#fff' : '#312e81')};
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 0.9rem;
  cursor: pointer;

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
`;

const Meta = styled.pre`
  margin: 0;
  padding: 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 0.75rem;
  overflow: auto;
  max-height: 180px;
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

/**
 * Thin V4 multi-agent panel: preview spine → Apply via multi-agent-apply CAS.
 */
const MultiAgentPanel = () => {
  const composition = useMusicStore((s) => s.editedMusicJson || s.generatedMusicJson);
  const status = useMusicStore((s) => s.multiAgentStatus);
  const error = useMusicStore((s) => s.multiAgentError);
  const candidate = useMusicStore((s) => s.multiAgentCandidate);
  const preview = useMusicStore((s) => s.previewMultiAgentWorkflow);
  const apply = useMusicStore((s) => s.applyMultiAgentCandidate);
  const discard = useMusicStore((s) => s.discardMultiAgentCandidate);

  const canPreview = isCanonicalComposition(composition);
  const loading = status === 'loading';

  return (
    <Panel data-testid="multi-agent-panel">
      <Title>Multi-agent (V4)</Title>
      <Hint>
        Runs Creative Director → Harmony → Melody → Arrangement → Critic as a session
        preview. Critic approve does not save; Apply uses multi-agent-apply via the
        revision CAS path. Competing arrange/develop/reharm candidates are discarded
        when a multi-agent candidate is set.
      </Hint>
      <Row>
        <Button
          type="button"
          $primary
          disabled={!canPreview || loading}
          onClick={() => {
            void preview();
          }}
        >
          {loading ? 'Running spine…' : 'Preview spine'}
        </Button>
        <Button
          type="button"
          disabled={!candidate || loading}
          onClick={() => {
            void apply();
          }}
        >
          Apply candidate
        </Button>
        <Button
          type="button"
          disabled={!candidate || loading}
          onClick={() => discard()}
        >
          Discard
        </Button>
      </Row>
      {error ? <ErrorText>{error}</ErrorText> : null}
      {candidate ? (
        <Meta>
          {JSON.stringify(
            {
              operation_type: candidate.operation_type,
              recommendation: candidate.recommendation,
              agent_sequence: candidate.agent_sequence,
              source_fingerprint: String(candidate.source_fingerprint || '').slice(0, 16),
              candidate_fingerprint: String(candidate.candidate_fingerprint || '').slice(0, 16),
              stage_agent_ids: Array.isArray(candidate.stages)
                ? candidate.stages.map((s) => s.agent_id).filter(Boolean)
                : [],
            },
            null,
            2,
          )}
        </Meta>
      ) : null}
    </Panel>
  );
};

export default MultiAgentPanel;
