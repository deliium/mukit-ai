import React, { useEffect, useMemo, useRef } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../../store/musicStore.js';
import { secondsToPlaybackPosition } from '../../utils/playbackPosition.js';
import { createAppLogger } from '../../utils/appLogger.js';
import {
  buildRecoveryBoundConfidenceGeoms,
  buildRecoveryProvisionalGeoms,
} from '../../utils/audioRecoveryOverlay.js';

const logger = createAppLogger('pianoRoll.overlay');
const CURSOR_LOG_THROTTLE_MS = 1000;

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

const EditCursor = styled.div`
  position: absolute;
  top: 0;
  bottom: 0;
  left: ${(props) => props.$left}px;
  width: 2px;
  background: #0ea5e9;
  box-shadow: 0 0 0 1px rgba(14, 165, 233, 0.35);
  pointer-events: none;
  z-index: 4;
`;

const LoopOverlay = styled.div`
  position: absolute;
  top: 0;
  bottom: 0;
  left: ${(props) => props.$left}px;
  width: ${(props) => props.$width}px;
  background: rgba(16, 185, 129, 0.1);
  border-left: 2px solid rgba(5, 150, 105, 0.75);
  border-right: 2px solid rgba(5, 150, 105, 0.75);
  pointer-events: none;
  z-index: 2;
`;

const SelectionOverlay = styled.div`
  position: absolute;
  top: 0;
  bottom: 0;
  left: ${(props) => props.$left}px;
  width: ${(props) => props.$width}px;
  background: rgba(79, 70, 229, 0.12);
  border-left: 2px solid rgba(79, 70, 229, 0.55);
  border-right: 2px solid rgba(79, 70, 229, 0.55);
  pointer-events: none;
  z-index: 2;
`;

const MotifUsageOverlay = styled(SelectionOverlay)`
  background: rgba(245, 158, 11, 0.14);
  border-left-color: rgba(217, 119, 6, 0.7);
  border-right-color: rgba(217, 119, 6, 0.7);
  z-index: 3;
`;

const MotifDestinationOverlay = styled(SelectionOverlay)`
  background: rgba(16, 185, 129, 0.12);
  border-left-color: rgba(5, 150, 105, 0.65);
  border-right-color: rgba(5, 150, 105, 0.65);
  z-index: 3;
`;

const DragGhost = styled.div`
  position: absolute;
  left: ${(props) => props.$left}px;
  top: ${(props) => props.$top}px;
  width: ${(props) => Math.max(4, props.$width)}px;
  height: ${(props) => Math.max(6, props.$height)}px;
  background: ${(props) => props.$color};
  opacity: 0.75;
  border-radius: 3px;
  border: 2px solid #111827;
  box-sizing: border-box;
  pointer-events: none;
  z-index: 6;
`;

const NoteBoxSelectOverlay = styled.div`
  position: absolute;
  left: ${(props) => props.$left}px;
  top: ${(props) => props.$top}px;
  width: ${(props) => Math.max(1, props.$width)}px;
  height: ${(props) => Math.max(1, props.$height)}px;
  background: rgba(14, 165, 233, 0.12);
  border: 1px solid rgba(14, 165, 233, 0.75);
  box-sizing: border-box;
  pointer-events: none;
  z-index: 7;
`;

const ProvisionalNote = styled.div`
  position: absolute;
  left: ${(props) => props.$left}px;
  top: ${(props) => props.$top}px;
  width: ${(props) => Math.max(3, props.$width)}px;
  height: ${(props) => Math.max(4, props.$height)}px;
  border-radius: 2px;
  box-sizing: border-box;
  pointer-events: none;
  z-index: 3;
  opacity: 0.85;
  background: ${(props) => (
    props.$low
      ? `repeating-linear-gradient(45deg, ${props.$fill}, ${props.$fill} 4px, rgba(0,0,0,0.12) 4px, rgba(0,0,0,0.12) 8px)`
      : props.$fill
  )};
  border: 1px solid ${(props) => (props.$low ? '#b45309' : '#d97706')};
`;

const BoundConfidenceNote = styled.div`
  position: absolute;
  left: ${(props) => props.$left}px;
  top: ${(props) => props.$top}px;
  width: ${(props) => Math.max(3, props.$width)}px;
  height: ${(props) => Math.max(4, props.$height)}px;
  border-radius: 3px;
  box-sizing: border-box;
  pointer-events: none;
  z-index: 3;
  background: transparent;
  border: 2px ${(props) => (props.$userEdited ? 'solid' : 'dashed')} ${(props) => (
    props.$low ? '#b45309' : props.$userEdited ? '#6366f1' : '#d97706'
  )};
  box-shadow: ${(props) => (
    props.$low
      ? 'inset 0 0 0 1px rgba(245, 158, 11, 0.35)'
      : 'none'
  )};
`;

