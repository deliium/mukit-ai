import { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';

import {
  applyArdourExchangePreview,
  ardourExchangeDownloadUrl,
  discardArdourExchangePreview,
  getArdourExchangeContext,
  getArdourExchangeStatus,
  ingestArdourExchangePackageId,
  ingestArdourExchangeZip,
  listArdourExchangePackages,
  prepareArdourExchange,
  realizeArdourExchange,
} from '../api/ardourExchangeApi.js';
import useMusicStore from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import { applyExchangeInbound } from '../utils/ardourExchange/applyInbound.js';

const logger = createAppLogger('ardourExchange');

const Wrap = styled.section`
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid #e2e8f0;
`;

const Title = styled.h3`
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
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
  background: #fff;
  color: #0f172a;
  cursor: ${(p) => (p.disabled ? 'not-allowed' : 'pointer')};
  opacity: ${(p) => (p.disabled ? 0.5 : 1)};
`;

const Muted = styled.p`
  margin: 0;
  color: #64748b;
  font-size: 0.9rem;
`;

const Banner = styled.p`
  margin: 0;
  padding: 8px 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  font-size: 0.9rem;
`;

const Select = styled.select`
  font: inherit;
  padding: 4px 6px;
`;

const INTENTS = [
  { value: 'counter_melody', label: 'Counter-melody (cello)' },
  { value: 'arrangement_variation', label: 'Arrangement variation' },
  { value: 'regenerate_region', label: 'Regenerate region' },
];

const ArdourExchangePanel = () => {
  const fileRef = useRef(null);
  const [status, setStatus] = useState(null);
  const [context, setContext] = useState(null);
  const [packages, setPackages] = useState([]);
  const [preview, setPreview] = useState(null);
  const [intent, setIntent] = useState('counter_melody');
  const [realizeResult, setRealizeResult] = useState(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState(null);
  const [prepareResult, setPrepareResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const completeImport = useMusicStore((state) => state.completeImport);
  const applySelectedArrangementCandidate = useMusicStore(
    (state) => state.applySelectedArrangementCandidate,
  );
  const applySelectedDevelopmentCandidate = useMusicStore(
    (state) => state.applySelectedDevelopmentCandidate,
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const nextStatus = await getArdourExchangeStatus();
        if (cancelled) return;
        setStatus(nextStatus);
        const nextContext = await getArdourExchangeContext();
        if (!cancelled) setContext(nextContext);
      } catch (err) {
        if (!cancelled) setError(err.message || 'Exchange status failed');
      }
    })();
    // Opening the tab never auto-ingests / applies / prepares.
    return () => {
      cancelled = true;
    };
  }, []);

  const enabled = Boolean(status?.enabled && status?.root_configured);

  async function run(label, fn) {
    setBusy(true);
    setError('');
    try {
      logger.debug(label, { enabled });
      return await fn();
    } catch (err) {
      setError(err.message || `${label} failed`);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleScanRoot() {
    const rows = await run('list', () => listArdourExchangePackages());
    if (rows) setPackages(rows);
  }

  async function handleIngestId(packageId) {
    const next = await run('ingest', () => ingestArdourExchangePackageId(packageId));
    if (next) {
      setPreview(next);
      setRealizeResult(null);
      setPrepareResult(null);
    }
  }

  async function handleIngestFile(file) {
    if (!file) return;
    const next = await run('ingest_zip', () => ingestArdourExchangeZip(file));
    if (next) {
      setPreview(next);
      setRealizeResult(null);
      setPrepareResult(null);
    }
  }

  async function handleApplyInbound() {
    const payload = await run('apply', () => applyArdourExchangePreview());
    if (!payload?.composition) return;
    const ok = await applyExchangeInbound(completeImport, payload);
    if (!ok) {
      setError('Apply replace failed validation');
      return;
    }
    logger.info('applied inbound via completeImport', {
      package_id: payload.manifest?.package_id,
    });
  }

  async function handleDiscard() {
    await run('discard', () => discardArdourExchangePreview());
    setPreview(null);
    setRealizeResult(null);
    setPrepareResult(null);
  }

  async function handleRealize() {
    const result = await run('realize', () => realizeArdourExchange(intent));
    if (!result) return;
    setRealizeResult(result);
    const candidates = result.preview?.candidates || [];
    setSelectedCandidateId(candidates[0]?.candidate_id || null);

    const compositionRevision = useMusicStore.getState().compositionRevision;
    if (result.surface === 'arrangement') {
      useMusicStore.setState({
        arrangementStatus: 'ready',
        arrangementError: '',
        arrangementCandidates: candidates,
        arrangementSelectedCandidateId: candidates[0]?.candidate_id || null,
        arrangementBaseRevision: compositionRevision,
        arrangementEditSourceFingerprint: result.preview?.edit_source_fingerprint || null,
        arrangementResponseCatalogFingerprint: result.preview?.catalog_fingerprint || null,
      });
    } else if (result.surface === 'development') {
      useMusicStore.setState({
        developmentStatus: 'ready',
        developmentError: '',
        developmentCandidates: candidates,
        developmentSelectedCandidateId: candidates[0]?.candidate_id || null,
        developmentBaseRevision: compositionRevision,
        developmentEditSourceFingerprint: result.preview?.edit_source_fingerprint || null,
      });
    }
  }

  async function handleApplyRealize() {
    if (!realizeResult || !selectedCandidateId) return;
    if (realizeResult.surface === 'arrangement') {
      useMusicStore.setState({ arrangementSelectedCandidateId: selectedCandidateId });
      const ok = await applySelectedArrangementCandidate();
      if (!ok) setError('Arrangement Apply failed');
      return;
    }
    useMusicStore.setState({ developmentSelectedCandidateId: selectedCandidateId });
    const ok = await applySelectedDevelopmentCandidate();
    if (!ok) setError('Development Apply failed');
  }

  async function handlePrepare() {
    const result = await run('prepare', () => prepareArdourExchange({ use_preview_alignment: true }));
    if (result) setPrepareResult(result);
  }

  const alignment = preview?.alignment;
  const candidates = realizeResult?.preview?.candidates || [];

  return (
    <Wrap data-testid="ardour-exchange-panel">
      <Title>Exchange</Title>
      <Muted>
        Move a selected MIDI region between Ardour and AI Composer. Opening this tab never
        auto-ingests or applies. Operator-installed Lua recipes write packages;
        Mukit never edits Ardour session XML.
      </Muted>
      <Banner>
        enabled={String(Boolean(status?.enabled))} root={String(Boolean(status?.root_configured))}
        {' '}companion={String(Boolean(status?.companion_connected))}
        {context ? ` · samples=${context.locate_samples ?? '—'} strip=${context.selected_strip_name || context.selected_ssid || '—'} sr=${context.sample_rate ?? '—'}` : ''}
      </Banner>
      {error ? <Banner role="alert">{error}</Banner> : null}

      <Row>
        <Button type="button" disabled={!enabled || busy} onClick={() => void handleScanRoot()}>
          Scan exchange root
        </Button>
        <Button
          type="button"
          disabled={!enabled || busy}
          onClick={() => fileRef.current?.click()}
        >
          Upload package zip
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".zip,application/zip"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            void handleIngestFile(file);
          }}
        />
      </Row>

      {packages.length > 0 ? (
        <Row>
          {packages.map((pkg) => (
            <Button
              key={pkg.package_id}
              type="button"
              disabled={!enabled || busy}
              onClick={() => void handleIngestId(pkg.package_id)}
            >
              Ingest {pkg.package_id.slice(0, 12)}… ({pkg.bar_count} bars)
            </Button>
          ))}
        </Row>
      ) : null}

      {preview ? (
        <Banner>
          Preview: {preview.manifest?.track_name} · {alignment?.tempo_bpm} bpm · {alignment?.time_signature}
          {' '}· bars {alignment?.start_bar}–{(alignment?.start_bar || 1) + (alignment?.bar_count || 1) - 1}
          {' '}· notes {preview.import_report?.note_count ?? 0}
        </Banner>
      ) : null}

      <Row>
        <Button type="button" disabled={!preview || busy} onClick={() => void handleApplyInbound()}>
          Apply (replace score)
        </Button>
        <Button type="button" disabled={!preview || busy} onClick={() => void handleDiscard()}>
          Discard preview
        </Button>
      </Row>

      <Row>
        <Select
          value={intent}
          disabled={!enabled || busy}
          onChange={(e) => setIntent(e.target.value)}
        >
          {INTENTS.map((item) => (
            <option key={item.value} value={item.value}>{item.label}</option>
          ))}
        </Select>
        <Button type="button" disabled={!preview || busy} onClick={() => void handleRealize()}>
          Realize
        </Button>
        <Button
          type="button"
          disabled={!realizeResult || !selectedCandidateId || busy}
          onClick={() => void handleApplyRealize()}
        >
          Apply realize candidate
        </Button>
      </Row>

      {candidates.length > 0 ? (
        <Row>
          {candidates.map((candidate) => (
            <label key={candidate.candidate_id}>
              <input
                type="radio"
                name="exchange-candidate"
                checked={selectedCandidateId === candidate.candidate_id}
                onChange={() => setSelectedCandidateId(candidate.candidate_id)}
              />
              {' '}
              {candidate.candidate_id.slice(0, 12)}…
            </label>
          ))}
        </Row>
      ) : null}

      <Row>
        <Button type="button" disabled={!preview || busy} onClick={() => void handlePrepare()}>
          Prepare outbound package
        </Button>
        {prepareResult ? (
          <a href={ardourExchangeDownloadUrl(prepareResult.package_id)}>
            Download {prepareResult.package_id.slice(0, 12)}…
          </a>
        ) : null}
      </Row>
      <Muted>
        Lua install docs: backend/examples/ardour/README.md · docs/ardour-session-exchange.md
      </Muted>
    </Wrap>
  );
};

export default ArdourExchangePanel;
