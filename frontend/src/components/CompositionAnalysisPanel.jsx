import React, { useEffect, useMemo, useRef } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { listAnalysisSectionOptions } from '../utils/compositionAnalysis.js';

const Root = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;
`;

const HeaderRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
`;

const Title = styled.h4`
  margin: 0;
  color: #312e81;
  font-size: 1rem;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #64748b;
  line-height: 1.4;
`;

const Controls = styled.div`
  display: grid;
  gap: 10px;
  min-width: 0;

  @media (min-width: 701px) {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
  }
`;

const ScopeGroup = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
`;

const ScopeButton = styled.button`
  min-height: 44px;
  padding: 8px 14px;
  border: 1px solid ${(props) => (props.$active ? '#4f46e5' : '#cbd5e1')};
  border-radius: 8px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#fff')};
  color: ${(props) => (props.$active ? '#312e81' : '#334155')};
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  touch-action: manipulation;

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  font-size: 0.85rem;
  color: #374151;
`;

const Select = styled.select`
  min-height: 44px;
  min-width: 0;
  max-width: 100%;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  background: white;
  font-size: 0.9rem;
  box-sizing: border-box;
`;

const ActionRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  min-height: 44px;
  padding: 8px 14px;
  border: none;
  border-radius: 8px;
  background: ${(props) => (props.$secondary ? '#e2e8f0' : '#4f46e5')};
  color: ${(props) => (props.$secondary ? '#1e293b' : '#fff')};
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  touch-action: manipulation;

  &:disabled {
    background: #d1d5db;
    color: #64748b;
    cursor: not-allowed;
  }
`;

const StatusBanner = styled.div`
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid ${(props) => {
    if (props.$tone === 'error') return '#fecaca';
    if (props.$tone === 'warn') return '#fde68a';
    if (props.$tone === 'ok') return '#bbf7d0';
    return '#c7d2fe';
  }};
  background: ${(props) => {
    if (props.$tone === 'error') return '#fef2f2';
    if (props.$tone === 'warn') return '#fffbeb';
    if (props.$tone === 'ok') return '#f0fdf4';
    return '#eef2ff';
  }};
  color: ${(props) => {
    if (props.$tone === 'error') return '#991b1b';
    if (props.$tone === 'warn') return '#92400e';
    if (props.$tone === 'ok') return '#166534';
    return '#1e3a8a';
  }};
  font-size: 0.9rem;
  line-height: 1.4;
  overflow-wrap: anywhere;
  word-break: break-word;
`;

const ScopeSummary = styled.div`
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #fff;
  font-size: 0.85rem;
  color: #334155;
  min-width: 0;
  overflow-wrap: anywhere;
`;

const MetricGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr));
  gap: 10px;
  min-width: 0;
`;

const Card = styled.article`
  min-width: 0;
  padding: 12px;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
  background: ${(props) => (props.$stale ? '#f8fafc' : '#ffffff')};
  opacity: ${(props) => (props.$stale ? 0.92 : 1)};
`;

const CardTitle = styled.h5`
  margin: 0 0 8px;
  font-size: 0.9rem;
  font-weight: 600;
  color: #312e81;
`;

const MetaLine = styled.div`
  margin-top: 6px;
  font-size: 0.75rem;
  color: #64748b;
  line-height: 1.35;
  overflow-wrap: anywhere;
`;

const Dl = styled.dl`
  margin: 0;
  display: grid;
  gap: 6px;
`;

const Row = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
  gap: 8px;
  font-size: 0.85rem;
  min-width: 0;

  dt {
    margin: 0;
    color: #64748b;
    overflow-wrap: anywhere;
  }

  dd {
    margin: 0;
    color: #1e293b;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
`;

const WarningList = styled.ul`
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  min-width: 0;
`;

