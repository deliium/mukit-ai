import { useEffect } from 'react';
import styled from 'styled-components';

import { useMusicStore } from '../store/musicStore.js';
import { CATALOG_PRESET_IDS } from '../utils/performanceConductor/constants.js';

const Wrap = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 720px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  font: inherit;
  padding: 6px 12px;
  border: 1px solid #cbd5e1;
  background: ${(p) => (p.$active ? '#0f172a' : '#fff')};
  color: ${(p) => (p.$active ? '#fff' : '#0f172a')};
  cursor: pointer;
`;

const Select = styled.select`
  font: inherit;
  padding: 6px 8px;
  min-width: 200px;
`;

const Banner = styled.p`
  margin: 0;
  padding: 8px 10px;
  background: #fff7ed;
  border: 1px solid #fdba74;
  color: #9a3412;
  font-size: 0.9rem;
`;

const Muted = styled.p`
  margin: 0;
  color: #64748b;
  font-size: 0.9rem;
`;

const Metrics = styled.pre`
  margin: 0;
  padding: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  font-size: 0.8rem;
  overflow: auto;
`;

/**
 * Thin Performance studio tab: catalog clone, plan select, before/after audition.
 * Opening the tab never auto-INSERTS plans.
 */
const PerformancePanel = () => {
  const currentProjectId = useMusicStore((s) => s.currentProjectId);
  const loadPerformanceStudio = useMusicStore((s) => s.loadPerformanceStudio);
  const clonePerformancePreset = useMusicStore((s) => s.clonePerformancePreset);
  const selectPerformancePlan = useMusicStore((s) => s.selectPerformancePlan);
  const setPerformanceAuditionMode = useMusicStore((s) => s.setPerformanceAuditionMode);
  const compareSelectedPerformancePlan = useMusicStore((s) => s.compareSelectedPerformancePlan);
  const plans = useMusicStore((s) => s.performancePlans);
  const presets = useMusicStore((s) => s.performancePresets);
  const selectedPlanId = useMusicStore((s) => s.performanceSelectedPlanId);
  const status = useMusicStore((s) => s.performanceStatus);
  const error = useMusicStore((s) => s.performanceError);
  const auditionMode = useMusicStore((s) => s.performanceAuditionMode);
  const stale = useMusicStore((s) => s.performanceStale);
  const compare = useMusicStore((s) => s.performanceCompare);
  const realization = useMusicStore((s) => s.performanceRealization);

  useEffect(() => {
    if (!currentProjectId) return undefined;
    loadPerformanceStudio();
    return undefined;
  }, [currentProjectId, loadPerformanceStudio]);

  if (!currentProjectId) {
    return (
      <Wrap data-testid="performance-panel">
        <Muted>Open a project to manage performance plans.</Muted>
      </Wrap>
    );
  }

  const metrics = realization?.metrics || compare?.performed_metrics;

  return (
    <Wrap data-testid="performance-panel">
      <div>
        <h3 style={{ margin: '0 0 4px' }}>Performance</h3>
        <Muted>
          Conductor plans reinterpret the same composition.v2 notes without changing pitch or harmony.
          Audition calls the API realize path — there is no browser conductor twin.
        </Muted>
      </div>

      <div>
        <Muted style={{ marginBottom: 6 }}>Clone preset into a plan (catalog is read-only)</Muted>
        <Row>
          {(presets.length ? presets.map((p) => p.preset_id) : CATALOG_PRESET_IDS).map((id) => (
            <Button
              key={id}
              type="button"
              onClick={() => clonePerformancePreset(id)}
              disabled={status === 'loading'}
            >
              Clone {id}
            </Button>
          ))}
        </Row>
      </div>

      <div>
        <Muted style={{ marginBottom: 6 }}>Saved plans</Muted>
        {plans.length === 0 ? (
          <Muted>No plans yet. Clone a preset to create one. Opening this tab never writes.</Muted>
        ) : (
          <Select
            aria-label="Performance plan"
            value={selectedPlanId || ''}
            onChange={(event) => selectPerformancePlan(event.target.value || null)}
          >
            {plans.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.name} ({plan.preset_id})
              </option>
            ))}
          </Select>
        )}
      </div>

      <div>
        <Muted style={{ marginBottom: 6 }}>Audition</Muted>
        <Row>
          <Button
            type="button"
            $active={auditionMode === 'mechanical'}
            onClick={() => setPerformanceAuditionMode('mechanical')}
          >
            Mechanical (before)
          </Button>
          <Button
            type="button"
            $active={auditionMode === 'performed'}
            onClick={() => setPerformanceAuditionMode('performed')}
            disabled={!selectedPlanId}
          >
            Performed (after)
          </Button>
          <Button
            type="button"
            onClick={() => compareSelectedPerformancePlan()}
            disabled={!selectedPlanId}
          >
            Compare metrics
          </Button>
        </Row>
      </div>

      {stale ? (
        <Banner role="status">
          Soft-stale: this plan’s composition fingerprint does not match the current draft.
          Audition still uses the request composition and does not rewrite the project score.
        </Banner>
      ) : null}

      {error ? <Banner role="alert">{error}</Banner> : null}

      {metrics ? (
        <Metrics>
          {JSON.stringify(
            {
              note_count: metrics.note_count,
              mean_abs_tick_delta: metrics.mean_abs_tick_delta,
              mean_abs_velocity_delta: metrics.mean_abs_velocity_delta,
              sustain_span_count: metrics.sustain_span_count,
              mode: auditionMode,
            },
            null,
            2,
          )}
        </Metrics>
      ) : null}
    </Wrap>
  );
};

export default PerformancePanel;
