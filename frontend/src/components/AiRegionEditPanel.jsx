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
  const refreshMusicXmlFromEditedComposition = useMusicStore(
    (state) => state.refreshMusicXmlFromEditedComposition,
  );
  const openDevelopWithAiSelection = useMusicStore((state) => state.openDevelopWithAiSelection);

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
    const started = startAiEdit();
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
      const applied = completeAiEdit({
        composition: response.composition,
        musicxml: response.musicxml || '',
        warnings: response.warnings || [],
      });
      if (!applied) {
        return;
      }
      console.debug('[AiRegionEditPanel] Notation refresh requested after AI edit', {
        eventCount: Array.isArray(response.composition?.tracks)
          ? response.composition.tracks.reduce((total, track) => total + (track.events?.length || 0), 0)
          : 0,
      });
      await refreshMusicXmlFromEditedComposition();
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
      {disabledReason && aiEditStatus !== 'loading' && (
        <Status $tone="warn">{disabledReason}</Status>
      )}
      {aiEditStatus === 'error' && aiEditError && (
        <Status $tone="error">{aiEditError}</Status>
      )}
      {aiEditStatus === 'success' && (
        <Status $tone="info">AI region edit applied. Use Undo/Redo to compare before/after.</Status>
      )}
      {aiEditWarnings.map((warning, index) => (
        <Status key={`${index}:${warning}`} $tone="warn">{warning}</Status>
      ))}
    </Panel>
  );
};

export default AiRegionEditPanel;
