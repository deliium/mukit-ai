import React, { useEffect, useId, useRef, useState } from 'react';
import styled from 'styled-components';
import NotationViewer from './NotationViewer.jsx';
import PlaybackControls from './PlaybackControls.jsx';
import PromptJsonEditor from './PromptJsonEditor.jsx';
import ExportControls from './ExportControls.jsx';
import PianoRollEditor from './PianoRollEditor.jsx';
import AiRegionEditPanel from './AiRegionEditPanel.jsx';
import CompositionAnalysisPanel from './CompositionAnalysisPanel.jsx';
import HarmonyTimelinePanel from './HarmonyTimelinePanel.jsx';
import MotifPanel from './MotifPanel.jsx';
import CompositionDevelopmentPanel from './CompositionDevelopmentPanel.jsx';
import ArrangementPanel from './ArrangementPanel.jsx';
import ProjectVersionsPanel from './ProjectVersionsPanel.jsx';
import { useMusicStore } from '../store/musicStore.js';

const Workspace = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
  min-width: 0;
  max-width: 100%;
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
  max-height: min(48vh, 520px);
  overflow: auto;
  min-width: 0;
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
  min-height: 44px;
  padding: 8px 14px;
  border: 1px solid ${(props) => (props.$active ? '#4f46e5' : '#cbd5e1')};
  border-radius: 8px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#fff')};
  color: ${(props) => (props.$active ? '#312e81' : '#334155')};
  font-weight: 600;
  cursor: pointer;
  touch-action: manipulation;
`;

const Panel = styled.section`
  padding: 14px;
  background: #f8f9ff;
  border: 1px solid #e0e7ff;
  border-radius: 12px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;
`;

const SideActions = styled.div`
  display: grid;
  gap: 12px;
  min-width: 0;

  @media (min-width: 1100px) {
    grid-template-columns: 1fr 1fr;
  }
`;

const TABS = [
  { id: 'piano', label: 'Piano roll' },
  { id: 'notation', label: 'Notation' },
  { id: 'versions', label: 'Versions' },
  { id: 'develop', label: 'Develop' },
  { id: 'arrange', label: 'Arrange' },
  { id: 'motifs', label: 'Motifs' },
  { id: 'harmony', label: 'Harmony' },
  { id: 'advanced', label: 'Advanced JSON' },
  { id: 'analysis', label: 'Analysis' },
];

/**
 * Laptop-oriented composer shell: sticky transport, primary piano roll,
 * tabbed notation / advanced JSON / analysis, AI edit + export alongside.
 */
const ComposerWorkspace = () => {
  const [activeTab, setActiveTab] = useState('piano');
  const setAnalysisTabVisible = useMusicStore((state) => state.setAnalysisTabVisible);
  const composerTabRequest = useMusicStore((state) => state.composerTabRequest);
  const composerTabRequestSeq = useMusicStore((state) => state.composerTabRequestSeq);
  const tablistRef = useRef(null);
  const reactId = useId();
  const tabId = (id) => `composer-tab-${id}-${reactId}`;
  const panelId = (id) => `composer-panel-${id}-${reactId}`;

  useEffect(() => {
    if (!composerTabRequest) {
      return;
    }
    if (TABS.some((tab) => tab.id === composerTabRequest)) {
      setActiveTab(composerTabRequest);
    }
  }, [composerTabRequest, composerTabRequestSeq]);

  useEffect(() => {
    const visible = activeTab === 'analysis';
    console.debug('[ComposerWorkspace] Analysis tab visibility sync', {
      activeTab,
      visible,
    });
    setAnalysisTabVisible(visible);
    return () => {
      if (visible) {
        setAnalysisTabVisible(false);
      }
    };
  }, [activeTab, setAnalysisTabVisible]);

  const onTabChange = (tabIdValue) => {
    console.debug('[ComposerWorkspace] View tab changed', {
      activeTab: tabIdValue,
      tabCount: TABS.length,
    });
    setActiveTab(tabIdValue);
  };

  const focusTabByIndex = (index) => {
    const next = ((index % TABS.length) + TABS.length) % TABS.length;
    const nextId = TABS[next].id;
    onTabChange(nextId);
    const button = tablistRef.current?.querySelector(`[data-tab-id="${nextId}"]`);
    if (button && typeof button.focus === 'function') {
      button.focus();
    }
  };

  const onTabKeyDown = (event, tabIndex) => {
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        event.preventDefault();
        focusTabByIndex(tabIndex + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        event.preventDefault();
        focusTabByIndex(tabIndex - 1);
        break;
      case 'Home':
        event.preventDefault();
        focusTabByIndex(0);
        break;
      case 'End':
        event.preventDefault();
        focusTabByIndex(TABS.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <Workspace>
      <StickyTransport>
        <SectionTitle>Transport & tracks</SectionTitle>
        <PlaybackControls />
      </StickyTransport>

      <TabRow
        ref={tablistRef}
        role="tablist"
        aria-label="Composer views"
      >
        {TABS.map((tab, index) => {
          const selected = activeTab === tab.id;
          return (
            <TabButton
              key={tab.id}
              type="button"
              role="tab"
              id={tabId(tab.id)}
              data-tab-id={tab.id}
              data-testid={`composer-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={panelId(tab.id)}
              tabIndex={selected ? 0 : -1}
              $active={selected}
              onClick={() => onTabChange(tab.id)}
              onKeyDown={(event) => onTabKeyDown(event, index)}
            >
              {tab.label}
            </TabButton>
          );
        })}
      </TabRow>

      {TABS.map((tab) => {
        const selected = activeTab === tab.id;
        return (
          <Panel
            key={tab.id}
            role="tabpanel"
            id={panelId(tab.id)}
            aria-labelledby={tabId(tab.id)}
            hidden={!selected}
            data-testid={`composer-panel-${tab.id}`}
          >
            {tab.id === 'piano' && selected ? <PianoRollEditor /> : null}
            {tab.id === 'notation' && selected ? <NotationViewer /> : null}
            {tab.id === 'advanced' && selected ? (
              <>
                <SectionTitle>Advanced — canonical JSON</SectionTitle>
                <p style={{ margin: '0 0 12px', color: '#64748b', fontSize: '0.9rem' }}>
                  Edit canonical composition JSON directly. Harmony is metadata only; playable notes live in tracks[].events[].
                </p>
                <PromptJsonEditor />
              </>
            ) : null}
            {tab.id === 'versions' && selected ? <ProjectVersionsPanel /> : null}
            {tab.id === 'analysis' && selected ? <CompositionAnalysisPanel /> : null}
            {tab.id === 'develop' && selected ? <CompositionDevelopmentPanel /> : null}
            {tab.id === 'arrange' && selected ? <ArrangementPanel /> : null}
            {tab.id === 'motifs' && selected ? (
              <MotifPanel onOpenPianoTab={() => onTabChange('piano')} />
            ) : null}
            {tab.id === 'harmony' && selected ? <HarmonyTimelinePanel /> : null}
          </Panel>
        );
      })}

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
