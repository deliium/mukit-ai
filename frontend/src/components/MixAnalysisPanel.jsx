/**
 * Mix Analysis subsection — hosted under NeuralAudioRenderPanel (not a workspace tab).
 * Never mutates stems/V2.
 */

import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  analyzeMix,
  deleteMixAnalysisReport,
  fetchNeuralAudioStemBlob,
  listMixAnalysisReports,
  MixAnalysisApiError,
} from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import { decodeBlobUrlToPeaks } from '../utils/audioWaveformPeaks.js';
import {
  MIX_ANALYSIS_DEFAULT_DIMENSIONS,
  MIX_ANALYSIS_DIMENSIONS,
  MIX_ANALYSIS_HONESTY_COPY,
  formatMixAnalysisLocus,
  normalizeMixAnalysisDimensions,
  observationLocusHighlightRanges,
  partitionMixAnalysisLayers,
} from '../utils/mixAnalysisUi.js';
import { MixAnalysisCharts, MixAnalysisWaveform } from './MixAnalysisCharts.jsx';

const log = createAppLogger('mixAnalysis');

function maxReportDurationSeconds(report) {
  let max = 0;
  for (const m of report?.measurements || []) {
    const end = Number(m?.locus?.end_seconds);
    if (Number.isFinite(end) && end > max) max = end;
  }
  for (const o of report?.observations || []) {
    const end = Number(o?.locus?.end_seconds);
    if (Number.isFinite(end) && end > max) max = end;
  }
  for (const series of report?.series || []) {
    for (const point of series?.points || []) {
      const t = Number(point?.t);
      if (Number.isFinite(t) && t > max) max = t;
    }
  }
  return max > 0 ? max : 1;
}

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

const Select = styled.select`
  min-width: 180px;
  padding: 4px 6px;
  font-size: 0.85rem;
`;

const Button = styled.button`
  padding: 6px 10px;
  font-size: 0.85rem;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  background: #111827;
  color: #fff;
  cursor: pointer;
  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

const SecondaryButton = styled(Button)`
  background: #fff;
  color: #111827;
`;

const LayerBlock = styled.div`
  margin-top: 10px;
`;

const LayerTitle = styled.div`
  font-size: 0.8rem;
  font-weight: 600;
  color: #374151;
  margin-bottom: 4px;
`;

const Finding = styled.div`
  font-size: 0.8rem;
  padding: 6px 0;
  border-bottom: 1px solid #f3f4f6;
`;

const Meta = styled.div`
  color: #6b7280;
  font-size: 0.75rem;
  margin-top: 2px;
