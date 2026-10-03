import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  compareModelLabExperiments,
  createModelLabExperiment,
  evaluateModelLabExperiment,
  getModelLabListening,
  getModelLabMetrics,
  getModelLabStatus,
  listModelLabDatasets,
  listModelLabExperiments,
  listModelLabPresets,
  registerModelLabExperiment,
} from '../api/modelLabApi.js';
import {
  LAB_STAGES,
  buildCreatePayload,
  createEligible,
  formatModelLabRefuseMessage,
  registerEligible,
} from '../utils/modelLabForm.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
`;

const Title = styled.h3`
  margin: 0;
  color: #1e293b;
  font-size: 1.1rem;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #64748b;
  line-height: 1.4;
`;

const StageRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
`;

const StageChip = styled.button`
  min-height: 32px;
  padding: 4px 10px;
  border: 1px solid ${(p) => (p.$active ? '#0f766e' : '#cbd5e1')};
  border-radius: 6px;
  background: ${(p) => (p.$active ? '#ccfbf1' : '#fff')};
  color: #134e4a;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
`;

const Grid = styled.div`
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.8rem;
  color: #475569;
`;

const Input = styled.input`
  min-height: 36px;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
`;

const Select = styled.select`
  min-height: 36px;
  padding: 6px 8px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
`;

const Button = styled.button`
  min-height: 40px;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #334155;
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
`;

const PrimaryButton = styled(Button)`
  border-color: #0f766e;
  background: #ccfbf1;
  color: #134e4a;
`;

const Table = styled.table`
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;

  th,
  td {
    border-bottom: 1px solid #e2e8f0;
    padding: 6px 8px;
    text-align: left;
  }
`;

const Status = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #334155;
`;

const ErrorText = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #b91c1c;
`;

const INITIAL_FORM = {
  displayName: 'TinyLab-v1',
  datasetVersionId: '',
  tokenizerPreset: 'core',
  architecturePreset: 'tiny_lab',
  seed: 42,
  steps: 8,
  batchSize: 2,
  lr: 0.0003,
  device: 'cpu',
  evalEnabled: false,
  listeningEnabled: false,
};

/**
 * Lab tab: dataset → tokenizer → architecture → train → run → eval → registry.
 * Opening the tab only lists catalog/experiments.
 */
