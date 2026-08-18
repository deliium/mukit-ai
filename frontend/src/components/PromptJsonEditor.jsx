import React from 'react';
import { JsonEditor } from 'json-edit-react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const EditorShell = styled.div`
  margin-top: 20px;
`;

const EditorHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;

  @media (max-width: 480px) {
    align-items: stretch;
    flex-direction: column;
  }
`;

const ResetButton = styled.button`
  background: #6b7280;
  color: white;
  border: none;
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;

  &:hover {
    background: #4b5563;
  }
`;

const EditorFrame = styled.div`
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px;
  background: #ffffff;
  overflow: auto;
`;

const ValidationMessage = styled.div`
  margin-top: 10px;
  color: ${(props) => (props.$valid ? '#065f46' : '#991b1b')};
  font-size: 0.9rem;
`;

const PromptJsonEditor = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const setEditedMusicJson = useMusicStore((state) => state.setEditedMusicJson);
  const resetEditedMusicJson = useMusicStore((state) => state.resetEditedMusicJson);
  const setUiError = useMusicStore((state) => state.setUiError);

  if (!editedMusicJson) {
    return null;
  }

  const validation = validateMusicJson(editedMusicJson);

  const handleSetData = (nextData) => {
    const nextValidation = validateMusicJson(nextData);
    console.debug('[PromptJsonEditor] JSON editor parse result', { valid: nextValidation.valid });
    setEditedMusicJson(nextData);
    if (!nextValidation.valid) {
      console.warn('[PromptJsonEditor] Invalid edited music JSON', { message: nextValidation.message });
      setUiError(nextValidation.message);
      return;
    }
    setUiError('');
  };

  return (
    <EditorShell>
      <EditorHeader>
        <div>
          <h4>Editable Music JSON</h4>
          <small>Edits are validated before notation or playback uses this JSON.</small>
        </div>
        <ResetButton type="button" onClick={resetEditedMusicJson}>Reset to generated JSON</ResetButton>
      </EditorHeader>
      <EditorFrame>
        <JsonEditor data={editedMusicJson} setData={handleSetData} rootName="music" />
      </EditorFrame>
      <ValidationMessage $valid={validation.valid}>{validation.message}</ValidationMessage>
    </EditorShell>
  );
};

function validateMusicJson(value) {
  if (!value || typeof value !== 'object') {
    return { valid: false, message: 'Music JSON must be an object.' };
  }
  if (!Number.isFinite(value.tempo) || value.tempo < 40 || value.tempo > 240) {
    return { valid: false, message: 'Tempo must be a number between 40 and 240.' };
  }
  if (!value.key || typeof value.key !== 'string') {
    return { valid: false, message: 'Key is required.' };
  }
  if (!value.time_signature || typeof value.time_signature !== 'string') {
    return { valid: false, message: 'Time signature is required.' };
  }
  if (!Array.isArray(value.sections) || value.sections.length === 0) {
    return { valid: false, message: 'At least one section is required.' };
  }
  if (!Array.isArray(value.tracks) || value.tracks.length === 0) {
    return { valid: false, message: 'At least one track is required.' };
  }
  if (!Array.isArray(value.harmony)) {
    return { valid: false, message: 'Harmony must be an array.' };
  }
  if (value.notes !== undefined && !Array.isArray(value.notes)) {
    return { valid: false, message: 'Notes must be an array.' };
  }
  return { valid: true, message: 'Edited JSON is valid for preview and playback.' };
}

export default PromptJsonEditor;
