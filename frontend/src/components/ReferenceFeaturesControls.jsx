import { useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  REFERENCE_FEATURE_DIMENSIONS,
  loadReferenceFeaturesSession,
  saveReferenceFeaturesSession,
  toggleDimensionMask,
} from '../utils/referenceFeatures.js';
import {
  CONDITIONING_STRENGTHS,
  REFERENCE_CONDITIONING_PRESETS,
  applyConditioningPreset,
  emptyPolicyState,
  loadConditioningSession,
  saveConditioningSession,
} from '../utils/referenceConditioningPolicy.js';

/**
 * Dimension mask + optional preserve/borrow/regenerate policy controls.
 * Does not copy melodies — abstract properties only.
 */
export default function ReferenceFeaturesControls({
  enabled = false,
  dimensions = null,
  report = null,
  onChange,
  affinity = null,
  compact = false,
  /** When true, show preserve / regenerate / strength / motif-reuse controls. */
  policyMode = false,
  policyState = null,
  onPolicyChange = null,
  activeProjectId = null,
  borrowProjectId = null,
  /** Enable multi-ref A/B assignment UI (AC: texture A + rhythm B). */
  allowMultiRef = false,
  /** @deprecated use allowMultiRef */
  allowMultiRefHint = false,
  projectList = null,
  primaryLabel = 'A',
  secondaryReference = null,
  secondaryStatus = 'idle',
  onSelectSecondaryProject = null,
  onClearSecondary = null,
}) {
  const multiRef = Boolean(allowMultiRef || allowMultiRefHint);
  const [localPolicy, setLocalPolicy] = useState(() => policyState || loadConditioningSession() || emptyPolicyState());

  useEffect(() => {
    if (dimensions != null || !onChange) return;
    const saved = loadReferenceFeaturesSession();
    if (saved.dimensions?.length) {
      onChange({ enabled: saved.enabled, dimensions: saved.dimensions });
    }
  }, [dimensions, onChange]);

  useEffect(() => {
    if (policyState) setLocalPolicy(policyState);
  }, [policyState]);

  const selected = Array.isArray(dimensions) ? dimensions : [];
  const policy = policyState || localPolicy;

  const emitPolicy = (next) => {
    setLocalPolicy(next);
    saveConditioningSession(next);
    onPolicyChange?.(next);
  };

  const syncDimensions = (nextDims, nextEnabled = true) => {
    const next = {
      enabled: nextEnabled,
      dimensions: nextDims,
    };
    saveReferenceFeaturesSession(next);
    onChange?.(next);
  };

  const handleToggleEnabled = (nextEnabled) => {
    syncDimensions(
      nextEnabled ? (selected.length ? selected : ['density', 'texture']) : selected,
      nextEnabled,
    );
  };

  const handleToggleDim = (dimensionId, checked) => {
    const nextDims = toggleDimensionMask(selected, dimensionId, checked);
    syncDimensions(nextDims, true);
  };

  const setDisposition = (dimensionId, disposition) => {
    const preserve = new Set(policy.preserve || []);
    const regenerate = new Set(policy.regenerate || []);
    const borrowSourceByDim = { ...(policy.borrowSourceByDim || {}) };
    preserve.delete(dimensionId);
    regenerate.delete(dimensionId);
    if (disposition === 'preserve') {
      preserve.add(dimensionId);
      delete borrowSourceByDim[dimensionId];
    } else if (disposition === 'regenerate') {
      regenerate.add(dimensionId);
      delete borrowSourceByDim[dimensionId];
    } else if (disposition === 'borrow') {
      if (!borrowSourceByDim[dimensionId]) borrowSourceByDim[dimensionId] = 'A';
    } else {
      delete borrowSourceByDim[dimensionId];
    }
    emitPolicy({
      ...policy,
      preserve: [...preserve],
      regenerate: [...regenerate],
      borrowSourceByDim,
    });
  };

  const dispositionFor = (dimensionId) => {
    if ((policy.preserve || []).includes(dimensionId)) return 'preserve';
    if ((policy.regenerate || []).includes(dimensionId)) return 'regenerate';
    if (selected.includes(dimensionId)) return 'borrow';
    return 'unspecified';
  };

  const handlePreset = (presetId) => {
    const applied = applyConditioningPreset(presetId, policy);
    if (!applied.ok) return;
    emitPolicy(applied.policy);
    const borrowDims = applied.suggestedBorrow.map((item) => item.dim);
    if (borrowDims.length) {
      const nextSelected = [...new Set([...selected.filter((d) => !(applied.policy.preserve || []).includes(d)
        && !(applied.policy.regenerate || []).includes(d)), ...borrowDims])];
      // Drop preserve/regenerate dims from borrow mask
      const cleaned = nextSelected.filter(
        (d) => !(applied.policy.preserve || []).includes(d)
          && !(applied.policy.regenerate || []).includes(d),
      );
      syncDimensions(cleaned.length ? cleaned : borrowDims, true);
    }
  };

  const secondaryProjectId = secondaryReference?.projectId || secondaryReference?.project_id || null;
  const borrowProjectIds = [borrowProjectId, secondaryProjectId].filter(Boolean);
  const motifReuseAllowed = Boolean(
    activeProjectId
    && borrowProjectIds.length
    && borrowProjectIds.every((id) => id === activeProjectId),
  );

  const projects = Array.isArray(projectList) ? projectList : [];

  return (
    <Root $compact={compact} data-testid="reference-features-controls">
      <HeaderRow>
        <label>
          <input
            type="checkbox"
            checked={Boolean(enabled)}
            onChange={(event) => handleToggleEnabled(event.target.checked)}
          />
          {' '}
          Select reference feature dimensions
        </label>
      </HeaderRow>
      <Hint>
        Uses abstract properties only — does not copy melodies or note sequences.
        Complementary to Composer Profiles.
        {multiRef ? ' Multi-reference: assign borrow dimensions to reference A or B.' : ''}
      </Hint>
      {policyMode && enabled && (
        <PresetRow data-testid="reference-conditioning-presets">
          {Object.values(REFERENCE_CONDITIONING_PRESETS).map((preset) => (
            <PresetButton
              key={preset.id}
              type="button"
              data-testid={`reference-conditioning-preset-${preset.id}`}
              onClick={() => handlePreset(preset.id)}
            >
              {preset.label}
            </PresetButton>
          ))}
        </PresetRow>
      )}
      {enabled && multiRef && (
        <BindingsPanel data-testid="reference-conditioning-bindings">
          <BindingRow>
            <strong>
              Ref
              {' '}
              {primaryLabel}
            </strong>
            <span>
              {borrowProjectId || 'primary musical reference'}
            </span>
          </BindingRow>
          <BindingRow>
            <strong>Ref B</strong>
            {secondaryProjectId ? (
              <>
                <span>{secondaryReference?.projectName || secondaryProjectId}</span>
                {typeof onClearSecondary === 'function' && (
                  <PresetButton type="button" data-testid="reference-clear-secondary" onClick={() => onClearSecondary()}>
                    Clear B
                  </PresetButton>
                )}
              </>
            ) : (
              <select
                aria-label="Select reference B project"
                data-testid="reference-secondary-project"
                value=""
                disabled={typeof onSelectSecondaryProject !== 'function' || secondaryStatus === 'loading'}
                onChange={(event) => {
                  const id = event.target.value;
                  if (id) onSelectSecondaryProject?.(id);
                }}
              >
                <option value="">
                  {secondaryStatus === 'loading' ? 'Loading…' : 'Add second reference (project)'}
                </option>
                {projects
                  .filter((item) => item.id && item.id !== borrowProjectId)
                  .map((item) => (
                    <option key={item.id} value={item.id}>{item.name || item.id}</option>
                  ))}
              </select>
            )}
          </BindingRow>
        </BindingsPanel>
      )}
      {enabled && (
        <Grid>
          {REFERENCE_FEATURE_DIMENSIONS.map((dim) => {
            const payload = report?.dimensions?.[dim.id];
            const status = payload?.status;
            const disposition = dispositionFor(dim.id);
            const borrowSource = policy.borrowSourceByDim?.[dim.id] === 'B' ? 'B' : 'A';
            return (
              <DimRow key={dim.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.includes(dim.id) || disposition === 'preserve' || disposition === 'regenerate'}
                    onChange={(event) => {
                      if (policyMode && event.target.checked && disposition === 'unspecified') {
                        handleToggleDim(dim.id, true);
                      } else if (!policyMode) {
                        handleToggleDim(dim.id, event.target.checked);
                      } else if (!event.target.checked) {
                        handleToggleDim(dim.id, false);
                        setDisposition(dim.id, 'unspecified');
                      } else {
                        handleToggleDim(dim.id, true);
                      }
                    }}
                  />
                  {' '}
                  {dim.label}
                </label>
                {status && status !== 'ok' && (
                  <Badge $kind={status}>{status}</Badge>
                )}
                {payload?.summary && (
                  <Summary title={payload.summary}>{payload.summary}</Summary>
                )}
                {policyMode && (
                  <PolicyRow>
                    <select
                      aria-label={`${dim.label} disposition`}
                      value={disposition}
                      onChange={(event) => {
                        const next = event.target.value;
                        if (next === 'borrow') {
                          setDisposition(dim.id, 'borrow');
                          handleToggleDim(dim.id, true);
                        } else if (next === 'unspecified') {
                          handleToggleDim(dim.id, false);
                          setDisposition(dim.id, 'unspecified');
                        } else {
                          handleToggleDim(dim.id, false);
                          setDisposition(dim.id, next);
                        }
                      }}
                    >
                      <option value="unspecified">Unspecified</option>
                      <option value="preserve">Preserve (current)</option>
                      <option value="borrow">Borrow (reference)</option>
                      <option value="regenerate">Regenerate (invent new)</option>
                    </select>
                    {disposition === 'borrow' && multiRef && (
                      <select
                        aria-label={`${dim.label} reference source`}
                        data-testid={`reference-borrow-source-${dim.id}`}
                        value={borrowSource}
                        onChange={(event) => {
                          const source = event.target.value === 'B' ? 'B' : 'A';
                          emitPolicy({
                            ...policy,
                            borrowSourceByDim: {
                              ...(policy.borrowSourceByDim || {}),
                              [dim.id]: source,
                            },
                          });
                        }}
                      >
                        <option value="A">
                          From A
                        </option>
                        <option value="B" disabled={!secondaryProjectId}>
                          From B
                          {!secondaryProjectId ? ' (add ref B)' : ''}
                        </option>
                      </select>
                    )}
                    {disposition === 'borrow' && (
                      <select
                        aria-label={`${dim.label} strength`}
                        value={policy.dimensionStrengths?.[dim.id] || policy.defaultBorrowStrength || 'normal'}
                        onChange={(event) => {
                          emitPolicy({
                            ...policy,
                            dimensionStrengths: {
                              ...(policy.dimensionStrengths || {}),
                              [dim.id]: event.target.value,
                            },
                          });
                        }}
                      >
                        {CONDITIONING_STRENGTHS.map((level) => (
                          <option key={level} value={level}>{level}</option>
                        ))}
                      </select>
                    )}
                  </PolicyRow>
                )}
              </DimRow>
            );
          })}
        </Grid>
      )}
      {policyMode && enabled && (
        <MotifRow>
          <label title={motifReuseAllowed ? undefined : 'Only when every borrow reference project_id matches the open project'}>
            <input
              type="checkbox"
              checked={Boolean(policy.allowMotifReuse) && motifReuseAllowed}
              disabled={!motifReuseAllowed}
              onChange={(event) => {
                emitPolicy({ ...policy, allowMotifReuse: event.target.checked });
              }}
            />
            {' '}
            Allow own-project motif characteristic reuse (still no note sequences)
          </label>
        </MotifRow>
      )}
      {affinity && (
        <Affinity>
          Affinity (structural only, not quality):
          {' '}
          {Object.entries(affinity.per_group || {})
            .map(([key, value]) => `${key}=${Number(value).toFixed(2)}`)
            .join(', ') || 'n/a'}
        </Affinity>
      )}
    </Root>
  );
}

