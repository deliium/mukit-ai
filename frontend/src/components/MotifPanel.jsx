import React, { useEffect, useMemo, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { listAnalysisSectionOptions } from '../utils/compositionAnalysis.js';
import {
  countDestinationOverlapEvents,
  isPitchedMotifSourceTrack,
  MOTIF_APPLY_OPERATIONS,
  MOTIF_CREATIVE_OPERATION_SET,
  nextMotifLabel,
  resolveMotifOccurrenceBarSpan,
  validateMotifAuthoringSelection,
} from '../utils/compositionMotifs.js';
import { motifDestinationTickRange } from '../utils/pianoRollSelection.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;
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

const Grid = styled.div`
  display: grid;
  gap: 12px;
  min-width: 0;

  @media (min-width: 900px) {
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
  }
`;

const Card = styled.article`
  padding: 12px;
  background: #ffffff;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
  min-width: 0;
`;

const CardTitle = styled.h5`
  margin: 0 0 10px;
  font-size: 0.9rem;
  font-weight: 600;
  color: #312e81;
`;

const List = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
  max-height: 280px;
  overflow: auto;
`;

const ListItem = styled.li`
  padding: 8px 10px;
  border: 1px solid ${(props) => (props.$active ? '#4f46e5' : '#e2e8f0')};
  border-radius: 8px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#f8fafc')};
  cursor: ${(props) => (props.$clickable === false ? 'default' : 'pointer')};
  opacity: ${(props) => (props.$stale ? 0.88 : 1)};
`;

const Meta = styled.div`
  font-size: 0.8rem;
  color: #475569;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 4px;
  overflow-wrap: anywhere;
`;

const Field = styled.label`
  display: grid;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
  margin-bottom: 8px;
  min-width: 0;
`;

const Input = styled.input`
  min-height: 44px;
  min-width: 0;
  max-width: 100%;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  box-sizing: border-box;
`;

const Select = styled.select`
  min-height: 44px;
  min-width: 0;
  max-width: 100%;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  background: #fff;
  box-sizing: border-box;
`;

const ButtonRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
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
`;

const WarningList = styled.ul`
  margin: 8px 0 0;
  padding-left: 18px;
  color: #92400e;
  font-size: 0.85rem;
`;

function formatIdentityScore(value) {
  if (value == null || !Number.isFinite(Number(value))) {
    return '—';
  }
  return Number(value).toFixed(2);
}

function formatUsageBars(usage) {
  if (usage?.startBar != null && usage?.endBar != null) {
    return usage.startBar === usage.endBar
      ? `bar ${usage.startBar}`
      : `bars ${usage.startBar}–${usage.endBar}`;
  }
  return 'bars —';
}

function operationLabel(operation) {
  return String(operation || '').replaceAll('_', ' ');
}

/**
 * Motifs tab: mark selections, manage definitions, inspect usages, configure transforms.
 */
const MotifPanel = ({ onOpenPianoTab = null }) => {
  const composition = useMusicStore((state) => state.editedMusicJson);
  const compositionRevision = useMusicStore((state) => state.compositionRevision);
  const analysisResult = useMusicStore((state) => state.analysisResult);
  const analysisResultKey = useMusicStore((state) => state.analysisResultKey);
  const getAnalysisFreshness = useMusicStore((state) => state.getAnalysisFreshness);
  const selectedMotifId = useMusicStore((state) => state.motifSelectedMotifId);
  const selectedOccurrenceId = useMusicStore((state) => state.motifSelectedOccurrenceId);
  const highlightedUsageKey = useMusicStore((state) => state.motifHighlightedUsageKey);
  const destinationSectionId = useMusicStore((state) => state.motifDestinationSectionId);
  const destinationTrackId = useMusicStore((state) => state.motifDestinationTrackId);
  const destinationStartBar = useMusicStore((state) => state.motifDestinationStartBar);
  const operation = useMusicStore((state) => state.motifOperation);
  const operationParams = useMusicStore((state) => state.motifOperationParams);
  const variationStrength = useMusicStore((state) => state.motifVariationStrength);
  const applyStatus = useMusicStore((state) => state.motifApplyStatus);
  const applyError = useMusicStore((state) => state.motifApplyError);
  const applyWarnings = useMusicStore((state) => state.motifApplyWarnings);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);
  const pianoRollNoteIds = useMusicStore((state) => state.pianoRollNoteIds);

  const [renameDraft, setRenameDraft] = useState('');
  const [confirmReplace, setConfirmReplace] = useState(false);

  const validation = useMemo(
    () => (composition ? validateMusicJson(composition) : { valid: false, message: 'No composition' }),
    [composition],
  );
  const canonical = isCanonicalComposition(composition);

  const usages = useMemo(() => {
    // Recompute when composition/analysis revisions change even if object identity is reused.
    void compositionRevision;
    void analysisResultKey;
    if (!composition || !canonical || !validation.valid) {
      return [];
    }
    return useMusicStore.getState().getMotifUsages();
  }, [composition, canonical, validation.valid, compositionRevision, analysisResultKey]);

  const motifs = Array.isArray(composition?.motifs) ? composition.motifs : [];
  const sectionOptions = useMemo(
    () => listAnalysisSectionOptions(composition),
    [composition],
  );
  const pitchedTracks = useMemo(
    () => (composition?.tracks || []).filter((track) => isPitchedMotifSourceTrack(track)),
    [composition],
  );

  const selection = useMemo(() => {
    if (!composition) {
      return { valid: false, message: 'No composition loaded' };
    }
    return validateMotifAuthoringSelection(composition, {
      trackId: pianoRollTrackId,
      eventIds: pianoRollNoteIds,
    });
  }, [composition, pianoRollTrackId, pianoRollNoteIds]);

  const selectedMotif = motifs.find((item) => item.id === selectedMotifId) || null;
  const selectedOccurrence = selectedMotif?.occurrences?.find(
    (item) => item.id === selectedOccurrenceId,
  ) || null;
  const sourceSpan = useMemo(() => {
    if (!composition || !selectedOccurrence) {
      return { valid: false, barSpan: 1 };
    }
    return resolveMotifOccurrenceBarSpan(composition, selectedOccurrence);
  }, [composition, selectedOccurrence]);

  const destinationPlacement = useMemo(() => {
    if (!composition || !destinationStartBar) {
      return { valid: false, overlapCount: 0 };
    }
    const placement = motifDestinationTickRange(destinationStartBar, {
      composition,
      barSpan: sourceSpan.valid ? sourceSpan.barSpan : 1,
    });
    if (!placement.valid || !destinationTrackId) {
      return { valid: false, overlapCount: 0, placement };
    }
    const overlapCount = countDestinationOverlapEvents(
      composition,
      destinationTrackId,
      placement.startTick,
      placement.endTick,
    );
    return { valid: true, overlapCount, placement };
  }, [composition, destinationStartBar, destinationTrackId, sourceSpan]);

  const isCreative = MOTIF_CREATIVE_OPERATION_SET.has(operation);
  const loading = applyStatus === 'loading';
  const analysisFreshness = getAnalysisFreshness();

  const disabledReason = useMemo(() => {
    if (!composition || !canonical || !validation.valid) {
      return 'Canonical composition is required';
    }
    if (!selectedMotifId || !selectedOccurrenceId) {
      return 'Select a source motif occurrence';
    }
    if (!destinationTrackId || !destinationStartBar) {
      return 'Configure destination track and start bar';
    }
    if (operation === 'transpose' && operationParams?.transpose_semitones == null) {
      return 'Transpose semitones are required';
    }
    if (operation === 'sequence') {
      const missing = ['sequence_steps', 'sequence_interval_semitones', 'sequence_step_ticks']
        .filter((key) => operationParams?.[key] == null);
      if (missing.length) {
        return `${missing.join(', ')} required for sequence`;
      }
    }
    if (isCreative && (!selectedProvider || !selectedModel)) {
      return 'Creative operations require LLM provider and model';
    }
    if (loading) {
      return 'Motif apply already in progress';
    }
    return '';
  }, [
    composition,
    canonical,
    validation.valid,
    selectedMotifId,
    selectedOccurrenceId,
    destinationTrackId,
    destinationStartBar,
    operation,
    operationParams,
    isCreative,
    selectedProvider,
    selectedModel,
    loading,
  ]);

  useEffect(() => {
    setRenameDraft(selectedMotif?.label || '');
  }, [selectedMotif?.id, selectedMotif?.label]);

  useEffect(() => {
    setConfirmReplace(false);
  }, [
    selectedMotifId,
    selectedOccurrenceId,
    destinationTrackId,
    destinationStartBar,
    operation,
    destinationPlacement.overlapCount,
  ]);

  const filteredUsages = useMemo(() => {
    if (!selectedMotifId) {
      return usages;
    }
    return usages.filter(
      (usage) => usage.source === 'detected' || usage.motifId === selectedMotifId,
    );
  }, [usages, selectedMotifId]);

  const onMark = () => {
    const label = nextMotifLabel(motifs);
    const result = useMusicStore.getState().markMotifFromSelection({ label });
    if (!result?.ok) {
      console.warn('[MotifPanel] Mark motif rejected', { code: result?.code || null });
    } else {
      console.info('[MotifPanel] Motif marked', {
        motifId: result.motifId,
        eventCount: result.eventCount,
      });
    }
  };

  const goToUsage = (usage) => {
    useMusicStore.getState().selectMotifUsage({
      usageKey: usage.key,
      motifId: usage.motifId,
      occurrenceId: usage.occurrenceId,
      trackId: usage.trackId,
      eventIds: usage.eventIds,
    });
    if (typeof onOpenPianoTab === 'function') {
      onOpenPianoTab();
    }
    console.debug('[MotifPanel] Navigated to motif usage', {
      usageKey: usage.key,
      trackId: usage.trackId,
      eventCount: usage.eventIds?.length || 0,
    });
  };

  const onApply = async () => {
    if (disabledReason) {
      console.debug('[MotifPanel] Apply disabled', { reason: disabledReason });
      return;
    }
    if (destinationPlacement.overlapCount > 0 && !confirmReplace) {
      setConfirmReplace(true);
      console.warn('[MotifPanel] Replace confirmation required', {
        overlapCount: destinationPlacement.overlapCount,
      });
      return;
    }
    setConfirmReplace(false);
    console.info('[MotifPanel] Submitting motif apply', {
      motifId: selectedMotifId,
      operation,
      destinationTrackId,
      destinationStartBar,
      overlapCount: destinationPlacement.overlapCount,
    });
    await useMusicStore.getState().applyMotifTransformation();
  };

  if (!composition) {
    return (
      <Panel data-testid="motif-panel">
        <Title>Motifs</Title>
        <Hint>Generate or import a composition to author motifs.</Hint>
      </Panel>
    );
  }

  if (!canonical || !validation.valid) {
    return (
      <Panel data-testid="motif-panel">
        <Title>Motifs</Title>
        <StatusBanner $tone="warn">
          {validation.message || 'Canonical composition.v2 is required for motif authoring.'}
        </StatusBanner>
      </Panel>
    );
  }

  return (
    <Panel data-testid="motif-panel">
      <Title>Motifs</Title>
      <Hint>
        Select 3–32 notes spanning one or two bars on a pitched track in the piano roll, mark a motif,
        then apply a transformation to another section or track. Detected repetitions appear when analysis is current.
      </Hint>

      <Grid>
        <Card>
          <CardTitle>Authoring</CardTitle>
          <Meta data-testid="motif-selection-summary">
            <span>{pianoRollNoteIds?.length || 0} note(s) selected</span>
            <span>{selection.valid ? `Eligible · ${selection.barSpan} bar span` : selection.message}</span>
          </Meta>
          <ButtonRow>
            <Button
              type="button"
              data-testid="motif-mark-button"
              disabled={!selection.valid || loading}
              aria-disabled={!selection.valid || loading}
              title={selection.valid ? '' : selection.message}
              onClick={onMark}
            >
              Mark as {nextMotifLabel(motifs)}
            </Button>
            <Button
              type="button"
              $secondary
              data-testid="motif-delete-button"
              disabled={!selectedMotifId || loading}
              onClick={() => selectedMotifId && useMusicStore.getState().deleteMotif(selectedMotifId)}
            >
              Delete selected
            </Button>
          </ButtonRow>

          <CardTitle style={{ marginTop: 14 }}>Definitions</CardTitle>
          <List aria-label="Authored motifs" data-testid="motif-definition-list">
            {motifs.length === 0 ? (
              <ListItem $active={false} $clickable={false}>No authored motifs yet.</ListItem>
            ) : (
              motifs.map((motif) => (
                <ListItem
                  key={motif.id}
                  $active={motif.id === selectedMotifId}
                  data-testid={`motif-item-${motif.id}`}
                  onClick={() => {
                    useMusicStore.getState().selectMotif(motif.id);
                    setRenameDraft(motif.label || '');
                  }}
                >
                  <strong>{motif.label || motif.id}</strong>
                  <Meta>
                    <span>{(motif.occurrences || []).length} usage(s)</span>
                    <span>{motif.id}</span>
                  </Meta>
                </ListItem>
              ))
            )}
          </List>

          {selectedMotif ? (
            <div style={{ marginTop: 10 }}>
              <Field>
                Rename
                <Input
                  value={renameDraft}
                  onChange={(event) => setRenameDraft(event.target.value)}
                  data-testid="motif-rename-input"
                  aria-label="Motif label"
                />
              </Field>
              <Button
                type="button"
                $secondary
                data-testid="motif-rename-save"
                onClick={() => useMusicStore.getState().renameMotif(selectedMotifId, renameDraft)}
              >
                Save name
              </Button>
            </div>
          ) : null}

          <CardTitle style={{ marginTop: 14 }}>Usages</CardTitle>
          {!analysisResult ? (
            <Hint>Open the Analysis tab to detect similar patterns automatically.</Hint>
          ) : null}
          {analysisResult && !analysisFreshness.isCurrent ? (
            <StatusBanner $tone="warn" data-testid="motif-detected-stale">
              Analysis is stale — detected usages may be outdated until analysis refreshes.
            </StatusBanner>
          ) : null}
          <List aria-label="Motif usages" data-testid="motif-usage-list">
            {filteredUsages.length === 0 ? (
              <ListItem $active={false} $clickable={false}>No usages to show.</ListItem>
            ) : (
              filteredUsages.slice(0, 60).map((usage) => (
                <ListItem
                  key={usage.key || `${usage.motifId}-${usage.occurrenceId}`}
                  $active={usage.key === highlightedUsageKey}
                  $stale={Boolean(usage.stale)}
                  data-testid={`motif-usage-${usage.key}`}
                  onClick={() => goToUsage(usage)}
                >
                  <strong>{usage.motifLabel || usage.relationship || 'usage'}</strong>
                  <Meta>
                    <span>{usage.source}</span>
                    <span>{usage.relationship}</span>
                    <span>{usage.trackId}</span>
                    <span>{formatUsageBars(usage)}</span>
                    <span>identity {formatIdentityScore(usage.identityScore)}</span>
                    {usage.stale ? <span>stale</span> : null}
                    {usage.truncated ? <span>truncated</span> : null}
                  </Meta>
                  <ButtonRow>
                    <Button
                      type="button"
                      $secondary
                      data-testid={`motif-goto-${usage.key}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        goToUsage(usage);
                      }}
                    >
                      Go to usage
                    </Button>
                  </ButtonRow>
                </ListItem>
              ))
            )}
          </List>
        </Card>

        <Card>
          <CardTitle>Transform</CardTitle>
          <Field>
            Destination section
            <Select
              data-testid="motif-destination-section"
              value={destinationSectionId || ''}
              aria-label="Destination section"
              onChange={(event) => useMusicStore.getState().configureMotifDestination({
                sectionId: event.target.value || null,
              })}
            >
              <option value="">Any / infer</option>
              {sectionOptions.map((section) => (
                <option key={section.key} value={section.id || ''}>
                  {section.displayLabel || section.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field>
            Destination track
            <Select
              data-testid="motif-destination-track"
              value={destinationTrackId || ''}
              aria-label="Destination track"
              onChange={(event) => useMusicStore.getState().configureMotifDestination({
                trackId: event.target.value || null,
              })}
            >
              <option value="">Select track</option>
              {pitchedTracks.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.name || track.id} ({track.role})
                </option>
              ))}
            </Select>
          </Field>
          <Field>
            Start bar
            <Input
              type="number"
              min={1}
              max={composition.bar_count || 1}
              data-testid="motif-destination-start-bar"
              value={destinationStartBar ?? ''}
              aria-label="Destination start bar"
              onChange={(event) => useMusicStore.getState().configureMotifDestination({
                startBar: event.target.value ? Number(event.target.value) : null,
              })}
            />
          </Field>
          <Field>
            Operation
            <Select
              data-testid="motif-operation"
              value={operation}
              aria-label="Motif operation"
              onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                operation: event.target.value,
              })}
            >
              {MOTIF_APPLY_OPERATIONS.map((item) => (
                <option key={item} value={item}>{operationLabel(item)}</option>
              ))}
            </Select>
          </Field>

          {operation === 'transpose' ? (
            <Field>
              Transpose semitones
              <Input
                type="number"
                min={-48}
                max={48}
                data-testid="motif-transpose-semitones"
                value={operationParams?.transpose_semitones ?? ''}
                onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                  operationParams: {
                    ...operationParams,
                    transpose_semitones: Number(event.target.value),
                  },
                })}
              />
            </Field>
          ) : null}

          {operation === 'inversion' ? (
            <Field>
              Inversion axis pitch (optional)
              <Input
                type="text"
                placeholder="e.g. C4"
                data-testid="motif-inversion-axis"
                value={operationParams?.inversion_axis_pitch ?? ''}
                onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                  operationParams: {
                    ...operationParams,
                    inversion_axis_pitch: event.target.value.trim() || undefined,
                  },
                })}
              />
            </Field>
          ) : null}

          {(operation === 'augmentation' || operation === 'diminution') ? (
            <>
              <Field>
                Time scale numerator
                <Input
                  type="number"
                  min={1}
                  max={8}
                  data-testid="motif-time-scale-num"
                  value={operationParams?.time_scale_numerator ?? (operation === 'augmentation' ? 2 : 1)}
                  onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                    operationParams: {
                      ...operationParams,
                      time_scale_numerator: Number(event.target.value),
                    },
                  })}
                />
              </Field>
              <Field>
                Time scale denominator
                <Input
                  type="number"
                  min={1}
                  max={8}
                  data-testid="motif-time-scale-den"
                  value={operationParams?.time_scale_denominator ?? (operation === 'augmentation' ? 1 : 2)}
                  onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                    operationParams: {
                      ...operationParams,
                      time_scale_denominator: Number(event.target.value),
                    },
                  })}
                />
              </Field>
            </>
          ) : null}

          {operation === 'sequence' ? (
            <>
              <Field>
                Sequence steps
                <Input
                  type="number"
                  min={1}
                  max={16}
                  data-testid="motif-sequence-steps"
                  value={operationParams?.sequence_steps ?? 2}
                  onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                    operationParams: {
                      ...operationParams,
                      sequence_steps: Number(event.target.value),
                    },
                  })}
                />
              </Field>
              <Field>
                Interval semitones
                <Input
                  type="number"
                  min={-24}
                  max={24}
                  data-testid="motif-sequence-interval"
                  value={operationParams?.sequence_interval_semitones ?? 2}
                  onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                    operationParams: {
                      ...operationParams,
                      sequence_interval_semitones: Number(event.target.value),
                    },
                  })}
                />
              </Field>
              <Field>
                Step ticks
                <Input
                  type="number"
                  min={1}
                  data-testid="motif-sequence-step-ticks"
                  value={operationParams?.sequence_step_ticks ?? composition.ticks_per_quarter ?? 480}
                  onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                    operationParams: {
                      ...operationParams,
                      sequence_step_ticks: Number(event.target.value),
                    },
                  })}
                />
              </Field>
            </>
          ) : null}

          {isCreative ? (
            <Field>
              Variation strength (0–1)
              <Input
                type="range"
                min={0}
                max={1}
                step={0.05}
                data-testid="motif-variation-strength"
                value={variationStrength}
                aria-valuemin={0}
                aria-valuemax={1}
                aria-valuenow={variationStrength}
                onChange={(event) => useMusicStore.getState().configureMotifTransformation({
                  variationStrength: Number(event.target.value),
                })}
              />
              <span>{Number(variationStrength).toFixed(2)}</span>
            </Field>
          ) : null}

          {destinationPlacement.valid && destinationPlacement.overlapCount > 0 ? (
            <StatusBanner $tone="warn" data-testid="motif-overlap-warning">
              Destination overlaps {destinationPlacement.overlapCount} existing note(s). Applying replaces them.
            </StatusBanner>
          ) : null}

          {confirmReplace ? (
            <StatusBanner $tone="warn" data-testid="motif-replace-confirm">
              Confirm replacement of {destinationPlacement.overlapCount} note(s). Click Apply again to proceed.
            </StatusBanner>
          ) : null}

          {disabledReason ? (
            <StatusBanner $tone="warn" data-testid="motif-apply-disabled-reason">
              {disabledReason}
            </StatusBanner>
          ) : null}

          <ButtonRow>
            <Button
              type="button"
              data-testid="motif-apply-button"
              disabled={Boolean(disabledReason)}
              aria-disabled={Boolean(disabledReason)}
              onClick={onApply}
            >
              {loading ? 'Applying…' : confirmReplace ? 'Confirm replace & apply' : 'Apply motif'}
            </Button>
            <Button
              type="button"
              $secondary
              data-testid="motif-reset-ui"
              onClick={() => {
                setConfirmReplace(false);
                useMusicStore.getState().resetMotifUiState();
              }}
            >
              Reset UI
            </Button>
          </ButtonRow>

          {applyStatus === 'loading' ? (
            <StatusBanner data-testid="motif-apply-loading">Applying motif transformation…</StatusBanner>
          ) : null}
          {applyError ? (
            <StatusBanner $tone="error" data-testid="motif-apply-error">{applyError}</StatusBanner>
          ) : null}
          {applyStatus === 'success' ? (
            <StatusBanner $tone="ok" data-testid="motif-apply-success">Motif applied successfully.</StatusBanner>
          ) : null}
          {Array.isArray(applyWarnings) && applyWarnings.length > 0 ? (
            <WarningList data-testid="motif-apply-warnings">
              {applyWarnings.map((warning) => (
                <li key={typeof warning === 'string' ? warning : warning.code || JSON.stringify(warning)}>
                  {typeof warning === 'string' ? warning : warning.message || warning.code}
                </li>
              ))}
            </WarningList>
          ) : null}
        </Card>
      </Grid>
    </Panel>
  );
};

export default MotifPanel;
