import React, { useCallback, useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import { exportMidi, exportMusicXml, exportWav } from '../api/musicApi.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { useMusicStore } from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import {
  blobToExportFile,
  createExportDragStartHandler,
  resolveExportDragMime,
} from '../utils/exportDrag.js';

const log = createAppLogger('exportControls');

const Controls = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 16px 0;
  align-items: stretch;
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

const DragChip = styled.button`
  background: ${(props) => (props.$ready ? '#ecfdf5' : '#f3f4f6')};
  color: ${(props) => (props.$ready ? '#065f46' : '#6b7280')};
  border: 1px dashed ${(props) => (props.$ready ? '#34d399' : '#d1d5db')};
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: ${(props) => (props.$ready ? 'grab' : 'pointer')};

  &:active:not(:disabled) {
    cursor: grabbing;
  }

  &:disabled {
    opacity: 0.6;
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
  const [midiDragFile, setMidiDragFile] = useState(null);
  const [musicXmlDragFile, setMusicXmlDragFile] = useState(null);
  const [dragPrep, setDragPrep] = useState(null);
  const midiFileRef = useRef(null);
  const musicXmlFileRef = useRef(null);

  useEffect(() => {
    midiFileRef.current = midiDragFile;
  }, [midiDragFile]);

  useEffect(() => {
    musicXmlFileRef.current = musicXmlDragFile;
  }, [musicXmlDragFile]);

  // Invalidate drag caches when the working composition changes.
  useEffect(() => {
    setMidiDragFile(null);
    setMusicXmlDragFile(null);
  }, [editedMusicJson]);

  const validation = editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false };
  const canExport = Boolean(
    editedMusicJson
      && validation.valid
      && isCanonicalComposition(editedMusicJson)
      && exportStatus !== 'loading'
      && dragPrep == null,
  );

  const handleMidiDragStart = useCallback(
    createExportDragStartHandler(() => midiFileRef.current, { format: 'midi' }),
    [],
  );
  const handleMusicXmlDragStart = useCallback(
    createExportDragStartHandler(() => musicXmlFileRef.current, { format: 'musicxml' }),
    [],
  );

  const prepareDrag = async (format) => {
    if (!canExport) {
      setStatusMessage(validation.message || 'Canonical composition JSON is required for export');
      return;
    }
    setDragPrep(format);
    setStatusMessage(`Preparing ${format === 'midi' ? 'MIDI' : 'MusicXML'} for drag…`);
    setUiError('');
    try {
      if (format === 'midi') {
        const result = await exportMidi(editedMusicJson, { download: false });
        const mime = resolveExportDragMime('midi', result.contentType);
        const file = blobToExportFile(result.blob, result.filename, mime);
        setMidiDragFile(file);
        log.info('MIDI drag payload ready', { filename: file.name, byteSize: file.size });
        setStatusMessage(`Ready to drag ${result.filename} into Ableton / Reaper / any DAW`);
      } else {
        const result = await exportMusicXml(editedMusicJson, { download: false });
        const mime = resolveExportDragMime('musicxml', result.contentType);
        const file = blobToExportFile(result.blob, result.filename, mime);
        setMusicXmlDragFile(file);
        setMusicXml(await result.blob.text());
        log.info('MusicXML drag payload ready', { filename: file.name, byteSize: file.size });
        setStatusMessage(`Ready to drag ${result.filename} (notation handoff)`);
      }
    } catch (error) {
      const message = error.message || `Failed to prepare ${format} for drag`;
      log.error('Drag prep failed', { format, message });
      setStatusMessage(message);
      setUiError(message);
    } finally {
      setDragPrep(null);
    }
  };

  const runExport = async (format) => {
    if (exportStatus === 'loading') {
      log.warn('Duplicate export blocked', { format });
      return;
    }
    if (!canExport) {
      log.warn('Export blocked', {
        format,
        hasJson: Boolean(editedMusicJson),
        valid: validation.valid,
        exportStatus,
      });
      setStatusMessage(validation.message || 'Canonical composition JSON is required for export');
      return;
    }

    log.debug('Export clicked', {
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
        const mime = resolveExportDragMime('musicxml', result.contentType);
        setMusicXmlDragFile(blobToExportFile(result.blob, result.filename, mime));
        log.debug('MusicXML store updated from export', {
          musicXmlLength: musicxmlText.length,
          filename: result.filename,
          projectionStatus: result.projection?.status,
        });
        const warningSuffix = result.warnings?.length
          ? ` (${result.warnings.join('; ')})`
          : '';
        setStatusMessage(`Downloaded ${result.filename} and refreshed notation preview${warningSuffix}`);
      } else if (format === 'wav') {
        const result = await exportWav(editedMusicJson);
        log.debug('WAV export state transition', {
          format: 'wav',
          filename: result.filename,
          blobSize: result.blob?.size,
        });
        const warningSuffix = result.warnings?.length
          ? ` (${result.warnings.join('; ')})`
          : '';
        setStatusMessage(`Downloaded ${result.filename} (deterministic FluidSynth WAV)${warningSuffix}`);
      } else {
        const result = await exportMidi(editedMusicJson);
        const mime = resolveExportDragMime('midi', result.contentType);
        setMidiDragFile(blobToExportFile(result.blob, result.filename, mime));
        const warningSuffix = result.warnings?.length
          ? ` (${result.warnings.join('; ')})`
          : '';
        setStatusMessage(`Downloaded ${result.filename}${warningSuffix}`);
      }
      setExportStatus('success');
    } catch (error) {
      const message = error.message || `Failed to export ${format}`;
      log.error('Export failed', { format, message });
      setExportStatus('error');
      setStatusMessage(message);
      setUiError(message);
    }
  };

  return (
    <Controls>
      <ExportButton
        type="button"
        data-testid="export-musicxml"
        disabled={!canExport}
        onClick={() => runExport('musicxml')}
      >
        {exportStatus === 'loading' ? 'Exporting...' : 'Export MusicXML'}
      </ExportButton>
      <ExportButton
        type="button"
        data-testid="export-midi"
        disabled={!canExport}
        onClick={() => runExport('midi')}
      >
        {exportStatus === 'loading' ? 'Exporting...' : 'Export MIDI'}
      </ExportButton>
      <DragChip
        type="button"
        data-testid="drag-midi"
        $ready={Boolean(midiDragFile)}
        disabled={!canExport && !midiDragFile}
        draggable={Boolean(midiDragFile)}
        onClick={() => {
          if (!midiDragFile) {
            void prepareDrag('midi');
          }
        }}
        onDragStart={handleMidiDragStart}
        title={
          midiDragFile
            ? `Drag ${midiDragFile.name} into a DAW`
            : 'Prepare Standard MIDI, then drag into Ableton / Reaper'
        }
      >
        {dragPrep === 'midi'
          ? 'Preparing MIDI…'
          : midiDragFile
            ? `Drag MIDI (${midiDragFile.name})`
            : 'Prepare MIDI drag'}
      </DragChip>
      <DragChip
        type="button"
        data-testid="drag-musicxml"
        $ready={Boolean(musicXmlDragFile)}
        disabled={!canExport && !musicXmlDragFile}
        draggable={Boolean(musicXmlDragFile)}
        onClick={() => {
          if (!musicXmlDragFile) {
            void prepareDrag('musicxml');
          }
        }}
        onDragStart={handleMusicXmlDragStart}
        title={
          musicXmlDragFile
            ? `Drag ${musicXmlDragFile.name}`
            : 'Prepare MusicXML, then drag for notation handoff'
        }
      >
        {dragPrep === 'musicxml'
          ? 'Preparing MusicXML…'
          : musicXmlDragFile
            ? `Drag MusicXML (${musicXmlDragFile.name})`
            : 'Prepare MusicXML drag'}
      </DragChip>
      <ExportButton
        type="button"
        data-testid="export-wav"
        disabled={!canExport}
        onClick={() => runExport('wav')}
      >
        {exportStatus === 'loading' ? 'Exporting...' : 'Export WAV (deterministic)'}
      </ExportButton>
      <Hint>
        For Ableton / Reaper / any DAW — download or drag Standard MIDI (SMF Type 1). MusicXML is
        the notation handoff. Deterministic WAV uses FluidSynth (note-faithful). Neural AI renders
        are a separate action and are not note-perfect.
      </Hint>
      {statusMessage && <Status $error={exportStatus === 'error'}>{statusMessage}</Status>}
    </Controls>
  );
};

export default ExportControls;