/**
 * Transient overlays: AI/motif selection, note box-select marquee, edit/playback cursors, drag ghost.
 * Subscribes to playbackSeconds so the note layer stays stable.
 *
 * Gesture scheme (documented for Task 5):
 * - Shift+drag on empty grid → AI bar-range selection (unchanged)
 * - Alt+drag (or Meta+drag) on empty grid → note box select marquee
 */
function PianoRollOverlayLayer({
  composition,
  pixelsPerTick,
  selectionRect,
  motifUsageRect,
  motifDestinationRect,
  showMotifDestination,
  dragPreview,
  noteBoxSelectRect = null,
  editCursorTick = null,
  pitchMidiMax = null,
  pitchMidiMin = null,
  rowHeight = 16,
}) {
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
  const playbackLoop = useMusicStore((state) => state.playbackLoop);
  const audioPhase = useMusicStore((state) => state.audioPhase);
  const audioPreview = useMusicStore((state) => state.audioPreview);
  const audioConfidenceThreshold = useMusicStore((state) => state.audioConfidenceThreshold);
  const audioSelectedProvisionalIds = useMusicStore((state) => state.audioSelectedProvisionalIds);
  const recoveryPhase = useMusicStore((state) => state.recoveryPhase);
  const recoveryPreview = useMusicStore((state) => state.recoveryPreview);
  const recoverySelectedProvisionalIds = useMusicStore((state) => state.recoverySelectedProvisionalIds);
  const recoveryConfidenceThreshold = useMusicStore((state) => state.recoveryConfidenceThreshold);
  const recoveryOverlay = useMusicStore((state) => state.recoveryOverlay);
  const lastCursorLogRef = useRef(0);

  const cursorTick = useMemo(() => {
    if (!composition || playbackStatus === 'idle') {
      return null;
    }
    const position = secondsToPlaybackPosition(playbackSeconds, {
      tempo: composition.tempo,
      ticksPerQuarter: composition.ticks_per_quarter,
      timeSignature: composition.time_signature,
      composition,
    });
    return position.tick;
  }, [composition, playbackStatus, playbackSeconds]);

  const loopRect = useMemo(() => {
    if (!playbackLoop?.enabled || !Number.isFinite(pixelsPerTick) || pixelsPerTick <= 0) {
      return null;
    }
    const start = Number(playbackLoop.startTick);
    const end = Number(playbackLoop.endTick);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      return null;
    }
    return {
      left: Math.max(0, start) * pixelsPerTick,
      width: Math.max(1, (end - start) * pixelsPerTick),
    };
  }, [playbackLoop, pixelsPerTick]);

  const editCursorLeft = useMemo(() => {
    const tick = Number(editCursorTick);
    if (!Number.isFinite(tick) || !Number.isFinite(pixelsPerTick) || pixelsPerTick <= 0) {
      return null;
    }
    return Math.max(0, tick) * pixelsPerTick;
  }, [editCursorTick, pixelsPerTick]);

  const layout = useMemo(() => ({
    pixelsPerTick,
    pitchMidiMax,
    pitchMidiMin,
    rowHeight,
  }), [pixelsPerTick, pitchMidiMax, pitchMidiMin, rowHeight]);

  const provisionalNotes = useMemo(() => {
    if (audioPhase !== 'review' || !audioPreview || !Array.isArray(audioPreview.notes)) {
      return [];
    }
    if (
      !Number.isFinite(pixelsPerTick)
      || pixelsPerTick <= 0
      || pitchMidiMax == null
      || pitchMidiMin == null
    ) {
      return [];
    }
    const selected = new Set((audioSelectedProvisionalIds || []).map(String));
    const threshold = Number(audioConfidenceThreshold) || 0.5;
    const height = Math.max(4, Number(rowHeight) || 16);
    return audioPreview.notes
      .filter((note) => selected.has(String(note.provisional_id)))
      .map((note) => {
        const midi = Math.round(Number(note.pitch));
        if (midi < pitchMidiMin || midi > pitchMidiMax) {
          return null;
        }
        const start = Math.max(0, Number(note.start_tick) || 0);
        const duration = Math.max(1, Number(note.duration_ticks) || 1);
        return {
          id: note.provisional_id,
          left: start * pixelsPerTick,
          width: duration * pixelsPerTick,
          top: (pitchMidiMax - midi) * height,
          height: height - 2,
          low: Number(note.confidence) < threshold,
          fill: 'rgba(245, 158, 11, 0.55)',
        };
      })
      .filter(Boolean);
  }, [
    audioPhase,
    audioPreview,
    audioSelectedProvisionalIds,
    audioConfidenceThreshold,
    pixelsPerTick,
    pitchMidiMax,
    pitchMidiMin,
    rowHeight,
  ]);

  const recoveryProvisionalNotes = useMemo(() => {
    if (recoveryPhase !== 'review' || !recoveryPreview || !Array.isArray(recoveryPreview.notes)) {
      return [];
    }
    return buildRecoveryProvisionalGeoms(
      recoveryPreview.notes,
      recoverySelectedProvisionalIds,
      {
        ...layout,
        threshold: Number(recoveryConfidenceThreshold) || 0.5,
      },
    );
  }, [
    recoveryPhase,
    recoveryPreview,
    recoverySelectedProvisionalIds,
    recoveryConfidenceThreshold,
    layout,
  ]);

  const recoveryBoundNotes = useMemo(() => {
    if (!Array.isArray(recoveryOverlay) || !recoveryOverlay.length) {
      return [];
    }
    return buildRecoveryBoundConfidenceGeoms(composition, recoveryOverlay, {
      ...layout,
      threshold: Number(recoveryConfidenceThreshold) || 0.5,
    });
  }, [composition, recoveryOverlay, recoveryConfidenceThreshold, layout]);

  useEffect(() => {
    if (cursorTick === null || !Number.isFinite(pixelsPerTick)) {
      return;
    }
    const now = Date.now();
    if (now - lastCursorLogRef.current < CURSOR_LOG_THROTTLE_MS) {
      return;
    }
    lastCursorLogRef.current = now;
    logger.debug('Playback cursor', {
      tick: Math.round(cursorTick),
      px: Math.round(cursorTick * pixelsPerTick),
      playbackStatus,
    });
  }, [cursorTick, pixelsPerTick, playbackStatus]);

  useEffect(() => {
    if (playbackStatus === 'playing') {
      logger.info('Playback cursor activated');
    } else if (playbackStatus === 'idle' || playbackStatus === 'paused') {
      logger.info('Playback cursor deactivated/paused', { playbackStatus });
    }
  }, [playbackStatus]);

  return (
    <div data-testid="piano-roll-overlay-layer" aria-hidden="true">
      {loopRect && (
        <LoopOverlay
          data-testid="piano-roll-loop-overlay"
          $left={loopRect.left}
          $width={loopRect.width}
        />
      )}
      {selectionRect && (
        <SelectionOverlay
          data-testid="piano-roll-ai-selection-overlay"
          $left={selectionRect.left}
          $width={selectionRect.width}
        />
      )}
      {motifUsageRect && (
        <MotifUsageOverlay
          data-testid="piano-roll-motif-usage-overlay"
          $left={motifUsageRect.left}
          $width={motifUsageRect.width}
        />
      )}
      {showMotifDestination && motifDestinationRect ? (
        <MotifDestinationOverlay
          data-testid="piano-roll-motif-destination-overlay"
          $left={motifDestinationRect.left}
          $width={motifDestinationRect.width}
        />
      ) : null}
      {provisionalNotes.map((note) => (
        <ProvisionalNote
          key={`audio:${note.id}`}
          data-testid="piano-roll-audio-provisional-note"
          data-low-confidence={note.low ? 'true' : 'false'}
          $left={note.left}
          $top={note.top}
          $width={note.width}
          $height={note.height}
          $low={note.low}
          $fill={note.fill}
        />
      ))}
      {recoveryProvisionalNotes.map((note) => (
        <ProvisionalNote
          key={`recovery:${note.id}`}
          data-testid="piano-roll-recovery-provisional-note"
          data-stem={note.stem || ''}
          data-low-confidence={note.low ? 'true' : 'false'}
          $left={note.left}
          $top={note.top}
          $width={note.width}
          $height={note.height}
          $low={note.low}
          $fill={note.fill}
        />
      ))}
      {recoveryBoundNotes.map((note) => (
        <BoundConfidenceNote
          key={`bound:${note.id}`}
          data-testid="piano-roll-recovery-bound-note"
          data-event-id={note.id}
          data-stem={note.stem || ''}
          data-status={note.status}
          data-low-confidence={note.low ? 'true' : 'false'}
          $left={note.left}
          $top={note.top}
          $width={note.width}
          $height={note.height}
          $low={note.low}
          $userEdited={note.status === 'user_edited'}
        />
      ))}
      {dragPreview && (
        <DragGhost
          data-testid="piano-roll-drag-ghost"
          $left={dragPreview.left}
          $top={dragPreview.top}
          $width={dragPreview.width}
          $height={dragPreview.height}
          $color={dragPreview.color}
        />
      )}
      {noteBoxSelectRect && (
        <NoteBoxSelectOverlay
          data-testid="piano-roll-note-box-select"
          $left={noteBoxSelectRect.left}
          $top={noteBoxSelectRect.top}
          $width={noteBoxSelectRect.width}
          $height={noteBoxSelectRect.height}
        />
      )}
      {editCursorLeft != null && (
        <EditCursor
          data-testid="piano-roll-edit-cursor"
          $left={editCursorLeft}
        />
      )}
      {cursorTick !== null && (
        <PlaybackCursor
          data-testid="piano-roll-playback-cursor"
          $left={cursorTick * pixelsPerTick}
        />
      )}
    </div>
  );
}

export default PianoRollOverlayLayer;
