import React, { useId, useState } from 'react';
import styled from 'styled-components';

const MixerShell = styled.section`
  margin-top: 12px;
  max-height: min(42vh, 360px);
  overflow: auto;
  border-top: 1px solid #e5e7eb;
  padding-top: 10px;

  @media (max-width: 390px) {
    max-height: min(36vh, 280px);
  }
`;

const MixerHeader = styled.div`
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
`;

const CollapseButton = styled.button`
  background: #e5e7eb;
  color: #111827;
  border: none;
  border-radius: 6px;
  padding: 8px 12px;
  min-height: 44px;
  cursor: pointer;
  font-weight: 600;
`;

const Hint = styled.p`
  margin: 0;
  color: #6b7280;
  font-size: 0.85rem;
`;

const TrackList = styled.div`
  display: flex;
  flex-direction: column;
  gap: 12px;
`;

const TrackRow = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) auto auto minmax(0, 1fr);
  gap: 8px 10px;
  align-items: center;
  padding-bottom: 10px;
  border-bottom: 1px solid #f3f4f6;

  @media (max-width: 720px) {
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
    font-size: 0.78rem;
    line-height: 1.35;
  }
`;

const StatusText = styled.span`
  color: ${(props) => (props.$fallback ? '#92400e' : '#065f46')};
`;

const ToggleButton = styled.button`
  background: ${(props) => (props.$active ? '#111827' : '#e5e7eb')};
  color: ${(props) => (props.$active ? '#fff' : '#111827')};
  border: none;
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
  font-weight: 600;
  min-height: 44px;
  min-width: 44px;
`;

const ControlsGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px;
  grid-column: 1 / -1;
`;

const SliderControl = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.8rem;
  color: #374151;

  input[type='range'] {
    width: 100%;
    min-height: 44px;
  }
`;

const LevelTrack = styled.div`
  grid-column: 1 / -1;
  height: 8px;
  background: #e5e7eb;
  border-radius: 999px;
  overflow: hidden;
`;

const LevelFill = styled.div`
  height: 100%;
  width: ${(props) => `${Math.max(0, Math.min(100, props.$level * 100))}%`};
  background: ${(props) => (props.$clipped ? '#b91c1c' : '#059669')};
  transition: width 80ms linear;

  @media (prefers-reduced-motion: reduce) {
    transition: none;
  }
`;

function formatTrim(trimDb) {
  const value = Number(trimDb) || 0;
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(1)} dB`;
}

function profileStatusLabel(status) {
  if (!status) {
    return 'Profile pending';
  }
  const profile = status.profileId || 'unknown';
  if (status.fallback) {
    return `${profile} · synth fallback${status.reasonCode ? ` (${status.reasonCode})` : ''}`;
  }
  if (!status.ready) {
    return `${profile} · loading`;
  }
  return `${profile} · sampled`;
}

