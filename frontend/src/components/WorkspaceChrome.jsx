import React from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const Bar = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 14px;
  background: rgba(15, 23, 42, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 12px;
  color: #f8fafc;
`;

const Brand = styled.div`
  display: flex;
  flex-direction: column;
  gap: 2px;

  strong {
    font-size: 1.15rem;
    letter-spacing: 0.02em;
  }

  span {
    font-size: 0.8rem;
    opacity: 0.85;
  }
`;

const StatusGroup = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Chip = styled.span`
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  background: ${(props) => props.$bg};
  color: ${(props) => props.$color};
`;

const Dot = styled.span`
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
`;

/**
 * Compact workspace chrome: brand + API/LLM readiness (not a marketing hero).
 */
const WorkspaceChrome = () => {
  const apiStatus = useMusicStore((state) => state.apiStatus);
  const llmModelsLoaded = useMusicStore((state) => state.llmModelsLoaded);
  const availableLlmModels = useMusicStore((state) => state.availableLlmModels);
  const activeView = useMusicStore((state) => state.activeView);

  const apiHealthy = apiStatus === 'healthy';
  const llmReady = llmModelsLoaded && availableLlmModels.length > 0;

  return (
    <Bar>
      <Brand>
        <strong>Mukit AI Composer</strong>
        <span>{activeView === 'home' ? 'Projects' : 'Workspace'}</span>
      </Brand>
      <StatusGroup>
        <Chip
          $bg={apiHealthy ? 'rgba(74, 222, 128, 0.2)' : 'rgba(248, 113, 113, 0.25)'}
          $color={apiHealthy ? '#bbf7d0' : '#fecaca'}
          title="Backend liveness (/health)"
        >
          <Dot />
          {apiHealthy ? 'API healthy' : apiStatus === 'checking' ? 'API checking…' : 'API down'}
        </Chip>
        <Chip
          $bg={llmReady ? 'rgba(129, 140, 248, 0.25)' : 'rgba(251, 191, 36, 0.22)'}
          $color={llmReady ? '#c7d2fe' : '#fde68a'}
          title="LLM providers configured on backend"
        >
          <Dot />
          {llmReady
            ? `LLM ready (${availableLlmModels.length})`
            : llmModelsLoaded
              ? 'LLM not configured'
              : 'LLM checking…'}
        </Chip>
      </StatusGroup>
    </Bar>
  );
};

export default WorkspaceChrome;
