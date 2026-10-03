/**
 * Mix assist subsection — preview, compare, apply, reject, and undo.
 * Hosted under NeuralAudioRenderPanel. Local state only; not composition autosave.
 */

import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  MixPlanApiError,
  applyMixPlan,
  fetchMixPlanPreviewAudio,
  listMixAnalysisReports,
  listMixPlanRevisions,
  previewMixPlan,
  rejectMixPlanPreview,
  undoMixPlanRevision,
} from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';
import {
  MIX_PLAN_MASTER_TARGETS,
  MIX_PLAN_NOT_A_GUARANTEE,
  editMixPlanAfter,
  formatMixPlanChange,
  isMixPlanStale,
  mixAssistPreviewRequest,
} from '../utils/mixPlanUi.js';
import ContentProvenanceInspect from './ContentProvenanceInspect.jsx';

const SubPanel = styled.div`
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px dashed #d1d5db;
`;

const Title = styled.h4`
  margin: 0 0 6px;
  font-size: 0.95rem;
  font-weight: 600;
  color: #111827;
`;

const Help = styled.p`
  margin: 0 0 8px;
  font-size: 0.8rem;
  color: #6b7280;
  line-height: 1.35;
`;

const Banner = styled.div`
  margin: 8px 0;
  padding: 8px 10px;
  border-radius: 4px;
  font-size: 0.8rem;
  background: ${(p) => (p.$tone === 'warn' ? '#fef3c7' : '#eff6ff')};
  color: ${(p) => (p.$tone === 'warn' ? '#92400e' : '#1e3a8a')};
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
`;

const Button = styled.button`
  padding: 6px 10px;
  font-size: 0.85rem;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
`;

const Table = styled.table`
  width: 100%;
  border-collapse: collapse;
  font-size: 0.78rem;
  margin: 8px 0;
  th, td {
    text-align: left;
    padding: 4px 6px;
    border-bottom: 1px solid #e5e7eb;
  }
`;

const Phrase = styled.input`
  flex: 1;
  min-width: 220px;
  padding: 6px 8px;
  font-size: 0.85rem;
`;

const SUGGESTIONS = Object.freeze([
  'make bass less dominant',
  'Make the strings less dominant.',
  'Give the piano more space.',
  'Make the climax wider.',
  'Reduce low-frequency masking.',
]);