const Root = styled.div`
  margin-top: ${(p) => (p.$compact ? '0.35rem' : '0.6rem')};
  padding: 0.5rem 0.65rem;
  border: 1px solid rgba(120, 120, 120, 0.25);
  border-radius: 4px;
  font-size: 0.85rem;
`;

const HeaderRow = styled.div`
  font-weight: 600;
`;

const Hint = styled.p`
  margin: 0.35rem 0 0.5rem;
  opacity: 0.8;
  font-size: 0.8rem;
`;

const PresetRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-bottom: 0.5rem;
`;

const PresetButton = styled.button`
  font-size: 0.75rem;
  padding: 0.2rem 0.45rem;
  border: 1px solid rgba(120, 120, 120, 0.35);
  border-radius: 3px;
  background: transparent;
  cursor: pointer;
  &:hover {
    background: rgba(120, 120, 120, 0.12);
  }
`;

const BindingsPanel = styled.div`
  display: grid;
  gap: 0.35rem;
  margin-bottom: 0.5rem;
  font-size: 0.8rem;
`;

const BindingRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
`;

const Grid = styled.div`
  display: grid;
  gap: 0.35rem;
`;

const DimRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.35rem 0.6rem;
`;

const Badge = styled.span`
  font-size: 0.7rem;
  text-transform: uppercase;
  padding: 0.1rem 0.35rem;
  border-radius: 3px;
  background: ${(p) => (p.$kind === 'unavailable' ? 'rgba(180,60,60,0.2)' : 'rgba(180,140,40,0.25)')};
`;

const Summary = styled.span`
  opacity: 0.75;
  font-size: 0.75rem;
  max-width: 28rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const Affinity = styled.div`
  margin-top: 0.45rem;
  font-size: 0.75rem;
  opacity: 0.85;
`;

const PolicyRow = styled.div`
  display: flex;
  gap: 0.35rem;
  flex-wrap: wrap;
`;

const MotifRow = styled.div`
  margin-top: 0.5rem;
  font-size: 0.8rem;
`;
