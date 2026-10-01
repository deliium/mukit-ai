import React, { Suspense, lazy, useEffect } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition } from '../utils/musicJsonValidation.js';
import { critiqueSummaryFromArtifact } from '../utils/compositionCritique.js';
import { createAppLogger } from '../utils/appLogger.js';
import { normalizeRevisionMode } from '../utils/revisionLoopModes.js';
import { operationSummaryText } from '../utils/operationSummaryText.js';
import AutonomousComposerPanel from './AutonomousComposerPanel.jsx';
import FilmScorePanel from './FilmScorePanel.jsx';
import FilmScoreAdaptPanel from './FilmScoreAdaptPanel.jsx';

const WorkflowListeningPanel = lazy(() => import('./WorkflowListeningPanel.jsx'));

const logger = createAppLogger('MultiAgentPanel');

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
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

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Label = styled.label`
  font-size: 0.85rem;
  color: #312e81;
  display: flex;
  align-items: center;
  gap: 6px;
`;

const Select = styled.select`
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  padding: 6px 8px;
  font-size: 0.85rem;
  color: #312e81;
  background: #fff;
`;

const Button = styled.button`
  border: 1px solid #c7d2fe;
  background: ${(p) => (p.$primary ? '#4338ca' : '#fff')};
  color: ${(p) => (p.$primary ? '#fff' : '#312e81')};
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 0.9rem;
  cursor: pointer;

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
`;

const PassList = styled.ul`
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
`;

const PassItem = styled.li`
  border: 1px solid ${(p) => (p.$active ? '#a5b4fc' : '#e2e8f0')};
  background: ${(p) => (p.$active ? '#eef2ff' : '#f8fafc')};
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 0.8rem;
  color: #334155;
  cursor: pointer;
`;

