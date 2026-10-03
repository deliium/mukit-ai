import React, { useEffect, useState } from 'react';
import styled from 'styled-components';

import {
  ContentProvenanceApiError,
  downloadContentProvenanceManifest,
  getContentProvenanceManifest,
} from '../api/contentProvenanceApi.js';
import {
  honestyHeadline,
  summarizeChainRecords,
} from '../utils/contentProvenanceChain.js';

const Box = styled.div`
  margin-top: 8px;
  padding: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  font-size: 0.8rem;
  background: #fafafa;
`;

const List = styled.ol`
  margin: 6px 0 0;
  padding-left: 1.2rem;
`;

const Err = styled.div`
  color: #991b1b;
  margin-top: 4px;
`;

const Button = styled.button`
  margin-top: 6px;
  font-size: 0.8rem;
`;

/**
 * Fetch chain only for the selected leaf. Opening a parent panel must not
 * fan-out or POST credentials.
 */
export default function ContentProvenanceInspect({
  projectId,
  artifactKind,
  artifactId,
  testIdPrefix = 'provenance',
}) {
  const [manifest, setManifest] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!projectId || !artifactKind || !artifactId) {
      setManifest(null);
      setError('');
      return undefined;
    }
    let cancelled = false;
    setBusy(true);
    setError('');
    getContentProvenanceManifest(projectId, artifactKind, artifactId)
      .then((body) => {
        if (!cancelled) setManifest(body);
      })
      .catch((err) => {
        if (cancelled) return;
        const code = err instanceof ContentProvenanceApiError ? err.code : 'provenance_invalid';
        setError(code);
        setManifest(null);
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, artifactKind, artifactId]);

  if (!projectId || !artifactKind || !artifactId) {
    return null;
  }

  const rows = summarizeChainRecords(manifest?.records);
  const headline = honestyHeadline(manifest?.honesty || { cryptographic: false });

  async function handleDownload() {
    setError('');
    try {
      await downloadContentProvenanceManifest(projectId, artifactKind, artifactId);
    } catch (err) {
      const code = err instanceof ContentProvenanceApiError ? err.code : 'provenance_invalid';
      setError(code);
    }
  }

  return (
    <Box data-testid={`${testIdPrefix}-inspect`}>
      <div data-testid={`${testIdPrefix}-honesty`}>{headline}</div>
      {busy ? <div>Loading provenance…</div> : null}
      {error ? <Err data-testid={`${testIdPrefix}-error`}>{error}</Err> : null}
      {rows.length ? (
        <List data-testid={`${testIdPrefix}-chain`}>
          {rows.map((row) => (
            <li key={row.recordId || `${row.operation}-${row.artifactId}`}>
              {row.operation}
              {' · '}
              {row.artifactKind}
              {' '}
              {String(row.artifactId || '').slice(0, 12)}
              {' · '}
              {row.trustLabel}
            </li>
          ))}
        </List>
      ) : null}
      <Button
        type="button"
        data-testid={`${testIdPrefix}-download`}
        disabled={busy || !manifest}
        onClick={() => {
          void handleDownload();
        }}
      >
        Download provenance
      </Button>
    </Box>
  );
}
