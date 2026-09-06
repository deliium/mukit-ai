import React, { useState } from 'react';
import styled from 'styled-components';
import NotationViewer from './NotationViewer.jsx';
import PlaybackControls from './PlaybackControls.jsx';
import PromptJsonEditor from './PromptJsonEditor.jsx';
import ExportControls from './ExportControls.jsx';
import PianoRollEditor from './PianoRollEditor.jsx';
import AiRegionEditPanel from './AiRegionEditPanel.jsx';

const Workspace = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
`;

const StickyTransport = styled.div`
  position: sticky;
  top: 0;
  z-index: 20;
  padding: 12px;
  background: rgba(248, 250, 252, 0.96);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  backdrop-filter: blur(6px);
`;

const SectionTitle = styled.h3`
  margin: 0 0 8px;
  font-size: 0.95rem;
  font-weight: 600;
  color: #334155;
`;

const TabRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`;

const TabButton = styled.button`
  padding: 8px 14px;
  border: 1px solid ${(props) => (props.$active ? '#4f46e5' : '#cbd5e1')};
  border-radius: 8px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#fff')};
  color: ${(props) => (props.$active ? '#312e81' : '#334155')};
  font-weight: 600;
  cursor: pointer;
`;

const Panel = styled.section`
  padding: 14px;
  background: #f8f9ff;
  border: 1px solid #e0e7ff;
  border-radius: 12px;
`;

const SideActions = styled.div`
  display: grid;
  gap: 12px;

  @media (min-width: 1100px) {
    grid-template-columns: 1fr 1fr;
  }
`;

const TABS = [
  { id: 'piano', label: 'Piano roll' },
  { id: 'notation', label: 'Notation' },
  { id: 'advanced', label: 'Advanced JSON' },
];

/**
 * Laptop-oriented composer shell: sticky transport, primary piano roll,
 * tabbed notation / advanced JSON, AI edit + export alongside.
 */
const ComposerWorkspace = () => {
  const [activeTab, setActiveTab] = useState('piano');

  const onTabChange = (tabId) => {
    console.debug('[ComposerWorkspace] View tab changed', { activeTab: tabId });
    setActiveTab(tabId);
  };

  return (
    <Workspace>
      <StickyTransport>
        <SectionTitle>Transport & tracks</SectionTitle>
        <PlaybackControls />
      </StickyTransport>

      <TabRow role="tablist" aria-label="Composer views">
        {TABS.map((tab) => (
          <TabButton
            key={tab.id}
            type="button"
            role="tab"
            data-testid={`composer-tab-${tab.id}`}
            aria-selected={activeTab === tab.id}
            $active={activeTab === tab.id}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </TabButton>
        ))}
      </TabRow>

      <Panel role="tabpanel">
        {activeTab === 'piano' && <PianoRollEditor />}
        {activeTab === 'notation' && <NotationViewer />}
        {activeTab === 'advanced' && (
          <>
            <SectionTitle>Advanced — canonical JSON</SectionTitle>
            <p style={{ margin: '0 0 12px', color: '#64748b', fontSize: '0.9rem' }}>
              Edit composition.v1 JSON directly. Harmony is metadata only; playable notes live in tracks[].events[].
            </p>
            <PromptJsonEditor />
          </>
        )}
      </Panel>

      <SideActions>
        <AiRegionEditPanel />
        <div>
          <SectionTitle>Export</SectionTitle>
          <ExportControls />
        </div>
      </SideActions>
    </Workspace>
  );
};

export default ComposerWorkspace;
