/**
 * SVG charts for mix.analysis.v1 series + observation locus highlights.
 */

import React, { useMemo } from 'react';
import styled from 'styled-components';
import { createAppLogger } from '../utils/appLogger.js';

const log = createAppLogger('mixAnalysis');

const ChartWrap = styled.div`
  margin-top: 8px;
`;

const ChartLabel = styled.div`
  font-size: 0.75rem;
  color: #6b7280;
  margin-bottom: 4px;
`;

const Svg = styled.svg`
  width: 100%;
  height: 72px;
  background: #f9fafb;
  display: block;
`;

/**
 * @param {{ series: object[], observations?: object[], width?: number }} props
 */
export function MixAnalysisCharts({ series = [], observations = [], width = 480 }) {
  const charts = useMemo(() => {
    const items = Array.isArray(series) ? series.slice(0, 6) : [];
    log.debug('Mix analysis charts series', {
      series_count: items.length,
      point_counts: items.map((s) => s?.points?.length ?? 0),
    });
    return items;
  }, [series]);

  const highlights = useMemo(() => {
    return (Array.isArray(observations) ? observations : [])
      .map((obs) => obs?.locus)
      .filter((locus) => locus && Number.isFinite(Number(locus.start_seconds)));
  }, [observations]);

  if (!charts.length) {
    return null;
  }

  return (
    <ChartWrap data-testid="mix-analysis-charts">
      {charts.map((item) => (
        <SeriesChart
          key={item.id}
          series={item}
          highlights={highlights.filter(
            (h) => !item.stem_id || !h.stem_ids?.length || h.stem_ids.includes(item.stem_id),
          )}
          width={width}
        />
      ))}
    </ChartWrap>
  );
}

function SeriesChart({ series, highlights, width }) {
  const height = 72;
  const points = Array.isArray(series?.points) ? series.points : [];
  if (!points.length) {
    return (
      <div>
      <ChartLabel>
        {series.kind}
        {series.band ? `:${series.band}` : ''}
        {' '}
        (
        {series.stem_id || series.id}
        ) — no points
      </ChartLabel>
      </div>
    );
  }
  const tMax = Math.max(...points.map((p) => Number(p.t) || 0), 0.001);
  const values = points.map((p) => Number(p.v) || 0);
  const vMin = Math.min(...values);
  const vMax = Math.max(...values);
  const span = Math.max(1e-6, vMax - vMin);
  const poly = points
    .map((p, index) => {
      const x = ((Number(p.t) || 0) / tMax) * (width - 8) + 4;
      const y = height - 4 - (((Number(p.v) || 0) - vMin) / span) * (height - 8);
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');

  return (
    <div data-testid={`mix-analysis-chart-${series.id}`}>
      <ChartLabel>
        {series.kind}
        {series.band ? `:${series.band}` : ''}
        {' '}
        ·
        {' '}
        {series.unit}
        {series.stem_id ? ` · ${series.stem_id.slice(0, 8)}` : ''}
      </ChartLabel>
      <Svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        {highlights.map((locus, index) => {
          const start = Number(locus.start_seconds) || 0;
          const end = Number(locus.end_seconds) || start;
          const x1 = (start / tMax) * (width - 8) + 4;
          const x2 = (end / tMax) * (width - 8) + 4;
          return (
            <rect
              key={`hl-${index}`}
              x={Math.min(x1, x2)}
              y={0}
              width={Math.max(2, Math.abs(x2 - x1))}
              height={height}
              fill="rgba(245, 158, 11, 0.25)"
            />
          );
        })}
        <path d={poly} fill="none" stroke="#2563eb" strokeWidth="1.5" />
      </Svg>
    </div>
  );
}

/**
 * Optional user-gated waveform from downloaded stem peaks.
 * @param {{ peaks: Float32Array|number[]|null, highlightRanges?: {start:number,end:number}[] }} props
 */
export function MixAnalysisWaveform({ peaks, highlightRanges = [] }) {
  if (!peaks || !peaks.length) {
    return null;
  }
  const width = 480;
  const height = 48;
  const mid = height / 2;
  const step = Math.max(1, Math.floor(peaks.length / width));
  const path = [];
  for (let i = 0, x = 0; i < peaks.length && x < width; i += step, x += 1) {
    const amp = Math.min(1, Number(peaks[i]) || 0) * (mid - 2);
    path.push(`M${x},${mid - amp} L${x},${mid + amp}`);
  }
  return (
    <ChartWrap data-testid="mix-analysis-waveform">
      <ChartLabel>Waveform (user-loaded)</ChartLabel>
      <Svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none">
        {highlightRanges.map((range, index) => {
          const start = Math.max(0, Math.min(1, Number(range.start) || 0));
          const end = Math.max(0, Math.min(1, Number(range.end) || start));
          const x1 = start * width;
          const x2 = end * width;
          return (
            <rect
              key={`w-hl-${index}`}
              x={Math.min(x1, x2)}
              y={0}
              width={Math.max(2, Math.abs(x2 - x1))}
              height={height}
              fill="rgba(245, 158, 11, 0.2)"
            />
          );
        })}
        <path d={path.join(' ')} stroke="#374151" strokeWidth="1" />
      </Svg>
    </ChartWrap>
  );
}

export default MixAnalysisCharts;
