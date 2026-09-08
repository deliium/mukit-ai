import React, { useId, useRef, useState } from 'react';
import styled from 'styled-components';
import { importMidi, importMusicXml } from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';

/** Default upload guidance; backend `IMPORT_MAX_UPLOAD_BYTES` is authoritative. */
export const IMPORT_MAX_UPLOAD_MIB = 5;

const MIDI_ACCEPT = '.mid,.midi,audio/midi,audio/x-midi';
const MUSICXML_ACCEPT = '.musicxml,.xml,.mxl,application/vnd.recordare.musicxml+xml,application/xml,text/xml';

const Shell = styled.section`
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: ${(props) => (props.$compact ? '0' : '12px 0')};
`;

const Title = styled.h4`
  margin: 0;
  font-size: 0.95rem;
  color: #334155;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.8rem;
  color: #6b7280;
`;

const ButtonRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`;

const Button = styled.button`
  padding: 8px 12px;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  background: ${(props) => (props.$secondary ? '#e5e7eb' : '#667eea')};
  color: ${(props) => (props.$secondary ? '#374151' : 'white')};

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
`;

const DropZone = styled.div`
  padding: 14px;
  border: 2px dashed ${(props) => (props.$active ? '#667eea' : '#cbd5e1')};
  border-radius: 10px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#f8fafc')};
  color: #475569;
  font-size: 0.9rem;
  outline: none;

  &:focus-visible {
    border-color: #4f46e5;
    box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.25);
  }
`;

const HiddenInput = styled.input`
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
`;

const Status = styled.div`
  font-size: 0.85rem;
  color: ${(props) => (props.$error ? '#991b1b' : '#334155')};
`;

const ReportBox = styled.div`
  padding: 10px 12px;
  border-radius: 8px;
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  font-size: 0.85rem;
  color: #334155;
`;

const WarningList = styled.ul`
  margin: 6px 0 0;
  padding-left: 18px;
  color: #92400e;
`;

const ConfirmBox = styled.div`
  padding: 12px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  color: #9a3412;
  font-size: 0.9rem;
