import React, { useEffect, useRef, useState } from 'react';
import { OpenSheetMusicDisplay } from 'opensheetmusicdisplay';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const ViewerShell = styled.div`
  margin-top: 20px;
`;

const ViewerFrame = styled.div`
  min-height: 220px;
  overflow: auto;
  padding: 12px;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
`;

const EmptyState = styled.div`
  padding: 18px;
  color: #6b7280;
  background: #f9fafb;
  border-radius: 8px;
`;

const ErrorState = styled.div`
  padding: 12px;
  color: #991b1b;
  background: #fee2e2;
  border: 1px solid #fca5a5;
  border-radius: 8px;
`;

const NotationViewer = () => {
  const containerRef = useRef(null);
  const musicXml = useMusicStore((state) => state.musicXml);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!containerRef.current || !musicXml) {
      return;
    }

    let cancelled = false;

    async function renderNotation() {
      console.debug('[NotationViewer] OSMD render started', { musicXmlLength: musicXml.length });
      setError('');
      containerRef.current.innerHTML = '';

      try {
        const osmd = new OpenSheetMusicDisplay(containerRef.current, {
          autoResize: true,
          drawTitle: true,
        });
        await osmd.load(musicXml);
        if (cancelled) {
          return;
        }
        osmd.render();
        console.debug('[NotationViewer] OSMD render completed');
      } catch (renderError) {
        console.error('[NotationViewer] OSMD render failed', { message: renderError.message });
        if (!cancelled) {
          setError('Notation could not be rendered from the generated MusicXML.');
        }
      }
    }

    renderNotation();

    return () => {
      cancelled = true;
    };
  }, [musicXml]);

  return (
    <ViewerShell>
      <h4>Notation Preview</h4>
      {!musicXml && <EmptyState>Generate LLM music JSON to render notation here.</EmptyState>}
      {error && <ErrorState>{error}</ErrorState>}
      <ViewerFrame ref={containerRef} aria-label="Music notation preview" />
    </ViewerShell>
  );
};

export default NotationViewer;
