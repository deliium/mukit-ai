import React, { useEffect, useMemo, useRef } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../../store/musicStore.js';
import { secondsToPlaybackPosition } from '../../utils/playbackPosition.js';
import { createAppLogger } from '../../utils/appLogger.js';

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

/**
 * Transient overlays: AI/motif selection, note box-select marquee, playback cursor, drag ghost.
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
}) {
  const playbackStatus = useMusicStore((state) => state.playbackStatus);
  const playbackSeconds = useMusicStore((state) => state.playbackSeconds);
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
