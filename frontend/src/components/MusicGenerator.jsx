import React from 'react';
import styled from 'styled-components';
import { generateLlmMusicJson } from '../api/musicApi.js';
import NotationViewer from './NotationViewer.jsx';
import PlaybackControls from './PlaybackControls.jsx';
import PromptJsonEditor from './PromptJsonEditor.jsx';
import { useMusicStore } from '../store/musicStore.js';

const Container = styled.div`
  h2 {
    color: #333;
    margin-bottom: 20px;
    font-size: 1.5rem;
    font-weight: 600;
  }
`;

const FormGroup = styled.div`
  margin-bottom: 20px;
`;

const Label = styled.label`
  display: block;
  margin-bottom: 8px;
  font-weight: 500;
  color: #374151;
`;

const Input = styled.input`
  width: 100%;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 1rem;
  transition: border-color 0.3s ease;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const Select = styled.select`
  width: 100%;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 1rem;
  background: white;
  transition: border-color 0.3s ease;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const TextArea = styled.textarea`
  width: 100%;
  min-height: 90px;
  padding: 12px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 1rem;
  font-family: inherit;
  resize: vertical;
  transition: border-color 0.3s ease;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const Button = styled.button`
  background: #667eea;
  color: white;
  border: none;
  padding: 12px 24px;
  border-radius: 8px;
  font-size: 1rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s ease;
  width: 100%;
  margin-top: 10px;

  &:hover {
    background: #5a67d8;
    transform: translateY(-1px);
  }

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
    transform: none;
  }
`;

const StatusMessage = styled.div`
  padding: 12px;
  border-radius: 8px;
  margin: 15px 0;
  font-size: 0.9rem;
  text-align: center;
  
  &.success {
    background: #d1fae5;
    color: #065f46;
    border: 1px solid #a7f3d0;
  }
  
  &.error {
    background: #fee2e2;
    color: #991b1b;
    border: 1px solid #fca5a5;
  }
  
  &.info {
    background: #dbeafe;
    color: #1e40af;
    border: 1px solid #93c5fd;
  }
`;

const GeneratedMusic = styled.div`
  margin-top: 20px;
  padding: 20px;
  background: #f8f9ff;
  border-radius: 12px;
  border: 1px solid #e0e7ff;
`;

const ParameterGrid = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
  
  @media (max-width: 480px) {
    grid-template-columns: 1fr;
  }
`;

