import React, { useEffect, useId, useMemo, useRef, useState, lazy, Suspense } from 'react';
import styled from 'styled-components';
import NotationViewer from './NotationViewer.jsx';
import PlaybackControls from './PlaybackControls.jsx';
import MidiInputPanel from './MidiInputPanel.jsx';
import CoPerformancePanel from './CoPerformancePanel.jsx';
import AudioInputPanel from './AudioInputPanel.jsx';
import AudioRecoveryPanel from './AudioRecoveryPanel.jsx';
import PromptJsonEditor from './PromptJsonEditor.jsx';
import ExportControls from './ExportControls.jsx';
import NeuralAudioRenderPanel from './NeuralAudioRenderPanel.jsx';
import PianoRollEditor from './PianoRollEditor.jsx';
import AiRegionEditPanel from './AiRegionEditPanel.jsx';
import CompositionAnalysisPanel from './CompositionAnalysisPanel.jsx';
import AdaptiveScorePanel from './AdaptiveScorePanel.jsx';
import PerformancePanel from './PerformancePanel.jsx';
import SpatialScenePanel from './SpatialScenePanel.jsx';
import ArdourCompanionPanel from './ArdourCompanionPanel.jsx';
import VideoScoringPanel from './VideoScoringPanel.jsx';
import HarmonyTimelinePanel from './HarmonyTimelinePanel.jsx';
import MotifPanel from './MotifPanel.jsx';
import MusicalUniversePanel from './MusicalUniversePanel.jsx';
import CompositionDevelopmentPanel from './CompositionDevelopmentPanel.jsx';
import ArrangementPanel from './ArrangementPanel.jsx';
import MultiAgentPanel from './MultiAgentPanel.jsx';
import ProjectVersionsPanel from './ProjectVersionsPanel.jsx';
import CollaborationPanel from './CollaborationPanel.jsx';
import ComposerProfilesPanel from './ComposerProfilesPanel.jsx';
import PersonalComposerPanel from './PersonalComposerPanel.jsx';
import ModelLabPanel from './ModelLabPanel.jsx';
import PreferenceLearningPanel from './PreferenceLearningPanel.jsx';
import GeneratePanel from './GeneratePanel.jsx';
import { useMusicStore } from '../store/musicStore.js';

const PluginsPanel = lazy(() => import('./PluginsPanel.jsx'));

const Workspace = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 16px;
  min-width: 0;
  max-width: 100%;
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

const TransportPanelBody = styled.div`
  overflow: auto;
  min-width: 0;
`;

const TABS = [
  { id: 'generate', label: 'Generate' },
  { id: 'transport', label: 'Transport & tracks' },
  { id: 'piano', label: 'Piano roll' },
  { id: 'notation', label: 'Notation' },
  { id: 'versions', label: 'Versions' },
  { id: 'develop', label: 'Develop' },
  { id: 'arrange', label: 'Arrange' },
  { id: 'agents', label: 'Agents' },
  { id: 'motifs', label: 'Motifs' },
  { id: 'universe', label: 'Universe' },
  { id: 'harmony', label: 'Harmony' },
  { id: 'profiles', label: 'Profiles' },
  { id: 'lab', label: 'Lab' },
  { id: 'plugins', label: 'Plugins' },
  { id: 'advanced', label: 'Advanced JSON' },
  { id: 'analysis', label: 'Analysis' },
  { id: 'adaptive', label: 'Adaptive' },
  { id: 'performance', label: 'Performance' },
  { id: 'spatial', label: 'Spatial' },
  { id: 'ardour', label: 'Ardour' },
  { id: 'picture', label: 'Picture' },
  { id: 'export', label: 'Export' },
];

const VERSIONS_TAB_INDEX = TABS.findIndex((tab) => tab.id === 'versions');

/**
 * Composer shell: Generate first, then Transport & tracks, then piano roll
 * and the remaining studio tabs. GeneratePanel and PlaybackControls stay
 * mounted while hidden so form state and Tone survive tab switches.
 */
