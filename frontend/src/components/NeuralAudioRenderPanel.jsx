import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  deleteNeuralAudioRender,
  deleteNeuralAudioStemSet,
  downloadNeuralAudioRender,
  downloadNeuralAudioStem,
  enqueueNeuralAudioRender,
  enqueueNeuralAudioStemSet,
  fetchAiModels,
  getNeuralAudioRender,
  listNeuralAudioRenders,
  listNeuralAudioStemSets,
  getNeuralAudioStemSet,
  NeuralAudioApiError,
  rerenderNeuralAudioStem,
} from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { createAppLogger } from '../utils/appLogger.js';
import {
  neuralAudioFidelityDisclaimer,
  isNeuralAudioDownloadReady,
  isNeuralAudioJobStale,
} from '../utils/neuralAudioRenderUi.js';
import {
  NEURAL_AUDIO_STEM_ROLES,
  isNeuralAudioStemSetStale,
  latestStemsByRole,
  neuralAudioStemSyncDisclaimer,
} from '../utils/neuralAudioStemUi.js';
import { resolveLiveSnapshotFingerprint } from '../utils/compositionSnapshotFingerprint.js';
import MixAnalysisPanel from './MixAnalysisPanel.jsx';

const log = createAppLogger('neuralAudioRender');
const stemLog = createAppLogger('neuralAudioStems');

const Panel = styled.section`
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e5e7eb;
`;

const Title = styled.h3`
  margin: 0 0 8px;
  font-size: 1rem;
  font-weight: 600;
  color: #111827;
`;

const Banner = styled.div`
  margin: 0 0 10px;
  padding: 8px 10px;
  border-left: 3px solid ${(props) => (props.$tone === 'warn' ? '#b45309' : '#1d4ed8')};
  background: ${(props) => (props.$tone === 'warn' ? '#fffbeb' : '#eff6ff')};
  color: #1f2937;
  font-size: 0.82rem;
  line-height: 1.35;
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
  font-size: 0.8rem;
  color: #374151;
`;

const Input = styled.input`
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 0.9rem;
`;

const TextArea = styled.textarea`
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 0.9rem;
  min-height: 72px;
  resize: vertical;
`;

const Select = styled.select`
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 0.9rem;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
`;

const Button = styled.button`
  background: #0f766e;
  color: white;
  border: none;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;

  &:hover:not(:disabled) {
    background: #0d9488;
  }

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }
`;

const SecondaryButton = styled(Button)`
  background: #4b5563;

  &:hover:not(:disabled) {
    background: #374151;
  }
`;

const Status = styled.div`
  font-size: 0.85rem;
  color: ${(props) => (props.$error ? '#991b1b' : '#065f46')};
  margin-bottom: 8px;
`;

const JobList = styled.ul`
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
`;

const JobItem = styled.li`
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 8px;
  font-size: 0.8rem;
  color: #374151;
`;

const Badge = styled.span`
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 600;
  background: ${(props) => {
    if (props.$status === 'complete') return '#d1fae5';
    if (props.$status === 'failed') return '#fee2e2';
    if (props.$status === 'running') return '#dbeafe';
    return '#f3f4f6';
  }};
  color: ${(props) => {
    if (props.$status === 'complete') return '#065f46';
    if (props.$status === 'failed') return '#991b1b';
    if (props.$status === 'running') return '#1e40af';
    return '#374151';
  }};
`;

