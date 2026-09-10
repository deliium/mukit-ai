import React, { memo } from 'react';
import styled from 'styled-components';

const NoteBlock = styled.div`
  position: absolute;
  left: ${(props) => props.$left}px;
  top: ${(props) => props.$top}px;
  width: ${(props) => Math.max(4, props.$width)}px;
  height: ${(props) => Math.max(6, props.$height - 2)}px;
  background: ${(props) => props.$color};
  opacity: ${(props) => props.$opacity};
  border-radius: 3px;
  border: 2px solid ${(props) => {
    if (props.$selected) return '#111827';
    if (props.$motifRole === 'usage') return '#d97706';
    if (props.$motifRole === 'source') return '#0891b2';
    if (props.$motifRole === 'authoring') return '#6366f1';
    return 'transparent';
  }};
  box-sizing: border-box;
  cursor: ${(props) => (props.$draggable ? 'grab' : 'pointer')};
  z-index: ${(props) => (props.$selected ? 4 : props.$draggable ? 3 : 1)};
  transform: ${(props) => (props.$dragging ? 'translateZ(0)' : 'none')};
  visibility: ${(props) => (props.$dragging ? 'hidden' : 'visible')};

  &:active {
    cursor: ${(props) => (props.$draggable ? 'grabbing' : 'pointer')};
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

/**
 * Memoized visible-note layer. Does not subscribe to playback cursor or drag deltas.
 * Notes on all visible tracks are selectable; only draggable notes support move/resize.
 */
function PianoRollNoteLayer({
  notes,
  rowHeight,
  selectedNoteId,
  selectedNoteSet,
  motifHighlightSets,
  draggingNoteId,
  onNotePointerDown,
  onResizePointerDown,
  onNoteClick,
}) {
  return (
    <div data-testid="piano-roll-note-layer" aria-hidden="false">
      {notes.map((item) => {
        const {
          key,
          trackId,
          event,
          left,
          top,
          width,
          color,
          editable,
          selectable = true,
          opacity,
          selected,
          motifRole,
        } = item;
        const eventId = String(event.id);
        const draggable = Boolean(editable);
        const dragging = draggingNoteId != null && String(draggingNoteId) === eventId && draggable;
        return (
          <NoteBlock
            key={key}
            $left={left}
            $top={top}
            $width={width}
            $height={rowHeight}
            $color={color}
            $opacity={opacity}
            $selected={selected}
            $motifRole={motifRole}
            $draggable={draggable}
            $dragging={dragging}
            role={selectable ? 'button' : 'presentation'}
            aria-label={selectable ? `Note ${event.pitch} at tick ${event.start_tick}` : undefined}
            tabIndex={selectable ? 0 : -1}
            data-track-id={trackId}
            data-event-id={eventId}
            onPointerDown={(pointerEvent) => {
              if (!draggable || !onNotePointerDown) {
                return;
              }
              // Selection modifiers are handled on click so pointerdown does not
              // collapse a Shift/Ctrl selection before the click handler runs.
              if (pointerEvent.shiftKey || pointerEvent.ctrlKey || pointerEvent.metaKey) {
                return;
              }
              onNotePointerDown(event, pointerEvent, {
                left,
                top,
                width,
                height: rowHeight,
                color,
                trackId,
              });
            }}
            onClick={(clickEvent) => {
              if (!selectable || !onNoteClick) {
                return;
              }
              onNoteClick(event, clickEvent, trackId);
            }}
          >
            {draggable && (
              <ResizeHandle
                aria-label={`Resize note ${event.pitch}`}
                onPointerDown={(pointerEvent) => {
                  if (!onResizePointerDown) {
                    return;
                  }
                  if (pointerEvent.shiftKey || pointerEvent.ctrlKey || pointerEvent.metaKey) {
                    return;
                  }
                  onResizePointerDown(event, pointerEvent, {
                    left,
                    top,
                    width,
                    height: rowHeight,
                    color,
                    trackId,
                  });
                }}
              />
            )}
          </NoteBlock>
        );
      })}
    </div>
  );
}

function propsAreEqual(prev, next) {
  return (
    prev.notes === next.notes
    && prev.rowHeight === next.rowHeight
    && prev.selectedNoteId === next.selectedNoteId
    && prev.selectedNoteSet === next.selectedNoteSet
    && prev.motifHighlightSets === next.motifHighlightSets
    && prev.draggingNoteId === next.draggingNoteId
    && prev.onNotePointerDown === next.onNotePointerDown
    && prev.onResizePointerDown === next.onResizePointerDown
    && prev.onNoteClick === next.onNoteClick
  );
}

export default memo(PianoRollNoteLayer, propsAreEqual);
