import React, { useState } from 'react';
import styled from 'styled-components';
import { exportMidi, exportMusicXml, exportWav } from '../api/musicApi.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { useMusicStore } from '../store/musicStore.js';

const Controls = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 16px 0;
`;

const ExportButton = styled.button`
  background: #4f46e5;
  color: white;
  border: none;
  padding: 10px 16px;
  border-radius: 8px;
  font-size: 0.95rem;
  font-weight: 500;
  cursor: pointer;

  &:hover:not(:disabled) {
    background: #4338ca;
  }

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }
`;

const Status = styled.div`
  width: 100%;
  font-size: 0.9rem;
  color: ${(props) => (props.$error ? '#991b1b' : '#065f46')};
`;

const Hint = styled.p`
  width: 100%;
  margin: 0;
  font-size: 0.8rem;
  color: #6b7280;
`;

const ExportControls = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const setMusicXml = useMusicStore((state) => state.setMusicXml);
  const setUiError = useMusicStore((state) => state.setUiError);
  const [exportStatus, setExportStatus] = useState('idle');
  const [statusMessage, setStatusMessage] = useState('');

  const validation = editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false };
  const canExport = Boolean(
    editedMusicJson
      && validation.valid
      && isCanonicalComposition(editedMusicJson)
      && exportStatus !== 'loading',
  );

  const runExport = async (format) => {
    if (exportStatus === 'loading') {
      console.warn('[ExportControls] Duplicate export blocked', { format });
      return;
    }
    if (!canExport) {
      console.warn('[ExportControls] Export blocked', {
        format,
        hasJson: Boolean(editedMusicJson),
        valid: validation.valid,
        exportStatus,
      });
      setStatusMessage(validation.message || 'Canonical composition.v1 JSON is required for export');
      return;
    }

    console.debug('[ExportControls] Export clicked', {
      format,
      schemaVersion: editedMusicJson.schema_version,
      trackCount: editedMusicJson.tracks?.length || 0,
    });
    setExportStatus('loading');
    setStatusMessage(`Exporting ${format}…`);
    setUiError('');

    try {
      if (format === 'musicxml') {
        const result = await exportMusicXml(editedMusicJson);
        const musicxmlText = await result.blob.text();
        setMusicXml(musicxmlText);
        console.debug('[ExportControls] MusicXML store updated from export', {
          musicXmlLength: musicxmlText.length,
          filename: result.filename,
        });
        setStatusMessage(`Downloaded ${result.filename} and refreshed notation preview`);
      } else if (format === 'wav') {
        const result = await exportWav(editedMusicJson);
        console.debug('[ExportControls] WAV export state transition', {
          format: 'wav',
          filename: result.filename,
          blobSize: result.blob?.size,
        });
        setStatusMessage(`Downloaded ${result.filename} (server-rendered WAV export)`);
      } else {
        const result = await exportMidi(editedMusicJson);
        setStatusMessage(`Downloaded ${result.filename}`);
      }
      setExportStatus('success');
    } catch (error) {
      const message = error.message || `Failed to export ${format}`;
      console.error('[ExportControls] Export failed', { format, message });
      setExportStatus('error');
      setStatusMessage(message);
      setUiError(message);
    }
  };

  return (
    <Controls>
      <ExportButton
        type="button"
        disabled={!canExport}
        onClick={() => runExport('musicxml')}
      >
        {exportStatus === 'loading' ? 'Exporting...' : 'Export MusicXML'}
      </ExportButton>
      <ExportButton
        type="button"
        disabled={!canExport}
        onClick={() => runExport('midi')}
      >
        {exportStatus === 'loading' ? 'Exporting...' : 'Export MIDI'}
      </ExportButton>
      <ExportButton
        type="button"
        disabled={!canExport}
        onClick={() => runExport('wav')}
      >
        {exportStatus === 'loading' ? 'Exporting...' : 'Export WAV'}
      </ExportButton>
      <Hint>WAV is a server-side render/export, not browser preview playback.</Hint>
      {statusMessage && <Status $error={exportStatus === 'error'}>{statusMessage}</Status>}
    </Controls>
  );
};

export default ExportControls;
