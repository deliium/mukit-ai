import React, { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import { fetchAiModels, generateLlmMusicJson } from '../api/musicApi.js';
import { listComposerProfiles } from '../api/composerProfileApi.js';
import {
  getEnsembleArbitrationStatus,
  getEnsembleArbitrationStrategies,
  previewEnsembleArbitration,
} from '../api/ensembleArbitrationApi.js';
import { buildLlmRequest } from '../utils/llmGenerateRequest.js';
import { buildStyleReferenceFromMusicalReference } from '../utils/compositionEmbeddingReference.js';
import {
  buildConditioningRequestFields,
  collectMultiRefBorrowRows,
  loadConditioningSession,
} from '../utils/referenceConditioningPolicy.js';
import { stageEnsembleSurvivorAsGenerationCandidate } from '../utils/compositionCandidateLifecycle.js';
import {
  buildEnsemblePreviewRequest,
  clampEnsembleModelIds,
  clampEnsembleTopN,
  normalizeEnsembleSelectionMode,
} from '../utils/ensembleArbitrationForm.js';
import ImportControls from './ImportControls.jsx';
import ReferenceFeaturesControls from './ReferenceFeaturesControls.jsx';
import { useMusicStore } from '../store/musicStore.js';

const PanelRoot = styled.div`
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
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

const GeneratePanel = () => {
  const availableLlmModels = useMusicStore((state) => state.availableLlmModels);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const prompt = useMusicStore((state) => state.prompt);
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
  const generationCandidate = useMusicStore((state) => state.generationCandidate);
  const generationAuditionActive = useMusicStore((state) => state.generationAuditionActive);
  const generationCompareResult = useMusicStore((state) => state.generationCompareResult);
  const setGenerationAuditionActive = useMusicStore((state) => state.setGenerationAuditionActive);
  const refreshGenerationComparison = useMusicStore((state) => state.refreshGenerationComparison);
  const rejectGenerationCandidate = useMusicStore((state) => state.rejectGenerationCandidate);
  const applyGenerationCandidate = useMusicStore((state) => state.applyGenerationCandidate);
  const installEnsembleGenerationCandidate = useMusicStore(
    (state) => state.installEnsembleGenerationCandidate,
  );
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const composerProfileId = useMusicStore((state) => state.composerProfileId);
  const composerProfileStrength = useMusicStore((state) => state.composerProfileStrength);
  const composerProfileList = useMusicStore((state) => state.composerProfileList);
  const musicalReferenceEnabled = useMusicStore((state) => state.musicalReferenceEnabled);
  const musicalReference = useMusicStore((state) => state.musicalReference);
  const musicalReferenceComposition = useMusicStore((state) => state.musicalReferenceComposition);
  const musicalReferenceB = useMusicStore((state) => state.musicalReferenceB);
  const musicalReferenceBComposition = useMusicStore((state) => state.musicalReferenceBComposition);
  const musicalReferenceBStatus = useMusicStore((state) => state.musicalReferenceBStatus);
  const projectList = useMusicStore((state) => state.projectList);
  const setMusicalReferenceFeatureMask = useMusicStore(
    (state) => state.setMusicalReferenceFeatureMask,
  );
  const selectMusicalReferenceBProject = useMusicStore((state) => state.selectMusicalReferenceBProject);
  const clearMusicalReferenceB = useMusicStore((state) => state.clearMusicalReferenceB);
  const setComposerProfileList = useMusicStore((state) => state.setComposerProfileList);
  const setComposerProfileSelection = useMusicStore((state) => state.setComposerProfileSelection);

  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [branchNameDraft, setBranchNameDraft] = useState('');
  const [applyBusy, setApplyBusy] = useState(false);
  const [pipeline, setPipeline] = useState('llm_only');
  const [hybridSeed, setHybridSeed] = useState('');
  const [symbolicReady, setSymbolicReady] = useState(false);
  const [symbolicModels, setSymbolicModels] = useState([]);
  const [composerModelId, setComposerModelId] = useState('');
  const [lastProvenance, setLastProvenance] = useState(null);
  const [ensembleEnabled, setEnsembleEnabled] = useState(false);
  const [ensembleModeOn, setEnsembleModeOn] = useState(false);
  const [ensembleMaxModels, setEnsembleMaxModels] = useState(3);
  const [ensembleModelIds, setEnsembleModelIds] = useState([]);
  const [ensembleSelectionMode, setEnsembleSelectionMode] = useState('human');
  const [ensembleTopN, setEnsembleTopN] = useState(2);
  const [ensembleBusy, setEnsembleBusy] = useState(false);
  const [ensembleError, setEnsembleError] = useState('');
  const [ensembleReport, setEnsembleReport] = useState(null);
  const [ensembleSelectedId, setEnsembleSelectedId] = useState('');
  const startedAtRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    listComposerProfiles()
      .then((items) => {
        if (!cancelled) setComposerProfileList(items || []);
      })
      .catch(() => {
        if (!cancelled) setComposerProfileList([]);
      });
    return () => { cancelled = true; };
  }, [setComposerProfileList]);

  useEffect(() => {
    let cancelled = false;
    fetchAiModels({ capability: 'symbolic_composer', status: 'ready' })
      .then((response) => {
        if (cancelled) return;
        const models = Array.isArray(response?.models) ? response.models : [];
        const ready = models.length > 0;
        setSymbolicModels(models);
        setSymbolicReady(ready);
        console.debug('[GeneratePanel] Symbolic composer discovery', {
          ready,
          modelIds: (response?.models || []).map((model) => model.id),
        });
      })
      .catch((error) => {
        if (cancelled) return;
        console.debug('[GeneratePanel] Symbolic composer discovery failed', {
          message: error?.message || String(error),
        });
        setSymbolicModels([]);
        setSymbolicReady(false);
      });
    // Status/strategies only — never start ensemble fan-out on open.
    Promise.all([
      getEnsembleArbitrationStatus().catch(() => null),
      getEnsembleArbitrationStrategies().catch(() => null),
    ]).then(([status]) => {
      if (cancelled || !status) return;
      setEnsembleEnabled(Boolean(status.enabled));
      setEnsembleMaxModels(Number(status.max_models) || 3);
      console.debug('[GeneratePanel] Ensemble status', {
        enabled: Boolean(status.enabled),
        maxModels: status.max_models,
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (pipeline === 'hybrid_plan_symbolic' && !symbolicReady) {
      setPipeline('llm_only');
    }
  }, [pipeline, symbolicReady]);

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

  const toggleEnsembleModel = (modelId) => {
    setEnsembleModelIds((current) => {
      const next = current.includes(modelId)
        ? current.filter((id) => id !== modelId)
        : [...current, modelId];
      return clampEnsembleModelIds(next, ensembleMaxModels);
    });
  };

  const handleRunEnsemble = async () => {
    setEnsembleError('');
    const built = buildEnsemblePreviewRequest({
      modelIds: ensembleModelIds,
      selectionMode: ensembleSelectionMode,
      topN: ensembleTopN,
      baseSeed: hybridSeed === '' ? 0 : Number(hybridSeed),
      maxModels: ensembleMaxModels,
      prompt,
    });
    if (!built.ok) {
      setEnsembleError(built.message);
      return;
    }
    setEnsembleBusy(true);
    try {
      const report = await previewEnsembleArbitration(built.request);
      setEnsembleReport(report);
      const suggested = report?.suggested_candidate_id || '';
      const firstId = report?.candidates?.[0]?.candidate_id || '';
      setEnsembleSelectedId(
        normalizeEnsembleSelectionMode(ensembleSelectionMode) === 'human'
          ? (firstId || '')
          : (suggested || firstId || ''),
      );
      console.debug('[GeneratePanel] Ensemble preview', {
        suggestedId: suggested || null,
        survivorCount: report?.candidates?.length || 0,
        rejectedCount: report?.rejected_attempts?.length || 0,
      });
    } catch (error) {
      setEnsembleReport(null);
      setEnsembleSelectedId('');
      setEnsembleError(error?.message || 'Ensemble preview failed');
    } finally {
      setEnsembleBusy(false);
    }
  };

  const handleStageEnsembleSurvivor = async ({ applyAfter = false } = {}) => {
    if (!ensembleReport || !ensembleSelectedId) {
      setEnsembleError('Select an ensemble survivor before staging.');
      return;
    }
    const survivor = (ensembleReport.candidates || []).find(
      (item) => item.candidate_id === ensembleSelectedId,
    );
    if (!survivor) {
      setEnsembleError('Selected ensemble survivor is missing.');
      return;
    }
    setApplyBusy(true);
    setEnsembleError('');
    try {
      const envelope = await stageEnsembleSurvivorAsGenerationCandidate({
        survivor,
        report: ensembleReport,
        workingComposition: editedMusicJson,
        promptSnapshot: prompt,
      });
      const installed = await installEnsembleGenerationCandidate(envelope);
      if (!installed) {
        setEnsembleError('Could not stage ensemble survivor.');
        return;
      }
      // Suggestion never auto-Applies — only explicit Apply does.
      if (applyAfter) {
        await applyGenerationCandidate();
      }
    } catch (error) {
      setEnsembleError(error?.message || 'Ensemble stage failed');
    } finally {
      setApplyBusy(false);
    }
  };

  const handleGenerateLlmJson = async () => {
    if (!availableLlmModels.length) {
      failGeneration('No LLM providers configured. Copy .env.example → .env, set a key, and restart compose.');
      return;
    }

    const started = await startGeneration();
    if (!started) {
      console.warn('[GeneratePanel] Duplicate generate blocked by store guard');
      return;
    }

    const requestData = buildLlmRequest(prompt, selectedProvider, selectedModel, {
      pipeline,
      seed: hybridSeed,
      composerModelId: pipeline === 'hybrid_plan_symbolic' ? composerModelId : '',
      profileId: composerProfileId,
      profileStrength: composerProfileStrength,
    });
    if (musicalReferenceEnabled && musicalReference) {
      const refInput = {
        ...musicalReference,
        composition: musicalReferenceComposition || musicalReference.composition,
      };
      if (musicalReference.referenceFeatureMaskEnabled) {
        refInput.dimensions = musicalReference.dimensions;
      } else {
        delete refInput.dimensions;
      }
      const built = buildStyleReferenceFromMusicalReference(refInput);
      if (built.ok && built.styleReference) {
        requestData.style_reference = built.styleReference;
      }
      const policyState = loadConditioningSession();
      const { rows: borrowRows } = collectMultiRefBorrowRows({
        enabled: Boolean(musicalReference.referenceFeatureMaskEnabled),
        dimensions: musicalReference.dimensions,
        borrowSourceByDim: policyState.borrowSourceByDim,
        primary: musicalReference,
        primaryComposition: refInput.composition,
        secondary: musicalReferenceB,
        secondaryComposition: musicalReferenceBComposition,
      });
      const conditioning = buildConditioningRequestFields({
        policyState,
        borrowRows,
        activeProjectId: currentProjectId,
        includePolicy: true,
      });
      if (conditioning.ok) {
        if (conditioning.fields.reference_conditioning_policy) {
          requestData.reference_conditioning_policy = conditioning.fields.reference_conditioning_policy;
        }
        if (conditioning.fields.active_project_id) {
          requestData.active_project_id = conditioning.fields.active_project_id;
        }
        if (conditioning.fields.style_references) {
          requestData.style_references = conditioning.fields.style_references;
          delete requestData.style_reference;
        } else if (conditioning.fields.style_reference) {
          requestData.style_reference = conditioning.fields.style_reference;
        }
      }
    }
    console.debug('[GeneratePanel] LLM generation requested', {
      provider: selectedProvider,
      model: selectedModel,
      genre: prompt.genre,
      mood: prompt.mood,
      pipeline,
      seed: requestData.options?.seed ?? null,
      profileId: requestData.profile_id || null,
      profileStrength: requestData.profile_strength,
      styleReferenceDimensions: requestData.style_reference?.dimensions || null,
    });

    try {
      const response = await generateLlmMusicJson(requestData);
      setLastProvenance({
        pipelineId: response.pipeline_id || pipeline,
        stages: response.stages || [],
        seed: response.seed ?? null,
        planSchemaVersion: response.plan_schema_version || null,
      });
      await completeGeneration({
        music: response.music,
        musicxml: response.musicxml,
        warnings: response.warnings,
        provider: response.provider,
        model: response.model,
        model_id: response.model_id || response.resolved_model_id || null,
        model_version: response.model_version || null,
        runtime: response.runtime || null,
        capability: response.capability || null,
        operation: response.operation || 'generate',
        generation_parameters: response.generation_parameters || null,
        requested_model_id: response.requested_model_id || null,
        resolved_model_id: response.resolved_model_id || null,
        fallback_applied: Boolean(response.fallback_applied),
        pipeline_id: response.pipeline_id || pipeline,
        stages: Array.isArray(response.stages) ? response.stages : [],
        seed: response.seed ?? null,
      });
    } catch (error) {
      failGeneration(error.message);
    }
  };

  const llmReady = availableLlmModels.length > 0;
  const generating = generationStatus === 'loading';

  return (
    <PanelRoot data-testid="generation-panel">
        {apiStatus === 'healthy' && !llmReady && (
          <StatusMessage className="setup">
            <strong>API is healthy, but no LLM providers are configured.</strong>
            <ol>
              <li>Copy <code>.env.example</code> → <code>.env</code></li>
              <li>
                Set <code>LLM_FAKE_MODE=1</code> for credit-free demos/tests, set
                <code>OPENAI_API_KEY</code> / <code>DEEPSEEK_API_KEY</code> for cloud providers,
                or enable optional local AI (<code>LOCAL_LLM_ENABLED=1</code> +{' '}
                <code>docker compose -f docker-compose.yml -f compose.local-ai.yml --profile local-ai up</code>)
              </li>
              <li>Run <code>docker compose up --build</code> (local AI profile only when using a sidecar)</li>
            </ol>
            Secrets and weight paths stay on the backend / host only. See <code>docs/local-ai.md</code>.
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
                const value = event.target.value;
                const sep = value.indexOf(':');
                const provider = sep >= 0 ? value.slice(0, sep) : value;
                const model = sep >= 0 ? value.slice(sep + 1) : '';
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

        <FormGroup>
          <Label htmlFor="generationPipeline">Pipeline</Label>
          <Select
            id="generationPipeline"
            data-testid="generation-pipeline-select"
            value={pipeline}
            disabled={generating || !llmReady}
            title={
              symbolicReady
                ? 'LLM plans and writes notes, or Hybrid uses LLM plan + symbolic notes'
                : 'Hybrid requires a ready symbolic_composer model'
            }
            onChange={(event) => setPipeline(event.target.value)}
          >
            <option value="llm_only">LLM</option>
            <option value="hybrid_plan_symbolic" disabled={!symbolicReady}>
              Hybrid{symbolicReady ? '' : ' (unavailable)'}
            </option>
          </Select>
        </FormGroup>

        {pipeline === 'hybrid_plan_symbolic' ? (
          <FormGroup>
            <Label htmlFor="composerModel">Symbolic composer</Label>
            <Select
              id="composerModel"
              data-testid="generation-composer-model"
              value={composerModelId}
              disabled={generating}
              onChange={(event) => setComposerModelId(event.target.value)}
            >
              <option value="">Default symbolic composer</option>
              {symbolicModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.display_name || model.id}
                </option>
              ))}
            </Select>
          </FormGroup>
        ) : null}

        {pipeline === 'hybrid_plan_symbolic' ? (
          <FormGroup>
            <Label htmlFor="hybridSeed">Seed (optional)</Label>
            <Input
              id="hybridSeed"
              data-testid="generation-hybrid-seed"
              type="number"
              min="0"
              placeholder="Deterministic symbolic seed"
              value={hybridSeed}
              disabled={generating}
              onChange={(event) => setHybridSeed(event.target.value)}
            />
          </FormGroup>
        ) : null}

        {pipeline === 'hybrid_plan_symbolic' && ensembleEnabled ? (
          <div data-testid="ensemble-arbitration-panel" style={{ marginBottom: 14 }}>
            <FormGroup>
              <Label htmlFor="ensembleModeToggle">
                <input
                  id="ensembleModeToggle"
                  data-testid="ensemble-mode-toggle"
                  type="checkbox"
                  checked={ensembleModeOn}
                  disabled={generating || ensembleBusy}
                  onChange={(event) => setEnsembleModeOn(event.target.checked)}
                  style={{ marginRight: 8 }}
                />
                Ensemble mode (multi-model preview)
              </Label>
            </FormGroup>
            {ensembleModeOn ? (
              <>
                <StatusMessage className="info" data-testid="ensemble-honesty-banner">
                  Preference and critic scores are not musical truth. Suggested
                  does not Apply — pick a survivor and commit explicitly.
                </StatusMessage>
                <FormGroup>
                  <Label>Symbolic models (2–{ensembleMaxModels})</Label>
                  <div data-testid="ensemble-model-multiselect">
                    {symbolicModels.map((model) => (
                      <label
                        key={model.id}
                        style={{ display: 'block', fontSize: '0.9rem', marginBottom: 4 }}
                      >
                        <input
                          type="checkbox"
                          checked={ensembleModelIds.includes(model.id)}
                          disabled={generating || ensembleBusy}
                          onChange={() => toggleEnsembleModel(model.id)}
                          style={{ marginRight: 8 }}
                        />
                        {model.display_name || model.id}
                      </label>
                    ))}
                  </div>
                </FormGroup>
                <ParameterGrid>
                  <FormGroup>
                    <Label htmlFor="ensembleSelectionMode">Selection mode</Label>
                    <Select
                      id="ensembleSelectionMode"
                      data-testid="ensemble-selection-mode"
                      value={ensembleSelectionMode}
                      disabled={generating || ensembleBusy}
                      onChange={(event) => setEnsembleSelectionMode(
                        normalizeEnsembleSelectionMode(event.target.value),
                      )}
                    >
                      <option value="human">Human pick</option>
                      <option value="auto_suggest">Auto suggest</option>
                      <option value="top_n">Top-N list</option>
                    </Select>
                  </FormGroup>
                  <FormGroup>
                    <Label htmlFor="ensembleTopN">Top-N</Label>
                    <Input
                      id="ensembleTopN"
                      data-testid="ensemble-top-n"
                      type="number"
                      min="1"
                      max={ensembleMaxModels}
                      value={ensembleTopN}
                      disabled={generating || ensembleBusy || ensembleSelectionMode !== 'top_n'}
                      onChange={(event) => setEnsembleTopN(
                        clampEnsembleTopN(event.target.value, ensembleMaxModels),
                      )}
                    />
                  </FormGroup>
                </ParameterGrid>
                <Button
                  type="button"
                  data-testid="ensemble-run"
                  disabled={generating || ensembleBusy || ensembleModelIds.length < 2}
                  onClick={handleRunEnsemble}
                >
                  {ensembleBusy ? 'Running ensemble…' : 'Run ensemble preview'}
                </Button>
                {ensembleError ? (
                  <StatusMessage className="error" data-testid="ensemble-error">
                    {ensembleError}
                  </StatusMessage>
                ) : null}
                {ensembleReport ? (
                  <div data-testid="ensemble-results" style={{ marginTop: 10 }}>
                    {ensembleReport.suggested_candidate_id ? (
                      <StatusMessage className="info" data-testid="ensemble-suggested">
                        Suggested (not Applied): {ensembleReport.suggested_candidate_id}
                      </StatusMessage>
                    ) : null}
                    {(ensembleReport.rejected_attempts || []).length ? (
                      <StatusMessage className="info" data-testid="ensemble-rejected">
                        Rejected:{' '}
                        {(ensembleReport.rejected_attempts || [])
                          .map((item) => `${item.model_id} (${item.stage}/${item.code})`)
                          .join('; ')}
                      </StatusMessage>
                    ) : null}
                    {(ensembleReport.candidates || []).map((candidate) => (
                      <label
                        key={candidate.candidate_id}
                        data-testid={`ensemble-candidate-${candidate.candidate_id}`}
                        style={{ display: 'block', marginBottom: 6, fontSize: '0.9rem' }}
                      >
                        <input
                          type="radio"
                          name="ensemble-survivor"
                          checked={ensembleSelectedId === candidate.candidate_id}
                          onChange={() => setEnsembleSelectedId(candidate.candidate_id)}
                          style={{ marginRight: 8 }}
                        />
                        {candidate.provenance?.model_id || candidate.candidate_id}
                        {candidate.candidate_id === ensembleReport.suggested_candidate_id
                          ? ' · suggested'
                          : ''}
                        {candidate.critic?.finding_count != null
                          ? ` · critic findings ${candidate.critic.finding_count}`
                          : ''}
                      </label>
                    ))}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                      <Button
                        type="button"
                        data-testid="ensemble-stage"
                        style={{ width: 'auto', marginTop: 0, background: '#475569' }}
                        disabled={applyBusy || !ensembleSelectedId}
                        onClick={() => handleStageEnsembleSurvivor({ applyAfter: false })}
                      >
                        Stage selected
                      </Button>
                      <Button
                        type="button"
                        data-testid="ensemble-apply-selected"
                        style={{ width: 'auto', marginTop: 0, background: '#059669' }}
                        disabled={applyBusy || !ensembleSelectedId}
                        onClick={() => handleStageEnsembleSurvivor({ applyAfter: true })}
                      >
                        Apply selected
                      </Button>
                    </div>
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}

        {lastProvenance?.stages?.length ? (
          <StatusMessage className="info" data-testid="generation-provenance">
            <strong>Provenance</strong>
            <div style={{ marginTop: 6 }}>
              Pipeline: {lastProvenance.pipelineId}
              {lastProvenance.seed != null ? ` · seed ${lastProvenance.seed}` : ''}
            </div>
            <ul style={{ margin: '6px 0 0 18px' }}>
              {lastProvenance.stages.map((stage) => (
                <li key={`${stage.operation}-${stage.model_id}`}>
                  {stage.operation}: {stage.model_id || 'unknown'}
                  {stage.capability ? ` (${stage.capability})` : ''}
                </li>
              ))}
            </ul>
          </StatusMessage>
        ) : null}

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

        <ParameterGrid>
          <FormGroup>
            <Label htmlFor="composerProfile">Composer profile</Label>
            <Select
              id="composerProfile"
              data-testid="generate-composer-profile"
              value={composerProfileId || ''}
              disabled={generating || !llmReady}
              onChange={(event) => setComposerProfileSelection({
                profileId: event.target.value || null,
                strength: event.target.value
                  ? (composerProfileStrength === 'off' ? 'normal' : composerProfileStrength)
                  : 'off',
              })}
            >
              <option value="">None</option>
              {(composerProfileList || []).map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </Select>
          </FormGroup>
          <FormGroup>
            <Label htmlFor="composerProfileStrength">Profile strength</Label>
            <Select
              id="composerProfileStrength"
              data-testid="generate-composer-profile-strength"
              value={composerProfileStrength}
              disabled={generating || !llmReady || !composerProfileId}
              onChange={(event) => setComposerProfileSelection({
                profileId: composerProfileId,
                strength: event.target.value,
              })}
            >
              <option value="off">Off</option>
              <option value="light">Light</option>
              <option value="normal">Normal</option>
              <option value="strong">Strong</option>
            </Select>
          </FormGroup>
        </ParameterGrid>
        <StatusMessage className="info">
          Composer profiles soft-condition generation only. Key, instruments, and other prompt fields always win. Manage profiles in the Profiles tab.
        </StatusMessage>
        {musicalReferenceEnabled && musicalReference && (
          <FormGroup>
            <Label>Reference feature dimensions</Label>
            <StatusMessage className="info">
              Optional mask for the Develop-tab musical reference on generate. Does not copy melodies.
            </StatusMessage>
            <ReferenceFeaturesControls
              enabled={Boolean(musicalReference?.referenceFeatureMaskEnabled)}
              dimensions={musicalReference?.dimensions ?? null}
              onChange={({ enabled, dimensions }) => {
                setMusicalReferenceFeatureMask({ enabled, dimensions });
              }}
              policyMode
              compact
              allowMultiRef
              activeProjectId={currentProjectId}
              borrowProjectId={musicalReference?.projectId || musicalReference?.project_id || null}
              projectList={projectList}
              secondaryReference={musicalReferenceB}
              secondaryStatus={musicalReferenceBStatus}
              onSelectSecondaryProject={(id) => selectMusicalReferenceBProject(id)}
              onClearSecondary={() => clearMusicalReferenceB()}
            />
          </FormGroup>
        )}

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

        {generationCandidate ? (
          <div data-testid="generation-candidate-panel" style={{ marginTop: 14 }}>
            <StatusMessage className="success">
              Generation preview ready
              {generationCandidate.provider
                ? ` · ${generationCandidate.provider}/${generationCandidate.model || '—'}`
                : ''}
              . Working composition is unchanged until Apply.
            </StatusMessage>
            {generationCompareResult ? (
              <StatusMessage className="info" data-testid="generation-compare-summary">
                Compare vs working:{' '}
                {generationCompareResult.identical ? 'identical' : 'differences'}
                {' · '}
                +{generationCompareResult.events?.added || 0}
                {' / -'}
                {generationCompareResult.events?.removed || 0}
                {' / ~'}
                {generationCompareResult.events?.changed || 0}
              </StatusMessage>
            ) : null}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              <Button
                type="button"
                data-testid="generation-audition-toggle"
                style={{ width: 'auto', marginTop: 0 }}
                disabled={applyBusy}
                onClick={() => {
                  const next = !generationAuditionActive;
                  console.debug('[GeneratePanel] Toggle generation audition', { active: next });
                  setGenerationAuditionActive(next);
                }}
              >
                {generationAuditionActive ? 'Play working' : 'Audition candidate'}
              </Button>
              <Button
                type="button"
                data-testid="generation-compare"
                style={{ width: 'auto', marginTop: 0, background: '#475569' }}
                disabled={applyBusy}
                onClick={() => {
                  console.debug('[GeneratePanel] Compare generation candidate');
                  refreshGenerationComparison();
                }}
              >
                Compare
              </Button>
              <Button
                type="button"
                data-testid="generation-apply"
                style={{ width: 'auto', marginTop: 0, background: '#059669' }}
                disabled={applyBusy}
                onClick={async () => {
                  setApplyBusy(true);
                  try {
                    console.info('[GeneratePanel] Apply generation candidate');
                    await applyGenerationCandidate();
                  } catch {
                    // store records uiError
                  } finally {
                    setApplyBusy(false);
                  }
                }}
              >
                Apply
              </Button>
              <Button
                type="button"
                data-testid="generation-reject"
                style={{ width: 'auto', marginTop: 0, background: '#dc2626' }}
                disabled={applyBusy}
                onClick={() => {
                  console.info('[GeneratePanel] Reject generation candidate');
                  rejectGenerationCandidate();
                }}
              >
                Reject
              </Button>
            </div>
            {currentProjectId ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
                <Input
                  aria-label="Apply as new branch name"
                  data-testid="generation-branch-name"
                  placeholder="New branch name"
                  value={branchNameDraft}
                  disabled={applyBusy}
                  onChange={(event) => setBranchNameDraft(event.target.value)}
                />
                <Button
                  type="button"
                  data-testid="generation-apply-as-branch"
                  style={{ width: 'auto', marginTop: 0, background: '#7c3aed' }}
                  disabled={applyBusy || !branchNameDraft.trim()}
                  onClick={async () => {
                    setApplyBusy(true);
                    try {
                      console.info('[GeneratePanel] Apply generation as new branch');
                      await applyGenerationCandidate({
                        asNewBranch: true,
                        branchName: branchNameDraft.trim(),
                      });
                      setBranchNameDraft('');
                    } catch {
                      // store records uiError
                    } finally {
                      setApplyBusy(false);
                    }
                  }}
                >
                  Apply as new branch
                </Button>
              </div>
            ) : (
              <ProgressHint>
                No project open: Apply installs locally only (no durable history).
              </ProgressHint>
            )}
          </div>
        ) : null}

        <ImportControls mode="replace" title="Or import a score" />
    </PanelRoot>
  );
};

export default GeneratePanel;
