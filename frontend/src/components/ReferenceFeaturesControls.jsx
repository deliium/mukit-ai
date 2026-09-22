import { useEffect } from 'react';
import styled from 'styled-components';
import {
  REFERENCE_FEATURE_DIMENSIONS,
  loadReferenceFeaturesSession,
  saveReferenceFeaturesSession,
  toggleDimensionMask,
} from '../utils/referenceFeatures.js';

/**
 * Dimension mask checkboxes for musical reference conditioning.
 * Does not copy melodies — abstract properties only.
 */
export default function ReferenceFeaturesControls({
  enabled = false,
  dimensions = null,
  report = null,
  onChange,
  affinity = null,
  compact = false,
}) {
  useEffect(() => {
    // Hydrate from session once if parent has no dimensions yet.
    if (dimensions != null || !onChange) return;
    const saved = loadReferenceFeaturesSession();
    if (saved.dimensions?.length) {
      onChange({ enabled: saved.enabled, dimensions: saved.dimensions });
    }
  }, [dimensions, onChange]);

  const selected = Array.isArray(dimensions) ? dimensions : [];
  const maskActive = enabled && selected.length > 0;

  const handleToggleEnabled = (nextEnabled) => {
    const next = {
      enabled: nextEnabled,
      dimensions: nextEnabled
        ? (selected.length ? selected : ['density', 'texture'])
        : selected,
    };
    saveReferenceFeaturesSession(next);
    onChange?.(next);
  };

  const handleToggleDim = (dimensionId, checked) => {
    const nextDims = toggleDimensionMask(selected, dimensionId, checked);
    const next = { enabled: true, dimensions: nextDims };
    saveReferenceFeaturesSession(next);
    onChange?.(next);
  };

  return (
    <Root $compact={compact}>
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
      </Hint>
      {enabled && (
        <Grid>
          {REFERENCE_FEATURE_DIMENSIONS.map((dim) => {
            const payload = report?.dimensions?.[dim.id];
            const status = payload?.status;
            return (
              <DimRow key={dim.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={selected.includes(dim.id)}
                    onChange={(event) => handleToggleDim(dim.id, event.target.checked)}
                  />
                  {' '}
                  {dim.label}
                </label>
                {status && status !== 'ok' && (
                  <Badge $tone={status === 'unavailable' ? 'error' : 'warn'}>
                    {status}
                  </Badge>
                )}
                {payload?.summary && (
                  <Summary title={payload.summary}>{payload.summary}</Summary>
                )}
              </DimRow>
            );
          })}
        </Grid>
      )}
      {maskActive && affinity?.per_dimension && (
        <AffinityBlock>
          Affinity (not quality):
          {' '}
          {Object.entries(affinity.per_dimension)
            .map(([id, score]) => `${id}=${Number(score).toFixed(2)}`)
            .join(', ')}
        </AffinityBlock>
      )}
    </Root>
  );
}

const Root = styled.div`
  display: flex;
  flex-direction: column;
  gap: ${({ $compact }) => ($compact ? '0.35rem' : '0.5rem')};
  margin-top: 0.5rem;
  font-size: 0.85rem;
`;

const HeaderRow = styled.div`
  font-weight: 600;
`;

const Hint = styled.p`
  margin: 0;
  opacity: 0.75;
  font-size: 0.8rem;
`;

const Grid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  gap: 0.35rem 0.75rem;
`;

const DimRow = styled.div`
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
`;

const Badge = styled.span`
  align-self: flex-start;
  font-size: 0.7rem;
  padding: 0.05rem 0.35rem;
  border-radius: 3px;
  background: ${({ $tone }) => ($tone === 'error' ? '#fde8e8' : '#fff4d6')};
  color: ${({ $tone }) => ($tone === 'error' ? '#8a1f1f' : '#7a5b00')};
`;

const Summary = styled.span`
  font-size: 0.75rem;
  opacity: 0.8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
`;

const AffinityBlock = styled.div`
  font-size: 0.75rem;
  opacity: 0.85;
`;