const ComposerWorkspace = () => {
  const [activeTab, setActiveTab] = useState('generate');
  const setAnalysisTabVisible = useMusicStore((state) => state.setAnalysisTabVisible);
  const composerTabRequest = useMusicStore((state) => state.composerTabRequest);
  const composerTabRequestSeq = useMusicStore((state) => state.composerTabRequestSeq);
  const collaborationEnabled = useMusicStore((state) => state.collaborationEnabled);
  const loadCollaborationStatus = useMusicStore((state) => state.loadCollaborationStatus);
  const tabs = useMemo(
    () => (collaborationEnabled
      ? [
        ...TABS.slice(0, VERSIONS_TAB_INDEX + 1),
        { id: 'collaborate', label: 'Collaborate' },
        ...TABS.slice(VERSIONS_TAB_INDEX + 1),
      ]
      : TABS),
    [collaborationEnabled],
  );
  const tablistRef = useRef(null);
  const reactId = useId();
  const tabId = (id) => `composer-tab-${id}-${reactId}`;
  const panelId = (id) => `composer-panel-${id}-${reactId}`;

  useEffect(() => {
    loadCollaborationStatus();
  }, [loadCollaborationStatus]);

  useEffect(() => {
    if (!composerTabRequest) {
      return;
    }
    if (tabs.some((tab) => tab.id === composerTabRequest)) {
      setActiveTab(composerTabRequest);
    }
  }, [composerTabRequest, composerTabRequestSeq, tabs]);

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
      tabCount: tabs.length,
    });
    setActiveTab(tabIdValue);
  };

  const focusTabByIndex = (index) => {
    const next = ((index % tabs.length) + tabs.length) % tabs.length;
    const nextId = tabs[next].id;
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
        focusTabByIndex(tabs.length - 1);
        break;
      default:
        break;
    }
  };

  return (
    <Workspace>
      <TabRow
        ref={tablistRef}
        role="tablist"
        aria-label="Composer views"
      >
        {tabs.map((tab, index) => {
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

      {tabs.map((tab) => {
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
            {/* Keep generate + transport mounted while hidden so form / Tone survive tab switches. */}
            {tab.id === 'generate' ? <GeneratePanel /> : null}
            {tab.id === 'transport' ? (
              <TransportPanelBody>
                <SectionTitle>Transport & tracks</SectionTitle>
                <PlaybackControls />
                <MidiInputPanel />
                <CoPerformancePanel />
                <AudioInputPanel />
                <AudioRecoveryPanel />
              </TransportPanelBody>
            ) : null}
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
            {tab.id === 'collaborate' && selected ? <CollaborationPanel /> : null}
            {tab.id === 'analysis' && selected ? <CompositionAnalysisPanel /> : null}
            {tab.id === 'adaptive' && selected ? <AdaptiveScorePanel /> : null}
            {tab.id === 'performance' && selected ? <PerformancePanel /> : null}
            {tab.id === 'spatial' && selected ? <SpatialScenePanel /> : null}
            {tab.id === 'ardour' && selected ? <ArdourCompanionPanel /> : null}
            {tab.id === 'picture' && selected ? <VideoScoringPanel /> : null}
            {tab.id === 'export' && selected ? (
              <>
                <SectionTitle>Export</SectionTitle>
                <ExportControls />
                <NeuralAudioRenderPanel />
              </>
            ) : null}
            {tab.id === 'develop' && selected ? <CompositionDevelopmentPanel /> : null}
            {tab.id === 'arrange' && selected ? <ArrangementPanel /> : null}
            {tab.id === 'agents' && selected ? <MultiAgentPanel /> : null}
            {tab.id === 'motifs' && selected ? (
              <MotifPanel onOpenPianoTab={() => onTabChange('piano')} />
            ) : null}
            {tab.id === 'universe' && selected ? <MusicalUniversePanel /> : null}
            {tab.id === 'harmony' && selected ? <HarmonyTimelinePanel /> : null}
            {tab.id === 'profiles' && selected ? (
              <>
                <ComposerProfilesPanel />
                <PersonalComposerPanel />
                <PreferenceLearningPanel />
              </>
            ) : null}
            {tab.id === 'lab' && selected ? <ModelLabPanel /> : null}
            {tab.id === 'plugins' && selected ? (
              <Suspense fallback={<p>Loading plugins…</p>}>
                <PluginsPanel />
              </Suspense>
            ) : null}
          </Panel>
        );
      })}

      <AiRegionEditPanel />
    </Workspace>
  );
};

export default ComposerWorkspace;