export function MixAssistPanel({
  stemSets = [],
  liveFingerprint = null,
  sessionReportId = null,
}) {
  const currentProjectId = useMusicStore((s) => s.currentProjectId);
  const [stemSetId, setStemSetId] = useState('');
  const [phrase, setPhrase] = useState('make bass less dominant');
  const [reportId, setReportId] = useState('');
  const [savedReports, setSavedReports] = useState([]);
  const [masterTarget, setMasterTarget] = useState('dynamic');
  const [preview, setPreview] = useState(null);
  const [plan, setPlan] = useState(null);
  const [headId, setHeadId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [audioUrls, setAudioUrls] = useState({ dry: null, processed: null });

  const completeSets = (Array.isArray(stemSets) ? stemSets : []).filter(
    (set) => set?.status === 'complete',
  );
  const selectedSet = completeSets.find((set) => set.id === stemSetId) || null;

  useEffect(() => {
    if (!stemSetId && completeSets[0]?.id) {
      setStemSetId(completeSets[0].id);
    }
  }, [completeSets, stemSetId]);

  useEffect(() => {
    if (!currentProjectId) {
      setSavedReports([]);
      return undefined;
    }
    let cancelled = false;
    listMixAnalysisReports(currentProjectId)
      .then((response) => {
        if (!cancelled) {
          setSavedReports(Array.isArray(response?.items) ? response.items : []);
        }
      })
      .catch(() => {
        if (!cancelled) setSavedReports([]);
      });
    return () => {
      cancelled = true;
    };
  }, [currentProjectId, sessionReportId]);

  const savedOtherReports = savedReports.filter((item) => item.report_id !== sessionReportId);
  const reportChoices = [
    ...(sessionReportId ? [sessionReportId] : []),
    ...savedOtherReports.map((item) => item.report_id),
  ];
  const selectedReportId = reportChoices.includes(reportId) ? reportId : '';

  useEffect(() => () => {
    if (audioUrls.dry) URL.revokeObjectURL(audioUrls.dry);
    if (audioUrls.processed) URL.revokeObjectURL(audioUrls.processed);
  }, [audioUrls.dry, audioUrls.processed]);

  const stale = isMixPlanStale(plan, {
    liveFingerprint,
    stemSetFingerprint: selectedSet?.source_fingerprint,
  });

  async function runPreviewRequest(body) {
    if (!currentProjectId || !stemSetId) {
      setError('A saved project and a completed stem set are required.');
      return;
    }
    console.debug('[MixAssist] preview', {
      report_id: body.report_id || null,
      observation_code_count: Array.isArray(body.observation_codes) ? body.observation_codes.length : 0,
    });
    setBusy(true);
    setError(null);
    try {
      const response = await previewMixPlan(body);
      setPreview(response);
      setPlan(response.plan);
      await loadAudio(response.preview_id, response.dry_audio, response.processed_audio);
    } catch (err) {
      setError(err instanceof MixPlanApiError ? `${err.code}: ${err.message}` : 'Preview failed');
    } finally {
      setBusy(false);
    }
  }

  function phraseRequest(nextPhrase = phrase) {
    return mixAssistPreviewRequest({
      projectId: currentProjectId,
      stemSetId,
      masterTarget,
      phrase: nextPhrase,
      reportId: selectedReportId || null,
      includeAudioPreview: true,
    });
  }

  function runPreview(nextPhrase = phrase) {
    return runPreviewRequest(phraseRequest(nextPhrase));
  }

  function runSectionPreview() {
    return runPreviewRequest(mixAssistPreviewRequest({
      projectId: currentProjectId,
      stemSetId,
      masterTarget,
      reportId: selectedReportId || null,
      observationCodes: ['section_loudness_flat'],
      includeAudioPreview: true,
    }));
  }

  async function loadAudio(previewId, hasDry, hasProcessed) {
    const next = { dry: null, processed: null };
    if (hasDry) {
      const blob = await fetchMixPlanPreviewAudio(previewId, 'dry');
      next.dry = URL.createObjectURL(blob);
    }
    if (hasProcessed) {
      const blob = await fetchMixPlanPreviewAudio(previewId, 'processed');
      next.processed = URL.createObjectURL(blob);
    }
    setAudioUrls(next);
  }

  async function onReject() {
    if (!preview?.preview_id) return;
    setBusy(true);
    setError(null);
    try {
      await rejectMixPlanPreview(preview.preview_id);
      setPreview(null);
      setPlan(null);
      setAudioUrls({ dry: null, processed: null });
    } catch (err) {
      setError(err instanceof MixPlanApiError ? `${err.code}: ${err.message}` : 'Reject failed');
    } finally {
      setBusy(false);
    }
  }

  async function onApply() {
    if (!preview || !plan) return;
    setBusy(true);
    setError(null);
    try {
      const body = await applyMixPlan({
        preview_id: preview.preview_id,
        digest: preview.digest,
        plan,
      });
      setHeadId(body.head_revision_id);
      setPreview(null);
      setAudioUrls({ dry: null, processed: null });
    } catch (err) {
      setError(err instanceof MixPlanApiError ? `${err.code}: ${err.message}` : 'Apply failed');
    } finally {
      setBusy(false);
    }
  }

  async function onUndo() {
    setBusy(true);
    setError(null);
    try {
      let revisionId = headId;
      if (!revisionId && currentProjectId && stemSetId) {
        const listed = await listMixPlanRevisions(currentProjectId, stemSetId);
        revisionId = (listed?.items || []).find((item) => item.is_head)?.id || null;
      }
      if (!revisionId) {
        setError('mix_plan_undo_empty: No applied mix head to undo');
        return;
      }
      const body = await undoMixPlanRevision(revisionId);
      setHeadId(body.head_revision_id);
    } catch (err) {
      setError(err instanceof MixPlanApiError ? `${err.code}: ${err.message}` : 'Undo failed');
    } finally {
      setBusy(false);
    }
  }

  const target = MIX_PLAN_MASTER_TARGETS.find((item) => item.id === masterTarget);

  return (
    <SubPanel data-testid="mix-assist-panel">
      <Title>Mix assist</Title>
      <Help>
        Preview parameter changes for a completed stem set, compare before and after,
        then apply a new mix revision. Original stems stay unchanged. {MIX_PLAN_NOT_A_GUARANTEE}.
      </Help>
      {completeSets.length === 0 ? (
        <Banner>A completed stem set is required.</Banner>
      ) : (
        <>
          <Row>
            <label htmlFor="mix-assist-stem-set">Stem set</label>
            <select
              id="mix-assist-stem-set"
              value={stemSetId}
              onChange={(event) => {
                setStemSetId(event.target.value);
                setPreview(null);
                setPlan(null);
              }}
            >
              {completeSets.map((set) => (
                <option key={set.id} value={set.id}>{set.id.slice(0, 8)}</option>
              ))}
            </select>
          </Row>
          <Row>
            <label htmlFor="mix-assist-report">Analysis report</label>
            <select
              id="mix-assist-report"
              value={selectedReportId}
              onChange={(event) => {
                setReportId(event.target.value);
                setPreview(null);
                setPlan(null);
              }}
            >
              <option value="">No analysis report</option>
              {sessionReportId ? (
                <option value={sessionReportId}>Session report</option>
              ) : null}
              {savedOtherReports.map((item) => (
                <option key={item.report_id} value={item.report_id}>
                  {item.report_id.slice(0, 8)}
                </option>
              ))}
            </select>
          </Row>
          {!sessionReportId && savedReports.length === 0 ? (
            <Help>Analysis suggestions need a saved or session report.</Help>
          ) : null}
          <Row>
            <Button
              type="button"
              disabled={busy || !selectedReportId}
              onClick={() => void runSectionPreview()}
            >
              Section loudness
            </Button>
            {SUGGESTIONS.map((text) => (
              <Button
                key={text}
                type="button"
                disabled={busy}
                onClick={() => {
                  setPhrase(text);
                  void runPreview(text);
                }}
              >
                {text}
              </Button>
            ))}
          </Row>
          <Row>
            <Phrase
              aria-label="Mix request"
              value={phrase}
              onChange={(event) => setPhrase(event.target.value)}
            />
            <Button type="button" disabled={busy || !currentProjectId} onClick={() => void runPreview()}>
              {busy ? 'Working…' : 'Preview'}
            </Button>
          </Row>
          {!currentProjectId ? <Banner>Open a project before applying a mix plan.</Banner> : null}
          <Row>
            {MIX_PLAN_MASTER_TARGETS.map((item) => (
              <label key={item.id}>
                <input
                  type="radio"
                  name="mix-master-target"
                  checked={masterTarget === item.id}
                  onChange={() => {
                    setMasterTarget(item.id);
                    setPreview(null);
                  }}
                />
                {' '}
                {item.label}
              </label>
            ))}
          </Row>
          {target ? (
            <Help>
              {target.goal} {MIX_PLAN_NOT_A_GUARANTEE}.
            </Help>
          ) : null}
          {stale ? (
            <Banner $tone="warn" data-testid="mix-assist-stale">
              The stem set moved on since this plan. Preview again before applying.
            </Banner>
          ) : null}
          {error ? <Banner $tone="warn">{error}</Banner> : null}
          {plan ? (
            <Table>
              <thead>
                <tr>
                  <th>Change</th>
                  <th>After</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {(plan.changes || []).map((change) => (
                  <tr key={change.op_id}>
                    <td>{formatMixPlanChange(change)}</td>
                    <td>
                      <input
                        aria-label={`after ${change.op_id}`}
                        type="number"
                        step="0.1"
                        value={change.after}
                        onChange={(event) => {
                          setPlan(editMixPlanAfter(plan, change.op_id, event.target.value));
                        }}
                      />
                    </td>
                    <td>{change.reason}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : null}
          {(plan?.warnings || []).map((warning) => (
            <Help key={warning.code}>{warning.message}</Help>
          ))}
          <Row>
            {audioUrls.dry ? (
              <div>
                Before
                <audio controls src={audioUrls.dry} data-testid="mix-assist-audio-dry" />
              </div>
            ) : null}
            {audioUrls.processed ? (
              <div>
                After
                <audio controls src={audioUrls.processed} data-testid="mix-assist-audio-processed" />
              </div>
            ) : null}
          </Row>
          <Row>
            <Button type="button" disabled={busy || !preview} onClick={() => void onApply()}>
              Apply
            </Button>
            <Button type="button" disabled={busy || !preview} onClick={() => void onReject()}>
              Reject
            </Button>
            <Button type="button" disabled={busy} onClick={() => void onUndo()}>
              Undo
            </Button>
            {headId ? <Help>Head {headId.slice(0, 8)}</Help> : null}
          </Row>
          {headId && currentProjectId ? (
            <ContentProvenanceInspect
              projectId={currentProjectId}
              artifactKind="mix_plan_revision"
              artifactId={headId}
              testIdPrefix="mix-assist-provenance"
            />
          ) : null}
        </>
      )}
    </SubPanel>
  );
}

export default MixAssistPanel;
