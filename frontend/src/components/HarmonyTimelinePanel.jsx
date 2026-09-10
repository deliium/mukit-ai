import React, { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';
import {
  REHARMONIZE_CONTENT_POLICIES,
  REHARMONIZE_ENGINES,
  REHARMONIZE_OPERATIONS,
} from '../api/musicApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { inferredBarLabelForSpan, selectionTicksFromBars } from '../utils/compositionHarmony.js';
import { compileTimeline, pointerXToBarFromTimeline } from '../utils/compositionTimeline.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { selectionOverlayRect } from '../utils/pianoRollSelection.js';

const PIXELS_PER_TICK = 0.12;
const TIMELINE_HEIGHT = 96;
const RULER_HEIGHT = 28;

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;
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
  min-height: 88px;
  min-width: 0;
  max-width: 100%;
  padding: 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  resize: vertical;
  box-sizing: border-box;
`;

const ButtonRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
`;

const Button = styled.button`
  min-height: 44px;
  padding: 8px 14px;
  border: none;
  border-radius: 8px;
  background: ${(props) => (props.$secondary ? '#e2e8f0' : '#4f46e5')};
  color: ${(props) => (props.$secondary ? '#1e293b' : '#fff')};
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

const TimelineScroll = styled.div`
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  overscroll-behavior-x: contain;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
  background: #f8fafc;
  touch-action: pan-x;
  contain: inline-size;
`;

const TimelineInner = styled.div`
  position: relative;
  height: ${TIMELINE_HEIGHT + RULER_HEIGHT}px;
  min-width: 100%;
  max-width: none;
  box-sizing: border-box;
  user-select: none;
`;

const Ruler = styled.div`
  position: absolute;
  top: 0;
  left: 0;
  height: ${RULER_HEIGHT}px;
  border-bottom: 1px solid #c7d2fe;
  background: #eef2ff;
`;

const BarLabel = styled.button`
  position: absolute;
  top: 0;
  height: ${RULER_HEIGHT}px;
  min-width: 28px;
  min-height: 28px;
  padding: 0 4px;
  border: none;
  background: transparent;
  color: #4338ca;
  font-size: 0.7rem;
  font-weight: 600;
  cursor: pointer;
  touch-action: manipulation;
`;

const Lane = styled.div`
  position: absolute;
  top: ${RULER_HEIGHT}px;
  left: 0;
  height: ${TIMELINE_HEIGHT}px;
  background:
    repeating-linear-gradient(
      90deg,
      transparent,
      transparent 23px,
      rgba(199, 210, 254, 0.35) 23px,
      rgba(199, 210, 254, 0.35) 24px
    );
`;

const SelectionOverlay = styled.div`
  position: absolute;
  top: ${RULER_HEIGHT}px;
  height: ${TIMELINE_HEIGHT}px;
  background: rgba(79, 70, 229, 0.18);
  border: 1px solid rgba(79, 70, 229, 0.45);
  pointer-events: none;
  box-sizing: border-box;
`;

const SpanBlock = styled.button`
  position: absolute;
  top: ${RULER_HEIGHT + 10}px;
  height: ${TIMELINE_HEIGHT - 20}px;
  min-height: 44px;
  padding: 4px 6px;
  border: 1px solid ${(props) => (props.$active ? '#312e81' : '#818cf8')};
  border-radius: 6px;
  background: ${(props) => (props.$active ? '#c7d2fe' : '#e0e7ff')};
  color: #1e1b4b;
  font-size: 0.75rem;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  touch-action: manipulation;
  z-index: 2;
`;

const GapBlock = styled.div`
  position: absolute;
  top: ${RULER_HEIGHT + 18}px;
  height: ${TIMELINE_HEIGHT - 36}px;
  border: 1px dashed #cbd5e1;
  border-radius: 4px;
  background: rgba(248, 250, 252, 0.7);
  color: #94a3b8;
  font-size: 0.65rem;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 1;
`;

const RadioGroup = styled.fieldset`
  margin: 0 0 10px;
  padding: 0;
  border: none;
  display: grid;
  gap: 6px;
  min-width: 0;
`;

const RadioLegend = styled.legend`
  font-size: 0.85rem;
  color: #334155;
  margin-bottom: 4px;
  padding: 0;
`;

const RadioOption = styled.label`
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 6px 8px;
  border: 1px solid ${(props) => (props.$active ? '#4f46e5' : '#e2e8f0')};
  border-radius: 8px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#fff')};
  font-size: 0.85rem;
  color: #334155;
  cursor: pointer;
  touch-action: manipulation;
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
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 6px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  font-size: 0.85rem;
  color: #334155;
  cursor: pointer;
  touch-action: manipulation;
`;

const Meta = styled.div`
  font-size: 0.8rem;
  color: #475569;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 4px;
  overflow-wrap: anywhere;
`;

const DetailList = styled.ul`
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: grid;
  gap: 6px;
  max-height: 200px;
  overflow: auto;
  font-size: 0.85rem;
  color: #334155;
`;

const DetailItem = styled.li`
  padding: 8px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  overflow-wrap: anywhere;
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

function enumLabel(value) {
  return String(value || '').replaceAll('_', ' ');
}

function isPitchedTargetTrack(track) {
  if (!track || typeof track !== 'object') {
    return false;
  }
  if (track.is_drum) {
    return false;
  }
  const role = String(track.role || '').toLowerCase();
  if (role === 'drums' || role === 'percussion') {
    return false;
  }
  return typeof track.id === 'string' && track.id.length > 0;
}

function warningCode(item) {
  if (!item) {
    return 'warning';
  }
  if (typeof item === 'string') {
    return item;
  }
  return item.code || item.message || 'warning';
}

function warningMessage(item) {
  if (!item || typeof item === 'string') {
    return null;
  }
  return item.message || null;
}

/**
 * Harmony tab: editable silent chord timeline + reharmonize preview/apply.
 */
const HarmonyTimelinePanel = () => {
  const composition = useMusicStore((state) => state.editedMusicJson);
  const compositionRevision = useMusicStore((state) => state.compositionRevision);
  const harmonySelectionStartBar = useMusicStore((state) => state.harmonySelectionStartBar);
  const harmonySelectionEndBar = useMusicStore((state) => state.harmonySelectionEndBar);
  const harmonySelectedSpanStartTick = useMusicStore((state) => state.harmonySelectedSpanStartTick);
  const reharmonizeStatus = useMusicStore((state) => state.reharmonizeStatus);
  const reharmonizeError = useMusicStore((state) => state.reharmonizeError);
  const reharmonizeWarnings = useMusicStore((state) => state.reharmonizeWarnings);
  const reharmonizeCandidate = useMusicStore((state) => state.reharmonizeCandidate);
  const reharmonizeAuditionActive = useMusicStore((state) => state.reharmonizeAuditionActive);
  const reharmonizeCompareResult = useMusicStore((state) => state.reharmonizeCompareResult);
  const reharmonizeHarmonyChanges = useMusicStore((state) => state.reharmonizeHarmonyChanges);
  const reharmonizeTrackChanges = useMusicStore((state) => state.reharmonizeTrackChanges);
  const reharmonizePreservation = useMusicStore((state) => state.reharmonizePreservation);
  const reharmonizeCompatibility = useMusicStore((state) => state.reharmonizeCompatibility);
  const reharmonizeProvider = useMusicStore((state) => state.reharmonizeProvider);
  const reharmonizeModel = useMusicStore((state) => state.reharmonizeModel);
  const reharmonizeActiveKey = useMusicStore((state) => state.reharmonizeActiveKey);
  const reharmonizeRecommendedTargetTrackIds = useMusicStore(
    (state) => state.reharmonizeRecommendedTargetTrackIds,
  );
  const reharmonizeOperation = useMusicStore((state) => state.reharmonizeOperation);
  const reharmonizeContentPolicy = useMusicStore((state) => state.reharmonizeContentPolicy);
  const reharmonizeEngine = useMusicStore((state) => state.reharmonizeEngine);
  const reharmonizeInstruction = useMusicStore((state) => state.reharmonizeInstruction);
  const reharmonizeTargetTrackIds = useMusicStore((state) => state.reharmonizeTargetTrackIds);
  const reharmonizeAllowModulation = useMusicStore((state) => state.reharmonizeAllowModulation);
  const reharmonizeTargetKey = useMusicStore((state) => state.reharmonizeTargetKey);
  const reharmonizeTargetChord = useMusicStore((state) => state.reharmonizeTargetChord);
  const reharmonizeBaseRevision = useMusicStore((state) => state.reharmonizeBaseRevision);
  const selectedProvider = useMusicStore((state) => state.selectedProvider);
  const selectedModel = useMusicStore((state) => state.selectedModel);
  const currentProjectId = useMusicStore((state) => state.currentProjectId);

  const [barStartDraft, setBarStartDraft] = useState('');
  const [barEndDraft, setBarEndDraft] = useState('');
  const [chordDraft, setChordDraft] = useState('');
  const [moveTickDraft, setMoveTickDraft] = useState('');
  const [resizeEdge, setResizeEdge] = useState('end');
  const [resizeTickDraft, setResizeTickDraft] = useState('');
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [confirmClearReplace, setConfirmClearReplace] = useState(false);
  const [confirmApplyEvents, setConfirmApplyEvents] = useState(false);
  const [localError, setLocalError] = useState('');
  const [branchNameDraft, setBranchNameDraft] = useState('');
  const [applyBusy, setApplyBusy] = useState(false);

  const scrollRef = useRef(null);
  const dragRef = useRef(null);

  const validation = useMemo(
    () => (composition ? validateMusicJson(composition) : { valid: false, message: 'No composition' }),
    [composition],
  );
  const canonical = isCanonicalComposition(composition);
  const timeline = useMemo(
    () => (composition && canonical && validation.valid ? compileTimeline(composition) : null),
    [composition, canonical, validation.valid],
  );

  const harmonySpans = useMemo(
    () => (Array.isArray(composition?.harmony) ? composition.harmony : []),
    [composition],
  );

  const pitchedTracks = useMemo(
    () => (composition?.tracks || []).filter(isPitchedTargetTrack),
    [composition],
  );

  const selectedSpan = useMemo(() => {
    if (harmonySelectedSpanStartTick == null) {
      return null;
    }
    return harmonySpans.find((span) => span.start_tick === harmonySelectedSpanStartTick) || null;
  }, [harmonySpans, harmonySelectedSpanStartTick]);

  const selectionRange = useMemo(() => {
    if (!composition || !harmonySelectionStartBar || !harmonySelectionEndBar) {
      return null;
    }
    try {
      return selectionTicksFromBars(composition, harmonySelectionStartBar, harmonySelectionEndBar);
    } catch {
      return null;
    }
  }, [composition, harmonySelectionStartBar, harmonySelectionEndBar]);

  const timelineWidth = timeline
    ? Math.max(320, timeline.durationTicks * PIXELS_PER_TICK)
    : 320;

  const selectionRect = useMemo(() => {
    if (!timeline || !harmonySelectionStartBar || !harmonySelectionEndBar) {
      return null;
    }
    return selectionOverlayRect(harmonySelectionStartBar, harmonySelectionEndBar, {
      pixelsPerTick: PIXELS_PER_TICK,
      totalHeight: TIMELINE_HEIGHT,
      barBoundaries: timeline.barBoundaries,
    });
  }, [timeline, harmonySelectionStartBar, harmonySelectionEndBar]);

  const gapRegions = useMemo(() => {
    if (!timeline) {
      return [];
    }
    const ordered = [...harmonySpans]
      .map((span) => ({
        start: Number(span.start_tick),
        end: Number(span.start_tick) + Number(span.duration_ticks),
      }))
      .filter((span) => Number.isFinite(span.start) && Number.isFinite(span.end) && span.end > span.start)
      .sort((a, b) => a.start - b.start);
    const gaps = [];
    let cursor = 0;
    for (const span of ordered) {
      if (span.start > cursor) {
        gaps.push({ start: cursor, end: span.start });
      }
      cursor = Math.max(cursor, span.end);
    }
    if (cursor < timeline.durationTicks) {
      gaps.push({ start: cursor, end: timeline.durationTicks });
    }
    return gaps;
  }, [harmonySpans, timeline]);

  const trackEventsChanged = useMemo(
    () => (reharmonizeTrackChanges || []).some((item) => Number(item.events_changed) > 0),
    [reharmonizeTrackChanges],
  );

  const applyDisabledReason = useMemo(() => {
    if (reharmonizeStatus !== 'ready') {
      return 'Preview is not ready';
    }
    if (compositionRevision !== reharmonizeBaseRevision) {
      return 'Composition changed since preview; request a new preview';
    }
    if (!reharmonizeCandidate) {
      return 'No candidate composition';
    }
    return '';
  }, [
    reharmonizeStatus,
    compositionRevision,
    reharmonizeBaseRevision,
    reharmonizeCandidate,
  ]);

  const previewBusy = reharmonizeStatus === 'loading';

  useEffect(() => {
    setBarStartDraft(
      harmonySelectionStartBar != null ? String(harmonySelectionStartBar) : '',
    );
    setBarEndDraft(
      harmonySelectionEndBar != null ? String(harmonySelectionEndBar) : '',
    );
  }, [harmonySelectionStartBar, harmonySelectionEndBar]);

  useEffect(() => {
    setChordDraft(selectedSpan?.chord || '');
    if (selectedSpan) {
      setMoveTickDraft(String(selectedSpan.start_tick));
      setResizeTickDraft(String(selectedSpan.start_tick + selectedSpan.duration_ticks));
      setResizeEdge('end');
    }
    setConfirmRemove(false);
    setConfirmClearReplace(false);
    setLocalError('');
  }, [selectedSpan]);

  useEffect(() => {
    setConfirmApplyEvents(false);
  }, [reharmonizeStatus, reharmonizeBaseRevision, compositionRevision]);

  const applyBarSelection = (startBar, endBar) => {
    const ok = useMusicStore.getState().setHarmonySelection(startBar, endBar);
    if (ok) {
      console.info('[HarmonyTimelinePanel] Bar selection set', { startBar, endBar });
    } else {
      console.warn('[HarmonyTimelinePanel] Invalid bar selection', { startBar, endBar });
    }
    return ok;
  };

  const barFromPointerEvent = (event) => {
    if (!timeline || !scrollRef.current) {
      return null;
    }
    const bounds = scrollRef.current.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const mapped = pointerXToBarFromTimeline(x, {
      timeline,
      pixelsPerTick: PIXELS_PER_TICK,
      scrollLeft: scrollRef.current.scrollLeft,
    });
    return mapped.bar;
  };

  const onTimelinePointerDown = (event) => {
    if (!timeline || event.button !== 0) {
      return;
    }
    if (event.target?.closest?.('[data-harmony-span="true"]')) {
      return;
    }
    const bar = barFromPointerEvent(event);
    if (!bar) {
      return;
    }
    dragRef.current = { anchor: bar, latest: bar };
    applyBarSelection(bar, bar);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const onTimelinePointerMove = (event) => {
    if (!dragRef.current) {
      return;
    }
    const bar = barFromPointerEvent(event);
    if (!bar) {
      return;
    }
    dragRef.current.latest = bar;
    const start = Math.min(dragRef.current.anchor, bar);
    const end = Math.max(dragRef.current.anchor, bar);
    applyBarSelection(start, end);
  };

  const onTimelinePointerUp = (event) => {
    if (dragRef.current) {
      console.debug('[HarmonyTimelinePanel] Pointer selection finished', {
        startBar: Math.min(dragRef.current.anchor, dragRef.current.latest),
        endBar: Math.max(dragRef.current.anchor, dragRef.current.latest),
      });
    }
    dragRef.current = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  const onCommitBarInputs = () => {
    const start = Number.parseInt(barStartDraft, 10);
    const end = Number.parseInt(barEndDraft, 10);
    if (!timeline || start < 1 || end < start || end > timeline.barCount) {
      setLocalError(`Enter inclusive bars between 1 and ${timeline?.barCount || '?'}`);
      console.warn('[HarmonyTimelinePanel] Bar input rejected', {
        start,
        end,
        barCount: timeline?.barCount ?? null,
      });
      return;
    }
    setLocalError('');
    applyBarSelection(start, end);
  };

  const onSelectBars912 = () => {
    applyBarSelection(9, 12);
  };

  const onSelectSpan = (span) => {
    useMusicStore.getState().setHarmonySelectedSpan(span.start_tick);
    const bar = inferredBarLabelForSpan(composition, span);
    console.debug('[HarmonyTimelinePanel] Span selected', {
      startTick: span.start_tick,
      durationTicks: span.duration_ticks,
      bar,
    });
  };

  const onAddSpan = () => {
    const chord = chordDraft.trim();
    if (!selectionRange || !chord) {
      setLocalError('Select bars and enter a chord to add into that range');
      return;
    }
    const ok = useMusicStore.getState().addHarmonySpan({
      start_tick: selectionRange.startTick,
      duration_ticks: selectionRange.endTick - selectionRange.startTick,
      chord,
    });
    if (!ok) {
      setLocalError('Add failed — range may overlap an existing span');
      console.warn('[HarmonyTimelinePanel] Add span failed', {
        startTick: selectionRange.startTick,
        durationTicks: selectionRange.endTick - selectionRange.startTick,
      });
      return;
    }
    setLocalError('');
    console.info('[HarmonyTimelinePanel] Harmony span added', {
      startTick: selectionRange.startTick,
      durationTicks: selectionRange.endTick - selectionRange.startTick,
    });
  };

  const onReplaceSelection = () => {
    if (!selectionRange) {
      setLocalError('Select a bar range to replace');
      return;
    }
    const chord = chordDraft.trim();
    const spans = chord
      ? [{
        start_tick: selectionRange.startTick,
        duration_ticks: selectionRange.endTick - selectionRange.startTick,
        chord,
      }]
      : [];
    if (spans.length === 0 && !confirmClearReplace) {
      setConfirmClearReplace(true);
      console.warn('[HarmonyTimelinePanel] Replace-clear confirmation required', {
        startBar: harmonySelectionStartBar,
        endBar: harmonySelectionEndBar,
      });
      return;
    }
    setConfirmClearReplace(false);
    const ok = useMusicStore.getState().replaceHarmonyRange({
      start_tick: selectionRange.startTick,
      duration_ticks: selectionRange.endTick - selectionRange.startTick,
      spans,
    });
    if (!ok) {
      setLocalError('Replace failed');
      console.warn('[HarmonyTimelinePanel] Replace failed', {
        startTick: selectionRange.startTick,
        spanCount: spans.length,
      });
      return;
    }
    setLocalError('');
    console.info('[HarmonyTimelinePanel] Harmony range replaced', {
      startTick: selectionRange.startTick,
      durationTicks: selectionRange.endTick - selectionRange.startTick,
      spanCount: spans.length,
    });
  };

  const onRemoveSelection = () => {
    if (!selectionRange) {
      setLocalError('Select a bar range to remove');
      return;
    }
    if (!confirmRemove) {
      setConfirmRemove(true);
      console.warn('[HarmonyTimelinePanel] Remove confirmation required', {
        startBar: harmonySelectionStartBar,
        endBar: harmonySelectionEndBar,
      });
      return;
    }
    setConfirmRemove(false);
    const ok = useMusicStore.getState().removeHarmonyRange({
      start_tick: selectionRange.startTick,
      duration_ticks: selectionRange.endTick - selectionRange.startTick,
    });
    if (!ok) {
      setLocalError('Remove failed');
      console.warn('[HarmonyTimelinePanel] Remove failed', {
        startTick: selectionRange.startTick,
      });
      return;
    }
    setLocalError('');
    console.info('[HarmonyTimelinePanel] Harmony range removed', {
      startTick: selectionRange.startTick,
      durationTicks: selectionRange.endTick - selectionRange.startTick,
    });
  };

  const onMoveSelected = () => {
    if (!selectedSpan) {
      setLocalError('Select a span to move');
      return;
    }
    const newStart = Number.parseInt(moveTickDraft, 10);
    if (!Number.isInteger(newStart) || newStart < 0) {
      setLocalError('Move requires a non-negative integer start tick');
      return;
    }
    const ok = useMusicStore.getState().moveHarmonySpan({
      source_start_tick: selectedSpan.start_tick,
      new_start_tick: newStart,
    });
    if (!ok) {
      setLocalError('Move failed — check bounds and overlaps');
      console.warn('[HarmonyTimelinePanel] Move failed', {
        sourceStartTick: selectedSpan.start_tick,
        newStartTick: newStart,
      });
      return;
    }
    setLocalError('');
    useMusicStore.getState().setHarmonySelectedSpan(newStart);
    console.info('[HarmonyTimelinePanel] Harmony span moved', {
      sourceStartTick: selectedSpan.start_tick,
      newStartTick: newStart,
    });
  };

  const onResizeSelected = () => {
    if (!selectedSpan) {
      setLocalError('Select a span to resize');
      return;
    }
    const newTick = Number.parseInt(resizeTickDraft, 10);
    if (!Number.isInteger(newTick) || newTick < 0) {
      setLocalError('Resize requires a non-negative integer tick');
      return;
    }
    const ok = useMusicStore.getState().resizeHarmonySpan({
      source_start_tick: selectedSpan.start_tick,
      edge: resizeEdge,
      new_tick: newTick,
    });
    if (!ok) {
      setLocalError('Resize failed — check edge tick and overlaps');
      console.warn('[HarmonyTimelinePanel] Resize failed', {
        sourceStartTick: selectedSpan.start_tick,
        edge: resizeEdge,
        newTick,
      });
      return;
    }
    setLocalError('');
    const nextStart = resizeEdge === 'start' ? newTick : selectedSpan.start_tick;
    useMusicStore.getState().setHarmonySelectedSpan(nextStart);
    console.info('[HarmonyTimelinePanel] Harmony span resized', {
      sourceStartTick: selectedSpan.start_tick,
      edge: resizeEdge,
      newTick,
    });
  };

  const onToggleTargetTrack = (trackId) => {
    const current = reharmonizeTargetTrackIds || [];
    const next = current.includes(trackId)
      ? current.filter((id) => id !== trackId)
      : [...current, trackId];
    useMusicStore.getState().setReharmonizeControls({ targetTrackIds: next });
    console.debug('[HarmonyTimelinePanel] Target tracks updated', { targetCount: next.length });
  };

  const onUseRecommended = () => {
    const ids = (reharmonizeRecommendedTargetTrackIds || []).filter(Boolean);
    if (!ids.length) {
      return;
    }
    useMusicStore.getState().setReharmonizeControls({ targetTrackIds: ids });
    console.info('[HarmonyTimelinePanel] Applied recommended target tracks', {
      targetCount: ids.length,
    });
  };

  const onPreview = async () => {
    console.info('[HarmonyTimelinePanel] Preview requested', {
      operation: reharmonizeOperation,
      contentPolicy: reharmonizeContentPolicy,
      engine: reharmonizeEngine,
      startBar: harmonySelectionStartBar,
      endBar: harmonySelectionEndBar,
      targetCount: (reharmonizeTargetTrackIds || []).length,
    });
    await useMusicStore.getState().startReharmonizePreview();
  };

  const onDiscard = () => {
    console.info('[HarmonyTimelinePanel] Preview discarded', {
      status: reharmonizeStatus,
      changeCount: (reharmonizeHarmonyChanges || []).length,
    });
    useMusicStore.getState().discardReharmonizePreview();
    setConfirmApplyEvents(false);
    setBranchNameDraft('');
  };

  const onReject = () => {
    console.info('[FIX:harmony-ui] Reject reharmonize candidate', {
      status: reharmonizeStatus,
      changeCount: (reharmonizeHarmonyChanges || []).length,
    });
    useMusicStore.getState().rejectReharmonizePreview();
    setConfirmApplyEvents(false);
    setBranchNameDraft('');
  };

  const onCompare = () => {
    if (!reharmonizeCandidate) {
      return;
    }
    console.debug('[FIX:harmony-ui] Compare reharmonize candidate');
    useMusicStore.getState().refreshReharmonizeComparison();
  };

  const onToggleAudition = () => {
    const next = !reharmonizeAuditionActive;
    console.info('[FIX:harmony-ui] Toggle reharmonize audition', { active: next });
    useMusicStore.getState().setReharmonizeAuditionActive(next);
  };

  const onApply = async ({ asNewBranch = false } = {}) => {
    if (applyDisabledReason || applyBusy) {
      console.debug('[HarmonyTimelinePanel] Apply disabled', { reason: applyDisabledReason });
      return;
    }
    if (asNewBranch && !branchNameDraft.trim()) {
      return;
    }
    if (trackEventsChanged && !confirmApplyEvents) {
      setConfirmApplyEvents(true);
      console.warn('[HarmonyTimelinePanel] Apply confirmation required for event changes', {
        changedTrackCount: (reharmonizeTrackChanges || []).filter(
          (item) => Number(item.events_changed) > 0,
        ).length,
        asNewBranch: Boolean(asNewBranch),
      });
      return;
    }
    setConfirmApplyEvents(false);
    setApplyBusy(true);
    console.info('[FIX:harmony-ui] Applying reharmonize preview', {
      operation: reharmonizeOperation,
      contentPolicy: reharmonizeContentPolicy,
      changeCount: (reharmonizeHarmonyChanges || []).length,
      trackChangeCount: (reharmonizeTrackChanges || []).length,
      asNewBranch: Boolean(asNewBranch),
    });
    try {
      await useMusicStore.getState().applyReharmonizePreview(
        asNewBranch
          ? { asNewBranch: true, branchName: branchNameDraft.trim() }
          : {},
      );
      if (asNewBranch) {
        setBranchNameDraft('');
      }
    } catch {
      // store records reharmonizeError / conflict
    } finally {
      setApplyBusy(false);
    }
  };

  if (!composition) {
    return (
      <Panel data-testid="harmony-panel">
        <Title>Harmony</Title>
        <Hint>Generate or import a composition to edit harmony metadata.</Hint>
      </Panel>
    );
  }

  if (!canonical || !validation.valid) {
    return (
      <Panel data-testid="harmony-panel">
        <Title>Harmony</Title>
        <StatusBanner $tone="warn">
          {validation.message || 'Canonical composition.v2 is required for harmony editing.'}
        </StatusBanner>
      </Panel>
    );
  }

  if (!timeline) {
    return (
      <Panel data-testid="harmony-panel">
        <Title>Harmony</Title>
        <StatusBanner $tone="warn">Unable to compile meter map for this composition.</StatusBanner>
      </Panel>
    );
  }

  const candidateHarmony = Array.isArray(reharmonizeCandidate?.harmony)
    ? reharmonizeCandidate.harmony
    : [];

  return (
    <Panel data-testid="harmony-panel">
      <Title>Harmony</Title>
      <Hint>
        Harmony is silent metadata for chord symbols and analysis. Audible notes come only from
        {' '}
        <code>tracks[].events[]</code>
        .
      </Hint>

      <Card>
        <CardTitle>Timeline</CardTitle>
        <TimelineScroll ref={scrollRef} data-testid="harmony-timeline">
          <TimelineInner
            style={{ width: timelineWidth }}
            onPointerDown={onTimelinePointerDown}
            onPointerMove={onTimelinePointerMove}
            onPointerUp={onTimelinePointerUp}
            onPointerCancel={onTimelinePointerUp}
            role="presentation"
          >
            <Ruler style={{ width: timelineWidth }} aria-hidden="true" />
            {Array.from({ length: timeline.barCount }, (_, index) => {
              const bar = index + 1;
              const left = timeline.barBoundaries[index] * PIXELS_PER_TICK;
              return (
                <BarLabel
                  key={`bar-${bar}`}
                  type="button"
                  style={{ left: left + 2 }}
                  aria-label={`Select bar ${bar}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    applyBarSelection(bar, bar);
                  }}
                >
                  {bar}
                </BarLabel>
              );
            })}
            <Lane style={{ width: timelineWidth }} />
            {selectionRect ? (
              <SelectionOverlay
                style={{ left: selectionRect.left, width: Math.max(2, selectionRect.width) }}
                aria-hidden="true"
              />
            ) : null}
            {gapRegions.map((gap) => {
              const width = (gap.end - gap.start) * PIXELS_PER_TICK;
              if (width < 8) {
                return null;
              }
              return (
                <GapBlock
                  key={`gap-${gap.start}-${gap.end}`}
                  style={{ left: gap.start * PIXELS_PER_TICK, width }}
                >
                  gap
                </GapBlock>
              );
            })}
            {harmonySpans.map((span) => {
              const left = Number(span.start_tick) * PIXELS_PER_TICK;
              const width = Math.max(24, Number(span.duration_ticks) * PIXELS_PER_TICK);
              const active = span.start_tick === harmonySelectedSpanStartTick;
              const bar = inferredBarLabelForSpan(composition, span);
              return (
                <SpanBlock
                  key={`span-${span.start_tick}-${span.duration_ticks}-${span.chord}`}
                  type="button"
                  data-harmony-span="true"
                  $active={active}
                  style={{ left, width }}
                  aria-pressed={active}
                  aria-label={`Harmony span ${span.chord} starting bar ${bar ?? '?'}`}
                  title={`${span.chord} · tick ${span.start_tick}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectSpan(span);
                  }}
                >
                  {span.chord}
                </SpanBlock>
              );
            })}
          </TimelineInner>
        </TimelineScroll>

        <Meta>
          <span>
            Selection:
            {' '}
            {harmonySelectionStartBar && harmonySelectionEndBar
              ? `bars ${harmonySelectionStartBar}–${harmonySelectionEndBar}`
              : 'none'}
          </span>
          <span>
            Spans:
            {' '}
            {harmonySpans.length}
          </span>
          <span>
            Bars:
            {' '}
            {timeline.barCount}
          </span>
        </Meta>

        <ButtonRow>
          <Field style={{ marginBottom: 0, flex: '1 1 100px' }}>
            Start bar
            <Input
              type="number"
              min={1}
              max={timeline.barCount}
              value={barStartDraft}
              data-testid="harmony-bar-start"
              aria-label="Harmony selection start bar"
              onChange={(event) => setBarStartDraft(event.target.value)}
              onBlur={onCommitBarInputs}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  onCommitBarInputs();
                }
              }}
            />
          </Field>
          <Field style={{ marginBottom: 0, flex: '1 1 100px' }}>
            End bar
            <Input
              type="number"
              min={1}
              max={timeline.barCount}
              value={barEndDraft}
              data-testid="harmony-bar-end"
              aria-label="Harmony selection end bar"
              onChange={(event) => setBarEndDraft(event.target.value)}
              onBlur={onCommitBarInputs}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  onCommitBarInputs();
                }
              }}
            />
          </Field>
          {timeline.barCount >= 12 ? (
            <Button
              type="button"
              $secondary
              data-testid="harmony-select-9-12"
              onClick={onSelectBars912}
            >
              Select bars 9–12
            </Button>
          ) : null}
        </ButtonRow>
      </Card>

      <Grid>
        <Card>
          <CardTitle>Local edit</CardTitle>
          <Field>
            Chord
            <Input
              value={chordDraft}
              data-testid="harmony-chord-input"
              aria-label="Harmony chord symbol"
              placeholder="e.g. Dm7"
              maxLength={32}
              onChange={(event) => setChordDraft(event.target.value)}
            />
          </Field>
          <ButtonRow>
            <Button type="button" onClick={onAddSpan} disabled={!selectionRange || !chordDraft.trim()}>
              Add into selection
            </Button>
            <Button type="button" $secondary onClick={onReplaceSelection} disabled={!selectionRange}>
              {confirmClearReplace && !chordDraft.trim()
                ? 'Confirm clear replace'
                : 'Replace selection'}
            </Button>
            <Button type="button" $secondary onClick={onRemoveSelection} disabled={!selectionRange}>
              {confirmRemove ? 'Confirm remove' : 'Remove selection'}
            </Button>
          </ButtonRow>

          <Field style={{ marginTop: 12 }}>
            Move selected span to start tick
            <Input
              type="number"
              min={0}
              value={moveTickDraft}
              aria-label="New start tick for selected harmony span"
              disabled={!selectedSpan}
              onChange={(event) => setMoveTickDraft(event.target.value)}
            />
          </Field>
          <ButtonRow>
            <Button type="button" $secondary onClick={onMoveSelected} disabled={!selectedSpan}>
              Move selected
            </Button>
          </ButtonRow>

          <Field>
            Resize edge
            <Select
              value={resizeEdge}
              aria-label="Resize edge"
              disabled={!selectedSpan}
              onChange={(event) => setResizeEdge(event.target.value)}
            >
              <option value="start">start</option>
              <option value="end">end</option>
            </Select>
          </Field>
          <Field>
            New edge tick
            <Input
              type="number"
              min={0}
              value={resizeTickDraft}
              aria-label="New tick for resized harmony edge"
              disabled={!selectedSpan}
              onChange={(event) => setResizeTickDraft(event.target.value)}
            />
          </Field>
          <ButtonRow>
            <Button type="button" $secondary onClick={onResizeSelected} disabled={!selectedSpan}>
              Resize selected
            </Button>
          </ButtonRow>

          {localError ? <StatusBanner $tone="warn">{localError}</StatusBanner> : null}
          {selectedSpan ? (
            <Meta>
              <span>
                Selected:
                {' '}
                {selectedSpan.chord}
              </span>
              <span>
                tick
                {' '}
                {selectedSpan.start_tick}
              </span>
              <span>
                dur
                {' '}
                {selectedSpan.duration_ticks}
              </span>
            </Meta>
          ) : (
            <Hint style={{ marginTop: 8 }}>Click a chord block to select it for move/resize.</Hint>
          )}
        </Card>

        <Card>
          <CardTitle>Reharmonize</CardTitle>
          <Field>
            Operation
            <Select
              value={reharmonizeOperation}
              data-testid="harmony-operation"
              aria-label="Reharmonize operation"
              onChange={(event) => {
                useMusicStore.getState().setReharmonizeControls({ operation: event.target.value });
              }}
            >
              {REHARMONIZE_OPERATIONS.map((operation) => (
                <option key={operation} value={operation}>{enumLabel(operation)}</option>
              ))}
            </Select>
          </Field>

          <RadioGroup data-testid="harmony-content-policy">
            <RadioLegend>Content policy</RadioLegend>
            {REHARMONIZE_CONTENT_POLICIES.map((policy) => (
              <RadioOption key={policy} $active={reharmonizeContentPolicy === policy}>
                <input
                  type="radio"
                  name="harmony-content-policy"
                  value={policy}
                  checked={reharmonizeContentPolicy === policy}
                  onChange={() => {
                    useMusicStore.getState().setReharmonizeControls({ contentPolicy: policy });
                  }}
                />
                {enumLabel(policy)}
              </RadioOption>
            ))}
          </RadioGroup>

          <Field>
            Engine
            <Select
              value={reharmonizeEngine}
              data-testid="harmony-engine"
              aria-label="Reharmonize engine"
              onChange={(event) => {
                useMusicStore.getState().setReharmonizeControls({ engine: event.target.value });
              }}
            >
              {REHARMONIZE_ENGINES.map((engine) => (
                <option key={engine} value={engine}>{engine}</option>
              ))}
            </Select>
          </Field>

          {reharmonizeEngine === 'ai' ? (
            <Meta>
              <span>
                Provider:
                {' '}
                {selectedProvider || '—'}
              </span>
              <span>
                Model:
                {' '}
                {selectedModel || '—'}
              </span>
            </Meta>
          ) : null}

          <Field>
            Instruction (optional, max 500)
            <TextArea
              value={reharmonizeInstruction}
              maxLength={500}
              aria-label="Reharmonize instruction"
              placeholder="Optional guidance for the engine"
              onChange={(event) => {
                useMusicStore.getState().setReharmonizeControls({
                  instruction: event.target.value.slice(0, 500),
                });
              }}
            />
          </Field>

          <CardTitle style={{ marginTop: 8 }}>Target tracks</CardTitle>
          <CheckList data-testid="harmony-target-tracks" aria-label="Reharmonize target tracks">
            {pitchedTracks.length === 0 ? (
              <Hint>No pitched tracks available.</Hint>
            ) : (
              pitchedTracks.map((track) => {
                const checked = (reharmonizeTargetTrackIds || []).includes(track.id);
                return (
                  <CheckOption key={track.id}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggleTargetTrack(track.id)}
                    />
                    <span>
                      {track.name || track.id}
                      {' '}
                      (
                      {track.role || 'unspecified'}
                      )
                    </span>
                  </CheckOption>
                );
              })
            )}
          </CheckList>
          {(reharmonizeRecommendedTargetTrackIds || []).length > 0 ? (
            <ButtonRow>
              <Button type="button" $secondary onClick={onUseRecommended}>
                Use recommended
              </Button>
            </ButtonRow>
          ) : null}

          <InlineCheck>
            <input
              type="checkbox"
              checked={Boolean(reharmonizeAllowModulation)}
              onChange={(event) => {
                useMusicStore.getState().setReharmonizeControls({
                  allowModulation: event.target.checked,
                });
              }}
            />
            Allow modulation
          </InlineCheck>
          {reharmonizeAllowModulation ? (
            <Field>
              Target key
              <Input
                value={reharmonizeTargetKey}
                aria-label="Target key for modulation"
                placeholder="e.g. A minor"
                onChange={(event) => {
                  useMusicStore.getState().setReharmonizeControls({
                    targetKey: event.target.value,
                  });
                }}
              />
            </Field>
          ) : null}
          <Field>
            Target chord
            <Input
              value={reharmonizeTargetChord}
              aria-label="Target chord"
              placeholder="Optional tonicization target"
              maxLength={32}
              onChange={(event) => {
                useMusicStore.getState().setReharmonizeControls({
                  targetChord: event.target.value,
                });
              }}
            />
          </Field>

          <ButtonRow>
            <Button
              type="button"
              data-testid="harmony-preview-btn"
              disabled={previewBusy || !harmonySelectionStartBar || !harmonySelectionEndBar}
              onClick={onPreview}
            >
              {previewBusy ? 'Previewing…' : 'Preview reharmonize'}
            </Button>
          </ButtonRow>
        </Card>
      </Grid>

      <Card>
        <CardTitle>Preview</CardTitle>
        <StatusBanner
          data-testid="harmony-preview-status"
          $tone={
            reharmonizeStatus === 'error' || reharmonizeStatus === 'stale'
              ? 'error'
              : reharmonizeStatus === 'ready'
                ? 'ok'
                : 'info'
          }
          role="status"
          aria-live="polite"
        >
          Status:
          {' '}
          {reharmonizeStatus}
          {reharmonizeError ? ` — ${reharmonizeError}` : ''}
          {reharmonizeActiveKey ? ` · active key ${reharmonizeActiveKey}` : ''}
          {reharmonizeProvider || reharmonizeModel
            ? ` · ${[reharmonizeProvider, reharmonizeModel].filter(Boolean).join(' / ')}`
            : ''}
        </StatusBanner>

        {(reharmonizeWarnings || []).length > 0 ? (
          <DetailList aria-label="Reharmonize warnings">
            {reharmonizeWarnings.map((item, index) => {
              const code = warningCode(item);
              const message = warningMessage(item);
              return (
                <DetailItem key={`warn-${code}-${index}`}>
                  <strong>{code}</strong>
                  {message ? ` — ${message}` : ''}
                </DetailItem>
              );
            })}
          </DetailList>
        ) : null}

        {(reharmonizeHarmonyChanges || []).length > 0 ? (
          <>
            <Meta>
              <span>
                Harmony changes:
                {' '}
                {reharmonizeHarmonyChanges.length}
              </span>
            </Meta>
            <DetailList aria-label="Harmony changes">
              {reharmonizeHarmonyChanges.slice(0, 40).map((change, index) => (
                <DetailItem key={`hc-${change.start_tick}-${index}`}>
                  tick
                  {' '}
                  {change.start_tick}
                  {' '}
                  (
                  {change.duration_ticks}
                  ):
                  {' '}
                  {change.before_chord || '∅'}
                  {' '}
                  →
                  {' '}
                  {change.after_chord || '∅'}
                </DetailItem>
              ))}
            </DetailList>
          </>
        ) : null}

        {(reharmonizeTrackChanges || []).length > 0 ? (
          <DetailList aria-label="Track changes">
            {reharmonizeTrackChanges.map((change) => (
              <DetailItem key={`tc-${change.track_id}`}>
                {change.track_id}
                {' '}
                (
                {change.role}
                ): events
                {' '}
                {change.events_before}
                →
                {change.events_after}
                {' '}
                (
                {change.events_changed}
                {' '}
                changed)
              </DetailItem>
            ))}
          </DetailList>
        ) : null}

        {(reharmonizePreservation || []).length > 0 ? (
          <DetailList aria-label="Preservation assertions">
            {reharmonizePreservation.map((item, index) => (
              <DetailItem key={`pres-${item.kind}-${index}`}>
                {item.satisfied ? '✓' : '✗'}
                {' '}
                {item.kind}
                {item.track_id ? ` · ${item.track_id}` : ''}
                {' '}
                —
                {' '}
                {item.detail}
              </DetailItem>
            ))}
          </DetailList>
        ) : null}

        {reharmonizeCompatibility ? (
          <>
            <Meta>
              <span>
                Compatibility:
                {' '}
                {reharmonizeCompatibility.status}
              </span>
            </Meta>
            <DetailList aria-label="Compatibility findings">
              {(reharmonizeCompatibility.findings || []).slice(0, 40).map((finding, index) => (
                <DetailItem key={`cf-${finding.code}-${index}`}>
                  <strong>{finding.code}</strong>
                  {' '}
                  (
                  {finding.severity}
                  )
                  {finding.message ? ` — ${finding.message}` : ''}
                </DetailItem>
              ))}
            </DetailList>
          </>
        ) : null}

        {reharmonizeStatus === 'ready' && candidateHarmony.length > 0 ? (
          <>
            <CardTitle style={{ marginTop: 12 }}>Candidate harmony (preview only)</CardTitle>
            <Hint>Does not swap the working composition until Apply.</Hint>
            <DetailList aria-label="Candidate harmony spans">
              {candidateHarmony.slice(0, 48).map((span) => (
                <DetailItem key={`cand-${span.start_tick}-${span.duration_ticks}-${span.chord}`}>
                  {span.chord}
                  {' '}
                  · tick
                  {' '}
                  {span.start_tick}
                  {' '}
                  · dur
                  {' '}
                  {span.duration_ticks}
                </DetailItem>
              ))}
            </DetailList>
          </>
        ) : null}

        {confirmApplyEvents ? (
          <StatusBanner $tone="warn">
            This preview changes note events on one or more tracks. Confirm Apply to continue.
          </StatusBanner>
        ) : null}

        <ButtonRow>
          <Button
            type="button"
            $secondary
            data-testid="harmony-audition-btn"
            disabled={!reharmonizeCandidate || applyBusy}
            aria-pressed={reharmonizeAuditionActive}
            onClick={onToggleAudition}
          >
            {reharmonizeAuditionActive ? 'Stop audition' : 'Audition candidate'}
          </Button>
          <Button
            type="button"
            $secondary
            data-testid="harmony-compare-btn"
            disabled={!reharmonizeCandidate || applyBusy}
            onClick={onCompare}
          >
            Compare
          </Button>
          <Button
            type="button"
            data-testid="harmony-apply-btn"
            disabled={Boolean(applyDisabledReason) || applyBusy}
            title={applyDisabledReason || ''}
            onClick={() => onApply()}
          >
            {confirmApplyEvents ? 'Confirm apply' : 'Apply'}
          </Button>
          <Button
            type="button"
            $secondary
            data-testid="harmony-reject-btn"
            disabled={!reharmonizeCandidate || applyBusy}
            onClick={onReject}
          >
            Reject
          </Button>
          <Button
            type="button"
            $secondary
            data-testid="harmony-discard-btn"
            disabled={(reharmonizeStatus === 'idle' && !reharmonizeCandidate) || applyBusy}
            onClick={onDiscard}
          >
            Discard
          </Button>
        </ButtonRow>
        {reharmonizeCompareResult ? (
          <Hint data-testid="harmony-compare-summary">
            Compare vs working:{' '}
            {reharmonizeCompareResult.identical ? 'identical' : 'differences'}
            {' · '}
            +{reharmonizeCompareResult.events?.added || 0}
            {' / -'}
            {reharmonizeCompareResult.events?.removed || 0}
            {' / ~'}
            {reharmonizeCompareResult.events?.changed || 0}
          </Hint>
        ) : null}
        {reharmonizeAuditionActive ? (
          <Hint data-testid="harmony-audition-active">
            Transport plays the reharmonize candidate. Working composition is unchanged until Apply.
          </Hint>
        ) : null}
        {currentProjectId ? (
          <ButtonRow>
            <Input
              aria-label="Apply reharmonize as new branch name"
              data-testid="harmony-branch-name"
              placeholder="New branch name"
              value={branchNameDraft}
              disabled={applyBusy}
              onChange={(event) => setBranchNameDraft(event.target.value)}
              style={{ flex: '1 1 160px', marginBottom: 0 }}
            />
            <Button
              type="button"
              data-testid="harmony-apply-as-branch-btn"
              disabled={Boolean(applyDisabledReason) || applyBusy || !branchNameDraft.trim()}
              title={applyDisabledReason || ''}
              onClick={() => onApply({ asNewBranch: true })}
            >
              Apply as new branch
            </Button>
          </ButtonRow>
        ) : null}
        {applyDisabledReason && reharmonizeStatus !== 'idle' ? (
          <Hint>{applyDisabledReason}</Hint>
        ) : null}
      </Card>
    </Panel>
  );
};

export default HarmonyTimelinePanel;
