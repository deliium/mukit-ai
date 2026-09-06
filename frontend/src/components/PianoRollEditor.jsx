import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import { isCanonicalComposition, validateMusicJson } from '../utils/musicJsonValidation.js';
import { secondsToPlaybackPosition } from '../utils/playbackPosition.js';
import {
  SNAP_VALUES,
  buildGridMetrics,
  defaultDurationForSnap,
  midiToPitch,
  pitchToMidi,
  pixelToPitchMidi,
  pixelToTick,
  sanitizeNoteSummary,
  snapTick,
} from '../utils/pianoRollEvents.js';

const CONTEXT_COLORS = ['#94a3b8', '#a78bfa', '#67e8f9', '#fbbf24', '#f472b6', '#86efac'];
const NOTE_NOTATION_DEBOUNCE_MS = 450;
const CURSOR_LOG_THROTTLE_MS = 1000;

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

const NoteBlock = styled.div`
  position: absolute;
  left: ${(props) => props.$left}px;
  top: ${(props) => props.$top}px;
  width: ${(props) => Math.max(4, props.$width)}px;
  height: ${(props) => Math.max(6, props.$height - 2)}px;
  background: ${(props) => props.$color};
  opacity: ${(props) => props.$opacity};
  border-radius: 3px;
  border: 2px solid ${(props) => (props.$selected ? '#111827' : 'transparent')};
  box-sizing: border-box;
  cursor: ${(props) => (props.$editable ? 'grab' : 'default')};
  z-index: ${(props) => (props.$selected ? 4 : props.$editable ? 3 : 1)};

  &:active {
    cursor: ${(props) => (props.$editable ? 'grabbing' : 'default')};
  }
`;

const ResizeHandle = styled.div`
  position: absolute;
  right: 0;
  top: 0;
  width: 8px;
  height: 100%;
  cursor: ew-resize;
  background: rgba(17, 24, 39, 0.25);
`;

const PlaybackCursor = styled.div`
  position: absolute;
  top: 0;
  bottom: 0;
  left: ${(props) => props.$left}px;
  width: 2px;
  background: #ef4444;
  pointer-events: none;
  z-index: 5;
`;

const CONTEXT_TRACK_OPACITY = 0.35;