const WarningItem = styled.li`
  min-width: 0;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid ${(props) => {
    if (props.$severity === 'error') return '#fecaca';
    if (props.$severity === 'info') return '#bfdbfe';
    return '#fde68a';
  }};
  background: ${(props) => {
    if (props.$severity === 'error') return '#fef2f2';
    if (props.$severity === 'info') return '#eff6ff';
    return '#fffbeb';
  }};
  font-size: 0.85rem;
  color: #334155;
  overflow-wrap: anywhere;
  word-break: break-word;
`;

const Loader = styled.div`
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.9rem;
  color: #1e3a8a;
`;

const Spinner = styled.span`
  width: 1rem;
  height: 1rem;
  border: 2px solid #c7d2fe;
  border-top-color: #4f46e5;
  border-radius: 50%;
  animation: analysis-spin 0.8s linear infinite;

  @keyframes analysis-spin {
    to {
      transform: rotate(360deg);
    }
  }
`;

function formatNumber(value, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) {
    return '—';
  }
  return Number(value).toFixed(digits);
}

function formatConfidence(inference) {
  if (!inference || typeof inference !== 'object') {
    return null;
  }
  const parts = [];
  if (typeof inference.status === 'string' && inference.status) {
    parts.push(inference.status.replaceAll('_', ' '));
  }
  if (inference.confidence != null && Number.isFinite(Number(inference.confidence))) {
    parts.push(`confidence ${formatNumber(inference.confidence, 2)}`);
  }
  return parts.length ? parts.join(' · ') : null;
}

function formatDeclaredInferred(declared, inferred, effective = null) {
  const lines = [];
  if (declared != null && declared !== '') {
    lines.push(`Declared: ${declared}`);
  }
  if (inferred != null && inferred !== '') {
    lines.push(`Inferred: ${inferred}`);
  }
  if (effective != null && effective !== '') {
    lines.push(`Effective: ${effective}`);
  }
  return lines.length ? lines.join(' · ') : 'No accepted value';
}

function formatResolvedScope(scope) {
  if (!scope || typeof scope !== 'object') {
    return 'Scope not resolved yet';
  }
  const kind = scope.kind || 'unknown';
  const bars = Number.isFinite(Number(scope.start_bar))
    && Number.isFinite(Number(scope.end_bar_exclusive))
    ? `bars ${scope.start_bar}–${Number(scope.end_bar_exclusive) - 1}`
    : null;
  const ticks = Number.isFinite(Number(scope.start_tick))
    && Number.isFinite(Number(scope.end_tick))
    ? `ticks ${scope.start_tick}–${scope.end_tick}`
    : null;
  const extras = [];
  if (kind === 'track' && scope.track_id) {
    extras.push(`track ${scope.track_id}`);
  }
  if (kind === 'section') {
    if (scope.section_id) {
      extras.push(`section ${scope.section_id}`);
    } else if (scope.section_index != null) {
      extras.push(`section index ${scope.section_index}`);
    }
  }
  return [kind, bars, ticks, ...extras].filter(Boolean).join(' · ');
}

function disambiguatedSectionOptions(composition) {
  const options = listAnalysisSectionOptions(composition);
  const labelCounts = new Map();
  for (const option of options) {
    labelCounts.set(option.label, (labelCounts.get(option.label) || 0) + 1);
  }
  return options.map((option) => ({
    ...option,
    displayLabel: labelCounts.get(option.label) > 1
      ? `${option.label} · #${option.index}`
      : option.label,
  }));
}

function MetricCard({ title, stale, children, inference, testId }) {
  const meta = formatConfidence(inference);
  return (
    <Card $stale={stale} data-testid={testId}>
      <CardTitle>{title}</CardTitle>
      {children}
      {meta ? <MetaLine>Inference: {meta}</MetaLine> : null}
    </Card>
  );
}

function DefinitionRows({ rows }) {
  return (
    <Dl>
      {rows.map(([term, value]) => (
        <Row key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </Row>
      ))}
    </Dl>
  );
}