const Meta = styled.pre`
  margin: 0;
  padding: 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 0.75rem;
  overflow: auto;
  max-height: 180px;
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

const MODE_OPTIONS = [
  { value: 'off', label: 'Off (no revise)' },
  { value: 'fast', label: 'Fast (max 1)' },
  { value: 'balanced', label: 'Balanced (max 2)' },
  { value: 'thorough', label: 'Thorough (max 3)' },
];

/**
 * Thin V4 multi-agent panel: preview spine → Apply via multi-agent-apply CAS.
 * Optional bounded revision modes; never auto-Apply.
 */
const MultiAgentPanel = () => {
  const composition = useMusicStore((s) => s.editedMusicJson || s.generatedMusicJson);
  const status = useMusicStore((s) => s.multiAgentStatus);
  const error = useMusicStore((s) => s.multiAgentError);
  const candidate = useMusicStore((s) => s.multiAgentCandidate);
  const revisionMode = useMusicStore((s) => s.multiAgentRevisionMode);
  const revisionHistory = useMusicStore((s) => s.multiAgentRevisionHistory);
  const passCandidates = useMusicStore((s) => s.multiAgentPassCandidates);
  const stopReason = useMusicStore((s) => s.multiAgentStopReason);
  const operationSummary = useMusicStore((s) => s.multiAgentOperationSummary);
  const loopStatus = useMusicStore((s) => s.multiAgentRevisionLoopStatus);
  const comparePassIndex = useMusicStore((s) => s.multiAgentComparePassIndex);
  const auditionActive = useMusicStore((s) => s.multiAgentAuditionActive);
  const preview = useMusicStore((s) => s.previewMultiAgentWorkflow);
  const apply = useMusicStore((s) => s.applyMultiAgentCandidate);
  const discard = useMusicStore((s) => s.discardMultiAgentCandidate);
  const cancelPreview = useMusicStore((s) => s.cancelMultiAgentPreview);
  const setMode = useMusicStore((s) => s.setMultiAgentRevisionMode);
  const setComparePass = useMusicStore((s) => s.setMultiAgentComparePassIndex);
  const setAuditionActive = useMusicStore((s) => s.setMultiAgentAuditionActive);

  const canPreview = isCanonicalComposition(composition);
  const loading = status === 'loading' || loopStatus === 'running';
  const canAudition = (Array.isArray(passCandidates) && passCandidates.some((c) => c?.composition))
    || Boolean(candidate?.composition);
  const critiqueSummary = (() => {
    const log = candidate?.artifact_log || candidate?.artifactLog || [];
    if (!Array.isArray(log)) return null;
    const art = log.find((a) => a?.content_type === 'agent.critique.v1');
    return critiqueSummaryFromArtifact(art);
  })();

  const selectedPass = Array.isArray(revisionHistory)
    ? revisionHistory.find((p) => p.pass_index === comparePassIndex) || revisionHistory[0]
    : null;
  const summaryText = operationSummaryText(operationSummary);
  useEffect(() => {
    if (!summaryText) return;
    const runId = String(operationSummary?.run_id || '');
    console.debug('[MultiAgentPanel] operation summary', {
      run_id_prefix: runId.slice(0, 12),
      status: operationSummary?.status || null,
      model_call_count: operationSummary?.model_call_count ?? null,
      revision_count: operationSummary?.revision_count ?? null,
    });
  }, [summaryText, operationSummary]);

  return (
    <Panel data-testid="multi-agent-panel">
      <AutonomousComposerPanel />
      <FilmScorePanel />
      <FilmScoreAdaptPanel />
      <Suspense fallback={null}>
        <WorkflowListeningPanel />
      </Suspense>
      <Title>Multi-agent (V4)</Title>
      <Hint>
        Runs Creative Director → Harmony → Melody → Arrangement → Critic as a session
        preview. Optional revision modes run a bounded Critic → revise → re-critique
        loop (never unbounded). Critic approve does not save; Apply uses
        multi-agent-apply via the revision CAS path.
      </Hint>
      <Row>
        <Label>
          Revision mode
          <Select
            data-testid="multi-agent-revision-mode"
            value={revisionMode || 'off'}
            disabled={loading}
            onChange={(e) => {
              const mode = normalizeRevisionMode(e.target.value);
              logger.debug('Revision mode change', { mode });
              setMode(mode);
            }}
          >
            {MODE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        </Label>
      </Row>
      <Row>
        <Button
          type="button"
          $primary
          disabled={!canPreview || loading}
          onClick={() => {
            void preview();
          }}
        >
          {loading ? 'Running…' : 'Preview spine'}
        </Button>
        <Button
          type="button"
          disabled={!loading}
          onClick={() => {
            logger.debug('Cancel revision loop');
            cancelPreview();
          }}
        >
          Cancel
        </Button>
        <Button
          type="button"
          data-testid="multi-agent-audition"
          disabled={!canAudition || loading}
          aria-pressed={auditionActive}
          onClick={() => {
            const next = !auditionActive;
            logger.debug('Toggle multi-agent audition', {
              active: next,
              passIndex: comparePassIndex,
            });
            setAuditionActive(next);
          }}
        >
          {auditionActive ? 'Stop audition' : 'Audition selected pass'}
        </Button>
        <Button
          type="button"
          disabled={!candidate || loading}
          onClick={() => {
            void apply();
          }}
        >
          Apply candidate
        </Button>
        <Button
          type="button"
          disabled={!candidate || loading}
          onClick={() => discard()}
        >
          Discard
        </Button>
      </Row>
      {error ? <ErrorText>{error}</ErrorText> : null}
      {stopReason ? (
        <Hint data-testid="multi-agent-stop-reason">
          Stop reason: {stopReason}
          {loopStatus === 'cancelled' ? ' (cancelled)' : ''}
        </Hint>
      ) : null}
      {summaryText ? (
        <Hint data-testid="multi-agent-operation-summary">{summaryText}</Hint>
      ) : null}
      {critiqueSummary ? (
        <Hint data-testid="multi-agent-critique-summary">
          Critique: {critiqueSummary.recommendation}
          {critiqueSummary.findings?.length
            ? ` · ${critiqueSummary.findings.length} finding(s)`
            : ''}
          {critiqueSummary.stratum_counts?.stylistic
            ? ` · stylistic ${critiqueSummary.stratum_counts.stylistic}`
            : ''}
        </Hint>
      ) : null}
      {Array.isArray(revisionHistory) && revisionHistory.length > 0 ? (
        <div>
          <Hint>Revision passes (select to compare / audition)</Hint>
          <PassList data-testid="multi-agent-revision-history">
            {revisionHistory.map((pass) => {
              const active = pass.pass_index === comparePassIndex;
              const fp = String(pass.candidate_fingerprint || '').slice(0, 12);
              return (
                <PassItem
                  key={`pass-${pass.pass_index}`}
                  $active={active}
                  onClick={() => {
                    logger.debug('Compare pass selected', { passIndex: pass.pass_index });
                    setComparePass(pass.pass_index);
                  }}
                >
                  Pass {pass.pass_index}
                  {pass.pass_index === 0 ? ' (initial)' : ''}
                  {' · '}
                  {pass.critique_recommendation || '—'}
                  {' · '}
                  {fp || 'no-fp'}
                  {pass.validation_result?.status
                    ? ` · val ${pass.validation_result.status}`
                    : ''}
                </PassItem>
              );
            })}
          </PassList>
          {selectedPass && candidate ? (
            <Hint data-testid="multi-agent-compare-summary">
              Compare: initial/pass0 fp{' '}
              {String(revisionHistory[0]?.candidate_fingerprint || '').slice(0, 12)}
              {' vs pass '}
              {selectedPass.pass_index}{' '}
              {String(selectedPass.candidate_fingerprint || '').slice(0, 12)}
              {' · final '}
              {String(candidate.candidate_fingerprint || candidate.candidateFingerprint || '').slice(0, 12)}
            </Hint>
          ) : null}
          {auditionActive ? (
            <Hint data-testid="multi-agent-audition-active">
              Auditioning pass {comparePassIndex ?? 0} (session preview; Apply still required)
            </Hint>
          ) : null}
        </div>
      ) : null}
      {candidate ? (
        <Meta>
          {JSON.stringify(
            {
              operation_type: candidate.operation_type,
              recommendation: candidate.recommendation,
              stop_reason: stopReason,
              revision_mode: revisionMode,
              agent_sequence: candidate.agent_sequence,
              source_fingerprint: String(candidate.source_fingerprint || '').slice(0, 16),
              candidate_fingerprint: String(candidate.candidate_fingerprint || '').slice(0, 16),
              stage_agent_ids: Array.isArray(candidate.stages)
                ? candidate.stages.map((s) => s.agent_id).filter(Boolean)
                : [],
            },
            null,
            2,
          )}
        </Meta>
      ) : null}
    </Panel>
  );
};

export default MultiAgentPanel;