const PianoRollEditor = () => {
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);
  const pianoRollNoteId = useMusicStore((state) => state.pianoRollNoteId);
  const pianoRollSnap = useMusicStore((state) => state.pianoRollSnap);
  const pianoRollZoom = useMusicStore((state) => state.pianoRollZoom);
  const pianoRollNotationStatus = useMusicStore((state) => state.pianoRollNotationStatus);
  const pianoRollNotationError = useMusicStore((state) => state.pianoRollNotationError);
  const noteEditUndoStack = useMusicStore((state) => state.noteEditUndoStack);
  const noteEditRedoStack = useMusicStore((state) => state.noteEditRedoStack);
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
  const compositionRevision = useMusicStore((state) => state.compositionRevision);
  const selectPianoRollTrack = useMusicStore((state) => state.selectPianoRollTrack);
  const selectPianoRollNote = useMusicStore((state) => state.selectPianoRollNote);
  const setPianoRollSnap = useMusicStore((state) => state.setPianoRollSnap);
  const setPianoRollZoom = useMusicStore((state) => state.setPianoRollZoom);
  const createNote = useMusicStore((state) => state.createNote);
  const updateNote = useMusicStore((state) => state.updateNote);
  const deleteNote = useMusicStore((state) => state.deleteNote);
  const undoNoteEdit = useMusicStore((state) => state.undoNoteEdit);
  const redoNoteEdit = useMusicStore((state) => state.redoNoteEdit);
  const refreshMusicXmlFromEditedComposition = useMusicStore(
    (state) => state.refreshMusicXmlFromEditedComposition,
  );

  const editorRef = useRef(null);
  const scrollRef = useRef(null);
  const dragRef = useRef(null);
  const debounceRef = useRef(null);
  const lastCursorLogRef = useRef(0);
  const [viewportWidth, setViewportWidth] = useState(0);

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

  useEffect(() => {
    if (!canonical || !validation.valid) {
      console.warn('[PianoRollEditor] Unsupported or invalid composition for piano roll', {
        canonical,
        valid: validation.valid,
        message: validation.message,
      });
      return;
    }
    console.debug('[PianoRollEditor] Mount/render summary', {
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
    console.debug('[PianoRollEditor] Grid metrics', {
      barTicks: metrics.barTicks,
      snapTicks: metrics.snapTicks,
      visiblePitchRange: `${metrics.minMidi}-${metrics.maxMidi}`,
      totalWidth: metrics.totalWidth,
      zoom: pianoRollZoom,
    });
    if (metrics.warning) {
      console.warn('[PianoRollEditor] Fallback/incomplete grid metrics', { warning: metrics.warning });
    }
    return undefined;
  }, [metrics, pianoRollZoom]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) {
      return undefined;
    }
    const update = () => setViewportWidth(node.clientWidth);
    update();
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null;
    observer?.observe(node);
    window.addEventListener('resize', update);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', update);
    };
  }, [canonical, validation.valid]);

  useEffect(() => {
    if (!canonical || !validation.valid) {
      return undefined;
    }
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    console.debug('[PianoRollEditor] Notation debounce scheduled', {
      compositionRevision: compositionRevision.slice(0, 48),
      delayMs: NOTE_NOTATION_DEBOUNCE_MS,
    });
    debounceRef.current = setTimeout(() => {
      console.debug('[PianoRollEditor] Notation debounce completed; refreshing MusicXML', {
        compositionRevision: compositionRevision.slice(0, 48),
      });
      refreshMusicXmlFromEditedComposition();
    }, NOTE_NOTATION_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        console.debug('[PianoRollEditor] Notation debounce cancelled');
      }
    };
  }, [canonical, validation.valid, compositionRevision, refreshMusicXmlFromEditedComposition]);

  const contextCounts = useMemo(() => {
    const counts = {};
    tracks.forEach((track) => {
      if (String(track.id) === String(pianoRollTrackId)) {
        return;
      }
      counts[track.id] = Array.isArray(track.events) ? track.events.length : 0;
    });
    return counts;
  }, [tracks, pianoRollTrackId]);

  useEffect(() => {
    if (Object.keys(contextCounts).length) {
      console.debug('[PianoRollEditor] Context note counts by track', contextCounts);
    }
  }, [contextCounts]);

  const cursorTick = useMemo(() => {
    if (!editedMusicJson || playbackStatus === 'idle') {
      return null;
    }
    const position = secondsToPlaybackPosition(playbackSeconds, {
      tempo: editedMusicJson.tempo,
      ticksPerQuarter: editedMusicJson.ticks_per_quarter,
      timeSignature: editedMusicJson.time_signature,
    });
    return position.tick;
  }, [editedMusicJson, playbackStatus, playbackSeconds]);

  useEffect(() => {
    if (cursorTick === null || !metrics) {
      return;
    }
    const now = Date.now();
    if (now - lastCursorLogRef.current < CURSOR_LOG_THROTTLE_MS) {
      return;
    }
    lastCursorLogRef.current = now;
    console.debug('[PianoRollEditor] Playback cursor', {
      tick: Math.round(cursorTick),
      px: Math.round(cursorTick * metrics.pixelsPerTick),
      playbackStatus,
    });
  }, [cursorTick, metrics, playbackStatus]);

  useEffect(() => {
    if (playbackStatus === 'playing') {
      console.info('[PianoRollEditor] Playback cursor activated');
    } else if (playbackStatus === 'idle' || playbackStatus === 'paused') {
      console.info('[PianoRollEditor] Playback cursor deactivated/paused', { playbackStatus });
    }
  }, [playbackStatus]);

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
    if (!metrics || !metrics.barTicks) {
      return [];
    }
    const labels = [];
    for (let bar = 0; bar < (metrics.barCount || 0); bar += 1) {
      labels.push({
        bar: bar + 1,
        left: bar * metrics.barTicks * metrics.pixelsPerTick + 4,
      });
    }
    return labels;
  }, [metrics]);

  const schedulePointerEnd = useCallback(() => {
    const interaction = dragRef.current;
    if (!interaction) {
      return;
    }
    window.removeEventListener('pointermove', interaction.onMove);
    window.removeEventListener('pointerup', interaction.onUp);
    window.removeEventListener('pointercancel', interaction.onUp);
    dragRef.current = null;
  }, []);

  useEffect(() => () => schedulePointerEnd(), [schedulePointerEnd]);

  const handleDeleteSelected = () => {
    if (!pianoRollTrackId || !pianoRollNoteId) {
      console.warn('[PianoRollEditor] Delete ignored; no note selected');
      return;
    }
    console.info('[PianoRollEditor] Note delete requested', {
      trackId: pianoRollTrackId,
      noteId: pianoRollNoteId,
    });
    deleteNote(pianoRollTrackId, pianoRollNoteId);
  };

  const handleKeyDown = (event) => {
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      handleDeleteSelected();
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
      event.preventDefault();
      if (event.shiftKey) {
        redoNoteEdit();
      } else {
        undoNoteEdit();
      }
    }
  };

  const beginDrag = (mode, note, event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!metrics || !pianoRollTrackId) {
      return;
    }
    const startMidi = pitchToMidi(note.pitch).midi;
    if (startMidi === null) {
      return;
    }
    selectPianoRollNote(note.id);
    const historySnapshot = {
      editedMusicJson: useMusicStore.getState().editedMusicJson,
      pianoRollTrackId: useMusicStore.getState().pianoRollTrackId,
      pianoRollNoteId: note.id,
    };
    let historyCommitted = false;
    const origin = {
      mode,
      noteId: note.id,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originStartTick: note.start_tick,
      originDurationTicks: note.duration_ticks,
      originMidi: startMidi,
    };

    const commitHistoryOnce = () => {
      if (historyCommitted) {
        return { skipHistory: true };
      }
      historyCommitted = true;
      return { historySnapshot };
    };

    const onMove = (moveEvent) => {
      if (!dragRef.current || moveEvent.pointerId !== origin.pointerId) {
        return;
      }
      const deltaX = moveEvent.clientX - origin.startClientX;
      const deltaY = moveEvent.clientY - origin.startClientY;
      const deltaTicks = pixelToTick(deltaX, metrics.pixelsPerTick).tick;
      const deltaRows = Math.round(deltaY / metrics.rowHeight);
      const historyOptions = commitHistoryOnce();

      if (origin.mode === 'move') {
        const snapped = snapTick(origin.originStartTick + deltaTicks, metrics.snapTicks, {
          maxTick: Math.max(0, metrics.durationTicks - origin.originDurationTicks),
        });
        const nextMidi = origin.originMidi - deltaRows;
        const pitch = midiToPitch(nextMidi).pitch;
        if (!pitch) {
          return;
        }
        if (snapped.clamped) {
          console.warn('[PianoRollEditor] Move clamped at composition/MIDI bounds', {
            noteId: origin.noteId,
            start_tick: snapped.tick,
            pitch,
          });
        }
        console.debug('[PianoRollEditor] Drag move', {
          noteId: origin.noteId,
          old: sanitizeNoteSummary(note),
          nextStart: snapped.tick,
          nextPitch: pitch,
          clamped: snapped.clamped,
        });
        updateNote(pianoRollTrackId, origin.noteId, {
          start_tick: snapped.tick,
          pitch,
        }, historyOptions);
        return;
      }

      if (origin.mode === 'resize') {
        const rawDuration = origin.originDurationTicks + deltaTicks;
        const snappedDuration = snapTick(rawDuration, metrics.snapTicks, {
          maxTick: Math.max(metrics.snapTicks, metrics.durationTicks - origin.originStartTick),
          mode: 'nearest',
        });
        const durationTicks = Math.max(metrics.snapTicks || 1, snappedDuration.tick);
        if (snappedDuration.clamped || durationTicks !== rawDuration) {
          console.warn('[PianoRollEditor] Resize clamped to min duration or composition end', {
            noteId: origin.noteId,
            durationTicks,
          });
        }
        console.debug('[PianoRollEditor] Drag resize', {
          noteId: origin.noteId,
          oldDuration: origin.originDurationTicks,
          nextDuration: durationTicks,
          clamped: snappedDuration.clamped,
        });
        updateNote(pianoRollTrackId, origin.noteId, { duration_ticks: durationTicks }, historyOptions);
      }
    };

    const onUp = (upEvent) => {
      if (!dragRef.current || upEvent.pointerId !== origin.pointerId) {
        return;
      }
      if (origin.mode === 'move') {
        console.info('[PianoRollEditor] Note move/transpose completed', {
          trackId: pianoRollTrackId,
          noteId: origin.noteId,
        });
      } else {
        console.info('[PianoRollEditor] Note duration edit completed', {
          trackId: pianoRollTrackId,
          noteId: origin.noteId,
        });
      }
      schedulePointerEnd();
    };

    dragRef.current = { onMove, onUp, ...origin };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    console.debug('[PianoRollEditor] Drag started', {
      mode,
      noteId: note.id,
      pitch: note.pitch,
      start_tick: note.start_tick,
      duration_ticks: note.duration_ticks,
    });
  };

  const handleGridPointerDown = (event) => {
    if (!metrics || !pianoRollTrackId || event.target !== event.currentTarget) {
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - bounds.left;
    const y = event.clientY - bounds.top;
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
    console.debug('[PianoRollEditor] Pointer create coordinates', {
      x,
      y,
      snappedStart: snappedStart.tick,
      pitch,
      durationTicks,
    });
    const note = createNote(pianoRollTrackId, {
      pitch,
      start_tick: snappedStart.tick,
      duration_ticks: durationTicks,
      velocity: 90,
    });
    if (note) {
      console.info('[PianoRollEditor] Note created', sanitizeNoteSummary(note));
      selectPianoRollNote(note.id);
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
          Piano roll supports canonical composition.v1 only. Legacy JSON remains editable in the JSON editor.
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
          <ControlGroup htmlFor="piano-roll-zoom">
            Zoom
            <Select
              id="piano-roll-zoom"
              value={String(pianoRollZoom)}
              aria-label="Horizontal zoom"
              onChange={(event) => setPianoRollZoom(Number(event.target.value))}
            >
              <option value="0.02">Far</option>
              <option value="0.05">Default</option>
              <option value="0.1">Close</option>
              <option value="0.18">Detail</option>
            </Select>
          </ControlGroup>
          <Button type="button" onClick={() => undoNoteEdit()} disabled={!noteEditUndoStack.length} aria-label="Undo note edit">
            Undo
          </Button>
          <Button type="button" onClick={() => redoNoteEdit()} disabled={!noteEditRedoStack.length} aria-label="Redo note edit">
            Redo
          </Button>
          <Button
            type="button"
            $danger
            onClick={handleDeleteSelected}
            disabled={!pianoRollNoteId}
            aria-label="Delete selected note"
          >
            Delete note
          </Button>
        </Controls>
      </HeaderRow>

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
      <Status>
        Scroll horizontally for longer pieces. Drag notes to move/transpose, use the right handle to resize,
        click empty space to create. Viewport ~{viewportWidth}px wide.
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
            $width={metrics.totalWidth}
            $height={metrics.totalHeight}
            $barWidth={barWidth}
            $beatWidth={beatWidth}
            $rowHeight={metrics.rowHeight}
            onPointerDown={handleGridPointerDown}
            aria-label="Piano roll timeline grid"
          >
            {barLabels.map((label) => (
              <BarLabel key={label.bar} $left={label.left}>
                Bar {label.bar}
              </BarLabel>
            ))}

            {tracks.map((track, trackIndex) => {
              const editable = String(track.id) === String(pianoRollTrackId);
              const color = editable ? '#4f46e5' : CONTEXT_COLORS[trackIndex % CONTEXT_COLORS.length];
              const events = Array.isArray(track.events) ? track.events : [];
              return events.map((event) => {
                const midi = pitchToMidi(event.pitch).midi;
                if (midi === null || midi < metrics.minMidi || midi > metrics.maxMidi) {
                  return null;
                }
                const top = (metrics.maxMidi - midi) * metrics.rowHeight;
                const left = event.start_tick * metrics.pixelsPerTick;
                const width = event.duration_ticks * metrics.pixelsPerTick;
                const selected = editable && String(event.id) === String(pianoRollNoteId);
                return (
                  <NoteBlock
                    key={`${track.id}:${event.id || `${event.pitch}-${event.start_tick}`}`}
                    $left={left}
                    $top={top}
                    $width={width}
                    $height={metrics.rowHeight}
                    $color={color}
                    $opacity={editable ? 0.95 : CONTEXT_TRACK_OPACITY}
                    $selected={selected}
                    $editable={editable}
                    role={editable ? 'button' : 'presentation'}
                    aria-label={editable ? `Note ${event.pitch} at tick ${event.start_tick}` : undefined}
                    tabIndex={editable ? 0 : -1}
                    onPointerDown={(pointerEvent) => {
                      if (!editable) {
                        return;
                      }
                      beginDrag('move', event, pointerEvent);
                    }}
                    onClick={(clickEvent) => {
                      if (!editable) {
                        return;
                      }
                      clickEvent.stopPropagation();
                      selectPianoRollNote(event.id);
                      console.info('[PianoRollEditor] Note selected', {
                        trackId: track.id,
                        noteId: event.id,
                        pitch: event.pitch,
                        start_tick: event.start_tick,
                        duration_ticks: event.duration_ticks,
                      });
                    }}
                  >
                    {editable && (
                      <ResizeHandle
                        aria-label={`Resize note ${event.pitch}`}
                        onPointerDown={(pointerEvent) => beginDrag('resize', event, pointerEvent)}
                      />
                    )}
                  </NoteBlock>
                );
              });
            })}

            {cursorTick !== null && (
              <PlaybackCursor $left={cursorTick * metrics.pixelsPerTick} aria-hidden="true" />
            )}
          </GridCanvas>
        </ScrollArea>
      </EditorShell>
    </Panel>
  );
};

export default PianoRollEditor;
