import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import {
  SNAP_VALUES,
  buildGridMetrics,
  buildTimelineCues,
  defaultDurationForSnap,
  midiToPitch,
  pitchToMidi,
  pixelToPitchMidi,
  pixelToTick,
  sanitizeNoteSummary,
  snapTick,
} from '../utils/pianoRollEvents.js';
import {
  motifDestinationTickRange,
  normalizeBarRange,
  pointerXToBar,
  selectionOverlayRect,
} from '../utils/pianoRollSelection.js';
import {
  nextMotifLabel,
  resolveMotifOccurrenceBarSpan,
  validateMotifAuthoringSelection,
} from '../utils/compositionMotifs.js';
import {
  collectNotesInBox,
  makeNoteRef,
  noteRefKey,
  selectionSummary,
  selectionTickRange,
} from '../utils/compositionEditorSelection.js';
import {
  SHORTCUT_COMMAND,
  dispatchShortcutEvent,
} from '../utils/editorShortcuts.js';
import {
  DEFAULT_TICK_BUFFER,
  anchorZoom,
  computeVisiblePitchRange,
  computeVisibleTickRange,
  createThrottledFn,
  filterNotesInViewport,
} from '../utils/pianoRollViewport.js';
import {
  listSectionsForNavigation,
  scrollLeftForCenterTick,
  tickToBar,
} from '../utils/editorNavigation.js';
import { secondsToPlaybackPosition } from '../utils/playbackPosition.js';
import { createAppLogger } from '../utils/appLogger.js';
import PianoRollNoteLayer from './piano-roll/PianoRollNoteLayer.jsx';
import PianoRollOverlayLayer from './piano-roll/PianoRollOverlayLayer.jsx';
import PianoRollSelectionInspector from './piano-roll/PianoRollSelectionInspector.jsx';
import PianoRollTrackControls from './piano-roll/PianoRollTrackControls.jsx';
import { usePianoRollDrag } from './piano-roll/usePianoRollDrag.js';

const logger = createAppLogger('pianoRoll');
const logViewportThrottled = createThrottledFn(500)((meta) => {
  logger.debug('Viewport cull', meta);
});

/**
 * Piano-roll empty-grid gestures (Task 5 / 7):
 * - Shift+drag → AI bar-range selection (existing)
 * - Alt+drag or Meta+drag → note box select across visible (non-hidden) tracks
 * - Ctrl/Cmd+click → place edit cursor without creating a note
 * - Click timeline ruler → place edit cursor
 * - Plain click → create note on the active track
 * - Ctrl/Cmd+wheel → zoom around pointer
 */

const CONTEXT_COLORS = ['#94a3b8', '#a78bfa', '#67e8f9', '#fbbf24', '#f472b6', '#86efac'];
const NOTE_NOTATION_DEBOUNCE_MS = 450;
const BAR_SELECTION_LOG_THROTTLE_MS = 250;
const CONTEXT_TRACK_OPACITY = 0.35;

const Panel = styled.section`
  margin: 20px 0;
  padding: 16px;
  background: #ffffff;
  border: 1px solid #e0e7ff;
  border-radius: 12px;
`;

const HeaderRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
`;

const Title = styled.h4`
  margin: 0;
  color: #312e81;
`;

const Controls = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const ControlGroup = styled.label`
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  color: #374151;
`;

const Select = styled.select`
  padding: 6px 8px;
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  background: white;
  font-size: 0.9rem;
`;

const Button = styled.button`
  padding: 6px 10px;
  border: none;
  border-radius: 6px;
  background: ${(props) => (props.$danger ? '#ef4444' : '#4f46e5')};
  color: white;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;

  &:disabled {
    background: #d1d5db;
    cursor: not-allowed;
  }
`;

const Status = styled.div`
  margin: 8px 0 12px;
  font-size: 0.85rem;
  color: ${(props) => {
    if (props.$tone === 'error') return '#991b1b';
    if (props.$tone === 'warn') return '#92400e';
    return '#1e3a8a';
  }};
`;

const EditorShell = styled.div`
  display: grid;
  grid-template-columns: 56px 1fr;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  overflow: hidden;
  background: #f8fafc;
  min-height: 240px;
  max-height: 420px;

  &:focus {
    outline: 2px solid #6366f1;
    outline-offset: 2px;
  }

  @media (max-width: 480px) {
    max-height: 320px;
  }
`;

const PitchGutter = styled.div`
  overflow: hidden;
  border-right: 1px solid #c7d2fe;
  background: #eef2ff;
`;

const PitchLabel = styled.div`
  height: ${(props) => props.$height}px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.65rem;
  color: ${(props) => (props.$blackKey ? '#64748b' : '#312e81')};
  background: ${(props) => (props.$blackKey ? '#e2e8f0' : 'transparent')};
  border-bottom: 1px solid #e2e8f0;
`;

const ScrollArea = styled.div`
  overflow: auto;
  position: relative;
  -webkit-overflow-scrolling: touch;
`;

const GridCanvas = styled.div`
  position: relative;
  width: ${(props) => props.$width}px;
  height: ${(props) => props.$height}px;
  background-image:
    linear-gradient(to right, rgba(99, 102, 241, 0.35) 1px, transparent 1px),
    linear-gradient(to right, rgba(148, 163, 184, 0.35) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(148, 163, 184, 0.45) 1px, transparent 1px);
  background-size:
    ${(props) => props.$barWidth}px 100%,
    ${(props) => props.$beatWidth}px 100%,
    100% ${(props) => props.$rowHeight}px;
  background-position: 0 0, 0 0, 0 0;
  cursor: crosshair;
  touch-action: none;
`;

const BarLabel = styled.div`
  position: absolute;
  top: 2px;
  left: ${(props) => props.$left}px;
  font-size: 0.65rem;
  color: #4338ca;
  pointer-events: none;
  z-index: 2;
`;

const TimelineRuler = styled.div`
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 18px;
  z-index: 8;
  cursor: pointer;
  background: linear-gradient(to bottom, rgba(238, 242, 255, 0.95), rgba(238, 242, 255, 0.35));
  border-bottom: 1px solid rgba(165, 180, 252, 0.55);
`;

const TimelineCue = styled.div`
  position: absolute;
  top: 18px;
  left: ${(props) => props.$left}px;
  max-width: 120px;
  padding: 1px 4px;
  border-radius: 4px;
  font-size: 0.6rem;
  line-height: 1.2;
  color: ${(props) => {
    if (props.$kind === 'tempo') return '#1d4ed8';
    if (props.$kind === 'meter') return '#047857';
    if (props.$kind === 'key') return '#7c3aed';
    if (props.$kind === 'section') return '#b45309';
    return '#334155';
  }};
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.45);
  pointer-events: none;
  z-index: 2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`;

const ExpressionControls = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-bottom: 10px;
`;

const NumberInput = styled.input`
  width: 64px;
  padding: 6px 8px;
  border: 1px solid #c7d2fe;
  border-radius: 6px;
  background: white;
  font-size: 0.9rem;
`;

