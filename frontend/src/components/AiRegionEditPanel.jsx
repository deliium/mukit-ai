import React, { useMemo } from 'react';
import styled from 'styled-components';
import { editCompositionRegion } from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { countV2FeatureSummary } from '../utils/pianoRollEvents.js';
import { defaultTargetTrackIds } from '../utils/pianoRollSelection.js';

const Panel = styled.section`
  margin: 16px 0;
  padding: 16px;
  background: #ffffff;
  border: 1px solid #e0e7ff;
  border-radius: 12px;
`;

const Title = styled.h4`
  margin: 0 0 10px;
  color: #312e81;
`;

const Meta = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  margin-bottom: 12px;
  font-size: 0.9rem;
  color: #374151;
`;

const TextArea = styled.textarea`
  width: 100%;
  min-height: 84px;
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
  margin-top: 12px;
`;

const Button = styled.button`
  padding: 8px 14px;
  border: none;
  border-radius: 6px;
  background: #4f46e5;
  color: white;
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }
`;

const Status = styled.div`
  margin-top: 10px;
  font-size: 0.85rem;
  color: ${(props) => {
    if (props.$tone === 'error') return '#991b1b';
    if (props.$tone === 'warn') return '#92400e';
    return '#1e3a8a';
  }};
`;

const AiRegionEditPanel = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const availableLlmModels = useMusicStore((state) => state.availableLlmModels);
  const aiEditStartBar = useMusicStore((state) => state.aiEditStartBar);
  const aiEditEndBar = useMusicStore((state) => state.aiEditEndBar);
  const aiEditTrackMode = useMusicStore((state) => state.aiEditTrackMode);
  const aiEditTrackIds = useMusicStore((state) => state.aiEditTrackIds);
  const aiEditInstruction = useMusicStore((state) => state.aiEditInstruction);
  const aiEditStatus = useMusicStore((state) => state.aiEditStatus);
  const aiEditError = useMusicStore((state) => state.aiEditError);
  const aiEditWarnings = useMusicStore((state) => state.aiEditWarnings);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);
  const setAiEditInstruction = useMusicStore((state) => state.setAiEditInstruction);
  const startAiEdit = useMusicStore((state) => state.startAiEdit);
  const failAiEdit = useMusicStore((state) => state.failAiEdit);
  const completeAiEdit = useMusicStore((state) => state.completeAiEdit);
  const rejectAiEditCandidate = useMusicStore((state) => state.rejectAiEditCandidate);
  const applyAiEditCandidate = useMusicStore((state) => state.applyAiEditCandidate);
  const setAiEditAuditionActive = useMusicStore((state) => state.setAiEditAuditionActive);
  const refreshAiEditComparison = useMusicStore((state) => state.refreshAiEditComparison);
  const aiEditCandidate = useMusicStore((state) => state.aiEditCandidate);
  const aiEditAuditionActive = useMusicStore((state) => state.aiEditAuditionActive);
  const aiEditCompareResult = useMusicStore((state) => state.aiEditCompareResult);
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const openDevelopWithAiSelection = useMusicStore((state) => state.openDevelopWithAiSelection);
  const [branchNameDraft, setBranchNameDraft] = React.useState('');
  const [applyBusy, setApplyBusy] = React.useState(false);

  const validation = useMemo(
    () => (editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false, message: 'No composition' }),
    [editedMusicJson],
  );
  const canonical = isCanonicalComposition(editedMusicJson);
  const trackIds = useMemo(() => {
    if (Array.isArray(aiEditTrackIds) && aiEditTrackIds.length) {
      return aiEditTrackIds;
    }
    const defaults = defaultTargetTrackIds(editedMusicJson, {
      mode: aiEditTrackMode,
      currentTrackId: pianoRollTrackId,
    });
    console.debug('[FIX:ai-region-track-defaults] Defaulted AI edit track targets', {
      mode: aiEditTrackMode,
      trackCount: defaults.length,
      hasCurrentTrack: Boolean(pianoRollTrackId),
    });
    return defaults;
  }, [aiEditTrackIds, editedMusicJson, aiEditTrackMode, pianoRollTrackId]);
  const featureCounts = useMemo(
    () => countV2FeatureSummary(editedMusicJson),
    [editedMusicJson],
  );

  const disabledReason = useMemo(() => {
    if (!editedMusicJson || !canonical || !validation.valid) {
      return 'Canonical composition is required';
    }
    if (!aiEditStartBar || !aiEditEndBar) {
      return 'Select a bar range in the piano roll (Shift+drag or start/end controls)';
    }
    if (!selectedProvider || !selectedModel) {
      return 'Configure an LLM provider/model first';
    }
    if (!String(aiEditInstruction || '').trim()) {
      return 'Enter a non-empty edit instruction';
    }
    if (!availableLlmModels.length) {
      return 'No LLM models are available';
    }
    if (aiEditStatus === 'loading') {
      return 'Edit already in progress';
    }
    return '';
  }, [
    editedMusicJson,
    canonical,
    validation.valid,
    aiEditStartBar,
    aiEditEndBar,
    selectedProvider,
    selectedModel,
    aiEditInstruction,
    availableLlmModels.length,
    aiEditStatus,
  ]);

  const handleSubmit = async () => {
    if (disabledReason) {
      console.debug('[AiRegionEditPanel] Submit disabled', { reason: disabledReason });
      return;
    }
    const started = await startAiEdit();
    if (!started) {
      return;
    }

    const payload = {
      composition: editedMusicJson,
      edit: {
        instruction: String(aiEditInstruction).trim(),
        selection: {
          start_bar: aiEditStartBar,
          end_bar: aiEditEndBar,
          track_ids: trackIds,
        },
        allow_harmony_changes: false,
        allow_added_tracks: /counter[\s-]?melody/i.test(aiEditInstruction),
      },
      selection: {
        provider: selectedProvider || null,
        model: selectedModel || null,
      },
      options: {
        max_retries: 1,
      },
    };

    console.info('[AiRegionEditPanel] User-initiated AI region edit', {
      startBar: aiEditStartBar,
      endBar: aiEditEndBar,
      trackScopeCount: trackIds.length,
      provider: selectedProvider,
      model: selectedModel,
    });

    try {
      const response = await editCompositionRegion(payload);
      const staged = await completeAiEdit({
        composition: response.composition,
        musicxml: response.musicxml || '',
        warnings: response.warnings || [],
        provider: response.provider,
        model: response.model,
      });
      if (!staged) {
        return;
      }
      console.debug('[AiRegionEditPanel] AI edit candidate staged', {
        eventCount: Array.isArray(response.composition?.tracks)
          ? response.composition.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
          : 0,
      });
    } catch (error) {
      const message = error.message || 'AI region edit failed';
      console.error('[AiRegionEditPanel] Submission failure', { message });
      failAiEdit(message);
    }
  };

  if (!editedMusicJson) {
    return null;
  }

  return (
    <Panel aria-label="AI region edit panel">
      <Title>AI Region Edit</Title>
      <Meta>
        <span>
          Range: {aiEditStartBar && aiEditEndBar ? `bars ${aiEditStartBar}-${aiEditEndBar}` : 'none'}
        </span>
        <span>
          Track scope: {aiEditTrackMode === 'all' ? 'all tracks' : `current (${trackIds.join(', ') || 'none'})`}
        </span>
        <span>
          Provider/model: {selectedProvider || '—'} / {selectedModel || '—'}
        </span>
        <span>
          V2 expression preserved: tempo {featureCounts.tempoChanges}, dynamics {featureCounts.dynamicMarks},
          ties {featureCounts.tiedNotes}
        </span>
      </Meta>
      <TextArea
        data-testid="ai-edit-instruction"
        aria-label="AI edit instruction"
        value={aiEditInstruction}
        onChange={(event) => setAiEditInstruction(event.target.value)}
        placeholder="Example: make this phrase more dramatic but keep the harmony"
      />
      <ButtonRow>
        <Button
          type="button"
          data-testid="ai-edit-submit"
          onClick={handleSubmit}
          disabled={Boolean(disabledReason)}
          aria-label="Regenerate selection with AI edit"
        >
          {aiEditStatus === 'loading' ? 'Editing selection…' : 'Regenerate Selection / AI Edit'}
        </Button>
        <Button
          type="button"
          data-testid="ai-edit-open-develop"
          onClick={() => openDevelopWithAiSelection()}
          disabled={!aiEditStartBar || !aiEditEndBar}
          aria-label="Open Develop tab with current selection"
          style={{ background: '#475569' }}
        >
          Develop selection…
        </Button>
      </ButtonRow>
      {aiEditCandidate ? (
        <div data-testid="ai-edit-candidate-panel" style={{ marginTop: 12 }}>
          <Status $tone="info">
            AI edit preview ready
            {aiEditCandidate.provider
              ? ` · ${aiEditCandidate.provider}/${aiEditCandidate.model || '—'}`
              : ''}
            . Working composition is unchanged until Apply.
          </Status>
          {aiEditCompareResult ? (
            <Status $tone="info" data-testid="ai-edit-compare-summary">
              Compare vs working:{' '}
              {aiEditCompareResult.identical ? 'identical' : 'differences'}
              {' · '}
              +{aiEditCompareResult.events?.added || 0}
              {' / -'}
              {aiEditCompareResult.events?.removed || 0}
              {' / ~'}
              {aiEditCompareResult.events?.changed || 0}
            </Status>
          ) : null}
          <ButtonRow>
            <Button
              type="button"
              data-testid="ai-edit-audition-toggle"
              disabled={applyBusy}
              style={{ background: '#475569' }}
              onClick={() => {
                const next = !aiEditAuditionActive;
                console.debug('[AiRegionEditPanel] Toggle AI edit audition', { active: next });
                setAiEditAuditionActive(next);
              }}
            >
              {aiEditAuditionActive ? 'Play working' : 'Audition candidate'}
            </Button>
            <Button
              type="button"
              data-testid="ai-edit-compare"
              disabled={applyBusy}
              style={{ background: '#475569' }}
              onClick={() => {
                console.debug('[AiRegionEditPanel] Compare AI edit candidate');
                refreshAiEditComparison();
              }}
            >
              Compare
            </Button>
            <Button
              type="button"
              data-testid="ai-edit-apply"
              disabled={applyBusy}
              style={{ background: '#059669' }}
              onClick={async () => {
                setApplyBusy(true);
                try {
                  console.info('[AiRegionEditPanel] Apply AI edit candidate');
                  await applyAiEditCandidate();
                } catch {
                  // store records uiError / aiEditError
                } finally {
                  setApplyBusy(false);
                }
              }}
            >
              Apply
            </Button>
            <Button
              type="button"
              data-testid="ai-edit-reject"
              disabled={applyBusy}
              style={{ background: '#dc2626' }}
              onClick={() => {
                console.info('[AiRegionEditPanel] Reject AI edit candidate');
                rejectAiEditCandidate();
              }}
            >
              Reject
            </Button>
          </ButtonRow>
          {currentProjectId ? (
            <ButtonRow>
              <input
                aria-label="Apply AI edit as new branch name"
                data-testid="ai-edit-branch-name"
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
                data-testid="ai-edit-apply-as-branch"
                disabled={applyBusy || !branchNameDraft.trim()}
                style={{ background: '#7c3aed' }}
                onClick={async () => {
                  setApplyBusy(true);
                  try {
                    console.info('[AiRegionEditPanel] Apply AI edit as new branch');
                    await applyAiEditCandidate({
                      asNewBranch: true,
                      branchName: branchNameDraft.trim(),
                    });
                    setBranchNameDraft('');
                  } catch {
                    // store records uiError / aiEditError
                  } finally {
                    setApplyBusy(false);
                  }
                }}
              >
                Apply as new branch
              </Button>
            </ButtonRow>
          ) : (
            <Status $tone="warn">
              No project open: Apply installs locally only (no durable history).
            </Status>
          )}
        </div>
      ) : null}
      {disabledReason && aiEditStatus !== 'loading' && (
        <Status $tone="warn">{disabledReason}</Status>
      )}
      {aiEditStatus === 'error' && aiEditError && (
        <Status $tone="error">{aiEditError}</Status>
      )}
      {aiEditStatus === 'success' && aiEditCandidate && (
        <Status $tone="info">Preview staged. Apply to update the working composition.</Status>
      )}
      {aiEditWarnings.map((warning, index) => (
        <Status key={`${index}:${warning}`} $tone="warn">{warning}</Status>
      ))}
    </Panel>
  );
};

export default AiRegionEditPanel;
