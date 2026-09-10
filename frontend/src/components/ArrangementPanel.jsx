import React, { useEffect, useId, useMemo, useState } from 'react';
import styled from 'styled-components';
import {
  ARRANGEMENT_AUDITION_CANDIDATE,
  ARRANGEMENT_AUDITION_SOURCE,
  useMusicStore,
} from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import {
  ARRANGEMENT_DOUBLING_POLICIES,
  ARRANGEMENT_MAX_INSTRUCTION_CHARS,
  ARRANGEMENT_OPERATIONS,
  ARRANGEMENT_RANGE_ADJUSTMENTS,
  editFingerprintLogPrefix,
  findArrangementCandidateById,
  normalizeArrangementRequest,
} from '../utils/compositionArrangementCandidates.js';
import { isCanonicalComposition, SUPPORTED_TRACK_ROLES } from '../utils/musicJsonValidation.js';

const panelLogger = createAppLogger('ArrangementPanel');

const OPERATION_HELP = Object.freeze({
  change_instrumentation:
    'Re-instrument selected tracks without changing topology or events. Roles stay; after inventory must differ.',
  add_accompaniment:
    'Add explicit harmony, rhythm, pad, or bass parts. Every source track remains exact; audible material increases.',
  remove_accompaniment:
    'Remove selected accompaniment-role material. Melody/lead and protected tracks stay exact.',
  orchestrate_selected_tracks:
    'Redistribute selected material among target parts. Every protected source note needs one primary representation.',
  piano_to_ensemble:
    'Split selected piano parts across at least two target instruments. Material is distributed, not cloned wholesale.',
  simplify_arrangement:
    'Remove or consolidate authorized non-melody material. Density decreases; no new musical material appears.',
  increase_texture_density:
    'Add harmonically compatible notes or parts so at least one density metric increases.',
  decrease_texture_density:
    'Thin authorized non-melody material so density decreases while melody stays protected.',
  create_countermelody:
    'Add one or more countermelody parts that stay compatible and are not excessive melody clones.',
  double_melody:
    'Add a declared instrument or register doubling. Only declared overlap is exempt from duplicate rejection.',
});

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation: none !important;
      transition: none !important;
      scroll-behavior: auto !important;
    }
  }
`;

const Title = styled.h4`
  margin: 0;
  color: #312e81;
  font-size: 1rem;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #64748b;
  line-height: 1.4;
  overflow-wrap: anywhere;
`;

const Grid = styled.div`
  display: grid;
  gap: 12px;
  min-width: 0;

  @media (min-width: 900px) {
    grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  }
`;

const Card = styled.article`
  padding: 12px;
  background: #ffffff;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;
`;

const CardTitle = styled.h5`
  margin: 0 0 10px;
  font-size: 0.9rem;
  font-weight: 600;
  color: #312e81;
`;

const Field = styled.label`
  display: grid;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
  margin-bottom: 8px;
  min-width: 0;
`;

const Fieldset = styled.fieldset`
  margin: 0 0 12px;
  padding: 10px;
  border: 1px solid #e0e7ff;
  border-radius: 8px;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
`;

const Legend = styled.legend`
  padding: 0 6px;
  font-size: 0.85rem;
  font-weight: 600;
  color: #312e81;
`;

const Input = styled.input`
  min-height: 44px;
  min-width: 0;
  max-width: 100%;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  box-sizing: border-box;
`;

const Select = styled.select`
  min-height: 44px;
  min-width: 0;
  max-width: 100%;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  background: #fff;
  box-sizing: border-box;
`;

const TextArea = styled.textarea`
  width: 100%;
  min-height: 72px;
  min-width: 0;
  max-width: 100%;
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
  margin-top: 8px;
`;

const Button = styled.button`
  min-height: 44px;
  min-width: 44px;
  padding: 8px 14px;
  border: none;
  border-radius: 8px;
  background: ${(props) => {
    if (props.$variant === 'danger') return '#dc2626';
    if (props.$variant === 'secondary') return '#475569';
    if (props.$variant === 'ghost') return '#e2e8f0';
    return '#4f46e5';
  }};
  color: ${(props) => (props.$variant === 'ghost' ? '#1e293b' : '#fff')};
  font-weight: 600;
  font-size: 0.9rem;
  cursor: pointer;
  touch-action: manipulation;

  &:disabled {
    background: #d1d5db;
    color: #64748b;
    cursor: not-allowed;
  }
`;

const StatusBanner = styled.div`
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid ${(props) => {
    if (props.$tone === 'error') return '#fecaca';
    if (props.$tone === 'warn') return '#fde68a';
    if (props.$tone === 'ok') return '#bbf7d0';
    return '#c7d2fe';
  }};
  background: ${(props) => {
    if (props.$tone === 'error') return '#fef2f2';
    if (props.$tone === 'warn') return '#fffbeb';
    if (props.$tone === 'ok') return '#f0fdf4';
    return '#eef2ff';
  }};
  color: ${(props) => {
    if (props.$tone === 'error') return '#991b1b';
    if (props.$tone === 'warn') return '#92400e';
    if (props.$tone === 'ok') return '#166534';
    return '#1e3a8a';
  }};
  font-size: 0.9rem;
  line-height: 1.4;
  overflow-wrap: anywhere;
`;

const CheckList = styled.div`
  display: grid;
  gap: 6px;
  max-height: 220px;
  overflow: auto;
  min-width: 0;
`;

const CheckOption = styled.label`
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 44px;
  padding: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 0.85rem;
  color: #334155;
  cursor: pointer;
  touch-action: manipulation;
  overflow-wrap: anywhere;

  input {
    margin-top: 2px;
    min-width: 18px;
    min-height: 18px;
  }
`;

const Meta = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  font-size: 0.8rem;
  color: #475569;
  overflow-wrap: anywhere;
`;

const InventoryList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
`;

const InventoryItem = styled.li`
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 0.85rem;
  color: #334155;
  overflow-wrap: anywhere;
`;

const PartCard = styled.div`
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid #e0e7ff;
  border-radius: 8px;
  background: #f8fafc;
  margin-bottom: 8px;
  min-width: 0;
