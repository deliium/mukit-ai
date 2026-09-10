import { useCallback, useEffect, useRef, useState } from 'react';
import { useMusicStore } from '../../store/musicStore.js';
import { makeNoteRef } from '../../utils/compositionEditorSelection.js';
import {
  midiToPitch,
  pitchToMidi,
  pixelToTick,
  sanitizeNoteSummary,
  snapTick,
} from '../../utils/pianoRollEvents.js';
import { createAppLogger } from '../../utils/appLogger.js';
import { beginDragPerf, endDragPerf } from '../../utils/editorPerfInstrumentation.js';

const logger = createAppLogger('pianoRoll.drag');

/**
 * Pointer move/resize with local preview only; one canonical updateNote on pointer-up.
 * Escape / cancel discards preview without mutating the store.
 *
 * Selection modifiers (Shift / Ctrl / Meta) must not begin a drag or collapse selection
 * on pointerdown — those clicks are handled by the note-layer click path instead.
 */
export function usePianoRollDrag({ metrics, trackId }) {
  const updateNote = useMusicStore((state) => state.updateNote);
  const setEditorSelection = useMusicStore((state) => state.setEditorSelection);
  const dragRef = useRef(null);
  const [dragPreview, setDragPreview] = useState(null);

  const clearListeners = useCallback(() => {
    const interaction = dragRef.current;
    if (!interaction) {
      return;
    }
    window.removeEventListener('pointermove', interaction.onMove);
    window.removeEventListener('pointerup', interaction.onUp);
    window.removeEventListener('pointercancel', interaction.onCancel);
    window.removeEventListener('keydown', interaction.onKeyDown);
    dragRef.current = null;
  }, []);

  const cancelDrag = useCallback((reason = 'cancel') => {
    const interaction = dragRef.current;
    if (!interaction) {
      return;
    }
    logger.warn('Drag cancelled without commit', {
      reason,
      mode: interaction.mode,
      noteId: interaction.noteId,
      durationMs: Date.now() - interaction.startedAt,
    });
    clearListeners();
    setDragPreview(null);
    endDragPerf({ committed: false });
  }, [clearListeners]);

  useEffect(() => () => {
    clearListeners();
  }, [clearListeners]);

  const beginDrag = useCallback((mode, note, event, layout = {}) => {
    // Defer to click handlers for multi-select modifiers so pointerdown does not
    // replace the selection before Shift-range / Ctrl-toggle runs.
    if (event.shiftKey || event.ctrlKey || event.metaKey) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (!metrics || !trackId || !note) {
      return;
    }
    beginDragPerf();
    const startMidi = pitchToMidi(note.pitch).midi;
    if (startMidi === null) {
      return;
    }

    const noteTrackId = layout.trackId || trackId;
    const ref = makeNoteRef(noteTrackId, note.id);
    if (ref) {
      setEditorSelection({ refs: [ref], primary: ref, anchor: ref });
    }

    const origin = {
      mode,
      noteId: note.id,
      trackId: noteTrackId,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      originStartTick: note.start_tick,
      originDurationTicks: note.duration_ticks,
      originMidi: startMidi,
      originLeft: Number(layout.left) || note.start_tick * metrics.pixelsPerTick,
      originTop: Number.isFinite(Number(layout.top))
        ? Number(layout.top)
        : (metrics.maxMidi - startMidi) * metrics.rowHeight,
      originWidth: Number(layout.width) || note.duration_ticks * metrics.pixelsPerTick,
      originHeight: Number(layout.height) || metrics.rowHeight,
      color: layout.color || '#4f46e5',
      startedAt: Date.now(),
      lastClamped: false,
    };

    const computePreview = (clientX, clientY) => {
      const deltaX = clientX - origin.startClientX;
      const deltaY = clientY - origin.startClientY;
      const deltaTicks = pixelToTick(deltaX, metrics.pixelsPerTick).tick;
      const deltaRows = Math.round(deltaY / metrics.rowHeight);

      if (origin.mode === 'move') {
        const snapped = snapTick(origin.originStartTick + deltaTicks, metrics.snapTicks, {
          maxTick: Math.max(0, metrics.durationTicks - origin.originDurationTicks),
        });
        const nextMidi = Math.max(
          0,
          Math.min(127, origin.originMidi - deltaRows),
        );
        const pitch = midiToPitch(nextMidi).pitch;
        if (!pitch) {
          return null;
        }
        const left = snapped.tick * metrics.pixelsPerTick;
        const top = (metrics.maxMidi - nextMidi) * metrics.rowHeight;
        return {
          mode: 'move',
          noteId: origin.noteId,
          left,
          top,
          width: origin.originWidth,
          height: Math.max(6, origin.originHeight - 2),
          color: origin.color,
          start_tick: snapped.tick,
          duration_ticks: origin.originDurationTicks,
          pitch,
          pitchMidi: nextMidi,
          clamped: Boolean(snapped.clamped) || nextMidi !== origin.originMidi - deltaRows,
        };
      }

      if (origin.mode === 'resize') {
        const rawDuration = origin.originDurationTicks + deltaTicks;
        const snappedDuration = snapTick(rawDuration, metrics.snapTicks, {
          maxTick: Math.max(metrics.snapTicks, metrics.durationTicks - origin.originStartTick),
          mode: 'nearest',
        });
        const durationTicks = Math.max(metrics.snapTicks || 1, snappedDuration.tick);
        return {
          mode: 'resize',
          noteId: origin.noteId,
          left: origin.originLeft,
          top: origin.originTop,
          width: durationTicks * metrics.pixelsPerTick,
          height: Math.max(6, origin.originHeight - 2),
          color: origin.color,
          start_tick: origin.originStartTick,
          duration_ticks: durationTicks,
          pitch: note.pitch,
          pitchMidi: origin.originMidi,
          clamped: Boolean(snappedDuration.clamped) || durationTicks !== rawDuration,
        };
      }

      return null;
    };

    const onMove = (moveEvent) => {
      if (!dragRef.current || moveEvent.pointerId !== origin.pointerId) {
        return;
      }
      const preview = computePreview(moveEvent.clientX, moveEvent.clientY);
      if (!preview) {
        return;
      }
      dragRef.current.latestPreview = preview;
      if (preview.clamped && !dragRef.current.lastClamped) {
        dragRef.current.lastClamped = true;
        logger.warn('Drag preview clamped at composition/MIDI bounds', {
          mode: origin.mode,
          noteId: origin.noteId,
        });
      } else if (!preview.clamped) {
        dragRef.current.lastClamped = false;
      }
      setDragPreview(preview);
    };

    const finish = (upEvent, { commit }) => {
      if (!dragRef.current || upEvent.pointerId !== origin.pointerId) {
        return;
      }
      const preview = dragRef.current.latestPreview || computePreview(upEvent.clientX, upEvent.clientY);
      const durationMs = Date.now() - origin.startedAt;
      clearListeners();
      setDragPreview(null);

      if (!commit || !preview) {
        endDragPerf({ committed: false });
        logger.warn('Drag ended without commit', {
          mode: origin.mode,
          noteId: origin.noteId,
          durationMs,
        });
        return;
      }

      const historySnapshot = {
        editedMusicJson: useMusicStore.getState().editedMusicJson,
        pianoRollTrackId: useMusicStore.getState().pianoRollTrackId,
        pianoRollNoteId: origin.noteId,
        pianoRollNoteIds: [origin.noteId],
        editorSelectionRefs: ref ? [{ ...ref }] : [],
        editorSelectionPrimary: ref ? { ...ref } : null,
        editCursorTick: useMusicStore.getState().editCursorTick,
      };

      let patch;
      if (origin.mode === 'move') {
        patch = { start_tick: preview.start_tick, pitch: preview.pitch };
      } else {
        patch = { duration_ticks: preview.duration_ticks };
      }

      const unchanged = origin.mode === 'move'
        ? preview.start_tick === origin.originStartTick && preview.pitchMidi === origin.originMidi
        : preview.duration_ticks === origin.originDurationTicks;

      if (unchanged) {
        endDragPerf({ committed: false });
        logger.debug('Drag completed with no musical change', {
          mode: origin.mode,
          noteId: origin.noteId,
          durationMs,
        });
        return;
      }

      const result = updateNote(origin.trackId, origin.noteId, patch, { historySnapshot });
      if (!result) {
        endDragPerf({ committed: false });
        logger.warn('Drag commit rejected by store', {
          mode: origin.mode,
          noteId: origin.noteId,
          durationMs,
        });
        return;
      }

      endDragPerf({ committed: true });
      logger.info(origin.mode === 'move' ? 'Note move/transpose committed' : 'Note duration edit committed', {
        trackId: origin.trackId,
        noteId: origin.noteId,
        durationMs,
        clamped: Boolean(preview.clamped),
        summary: sanitizeNoteSummary(result),
      });
    };

    const onUp = (upEvent) => finish(upEvent, { commit: true });
    const onCancel = (upEvent) => finish(upEvent, { commit: false });
    const onKeyDown = (keyEvent) => {
      if (keyEvent.key === 'Escape') {
        keyEvent.preventDefault();
        cancelDrag('escape');
      }
    };

    dragRef.current = {
      ...origin,
      onMove,
      onUp,
      onCancel,
      onKeyDown,
      latestPreview: null,
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
    window.addEventListener('keydown', onKeyDown);

    setDragPreview({
      mode,
      noteId: note.id,
      left: origin.originLeft,
      top: origin.originTop,
      width: origin.originWidth,
      height: Math.max(6, origin.originHeight - 2),
      color: origin.color,
      start_tick: note.start_tick,
      duration_ticks: note.duration_ticks,
      pitch: note.pitch,
      pitchMidi: startMidi,
      clamped: false,
    });

    logger.debug('Drag started', {
      mode,
      noteId: note.id,
      start_tick: note.start_tick,
      duration_ticks: note.duration_ticks,
    });
  }, [metrics, trackId, updateNote, setEditorSelection, clearListeners, cancelDrag]);

  return {
    beginDrag,
    cancelDrag,
    dragPreview,
    draggingNoteId: dragPreview?.noteId ?? null,
  };
}

export default usePianoRollDrag;