const MusicGenerator = () => {
  const availableLlmModels = useMusicStore((state) => state.availableLlmModels);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const prompt = useMusicStore((state) => state.prompt);
  const generatedMusicJson = useMusicStore((state) => state.generatedMusicJson);
  const generationStatus = useMusicStore((state) => state.generationStatus);
  const uiError = useMusicStore((state) => state.uiError);
  const warnings = useMusicStore((state) => state.warnings);
  const setSelectedLlmModel = useMusicStore((state) => state.setSelectedLlmModel);
  const updatePrompt = useMusicStore((state) => state.updatePrompt);
  const startGeneration = useMusicStore((state) => state.startGeneration);
  const completeGeneration = useMusicStore((state) => state.completeGeneration);
  const failGeneration = useMusicStore((state) => state.failGeneration);

  const handleGenerateLlmJson = async () => {
    if (!availableLlmModels.length) {
      failGeneration('No LLM providers configured. Set OPENAI_API_KEY or DEEPSEEK_API_KEY on the backend.');
      return;
    }

    startGeneration();
    const requestData = buildLlmRequest(prompt, selectedProvider, selectedModel);
    console.debug('[MusicGenerator] LLM generation requested', {
      provider: selectedProvider,
      model: selectedModel,
      genre: prompt.genre,
      mood: prompt.mood,
    });

    try {
      const response = await generateLlmMusicJson(requestData);
      completeGeneration({
        music: response.music,
        musicxml: response.musicxml,
        warnings: response.warnings,
      });
    } catch (error) {
      failGeneration(error.message);
    }
  };

  return (
    <Container>
      <h2>LLM JSON Composer</h2>
        {!availableLlmModels.length && (
          <StatusMessage className="info">
            Configure OPENAI_API_KEY or DEEPSEEK_API_KEY on the backend to enable LLM generation.
          </StatusMessage>
        )}

        {availableLlmModels.length > 0 && (
          <FormGroup>
            <Label htmlFor="llmModel">Provider / Model</Label>
            <Select
              id="llmModel"
              value={`${selectedProvider}:${selectedModel}`}
              onChange={(event) => {
                const [provider, model] = event.target.value.split(':');
                setSelectedLlmModel(provider, model);
              }}
            >
              {availableLlmModels.map((model) => (
                <option key={`${model.provider}:${model.model}`} value={`${model.provider}:${model.model}`}>
                  {model.display_name || `${model.provider} (${model.model})`}
                </option>
              ))}
            </Select>
          </FormGroup>
        )}

        <ParameterGrid>
          <FormGroup>
            <Label htmlFor="genre">Genre</Label>
            <Input id="genre" value={prompt.genre} onChange={(event) => updatePrompt('genre', event.target.value)} />
          </FormGroup>
          <FormGroup>
            <Label htmlFor="mood">Mood</Label>
            <Input id="mood" value={prompt.mood} onChange={(event) => updatePrompt('mood', event.target.value)} />
          </FormGroup>
          <FormGroup>
            <Label htmlFor="key">Key</Label>
            <Input id="key" value={prompt.key} onChange={(event) => updatePrompt('key', event.target.value)} placeholder="C minor" />
          </FormGroup>
          <FormGroup>
            <Label htmlFor="timeSignature">Time Signature</Label>
            <Input id="timeSignature" value={prompt.time_signature} onChange={(event) => updatePrompt('time_signature', event.target.value)} />
          </FormGroup>
          <FormGroup>
            <Label htmlFor="tempoMin">Tempo Min</Label>
            <Input id="tempoMin" type="number" min="40" max="240" value={prompt.tempo_min} onChange={(event) => updatePrompt('tempo_min', event.target.value)} />
          </FormGroup>
          <FormGroup>
            <Label htmlFor="tempoMax">Tempo Max</Label>
            <Input id="tempoMax" type="number" min="40" max="240" value={prompt.tempo_max} onChange={(event) => updatePrompt('tempo_max', event.target.value)} />
          </FormGroup>
        </ParameterGrid>

        <FormGroup>
          <Label htmlFor="instruments">Instruments / Tracks</Label>
          <Input id="instruments" value={prompt.instruments} onChange={(event) => updatePrompt('instruments', event.target.value)} placeholder="piano,bass,strings" />
        </FormGroup>

        <FormGroup>
          <Label htmlFor="sections">Sections / Bars</Label>
          <Input id="sections" value={prompt.sections} onChange={(event) => updatePrompt('sections', event.target.value)} placeholder="intro:4,verse:8,chorus:8" />
        </FormGroup>

        <ParameterGrid>
          <FormGroup>
            <Label htmlFor="complexity">Complexity</Label>
            <Select id="complexity" value={prompt.complexity} onChange={(event) => updatePrompt('complexity', event.target.value)}>
              <option value="simple">Simple</option>
              <option value="moderate">Moderate</option>
              <option value="complex">Complex</option>
            </Select>
          </FormGroup>
          <FormGroup>
            <Label htmlFor="durationBars">Duration Bars</Label>
            <Input id="durationBars" type="number" min="1" max="512" value={prompt.duration_bars} onChange={(event) => updatePrompt('duration_bars', event.target.value)} />
          </FormGroup>
        </ParameterGrid>

        <FormGroup>
          <Label htmlFor="instructions">Freeform Instructions</Label>
          <TextArea id="instructions" value={prompt.instructions} onChange={(event) => updatePrompt('instructions', event.target.value)} placeholder="Add arrangement, texture, or reference notes" />
        </FormGroup>

        {uiError && <StatusMessage className="error">{uiError}</StatusMessage>}
        {warnings.map((warning) => (
          <StatusMessage key={warning} className="info">{warning}</StatusMessage>
        ))}

        <Button onClick={handleGenerateLlmJson} disabled={generationStatus === 'loading' || !availableLlmModels.length}>
          {generationStatus === 'loading' ? 'Generating JSON...' : 'Generate LLM Music JSON'}
        </Button>

        {generatedMusicJson && (
          <GeneratedMusic>
            <h4>Generated Music JSON</h4>
            <PromptJsonEditor />
            <NotationViewer />
            <PlaybackControls />
          </GeneratedMusic>
        )}
    </Container>
  );
};

function buildLlmRequest(prompt, selectedProvider, selectedModel) {
  return {
    selection: {
      provider: selectedProvider || null,
      model: selectedModel || null,
    },
    options: {
      max_retries: 1,
    },
    prompt: {
      genre: prompt.genre,
      mood: prompt.mood,
      key: prompt.key || null,
      time_signature: prompt.time_signature,
      tempo_min: Number(prompt.tempo_min),
      tempo_max: Number(prompt.tempo_max),
      instruments: prompt.instruments.split(',').map((instrument) => instrument.trim()).filter(Boolean),
      sections: parseSections(prompt.sections),
      complexity: prompt.complexity,
      duration_bars: Number(prompt.duration_bars),
      instructions: prompt.instructions || null,
    },
  };
}

function parseSections(value) {
  return value
    .split(',')
    .map((section) => {
      const [type, bars] = section.split(':').map((part) => part.trim());
      return type && bars ? { type, bars: Number(bars) } : null;
    })
    .filter(Boolean);
}

export default MusicGenerator;