const NeuralAudioRenderPanel = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const currentRevisionId = useMusicStore((state) => state.currentRevisionId);
  const workingFingerprint = useMusicStore((state) => state.workingFingerprint);
  const saveStatus = useMusicStore((state) => state.saveStatus);
  const roundtripProvenance = useMusicStore((state) => state.roundtripProvenance);
  const setUiError = useMusicStore((state) => state.setUiError);

  const [models, setModels] = useState([]);
  const [modelId, setModelId] = useState('');
  const [instructions, setInstructions] = useState('');
  const [genre, setGenre] = useState('');
  const [mood, setMood] = useState('');
  const [adapterKind, setAdapterKind] = useState('');
  const [jobs, setJobs] = useState([]);
  const [stemSets, setStemSets] = useState([]);
  const [selectedStemRoles, setSelectedStemRoles] = useState(() => (
    NEURAL_AUDIO_STEM_ROLES.filter((role) => role !== 'vocals' && role !== 'other')
  ));
  const [stemEngine, setStemEngine] = useState('neural');
  const [busy, setBusy] = useState(false);
  const [statusMessage, setStatusMessage] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [liveFingerprint, setLiveFingerprint] = useState(null);

  const validation = editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false };
  const canRender = Boolean(
    editedMusicJson
      && validation.valid
      && isCanonicalComposition(editedMusicJson)
      && !busy,
  );

  const selectedModel = models.find((m) => m.id === modelId) || null;
  const fidelityClass = selectedModel?.limits?.fidelity_class || 'generative';

  useEffect(() => {
    let cancelled = false;
    resolveLiveSnapshotFingerprint({
      composition: editedMusicJson,
      workingFingerprint,
      saveStatus,
    }).then((fp) => {
      if (!cancelled) setLiveFingerprint(fp);
    });
    return () => {
      cancelled = true;
    };
  }, [editedMusicJson, workingFingerprint, saveStatus]);

  useEffect(() => {
    let cancelled = false;
    fetchAiModels({ operation: 'audio_render' })
      .then((response) => {
        if (cancelled) return;
        const list = Array.isArray(response?.models) ? response.models : [];
        const usable = list.filter((m) => m && (m.status === 'ready' || m.id?.startsWith('fake:')));
        setModels(usable.length ? usable : list);
        const preferred =
          usable.find((m) => m.id === 'fake:neural-audio')
          || usable.find((m) => m.status === 'ready')
          || list[0];
        if (preferred?.id) {
          setModelId(preferred.id);
        }
        log.info('Neural audio models loaded', {
          count: list.length,
          readyCount: usable.length,
        });
      })
      .catch((error) => {
        if (cancelled) return;
        log.warn('Neural audio model catalog failed', {
          message: error?.message || 'unknown',
        });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!currentProjectId) {
      setJobs([]);
      setStemSets([]);
      return undefined;
    }
    let cancelled = false;
    listNeuralAudioRenders(currentProjectId)
      .then((response) => {
        if (cancelled) return;
        setJobs(Array.isArray(response?.items) ? response.items : []);
      })
      .catch(() => {
        if (!cancelled) setJobs([]);
      });
    listNeuralAudioStemSets(currentProjectId)
      .then((response) => {
        if (cancelled) return;
        setStemSets(Array.isArray(response?.items) ? response.items : []);
      })
      .catch(() => {
        if (!cancelled) setStemSets([]);
      });
    return () => {
      cancelled = true;
    };
  }, [currentProjectId]);

  useEffect(() => {
    const pending = jobs.filter((j) => j.status === 'queued' || j.status === 'running');
    if (pending.length === 0) return undefined;
    const timer = setInterval(() => {
      pending.forEach((job) => {
        getNeuralAudioRender(job.id)
          .then((fresh) => {
            setJobs((prev) => {
              const next = prev.map((item) => (item.id === fresh.id ? fresh : item));
              return next;
            });
            log.debug('Neural audio poll', {
              renderId: fresh.id,
              status: fresh.status,
            });
          })
          .catch(() => {});
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [jobs]);

  useEffect(() => {
    const pendingSets = stemSets.filter((set) => {
      if (set.status === 'queued' || set.status === 'running') return true;
      return (set.stems || []).some(
        (stem) => stem.status === 'queued' || stem.status === 'running',
      );
    });
    if (pendingSets.length === 0) return undefined;
    const timer = setInterval(() => {
      pendingSets.forEach((stemSet) => {
        getNeuralAudioStemSet(stemSet.id)
          .then((fresh) => {
            setStemSets((prev) => {
              const next = prev.map((item) => (item.id === fresh.id ? fresh : item));
              return next;
            });
            stemLog.debug('Stem set poll', {
              stemSetId: fresh.id,
              status: fresh.status,
            });
          })
          .catch(() => {});
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [stemSets]);

  const handleRender = async () => {
    if (!canRender) {
      setErrorMessage(validation.message || 'Canonical composition required');
      return;
    }
    setBusy(true);
    setErrorMessage('');
    setStatusMessage('Submitting neural render…');
    log.info('Render with AI clicked', {
      modelId: modelId || null,
      adapterKind: adapterKind || null,
      instructionChars: instructions.length,
      hasProject: Boolean(currentProjectId),
      hasRevision: Boolean(currentRevisionId),
      fidelityClass,
    });
    log.debug('Prompt length only', { instructionChars: instructions.length });
    try {
      const payload = {
        composition: editedMusicJson,
        instructions,
        genre: genre || null,
        mood: mood || null,
        model_id: modelId || null,
        adapter_kind: adapterKind || null,
      };
      if (currentProjectId && currentRevisionId) {
        payload.project_id = currentProjectId;
        payload.source_revision_id = currentRevisionId;
      }
      const job = await enqueueNeuralAudioRender(payload);
      setJobs((prev) => [job, ...prev.filter((item) => item.id !== job.id)]);
      setStatusMessage(`Job ${job.status}: ${job.fidelity_label || job.fidelity_class}`);
      log.info('Neural audio job accepted', {
        renderId: job.id,
        status: job.status,
        fidelityClass: job.fidelity_class,
        modelId: job.model_id,
      });
    } catch (error) {
      const message =
        error instanceof NeuralAudioApiError
          ? error.message
          : error?.message || 'Neural audio render failed';
      setErrorMessage(message);
      setUiError?.(message);
      setStatusMessage('');
      log.error('Neural audio enqueue failed', {
        code: error?.code || null,
        status: error?.status || null,
      });
    } finally {
      setBusy(false);
    }
  };

  const handleDownload = async (renderId) => {
    try {
      await downloadNeuralAudioRender(renderId);
      log.info('Neural audio download started', { renderId });
    } catch (error) {
      setErrorMessage(error?.message || 'Download failed');
    }
  };

  const handleDelete = async (renderId) => {
    try {
      await deleteNeuralAudioRender(renderId);
      setJobs((prev) => prev.filter((item) => item.id !== renderId));
      log.info('Neural audio job deleted', { renderId });
    } catch (error) {
      setErrorMessage(error?.message || 'Delete failed');
    }
  };

  const toggleStemRole = (role) => {
    setSelectedStemRoles((prev) => (
      prev.includes(role) ? prev.filter((item) => item !== role) : [...prev, role]
    ));
  };

  const handleRenderStems = async () => {
    if (!canRender) {
      setErrorMessage(validation.message || 'Canonical composition required');
      return;
    }
    if (selectedStemRoles.length === 0) {
      setErrorMessage('Select at least one stem role');
      return;
    }
    setBusy(true);
    setErrorMessage('');
    setStatusMessage('Submitting stem set…');
    stemLog.info('Render stems clicked', {
      modelId: modelId || null,
      engine: stemEngine,
      roles: selectedStemRoles,
      fidelityClass,
    });
    try {
      const payload = {
        composition: editedMusicJson,
        instructions,
        genre: genre || null,
        mood: mood || null,
        model_id: stemEngine === 'neural' ? (modelId || null) : null,
        adapter_kind: adapterKind || null,
        engine: stemEngine,
        stem_roles: selectedStemRoles,
      };
      if (currentProjectId && currentRevisionId) {
        payload.project_id = currentProjectId;
        payload.source_revision_id = currentRevisionId;
      }
      const stemSet = await enqueueNeuralAudioStemSet(payload);
      setStemSets((prev) => [stemSet, ...prev.filter((item) => item.id !== stemSet.id)]);
      setStatusMessage(
        `Stem set ${stemSet.status}: ${stemSet.stems?.length || 0} stems (${stemSet.engine})`,
      );
      stemLog.info('Stem set accepted', {
        stemSetId: stemSet.id,
        status: stemSet.status,
        stemCount: stemSet.stems?.length || 0,
        engine: stemSet.engine,
      });
    } catch (error) {
      const message =
        error instanceof NeuralAudioApiError
          ? error.message
          : error?.message || 'Stem render failed';
      setErrorMessage(message);
      setUiError?.(message);
      setStatusMessage('');
      stemLog.error('Stem set enqueue failed', {
        code: error?.code || null,
        status: error?.status || null,
      });
    } finally {
      setBusy(false);
    }
  };

  const handleRerenderStem = async (stemSetId, stemId) => {
    if (!canRender) return;
    setBusy(true);
    setErrorMessage('');
    stemLog.info('Rerender stem clicked', { stemSetId, stemId });
    try {
      const payload = {
        composition: editedMusicJson,
        instructions,
        model_id: stemEngine === 'neural' ? (modelId || null) : null,
        engine: stemEngine,
      };
      if (currentProjectId && currentRevisionId) {
        payload.project_id = currentProjectId;
        payload.source_revision_id = currentRevisionId;
      }
      const updated = await rerenderNeuralAudioStem(stemSetId, stemId, payload);
      setStemSets((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setStatusMessage('Stem rerender complete');
    } catch (error) {
      setErrorMessage(error?.message || 'Stem rerender failed');
    } finally {
      setBusy(false);
    }
  };

  const handleDownloadStem = async (stemId) => {
    try {
      await downloadNeuralAudioStem(stemId);
      stemLog.info('Stem download started', { stemId });
    } catch (error) {
      setErrorMessage(error?.message || 'Stem download failed');
    }
  };

  const handleDeleteStemSet = async (stemSetId) => {
    try {
      await deleteNeuralAudioStemSet(stemSetId);
      setStemSets((prev) => prev.filter((item) => item.id !== stemSetId));
      stemLog.info('Stem set deleted', { stemSetId });
    } catch (error) {
      setErrorMessage(error?.message || 'Stem set delete failed');
    }
  };

  const stemCapabilities = selectedModel?.stem_capabilities || selectedModel?.limits?.stem_capabilities || [];

  return (
    <Panel data-testid="neural-audio-render-panel">
      <Title>Render with AI</Title>
      <Banner $tone="warn" data-testid="neural-audio-disclaimer">
        {neuralAudioFidelityDisclaimer(fidelityClass)} This is separate from Export WAV (deterministic
        FluidSynth). Neural audio never changes your composition.
      </Banner>
      <Field>
        Model
        <Select
          data-testid="neural-audio-model"
          value={modelId}
          onChange={(event) => setModelId(event.target.value)}
          disabled={busy}
        >
          {models.length === 0 ? <option value="">No audio_render models</option> : null}
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.display_name || model.id}
              {model.status && model.status !== 'ready' ? ` (${model.status})` : ''}
            </option>
          ))}
        </Select>
      </Field>
      <Field>
        Production / render instructions
        <TextArea
          data-testid="neural-audio-instructions"
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          disabled={busy}
          maxLength={2000}
          placeholder="e.g. warm chamber string mix, soft room reverb"
        />
      </Field>
      <Row>
        <Field style={{ flex: 1, minWidth: 120 }}>
          Genre (optional)
          <Input
            value={genre}
            onChange={(event) => setGenre(event.target.value)}
            disabled={busy}
            maxLength={64}
          />
        </Field>
        <Field style={{ flex: 1, minWidth: 120 }}>
          Mood (optional)
          <Input
            value={mood}
            onChange={(event) => setMood(event.target.value)}
            disabled={busy}
            maxLength={64}
          />
        </Field>
      </Row>
      <Field>
        Adapter hint (optional)
        <Select
          data-testid="neural-audio-adapter"
          value={adapterKind}
          onChange={(event) => setAdapterKind(event.target.value)}
          disabled={busy}
        >
          <option value="">Auto (from model)</option>
          <option value="text_prompt">text_prompt (generative)</option>
          <option value="melody_conditioning">melody_conditioning (generative)</option>
          <option value="midi_projection">midi_projection (neural instrument)</option>
        </Select>
      </Field>
      {roundtripProvenance ? (
        <Banner $tone="info" data-testid="audio-roundtrip-provenance">
          Provenance:
          {' '}
          {[
            roundtripProvenance.source_audio_asset_id
              && `src ${String(roundtripProvenance.source_audio_asset_id).slice(0, 8)}`,
            roundtripProvenance.alignment_asset_id
              && `align ${String(roundtripProvenance.alignment_asset_id).slice(0, 8)}`,
            roundtripProvenance.composition_fingerprint
              && `fp ${String(roundtripProvenance.composition_fingerprint).slice(0, 12)}`,
          ].filter(Boolean).join(' → ') || 'bound recovery'}
        </Banner>
      ) : null}
      <Button
        type="button"
        data-testid="neural-audio-submit"
        disabled={!canRender}
        onClick={handleRender}
      >
        {busy ? 'Rendering…' : 'Render with AI'}
      </Button>
      {statusMessage ? <Status>{statusMessage}</Status> : null}
      {errorMessage ? <Status $error>{errorMessage}</Status> : null}
      {jobs.length > 0 ? (
        <JobList data-testid="neural-audio-job-list">
          {jobs.map((job) => {
            const stale = isNeuralAudioJobStale(job, liveFingerprint);
            if (stale) {
              log.info('stale detect', {
                job_id_prefix: String(job.id).slice(0, 8),
                fp_prefix: String(job.source_fingerprint || '').slice(0, 12),
              });
            }
            return (
              <JobItem key={job.id} data-testid={`neural-audio-job-${job.id}`} data-status={job.status} data-stale={stale ? 'true' : 'false'}>
                <div>
                  <Badge $status={job.status} data-testid={`neural-audio-job-status-${job.id}`}>
                    {job.status}
                  </Badge>
                  {' '}
                  {job.fidelity_label || job.fidelity_class}
                  {stale ? (
                    <Badge $status="failed" data-testid={`neural-audio-job-stale-${job.id}`}>
                      stale
                    </Badge>
                  ) : null}
                </div>
                {stale ? (
                  <Banner $tone="warn" data-testid={`neural-audio-stale-banner-${job.id}`}>
                    Composition moved on since this render. Download still works — Render again for a new job (source audio is never overwritten).
                    {' '}
                    <Button
                      type="button"
                      data-testid={`neural-audio-render-again-${job.id}`}
                      disabled={!canRender || busy}
                      onClick={handleRender}
                      style={{ marginTop: 6 }}
                    >
                      Render again
                    </Button>
                  </Banner>
                ) : null}
                <div>
                  {job.model_id}
                  {job.model_version ? ` @ ${job.model_version}` : ''}
                </div>
                <div>
                  revision:
                  {' '}
                  {job.source_revision_id || 'workspace'}
                </div>
                <div>
                  source fp:
                  {' '}
                  {String(job.source_fingerprint || '').slice(0, 12) || '—'}
                </div>
                <Row>
                  <SecondaryButton
                    type="button"
                    disabled={!isNeuralAudioDownloadReady(job)}
                    data-testid={`neural-audio-download-${job.id}`}
                    data-ready={isNeuralAudioDownloadReady(job) ? 'true' : 'false'}
                    onClick={() => handleDownload(job.id)}
                  >
                    Download
                  </SecondaryButton>
                  <SecondaryButton
                    type="button"
                    data-testid={`neural-audio-delete-${job.id}`}
                    onClick={() => handleDelete(job.id)}
                  >
                    Delete
                  </SecondaryButton>
                </Row>
              </JobItem>
            );
          })}
        </JobList>
      ) : null}

      <Title style={{ marginTop: 16 }}>Stems</Title>
      <Banner $tone="info" data-testid="neural-audio-stems-disclaimer">
        Render separate piano/bass/strings (etc.) stems without changing the symbolic score.
        Generative stems are not sample-locked to siblings.
        {stemCapabilities.length
          ? ` Model capabilities: ${stemCapabilities.join(', ')}.`
          : ''}
      </Banner>
      <Field>
        Stem engine
        <Select
          data-testid="neural-audio-stem-engine"
          value={stemEngine}
          onChange={(event) => setStemEngine(event.target.value)}
          disabled={busy}
        >
          <option value="neural">Neural (capability-aware)</option>
          <option value="fluidsynth">FluidSynth (deterministic, explicit)</option>
        </Select>
      </Field>
      <Row data-testid="neural-audio-stem-roles">
        {NEURAL_AUDIO_STEM_ROLES.map((role) => (
          <label key={role} style={{ fontSize: '0.8rem', marginRight: 8 }}>
            <input
              type="checkbox"
              checked={selectedStemRoles.includes(role)}
              onChange={() => toggleStemRole(role)}
              disabled={busy}
            />
            {' '}
            {role}
          </label>
        ))}
      </Row>
      <Button
        type="button"
        data-testid="neural-audio-stems-submit"
        disabled={!canRender || selectedStemRoles.length === 0}
        onClick={handleRenderStems}
      >
        {busy ? 'Rendering stems…' : 'Render stems'}
      </Button>
      {stemSets.length > 0 ? (
        <JobList data-testid="neural-audio-stem-set-list">
          {stemSets.map((stemSet) => {
            const stale = isNeuralAudioStemSetStale(stemSet, liveFingerprint);
            const latest = latestStemsByRole(stemSet);
            return (
              <JobItem
                key={stemSet.id}
                data-testid={`neural-audio-stem-set-${stemSet.id}`}
                data-stale={stale ? 'true' : 'false'}
              >
                <div>
                  <Badge $status={stemSet.status}>{stemSet.status}</Badge>
                  {' '}
                  {stemSet.engine}
                  {' '}
                  (
                  {stemSet.stems?.length || 0}
                  {' '}
                  members)
                  {stale ? <Badge $status="failed">stale</Badge> : null}
                </div>
                {stale ? (
                  <Banner $tone="warn">
                    Composition moved on since this stem set. Prior stems remain downloadable.
                  </Banner>
                ) : null}
                <div>
                  sync:
                  {' '}
                  {neuralAudioStemSyncDisclaimer(
                    [...latest.values()][0]?.sync_class || 'generative_independent',
                  )}
                </div>
                {[...latest.entries()].map(([role, stem]) => (
                  <Row key={stem.id} style={{ alignItems: 'center' }}>
                    <span style={{ minWidth: 72 }}>{role}</span>
                    <Badge $status={stem.status}>{stem.status}</Badge>
                    <SecondaryButton
                      type="button"
                      disabled={stem.status !== 'complete'}
                      data-testid={`neural-audio-stem-download-${stem.id}`}
                      onClick={() => handleDownloadStem(stem.id)}
                    >
                      Download
                    </SecondaryButton>
                    <SecondaryButton
                      type="button"
                      disabled={!canRender || busy}
                      data-testid={`neural-audio-stem-rerender-${stem.id}`}
                      onClick={() => handleRerenderStem(stemSet.id, stem.id)}
                    >
                      Rerender
                    </SecondaryButton>
                  </Row>
                ))}
                <SecondaryButton
                  type="button"
                  data-testid={`neural-audio-stem-set-delete-${stemSet.id}`}
                  onClick={() => handleDeleteStemSet(stemSet.id)}
                >
                  Delete set
                </SecondaryButton>
              </JobItem>
            );
          })}
        </JobList>
      ) : null}
      <MixAnalysisPanel
        stemSets={stemSets}
        liveFingerprint={liveFingerprint}
        composition={editedMusicJson}
      />
    </Panel>
  );
};

export default NeuralAudioRenderPanel;
