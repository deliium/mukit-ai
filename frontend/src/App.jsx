import React, { useCallback, useEffect } from 'react';
import styled from 'styled-components';
import MusicGenerator from './components/MusicGenerator.jsx';
import ProjectBrowser from './components/ProjectBrowser.jsx';
import WorkspaceChrome from './components/WorkspaceChrome.jsx';
import { getHealth, getLlmModels } from './api/musicApi.js';
import { useMusicStore } from './store/musicStore.js';

const AppContainer = styled.div`
  min-height: 100vh;
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(99, 102, 241, 0.35), transparent 60%),
    radial-gradient(900px 500px at 100% 0%, rgba(14, 165, 233, 0.25), transparent 55%),
    linear-gradient(160deg, #0f172a 0%, #1e293b 45%, #312e81 100%);
  padding: 12px 16px 24px;
`;

const MainContent = styled.div`
  max-width: 1440px;
  margin: 0 auto;
`;

const Card = styled.div`
  background: rgba(255, 255, 255, 0.96);
  border-radius: 14px;
  padding: 16px 18px 20px;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.28);
  border: 1px solid rgba(255, 255, 255, 0.35);
  min-height: calc(100vh - 88px);
`;

function App() {
  const activeView = useMusicStore((state) => state.activeView);
  const setApiStatus = useMusicStore((state) => state.setApiStatus);
  const setAvailableLlmModels = useMusicStore((state) => state.setAvailableLlmModels);
  const setUiError = useMusicStore((state) => state.setUiError);

  const checkApiStatus = useCallback(async () => {
    try {
      await getHealth();
      setApiStatus('healthy');

      const modelResponse = await getLlmModels();
      setAvailableLlmModels(modelResponse.models, {
        defaultProvider: modelResponse.default_provider,
        defaultModel: modelResponse.default_model,
      });
      console.debug('[App] Startup health/model discovery completed', {
        modelCount: modelResponse.models?.length || 0,
        llmReady: Boolean(modelResponse.models?.length),
      });
    } catch (error) {
      setApiStatus('error');
      setUiError(error.message);
      console.error('[App] Health/model discovery failed', { message: error.message });
    }
  }, [setApiStatus, setAvailableLlmModels, setUiError]);

  useEffect(() => {
    console.debug('[App] Startup health/model discovery started');
    checkApiStatus();
  }, [checkApiStatus]);

  useEffect(() => {
    console.debug('[App] Active view changed', { activeView });
  }, [activeView]);

  return (
    <AppContainer>
      <MainContent>
        <WorkspaceChrome />
        <Card>
          {activeView === 'home' ? <ProjectBrowser /> : <MusicGenerator />}
        </Card>
      </MainContent>
    </AppContainer>
  );
}

export default App;