`;

const PartGrid = styled.div`
  display: grid;
  gap: 8px;
  min-width: 0;

  @media (min-width: 520px) {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
`;

const CandidateList = styled.div`
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  border: none;
  min-width: 0;
`;

const CandidateCard = styled.label`
  display: grid;
  gap: 6px;
  border: 2px solid ${(props) => (props.$selected ? '#4f46e5' : '#e2e8f0')};
  border-radius: 10px;
  padding: 10px;
  background: ${(props) => (props.$selected ? '#eef2ff' : '#f8fafc')};
  cursor: pointer;
  touch-action: manipulation;
  min-width: 0;
  overflow-wrap: anywhere;

  input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }
`;

const WarningChip = styled.span`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 28px;
  padding: 2px 8px;
  border-radius: 6px;
  border: 1px solid ${(props) => (props.$tone === 'error' ? '#fecaca' : '#fde68a')};
  background: ${(props) => (props.$tone === 'error' ? '#fef2f2' : '#fffbeb')};
  color: ${(props) => (props.$tone === 'error' ? '#991b1b' : '#92400e')};
  font-size: 0.75rem;
  font-weight: 600;
`;

const RejectedList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
`;

const RejectedItem = styled.li`
  padding: 10px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 0.85rem;
  overflow-wrap: anywhere;
`;

const Details = styled.details`
  margin-top: 4px;
  font-size: 0.8rem;
  color: #475569;

  summary {
    min-height: 44px;
    display: flex;
    align-items: center;
    cursor: pointer;
    touch-action: manipulation;
  }
`;

const InlineCheck = styled.label`
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  font-size: 0.85rem;
  color: #334155;
  cursor: pointer;
  touch-action: manipulation;
`;

function operationLabel(operation) {
  return String(operation || '').replace(/_/g, ' ');
}

function normalizeToken(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/-/g, '_')
    .replace(/\s+/g, '_');
}

function resolveCatalogInstrumentForTrack(track, catalog) {
  const instruments = catalog?.instruments || [];
  if (!instruments.length || !track) {
    return null;
  }
  const label = String(track.instrument || '').trim();
  const byId = instruments.find((item) => item.instrument_id === label);
  if (byId) {
    return byId;
  }
  const normalized = normalizeToken(label);
  const byAlias = instruments.find((item) => (
    (item.aliases || []).some((alias) => normalizeToken(alias) === normalized)
  ));
  if (byAlias) {
    return byAlias;
  }
  const program = Number(track.midi_program);
  if (Number.isInteger(program)) {
    const isDrum = Boolean(track.is_drum);
    const byProgram = instruments.filter((item) => (
      item.midi_program === program && Boolean(item.is_drum) === isDrum
    ));
    if (byProgram.length === 1) {
      return byProgram[0];
    }
    if (byProgram.length > 1) {
      return [...byProgram].sort((a, b) => a.instrument_id.localeCompare(b.instrument_id))[0];
    }
  }
  return instruments.find((item) => normalizeToken(item.display_name) === normalized) || null;
}

function buildBeforePartsFromTracks(tracks, catalog) {
  return (tracks || []).map((track, index) => {
    const profile = resolveCatalogInstrumentForTrack(track, catalog);
    return {
      part_id: `before-${track.id || index}`,
      instrument_id: profile?.instrument_id || '',
      role: track.role || null,
      source_track_ids: [track.id],
      doubling_policy: 'none',
    };
  });
}

function createAfterPart(seed = {}, index = 0) {
  return {
    part_id: seed.part_id || `after-${index + 1}-${Date.now().toString(36).slice(-4)}`,
    instrument_id: seed.instrument_id || '',
    role: seed.role ?? null,
    source_track_ids: Array.isArray(seed.source_track_ids) ? [...seed.source_track_ids] : [],
    doubling_policy: seed.doubling_policy || 'none',
  };
}

function inventoryRowsFromComposition(composition) {
  return (composition?.tracks || []).map((track) => ({
    track_id: track.id,
    name: track.name || track.id,
    instrument: track.instrument || '—',
    role: track.role || '—',
    midi_program: track.midi_program ?? '—',
    event_count: Array.isArray(track.events) ? track.events.length : 0,
  }));
}

function formatDensityDelta(density) {
  if (!density?.before || !density?.after) {
    return null;
  }
  const keys = ['event_count', 'attack_count', 'active_track_count', 'max_simultaneity'];
  return keys.map((key) => {
    const before = Number(density.before[key]) || 0;
    const after = Number(density.after[key]) || 0;
    const delta = after - before;
    const sign = delta > 0 ? '+' : '';
    return `${key.replace(/_/g, ' ')}: ${before} → ${after} (${sign}${delta})`;
  });
}

function summarizeCandidateImpact(candidate) {
  const manifest = candidate?.manifest || {};
  const eventCounts = candidate?.event_counts || {};
  const removedTracks = (manifest.removed_track_ids || []).length;
  const addedTracks = (manifest.added_track_ids || []).length;
  const reinstrumented = (manifest.reinstrumented_track_ids || []).length;
  const removedEvents = Number(eventCounts.removed) || 0;
  const generatedEvents = Number(eventCounts.generated) || 0;
  const warningCodes = candidate?.warning_codes || [];
  const rangeWarnings = (candidate?.range_findings || []).filter((item) => item.severity === 'warning');
  const duplicateWarnings = (candidate?.duplicate_findings || []).filter((item) => item.severity === 'warning');
  const destructiveCount = removedTracks + removedEvents;
  const warningCount = warningCodes.length + rangeWarnings.length + duplicateWarnings.length;
  return {
    removedTracks,
    addedTracks,
    reinstrumented,
    removedEvents,
    generatedEvents,
    destructiveCount,
    warningCount,
    warningCodes,
  };
}

function toggleIdInList(list, id) {
  const current = Array.isArray(list) ? list : [];
  if (current.includes(id)) {
    return current.filter((item) => item !== id);
  }
  return [...current, id];
}

function roleOptions(catalog) {
  const fromCatalog = Array.isArray(catalog?.track_roles) ? catalog.track_roles : [];
  if (fromCatalog.length) {
    return fromCatalog;
  }
  return [...SUPPORTED_TRACK_ROLES];
}

