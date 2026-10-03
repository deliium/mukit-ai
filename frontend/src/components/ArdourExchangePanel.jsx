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
import { applyRealizeCandidate } from '../utils/ardourExchange/applyRealize.js';
import { buildArdourScopeSummary } from '../utils/ardourExchange/scopeSummary.js';
import {
  runArdourStemWorkflow,
  STEM_WORKFLOW_HONESTY,
} from '../utils/ardourExchange/stemWorkflow.js';
import {
  canApplyRealizeCandidate,
  canPrepareOutbound,
  canRealizeExchange,
  canSendToAiComposer,
} from '../utils/ardourExchange/workflowGating.js';

const logger = createAppLogger('ardourExchange');

const Wrap = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
`;

const Step = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 0;
  border-top: 1px solid #e2e8f0;
`;

const StepTitle = styled.h3`
  margin: 0;
  font-size: 0.95rem;
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
  { value: 'add_accompaniment', label: 'Generate accompaniment' },
  { value: 'counter_melody', label: 'Generate counterpoint' },
  { value: 'regenerate_region', label: 'Create variation' },
  { value: 'reharmonize_selection', label: 'Reharmonize selection' },
  { value: 'orchestrate_selection', label: 'Orchestrate selection' },
  { value: 'arrangement_variation', label: 'Arrangement variation (legacy)' },
];

/**
 * Exchange + AI op steps of the Ardour workflow surface.
 * Opening never auto-ingests or applies.
 */
const ArdourExchangePanel = ({ companionStatus = null }) => {
  const fileRef = useRef(null);
  const [status, setStatus] = useState(null);
  const [context, setContext] = useState(null);
  const [packages, setPackages] = useState([]);
  const [preview, setPreview] = useState(null);
  const [intent, setIntent] = useState('add_accompaniment');
  const [realizeResult, setRealizeResult] = useState(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState(null);
  const [prepareResult, setPrepareResult] = useState(null);
  const [stemMessage, setStemMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const completeImport = useMusicStore((state) => state.completeImport);
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);

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
  const sendEnabled = canSendToAiComposer({
    exchangeEnabled: Boolean(status?.enabled),
    exchangeRootConfigured: Boolean(status?.root_configured),
    busy,
  });
  const realizeEnabled = canRealizeExchange({
    exchangeEnabled: enabled,
    hasPreview: Boolean(preview),
    busy,
  });
  const applyRealizeEnabled = canApplyRealizeCandidate({
    hasRealizeResult: Boolean(realizeResult),
    selectedCandidateId,
    busy,
  });
  const prepareEnabled = canPrepareOutbound({ hasPreview: Boolean(preview), busy });

  const scope = buildArdourScopeSummary({
    companionStatus: companionStatus || {
      connection_state: context?.connection_state,
      locate_samples: context?.locate_samples,
      sample_rate: context?.sample_rate,
      selected_ssid: context?.selected_ssid,
      selected_strip_name: context?.selected_strip_name,
      transport_playing: context?.transport_playing,
    },
    exchangePreview: preview,
  });

  async function run(label, fn) {
    setBusy(true);
    setError('');
    try {
      logger.debug(label, { enabled });
      return await fn();
    } catch (err) {
      setError(err.message || `${label} failed`);
      logger.error('workflow step failed', { step: label, code: err?.code || null });
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function handleScanRoot() {
    logger.info('workflow step', { step: 'send_scan' });
    const rows = await run('list', () => listArdourExchangePackages());
    if (rows) setPackages(rows);
  }

  async function handleIngestId(packageId) {
    logger.info('workflow step', { step: 'send_ingest' });
    const next = await run('ingest', () => ingestArdourExchangePackageId(packageId));
    if (next) {
      setPreview(next);
      setRealizeResult(null);
      setPrepareResult(null);
      setStemMessage('');
    }
  }

  async function handleIngestFile(file) {
    if (!file) return;
    logger.info('workflow step', { step: 'send_ingest_zip' });
    const next = await run('ingest_zip', () => ingestArdourExchangeZip(file));
    if (next) {
      setPreview(next);
      setRealizeResult(null);
      setPrepareResult(null);
      setStemMessage('');
    }
  }

  async function handleApplyInbound() {
    logger.info('workflow step', { step: 'apply_inbound' });
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
    setStemMessage('');
  }

  async function handleRealize() {
    logger.info('workflow step', { step: 'realize', intent });
    const result = await run('realize', () => realizeArdourExchange(intent));
    if (!result) return;
    setRealizeResult(result);
    const candidates = result.preview?.candidates || [];
    setSelectedCandidateId(candidates[0]?.candidate_id || null);
  }

  async function handleApplyRealize() {
    if (!realizeResult || !selectedCandidateId) return;
    logger.info('workflow step', { step: 'apply_realize' });
    setBusy(true);
    setError('');
    try {
      const ok = await applyRealizeCandidate(realizeResult, selectedCandidateId);
      if (!ok) setError('Realize Apply failed');
    } catch (err) {
      setError(err.message || 'Realize Apply failed');
      logger.error('workflow step failed', { step: 'apply_realize', code: err?.code || null });
    } finally {
      setBusy(false);
    }
  }

  async function handlePrepare() {
    logger.info('workflow step', { step: 'prepare' });
    const working = editedMusicJson?.schema_version === 'composition.v2' ? editedMusicJson : null;
    const result = await run('prepare', () => prepareArdourExchange({
      use_preview_alignment: true,
      composition: working || undefined,
    }));
    if (result) setPrepareResult(result);
  }

  async function handleStemWorkflow() {
    logger.info('workflow step', { step: 'stem_workflow' });
    setStemMessage('');
    const working = editedMusicJson?.schema_version === 'composition.v2' ? editedMusicJson : null;
    if (!working) {
      setError('Working composition.v2 required for stem prepare');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await runArdourStemWorkflow({ composition: working });
      setStemMessage(result.honesty || STEM_WORKFLOW_HONESTY);
      if (!result.ok) {
        setError(result.error || 'Stem workflow failed');
        return;
      }
      setPrepareResult(result.prepare);
    } catch (err) {
      setError(err.message || 'Stem workflow failed');
    } finally {
      setBusy(false);
    }
  }

  const alignment = preview?.alignment;
  const candidates = realizeResult?.preview?.candidates || [];

  return (
    <Wrap data-testid="ardour-exchange-panel">
      <Step data-testid="ardour-workflow-scope">
        <StepTitle>3. Selected musical scope</StepTitle>
        <Banner>
          {scope.hasPreviewAlignment
            ? `${scope.barsLabel} · ${scope.tempoBpm} bpm · ${scope.timeSignature}`
              + (scope.trackName ? ` · ${scope.trackName}` : '')
            : [
              scope.stripName ? `strip=${scope.stripName}` : 'strip=—',
              `locate=${scope.locateDisplay}`,
              scope.playing ? 'playing' : 'stopped',
            ].join(' · ')}
        </Banner>
        {scope.exportSelectionCta ? <Muted>{scope.exportSelectionCta}</Muted> : null}
      </Step>

      <Step data-testid="ardour-workflow-send">
        <StepTitle>4. Send to AI Composer</StepTitle>
        <Muted>
          AI Composer holds a temporary working score for AI ops; Ardour remains the DAW.
          Inbound Apply replaces the open score (merge-into-existing is out of scope).
        </Muted>
        <Banner>
          enabled={String(Boolean(status?.enabled))} root={String(Boolean(status?.root_configured))}
          {' '}companion={String(Boolean(status?.companion_connected))}
        </Banner>
        {error ? <Banner role="alert">{error}</Banner> : null}

        <Row>
          <Button type="button" disabled={!sendEnabled} onClick={() => void handleScanRoot()}>
            Scan exchange root
          </Button>
          <Button
            type="button"
            disabled={!sendEnabled}
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
                disabled={!sendEnabled}
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
            Apply inbound (replace score)
          </Button>
          <Button type="button" disabled={!preview || busy} onClick={() => void handleDiscard()}>
            Discard preview
          </Button>
        </Row>
      </Step>

      <Step data-testid="ardour-workflow-realize">
        <StepTitle>5. AI op → alternatives → Apply</StepTitle>
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
          <Button type="button" disabled={!realizeEnabled} onClick={() => void handleRealize()}>
            Realize
          </Button>
          <Button
            type="button"
            disabled={!applyRealizeEnabled}
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
                {candidate.composition?.tracks
                  ? ` (${candidate.composition.tracks.length} tracks)`
                  : ''}
              </label>
            ))}
          </Row>
        ) : null}
        {realizeResult ? (
          <Muted>
            surface={realizeResult.surface} · intent={realizeResult.intent}
            {' '}· candidates={candidates.length}
          </Muted>
        ) : null}
      </Step>

      <Step data-testid="ardour-workflow-return">
        <StepTitle>6. Send / import back to Ardour</StepTitle>
        <Muted>
          Prepare outbound MIDI from the working score (post-Apply) with preview alignment,
          then import the package in Ardour via the operator-installed Lua recipe.
        </Muted>
        <Row>
          <Button type="button" disabled={!prepareEnabled} onClick={() => void handlePrepare()}>
            Prepare outbound package
          </Button>
          <Button type="button" disabled={!prepareEnabled} onClick={() => void handleStemWorkflow()}>
            Create stems / render (optional)
          </Button>
          {prepareResult ? (
            <a href={ardourExchangeDownloadUrl(prepareResult.package_id)}>
              Download {prepareResult.package_id.slice(0, 12)}…
            </a>
          ) : null}
        </Row>
        {stemMessage ? <Muted>{stemMessage}</Muted> : null}
        <Muted>
          Lua import: backend/examples/ardour/import_exchange_package.lua · docs/ardour-session-exchange.md
        </Muted>
      </Step>
    </Wrap>
  );
};

export default ArdourExchangePanel;
