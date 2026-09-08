import React, { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import { generateLlmMusicJson } from '../api/musicApi.js';
import ComposerWorkspace from './ComposerWorkspace.jsx';
import ImportControls from './ImportControls.jsx';
import ProjectComposerBar from './ProjectComposerBar.jsx';
import { useMusicStore } from '../store/musicStore.js';

const Container = styled.div`
  h2 {
    color: #1e293b;
    margin-bottom: 12px;
    font-size: 1.35rem;
    font-weight: 650;
  }
`;

const Layout = styled.div`
  display: grid;
  gap: 16px;

  @media (min-width: 1200px) {
    grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
    align-items: start;
  }
`;

const GenerationPanel = styled.section`
  padding: 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
`;

const FormGroup = styled.div`
  margin-bottom: 14px;
`;

const Label = styled.label`
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
  color: #374151;
  font-size: 0.9rem;
`;

const Input = styled.input`
  width: 100%;
  padding: 10px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 0.95rem;
  box-sizing: border-box;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const Select = styled.select`
  width: 100%;
  padding: 10px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 0.95rem;
  background: white;
  box-sizing: border-box;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const TextArea = styled.textarea`
  width: 100%;
  min-height: 72px;
  padding: 10px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  font-size: 0.95rem;
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;

  &:focus {
    outline: none;
    border-color: #667eea;
  }
`;

const Button = styled.button`
  background: #667eea;
  color: white;
  border: none;
  padding: 12px 18px;
  border-radius: 8px;
  font-size: 0.95rem;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
  margin-top: 6px;

  &:hover:not(:disabled) {
    background: #5a67d8;
  }

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }
`;

const StatusMessage = styled.div`
  padding: 12px;
  border-radius: 8px;
  margin: 12px 0;
  font-size: 0.9rem;

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

  &.setup {
    background: #fff7ed;
    color: #9a3412;
    border: 1px solid #fdba74;
  }

  code {
    font-size: 0.85em;
  }

  ol {
    margin: 8px 0 0 18px;
  }
`;

const ParameterGrid = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;

  @media (max-width: 480px) {
    grid-template-columns: 1fr;
  }
`;

const ProgressHint = styled.p`
  margin: 8px 0 0;
  font-size: 0.85rem;
  color: #4338ca;
`;

const MusicGenerator = () => {
  const availableLlmModels = useMusicStore((state) => state.availableLlmModels);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const prompt = useMusicStore((state) => state.prompt);
  const generatedMusicJson = useMusicStore((state) => state.generatedMusicJson);
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const generationStatus = useMusicStore((state) => state.generationStatus);
  const uiError = useMusicStore((state) => state.uiError);
  const warnings = useMusicStore((state) => state.warnings);
  const apiStatus = useMusicStore((state) => state.apiStatus);
  const setSelectedLlmModel = useMusicStore((state) => state.setSelectedLlmModel);
  const updatePrompt = useMusicStore((state) => state.updatePrompt);
  const startGeneration = useMusicStore((state) => state.startGeneration);
  const completeGeneration = useMusicStore((state) => state.completeGeneration);
  const failGeneration = useMusicStore((state) => state.failGeneration);

  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const startedAtRef = useRef(null);

  useEffect(() => {
    if (generationStatus !== 'loading') {
      startedAtRef.current = null;
      setElapsedSeconds(0);
      return undefined;
    }
    startedAtRef.current = Date.now();
    const timer = setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [generationStatus]);

  const handleGenerateLlmJson = async () => {
    if (!availableLlmModels.length) {
      failGeneration('No LLM providers configured. Copy .env.example → .env, set a key, and restart compose.');
      return;
    }

    const started = startGeneration();
    if (!started) {
      console.warn('[MusicGenerator] Duplicate generate blocked by store guard');
      return;
    }

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
        provider: response.provider,
        model: response.model,
      });
    } catch (error) {
      failGeneration(error.message);
    }
  };

  const llmReady = availableLlmModels.length > 0;
  const generating = generationStatus === 'loading';

  return (
    <Container>
      <ProjectComposerBar />
      <h2>AI Composer</h2>

      <Layout>
        <GenerationPanel>
          <h3 style={{ margin: '0 0 10px', fontSize: '1rem', color: '#334155' }}>Generate</h3>

          {apiStatus === 'healthy' && !llmReady && (
            <StatusMessage className="setup">
              <strong>API is healthy, but no LLM providers are configured.</strong>
              <ol>
                <li>Copy <code>.env.example</code> → <code>.env</code></li>
                <li>
                  Set <code>LLM_FAKE_MODE=1</code> for credit-free demos/tests, or set
                  <code>OPENAI_API_KEY</code> / <code>DEEPSEEK_API_KEY</code> for real providers
                </li>
                <li>Run <code>docker compose up --build</code></li>
              </ol>
              Secrets stay on the backend only.
            </StatusMessage>
          )}

          {llmReady && (
            <FormGroup>
              <Label htmlFor="llmModel">Provider / Model</Label>
              <Select
                id="llmModel"
                data-testid="llm-model-select"
                value={`${selectedProvider}:${selectedModel}`}
                disabled={generating}
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
              <Input id="genre" value={prompt.genre} disabled={generating || !llmReady} onChange={(event) => updatePrompt('genre', event.target.value)} />
            </FormGroup>
            <FormGroup>
              <Label htmlFor="mood">Mood</Label>
              <Input id="mood" value={prompt.mood} disabled={generating || !llmReady} onChange={(event) => updatePrompt('mood', event.target.value)} />
            </FormGroup>
            <FormGroup>
              <Label htmlFor="key">Key</Label>
              <Input id="key" value={prompt.key} disabled={generating || !llmReady} onChange={(event) => updatePrompt('key', event.target.value)} placeholder="C minor" />
            </FormGroup>
            <FormGroup>
              <Label htmlFor="timeSignature">Time Signature</Label>
              <Input id="timeSignature" value={prompt.time_signature} disabled={generating || !llmReady} onChange={(event) => updatePrompt('time_signature', event.target.value)} />
            </FormGroup>
            <FormGroup>
              <Label htmlFor="tempoMin">Tempo Min</Label>
              <Input id="tempoMin" type="number" min="40" max="240" value={prompt.tempo_min} disabled={generating || !llmReady} onChange={(event) => updatePrompt('tempo_min', event.target.value)} />
            </FormGroup>
            <FormGroup>
              <Label htmlFor="tempoMax">Tempo Max</Label>
              <Input id="tempoMax" type="number" min="40" max="240" value={prompt.tempo_max} disabled={generating || !llmReady} onChange={(event) => updatePrompt('tempo_max', event.target.value)} />
            </FormGroup>
          </ParameterGrid>

          <FormGroup>
            <Label htmlFor="instruments">Instruments / Tracks</Label>
            <Input id="instruments" value={prompt.instruments} disabled={generating || !llmReady} onChange={(event) => updatePrompt('instruments', event.target.value)} placeholder="piano,bass,strings" />
          </FormGroup>

          <FormGroup>
            <Label htmlFor="sections">Sections / Bars</Label>
            <Input id="sections" value={prompt.sections} disabled={generating || !llmReady} onChange={(event) => updatePrompt('sections', event.target.value)} placeholder="intro:4,verse:8,chorus:8" />
          </FormGroup>

          <ParameterGrid>
            <FormGroup>
              <Label htmlFor="complexity">Complexity</Label>
              <Select id="complexity" value={prompt.complexity} disabled={generating || !llmReady} onChange={(event) => updatePrompt('complexity', event.target.value)}>
                <option value="simple">Simple</option>
                <option value="moderate">Moderate</option>
                <option value="complex">Complex</option>
              </Select>
            </FormGroup>
            <FormGroup>
              <Label htmlFor="durationBars">Duration Bars</Label>
              <Input id="durationBars" type="number" min="1" max="512" value={prompt.duration_bars} disabled={generating || !llmReady} onChange={(event) => updatePrompt('duration_bars', event.target.value)} />
            </FormGroup>
          </ParameterGrid>

          <FormGroup>
            <Label htmlFor="instructions">Freeform Instructions</Label>
            <TextArea id="instructions" value={prompt.instructions} disabled={generating || !llmReady} onChange={(event) => updatePrompt('instructions', event.target.value)} placeholder="Add arrangement, texture, or reference notes" />
          </FormGroup>

          {uiError && <StatusMessage className="error">{uiError}</StatusMessage>}
          {warnings.map((warning, index) => (
            <StatusMessage key={`${index}:${warning}`} className="info">{warning}</StatusMessage>
          ))}

          <Button
            data-testid="generate-music"
            onClick={handleGenerateLlmJson}
            disabled={generating || !llmReady}
          >
            {generating ? 'Generating composition…' : 'Generate LLM Music JSON'}
          </Button>
          {generating && (
            <ProgressHint>
              Multi-stage LLM compose in progress ({elapsedSeconds}s). This can take a minute…
            </ProgressHint>
          )}

          <ImportControls mode="replace" title="Or import a score" />
        </GenerationPanel>

        <div>
          {editedMusicJson || generatedMusicJson ? (
            <ComposerWorkspace />
          ) : (
            <StatusMessage className="info">
              Generate or import a composition to open the piano roll, notation, AI region edit, and export tools.
            </StatusMessage>
          )}
        </div>
      </Layout>
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