const PianoRollEditor = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);
  const pianoRollNoteId = useMusicStore((state) => state.pianoRollNoteId);
  const pianoRollNoteIds = useMusicStore((state) => state.pianoRollNoteIds);
  const editorSelectionRefs = useMusicStore((state) => state.editorSelectionRefs);
  const editorClipboard = useMusicStore((state) => state.editorClipboard);
  const editorCommandFeedback = useMusicStore((state) => state.editorCommandFeedback);
  const hiddenTrackIds = useMusicStore((state) => state.hiddenTrackIds);
  const lockedTrackIds = useMusicStore((state) => state.lockedTrackIds);
  const pianoRollSnap = useMusicStore((state) => state.pianoRollSnap);
  const pianoRollZoom = useMusicStore((state) => state.pianoRollZoom);
  const editCursorTick = useMusicStore((state) => state.editCursorTick);
  const viewportScrollRequest = useMusicStore((state) => state.viewportScrollRequest);
  const pianoRollNotationStatus = useMusicStore((state) => state.pianoRollNotationStatus);
  const pianoRollNotationError = useMusicStore((state) => state.pianoRollNotationError);
  const compositionEditUndoStack = useMusicStore((state) => state.compositionEditUndoStack);
  const compositionEditRedoStack = useMusicStore((state) => state.compositionEditRedoStack);
  const notationRevision = useMusicStore((state) => state.notationRevision);
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
  const playbackAutoFollow = useMusicStore((state) => state.playbackAutoFollow);
  const setPlaybackAutoFollow = useMusicStore((state) => state.setPlaybackAutoFollow);
  const togglePlaybackTransport = useMusicStore((state) => state.togglePlaybackTransport);
  const selectPianoRollTrack = useMusicStore((state) => state.selectPianoRollTrack);
  const setEditorSelection = useMusicStore((state) => state.setEditorSelection);
  const toggleEditorSelectionRef = useMusicStore((state) => state.toggleEditorSelectionRef);
  const extendEditorSelectionTo = useMusicStore((state) => state.extendEditorSelectionTo);
  const selectAllVisible = useMusicStore((state) => state.selectAllVisible);
  const clearEditorSelection = useMusicStore((state) => state.clearEditorSelection);
  const copySelection = useMusicStore((state) => state.copySelection);
  const cutSelection = useMusicStore((state) => state.cutSelection);
  const pasteClipboard = useMusicStore((state) => state.pasteClipboard);
  const duplicateSelection = useMusicStore((state) => state.duplicateSelection);
  const deleteSelection = useMusicStore((state) => state.deleteSelection);
  const transposeSelection = useMusicStore((state) => state.transposeSelection);
  const nudgeSelectionBySnap = useMusicStore((state) => state.nudgeSelectionBySnap);
  const setPianoRollSnap = useMusicStore((state) => state.setPianoRollSnap);
  const setPianoRollZoom = useMusicStore((state) => state.setPianoRollZoom);
  const setEditCursorTick = useMusicStore((state) => state.setEditCursorTick);
  const gotoBar = useMusicStore((state) => state.gotoBar);
  const gotoPrevBar = useMusicStore((state) => state.gotoPrevBar);
  const gotoNextBar = useMusicStore((state) => state.gotoNextBar);
  const gotoSection = useMusicStore((state) => state.gotoSection);
  const gotoPrevSection = useMusicStore((state) => state.gotoPrevSection);
  const gotoNextSection = useMusicStore((state) => state.gotoNextSection);
  const zoomIn = useMusicStore((state) => state.zoomIn);
  const zoomOut = useMusicStore((state) => state.zoomOut);
  const zoomToFit = useMusicStore((state) => state.zoomToFit);
  const zoomToSelection = useMusicStore((state) => state.zoomToSelection);
  const createNote = useMusicStore((state) => state.createNote);
  const applyTieChain = useMusicStore((state) => state.applyTieChain);
  const removeTieChain = useMusicStore((state) => state.removeTieChain);
  const undoCompositionEdit = useMusicStore((state) => state.undoCompositionEdit);
  const redoCompositionEdit = useMusicStore((state) => state.redoCompositionEdit);
  const refreshMusicXmlFromEditedComposition = useMusicStore(
    (state) => state.refreshMusicXmlFromEditedComposition,
  );
  const aiEditStartBar = useMusicStore((state) => state.aiEditStartBar);
  const aiEditEndBar = useMusicStore((state) => state.aiEditEndBar);
  const aiEditTrackMode = useMusicStore((state) => state.aiEditTrackMode);
  const setAiEditSelection = useMusicStore((state) => state.setAiEditSelection);
  const clearAiEditSelection = useMusicStore((state) => state.clearAiEditSelection);
  const setAiEditTrackMode = useMusicStore((state) => state.setAiEditTrackMode);
  const motifSelectedMotifId = useMusicStore((state) => state.motifSelectedMotifId);
  const motifSelectedOccurrenceId = useMusicStore((state) => state.motifSelectedOccurrenceId);
  const motifHighlightedUsageKey = useMusicStore((state) => state.motifHighlightedUsageKey);
  const motifDestinationTrackId = useMusicStore((state) => state.motifDestinationTrackId);
  const motifDestinationStartBar = useMusicStore((state) => state.motifDestinationStartBar);
  const markMotifFromSelection = useMusicStore((state) => state.markMotifFromSelection);

  const editorRef = useRef(null);
  const scrollRef = useRef(null);
  const barSelectRef = useRef(null);
  const noteBoxSelectRef = useRef(null);
  const debounceRef = useRef(null);
  const lastBarSelectLogRef = useRef(0);
  const scrollRafRef = useRef(0);
  const zoomAnchorPendingRef = useRef(null);
  const userScrollSuppressFollowRef = useRef(false);
  const followSuppressTimerRef = useRef(0);
  const programmaticScrollRef = useRef(false);
  const [viewport, setViewport] = useState({
    scrollLeft: 0,
    scrollTop: 0,
    clientWidth: 0,
    clientHeight: 0,
  });
  const [noteBoxSelectRect, setNoteBoxSelectRect] = useState(null);

  const validation = useMemo(
    () => (editedMusicJson ? validateMusicJson(editedMusicJson) : { valid: false, message: 'No composition generated yet.' }),
    [editedMusicJson],
  );
  const canonical = isCanonicalComposition(editedMusicJson);
  const tracks = useMemo(
    () => (Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : []),
    [editedMusicJson],
  );
  const selectedTrack = tracks.find((track) => String(track.id) === String(pianoRollTrackId)) || null;
  const selectedEvents = Array.isArray(selectedTrack?.events) ? selectedTrack.events : [];
  const selectedNote = selectedEvents.find((event) => String(event.id) === String(pianoRollNoteId)) || null;
  const selectedNoteKeySet = useMemo(() => {
    const keys = new Set();
    const refs = Array.isArray(editorSelectionRefs) ? editorSelectionRefs : [];
    if (refs.length) {
      refs.forEach((ref) => {
        const key = noteRefKey(ref);
        if (key) {
          keys.add(key);
        }
      });
      return keys;
    }
    (pianoRollNoteIds || []).forEach((id) => {
      const key = noteRefKey(makeNoteRef(pianoRollTrackId, id));
      if (key) {
        keys.add(key);
      }
    });
    return keys;
  }, [editorSelectionRefs, pianoRollNoteIds, pianoRollTrackId]);

  const selectionStats = useMemo(() => {
    if (!editedMusicJson) {
      return { selectedCount: 0, trackCount: 0, startTick: null, endTick: null };
    }
    const refs = Array.isArray(editorSelectionRefs) && editorSelectionRefs.length
      ? editorSelectionRefs
      : (pianoRollNoteIds || []).map((id) => makeNoteRef(pianoRollTrackId, id)).filter(Boolean);
    return selectionSummary(editedMusicJson, refs, {
      hiddenTrackIds,
      lockedTrackIds,
    });
  }, [
    editedMusicJson,
    editorSelectionRefs,
    pianoRollNoteIds,
    pianoRollTrackId,
    hiddenTrackIds,
    lockedTrackIds,
  ]);

  const selectionRangeLabel = useMemo(() => {
    if (!editedMusicJson || !selectionStats.selectedCount) {
      return null;
    }
    const refs = Array.isArray(editorSelectionRefs) && editorSelectionRefs.length
      ? editorSelectionRefs
      : (pianoRollNoteIds || []).map((id) => makeNoteRef(pianoRollTrackId, id)).filter(Boolean);
    const range = selectionTickRange(editedMusicJson, refs);
    if (!range) {
      return null;
    }
    return `${range.startTick}–${range.endTick}`;
  }, [
    editedMusicJson,
    selectionStats.selectedCount,
    editorSelectionRefs,
    pianoRollNoteIds,
    pianoRollTrackId,
  ]);

  const hiddenTrackIdSet = useMemo(
    () => new Set((hiddenTrackIds || []).map(String)),
    [hiddenTrackIds],
  );

  const motifAuthoringSelection = useMemo(() => {
    if (!editedMusicJson || !canonical || !validation.valid) {
      return { valid: false, message: 'Invalid composition' };
    }
    return validateMotifAuthoringSelection(editedMusicJson, {
      trackId: pianoRollTrackId,
      eventIds: pianoRollNoteIds,
    });
  }, [editedMusicJson, canonical, validation.valid, pianoRollTrackId, pianoRollNoteIds]);

  const motifHighlightSets = useMemo(() => {
    const authoring = new Set((pianoRollNoteIds || []).map(String));
    const usage = new Set();
    const source = new Set();
    if (!editedMusicJson || !Array.isArray(editedMusicJson.motifs)) {
      return { authoring, usage, source };
    }
    const usages = useMusicStore.getState().getMotifUsages();
    const highlighted = usages.find((item) => item.key === motifHighlightedUsageKey);
    if (highlighted?.eventIds?.length) {
      highlighted.eventIds.forEach((id) => usage.add(String(id)));
    }
    const motif = editedMusicJson.motifs.find((item) => item.id === motifSelectedMotifId);
    const occurrence = motif?.occurrences?.find((item) => item.id === motifSelectedOccurrenceId);
    if (occurrence?.event_ids?.length) {
      occurrence.event_ids.forEach((id) => source.add(String(id)));
    }
    return { authoring, usage, source };
  }, [
    editedMusicJson,
    pianoRollNoteIds,
    motifHighlightedUsageKey,
    motifSelectedMotifId,
    motifSelectedOccurrenceId,
  ]);

  const metrics = useMemo(() => {
    if (!canonical || !validation.valid) {
      return null;
    }
    return buildGridMetrics(editedMusicJson, {
      snapValue: pianoRollSnap,
      pixelsPerTick: pianoRollZoom,
      rowHeight: 14,
    });
  }, [canonical, validation.valid, editedMusicJson, pianoRollSnap, pianoRollZoom]);

  const { beginDrag, dragPreview, draggingNoteId } = usePianoRollDrag({
    metrics,
    trackId: pianoRollTrackId,
  });

  const motifOverlays = useMemo(() => {
    if (!metrics || !editedMusicJson) {
      return { usageRect: null, destinationRect: null };
    }
    const overlayArgs = {
      pixelsPerTick: metrics.pixelsPerTick,
      barTicks: metrics.barTicks,
      barBoundaries: metrics.barBoundaries,
      totalHeight: metrics.totalHeight,
    };
    let usageRect = null;
    const usages = useMusicStore.getState().getMotifUsages();
    const highlighted = usages.find((item) => item.key === motifHighlightedUsageKey);
    if (highlighted?.startBar != null && highlighted?.endBar != null) {
      usageRect = selectionOverlayRect(highlighted.startBar, highlighted.endBar, overlayArgs);
    }
    let destinationRect = null;
    if (motifDestinationStartBar && motifDestinationTrackId) {
      const motif = editedMusicJson.motifs?.find((item) => item.id === motifSelectedMotifId);
      const occurrence = motif?.occurrences?.find((item) => item.id === motifSelectedOccurrenceId);
      const span = occurrence
        ? resolveMotifOccurrenceBarSpan(editedMusicJson, occurrence)
        : { valid: false, barSpan: 1 };
      const placement = motifDestinationTickRange(motifDestinationStartBar, {
        composition: editedMusicJson,
        barSpan: span.valid ? span.barSpan : 1,
      });
      if (placement.valid && placement.startBar != null && placement.endBar != null) {
        destinationRect = selectionOverlayRect(placement.startBar, placement.endBar, overlayArgs);
      }
    }
    return { usageRect, destinationRect };
  }, [
    metrics,
    editedMusicJson,
    motifHighlightedUsageKey,
    motifDestinationStartBar,
    motifDestinationTrackId,
    motifSelectedMotifId,
    motifSelectedOccurrenceId,
  ]);

  useEffect(() => {
    if (!canonical || !validation.valid) {
      logger.warn('Unsupported or invalid composition for piano roll', {
        canonical,
        valid: validation.valid,
        message: validation.message,
      });
      return;
    }
    logger.debug('Mount/render summary', {
      trackCount: tracks.length,
      selectedTrack: pianoRollTrackId,
      selectedNote: pianoRollNoteId,
      zoom: pianoRollZoom,
      snap: pianoRollSnap,
      eventCount: selectedEvents.length,
    });
  }, [
    canonical,
    validation.valid,
    validation.message,
    tracks.length,
    pianoRollTrackId,
    pianoRollNoteId,
    pianoRollZoom,
    pianoRollSnap,
    selectedEvents.length,
  ]);

  useEffect(() => {
    if (!metrics) {
      return undefined;
    }
    logger.debug('Grid metrics', {
      barTicks: metrics.barTicks,
      snapTicks: metrics.snapTicks,
      visiblePitchRange: `${metrics.minMidi}-${metrics.maxMidi}`,
      totalWidth: metrics.totalWidth,
      zoom: pianoRollZoom,
    });
    if (metrics.warning) {
      logger.warn('Fallback/incomplete grid metrics', { warning: metrics.warning });
    }
    return undefined;
  }, [metrics, pianoRollZoom]);

  const measureViewport = useCallback(() => {
    const node = scrollRef.current;
    if (!node) {
      return;
    }
    setViewport({
      scrollLeft: node.scrollLeft,
      scrollTop: node.scrollTop,
      clientWidth: node.clientWidth,
      clientHeight: node.clientHeight,
    });
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) {
      return undefined;
    }
    measureViewport();
    const onScroll = () => {
      // Manual scrolling should not fight auto-follow.
      if (
        !programmaticScrollRef.current
        && playbackAutoFollow
        && playbackStatus === 'playing'
      ) {
        userScrollSuppressFollowRef.current = true;
        if (followSuppressTimerRef.current) {
          clearTimeout(followSuppressTimerRef.current);
        }
        followSuppressTimerRef.current = setTimeout(() => {
          userScrollSuppressFollowRef.current = false;
          followSuppressTimerRef.current = 0;
        }, 900);
      }
      programmaticScrollRef.current = false;
      if (scrollRafRef.current) {
        return;
      }
      scrollRafRef.current = requestAnimationFrame(() => {
        scrollRafRef.current = 0;
        measureViewport();
      });
    };
    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => measureViewport())
      : null;
    observer?.observe(node);
    node.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', measureViewport);
    return () => {
      observer?.disconnect();
      node.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', measureViewport);
      if (scrollRafRef.current) {
        cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = 0;
      }
      if (followSuppressTimerRef.current) {
        clearTimeout(followSuppressTimerRef.current);
        followSuppressTimerRef.current = 0;
      }
    };
  }, [
    canonical,
    validation.valid,
    measureViewport,
    metrics?.totalWidth,
    metrics?.totalHeight,
    playbackAutoFollow,
    playbackStatus,
  ]);

  useEffect(() => {
    const request = viewportScrollRequest;
    const node = scrollRef.current;
    if (!request || !node || !metrics) {
      return undefined;
    }
    const frame = requestAnimationFrame(() => {
      const duration = Number(editedMusicJson?.duration_ticks) || 0;
      let nextLeft = request.scrollLeft;
      if (nextLeft == null && request.centerTick != null) {
        nextLeft = scrollLeftForCenterTick({
          centerTick: request.centerTick,
          pixelsPerTick: metrics.pixelsPerTick,
          clientWidth: node.clientWidth,
          durationTicks: duration,
        }).scrollLeft;
      }
      if (nextLeft == null || !Number.isFinite(Number(nextLeft))) {
        return;
      }
      programmaticScrollRef.current = true;
      node.scrollLeft = Number(nextLeft);
      measureViewport();
      logger.debug('Applied viewport scroll request', {
        requestId: request.id,
        reason: request.reason,
        scrollLeft: node.scrollLeft,
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [
    viewportScrollRequest,
    metrics,
    editedMusicJson?.duration_ticks,
    measureViewport,
  ]);

  // Optional auto-follow: keep playback cursor in view without fighting manual scroll.
  useEffect(() => {
    if (!playbackAutoFollow || playbackStatus !== 'playing' || !metrics || !scrollRef.current) {
      return undefined;
    }
    if (userScrollSuppressFollowRef.current) {
      return undefined;
    }
    const position = secondsToPlaybackPosition(playbackSeconds, {
      tempo: editedMusicJson?.tempo,
      ticksPerQuarter: editedMusicJson?.ticks_per_quarter,
      timeSignature: editedMusicJson?.time_signature,
      composition: editedMusicJson,
    });
    const cursorPx = position.tick * metrics.pixelsPerTick;
    const node = scrollRef.current;
    const margin = Math.max(48, node.clientWidth * 0.15);
    const left = node.scrollLeft;
    const right = left + node.clientWidth;
    if (cursorPx >= left + margin && cursorPx <= right - margin) {
      return undefined;
    }
    const duration = Number(editedMusicJson?.duration_ticks) || 0;
    const nextLeft = scrollLeftForCenterTick({
      centerTick: position.tick,
      pixelsPerTick: metrics.pixelsPerTick,
      clientWidth: node.clientWidth,
      durationTicks: duration,
    }).scrollLeft;
    if (!Number.isFinite(nextLeft)) {
      return undefined;
    }
    // Programmatic follow must not re-trigger the manual-scroll suppress window.
    programmaticScrollRef.current = true;
    node.scrollLeft = nextLeft;
    measureViewport();
    return undefined;
  }, [
    playbackAutoFollow,
    playbackStatus,
    playbackSeconds,
    metrics,
    editedMusicJson,
    measureViewport,
  ]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) {
      return undefined;
    }
    const onWheel = (event) => {
      if (!(event.ctrlKey || event.metaKey)) {
        return;
      }
      event.preventDefault();
      const bounds = node.getBoundingClientRect();
      const pointerX = event.clientX - bounds.left;
      const oldPpt = metrics?.pixelsPerTick || pianoRollZoom;
      const direction = event.deltaY < 0 ? 1 : -1;
      const factor = direction > 0 ? 1.1 : 1 / 1.1;
      const nextZoom = Math.min(0.25, Math.max(0.01, (pianoRollZoom || 0.05) * factor));
      if (nextZoom === pianoRollZoom) {
        return;
      }
      zoomAnchorPendingRef.current = {
        scrollLeft: node.scrollLeft,
        pointerX,
        oldPixelsPerTick: oldPpt,
      };
      setPianoRollZoom(nextZoom);
      logger.debug('Ctrl-wheel zoom', { zoom: nextZoom, pointerX: Math.round(pointerX) });
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [metrics, pianoRollZoom, setPianoRollZoom]);

  useEffect(() => {
    const pending = zoomAnchorPendingRef.current;
    const node = scrollRef.current;
    if (!pending || !node || !metrics) {
      return;
    }
    zoomAnchorPendingRef.current = null;
    const nextContentWidth = metrics.totalWidth;
    const anchored = anchorZoom({
      scrollLeft: pending.scrollLeft,
      pointerX: pending.pointerX,
      oldPixelsPerTick: pending.oldPixelsPerTick,
      newPixelsPerTick: metrics.pixelsPerTick,
      contentWidth: nextContentWidth,
      clientWidth: node.clientWidth,
    });
    if (anchored.clamped) {
      logger.warn('Zoom scroll clamped to content bounds', {
        scrollLeft: anchored.scrollLeft,
        contentWidth: nextContentWidth,
      });
    }
    node.scrollLeft = anchored.scrollLeft;
    measureViewport();
  }, [pianoRollZoom, metrics, measureViewport]);

  useEffect(() => {
    if (!canonical || !validation.valid) {
      return undefined;
    }
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    logger.debug('Notation debounce scheduled', {
      notationRevision: notationRevision.slice(0, 48),
      delayMs: NOTE_NOTATION_DEBOUNCE_MS,
    });
    debounceRef.current = setTimeout(() => {
      logger.debug('Notation debounce completed; refreshing MusicXML', {
        notationRevision: notationRevision.slice(0, 48),
      });
      refreshMusicXmlFromEditedComposition();
    }, NOTE_NOTATION_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        logger.debug('Notation debounce cancelled');
      }
    };
  }, [canonical, validation.valid, notationRevision, refreshMusicXmlFromEditedComposition]);

  const { trackById, noteByKey, allNoteGeoms } = useMemo(() => {
    const tracksMap = new Map();
    const notesMap = new Map();
    const items = [];
    tracks.forEach((track, trackIndex) => {
      const trackId = String(track.id);
      if (hiddenTrackIdSet.has(trackId)) {
        return;
      }
      tracksMap.set(trackId, track);
      const editable = trackId === String(pianoRollTrackId);
      const color = editable ? '#4f46e5' : CONTEXT_COLORS[trackIndex % CONTEXT_COLORS.length];
      const events = Array.isArray(track.events) ? track.events : [];
      events.forEach((event) => {
        const midi = pitchToMidi(event.pitch).midi;
        if (midi === null) {
          return;
        }
        const startTick = Number(event.start_tick) || 0;
        const durationTicks = Number(event.duration_ticks) || 0;
        const item = {
          trackId: track.id,
          trackIndex,
          event,
          startTick,
          endTick: startTick + durationTicks,
          pitchMidi: midi,
          editable,
          selectable: true,
          color,
        };
        items.push(item);
        notesMap.set(`${track.id}:${event.id}`, item);
      });
    });
    return { trackById: tracksMap, noteByKey: notesMap, allNoteGeoms: items };
  }, [tracks, pianoRollTrackId, hiddenTrackIdSet]);

  // Keep Map indexes live for O(1) lookup (selection/edit paths; size used in cull logging).
  const noteIndexSize = noteByKey.size + trackById.size;
  const visibleTicks = useMemo(() => {
    if (!metrics) {
      return { startTick: 0, endTick: 0, bufferTicks: DEFAULT_TICK_BUFFER };
    }
    return computeVisibleTickRange({
      scrollLeft: viewport.scrollLeft,
      clientWidth: viewport.clientWidth || 1,
      pixelsPerTick: metrics.pixelsPerTick,
      durationTicks: metrics.durationTicks,
      bufferTicks: Math.max(DEFAULT_TICK_BUFFER, metrics.barTicks || DEFAULT_TICK_BUFFER),
    });
  }, [metrics, viewport.scrollLeft, viewport.clientWidth]);

  const visiblePitches = useMemo(() => {
    if (!metrics) {
      return { minMidi: 0, maxMidi: 127, bufferRows: 2 };
    }
    return computeVisiblePitchRange({
      scrollTop: viewport.scrollTop,
      clientHeight: viewport.clientHeight || 1,
      rowHeight: metrics.rowHeight,
      minMidi: metrics.minMidi,
      maxMidi: metrics.maxMidi,
    });
  }, [metrics, viewport.scrollTop, viewport.clientHeight]);

  const visibleNoteGeoms = useMemo(() => filterNotesInViewport(allNoteGeoms, {
    visibleTicks,
    visiblePitches,
  }), [allNoteGeoms, visibleTicks, visiblePitches]);

  useEffect(() => {
    if (!metrics) {
      return;
    }
    logViewportThrottled({
      startTick: visibleTicks.startTick,
      endTick: visibleTicks.endTick,
      minMidi: visiblePitches.minMidi,
      maxMidi: visiblePitches.maxMidi,
      renderedNotes: visibleNoteGeoms.length,
      totalNotes: allNoteGeoms.length,
      indexSize: noteIndexSize,
    });
  }, [
    metrics,
    visibleTicks.startTick,
    visibleTicks.endTick,
    visiblePitches.minMidi,
    visiblePitches.maxMidi,
    visibleNoteGeoms.length,
    allNoteGeoms.length,
    noteIndexSize,
  ]);

  const visibleNotes = useMemo(() => {
    if (!metrics) {
      return [];
    }
    return visibleNoteGeoms.map((item) => {
      const event = item.event;
      const eventId = String(event.id);
      const refKey = noteRefKey(makeNoteRef(item.trackId, eventId));
      const selected = Boolean(refKey && selectedNoteKeySet.has(refKey));
      const onHighlightedTrack = item.editable
        || motifHighlightSets.usage.has(eventId)
        || motifHighlightSets.source.has(eventId)
        || selected;
      let motifRole = null;
      if (motifHighlightSets.usage.has(eventId)) {
        motifRole = 'usage';
      } else if (motifHighlightSets.source.has(eventId)) {
        motifRole = 'source';
      } else if (item.editable && motifHighlightSets.authoring.has(eventId)) {
        motifRole = 'authoring';
      }
      return {
        key: `${item.trackId}:${event.id || `${event.pitch}-${event.start_tick}`}`,
        trackId: item.trackId,
        event,
        left: item.startTick * metrics.pixelsPerTick,
        top: (metrics.maxMidi - item.pitchMidi) * metrics.rowHeight,
        width: (item.endTick - item.startTick) * metrics.pixelsPerTick,
        color: item.color,
        editable: item.editable,
        selectable: item.selectable !== false,
        opacity: onHighlightedTrack && motifRole ? 1 : (item.editable || selected ? 0.95 : CONTEXT_TRACK_OPACITY),
        selected,
        motifRole,
      };
    });
  }, [metrics, visibleNoteGeoms, selectedNoteKeySet, motifHighlightSets]);

  const pitchRows = useMemo(() => {
    if (!metrics) {
      return [];
    }
    const rows = [];
    for (let midi = metrics.maxMidi; midi >= metrics.minMidi; midi -= 1) {
      rows.push(midi);
    }
    return rows;
  }, [metrics]);

  const barLabels = useMemo(() => {
    if (!metrics) {
      return [];
    }
    const labels = [];
    if (Array.isArray(metrics.barBoundaries) && metrics.barBoundaries.length > 1) {
      for (let bar = 0; bar < (metrics.barCount || 0); bar += 1) {
        const tick = metrics.barBoundaries[bar];
        if (tick < visibleTicks.startTick || tick > visibleTicks.endTick) {
          continue;
        }
        labels.push({
          bar: bar + 1,
          left: tick * metrics.pixelsPerTick + 4,
        });
      }
      return labels;
    }
    if (!metrics.barTicks) {
      return [];
    }
    for (let bar = 0; bar < (metrics.barCount || 0); bar += 1) {
      const tick = bar * metrics.barTicks;
      if (tick < visibleTicks.startTick || tick > visibleTicks.endTick) {
        continue;
      }
      labels.push({
        bar: bar + 1,
        left: tick * metrics.pixelsPerTick + 4,
      });
    }
    return labels;
  }, [metrics, visibleTicks.startTick, visibleTicks.endTick]);

  const timelineCues = useMemo(() => {
    if (!editedMusicJson || !metrics) {
      return [];
    }
    return buildTimelineCues(editedMusicJson)
      .filter((cue) => cue.tick >= visibleTicks.startTick && cue.tick <= visibleTicks.endTick)
      .map((cue) => ({
        ...cue,
        left: cue.tick * metrics.pixelsPerTick + 2,
      }));
  }, [editedMusicJson, metrics, visibleTicks.startTick, visibleTicks.endTick]);

  const navigationSections = useMemo(
    () => listSectionsForNavigation(editedMusicJson),
    [editedMusicJson],
  );

  const currentBar = useMemo(
    () => tickToBar(editedMusicJson, editCursorTick) || 1,
    [editedMusicJson, editCursorTick],
  );

  const currentSectionKey = useMemo(() => {
    const tick = Number(editCursorTick) || 0;
    const match = navigationSections.find((section) => (
      tick >= section.startTick && tick < section.endTick
    )) || navigationSections.find((section) => tick === section.endTick)
      || navigationSections[0];
    return match?.key || '';
  }, [navigationSections, editCursorTick]);

  const placeEditCursorAtClientX = useCallback((clientX, targetEl) => {
    if (!metrics || !targetEl) {
      return;
    }
    const bounds = targetEl.getBoundingClientRect();
    const x = clientX - bounds.left;
    const mapped = pixelToTick(x, metrics.pixelsPerTick);
    const tick = setEditCursorTick(mapped.tick);
    logger.debug('Edit cursor placed', {
      tick,
      bar: tickToBar(editedMusicJson, tick),
    });
  }, [metrics, setEditCursorTick, editedMusicJson]);

  const handleDeleteSelected = () => {
    const result = deleteSelection();
    if (!result?.ok) {
      logger.warn('Delete selection guarded', { code: result?.code || null });
    }
  };

  const handleZoomChange = useCallback((nextZoom) => {
    const node = scrollRef.current;
    const oldPpt = metrics?.pixelsPerTick || pianoRollZoom;
    zoomAnchorPendingRef.current = {
      scrollLeft: node?.scrollLeft || 0,
      pointerX: node ? node.clientWidth / 2 : 0,
      oldPixelsPerTick: oldPpt,
    };
    setPianoRollZoom(nextZoom);
  }, [metrics, pianoRollZoom, setPianoRollZoom]);

  const handleZoomIn = useCallback(() => {
    const node = scrollRef.current;
    zoomAnchorPendingRef.current = {
      scrollLeft: node?.scrollLeft || 0,
      pointerX: node ? node.clientWidth / 2 : 0,
      oldPixelsPerTick: metrics?.pixelsPerTick || pianoRollZoom,
    };
    zoomIn();
  }, [metrics, pianoRollZoom, zoomIn]);

  const handleZoomOut = useCallback(() => {
    const node = scrollRef.current;
    zoomAnchorPendingRef.current = {
      scrollLeft: node?.scrollLeft || 0,
      pointerX: node ? node.clientWidth / 2 : 0,
      oldPixelsPerTick: metrics?.pixelsPerTick || pianoRollZoom,
    };
    zoomOut();
  }, [metrics, pianoRollZoom, zoomOut]);

  const handleZoomToFit = useCallback(() => {
    zoomToFit({
      clientWidth: scrollRef.current?.clientWidth || viewport.clientWidth || 0,
    });
  }, [zoomToFit, viewport.clientWidth]);

  const handleZoomToSelection = useCallback(() => {
    zoomToSelection({
      clientWidth: scrollRef.current?.clientWidth || viewport.clientWidth || 0,
    });
  }, [zoomToSelection, viewport.clientWidth]);

  const handleEditorShortcut = useCallback((event) => {
    const { command } = dispatchShortcutEvent(event, { allowTransport: true });
    if (!command) {
      return;
    }
    event.preventDefault();
    logger.debug('Shortcut command', {
      command,
      selectedCount: selectionStats.selectedCount,
    });

    switch (command) {
      case SHORTCUT_COMMAND.COPY:
        copySelection();
        break;
      case SHORTCUT_COMMAND.CUT:
        cutSelection();
        break;
      case SHORTCUT_COMMAND.PASTE:
        pasteClipboard();
        break;
      case SHORTCUT_COMMAND.DUPLICATE:
        duplicateSelection();
        break;
      case SHORTCUT_COMMAND.SELECT_ALL:
        selectAllVisible();
        break;
      case SHORTCUT_COMMAND.CLEAR_SELECTION:
        clearEditorSelection();
        break;
      case SHORTCUT_COMMAND.DELETE:
        deleteSelection();
        break;
      case SHORTCUT_COMMAND.UNDO:
        undoCompositionEdit();
        break;
      case SHORTCUT_COMMAND.REDO:
        redoCompositionEdit();
        break;
      case SHORTCUT_COMMAND.NUDGE_LEFT:
        nudgeSelectionBySnap(-1);
        break;
      case SHORTCUT_COMMAND.NUDGE_RIGHT:
        nudgeSelectionBySnap(1);
        break;
      case SHORTCUT_COMMAND.NUDGE_UP:
        transposeSelection(1);
        break;
      case SHORTCUT_COMMAND.NUDGE_DOWN:
        transposeSelection(-1);
        break;
      case SHORTCUT_COMMAND.TRANSPOSE_OCTAVE_UP:
        transposeSelection(12);
        break;
      case SHORTCUT_COMMAND.TRANSPOSE_OCTAVE_DOWN:
        transposeSelection(-12);
        break;
      case SHORTCUT_COMMAND.TRANSPORT_TOGGLE:
        togglePlaybackTransport();
        break;
      case SHORTCUT_COMMAND.ESCAPE:
        clearEditorSelection();
        setNoteBoxSelectRect(null);
        break;
      case SHORTCUT_COMMAND.NAV_PREV_BAR:
        gotoPrevBar();
        break;
      case SHORTCUT_COMMAND.NAV_NEXT_BAR:
        gotoNextBar();
        break;
      case SHORTCUT_COMMAND.NAV_PREV_SECTION:
        gotoPrevSection();
        break;
      case SHORTCUT_COMMAND.NAV_NEXT_SECTION:
        gotoNextSection();
        break;
      case SHORTCUT_COMMAND.ZOOM_IN:
        handleZoomIn();
        break;
      case SHORTCUT_COMMAND.ZOOM_OUT:
        handleZoomOut();
        break;
      case SHORTCUT_COMMAND.ZOOM_TO_FIT:
        handleZoomToFit();
        break;
      case SHORTCUT_COMMAND.ZOOM_TO_SELECTION:
        handleZoomToSelection();
        break;
      default:
        logger.debug('Unhandled editor shortcut', { command });
        break;
    }
  }, [
    selectionStats.selectedCount,
    copySelection,
    cutSelection,
    pasteClipboard,
    duplicateSelection,
    selectAllVisible,
    clearEditorSelection,
    deleteSelection,
    undoCompositionEdit,
    redoCompositionEdit,
    nudgeSelectionBySnap,
    transposeSelection,
    togglePlaybackTransport,
    gotoPrevBar,
    gotoNextBar,
    gotoPrevSection,
    gotoNextSection,
    handleZoomIn,
    handleZoomOut,
    handleZoomToFit,
    handleZoomToSelection,
  ]);

  const handleKeyDown = (event) => {
    handleEditorShortcut(event);
  };

  const onNotePointerDown = useCallback((note, pointerEvent, layout) => {
    beginDrag('move', note, pointerEvent, layout);
  }, [beginDrag]);

  const onResizePointerDown = useCallback((note, pointerEvent, layout) => {
    beginDrag('resize', note, pointerEvent, layout);
  }, [beginDrag]);

  const onNoteClick = useCallback((event, clickEvent, trackId) => {
    clickEvent.stopPropagation();
    const ref = makeNoteRef(trackId, event.id);
    if (!ref) {
      return;
    }
    if (clickEvent.shiftKey) {
      extendEditorSelectionTo(ref);
    } else if (clickEvent.ctrlKey || clickEvent.metaKey) {
      toggleEditorSelectionRef(ref);
    } else {
      setEditorSelection({ refs: [ref], primary: ref, anchor: ref });
    }
    logger.debug('Note selection click', {
      trackId,
      selectedCount: useMusicStore.getState().editorSelectionRefs?.length || 0,
      shift: clickEvent.shiftKey,
      toggle: clickEvent.ctrlKey || clickEvent.metaKey,
    });
  }, [extendEditorSelectionTo, toggleEditorSelectionRef, setEditorSelection]);

  const handleGridPointerDown = (event) => {
    if (!metrics || event.target !== event.currentTarget) {
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;

    // Ctrl+click places the edit cursor without creating a note (Alt/Meta keep box-select).
    if (event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
      event.preventDefault();
      placeEditCursorAtClientX(event.clientX, event.currentTarget);
      return;
    }

    // Alt+drag or Meta+drag → note box select (Shift+drag remains AI bars).
    if (event.altKey || (event.metaKey && !event.shiftKey)) {
      event.preventDefault();
      const origin = { x, y };
      setNoteBoxSelectRect({ left: x, top: y, width: 0, height: 0 });
      logger.debug('Note box select started');

      const onMove = (moveEvent) => {
        if (!noteBoxSelectRef.current || moveEvent.pointerId !== event.pointerId) {
          return;
        }
        const moveBounds = event.currentTarget.getBoundingClientRect();
        const moveX = moveEvent.clientX - moveBounds.left;
        const moveY = moveEvent.clientY - moveBounds.top;
        const left = Math.min(origin.x, moveX);
        const top = Math.min(origin.y, moveY);
        const width = Math.abs(moveX - origin.x);
        const height = Math.abs(moveY - origin.y);
        setNoteBoxSelectRect({ left, top, width, height });
      };

      const onUp = (upEvent) => {
        if (!noteBoxSelectRef.current || upEvent.pointerId !== event.pointerId) {
          return;
        }
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        const rect = noteBoxSelectRef.current.rect;
        noteBoxSelectRef.current = null;
        setNoteBoxSelectRect(null);
        if (!rect || rect.width < 2 || rect.height < 2) {
          return;
        }
        const startTick = pixelToTick(rect.left, metrics.pixelsPerTick).tick;
        const endTick = pixelToTick(rect.left + rect.width, metrics.pixelsPerTick).tick;
        const topMidi = pixelToPitchMidi(rect.top, {
          minMidi: metrics.minMidi,
          maxMidi: metrics.maxMidi,
          rowHeight: metrics.rowHeight,
        }).midi;
        const bottomMidi = pixelToPitchMidi(rect.top + rect.height, {
          minMidi: metrics.minMidi,
          maxMidi: metrics.maxMidi,
          rowHeight: metrics.rowHeight,
        }).midi;
        const refs = collectNotesInBox(editedMusicJson, {
          startTick: Math.min(startTick, endTick),
          endTick: Math.max(startTick, endTick),
          pitchMin: Math.min(topMidi, bottomMidi),
          pitchMax: Math.max(topMidi, bottomMidi),
        }, { hiddenTrackIds });
        setEditorSelection({
          refs,
          primary: refs[0] || null,
          anchor: refs[0] || null,
        });
        logger.debug('Note box select committed', { selectedCount: refs.length });
      };

      noteBoxSelectRef.current = {
        origin,
        rect: { left: x, top: y, width: 0, height: 0 },
        onMove,
        onUp,
      };
      const wrapMove = (moveEvent) => {
        onMove(moveEvent);
        if (noteBoxSelectRef.current) {
          const moveBounds = event.currentTarget.getBoundingClientRect();
          const moveX = moveEvent.clientX - moveBounds.left;
          const moveY = moveEvent.clientY - moveBounds.top;
          noteBoxSelectRef.current.rect = {
            left: Math.min(origin.x, moveX),
            top: Math.min(origin.y, moveY),
            width: Math.abs(moveX - origin.x),
            height: Math.abs(moveY - origin.y),
          };
        }
      };
      noteBoxSelectRef.current.onMove = wrapMove;
      window.addEventListener('pointermove', wrapMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
      return;
    }

    if (event.shiftKey) {
      if (!pianoRollTrackId) {
        return;
      }
      event.preventDefault();
      const mapped = pointerXToBar(x, {
        pixelsPerTick: metrics.pixelsPerTick,
        barTicks: metrics.barTicks,
        barCount: metrics.barCount,
        barBoundaries: metrics.barBoundaries,
        scrollLeft: 0,
      });
      if (mapped.bar === null) {
        return;
      }
      const originBar = mapped.bar;
      setAiEditSelection({
        startBar: originBar,
        endBar: originBar,
        trackMode: aiEditTrackMode,
      });
      logger.info('AI bar selection started', {
        startBar: originBar,
        endBar: originBar,
        trackMode: aiEditTrackMode,
      });

      const onMove = (moveEvent) => {
        if (!barSelectRef.current || moveEvent.pointerId !== event.pointerId) {
          return;
        }
        const moveBounds = event.currentTarget.getBoundingClientRect();
        const moveX = moveEvent.clientX - moveBounds.left;
        const nextBar = pointerXToBar(moveX, {
          pixelsPerTick: metrics.pixelsPerTick,
          barTicks: metrics.barTicks,
          barCount: metrics.barCount,
          barBoundaries: metrics.barBoundaries,
        });
        if (nextBar.bar === null) {
          return;
        }
        const now = Date.now();
        if (now - lastBarSelectLogRef.current >= BAR_SELECTION_LOG_THROTTLE_MS) {
          lastBarSelectLogRef.current = now;
          logger.debug('AI bar selection pointer update', {
            startBar: originBar,
            endBar: nextBar.bar,
          });
        }
        setAiEditSelection({
          startBar: originBar,
          endBar: nextBar.bar,
          trackMode: aiEditTrackMode,
        });
      };

      const onUp = (upEvent) => {
        if (!barSelectRef.current || upEvent.pointerId !== event.pointerId) {
          return;
        }
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        const final = normalizeBarRange(
          barSelectRef.current.originBar,
          useMusicStore.getState().aiEditEndBar,
          metrics.barCount,
        );
        barSelectRef.current = null;
        logger.info('AI bar selection committed', {
          startBar: final.startBar,
          endBar: final.endBar,
          trackMode: aiEditTrackMode,
          trackScope: aiEditTrackMode === 'all' ? 'all-tracks' : 'current-track',
        });
      };

      barSelectRef.current = { originBar, onMove, onUp };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
      return;
    }

    if (!pianoRollTrackId) {
      return;
    }

    const rawTick = pixelToTick(x, metrics.pixelsPerTick).tick;
    const { durationTicks } = defaultDurationForSnap(pianoRollSnap, metrics.ticksPerQuarter);
    const snappedStart = snapTick(rawTick, metrics.snapTicks, {
      maxTick: Math.max(0, metrics.durationTicks - durationTicks),
    });
    const pitchMidi = pixelToPitchMidi(y, {
      minMidi: metrics.minMidi,
      maxMidi: metrics.maxMidi,
      rowHeight: metrics.rowHeight,
    });
    const pitch = midiToPitch(pitchMidi.midi).pitch;
    if (!pitch) {
      return;
    }
    logger.debug('Pointer create coordinates', {
      x,
      y,
      snappedStart: snappedStart.tick,
      pitch,
      durationTicks,
    });
    try {
      const note = createNote(pianoRollTrackId, {
        pitch,
        start_tick: snappedStart.tick,
        duration_ticks: durationTicks,
        velocity: 90,
      });
      if (note) {
        logger.info('Note created', sanitizeNoteSummary(note));
        const ref = makeNoteRef(pianoRollTrackId, note.id);
        if (ref) {
          setEditorSelection({ refs: [ref], primary: ref, anchor: ref });
        }
      }
    } catch (error) {
      logger.error('Note create failed', { message: error?.message || 'unknown' });
    }
  };

  if (!editedMusicJson) {
    return (
      <Panel aria-label="Piano roll editor">
        <Title>Piano Roll Editor</Title>
        <Status $tone="warn">Generate a composition to edit notes on the piano roll.</Status>
      </Panel>
    );
  }

  if (!canonical) {
    return (
      <Panel aria-label="Piano roll editor">
        <Title>Piano Roll Editor</Title>
        <Status $tone="warn">
          Piano roll supports canonical composition only. Legacy JSON remains editable in the JSON editor.
        </Status>
      </Panel>
    );
  }

  if (!validation.valid) {
    return (
      <Panel aria-label="Piano roll editor">
        <Title>Piano Roll Editor</Title>
        <Status $tone="error">{validation.message || 'Invalid composition JSON.'}</Status>
      </Panel>
    );
  }

  if (!tracks.length) {
    return (
      <Panel aria-label="Piano roll editor">
        <Title>Piano Roll Editor</Title>
        <Status $tone="warn">This composition has no tracks to edit.</Status>
      </Panel>
    );
  }

  if (!metrics) {
    return (
      <Panel aria-label="Piano roll editor">
        <Title>Piano Roll Editor</Title>
        <Status $tone="error">Unable to build piano-roll grid metrics.</Status>
      </Panel>
    );
  }

  const beatWidth = metrics.ticksPerQuarter * metrics.pixelsPerTick;
  const barWidth = metrics.barTicks * metrics.pixelsPerTick;
  const selectionRect = (aiEditStartBar && aiEditEndBar)
    ? selectionOverlayRect(aiEditStartBar, aiEditEndBar, {
      pixelsPerTick: metrics.pixelsPerTick,
      barTicks: metrics.barTicks,
      barBoundaries: metrics.barBoundaries,
      totalHeight: metrics.totalHeight,
    })
    : null;
  const notationMessage = pianoRollNotationStatus === 'loading'
    ? 'Refreshing notation preview…'
    : pianoRollNotationStatus === 'error'
      ? pianoRollNotationError || 'Notation refresh failed'
      : pianoRollNotationStatus === 'success'
        ? 'Notation preview synced with piano-roll edits'
        : '';

  return (
    <Panel aria-label="Piano roll editor">
      <HeaderRow>
        <Title>Piano Roll Editor</Title>
        <Controls>
          <ControlGroup htmlFor="piano-roll-track">
            Track
            <Select
              id="piano-roll-track"
              data-testid="piano-roll-track-select"
              value={pianoRollTrackId || ''}
              aria-label="Selected piano-roll track"
              onChange={(event) => selectPianoRollTrack(event.target.value)}
            >
              {tracks.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.name || track.id} ({track.role})
                </option>
              ))}
            </Select>
          </ControlGroup>
          <ControlGroup htmlFor="ai-edit-start-bar">
            AI start bar
            <NumberInput
              id="ai-edit-start-bar"
              data-testid="ai-edit-start-bar"
              type="number"
              min="1"
              max={metrics.barCount || 1}
              value={aiEditStartBar || ''}
              aria-label="AI edit start bar"
              onChange={(event) => {
                const startBar = Number(event.target.value);
                const endBar = aiEditEndBar || startBar;
                setAiEditSelection({ startBar, endBar, trackMode: aiEditTrackMode });
              }}
            />
          </ControlGroup>
          <ControlGroup htmlFor="ai-edit-end-bar">
            AI end bar
            <NumberInput
              id="ai-edit-end-bar"
              data-testid="ai-edit-end-bar"
              type="number"
              min="1"
              max={metrics.barCount || 1}
              value={aiEditEndBar || ''}
              aria-label="AI edit end bar"
              onChange={(event) => {
                const endBar = Number(event.target.value);
                const startBar = aiEditStartBar || endBar;
                setAiEditSelection({ startBar, endBar, trackMode: aiEditTrackMode });
              }}
            />
          </ControlGroup>
          <ControlGroup htmlFor="ai-edit-track-mode">
            AI track scope
            <Select
              id="ai-edit-track-mode"
              value={aiEditTrackMode}
              aria-label="AI edit track scope"
              onChange={(event) => setAiEditTrackMode(event.target.value)}
            >
              <option value="current">Current track</option>
              <option value="all">All tracks</option>
            </Select>
          </ControlGroup>
          <Button
            type="button"
            onClick={() => clearAiEditSelection()}
            disabled={!aiEditStartBar}
            aria-label="Clear AI bar selection"
          >
            Clear AI selection
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-mark-motif"
            disabled={!motifAuthoringSelection.valid}
            title={motifAuthoringSelection.valid ? '' : motifAuthoringSelection.message}
            onClick={() => {
              const label = nextMotifLabel(editedMusicJson?.motifs || []);
              const result = markMotifFromSelection({ label });
              if (!result?.ok) {
                logger.warn('Mark motif rejected', { code: result?.code || null });
              }
            }}
          >
            Mark as motif
          </Button>
          <ControlGroup htmlFor="piano-roll-snap">
            Snap
            <Select
              id="piano-roll-snap"
              value={pianoRollSnap}
              aria-label="Rhythmic snap value"
              onChange={(event) => setPianoRollSnap(event.target.value)}
            >
              {SNAP_VALUES.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </Select>
          </ControlGroup>
          <ControlGroup htmlFor="piano-roll-current-bar">
            Bar
            <NumberInput
              id="piano-roll-current-bar"
              data-testid="piano-roll-current-bar"
              type="number"
              min="1"
              max={metrics.barCount || 1}
              value={currentBar}
              aria-label="Current edit-cursor bar"
              onChange={(event) => {
                const bar = Number(event.target.value);
                if (Number.isInteger(bar)) {
                  gotoBar(bar);
                }
              }}
            />
          </ControlGroup>
          <Button
            type="button"
            data-testid="piano-roll-prev-bar"
            onClick={() => gotoPrevBar()}
            aria-label="Go to previous bar"
          >
            Prev bar
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-next-bar"
            onClick={() => gotoNextBar()}
            aria-label="Go to next bar"
          >
            Next bar
          </Button>
          <ControlGroup htmlFor="piano-roll-section">
            Section
            <Select
              id="piano-roll-section"
              data-testid="piano-roll-section"
              value={currentSectionKey}
              aria-label="Go to section"
              onChange={(event) => gotoSection(event.target.value)}
            >
              {navigationSections.map((section) => (
                <option key={section.key} value={section.key}>{section.label}</option>
              ))}
            </Select>
          </ControlGroup>
          <Button
            type="button"
            data-testid="piano-roll-prev-section"
            onClick={() => gotoPrevSection()}
            aria-label="Go to previous section"
          >
            Prev section
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-next-section"
            onClick={() => gotoNextSection()}
            aria-label="Go to next section"
          >
            Next section
          </Button>
          <ControlGroup htmlFor="piano-roll-zoom">
            Zoom
            <Select
              id="piano-roll-zoom"
              value={String(pianoRollZoom)}
              aria-label="Horizontal zoom"
              onChange={(event) => handleZoomChange(Number(event.target.value))}
            >
              <option value="0.02">Far</option>
              <option value="0.05">Default</option>
              <option value="0.1">Close</option>
              <option value="0.18">Detail</option>
            </Select>
          </ControlGroup>
          <Button
            type="button"
            data-testid="piano-roll-zoom-in"
            onClick={handleZoomIn}
            aria-label="Zoom in"
          >
            Zoom in
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-zoom-out"
            onClick={handleZoomOut}
            aria-label="Zoom out"
          >
            Zoom out
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-zoom-selection"
            onClick={handleZoomToSelection}
            disabled={!selectionStats.selectedCount}
            aria-label="Zoom to selection"
          >
            Zoom selection
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-zoom-fit"
            onClick={handleZoomToFit}
            aria-label="Zoom to fit composition"
          >
            Zoom fit
          </Button>
          <Button type="button" data-testid="piano-roll-undo" onClick={() => undoCompositionEdit()} disabled={!compositionEditUndoStack.length} aria-label="Undo composition edit">
            Undo
          </Button>
          <Button type="button" onClick={() => redoCompositionEdit()} disabled={!compositionEditRedoStack.length} aria-label="Redo composition edit">
            Redo
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-select-all"
            onClick={() => selectAllVisible()}
            aria-label="Select all visible notes"
          >
            Select all visible
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-clear-selection"
            onClick={() => clearEditorSelection()}
            disabled={!selectionStats.selectedCount}
            aria-label="Clear note selection"
          >
            Clear selection
          </Button>
          <Button
            type="button"
            $danger
            onClick={handleDeleteSelected}
            disabled={!selectionStats.selectedCount}
            aria-label="Delete selected notes"
          >
            Delete
          </Button>
        </Controls>
      </HeaderRow>

      <PianoRollTrackControls tracks={tracks} />

      <Status data-testid="piano-roll-selection-status">
        Selected {selectionStats.selectedCount} note(s)
        {selectionStats.trackCount ? ` · ${selectionStats.trackCount} track(s)` : ''}
        {selectionRangeLabel ? ` · ticks ${selectionRangeLabel}` : ''}
        {editorClipboard ? ' · clipboard ready' : ''}
        {editorCommandFeedback?.message ? ` · ${editorCommandFeedback.message}` : ''}
      </Status>

      <PianoRollSelectionInspector
        selectedCount={selectionStats.selectedCount}
        selectedNote={selectedNote}
        hasSelection={selectionStats.selectedCount > 0}
      />

      {selectedNote && (
        <ExpressionControls aria-label="Tie chain controls">
          <span style={{ fontSize: '0.8rem', color: '#374151' }}>
            Note {selectedNote.pitch} · selected {pianoRollNoteIds.length}
          </span>
          <Button
            type="button"
            data-testid="piano-roll-tie"
            disabled={pianoRollNoteIds.length < 2}
            onClick={() => {
              if (!pianoRollTrackId) {
                return;
              }
              applyTieChain(pianoRollTrackId, pianoRollNoteIds);
            }}
          >
            Tie chain
          </Button>
          <Button
            type="button"
            data-testid="piano-roll-untie"
            disabled={!pianoRollNoteIds.length}
            onClick={() => {
              if (!pianoRollTrackId) {
                return;
              }
              removeTieChain(pianoRollTrackId, pianoRollNoteIds);
            }}
          >
            Remove tie
          </Button>
        </ExpressionControls>
      )}

      {!selectedEvents.length && (
        <Status $tone="warn">
          Selected track has no events yet. Click empty grid space to create a snapped note.
        </Status>
      )}
      {notationMessage && (
        <Status $tone={pianoRollNotationStatus === 'error' ? 'error' : 'info'}>
          {notationMessage}
        </Status>
      )}
      <Status data-testid="piano-roll-motif-selection-status">
        Motif selection: {pianoRollNoteIds.length} note(s)
        {motifAuthoringSelection.valid
          ? ` · eligible (${motifAuthoringSelection.barSpan} bar span)`
          : ` · ${motifAuthoringSelection.message}`}
      </Status>
      <Status>
        Scroll horizontally for longer pieces. Drag notes to move/transpose, use the right handle to resize,
        click empty space to create, click the bar ruler or Ctrl+click to place the edit cursor,
        Shift+drag for AI bars, Alt+drag (or Meta+drag) for note box select, Ctrl/Cmd+wheel to zoom.
        Viewport ~{viewport.clientWidth}px wide · showing {visibleNotes.length}/{allNoteGeoms.length} notes.
        Edit cursor: tick {editCursorTick} (bar {currentBar}).
        {' '}
        <label>
          <input
            type="checkbox"
            data-testid="playback-auto-follow"
            checked={Boolean(playbackAutoFollow)}
            onChange={(event) => setPlaybackAutoFollow(event.target.checked)}
          />
          {' '}
          Auto-follow playback
        </label>
        {selectionRect ? ` AI selection: bars ${selectionRect.startBar}-${selectionRect.endBar}.` : ''}
        {motifOverlays.usageRect
          ? ` Motif usage: bars ${motifOverlays.usageRect.startBar}-${motifOverlays.usageRect.endBar}.`
          : ''}
        {motifOverlays.destinationRect
          ? ` Destination: bars ${motifOverlays.destinationRect.startBar}-${motifOverlays.destinationRect.endBar}.`
          : ''}
      </Status>

      <EditorShell
        ref={editorRef}
        tabIndex={0}
        role="application"
        aria-label="Piano roll note grid"
        onKeyDown={handleKeyDown}
      >
        <PitchGutter aria-hidden="true">
          {pitchRows.map((midi) => {
            const pitch = midiToPitch(midi).pitch;
            const isBlack = pitch?.includes('#');
            return (
              <PitchLabel key={midi} $height={metrics.rowHeight} $blackKey={isBlack}>
                {pitch}
              </PitchLabel>
            );
          })}
        </PitchGutter>
        <ScrollArea ref={scrollRef}>
          <GridCanvas
            data-testid="piano-roll-grid"
            $width={metrics.totalWidth}
            $height={metrics.totalHeight}
            $barWidth={barWidth}
            $beatWidth={beatWidth}
            $rowHeight={metrics.rowHeight}
            onPointerDown={handleGridPointerDown}
            aria-label="Piano roll timeline grid"
          >
            <TimelineRuler
              data-testid="piano-roll-timeline-ruler"
              aria-label="Timeline ruler — click to place edit cursor"
              onPointerDown={(event) => {
                event.preventDefault();
                event.stopPropagation();
                placeEditCursorAtClientX(event.clientX, event.currentTarget.parentElement);
              }}
            />
            {barLabels.map((label) => (
              <BarLabel key={label.bar} $left={label.left}>
                Bar {label.bar}
              </BarLabel>
            ))}
            {timelineCues.map((cue) => (
              <TimelineCue
                key={`${cue.kind}:${cue.tick}:${cue.label}`}
                $left={cue.left}
                $kind={cue.kind}
                title={`${cue.kind}: ${cue.label}`}
              >
                {cue.label}
              </TimelineCue>
            ))}

            <PianoRollNoteLayer
              notes={visibleNotes}
              rowHeight={metrics.rowHeight}
              draggingNoteId={draggingNoteId}
              onNotePointerDown={onNotePointerDown}
              onResizePointerDown={onResizePointerDown}
              onNoteClick={onNoteClick}
            />

            <PianoRollOverlayLayer
              composition={editedMusicJson}
              pixelsPerTick={metrics.pixelsPerTick}
              selectionRect={selectionRect}
              motifUsageRect={motifOverlays.usageRect}
              motifDestinationRect={motifOverlays.destinationRect}
              showMotifDestination={
                String(motifDestinationTrackId) === String(pianoRollTrackId)
              }
              dragPreview={dragPreview}
              noteBoxSelectRect={noteBoxSelectRect}
              editCursorTick={editCursorTick}
            />
          </GridCanvas>
        </ScrollArea>
      </EditorShell>
    </Panel>
  );
};

export default PianoRollEditor;