const TrackPlaybackControls = ({
  tracks = [],
  trackControls = {},
  instrumentStatuses = {},
  activityLevels = {},
  activityClipped = false,
  disabled = false,
  collapsed: collapsedProp = null,
  onCollapsedChange = null,
  onMuteToggle,
  onSoloToggle,
  onTrimChange,
  onPanChange,
  onSendChange,
  onVolumeChange,
  ariaLabel = 'Track mixer',
  hint = 'Tracks / mixer (canonical composition.v2; mute/solo are UI-only)',
}) => {
  const headingId = useId();
  const [collapsedInternal, setCollapsedInternal] = useState(false);
  const collapsed = collapsedProp == null ? collapsedInternal : Boolean(collapsedProp);
  const setCollapsed = (next) => {
    if (onCollapsedChange) {
      onCollapsedChange(next);
      return;
    }
    setCollapsedInternal(next);
  };

  if (!tracks.length) {
    return <Hint>Track controls appear for canonical composition payloads.</Hint>;
  }

  return (
    <MixerShell aria-label={ariaLabel} data-testid="playback-mixer">
      <MixerHeader>
        <Hint id={headingId}>{hint}</Hint>
        <CollapseButton
          type="button"
          aria-expanded={!collapsed}
          aria-controls="playback-mixer-rows"
          data-testid="playback-mixer-collapse"
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? 'Show mixer' : 'Hide mixer'}
        </CollapseButton>
      </MixerHeader>
      {!collapsed ? (
        <TrackList id="playback-mixer-rows" aria-labelledby={headingId}>
          {tracks.map((track) => {
            const trackId = String(track.id);
            const trackName = track.name || trackId;
            const control = trackControls[trackId] || {
              muted: false,
              solo: false,
              volumeMidi: Number.isFinite(Number(track.volume)) ? Number(track.volume) : 100,
              trimDb: 0,
              panOffset: 0,
              reverbSend: 0,
            };
            const status = instrumentStatuses[trackId] || null;
            const level = Number(activityLevels[trackId]) || 0;
            const trimDb = Number.isFinite(Number(control.trimDb)) ? Number(control.trimDb) : 0;
            const panOffset = Number.isFinite(Number(control.panOffset)) ? Number(control.panOffset) : 0;
            const reverbSend = Number.isFinite(Number(control.reverbSend)) ? Number(control.reverbSend) : 0;
            const volumeMidi = Number.isFinite(Number(control.volumeMidi))
              ? Number(control.volumeMidi)
              : 100;

            return (
              <TrackRow key={trackId} data-testid={`playback-mixer-row-${trackId}`}>
                <TrackLabel>
                  <strong>{trackName}</strong>
                  <span>
                    {track.instrument || 'unknown'}
                    {Number.isInteger(Number(track.midi_program)) ? ` · prog ${track.midi_program}` : ''}
                    {Number.isInteger(Number(track.expression)) ? ` · expr ${track.expression}` : ''}
                  </span>
                  <StatusText $fallback={Boolean(status?.fallback)} data-testid={`playback-mixer-profile-${trackId}`}>
                    {profileStatusLabel(status)}
                  </StatusText>
                </TrackLabel>
                <ToggleButton
                  type="button"
                  $active={control.muted}
                  disabled={disabled}
                  aria-pressed={Boolean(control.muted)}
                  aria-label={`Mute ${trackName}`}
                  data-testid={`playback-mixer-mute-${trackId}`}
                  onClick={() => onMuteToggle?.(trackId)}
                >
                  Mute
                </ToggleButton>
                <ToggleButton
                  type="button"
                  $active={control.solo}
                  disabled={disabled}
                  aria-pressed={Boolean(control.solo)}
                  aria-label={`Solo ${trackName}`}
                  data-testid={`playback-mixer-solo-${trackId}`}
                  onClick={() => onSoloToggle?.(trackId)}
                >
                  Solo
                </ToggleButton>
                <ControlsGrid>
                  <SliderControl>
                    Trim {formatTrim(trimDb)}
                    <input
                      type="range"
                      min="-24"
                      max="24"
                      step="0.5"
                      value={trimDb}
                      disabled={disabled}
                      aria-valuetext={formatTrim(trimDb)}
                      aria-label={`Trim ${trackName}`}
                      data-testid={`playback-mixer-trim-${trackId}`}
                      onChange={(event) => onTrimChange?.(trackId, Number(event.target.value))}
                    />
                  </SliderControl>
                  <SliderControl>
                    Pan {panOffset.toFixed(2)}
                    <input
                      type="range"
                      min="-1"
                      max="1"
                      step="0.01"
                      value={panOffset}
                      disabled={disabled}
                      aria-valuetext={`${panOffset.toFixed(2)}`}
                      aria-label={`Pan offset ${trackName}`}
                      data-testid={`playback-mixer-pan-${trackId}`}
                      onChange={(event) => onPanChange?.(trackId, Number(event.target.value))}
                    />
                  </SliderControl>
                  <SliderControl>
                    Send {(reverbSend * 100).toFixed(0)}%
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.01"
                      value={reverbSend}
                      disabled={disabled}
                      aria-valuetext={`${Math.round(reverbSend * 100)} percent`}
                      aria-label={`Reverb send ${trackName}`}
                      data-testid={`playback-mixer-send-${trackId}`}
                      onChange={(event) => onSendChange?.(trackId, Number(event.target.value))}
                    />
                  </SliderControl>
                  {typeof onVolumeChange === 'function' ? (
                    <SliderControl>
                      Legacy vol {volumeMidi}
                      <input
                        type="range"
                        min="0"
                        max="127"
                        value={volumeMidi}
                        disabled={disabled}
                        aria-label={`Legacy volume ${trackName}`}
                        data-testid={`playback-mixer-volume-${trackId}`}
                        onChange={(event) => onVolumeChange?.(trackId, Number(event.target.value))}
                      />
                    </SliderControl>
                  ) : null}
                </ControlsGrid>
                <LevelTrack
                  role="meter"
                  aria-label={`Activity ${trackName}`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(level * 100)}
                  data-testid={`playback-mixer-level-${trackId}`}
                >
                  <LevelFill $level={level} $clipped={activityClipped && level >= 0.98} />
                </LevelTrack>
              </TrackRow>
            );
          })}
        </TrackList>
      ) : null}
    </MixerShell>
  );
};

export default TrackPlaybackControls;