/**
 * Analysis tab panel: scoped composition.analysis.v1 results with freshness UX.
 * Does not reuse global generation errors; analysis lifecycle is store-local.
 */
const CompositionAnalysisPanel = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const compositionRevision = useMusicStore((state) => state.compositionRevision);
  const analysisScope = useMusicStore((state) => state.analysisScope);
  const analysisSelectedSectionKey = useMusicStore((state) => state.analysisSelectedSectionKey);
  const analysisResult = useMusicStore((state) => state.analysisResult);
  const analysisStatus = useMusicStore((state) => state.analysisStatus);
  const analysisError = useMusicStore((state) => state.analysisError);
  const analysisWarnings = useMusicStore((state) => state.analysisWarnings);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);
  const setAnalysisScope = useMusicStore((state) => state.setAnalysisScope);
  const selectAnalysisSection = useMusicStore((state) => state.selectAnalysisSection);
  const selectPianoRollTrack = useMusicStore((state) => state.selectPianoRollTrack);
  const refreshAnalysis = useMusicStore((state) => state.refreshAnalysis);
  const retryAnalysis = useMusicStore((state) => state.retryAnalysis);
  const resetAnalysis = useMusicStore((state) => state.resetAnalysis);
  const getAnalysisFreshness = useMusicStore((state) => state.getAnalysisFreshness);

  const previousStatusRef = useRef(analysisStatus);
  const freshness = getAnalysisFreshness();
  const validation = useMemo(
    () => (editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false, message: 'No composition' }),
    [editedMusicJson],
  );
  const canonical = isCanonicalComposition(editedMusicJson);
  const compositionInvalid = !editedMusicJson || !canonical || !validation.valid;
  const sectionOptions = useMemo(
    () => disambiguatedSectionOptions(editedMusicJson),
    [editedMusicJson],
  );
  const tracks = Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : [];
  const selectedTrack = tracks.find((track) => String(track.id) === String(pianoRollTrackId)) || null;
  const warnings = Array.isArray(analysisWarnings) && analysisWarnings.length
    ? analysisWarnings
    : (Array.isArray(analysisResult?.warnings) ? analysisResult.warnings : []);
  const staleCards = Boolean(freshness.hasResult && (freshness.isStale || freshness.isLoading));
  const isBusy = analysisStatus === 'loading';
  const showInitialLoader = isBusy && !freshness.hasResult;
  const invalidCompositionError = compositionInvalid
    || (typeof analysisError === 'string'
      && /invalid.?composition|canonical composition/i.test(analysisError));

  useEffect(() => {
    const previous = previousStatusRef.current;
    if (previous !== analysisStatus) {
      console.debug('[CompositionAnalysisPanel] Status transition', {
        previous,
        next: analysisStatus,
        hasResult: freshness.hasResult,
        isCurrent: freshness.isCurrent,
        isStale: freshness.isStale,
        warningCount: warnings.length,
        hasRevision: Boolean(compositionRevision),
      });
      previousStatusRef.current = analysisStatus;
    }
  }, [
    analysisStatus,
    compositionRevision,
    freshness.hasResult,
    freshness.isCurrent,
    freshness.isStale,
    warnings.length,
  ]);

  useEffect(() => {
    if (!analysisResult) {
      return;
    }
    const missing = [];
    for (const key of [
      'tonality',
      'harmony',
      'melody',
      'density',
      'roles',
      'repetition',
      'tension',
      'resolved_scope',
    ]) {
      if (!analysisResult[key] || typeof analysisResult[key] !== 'object') {
        missing.push(key);
      }
    }
    if (missing.length) {
      console.warn('[CompositionAnalysisPanel] Render-contract fallback', {
        code: 'analysis_render_missing_groups',
        missingGroupCount: missing.length,
        missingGroups: missing,
      });
    }
  }, [analysisResult]);

  const uiPhase = (() => {
    if (compositionInvalid) {
      return { label: 'Invalid composition', tone: 'error' };
    }
    if (analysisStatus === 'error') {
      return { label: 'Analysis failed', tone: 'error' };
    }
    if (isBusy && freshness.hasResult) {
      return { label: 'Updating analysis…', tone: 'info' };
    }
    if (isBusy) {
      return { label: 'Analyzing composition…', tone: 'info' };
    }
    if (freshness.isStale) {
      return { label: 'Results out of date', tone: 'warn' };
    }
    if (freshness.isCurrent) {
      return { label: 'Results current', tone: 'ok' };
    }
    if (analysisStatus === 'idle') {
      return { label: 'Waiting for analysis', tone: 'info' };
    }
    return { label: 'Analysis ready', tone: 'info' };
  })();

  const onScopeChange = (kind) => {
    console.debug('[CompositionAnalysisPanel] Scope control', {
      kind,
      sectionSelected: Boolean(analysisSelectedSectionKey),
      hasTrackId: Boolean(pianoRollTrackId),
      sectionCount: sectionOptions.length,
      trackCount: tracks.length,
    });
    setAnalysisScope(kind);
  };

  const onRefresh = () => {
    console.info('[CompositionAnalysisPanel] Manual refresh', {
      scopeKind: analysisScope,
      hasTrackId: Boolean(pianoRollTrackId),
      sectionSelected: Boolean(analysisSelectedSectionKey),
    });
    refreshAnalysis();
  };

  const onRetry = () => {
    console.info('[CompositionAnalysisPanel] Manual retry', {
      scopeKind: analysisScope,
      hasError: Boolean(analysisError),
    });
    retryAnalysis();
  };

  const onClearInvalid = () => {
    console.debug('[CompositionAnalysisPanel] Clearing invalid-composition analysis state', {
      hasResult: freshness.hasResult,
    });
    resetAnalysis();
  };

  const tonality = analysisResult?.tonality;
  const harmony = analysisResult?.harmony;
  const melody = analysisResult?.melody;
  const density = analysisResult?.density;
  const roles = analysisResult?.roles;
  const repetition = analysisResult?.repetition;
  const tension = analysisResult?.tension;
  const primaryProfile = Array.isArray(melody?.profiles) ? melody.profiles[0] : null;
  const chordSummary = Array.isArray(harmony?.spans) && harmony.spans.length
    ? harmony.spans
      .slice(0, 6)
      .map((span) => span.symbol || span.roman || span.quality || '—')
      .join(', ')
    : 'No inferred chords';
  const localModulation = Array.isArray(tonality?.local_spans) && tonality.local_spans.length > 1
    ? tonality.local_spans
      .slice(0, 5)
      .map((span) => {
        const key = span.key || 'ambiguous';
        const endBar = Number.isFinite(Number(span.end_bar_exclusive))
          ? Number(span.end_bar_exclusive) - 1
          : '?';
        return `${key} (bars ${span.start_bar}–${endBar})`;
      })
      .join('; ')
    : (tonality?.local_spans?.[0]?.key
      ? `Local: ${tonality.local_spans[0].key}`
      : 'No local modulation spans');
  const cadenceSummary = Array.isArray(melody?.cadences) && melody.cadences.length
    ? melody.cadences
      .slice(0, 5)
      .map((item) => `${String(item.kind || 'unclassified').replaceAll('_', ' ')}${item.bar != null ? ` @ bar ${item.bar}` : ''}`)
      .join('; ')
    : 'No cadences reported';
  const phraseCount = Array.isArray(melody?.phrases) ? melody.phrases.length : 0;
  const roleRows = Array.isArray(roles?.tracks)
    ? roles.tracks.slice(0, 6).map((track) => (
      `${track.track_id}: ${formatDeclaredInferred(track.declared_role, track.inferred_role, track.effective_role)}`
    ))
    : [];
  const motifSummary = Array.isArray(repetition?.motifs) && repetition.motifs.length
    ? `${repetition.motifs.length} motif(s); kinds: ${[...new Set(repetition.motifs.map((m) => m.kind))].join(', ')}`
    : 'No repeated motifs reported';

  if (compositionInvalid) {
    return (
      <Root data-testid="composition-analysis-panel" aria-busy="false">
        <HeaderRow>
          <Title>Composition analysis</Title>
        </HeaderRow>
        <Hint>
          Analysis is a derived report over canonical composition.v2 events. It is not playable
          authority and does not modify the composition.
        </Hint>
        <StatusBanner $tone="error" role="alert" data-testid="analysis-invalid-composition">
          {validation.message || analysisError || 'Canonical composition.v2 is required for analysis.'}
        </StatusBanner>
        <ActionRow>
          <Button type="button" $secondary onClick={onClearInvalid} data-testid="analysis-clear-invalid">
            Clear analysis state
          </Button>
        </ActionRow>
      </Root>
    );
  }

  return (
    <Root
      data-testid="composition-analysis-panel"
      aria-busy={isBusy}
    >
      <HeaderRow>
        <div>
          <Title>Composition analysis</Title>
          <Hint>
            Deterministic sidecar over track events. Labels show declared vs inferred values;
            confidence reflects evidence strength, not musical certainty.
          </Hint>
        </div>
        <ActionRow>
          <Button
            type="button"
            onClick={onRefresh}
            disabled={isBusy}
            data-testid="analysis-refresh"
          >
            Refresh
          </Button>
          {analysisStatus === 'error' ? (
            <Button
              type="button"
              $secondary
              onClick={onRetry}
              disabled={isBusy}
              data-testid="analysis-retry"
            >
              Retry
            </Button>
          ) : null}
          {invalidCompositionError && analysisStatus === 'error' ? (
            <Button
              type="button"
              $secondary
              onClick={onClearInvalid}
              data-testid="analysis-clear-invalid"
            >
              Clear
            </Button>
          ) : null}
        </ActionRow>
      </HeaderRow>

      <Controls>
        <div>
          <div id="analysis-scope-label" style={{ marginBottom: 6, fontSize: '0.85rem', color: '#64748b' }}>
            Analysis scope
          </div>
          <ScopeGroup role="radiogroup" aria-labelledby="analysis-scope-label">
            <ScopeButton
              type="button"
              role="radio"
              aria-checked={analysisScope === 'composition'}
              $active={analysisScope === 'composition'}
              data-testid="analysis-scope-composition"
              onClick={() => onScopeChange('composition')}
            >
              Whole composition
            </ScopeButton>
            <ScopeButton
              type="button"
              role="radio"
              aria-checked={analysisScope === 'section'}
              $active={analysisScope === 'section'}
              data-testid="analysis-scope-section"
              disabled={!sectionOptions.length}
              onClick={() => onScopeChange('section')}
            >
              Selected section
            </ScopeButton>
            <ScopeButton
              type="button"
              role="radio"
              aria-checked={analysisScope === 'track'}
              $active={analysisScope === 'track'}
              data-testid="analysis-scope-track"
              disabled={!tracks.length}
              onClick={() => onScopeChange('track')}
            >
              Selected track
            </ScopeButton>
          </ScopeGroup>
        </div>
        <ActionRow>
          {analysisScope === 'section' ? (
            <Field htmlFor="analysis-section-select">
              Section
              <Select
                id="analysis-section-select"
                data-testid="analysis-section-select"
                value={analysisSelectedSectionKey || ''}
                aria-label="Section for analysis"
                onChange={(event) => {
                  const key = event.target.value;
                  console.debug('[CompositionAnalysisPanel] Section selected', {
                    hasKey: Boolean(key),
                    optionCount: sectionOptions.length,
                  });
                  selectAnalysisSection(key || null);
                }}
              >
                {!sectionOptions.length ? (
                  <option value="">No sections</option>
                ) : null}
                {sectionOptions.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.displayLabel}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {analysisScope === 'track' ? (
            <Field htmlFor="analysis-track-select">
              Track (shared selection)
              <Select
                id="analysis-track-select"
                data-testid="analysis-track-select"
                value={pianoRollTrackId || ''}
                aria-label="Track for analysis (shared with piano roll)"
                onChange={(event) => {
                  console.debug('[CompositionAnalysisPanel] Track selected', {
                    hasTrackId: Boolean(event.target.value),
                    trackCount: tracks.length,
                  });
                  selectPianoRollTrack(event.target.value);
                }}
              >
                {!tracks.length ? (
                  <option value="">No tracks</option>
                ) : null}
                {tracks.map((track) => (
                  <option key={track.id} value={track.id}>
                    {track.name || track.id}
                    {track.role ? ` (${track.role})` : ''}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
        </ActionRow>
      </Controls>

      <ScopeSummary data-testid="analysis-resolved-scope">
        <strong>Resolved scope:</strong>
        {' '}
        {analysisResult?.resolved_scope
          ? formatResolvedScope(analysisResult.resolved_scope)
          : (
            analysisScope === 'composition'
              ? 'Whole composition (pending)'
              : analysisScope === 'section'
                ? `Selected section${analysisSelectedSectionKey ? '' : ' (choose a section)'}`
                : `Selected track${selectedTrack ? `: ${selectedTrack.name || selectedTrack.id}` : ' (choose a track)'}`
          )}
        {analysisResult?.algorithm_version
          ? ` · algorithm ${analysisResult.algorithm_version}`
          : null}
        {analysisResult?.status
          ? ` · report ${analysisResult.status}`
          : null}
      </ScopeSummary>

      <StatusBanner
        $tone={uiPhase.tone}
        role="status"
        aria-live="polite"
        data-testid="analysis-status"
      >
        {showInitialLoader || isBusy ? (
          <Loader>
            <Spinner aria-hidden="true" />
            <span>{uiPhase.label}</span>
          </Loader>
        ) : (
          uiPhase.label
        )}
        {freshness.isStale && !isBusy
          ? ' — edit the score or refresh to recompute.'
          : null}
      </StatusBanner>

      {analysisStatus === 'error' && analysisError ? (
        <StatusBanner $tone="error" role="alert" data-testid="analysis-error">
          {analysisError}
        </StatusBanner>
      ) : null}

      {showInitialLoader ? (
        <StatusBanner $tone="info" data-testid="analysis-initial-loader">
          <Loader>
            <Spinner aria-hidden="true" />
            <span>Loading first analysis report…</span>
          </Loader>
        </StatusBanner>
      ) : null}

      {freshness.hasResult ? (
        <MetricGrid data-testid="analysis-metric-grid">
          <MetricCard
            title="Tonal context"
            stale={staleCards}
            inference={tonality?.inference}
            testId="analysis-card-tonality"
          >
            <DefinitionRows
              rows={[
                ['Declared / inferred', formatDeclaredInferred(
                  tonality?.declared_key,
                  tonality?.global_key?.key,
                  tonality?.effective_key,
                )],
                ['Local / modulation', localModulation],
              ]}
            />
          </MetricCard>

          <MetricCard
            title="Harmony"
            stale={staleCards}
            inference={harmony?.inference}
            testId="analysis-card-harmony"
          >
            <DefinitionRows
              rows={[
                ['Inferred chords', chordSummary],
                ['Changes / bar', formatNumber(harmony?.harmonic_rhythm?.changes_per_bar)],
                ['Changes / quarter', formatNumber(harmony?.harmonic_rhythm?.changes_per_quarter)],
                ['Unique chords', String(harmony?.harmonic_rhythm?.unique_chord_count ?? 0)],
                ['Declared agreement', String(harmony?.declared_agreement || 'not_applicable').replaceAll('_', ' ')],
              ]}
            />
          </MetricCard>

          <MetricCard
            title="Phrase & cadence"
            stale={staleCards}
            inference={melody?.inference}
            testId="analysis-card-phrase"
          >
            <DefinitionRows
              rows={[
                ['Phrase count', String(phraseCount)],
                ['Cadences', cadenceSummary],
              ]}
            />
          </MetricCard>

          <MetricCard
            title="Density"
            stale={staleCards}
            inference={density?.inference}
            testId="analysis-card-density"
          >
            <DefinitionRows
              rows={[
                ['Attacks / quarter', formatNumber(density?.metrics?.attacks_per_quarter)],
                ['Attacks / bar', formatNumber(density?.metrics?.attacks_per_bar)],
                ['Note load', formatNumber(density?.metrics?.note_load)],
                ['Active-time union', formatNumber(density?.metrics?.active_time_union_ratio)],
                ['Max simultaneity', String(density?.metrics?.max_simultaneity ?? 0)],
                ['Chord changes', String(density?.metrics?.chord_changes ?? 0)],
              ]}
            />
          </MetricCard>

          <MetricCard
            title="Range & contour"
            stale={staleCards}
            inference={primaryProfile?.inference || melody?.inference}
            testId="analysis-card-melody"
          >
            <DefinitionRows
              rows={[
                ['Track', primaryProfile?.track_id || '—'],
                ['Range', primaryProfile?.pitch_min && primaryProfile?.pitch_max
                  ? `${primaryProfile.pitch_min}–${primaryProfile.pitch_max}`
                  : '—'],
                ['Semitones', primaryProfile?.range_semitones != null
                  ? String(primaryProfile.range_semitones)
                  : '—'],
                ['Contour', primaryProfile?.contour || 'unknown'],
                ['Skyline reduced', primaryProfile?.skyline_reduced ? 'yes (heuristic)' : 'no'],
              ]}
            />
          </MetricCard>

          <MetricCard
            title="Roles & repetition"
            stale={staleCards}
            inference={roles?.inference || repetition?.inference}
            testId="analysis-card-roles"
          >
            <DefinitionRows
              rows={[
                ['Roles', roleRows.length ? roleRows.join(' | ') : 'No role estimates'],
                ['Repetition', motifSummary],
                ['Section fingerprints', String(repetition?.section_fingerprint_ids?.length ?? 0)],
              ]}
            />
          </MetricCard>

          <MetricCard
            title="Tension components"
            stale={staleCards}
            inference={tension?.inference}
            testId="analysis-card-tension"
          >
            <DefinitionRows
              rows={[
                ['Vertical dissonance', formatNumber(tension?.components?.vertical_dissonance)],
                ['Chromatic mass', formatNumber(tension?.components?.chromatic_mass)],
                ['Non-chord-tone mass', formatNumber(tension?.components?.non_chord_tone_mass)],
                ['Functional distance', formatNumber(tension?.components?.functional_distance)],
                ['Unresolved tendency', formatNumber(tension?.components?.unresolved_tendency)],
                ['Limitations', Array.isArray(tension?.limitations) && tension.limitations.length
                  ? tension.limitations.join(', ')
                  : 'none reported'],
              ]}
            />
          </MetricCard>
        </MetricGrid>
      ) : null}

      {warnings.length ? (
        <div>
          <CardTitle as="h5">Warnings</CardTitle>
          <WarningList data-testid="analysis-warnings" aria-label="Analysis warnings">
            {warnings.map((warning, index) => (
              <WarningItem
                key={`${warning.code}-${index}`}
                $severity={warning.severity || 'warning'}
              >
                <strong>{warning.code}</strong>
                {' · '}
                {warning.severity || 'warning'}
                {warning.category ? ` · ${warning.category}` : ''}
                <div>{warning.message || warning.code}</div>
              </WarningItem>
            ))}
          </WarningList>
        </div>
      ) : null}
    </Root>
  );
};

export default CompositionAnalysisPanel;
