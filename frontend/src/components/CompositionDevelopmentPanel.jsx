import React, { useEffect, useMemo, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import {
  DEVELOPMENT_INTENTS,
  DEVELOPMENT_OPERATIONS,
  DEVELOPMENT_SECTION_TYPES,
  VARIATION_STRENGTHS,
  editFingerprintLogPrefix,
  findDevelopmentCandidateById,
  listDevelopmentSectionOptions,
  resolveDevelopmentDefaults,
} from '../utils/compositionCandidates.js';
import { isCanonicalComposition } from '../utils/musicJsonValidation.js';

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
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr);
  }
`;

const Card = styled.article`
  padding: 12px;
  background: #ffffff;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
  min-width: 0;
`;

const Field = styled.label`
  display: grid;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
  margin-bottom: 8px;
`;

const Input = styled.input`
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
`;

const Select = styled.select`
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  background: #fff;
`;

const TextArea = styled.textarea`
  width: 100%;
  min-height: 72px;
  padding: 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  resize: vertical;
  font-size: 0.95rem;
  box-sizing: border-box;
`;

const ButtonRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
`;

const Button = styled.button`
  min-height: 44px;
  padding: 8px 14px;
  border: none;
  border-radius: 8px;
  background: ${(props) => {
    if (props.$variant === 'danger') return '#dc2626';
    if (props.$variant === 'secondary') return '#475569';
    if (props.$variant === 'ghost') return '#e2e8f0';
    return '#4f46e5';
  }};
  color: ${(props) => (props.$variant === 'ghost' ? '#1e293b' : '#fff')};
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    background: #d1d5db;
    color: #64748b;
    cursor: not-allowed;
  }
`;

const Status = styled.div`
  font-size: 0.85rem;
  color: ${(props) => {
    if (props.$tone === 'error') return '#991b1b';
    if (props.$tone === 'warn') return '#92400e';
    return '#1e3a8a';
  }};
`;

const CandidateList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
`;

const CandidateCard = styled.li`
  border: 2px solid ${(props) => (props.$selected ? '#4f46e5' : '#e2e8f0')};
  border-radius: 10px;
  padding: 10px;
  background: ${(props) => (props.$selected ? '#eef2ff' : '#f8fafc')};
  cursor: pointer;
`;

const Meta = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  font-size: 0.8rem;
  color: #475569;
`;

const STRENGTH_HELP = {
  conservative: 'Stay close to the source contour and identity anchors.',
  balanced: 'Moderate development while preserving seam continuity.',
  experimental: 'Allow more contrast while keeping at least one identity anchor.',
};