const ArrangementPanel = () => {
  const reactId = useId();
  const radioName = `arrangement-candidate-${reactId}`;
  const [confirmApply, setConfirmApply] = useState(false);
  const [expandedCandidateId, setExpandedCandidateId] = useState(null);
  const [branchNameDraft, setBranchNameDraft] = useState('');
  const [applyBusy, setApplyBusy] = useState(false);

  const composition = useMusicStore((state) => state.editedMusicJson);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const baseRevision = useMusicStore((state) => state.compositionRevision);

  const operation = useMusicStore((state) => state.arrangementOperation);
  const sourceTrackIds = useMusicStore((state) => state.arrangementSourceTrackIds);
  const protectedTrackIds = useMusicStore((state) => state.arrangementProtectedTrackIds);
  const instrumentationBefore = useMusicStore((state) => state.arrangementInstrumentationBefore);
  const instrumentationAfter = useMusicStore((state) => state.arrangementInstrumentationAfter);
  const allowUnlistedAfter = useMusicStore((state) => state.arrangementAllowUnlistedAfter);
  const preserveMelody = useMusicStore((state) => state.arrangementPreserveMelody);
  const preserveHarmony = useMusicStore((state) => state.arrangementPreserveHarmony);
  const rangeAdjustment = useMusicStore((state) => state.arrangementRangeAdjustment);
  const candidateCount = useMusicStore((state) => state.arrangementCandidateCount);
  const instruction = useMusicStore((state) => state.arrangementInstruction);

  const catalog = useMusicStore((state) => state.arrangementCatalog);
  const catalogStatus = useMusicStore((state) => state.arrangementCatalogStatus);
  const catalogError = useMusicStore((state) => state.arrangementCatalogError);
  const catalogFingerprint = useMusicStore((state) => state.arrangementCatalogFingerprint);
  const status = useMusicStore((state) => state.arrangementStatus);
  const error = useMusicStore((state) => state.arrangementError);
  const staleReason = useMusicStore((state) => state.arrangementStaleReason);
  const warnings = useMusicStore((state) => state.arrangementWarnings);
  const arrangementBaseRevision = useMusicStore((state) => state.arrangementBaseRevision);
  const editSourceFingerprint = useMusicStore((state) => state.arrangementEditSourceFingerprint);
  const responseCatalogFingerprint = useMusicStore((state) => state.arrangementResponseCatalogFingerprint);
  const candidates = useMusicStore((state) => state.arrangementCandidates);
  const rejectedAttempts = useMusicStore((state) => state.arrangementRejectedAttempts);
  const selectedCandidateId = useMusicStore((state) => state.arrangementSelectedCandidateId);
  const auditionMode = useMusicStore((state) => state.arrangementAuditionMode);
  const arrangementCompareResult = useMusicStore((state) => state.arrangementCompareResult);
  const arrangementProvider = useMusicStore((state) => state.arrangementProvider);
  const arrangementModel = useMusicStore((state) => state.arrangementModel);
  const currentProjectId = useMusicStore((state) => state.currentProjectId);

  const setArrangementControls = useMusicStore((state) => state.setArrangementControls);
  const loadArrangementCatalog = useMusicStore((state) => state.loadArrangementCatalog);
  const startArrangementPreview = useMusicStore((state) => state.startArrangementPreview);
  const selectArrangementCandidate = useMusicStore((state) => state.selectArrangementCandidate);
  const setArrangementAuditionMode = useMusicStore((state) => state.setArrangementAuditionMode);
  const discardArrangementCandidates = useMusicStore((state) => state.discardArrangementCandidates);
  const rejectArrangementCandidate = useMusicStore((state) => state.rejectArrangementCandidate);
  const refreshArrangementComparison = useMusicStore((state) => state.refreshArrangementComparison);
  const applySelectedArrangementCandidate = useMusicStore(
    (state) => state.applySelectedArrangementCandidate,
  );

  useEffect(() => {
    if (catalogStatus === 'idle' || catalogStatus === 'error') {
      void loadArrangementCatalog();
    }
  }, [catalogStatus, loadArrangementCatalog]);

  useEffect(() => {
    setConfirmApply(false);
  }, [selectedCandidateId, status, arrangementBaseRevision]);

  const tracks = useMemo(
    () => (Array.isArray(composition?.tracks) ? composition.tracks : []),
    [composition],
  );

  const actualInventory = useMemo(
    () => inventoryRowsFromComposition(composition),
    [composition],
  );

  const selectedCandidate = useMemo(
    () => findArrangementCandidateById(candidates, selectedCandidateId),
    [candidates, selectedCandidateId],
  );

  const catalogStale = Boolean(
    responseCatalogFingerprint
    && catalogFingerprint
    && responseCatalogFingerprint !== catalogFingerprint,
  );
  const revisionStale = arrangementBaseRevision != null
    && arrangementBaseRevision !== baseRevision;
  const stale = status === 'stale' || catalogStale || revisionStale;
  const loading = status === 'loading' || catalogStatus === 'loading';

  const requestValidation = useMemo(() => {
    if (!isCanonicalComposition(composition)) {
      return { ok: false, message: 'Canonical composition.v2 required' };
    }
    return normalizeArrangementRequest({
      composition,
      operation,
      source_track_ids: sourceTrackIds,
      protected_track_ids: protectedTrackIds,
      instrumentation: {
        before: instrumentationBefore,
        after: instrumentationAfter,
      },
      allow_unlisted_after: allowUnlistedAfter,
      preserve_melody: preserveMelody,
      preserve_harmony: preserveHarmony,
      range_adjustment: rangeAdjustment,
      candidate_count: candidateCount,
      instruction: instruction || null,
    });
  }, [
    composition,
    operation,
    sourceTrackIds,
    protectedTrackIds,
    instrumentationBefore,
    instrumentationAfter,
    allowUnlistedAfter,
    preserveMelody,
    preserveHarmony,
    rangeAdjustment,
    candidateCount,
    instruction,
  ]);

  const canGenerate = requestValidation.ok && catalogStatus === 'ready' && !loading;
  const canApply = status === 'ready'
    && Boolean(selectedCandidate)
    && !stale
    && !loading;
  const applyImpact = selectedCandidate ? summarizeCandidateImpact(selectedCandidate) : null;
  const roles = roleOptions(catalog);
  const instruments = catalog?.instruments || [];

  const syncBeforeFromSources = () => {
    const sourceTracks = tracks.filter((track) => sourceTrackIds.includes(track.id));
    const parts = buildBeforePartsFromTracks(sourceTracks, catalog);
    panelLogger.info('Arrangement before parts synced from sources', {
      operation,
      partCount: parts.length,
      sourceCount: sourceTracks.length,
    });
    setArrangementControls({ instrumentationBefore: parts });
  };

  const onToggleSource = (trackId) => {
    const nextSources = toggleIdInList(sourceTrackIds, trackId);
    const nextProtected = protectedTrackIds.filter((id) => !nextSources.includes(id));
    panelLogger.debug('Arrangement source tracks updated', {
      operation,
      sourceCount: nextSources.length,
      protectedCount: nextProtected.length,
    });
    setArrangementControls({
      sourceTrackIds: nextSources,
      protectedTrackIds: nextProtected,
    });
  };

  const onToggleProtected = (trackId) => {
    if (sourceTrackIds.includes(trackId)) {
      return;
    }
    const nextProtected = toggleIdInList(protectedTrackIds, trackId);
    panelLogger.debug('Arrangement protected tracks updated', {
      operation,
      protectedCount: nextProtected.length,
    });
    setArrangementControls({ protectedTrackIds: nextProtected });
  };

  const updateAfterPart = (index, patch) => {
    const next = instrumentationAfter.map((part, partIndex) => (
      partIndex === index ? { ...part, ...patch } : part
    ));
    setArrangementControls({ instrumentationAfter: next });
  };

  const updateBeforePart = (index, patch) => {
    const next = instrumentationBefore.map((part, partIndex) => (
      partIndex === index ? { ...part, ...patch } : part
    ));
    setArrangementControls({ instrumentationBefore: next });
  };

  const addAfterPart = () => {
    const next = [
      ...instrumentationAfter,
      createAfterPart(
        {
          instrument_id: instruments[0]?.instrument_id || '',
          role: 'harmony',
        },
        instrumentationAfter.length,
      ),
    ];
    panelLogger.info('Arrangement after part added', {
      operation,
      partCount: next.length,
    });
    setArrangementControls({ instrumentationAfter: next });
  };

  const removeAfterPart = (index) => {
    const next = instrumentationAfter.filter((_, partIndex) => partIndex !== index);
    panelLogger.info('Arrangement after part removed', {
      operation,
      partCount: next.length,
    });
    setArrangementControls({ instrumentationAfter: next });
  };

  const handleGenerate = () => {
    setConfirmApply(false);
    panelLogger.info('Arrangement generate requested', {
      operation,
      candidateCount,
      sourceCount: sourceTrackIds.length,
      status: 'start',
    });
    void startArrangementPreview();
  };

  const handleSelectCandidate = (candidateId) => {
    setConfirmApply(false);
    panelLogger.info('Arrangement candidate selected from UI', {
      operation,
      candidateIdSuffix: String(candidateId || '').slice(-8),
    });
    selectArrangementCandidate(candidateId);
  };

  const handlePlaySource = () => {
    panelLogger.info('Arrangement play source', {
      operation,
      status: auditionMode,
    });
    setArrangementAuditionMode(ARRANGEMENT_AUDITION_SOURCE);
  };

  const handleAuditionCandidate = () => {
    if (!selectedCandidate) {
      return;
    }
    panelLogger.info('Arrangement audition candidate', {
      operation,
      candidateIdSuffix: selectedCandidate.candidate_id.slice(-8),
    });
    setArrangementAuditionMode(ARRANGEMENT_AUDITION_CANDIDATE);
  };

  const handleDiscard = () => {
    setConfirmApply(false);
    setBranchNameDraft('');
    panelLogger.info('Arrangement discard from UI', {
      operation,
      candidateCount: candidates.length,
    });
    discardArrangementCandidates();
  };

  const handleRejectSelected = () => {
    if (!selectedCandidate) {
      return;
    }
    setConfirmApply(false);
    console.info('[FIX:arrange-ui] Reject arrangement candidate', {
      candidateIdSuffix: selectedCandidate.candidate_id.slice(-8),
      remaining: Math.max(0, candidates.length - 1),
    });
    rejectArrangementCandidate(selectedCandidate.candidate_id);
  };

  const handleCompareSelected = () => {
    if (!selectedCandidate) {
      return;
    }
    console.debug('[FIX:arrange-ui] Compare arrangement candidate', {
      candidateIdSuffix: selectedCandidate.candidate_id.slice(-8),
    });
    refreshArrangementComparison();
  };

  const handleApplyClick = async () => {
    if (!canApply || !selectedCandidate || applyBusy) {
      return;
    }
    if (!confirmApply) {
      setConfirmApply(true);
      panelLogger.info('Arrangement apply confirmation shown', {
        operation,
        candidateIdSuffix: selectedCandidate.candidate_id.slice(-8),
        destructiveCount: applyImpact?.destructiveCount || 0,
        warningCount: applyImpact?.warningCount || 0,
      });
      return;
    }
    setApplyBusy(true);
    panelLogger.info('Arrangement apply confirmed', {
      operation,
      candidateIdSuffix: selectedCandidate.candidate_id.slice(-8),
      status: 'apply',
    });
    try {
      const ok = await applySelectedArrangementCandidate();
      if (ok) {
        setConfirmApply(false);
      }
    } finally {
      setApplyBusy(false);
    }
  };

  const handleApplyAsBranch = async () => {
    if (!canApply || !selectedCandidate || applyBusy || !branchNameDraft.trim()) {
      return;
    }
    setApplyBusy(true);
    console.info('[FIX:arrange-ui] Apply arrangement as new branch', {
      candidateIdSuffix: selectedCandidate.candidate_id.slice(-8),
      nameLength: branchNameDraft.trim().length,
    });
    try {
      const ok = await applySelectedArrangementCandidate({
        asNewBranch: true,
        branchName: branchNameDraft.trim(),
      });
      if (ok) {
        setConfirmApply(false);
        setBranchNameDraft('');
      }
    } catch {
      // store records arrangementError / conflict
    } finally {
      setApplyBusy(false);
    }
  };

  if (!isCanonicalComposition(composition)) {
    return (
      <Panel data-testid="arrange-panel">
        <Title>Arrange</Title>
        <Hint>
          Load or generate a composition.v2 piece to rearrange instrumentation and texture
          without replacing the music wholesale.
        </Hint>
      </Panel>
    );
  }

  const statusTone = (() => {
    if (status === 'error' || stale) return 'error';
    if (status === 'stale' || catalogStale) return 'warn';
    if (status === 'ready') return 'ok';
    return undefined;
  })();

  const statusMessage = (() => {
    if (loading && catalogStatus === 'loading') return 'Loading instrument catalog…';
    if (loading && status === 'loading') return 'Generating arrangement candidates…';
    if (catalogStatus === 'error') return catalogError || 'Instrument catalog unavailable';
    if (stale) {
      if (catalogStale || staleReason === 'catalog_changed') {
        return error || 'Instrument catalog changed — regenerate candidates';
      }
      if (staleReason === 'settings_changed') {
        return error || 'Arrangement settings changed — regenerate candidates';
      }
      return error || 'Composition changed — regenerate candidates';
    }
    if (status === 'ready') {
      return `${candidates.length} valid candidate(s)`
        + (rejectedAttempts.length ? `, ${rejectedAttempts.length} rejected` : '')
        + ' ready';
    }
    if (status === 'error') return error || 'Preview failed';
    return 'No arrangement candidates yet';
  })();

  return (
    <Panel data-testid="arrange-panel" aria-label="Composition arrangement">
      <Title>Arrange</Title>
      <Hint>
        Preview candidates do not change or save the composition. Apply commits one verified
        candidate as a normal edit (undoable, dirty, autosaved). Role and instrument stay independent.
      </Hint>

      <Grid>
        <Card>
          <Field>
            Operation
            <Select
              data-testid="arrange-operation"
              value={operation}
              onChange={(event) => {
                panelLogger.info('Arrangement operation changed', {
                  operation: event.target.value,
                });
                setArrangementControls({ operation: event.target.value });
              }}
              aria-describedby="arrange-operation-help"
              aria-label="Arrangement operation"
            >
              {ARRANGEMENT_OPERATIONS.map((value) => (
                <option key={value} value={value}>{operationLabel(value)}</option>
              ))}
            </Select>
            <Hint id="arrange-operation-help" data-testid="arrange-operation-help">
              {OPERATION_HELP[operation] || 'Choose an arrangement operation.'}
            </Hint>
          </Field>

          <Fieldset data-testid="arrange-source-tracks">
            <Legend>Source tracks</Legend>
            <Hint>Material authorized for redistribution or transformation.</Hint>
            <CheckList aria-label="Source tracks">
              {tracks.map((track) => {
                const checked = sourceTrackIds.includes(track.id);
                return (
                  <CheckOption key={`src-${track.id}`}>
                    <input
                      type="checkbox"
                      data-testid={`arrange-source-${track.id}`}
                      checked={checked}
                      onChange={() => onToggleSource(track.id)}
                    />
                    <span>
                      <strong>{track.name || track.id}</strong>
                      {` · role ${track.role || '—'} · instrument ${track.instrument || '—'} · prog ${track.midi_program ?? '—'}`}
                    </span>
                  </CheckOption>
                );
              })}
            </CheckList>
          </Fieldset>

          <Fieldset data-testid="arrange-protected-tracks">
            <Legend>Protected tracks</Legend>
            <Hint>Must remain exact; cannot also be selected as source.</Hint>
            <CheckList aria-label="Protected tracks">
              {tracks.map((track) => {
                const lockedBySource = sourceTrackIds.includes(track.id);
                const checked = protectedTrackIds.includes(track.id);
                return (
                  <CheckOption key={`prot-${track.id}`}>
                    <input
                      type="checkbox"
                      data-testid={`arrange-protected-${track.id}`}
                      checked={checked}
                      disabled={lockedBySource}
                      onChange={() => onToggleProtected(track.id)}
                    />
                    <span>
                      <strong>{track.name || track.id}</strong>
                      {lockedBySource ? ' (selected as source)' : ` · ${track.role || '—'}`}
                    </span>
                  </CheckOption>
                );
              })}
            </CheckList>
          </Fieldset>

          <Fieldset data-testid="arrange-before-inventory">
            <Legend>Actual before inventory</Legend>
            <Hint>Current composition tracks (read-only). Role ≠ instrument.</Hint>
            <InventoryList>
              {actualInventory.map((row) => (
                <InventoryItem key={row.track_id} data-testid={`arrange-inventory-${row.track_id}`}>
                  <strong>{row.name}</strong>
                  {` · role ${row.role} · instrument ${row.instrument} · prog ${row.midi_program} · ${row.event_count} events`}
                </InventoryItem>
              ))}
            </InventoryList>
          </Fieldset>

          <Fieldset data-testid="arrange-before-parts">
            <Legend>Request before parts</Legend>
            <Hint>Precondition inventory sent to the backend. Sync from selected sources, then adjust if needed.</Hint>
            <ButtonRow>
              <Button
                type="button"
                $variant="ghost"
                data-testid="arrange-sync-before"
                onClick={syncBeforeFromSources}
                disabled={!sourceTrackIds.length || catalogStatus !== 'ready'}
              >
                Sync before from sources
              </Button>
            </ButtonRow>
            {(instrumentationBefore || []).map((part, index) => (
              <PartCard key={part.part_id || `before-${index}`} data-testid={`arrange-before-part-${index}`}>
                <PartGrid>
                  <Field>
                    Part ID
                    <Input
                      value={part.part_id || ''}
                      aria-label={`Before part ${index + 1} id`}
                      onChange={(event) => updateBeforePart(index, { part_id: event.target.value })}
                    />
                  </Field>
                  <Field>
                    Instrument
                    <Select
                      data-testid={`arrange-before-instrument-${index}`}
                      value={part.instrument_id || ''}
                      aria-label={`Before part ${index + 1} instrument`}
                      onChange={(event) => updateBeforePart(index, { instrument_id: event.target.value })}
                    >
                      <option value="">Select instrument…</option>
                      {instruments.map((item) => (
                        <option key={item.instrument_id} value={item.instrument_id}>
                          {item.display_name}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field>
                    Role
                    <Select
                      data-testid={`arrange-before-role-${index}`}
                      value={part.role || ''}
                      aria-label={`Before part ${index + 1} role`}
                      onChange={(event) => updateBeforePart(index, {
                        role: event.target.value || null,
                      })}
                    >
                      <option value="">(optional)</option>
                      {roles.map((role) => (
                        <option key={role} value={role}>{role}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field>
                    Doubling
                    <Select
                      value={part.doubling_policy || 'none'}
                      aria-label={`Before part ${index + 1} doubling`}
                      onChange={(event) => updateBeforePart(index, {
                        doubling_policy: event.target.value,
                      })}
                    >
                      {ARRANGEMENT_DOUBLING_POLICIES.map((policy) => (
                        <option key={policy} value={policy}>{policy}</option>
                      ))}
                    </Select>
                  </Field>
                </PartGrid>
                <Hint>
                  Sources:
                  {' '}
                  {(part.source_track_ids || []).join(', ') || '—'}
                </Hint>
              </PartCard>
            ))}
          </Fieldset>

          <Fieldset data-testid="arrange-after-parts">
            <Legend>Target after parts</Legend>
            <Hint>
              Desired instrumentation. Same instrument with different roles is allowed
              (e.g. piano melody + piano accompaniment).
            </Hint>
            {(instrumentationAfter || []).map((part, index) => (
              <PartCard key={part.part_id || `after-${index}`} data-testid={`arrange-after-part-${index}`}>
                <PartGrid>
                  <Field>
                    Part ID
                    <Input
                      value={part.part_id || ''}
                      aria-label={`After part ${index + 1} id`}
                      onChange={(event) => updateAfterPart(index, { part_id: event.target.value })}
                    />
                  </Field>
                  <Field>
                    Instrument
                    <Select
                      data-testid={`arrange-after-instrument-${index}`}
                      value={part.instrument_id || ''}
                      aria-label={`After part ${index + 1} instrument`}
                      onChange={(event) => updateAfterPart(index, { instrument_id: event.target.value })}
                    >
                      <option value="">Select instrument…</option>
                      {instruments.map((item) => (
                        <option key={item.instrument_id} value={item.instrument_id}>
                          {item.display_name}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field>
                    Role
                    <Select
                      data-testid={`arrange-after-role-${index}`}
                      value={part.role || ''}
                      aria-label={`After part ${index + 1} role`}
                      onChange={(event) => updateAfterPart(index, {
                        role: event.target.value || null,
                      })}
                    >
                      <option value="">(optional)</option>
                      {roles.map((role) => (
                        <option key={role} value={role}>{role}</option>
                      ))}
                    </Select>
                  </Field>
                  <Field>
                    Doubling
                    <Select
                      data-testid={`arrange-after-doubling-${index}`}
                      value={part.doubling_policy || 'none'}
                      aria-label={`After part ${index + 1} doubling`}
                      onChange={(event) => updateAfterPart(index, {
                        doubling_policy: event.target.value,
                      })}
                    >
                      {ARRANGEMENT_DOUBLING_POLICIES.map((policy) => (
                        <option key={policy} value={policy}>{policy}</option>
                      ))}
                    </Select>
                  </Field>
                </PartGrid>
                <Field>
                  Source track mapping (comma-separated ids)
                  <Input
                    data-testid={`arrange-after-sources-${index}`}
                    value={(part.source_track_ids || []).join(', ')}
                    aria-label={`After part ${index + 1} source tracks`}
                    onChange={(event) => {
                      const ids = event.target.value
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean);
                      updateAfterPart(index, { source_track_ids: ids });
                    }}
                  />
                </Field>
                <ButtonRow>
                  <Button
                    type="button"
                    $variant="ghost"
                    data-testid={`arrange-remove-after-${index}`}
                    onClick={() => removeAfterPart(index)}
                    disabled={instrumentationAfter.length <= 1}
                  >
                    Remove part
                  </Button>
                </ButtonRow>
              </PartCard>
            ))}
            <ButtonRow>
              <Button
                type="button"
                $variant="secondary"
                data-testid="arrange-add-after"
                onClick={addAfterPart}
                disabled={catalogStatus !== 'ready'}
              >
                Add target part
              </Button>
            </ButtonRow>
          </Fieldset>

          <Fieldset data-testid="arrange-preservation">
            <Legend>Preservation & range</Legend>
            <InlineCheck>
              <input
                type="checkbox"
                data-testid="arrange-preserve-melody"
                checked={Boolean(preserveMelody)}
                onChange={(event) => setArrangementControls({
                  preserveMelody: event.target.checked,
                })}
              />
              Preserve melody
            </InlineCheck>
            <InlineCheck>
              <input
                type="checkbox"
                data-testid="arrange-preserve-harmony"
                checked={Boolean(preserveHarmony)}
                onChange={(event) => setArrangementControls({
                  preserveHarmony: event.target.checked,
                })}
              />
              Preserve harmony compatibility
            </InlineCheck>
            <InlineCheck>
              <input
                type="checkbox"
                data-testid="arrange-allow-unlisted"
                checked={Boolean(allowUnlistedAfter)}
                onChange={(event) => setArrangementControls({
                  allowUnlistedAfter: event.target.checked,
                })}
              />
              Allow unlisted after parts
            </InlineCheck>
            <Field>
              Range adjustment
              <Select
                data-testid="arrange-range-adjustment"
                value={rangeAdjustment}
                aria-label="Range adjustment policy"
                onChange={(event) => setArrangementControls({
                  rangeAdjustment: event.target.value,
                })}
              >
                {ARRANGEMENT_RANGE_ADJUSTMENTS.map((value) => (
                  <option key={value} value={value}>{operationLabel(value)}</option>
                ))}
              </Select>
            </Field>
          </Fieldset>

          <Field>
            Candidate count
            <Select
              data-testid="arrange-candidate-count"
              value={candidateCount}
              aria-label="Arrangement candidate count"
              onChange={(event) => setArrangementControls({
                candidateCount: Number(event.target.value),
              })}
            >
              {[1, 2, 3, 4].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </Select>
          </Field>

          <Field>
            Instruction (optional)
            <TextArea
              data-testid="arrange-instruction"
              value={instruction}
              maxLength={ARRANGEMENT_MAX_INSTRUCTION_CHARS}
              aria-label="Arrangement instruction"
              placeholder="e.g. keep melody on piano; cello on bass; strings on harmony"
              onChange={(event) => setArrangementControls({
                instruction: event.target.value.slice(0, ARRANGEMENT_MAX_INSTRUCTION_CHARS),
              })}
            />
          </Field>

          <Meta data-testid="arrange-provider-model">
            <span>
              Provider:
              {' '}
              {selectedProvider || arrangementProvider || '—'}
            </span>
            <span>
              Model:
              {' '}
              {selectedModel || arrangementModel || '—'}
            </span>
            {catalogFingerprint ? (
              <span>
                Catalog:
                {' '}
                {editFingerprintLogPrefix(catalogFingerprint)}
                …
              </span>
            ) : null}
          </Meta>

          {!requestValidation.ok ? (
            <StatusBanner
              role="alert"
              $tone="error"
              data-testid="arrange-request-invalid"
            >
              Invalid request:
              {' '}
              {requestValidation.message || requestValidation.code || 'Fix controls before generating'}
            </StatusBanner>
          ) : null}

          <ButtonRow>
            <Button
              type="button"
              data-testid="arrange-generate"
              onClick={handleGenerate}
              disabled={!canGenerate}
            >
              {status === 'loading'
                ? 'Generating…'
                : (candidates.length ? 'Regenerate' : 'Generate candidates')}
            </Button>
            <Button
              type="button"
              $variant="ghost"
              data-testid="arrange-discard"
              onClick={handleDiscard}
              disabled={!candidates.length && !rejectedAttempts.length && status === 'idle'}
            >
              Discard
            </Button>
          </ButtonRow>
        </Card>

        <Card>
          <StatusBanner
            role="status"
            aria-live="polite"
            data-testid="arrange-status"
            $tone={statusTone}
          >
            {statusMessage}
          </StatusBanner>

          {editSourceFingerprint ? (
            <Hint data-testid="arrange-source-fingerprint">
              Source fingerprint
              {' '}
              {editFingerprintLogPrefix(editSourceFingerprint)}
              …
            </Hint>
          ) : null}

          {stale ? (
            <StatusBanner role="alert" $tone="warn" data-testid="arrange-stale">
              Preview is stale
              {staleReason ? ` (${staleReason})` : ''}
              . Regenerate before audition or Apply.
            </StatusBanner>
          ) : null}

          {(warnings || []).length > 0 ? (
            <StatusBanner $tone="warn" data-testid="arrange-warnings" role="status">
              Response warnings:
              {' '}
              {warnings.join(', ')}
            </StatusBanner>
          ) : null}

          <CardTitle style={{ marginTop: 8 }}>Valid candidates</CardTitle>
          <CandidateList
            role="radiogroup"
            aria-label="Arrangement candidates"
            data-testid="arrange-candidate-list"
          >
            {candidates.length === 0 ? (
              <Hint>No valid candidates yet.</Hint>
            ) : (
              candidates.map((candidate, index) => {
                const selected = candidate.candidate_id === selectedCandidateId;
                const impact = summarizeCandidateImpact(candidate);
                const densityLines = formatDensityDelta(candidate.density);
                const beforeInv = candidate.before_inventory || [];
                const afterInv = candidate.after_inventory || [];
                const assertions = candidate.assertions || [];
                const rangeFindings = candidate.range_findings || [];
                const duplicateFindings = candidate.duplicate_findings || [];
                const harmony = candidate.harmony_compatibility;
                const eventCounts = candidate.event_counts || {};
                const manifest = candidate.manifest || {};
                const detailsOpen = expandedCandidateId === candidate.candidate_id
                  || selected;
                return (
                  <CandidateCard
                    key={candidate.candidate_id}
                    $selected={selected}
                    data-testid={`arrange-candidate-${index}`}
                    data-candidate-id={candidate.candidate_id}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <input
                        type="radio"
                        name={radioName}
                        value={candidate.candidate_id}
                        checked={selected}
                        onChange={() => handleSelectCandidate(candidate.candidate_id)}
                        aria-label={`Select arrangement candidate ${index + 1}`}
                        data-testid={`arrange-candidate-radio-${index}`}
                      />
                      <strong>
                        Candidate
                        {' '}
                        {index + 1}
                      </strong>
                    </span>
                    <Meta>
                      <span>
                        id …
                        {candidate.candidate_id.slice(-8)}
                      </span>
                      <span>
                        Provider:
                        {' '}
                        {candidate.provider || arrangementProvider || '—'}
                      </span>
                      <span>
                        Model:
                        {' '}
                        {candidate.model || arrangementModel || '—'}
                      </span>
                      <span>
                        tracks
                        {' '}
                        {beforeInv.length}
                        →
                        {afterInv.length}
                      </span>
                      <span>
                        events +
                        {Number(eventCounts.generated) || 0}
                        {' / −'}
                        {Number(eventCounts.removed) || 0}
                      </span>
                    </Meta>
                    <Meta>
                      <span>
                        Topology: +
                        {(manifest.added_track_ids || []).length}
                        {' / −'}
                        {(manifest.removed_track_ids || []).length}
                        {' / reinstrument '}
                        {(manifest.reinstrumented_track_ids || []).length}
                      </span>
                    </Meta>
                    {(candidate.warning_codes || []).length > 0 ? (
                      <Meta data-testid={`arrange-candidate-warnings-${index}`}>
                        {(candidate.warning_codes || []).map((code) => (
                          <WarningChip key={code} $tone="warn">
                            Warning:
                            {' '}
                            {code}
                          </WarningChip>
                        ))}
                      </Meta>
                    ) : null}
                    {rangeFindings.filter((item) => item.severity !== 'info').map((finding, findingIndex) => (
                      <WarningChip
                        key={`${candidate.candidate_id}-range-${findingIndex}`}
                        $tone={finding.severity === 'error' ? 'error' : 'warn'}
                      >
                        {finding.severity === 'error' ? 'Range error' : 'Range warning'}
                        :
                        {' '}
                        {finding.code}
                      </WarningChip>
                    ))}
                    {duplicateFindings.filter((item) => item.severity !== 'info').map((finding, findingIndex) => (
                      <WarningChip
                        key={`${candidate.candidate_id}-dup-${findingIndex}`}
                        $tone={finding.severity === 'error' ? 'error' : 'warn'}
                      >
                        {finding.severity === 'error' ? 'Duplicate error' : 'Duplicate warning'}
                        :
                        {' '}
                        {finding.code}
                      </WarningChip>
                    ))}
                    {harmony?.failed ? (
                      <WarningChip $tone="error">
                        Harmony:
                        {' '}
                        failed
                      </WarningChip>
                    ) : null}
                    {harmony && !harmony.failed && Number(harmony.tension_note_count) > 0 ? (
                      <WarningChip $tone="warn">
                        Harmony tension:
                        {' '}
                        {harmony.tension_note_count}
                      </WarningChip>
                    ) : null}
                    <Details
                      open={detailsOpen}
                      onToggle={(event) => {
                        setExpandedCandidateId(
                          event.currentTarget.open ? candidate.candidate_id : null,
                        );
                      }}
                    >
                      <summary data-testid={`arrange-candidate-details-${index}`}>
                        Expand instrumentation, density, and assertions
                      </summary>
                      <Hint>
                        Before:
                        {' '}
                        {beforeInv.map((row) => `${row.role || '?'}/${row.instrument || '?'}`).join(', ') || '—'}
                      </Hint>
                      <Hint>
                        After:
                        {' '}
                        {afterInv.map((row) => `${row.role || '?'}/${row.instrument || '?'}`).join(', ') || '—'}
                      </Hint>
                      {densityLines ? (
                        <Hint data-testid={`arrange-candidate-density-${index}`}>
                          Density:
                          {' '}
                          {densityLines.join('; ')}
                        </Hint>
                      ) : (
                        <Hint>Density: —</Hint>
                      )}
                      <Hint>
                        Assertions:
                        {' '}
                        {assertions.length
                          ? assertions.map((item) => (
                            `${item.kind}:${item.satisfied ? 'ok' : 'fail'}`
                          )).join(', ')
                          : '—'}
                      </Hint>
                      {impact.warningCount > 0 ? (
                        <Hint>
                          Warning count:
                          {' '}
                          {impact.warningCount}
                          ; destructive count:
                          {' '}
                          {impact.destructiveCount}
                        </Hint>
                      ) : null}
                    </Details>
                  </CandidateCard>
                );
              })
            )}
          </CandidateList>

          <CardTitle style={{ marginTop: 12 }}>Rejected attempts</CardTitle>
          <RejectedList data-testid="arrange-rejected-list" aria-label="Rejected arrangement attempts">
            {rejectedAttempts.length === 0 ? (
              <Hint>No rejected attempts.</Hint>
            ) : (
              rejectedAttempts.map((attempt, index) => (
                <RejectedItem
                  key={`rejected-${attempt.ordinal}-${attempt.stage}-${index}`}
                  data-testid={`arrange-rejected-${index}`}
                >
                  <strong>
                    Attempt
                    {' '}
                    {attempt.ordinal}
                  </strong>
                  {` · stage ${attempt.stage}`}
                  <div>
                    Codes:
                    {' '}
                    {(attempt.codes || []).join(', ') || '—'}
                  </div>
                  {(attempt.reasons || []).length > 0 ? (
                    <div>
                      Reasons:
                      {' '}
                      {attempt.reasons.join(' · ')}
                    </div>
                  ) : null}
                  <Hint style={{ color: '#7f1d1d' }}>
                    Rejected attempts cannot be selected, auditioned, or applied.
                  </Hint>
                </RejectedItem>
              ))
            )}
          </RejectedList>

          {confirmApply && selectedCandidate && applyImpact ? (
            <StatusBanner
              role="alert"
              $tone="warn"
              data-testid="arrange-apply-confirm"
            >
              Apply will commit candidate …
              {selectedCandidate.candidate_id.slice(-8)}
              .
              {' '}
              Destructive changes:
              {' '}
              {applyImpact.destructiveCount}
              {' '}
              (removed tracks
              {' '}
              {applyImpact.removedTracks}
              , removed events
              {' '}
              {applyImpact.removedEvents}
              ).
              {' '}
              Warnings:
              {' '}
              {applyImpact.warningCount}
              .
              {' '}
              Added tracks
              {' '}
              {applyImpact.addedTracks}
              ; reinstrumented
              {' '}
              {applyImpact.reinstrumented}
              .
              Confirm to continue.
            </StatusBanner>
          ) : null}

          <ButtonRow>
            <Button
              type="button"
              $variant="secondary"
              data-testid="arrange-play-source"
              onClick={handlePlaySource}
              aria-pressed={auditionMode === ARRANGEMENT_AUDITION_SOURCE}
              disabled={applyBusy}
            >
              Play source
            </Button>
            <Button
              type="button"
              $variant="secondary"
              data-testid="arrange-audition"
              disabled={!selectedCandidate || stale || status !== 'ready' || applyBusy}
              onClick={handleAuditionCandidate}
              aria-pressed={auditionMode === ARRANGEMENT_AUDITION_CANDIDATE}
            >
              {auditionMode === ARRANGEMENT_AUDITION_CANDIDATE
                ? 'Auditioning candidate'
                : 'Audition candidate'}
            </Button>
            <Button
              type="button"
              $variant="secondary"
              data-testid="arrange-compare"
              disabled={!selectedCandidate || applyBusy}
              onClick={handleCompareSelected}
            >
              Compare
            </Button>
            <Button
              type="button"
              data-testid="arrange-apply"
              disabled={!canApply || applyBusy}
              onClick={handleApplyClick}
            >
              {confirmApply ? 'Confirm apply selected' : 'Apply selected'}
            </Button>
            <Button
              type="button"
              $variant="secondary"
              data-testid="arrange-reject"
              disabled={!selectedCandidate || applyBusy}
              onClick={handleRejectSelected}
            >
              Reject
            </Button>
          </ButtonRow>
          {arrangementCompareResult ? (
            <Hint data-testid="arrange-compare-summary">
              Compare vs working:{' '}
              {arrangementCompareResult.identical ? 'identical' : 'differences'}
              {' · '}
              +{arrangementCompareResult.events?.added || 0}
              {' / -'}
              {arrangementCompareResult.events?.removed || 0}
              {' / ~'}
              {arrangementCompareResult.events?.changed || 0}
            </Hint>
          ) : null}
          {currentProjectId ? (
            <ButtonRow>
              <input
                aria-label="Apply arrangement as new branch name"
                data-testid="arrange-branch-name"
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
                data-testid="arrange-apply-as-branch"
                disabled={!canApply || applyBusy || !branchNameDraft.trim()}
                onClick={handleApplyAsBranch}
              >
                Apply as new branch
              </Button>
            </ButtonRow>
          ) : null}
          {auditionMode === ARRANGEMENT_AUDITION_CANDIDATE ? (
            <Hint data-testid="arrange-audition-active">
              Transport plays the selected candidate. Working composition is unchanged until Apply.
            </Hint>
          ) : (
            <Hint data-testid="arrange-audition-source">
              Transport plays the working source composition.
            </Hint>
          )}
        </Card>
      </Grid>
    </Panel>
  );
};

export default ArrangementPanel;
