import React from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../../store/musicStore.js';

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: stretch;
  margin-bottom: 10px;
`;

const TrackList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1 1 220px;
`;

const TrackItem = styled.li`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  border: 1px solid ${(props) => (props.$active ? '#4338ca' : '#c7d2fe')};
  border-radius: 6px;
  background: ${(props) => (props.$active ? '#eef2ff' : '#ffffff')};
`;

const TrackButton = styled.button`
  border: none;
  background: transparent;
  color: #312e81;
  font-size: 0.8rem;
  font-weight: ${(props) => (props.$active ? 600 : 500)};
  cursor: pointer;
  padding: 2px 4px;

  &:focus-visible {
    outline: 2px solid #6366f1;
    outline-offset: 1px;
  }
`;

const IconToggle = styled.button`
  border: 1px solid ${(props) => (props.$on ? '#6366f1' : '#d1d5db')};
  background: ${(props) => (props.$on ? '#e0e7ff' : '#f8fafc')};
  color: #312e81;
  border-radius: 4px;
  font-size: 0.7rem;
  padding: 2px 5px;
  cursor: pointer;
  min-width: 28px;

  &:focus-visible {
    outline: 2px solid #6366f1;
    outline-offset: 1px;
  }

  &:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
`;

/**
 * Compact per-track select / visibility / lock controls (ephemeral UI state).
 */
export default function PianoRollTrackControls({ tracks = [] }) {
  const pianoRollTrackId = useMusicStore((state) => state.pianoRollTrackId);
  const hiddenTrackIds = useMusicStore((state) => state.hiddenTrackIds);
  const lockedTrackIds = useMusicStore((state) => state.lockedTrackIds);
  const selectPianoRollTrack = useMusicStore((state) => state.selectPianoRollTrack);
  const toggleHiddenTrackId = useMusicStore((state) => state.toggleHiddenTrackId);
  const toggleLockedTrackId = useMusicStore((state) => state.toggleLockedTrackId);

  const hidden = new Set((hiddenTrackIds || []).map(String));
  const locked = new Set((lockedTrackIds || []).map(String));

  if (!tracks.length) {
    return null;
  }

  return (
    <Row data-testid="piano-roll-track-controls" aria-label="Track visibility and lock">
      <TrackList>
        {tracks.map((track) => {
          const id = String(track.id);
          const active = id === String(pianoRollTrackId);
          const isHidden = hidden.has(id);
          const isLocked = locked.has(id);
          const label = track.name || track.id;
          return (
            <TrackItem key={id} $active={active}>
              <TrackButton
                type="button"
                $active={active}
                data-testid={`piano-roll-track-select-${id}`}
                aria-label={`Select track ${label}`}
                aria-pressed={active}
                onClick={() => selectPianoRollTrack(id)}
              >
                {label}
              </TrackButton>
              <IconToggle
                type="button"
                $on={isHidden}
                data-testid={`piano-roll-track-hide-${id}`}
                aria-label={isHidden ? `Show track ${label}` : `Hide track ${label}`}
                aria-pressed={isHidden}
                title="Hide from editor hit-testing (not mute)"
                onClick={() => toggleHiddenTrackId(id)}
              >
                {isHidden ? 'Hid' : 'Vis'}
              </IconToggle>
              <IconToggle
                type="button"
                $on={isLocked}
                data-testid={`piano-roll-track-lock-${id}`}
                aria-label={isLocked ? `Unlock track ${label}` : `Lock track ${label}`}
                aria-pressed={isLocked}
                title="Lock edits (still visible)"
                onClick={() => toggleLockedTrackId(id)}
              >
                {isLocked ? 'Lck' : 'Unl'}
              </IconToggle>
            </TrackItem>
          );
        })}
      </TrackList>
    </Row>
  );
}