const CompositionDevelopmentPanel = () => {
  const composition = useMusicStore((state) => state.editedMusicJson);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const operation = useMusicStore((state) => state.developmentOperation);
  const intent = useMusicStore((state) => state.developmentIntent);
  const strength = useMusicStore((state) => state.developmentStrength);
  const outputBars = useMusicStore((state) => state.developmentOutputBars);
  const candidateCount = useMusicStore((state) => state.developmentCandidateCount);
  const instruction = useMusicStore((state) => state.developmentInstruction);
  const targetSectionType = useMusicStore((state) => state.developmentTargetSectionType);
  const targetSectionLabel = useMusicStore((state) => state.developmentTargetSectionLabel);
  const allowModulation = useMusicStore((state) => state.developmentAllowModulation);
  const sourceStartBar = useMusicStore((state) => state.developmentSourceStartBar);
  const sourceEndBar = useMusicStore((state) => state.developmentSourceEndBar);
  const status = useMusicStore((state) => state.developmentStatus);
  const error = useMusicStore((state) => state.developmentError);
  const warnings = useMusicStore((state) => state.developmentWarnings);
  const candidates = useMusicStore((state) => state.developmentCandidates);
  const selectedCandidateId = useMusicStore((state) => state.developmentSelectedCandidateId);
  const auditionActive = useMusicStore((state) => state.developmentAuditionActive);
  const editSourceFingerprint = useMusicStore((state) => state.developmentEditSourceFingerprint);
  const baseRevision = useMusicStore((state) => state.compositionRevision);
  const developmentBaseRevision = useMusicStore((state) => state.developmentBaseRevision);

  const setDevelopmentControls = useMusicStore((state) => state.setDevelopmentControls);
  const syncDevelopmentDefaultsFromComposition = useMusicStore(
    (state) => state.syncDevelopmentDefaultsFromComposition,
  );
  const startDevelopmentPreview = useMusicStore((state) => state.startDevelopmentPreview);
  const selectDevelopmentCandidate = useMusicStore((state) => state.selectDevelopmentCandidate);
  const setDevelopmentAuditionActive = useMusicStore((state) => state.setDevelopmentAuditionActive);
  const discardDevelopmentCandidates = useMusicStore((state) => state.discardDevelopmentCandidates);
  const rejectDevelopmentCandidate = useMusicStore((state) => state.rejectDevelopmentCandidate);
  const refreshDevelopmentComparison = useMusicStore((state) => state.refreshDevelopmentComparison);
  const developmentCompareResult = useMusicStore((state) => state.developmentCompareResult);
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const applySelectedDevelopmentCandidate = useMusicStore(
    (state) => state.applySelectedDevelopmentCandidate,
  );
  const [branchNameDraft, setBranchNameDraft] = useState('');
  const [applyBusy, setApplyBusy] = useState(false);

  useEffect(() => {
    if (isCanonicalComposition(composition) && sourceStartBar == null) {
      syncDevelopmentDefaultsFromComposition();
    }
  }, [composition, sourceStartBar, syncDevelopmentDefaultsFromComposition]);

  const sectionOptions = useMemo(
    () => listDevelopmentSectionOptions(composition),
    [composition],
  );

  const selectedCandidate = useMemo(
    () => findDevelopmentCandidateById(candidates, selectedCandidateId),
    [candidates, selectedCandidateId],
  );

  const loading = status === 'loading';
  const stale = status === 'stale'
    || (developmentBaseRevision != null && developmentBaseRevision !== baseRevision);
  const canApply = status === 'ready'
    && Boolean(selectedCandidate)
    && !stale
    && !loading;

  if (!isCanonicalComposition(composition)) {
    return (
      <Panel data-testid="develop-panel">
        <Title>Develop</Title>
        <Hint>Load or generate a composition.v2 piece to continue, add a section, or vary bars.</Hint>
      </Panel>
    );
  }

  const onOperationChange = (nextOperation) => {
    const defaults = resolveDevelopmentDefaults(composition, {
      operation: nextOperation,
      aiEditStartBar: sourceStartBar,
      aiEditEndBar: sourceEndBar,
    });
    setDevelopmentControls({
      operation: nextOperation,
      ...(defaults.outputBars != null ? { outputBars: defaults.outputBars } : {}),
      targetSectionType: defaults.targetSectionType ?? 'verse',
      sourceStartBar: defaults.sourceStartBar,
      sourceEndBar: defaults.sourceEndBar,
      sourceSectionKey: defaults.sourceSectionKey,
    });
  };

  const handleApply = async () => {
    if (!canApply || applyBusy) {
      return;
    }
    if (operation === 'vary_section') {
      const confirmed = window.confirm(
        `Replace bars ${sourceStartBar}–${sourceEndBar} with the selected candidate? This cannot be undone except via Undo.`,
      );
      if (!confirmed) {
        return;
      }
    }
    setApplyBusy(true);
    try {
      console.info('[CompositionDevelopmentPanel] Apply selected development candidate');
      await applySelectedDevelopmentCandidate();
    } catch {
      // store records developmentError
    } finally {
      setApplyBusy(false);
    }
  };

  return (
    <Panel data-testid="develop-panel" aria-label="Composition development">
      <Title>Develop</Title>
      <Hint>
        Preview candidates do not change or save the composition. Apply commits one candidate as a
        normal edit (undoable, dirty, autosaved).
      </Hint>

      <Grid>
        <Card>
          <Field>
            Mode
            <Select
              data-testid="develop-operation"
              value={operation}
              onChange={(event) => onOperationChange(event.target.value)}
              aria-label="Development operation"
            >
              {DEVELOPMENT_OPERATIONS.map((value) => (
                <option key={value} value={value}>{value.replace('_', ' ')}</option>
              ))}
            </Select>
          </Field>

          <Field>
            Source bars
            <div style={{ display: 'flex', gap: 8 }}>
              <Input
                data-testid="develop-source-start"
                type="number"
                min={1}
                value={sourceStartBar ?? ''}
                onChange={(event) => setDevelopmentControls({
                  sourceStartBar: Number(event.target.value),
                  sourceEndBar: sourceEndBar ?? Number(event.target.value),
                })}
                aria-label="Source start bar"
              />
              <Input
                data-testid="develop-source-end"
                type="number"
                min={1}
                value={sourceEndBar ?? ''}
                onChange={(event) => setDevelopmentControls({
                  sourceStartBar: sourceStartBar ?? Number(event.target.value),
                  sourceEndBar: Number(event.target.value),
                })}
                aria-label="Source end bar"
              />
            </div>
          </Field>

          {sectionOptions.length > 0 && (
            <Field>
              Jump to section
              <Select
                data-testid="develop-section"
                value=""
                onChange={(event) => {
                  const option = sectionOptions.find((item) => item.key === event.target.value);
                  if (!option) return;
                  setDevelopmentControls({
                    sourceStartBar: option.start_bar,
                    sourceEndBar: option.start_bar + option.bar_count - 1,
                    sourceSectionKey: option.key,
                  });
                }}
                aria-label="Source section"
              >
                <option value="">Select section…</option>
                {sectionOptions.map((option) => (
                  <option key={option.key} value={option.key}>{option.label}</option>
                ))}
              </Select>
            </Field>
          )}

          {operation !== 'vary_section' && (
            <Field>
              Output bars
              <Input
                data-testid="develop-output-bars"
                type="number"
                min={1}
                max={64}
                value={outputBars ?? 8}
                onChange={(event) => setDevelopmentControls({ outputBars: Number(event.target.value) })}
                aria-label="Output bars to append"
              />
            </Field>
          )}

          {operation === 'add_section' && (
            <>
              <Field>
                Target section type
                <Select
                  data-testid="develop-target-section-type"
                  value={targetSectionType || 'verse'}
                  onChange={(event) => setDevelopmentControls({ targetSectionType: event.target.value })}
                >
                  {DEVELOPMENT_SECTION_TYPES.map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </Select>
              </Field>
              <Field>
                Label (optional)
                <Input
                  data-testid="develop-target-section-label"
                  value={targetSectionLabel || ''}
                  onChange={(event) => setDevelopmentControls({ targetSectionLabel: event.target.value })}
                />
              </Field>
            </>
          )}

          <Field>
            Intent
            <Select
              data-testid="develop-intent"
              value={intent}
              onChange={(event) => setDevelopmentControls({ intent: event.target.value })}
            >
              {DEVELOPMENT_INTENTS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </Select>
          </Field>

          <Field>
            Variation strength
            <Select
              data-testid="develop-strength"
              value={strength}
              onChange={(event) => setDevelopmentControls({ strength: event.target.value })}
              aria-describedby="develop-strength-help"
            >
              {VARIATION_STRENGTHS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </Select>
            <Hint id="develop-strength-help">{STRENGTH_HELP[strength]}</Hint>
          </Field>

          <Field>
            Candidate count
            <Select
              data-testid="develop-candidate-count"
              value={candidateCount}
              onChange={(event) => setDevelopmentControls({ candidateCount: Number(event.target.value) })}
            >
              {[1, 2, 3, 4].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </Select>
          </Field>

          <Field>
            <span>
              <input
                data-testid="develop-allow-modulation"
                type="checkbox"
                checked={Boolean(allowModulation)}
                onChange={(event) => setDevelopmentControls({ allowModulation: event.target.checked })}
              />
              {' '}Allow modulation at generated boundaries
            </span>
          </Field>

          <Field>
            Instruction (optional)
            <TextArea
              data-testid="develop-instruction"
              value={instruction}
              onChange={(event) => setDevelopmentControls({ instruction: event.target.value })}
              placeholder="e.g. continue into a brighter chorus while keeping the bass groove"
            />
          </Field>

          <Meta>
            <span>Provider: {selectedProvider || '—'}</span>
            <span>Model: {selectedModel || '—'}</span>
          </Meta>

          <ButtonRow>
            <Button
              type="button"
              data-testid="develop-generate"
              onClick={() => startDevelopmentPreview()}
              disabled={loading}
            >
              {loading ? 'Generating…' : candidates.length ? 'Regenerate' : 'Generate candidates'}
            </Button>
            <Button
              type="button"
              $variant="ghost"
              data-testid="develop-discard"
              onClick={() => discardDevelopmentCandidates()}
              disabled={!candidates.length && status === 'idle'}
            >
              Discard
            </Button>
          </ButtonRow>
        </Card>

        <Card>
          <Status
            role="status"
            aria-live="polite"
            data-testid="develop-status"
            $tone={status === 'error' || stale ? 'error' : status === 'stale' ? 'warn' : undefined}
          >
            {loading && 'Generating candidates…'}
            {status === 'ready' && !stale && `${candidates.length} candidate(s) ready`}
            {status === 'error' && (error || 'Preview failed')}
            {stale && (error || 'Composition changed — regenerate candidates')}
            {status === 'idle' && 'No candidates yet'}
          </Status>

          {editSourceFingerprint && (
            <Hint>Source fingerprint {editFingerprintLogPrefix(editSourceFingerprint)}…</Hint>
          )}

          {warnings?.length > 0 && (
            <Status $tone="warn" data-testid="develop-warnings">
              Warnings: {warnings.join(', ')}
            </Status>
          )}

          <CandidateList data-testid="develop-candidate-list">
            {candidates.map((candidate, index) => {
              const selected = candidate.candidate_id === selectedCandidateId;
              const range = candidate.output_range || {};
              return (
                <CandidateCard
                  key={candidate.candidate_id}
                  $selected={selected}
                  data-testid={`develop-candidate-${index}`}
                  data-candidate-id={candidate.candidate_id}
                  onClick={() => selectDevelopmentCandidate(candidate.candidate_id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      selectDevelopmentCandidate(candidate.candidate_id);
                    }
                  }}
                  tabIndex={0}
                  role="option"
                  aria-selected={selected}
                >
                  <strong>Candidate {index + 1}</strong>
                  <Meta>
                    <span>bars {range.start_bar}–{range.end_bar}</span>
                    <span>{candidate.composition?.bar_count} bars total</span>
                    <span>id …{candidate.candidate_id.slice(-8)}</span>
                  </Meta>
                  {(candidate.identity_diagnostics || []).slice(0, 3).map((diag) => (
                    <Hint key={`${candidate.candidate_id}-${diag.code}`}>
                      {diag.code}: {diag.severity || diag.message || ''}
                    </Hint>
                  ))}
                </CandidateCard>
              );
            })}
          </CandidateList>

          <ButtonRow>
            <Button
              type="button"
              $variant="secondary"
              data-testid="develop-audition"
              disabled={!selectedCandidate || applyBusy}
              onClick={() => setDevelopmentAuditionActive(!auditionActive)}
            >
              {auditionActive ? 'Stop audition source' : 'Audition selected'}
            </Button>
            <Button
              type="button"
              $variant="secondary"
              data-testid="develop-compare"
              disabled={!selectedCandidate || applyBusy}
              onClick={() => {
                console.debug('[CompositionDevelopmentPanel] Compare development candidate');
                refreshDevelopmentComparison();
              }}
            >
              Compare
            </Button>
            <Button
              type="button"
              data-testid="develop-apply"
              disabled={!canApply || applyBusy}
              onClick={handleApply}
            >
              Apply selected
            </Button>
            <Button
              type="button"
              $variant="secondary"
              data-testid="develop-reject"
              disabled={!selectedCandidate || applyBusy}
              onClick={() => {
                console.info('[CompositionDevelopmentPanel] Reject development candidate');
                rejectDevelopmentCandidate(selectedCandidateId);
              }}
            >
              Reject
            </Button>
          </ButtonRow>
          {developmentCompareResult ? (
            <Hint data-testid="develop-compare-summary">
              Compare vs working:{' '}
              {developmentCompareResult.identical ? 'identical' : 'differences'}
              {' · '}
              +{developmentCompareResult.events?.added || 0}
              {' / -'}
              {developmentCompareResult.events?.removed || 0}
              {' / ~'}
              {developmentCompareResult.events?.changed || 0}
            </Hint>
          ) : null}
          {currentProjectId ? (
            <ButtonRow>
              <input
                aria-label="Apply development as new branch name"
                data-testid="develop-branch-name"
                placeholder="New branch name"
                value={branchNameDraft}
                disabled={applyBusy}
                onChange={(event) => setBranchNameDraft(event.target.value)}
                style={{
                  flex: '1 1 160px',
                  padding: '8px 10px',
                  border: '1px solid #c7d2fe',
                  borderRadius: 6,
                }}
              />
              <Button
                type="button"
                data-testid="develop-apply-as-branch"
                disabled={!canApply || applyBusy || !branchNameDraft.trim()}
                onClick={async () => {
                  setApplyBusy(true);
                  try {
                    console.info('[CompositionDevelopmentPanel] Apply development as new branch');
                    await applySelectedDevelopmentCandidate({
                      asNewBranch: true,
                      branchName: branchNameDraft.trim(),
                    });
                    setBranchNameDraft('');
                  } catch {
                    // store records developmentError
                  } finally {
                    setApplyBusy(false);
                  }
                }}
              >
                Apply as new branch
              </Button>
            </ButtonRow>
          ) : null}
          {auditionActive && (
            <Hint data-testid="develop-audition-active">
              Transport plays the selected candidate. Working composition is unchanged until Apply.
            </Hint>
          )}
        </Card>
      </Grid>
    </Panel>
  );
};

export default CompositionDevelopmentPanel;
