import React, { useState, useEffect } from 'react';
import styled from 'styled-components';
import axios from 'axios';
import MusicGenerator from './components/MusicGenerator.jsx';
import TrainingDataUploader from './components/TrainingDataUploader.jsx';
import Header from './components/Header.jsx';

const AppContainer = styled.div`
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
`;

const MainContent = styled.div`
  max-width: 1200px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 30px;
  
  @media (max-width: 768px) {
    grid-template-columns: 1fr;
    gap: 20px;
  }
`;

const Card = styled.div`
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  border-radius: 20px;
  padding: 30px;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.2);
`;

const StatusCard = styled(Card)`
  grid-column: 1 / -1;
  text-align: center;
  margin-bottom: 20px;
`;

const StatusIndicator = styled.div`
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  border-radius: 25px;
  background: ${props => props.status === 'healthy' ? '#4ade80' : '#f87171'};
  color: white;
  font-weight: 500;
  margin-top: 10px;
`;

const StatusDot = styled.div`
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: white;
  animation: ${props => props.status === 'healthy' ? 'pulse' : 'none'} 2s infinite;
  
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
`;

function App() {
  const [apiStatus, setApiStatus] = useState('checking');
  const [modelLoaded, setModelLoaded] = useState(false);

  useEffect(() => {
    checkApiStatus();
  }, []);

  const checkApiStatus = async () => {
    try {
      const response = await axios.get('/health');
      setApiStatus('healthy');
      setModelLoaded(response.data.model_loaded);
    } catch (error) {
      setApiStatus('error');
      console.error('API health check failed:', error);
    }
  };

  const handleModelTrained = () => {
    setModelLoaded(true);
    checkApiStatus();
  };

  return (
    <AppContainer>
      <Header />
      
      <StatusCard>
        <h2>System Status</h2>
        <StatusIndicator status={apiStatus}>
          <StatusDot status={apiStatus} />
          {apiStatus === 'healthy' ? 'API Connected' : 'API Disconnected'}
        </StatusIndicator>
        {apiStatus === 'healthy' && (
          <p style={{ marginTop: '10px', color: '#666' }}>
            Model Status: {modelLoaded ? 'Loaded and Ready' : 'Not Loaded - Please train the model'}
          </p>
        )}
      </StatusCard>

      <MainContent>
        <Card>
          <TrainingDataUploader onModelTrained={handleModelTrained} />
        </Card>
        
        <Card>
          <MusicGenerator modelLoaded={modelLoaded} />
        </Card>
      </MainContent>
    </AppContainer>
  );
}

export default App;