`;

function inferImportFormat(file) {
  const name = String(file?.name || '').toLowerCase();
  if (name.endsWith('.mid') || name.endsWith('.midi')) {
    return 'midi';
  }
  if (name.endsWith('.musicxml') || name.endsWith('.xml') || name.endsWith('.mxl')) {
    return 'musicxml';
  }
  return null;
}

function formatBytesGuidance() {
  return `Up to about ${IMPORT_MAX_UPLOAD_MIB} MiB per file (server limit applies).`;
}

function summarizeImportReport(report) {
  if (!report || typeof report !== 'object') {
    return null;
  }
  const summary = report.summary || {};
  const issues = Array.isArray(report.issues) ? report.issues : [];
  const grouped = { defaulted: [], normalized: [], quantized: [], omitted: [] };
  issues.forEach((issue) => {
    const action = issue.action || 'normalized';
    if (!grouped[action]) {
      grouped[action] = [];
    }
    grouped[action].push(issue.code || issue.message || 'issue');
  });
  return {
    status: report.status || 'exact',
    format: summary.detected_format || null,
    sourceTracks: summary.source_track_count ?? null,
    resultTracks: summary.result_track_count ?? null,
    sourceNotes: summary.source_note_count ?? null,
    resultNotes: summary.result_note_count ?? null,
    bars: summary.bar_count ?? null,
    grouped,
  };
}

/**
 * Accessible MIDI / MusicXML import controls.
 * @param {{ mode?: 'create-project' | 'replace', compact?: boolean, title?: string }} props
 */
const ImportControls = ({
  mode = 'replace',
  compact = false,
  title = 'Import score',
} = {}) => {
  const midiInputId = useId();
  const musicXmlInputId = useId();
  const midiInputRef = useRef(null);
  const musicXmlInputRef = useRef(null);
  const [dragActive, setDragActive] = useState(false);
  const [localError, setLocalError] = useState('');
  const [pendingReplace, setPendingReplace] = useState(null);
  const [localStatus, setLocalStatus] = useState('');

  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const importStatus = useMusicStore((state) => state.importStatus);
  const importError = useMusicStore((state) => state.importError);
  const importReport = useMusicStore((state) => state.importReport);
  const notationReport = useMusicStore((state) => state.notationReport);
  const startImport = useMusicStore((state) => state.startImport);
  const completeImport = useMusicStore((state) => state.completeImport);
  const failImport = useMusicStore((state) => state.failImport);
  const createImportedProject = useMusicStore((state) => state.createImportedProject);

  const busy = importStatus === 'loading';
  const summary = summarizeImportReport(importReport);

  const resetFileInput = (input) => {
    if (input) {
      input.value = '';
    }
  };

  const hasComposition = Boolean(
    editedMusicJson
    && Array.isArray(editedMusicJson.tracks)
    && editedMusicJson.tracks.some((track) => Array.isArray(track.events) && track.events.length > 0),
  );

  const runImport = async (file, format) => {
    if (!file || busy) {
      return;
    }
    setLocalError('');
    setLocalStatus('');
    console.debug('[ImportControls] Import selected', {
      mode,
      format,
      byteCount: typeof file.size === 'number' ? file.size : null,
    });

    if (mode === 'create-project') {
      try {
        await createImportedProject(file, { format });
        setLocalStatus('Import finished — project created.');
      } catch (error) {
        setLocalError(error.message || 'Import failed');
      }
      return;
    }

    if (!startImport({ format })) {
      return;
    }
    try {
      const result = format === 'musicxml'
        ? await importMusicXml(file)
        : await importMidi(file);
      const ok = completeImport(result);
      if (!ok) {
        setLocalError(useMusicStore.getState().importError || 'Import validation failed');
        return;
      }
      setLocalStatus('Import finished — composition replaced.');
      console.info('[ImportControls] Replace import completed', {
        format,
        importStatus: result.import_report?.status || null,
      });
    } catch (error) {
      failImport(error.message || 'Import failed', { code: error.code || null });
      setLocalError(error.message || 'Import failed');
    }
  };

  const requestImport = (file, format) => {
    if (!file) {
      return;
    }
    if (mode === 'replace' && hasComposition) {
      setPendingReplace({ file, format });
      return;
    }
    runImport(file, format);
  };

  const onFilesChosen = (fileList, preferredFormat) => {
    const files = Array.from(fileList || []);
    if (files.length === 0) {
      return;
    }
    if (files.length > 1) {
      console.warn('[ImportControls] Rejected multi-file selection', { count: files.length });
      setLocalError('Import one MIDI or MusicXML file at a time.');
      return;
    }
    const file = files[0];
    const format = preferredFormat || inferImportFormat(file);
    if (!format) {
      console.warn('[ImportControls] Could not infer import format from selection');
      setLocalError('Choose a .mid/.midi or .musicxml/.xml/.mxl file.');
      return;
    }
    requestImport(file, format);
  };

  const onDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    const types = Array.from(event.dataTransfer?.types || []);
    if (!types.includes('Files')) {
      return;
    }
    onFilesChosen(event.dataTransfer.files, null);
  };

  const onDragOver = (event) => {
    const types = Array.from(event.dataTransfer?.types || []);
    if (!types.includes('Files')) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setDragActive(true);
  };

  return (
    <Shell $compact={compact} data-testid="import-controls">
      {!compact && <Title>{title}</Title>}
      <Hint>
        Import MIDI or MusicXML into canonical composition JSON. No LLM required.
        {' '}
        {formatBytesGuidance()}
      </Hint>

      <ButtonRow>
        <Button
          type="button"
          data-testid="import-midi-button"
          disabled={busy}
          onClick={() => {
            resetFileInput(midiInputRef.current);
            midiInputRef.current?.click();
          }}
        >
          {busy ? 'Importing…' : 'Import MIDI'}
        </Button>
        <Button
          type="button"
          $secondary
          data-testid="import-musicxml-button"
          disabled={busy}
          onClick={() => {
            resetFileInput(musicXmlInputRef.current);
            musicXmlInputRef.current?.click();
          }}
        >
          {busy ? 'Importing…' : 'Import MusicXML'}
        </Button>
      </ButtonRow>

      <HiddenInput
        id={midiInputId}
        ref={midiInputRef}
        type="file"
        accept={MIDI_ACCEPT}
        data-testid="import-midi-input"
        aria-label="Choose MIDI file to import"
        onChange={(event) => {
          onFilesChosen(event.target.files, 'midi');
          resetFileInput(event.target);
        }}
      />
      <HiddenInput
        id={musicXmlInputId}
        ref={musicXmlInputRef}
        type="file"
        accept={MUSICXML_ACCEPT}
        data-testid="import-musicxml-input"
        aria-label="Choose MusicXML or MXL file to import"
        onChange={(event) => {
          onFilesChosen(event.target.files, 'musicxml');
          resetFileInput(event.target);
        }}
      />

      <DropZone
        role="button"
        tabIndex={0}
        $active={dragActive}
        data-testid="import-drop-zone"
        aria-label="Drop a MIDI or MusicXML file to import"
        onDragEnter={onDragOver}
        onDragOver={onDragOver}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            midiInputRef.current?.click();
          }
        }}
      >
        Drop a .mid / .midi or .musicxml / .xml / .mxl file here
      </DropZone>

      {pendingReplace && (
        <ConfirmBox data-testid="import-replace-confirm">
          Replace the current composition with this import? A failed conversion leaves the current music intact.
          <ButtonRow style={{ marginTop: 10 }}>
            <Button
              type="button"
              disabled={busy}
              onClick={() => {
                console.info('[ImportControls] User confirmed replace import', {
                  format: pendingReplace.format,
                });
                const next = pendingReplace;
                setPendingReplace(null);
                runImport(next.file, next.format);
              }}
            >
              Replace composition
            </Button>
            <Button
              type="button"
              $secondary
              onClick={() => {
                console.debug('[ImportControls] Replace import cancelled');
                setPendingReplace(null);
              }}
            >
              Cancel
            </Button>
          </ButtonRow>
        </ConfirmBox>
      )}

      {(localError || importError) && (
        <Status $error data-testid="import-error">
          {localError || importError}
        </Status>
      )}
      {localStatus && !localError && <Status data-testid="import-status">{localStatus}</Status>}

      {importStatus === 'success' && summary && (
        <ReportBox data-testid="import-report-summary" aria-live="polite">
          <div>
            Import
            {summary.format ? ` (${summary.format})` : ''}
            {': '}
            {summary.status}
            {' · '}
            tracks {summary.sourceTracks ?? '—'}→{summary.resultTracks ?? '—'}
            {' · '}
            notes {summary.sourceNotes ?? '—'}→{summary.resultNotes ?? '—'}
            {' · '}
            bars {summary.bars ?? '—'}
          </div>
          {Object.entries(summary.grouped).map(([action, codes]) => (
            codes.length > 0 ? (
              <WarningList key={action}>
                <li>
                  <strong>{action}</strong>
                  {': '}
                  {[...new Set(codes)].join(', ')}
                </li>
              </WarningList>
            ) : null
          ))}
          {notationReport?.status && notationReport.status !== 'exact' && (
            <Hint style={{ marginTop: 8 }}>
              Notation projection:
              {' '}
              {notationReport.status}
              {Array.isArray(notationReport.issue_codes) && notationReport.issue_codes.length > 0
                ? ` (${notationReport.issue_codes.join(', ')})`
                : ''}
            </Hint>
          )}
        </ReportBox>
      )}
    </Shell>
  );
};

export default ImportControls;
