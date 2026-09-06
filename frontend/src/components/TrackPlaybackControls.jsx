import React from 'react';
import styled from 'styled-components';

const TrackList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 16px;
`;

const TrackRow = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) auto auto minmax(120px, 1fr);
  gap: 8px;
  align-items: center;

  @media (max-width: 640px) {
    grid-template-columns: 1fr;
  }
`;

const TrackLabel = styled.div`
  min-width: 0;

  strong {
    display: block;
    font-size: 0.95rem;
  }

  span {
    display: block;
    color: #4b5563;
    font-size: 0.8rem;
  }
`;

const ToggleButton = styled.button`
  background: ${(props) => (props.$active ? '#111827' : '#e5e7eb')};
  color: ${(props) => (props.$active ? '#fff' : '#111827')};
  border: none;
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
  font-weight: 600;
`;

const VolumeControl = styled.label`
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;

  input {
    width: 100%;
  }
`;

const Hint = styled.p`
  margin: 12px 0 0;
  color: #6b7280;
  font-size: 0.85rem;
`;

const TrackPlaybackControls = ({
  tracks = [],
  trackControls = {},
  disabled = false,
  onMuteToggle,
  onSoloToggle,
  onVolumeChange,
}) => {
  if (!tracks.length) {
    return <Hint>Track controls appear for composition.v1 payloads.</Hint>;
  }

  return (
    <TrackList>
      {tracks.map((track) => {
        const trackId = String(track.id);
        const control = trackControls[trackId] || {
          muted: false,
          solo: false,
          volumeMidi: Number.isFinite(Number(track.volume)) ? Number(track.volume) : 100,
        };
        return (
          <TrackRow key={trackId}>
            <TrackLabel>
              <strong>{track.name || trackId}</strong>
              <span>{track.instrument || 'unknown'} · ch {track.channel ?? '-'} · prog {track.midi_program ?? '-'}</span>
            </TrackLabel>
            <ToggleButton
              type="button"
              $active={control.muted}
              disabled={disabled}
              onClick={() => onMuteToggle?.(trackId)}
            >
              Mute
            </ToggleButton>
            <ToggleButton
              type="button"
              $active={control.solo}
              disabled={disabled}
              onClick={() => onSoloToggle?.(trackId)}
            >
              Solo
            </ToggleButton>
            <VolumeControl>
              Vol {control.volumeMidi}
              <input
                type="range"
                min="0"
                max="127"
                value={control.volumeMidi}
                disabled={disabled}
                onChange={(event) => onVolumeChange?.(trackId, Number(event.target.value))}
              />
            </VolumeControl>
          </TrackRow>
        );
      })}
    </TrackList>
  );
};

export default TrackPlaybackControls;