export default function ModelLabPanel() {
  const [stage, setStage] = useState('dataset');
  const [status, setStatus] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [presets, setPresets] = useState(null);
  const [experiments, setExperiments] = useState([]);
  const [form, setForm] = useState(INITIAL_FORM);
  const [selectedId, setSelectedId] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [listening, setListening] = useState(null);
  const [compareIds, setCompareIds] = useState([]);
  const [compareReport, setCompareReport] = useState(null);
  const [checkpointStep, setCheckpointStep] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [statusBody, datasetBody, presetBody, experimentBody] = await Promise.all([
          getModelLabStatus(),
          listModelLabDatasets(),
          listModelLabPresets(),
          listModelLabExperiments(),
        ]);
        if (cancelled) return;
        setStatus(statusBody);
        setDatasets(Array.isArray(datasetBody) ? datasetBody : []);
        setPresets(presetBody);
        setExperiments(Array.isArray(experimentBody) ? experimentBody : []);
        const firstDataset = Array.isArray(datasetBody) && datasetBody[0]
          ? datasetBody[0].dataset_version_id
          : '';
        setForm((prev) => ({
          ...prev,
          datasetVersionId: prev.datasetVersionId || firstDataset,
        }));
        console.debug('[ModelLabPanel] catalog loaded', {
          datasetCount: Array.isArray(datasetBody) ? datasetBody.length : 0,
          experimentCount: Array.isArray(experimentBody) ? experimentBody.length : 0,
        });
      } catch (err) {
        if (!cancelled) {
          setError(formatModelLabRefuseMessage(err.code) || err.message);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = experiments.find((item) => item.id === selectedId) || null;

  const refreshExperiments = async () => {
    const listed = await listModelLabExperiments();
    setExperiments(Array.isArray(listed) ? listed : []);
  };

  const onCreate = async () => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const payload = buildCreatePayload(form, status);
      console.debug('[ModelLabPanel] create', { stage, displayName: payload.display_name });
      const created = await createModelLabExperiment(payload);
      setSelectedId(created.id);
      setCheckpointStep(created.checkpoint_refs?.[0]?.step ?? null);
      setMessage(`Experiment ${created.id} → ${created.status}`);
      setStage('evaluation');
      await refreshExperiments();
      const metricBody = await getModelLabMetrics(created.id);
      setMetrics(metricBody);
      setListening(await getModelLabListening(created.id));
    } catch (err) {
      setError(formatModelLabRefuseMessage(err.code) || err.message);
    } finally {
      setBusy(false);
    }
  };

  const onInspect = async (experimentId) => {
    setSelectedId(experimentId);
    setError(null);
    console.debug('[ModelLabPanel] inspect', { experimentId });
    try {
      const [metricBody, listeningBody] = await Promise.all([
        getModelLabMetrics(experimentId),
        getModelLabListening(experimentId),
      ]);
      setMetrics(metricBody);
      setListening(listeningBody);
      const exp = experiments.find((item) => item.id === experimentId);
      setCheckpointStep(exp?.checkpoint_refs?.[0]?.step ?? null);
      setStage('evaluation');
    } catch (err) {
      setError(formatModelLabRefuseMessage(err.code) || err.message);
    }
  };

  const onEvaluate = async () => {
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await evaluateModelLabExperiment(selectedId);
      setMessage('Evaluation updated (symbolic metrics only).');
      await refreshExperiments();
    } catch (err) {
      setError(formatModelLabRefuseMessage(err.code) || err.message);
    } finally {
      setBusy(false);
    }
  };

  const onRegister = async () => {
    if (!selected || !registerEligible(selected, checkpointStep)) return;
    setBusy(true);
    setError(null);
    try {
      const registered = await registerModelLabExperiment(selected.id, {
        checkpoint_step: Number(checkpointStep),
      });
      setMessage(`Registered as ${registered.registry_model_id}`);
      setStage('registry');
      await refreshExperiments();
    } catch (err) {
      setError(formatModelLabRefuseMessage(err.code) || err.message);
    } finally {
      setBusy(false);
    }
  };

  const onCompare = async () => {
    if (compareIds.length < 2) return;
    setBusy(true);
    setError(null);
    try {
      const report = await compareModelLabExperiments(compareIds);
      setCompareReport(report);
      setMessage(`Compared ${report.experiment_ids.length} experiments (no quality winner).`);
    } catch (err) {
      setError(formatModelLabRefuseMessage(err.code) || err.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleCompare = (id) => {
    setCompareIds((prev) => (
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id].slice(0, 8)
    ));
  };

  return (
    <Panel data-testid="model-lab-panel">
      <Title>Model Lab</Title>
      <Hint>
        Research control plane over corpus Music Transformer experiments.
        Opening this tab lists catalog and experiments only — it never starts training
        and never mutates the working score.
      </Hint>
      {status && !status.enabled ? (
        <Hint>
          Model Lab is disabled. Set MODEL_LAB_ENABLED=1 to create experiments.
          Catalog and list GETs still work.
        </Hint>
      ) : null}

      <StageRow>
        {LAB_STAGES.map((id) => (
          <StageChip
            key={id}
            type="button"
            $active={stage === id}
            onClick={() => {
              console.debug('[ModelLabPanel] stage', { stage: id });
              setStage(id);
            }}
          >
            {id}
          </StageChip>
        ))}
      </StageRow>

      {(stage === 'dataset' || stage === 'tokenizer' || stage === 'architecture' || stage === 'training' || stage === 'run') ? (
        <Grid>
          <Field>
            Display name
            <Input
              value={form.displayName}
              onChange={(e) => setForm((prev) => ({ ...prev, displayName: e.target.value }))}
            />
          </Field>
          <Field>
            Dataset version
            <Select
              value={form.datasetVersionId}
              onChange={(e) => setForm((prev) => ({ ...prev, datasetVersionId: e.target.value }))}
            >
              <option value="">Select…</option>
              {datasets.map((item) => (
                <option key={item.dataset_version_id} value={item.dataset_version_id}>
                  {item.dataset_name} / {item.dataset_version_id}
                  {item.has_rights_index ? '' : ' (no rights)'}
                </option>
              ))}
            </Select>
          </Field>
          <Field>
            Tokenizer preset
            <Select
              value={form.tokenizerPreset}
              onChange={(e) => setForm((prev) => ({ ...prev, tokenizerPreset: e.target.value }))}
            >
              {(presets?.tokenizer || [{ preset_id: 'core' }, { preset_id: 'core_harmony' }]).map((item) => (
                <option key={item.preset_id} value={item.preset_id}>{item.preset_id}</option>
              ))}
            </Select>
          </Field>
          <Field>
            Architecture preset
            <Select
              value={form.architecturePreset}
              onChange={(e) => setForm((prev) => ({ ...prev, architecturePreset: e.target.value }))}
            >
              {(presets?.architecture || [{ preset_id: 'tiny_lab' }, { preset_id: 'small_lab' }]).map((item) => (
                <option key={item.preset_id} value={item.preset_id}>{item.preset_id}</option>
              ))}
            </Select>
          </Field>
          <Field>
            Seed
            <Input
              type="number"
              value={form.seed}
              onChange={(e) => setForm((prev) => ({ ...prev, seed: e.target.value }))}
            />
          </Field>
          <Field>
            Steps
            <Input
              type="number"
              value={form.steps}
              onChange={(e) => setForm((prev) => ({ ...prev, steps: e.target.value }))}
            />
          </Field>
          <Field>
            Batch size
            <Input
              type="number"
              value={form.batchSize}
              onChange={(e) => setForm((prev) => ({ ...prev, batchSize: e.target.value }))}
            />
          </Field>
        </Grid>
      ) : null}

      <StageRow>
        <PrimaryButton
          type="button"
          disabled={busy || !createEligible(form, status)}
          onClick={onCreate}
        >
          Run experiment
        </PrimaryButton>
        <Button type="button" disabled={busy || !selectedId} onClick={onEvaluate}>
          Evaluate
        </Button>
        <Button
          type="button"
          disabled={busy || !registerEligible(selected, checkpointStep)}
          onClick={onRegister}
        >
          Register checkpoint
        </Button>
        <Button type="button" disabled={busy || compareIds.length < 2} onClick={onCompare}>
          Compare selected
        </Button>
      </StageRow>

      {error ? <ErrorText>{error}</ErrorText> : null}
      {message ? <Status>{message}</Status> : null}

      <Title as="h4">Experiments</Title>
      <Table>
        <thead>
          <tr>
            <th>Compare</th>
            <th>Name</th>
            <th>Status</th>
            <th>Seed</th>
            <th>Registry</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {experiments.map((item) => (
            <tr key={item.id}>
              <td>
                <input
                  type="checkbox"
                  checked={compareIds.includes(item.id)}
                  onChange={() => toggleCompare(item.id)}
                  aria-label={`Compare ${item.display_name}`}
                />
              </td>
              <td>{item.display_name}</td>
              <td>{item.status}</td>
              <td>{item.seed}</td>
              <td>{item.registry_model_id || '—'}</td>
              <td>
                <Button type="button" onClick={() => onInspect(item.id)}>
                  Inspect
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>

      {selected ? (
        <>
          <Title as="h4">Selected: {selected.display_name}</Title>
          <Hint>
            Runtime: {selected.runtime?.device || '—'} · wall_ms={selected.runtime?.wall_ms ?? '—'}
            · evaluation={selected.evaluation_version || '—'}
          </Hint>
          {selected.checkpoint_refs?.length ? (
            <Field>
              Checkpoint step
              <Select
                value={checkpointStep ?? ''}
                onChange={(e) => setCheckpointStep(e.target.value)}
              >
                {selected.checkpoint_refs.map((ref) => (
                  <option key={ref.step} value={ref.step}>
                    step {ref.step}{ref.card_present ? ' (card)' : ''}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
        </>
      ) : null}

      {metrics?.rows?.length ? (
        <>
          <Title as="h4">Metrics</Title>
          <Hint>Symbolic metrics only — not musical quality.</Hint>
          <Table>
            <thead>
              <tr>
                <th>Step</th>
                <th>Loss</th>
                <th>Val loss</th>
                <th>Token acc</th>
              </tr>
            </thead>
            <tbody>
              {metrics.rows.slice(0, 20).map((row) => (
                <tr key={row.step}>
                  <td>{row.step}</td>
                  <td>{row.loss ?? '—'}</td>
                  <td>{row.val_loss ?? '—'}</td>
                  <td>{row.token_accuracy ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      ) : null}

      {listening?.prompts?.length ? (
        <>
          <Title as="h4">Listening digests</Title>
          <Table>
            <thead>
              <tr>
                <th>Prompt</th>
                <th>Digest</th>
              </tr>
            </thead>
            <tbody>
              {listening.prompts.map((prompt) => (
                <tr key={prompt.id}>
                  <td>{prompt.id}</td>
                  <td>{prompt.generation_digest || '—'}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      ) : null}

      {compareReport ? (
        <>
          <Title as="h4">Compare report</Title>
          <Hint>
            tokenizer_equal={String(compareReport.tokenizer_equal)} ·
            architecture_equal={String(compareReport.architecture_equal)} ·
            musical_quality_claim={String(compareReport.musical_quality_claim)}
          </Hint>
          <pre style={{ fontSize: '0.75rem', overflow: 'auto' }}>
            {JSON.stringify(compareReport.metric_deltas || {}, null, 2)}
          </pre>
        </>
      ) : null}
    </Panel>
  );
}