`;

/**
 * @param {{
 *   stemSets: object[],
 *   liveFingerprint: string|null,
 *   composition: object|null,
 * }} props
 */
export function MixAnalysisPanel({ stemSets = [], liveFingerprint = null, composition = null }) {
  const currentProjectId = useMusicStore((s) => s.currentProjectId);
  const [stemSetId, setStemSetId] = useState('');
  const [dimensions, setDimensions] = useState(() => [...MIX_ANALYSIS_DEFAULT_DIMENSIONS]);
  const [includeAi, setIncludeAi] = useState(false);
  const [persist, setPersist] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [report, setReport] = useState(null);
  const [savedReports, setSavedReports] = useState([]);
  const [waveformPeaks, setWaveformPeaks] = useState(null);
  const [waveformLoading, setWaveformLoading] = useState(false);

  const completeSets = (Array.isArray(stemSets) ? stemSets : []).filter(
    (s) => s?.status === 'complete',
  );

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
  }, [currentProjectId, report?.report_id]);

  const layers = partitionMixAnalysisLayers(report);

  const toggleDimension = (dim) => {
    setDimensions((prev) => {
      if (prev.includes(dim)) {
        return prev.filter((d) => d !== dim);
      }
      return [...prev, dim];
    });
  };

  const handleAnalyze = async () => {
    if (!stemSetId) return;
    setBusy(true);
    setError(null);
    setWaveformPeaks(null);
    log.info('mixAnalysis start', { stemSetId, persist, includeAi });
    try {
      const payload = {
        project_id: currentProjectId || null,
        stem_set_id: stemSetId,
        dimensions: normalizeMixAnalysisDimensions(dimensions),
        include_ai_interpretation: includeAi,
        persist: Boolean(persist && currentProjectId),
        composition: composition || null,
      };
      const response = await analyzeMix(payload);
      setReport(response?.report || null);
      log.info('mixAnalysis complete', {
        observation_count: response?.report?.observations?.length,
        measurement_count: response?.report?.measurements?.length,
      });
    } catch (err) {
      const code = err instanceof MixAnalysisApiError ? err.code : 'mix_analysis_error';
      setError(err?.message || 'Mix analysis failed');
      log.error('mixAnalysis error', { code });
    } finally {
      setBusy(false);
    }
  };

  const handleLoadWaveform = async () => {
    const stem = selectedSet?.stems?.find((s) => s.status === 'complete' && s.audio_relpath !== undefined)
      || selectedSet?.stems?.find((s) => s.status === 'complete');
    if (!stem?.id) {
      setError('No complete stem available for waveform');
      return;
    }
    setWaveformLoading(true);
    setError(null);
    try {
      const blob = await fetchNeuralAudioStemBlob(stem.id);
      const url = URL.createObjectURL(blob);
      try {
        const result = await decodeBlobUrlToPeaks(url, { maxPeaks: 400 });
        setWaveformPeaks(result?.peaks || null);
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      setError(err?.message || 'Failed to load waveform');
    } finally {
      setWaveformLoading(false);
    }
  };

  const handleDeleteSaved = async (reportId) => {
    try {
      await deleteMixAnalysisReport(reportId);
      setSavedReports((prev) => prev.filter((r) => r.report_id !== reportId));
      if (report?.report_id === reportId) {
        setReport(null);
      }
    } catch (err) {
      setError(err?.message || 'Delete failed');
    }
  };

  return (
    <SubPanel data-testid="mix-analysis-panel">
      <Title>Mix Analysis</Title>
      <Help>{MIX_ANALYSIS_HONESTY_COPY}</Help>
      {completeSets.length === 0 ? (
        <Help>Render a complete stem set first to analyze peaks, loudness, and masking proxies.</Help>
      ) : (
        <>
          <Row>
            <label>
              Stem set
              {' '}
              <Select
                data-testid="mix-analysis-stem-set"
                value={stemSetId}
                onChange={(e) => setStemSetId(e.target.value)}
                disabled={busy}
              >
                {completeSets.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.id.slice(0, 8)}
                    {' '}
                    (
                    {s.stems?.length || 0}
                    {' '}
                    stems)
                  </option>
                ))}
              </Select>
            </label>
            <label style={{ fontSize: '0.8rem' }}>
              <input
                type="checkbox"
                checked={includeAi}
                onChange={(e) => setIncludeAi(e.target.checked)}
                disabled={busy}
                data-testid="mix-analysis-include-ai"
              />
              {' '}
              AI notes (advisory)
            </label>
            <label style={{ fontSize: '0.8rem' }}>
              <input
                type="checkbox"
                checked={persist && Boolean(currentProjectId)}
                onChange={(e) => setPersist(e.target.checked)}
                disabled={busy || !currentProjectId}
                data-testid="mix-analysis-persist"
              />
              {' '}
              Persist report
            </label>
          </Row>
          <Row data-testid="mix-analysis-dimensions">
            {MIX_ANALYSIS_DIMENSIONS.map((dim) => (
              <label key={dim} style={{ fontSize: '0.75rem' }}>
                <input
                  type="checkbox"
                  checked={dimensions.includes(dim)}
                  onChange={() => toggleDimension(dim)}
                  disabled={busy}
                />
                {' '}
                {dim}
              </label>
            ))}
          </Row>
          <Row>
            <Button
              type="button"
              data-testid="mix-analysis-run"
              disabled={busy || !stemSetId}
              onClick={handleAnalyze}
            >
              {busy ? 'Analyzing…' : 'Run mix analysis'}
            </Button>
            <SecondaryButton
              type="button"
              data-testid="mix-analysis-load-waveform"
              disabled={busy || waveformLoading || !stemSetId}
              onClick={handleLoadWaveform}
            >
              {waveformLoading ? 'Loading waveform…' : 'Load waveform'}
            </SecondaryButton>
          </Row>
        </>
      )}
      {error ? (
        <Banner $tone="warn" data-testid="mix-analysis-error">
          {error}
        </Banner>
      ) : null}
      {report ? (
        <>
          <Help>
            backend=
            {report.dsp_backend}
            {' · '}
            measurements=
            {layers.measurements.length}
            {' · '}
            observations=
            {layers.observations.length}
            {' · '}
            AI notes=
            {layers.interpretations.length}
          </Help>
          <MixAnalysisCharts series={report.series} observations={layers.observations} />
          <MixAnalysisWaveform
            peaks={waveformPeaks}
            highlightRanges={observationLocusHighlightRanges(
              layers.observations,
              maxReportDurationSeconds(report),
            )}
          />
          <LayerBlock data-testid="mix-analysis-measurements">
            <LayerTitle>Measurements</LayerTitle>
            {layers.measurements.slice(0, 40).map((m, index) => (
              <Finding key={`m-${m.code}-${index}`}>
                <strong>{m.code}</strong>
                {m.value != null ? ` = ${m.value} ${m.unit || ''}` : ' (unavailable)'}
                <Meta>{formatMixAnalysisLocus(m.locus)}</Meta>
              </Finding>
            ))}
          </LayerBlock>
          <LayerBlock data-testid="mix-analysis-observations">
            <LayerTitle>Observations</LayerTitle>
            {layers.observations.length === 0 ? (
              <Help>No threshold observations for this run.</Help>
            ) : (
              layers.observations.map((o, index) => (
                <Finding key={`o-${o.code}-${index}`}>
                  <strong>{o.code}</strong>
                  {' '}
                  [
                  {o.severity}
                  ]
                  <div>{o.message}</div>
                  <Meta>
                    {formatMixAnalysisLocus(o.locus)}
                    {o.reason ? ` · ${o.reason}` : ''}
                  </Meta>
                  {o.suggested_action ? (
                    <Meta>
                      Suggestion:
                      {' '}
                      {o.suggested_action}
                    </Meta>
                  ) : null}
                </Finding>
              ))
            )}
          </LayerBlock>
          <LayerBlock data-testid="mix-analysis-interpretations">
            <LayerTitle>AI notes (advisory)</LayerTitle>
            {layers.interpretations.length === 0 ? (
              <Help>No AI notes for this run.</Help>
            ) : (
              layers.interpretations.map((item, index) => (
                <Finding key={`i-${index}`}>
                  <div>{item.message}</div>
                  <Meta>{formatMixAnalysisLocus(item.locus)}</Meta>
                </Finding>
              ))
            )}
          </LayerBlock>
        </>
      ) : null}
      {savedReports.length > 0 ? (
        <LayerBlock data-testid="mix-analysis-saved-list">
          <LayerTitle>Saved reports</LayerTitle>
          {savedReports.map((item) => (
            <Row key={item.report_id}>
              <span style={{ fontSize: '0.8rem' }}>
                {item.report_id.slice(0, 8)}
                {' '}
                ·
                {item.observation_count}
                {' '}
                obs ·
                {item.created_at}
              </span>
              <SecondaryButton
                type="button"
                onClick={() => handleDeleteSaved(item.report_id)}
              >
                Delete
              </SecondaryButton>
            </Row>
          ))}
        </LayerBlock>
      ) : null}
    </SubPanel>
  );
}

export default MixAnalysisPanel;
