import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  deleteNeuralAudioRender,
  downloadNeuralAudioRender,
  enqueueNeuralAudioRender,
  fetchAiModels,
  getNeuralAudioRender,
  listNeuralAudioRenders,
  NeuralAudioApiError,
} from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { createAppLogger } from '../utils/appLogger.js';
import {
  neuralAudioFidelityDisclaimer,
  isNeuralAudioDownloadReady,
  isNeuralAudioJobStale,
} from '../utils/neuralAudioRenderUi.js';
import { resolveLiveSnapshotFingerprint } from '../utils/compositionSnapshotFingerprint.js';

const log = createAppLogger('neuralAudioRender');

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
    </Panel>
  );
};

export default NeuralAudioRenderPanel;
