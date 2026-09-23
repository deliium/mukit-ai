import React, { useState } from 'react';
import styled from 'styled-components';
import { MIDI_PHASES, useMusicStore } from '../store/musicStore.js';
import { createAppLogger } from '../utils/appLogger.js';
import {
  JAM_ACCOMPANIMENT_STYLES,
  JAM_COMPLEXITY_LEVELS,
  JAM_DENSITY_LEVELS,
  JAM_RESPONSIVENESS_LEVELS,
  isJamMode,
  resolveJamRolePartition,
} from '../utils/liveJamContracts.js';
import {
  defaultMidiProgramForJamRole,
} from '../utils/liveJamEnsureTracks.js';
import { defaultCommitHarmonySpans } from '../utils/liveTakeApply.js';
import { LIVE_ENGINE_UNAVAILABLE, LIVE_MIDI_PHASE_EXCLUSION } from '../utils/liveSessionContracts.js';

const log = createAppLogger('liveTransport');
const jamLog = createAppLogger('liveJam');

/**
 * Bounded GM program choices for jam ensure / Commit (catalog IDs only — never ranges on notes).
 * Role defaults from ensureJamRoleTracks are always included.
 */
const JAM_GM_PROGRAM_OPTIONS = Object.freeze([
  { program: 0, label: '0 Acoustic Grand' },
  { program: 4, label: '4 Electric Piano' },
  { program: 24, label: '24 Nylon Guitar' },
  { program: 32, label: '32 Acoustic Bass' },
  { program: 33, label: '33 Finger Bass' },
  { program: 48, label: '48 String Ensemble' },
  { program: 52, label: '52 Choir Aahs' },
  { program: 56, label: '56 Trumpet' },
  { program: 73, label: '73 Flute' },
  { program: 80, label: '80 Square Lead' },
  { program: 89, label: '89 Warm Pad' },
]);

const Panel = styled.div`
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 8px;
`;

const Title = styled.h4`
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
  color: #334155;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const RoleBlock = styled.div`
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  padding: 4px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
`;

const RoleName = styled.span`
  font-size: 0.75rem;
  font-weight: 600;
  color: #334155;
  text-transform: capitalize;
  min-width: 4.5rem;
`;

const Button = styled.button`
  min-height: 36px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  background: ${(props) => (props.$primary ? '#e0e7ff' : '#fff')};
  color: #1e293b;
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
`;

const Meta = styled.div`
  font-size: 0.8rem;
  color: #64748b;
`;

const Badge = styled.span`
  font-size: 0.75rem;
  font-weight: 600;
  color: ${(props) => (props.$warn ? '#b45309' : '#334155')};
  margin-left: 4px;
`;

const ErrorText = styled.div`
  color: #991b1b;
  font-size: 0.85rem;
`;

const Select = styled.select`
  margin-left: 6px;
  min-height: 32px;
  border-radius: 6px;
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #1e293b;
`;

const Label = styled.label`
  font-size: 0.8rem;
  color: #475569;
  display: inline-flex;
  align-items: center;
`;

const CheckboxLabel = styled.label`
  font-size: 0.8rem;
  color: #475569;
  display: inline-flex;
  align-items: center;
  gap: 6px;
`;

/**
 * GM options for a role — always include the role default program.
 * @param {string} role
 */
function gmOptionsForRole(role) {
  const defaultProgram = defaultMidiProgramForJamRole(role);
  const seen = new Set(JAM_GM_PROGRAM_OPTIONS.map((o) => o.program));
  if (!seen.has(defaultProgram)) {
    return [
      { program: defaultProgram, label: `${defaultProgram} (role default)` },
      ...JAM_GM_PROGRAM_OPTIONS,
    ];
  }
  return JAM_GM_PROGRAM_OPTIONS;
}

const CoPerformancePanel = () => {
  const livePhase = useMusicStore((s) => s.livePhase);
  const liveSessionSnapshot = useMusicStore((s) => s.liveSessionSnapshot);
  const liveErrorCode = useMusicStore((s) => s.liveErrorCode);
  const liveErrorMessage = useMusicStore((s) => s.liveErrorMessage);
  const liveHorizonBars = useMusicStore((s) => s.liveHorizonBars);
  const liveHorizonMs = useMusicStore((s) => s.liveHorizonMs);
  const liveStreamNoteOnCount = useMusicStore((s) => s.liveStreamNoteOnCount);
  const jamMode = useMusicStore((s) => s.jamMode);
  const jamControls = useMusicStore((s) => s.jamControls);
  const jamBelief = useMusicStore((s) => s.jamBelief);
  const jamPredictUnavailable = useMusicStore((s) => s.jamPredictUnavailable);
  const jamHarmonyHoldCount = useMusicStore((s) => s.jamHarmonyHoldCount);
  const midiPhase = useMusicStore((s) => s.midiPhase);
  const midiDestinationTrackId = useMusicStore((s) => s.midiDestinationTrackId);
  const pianoRollTrackId = useMusicStore((s) => s.pianoRollTrackId);
  const editedMusicJson = useMusicStore((s) => s.editedMusicJson);
  const startLiveCoPerformance = useMusicStore((s) => s.startLiveCoPerformance);
  const stopLiveCoPerformance = useMusicStore((s) => s.stopLiveCoPerformance);
  const cancelLiveCoPerformance = useMusicStore((s) => s.cancelLiveCoPerformance);
  const commitLiveCoPerformance = useMusicStore((s) => s.commitLiveCoPerformance);
  const setLiveHorizon = useMusicStore((s) => s.setLiveHorizon);
  const setMidiDestinationTrackId = useMusicStore((s) => s.setMidiDestinationTrackId);
  const setJamMode = useMusicStore((s) => s.setJamMode);
  const setJamControls = useMusicStore((s) => s.setJamControls);

  const [ensureMissingTracks, setEnsureMissingTracks] = useState(false);
  const [commitHarmonySpans, setCommitHarmonySpans] = useState(null);

  const running = livePhase === 'running' || livePhase === 'degraded';
  const midiBlocking =
    midiPhase === MIDI_PHASES.ARMED
    || midiPhase === MIDI_PHASES.COUNTING_IN
    || midiPhase === MIDI_PHASES.RECORDING
    || midiPhase === MIDI_PHASES.STOPPING;

  const tracks = Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : [];
  const dest = midiDestinationTrackId || pianoRollTrackId || tracks[0]?.id || '';

  const snap = liveSessionSnapshot;
  const deg = snap?.degradation;
  const clock = snap?.transport;
  const belief = snap?.belief || jamBelief;
  const latency = snap?.latency_ms;
  const holdCount = snap?.jam_flags?.harmony_hold_count ?? jamHarmonyHoldCount;
  const predictUnavailable = snap?.jam_flags?.predict_unavailable ?? jamPredictUnavailable;
  const controls = jamControls || {};
  const instrumentSet = controls.instrument_set || {};

  const aiRoles = isJamMode(jamMode)
    ? resolveJamRolePartition(jamMode, controls.complexity || 'medium').ai_roles
    : [];

  const harmonyToggle = commitHarmonySpans != null
    ? commitHarmonySpans
    : defaultCommitHarmonySpans(jamMode);

  const onStart = () => {
    if (!jamMode) {
      jamLog.info('UI start blocked — mode required');
      return;
    }
    const result = startLiveCoPerformance();
    log.info('UI start live', { ok: result?.ok, code: result?.code, jamMode });
  };

  const onStop = () => {
    stopLiveCoPerformance({ reason: 'ui-stop' });
  };

  const onCancel = () => {
    cancelLiveCoPerformance({ reason: 'ui-cancel' });
  };

  const onCommit = () => {
    /** @type {Record<string, string>} */
    const roleTracks = {};
    for (const role of aiRoles) {
      const tid = instrumentSet[role]?.track_id;
      if (tid) roleTracks[role] = tid;
    }
    const result = commitLiveCoPerformance({
      trackId: dest,
      roleTracks,
      ensureMissingTracks,
      commitHarmonySpans: harmonyToggle,
      includeStream: true,
      includeAccompaniment: true,
    });
    log.info('UI commit live', { ok: result?.ok, code: result?.code });
  };

  const onModeChange = (e) => {
    const value = e.target.value || null;
    setJamMode(value === '' ? null : value);
    setCommitHarmonySpans(null);
  };

  const onControlChange = (key) => (e) => {
    setJamControls({ [key]: e.target.value });
  };

  /**
   * Patch one role entry in session-only instrument_set (track_id and/or midi_program).
   * @param {string} role
   * @param {{ track_id?: string|null, midi_program?: number|null }} patch
   */
  const patchInstrumentRole = (role, patch) => {
    const nextSet = { ...instrumentSet };
    const prev = { ...(nextSet[role] || {}) };
    if ('track_id' in patch) {
      if (patch.track_id) {
        prev.track_id = patch.track_id;
      } else {
        delete prev.track_id;
      }
    }
    if ('midi_program' in patch) {
      if (patch.midi_program == null) {
        delete prev.midi_program;
      } else {
        prev.midi_program = patch.midi_program;
      }
    }
    if (Object.keys(prev).length) {
      nextSet[role] = prev;
    } else {
      delete nextSet[role];
    }
    jamLog.info('instrument_set patch', {
      role,
      hasTrack: Boolean(prev.track_id),
      midi_program: prev.midi_program ?? null,
    });
    setJamControls({ instrument_set: nextSet });
  };

  const onRoleTrackChange = (role) => (e) => {
    patchInstrumentRole(role, { track_id: e.target.value || null });
  };

  const onRoleProgramChange = (role) => (e) => {
    const raw = e.target.value;
    if (raw === '') {
      patchInstrumentRole(role, { midi_program: null });
      return;
    }
    const program = Number.parseInt(raw, 10);
    if (!Number.isFinite(program) || program < 0 || program > 127) return;
    patchInstrumentRole(role, { midi_program: program });
  };

  return (
    <Panel data-testid="co-performance-panel">
      <Title>AI Jam / Co-performance</Title>
      <Row>
        <Label>
          Mode
          <Select
            value={jamMode || ''}
            disabled={running}
            onChange={onModeChange}
            data-testid="jam-mode-select"
          >
            <option value="">Select…</option>
            <option value="user_melody">User melody</option>
            <option value="user_chords">User chords</option>
          </Select>
        </Label>
      </Row>
      <Row>
        <Label>
          Complexity
          <Select
            value={controls.complexity || 'medium'}
            disabled={running}
            onChange={onControlChange('complexity')}
          >
            {JAM_COMPLEXITY_LEVELS.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </Select>
        </Label>
        <Label>
          Density
          <Select
            value={controls.density || 'medium'}
            disabled={running}
            onChange={onControlChange('density')}
          >
            {JAM_DENSITY_LEVELS.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </Select>
        </Label>
        <Label>
          Style
          <Select
            value={controls.style || 'block'}
            disabled={running}
            onChange={onControlChange('style')}
          >
            {JAM_ACCOMPANIMENT_STYLES.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </Select>
        </Label>
        <Label>
          Responsiveness
          <Select
            value={controls.responsiveness || 'medium'}
            disabled={running}
            onChange={onControlChange('responsiveness')}
          >
            {JAM_RESPONSIVENESS_LEVELS.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </Select>
        </Label>
      </Row>
      <Row>
        <Button
          type="button"
          $primary
          disabled={running || midiBlocking || !jamMode}
          onClick={onStart}
        >
          Start
        </Button>
        <Button type="button" disabled={!running} onClick={onStop}>
          Stop
        </Button>
        <Button type="button" disabled={livePhase === 'idle'} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="button"
          disabled={livePhase === 'idle' || (!dest && !ensureMissingTracks)}
          onClick={onCommit}
          data-testid="jam-commit-button"
        >
          Commit
        </Button>
      </Row>
      <Row>
        <Label>
          Horizon bars
          <input
            type="number"
            min={1}
            max={2}
            step={1}
            value={liveHorizonBars}
            disabled={running}
            onChange={(e) => setLiveHorizon({ bars: Number(e.target.value) })}
            style={{ width: 56, marginLeft: 6 }}
          />
        </Label>
        <Label>
          Horizon ms
          <input
            type="number"
            min={250}
            max={8000}
            step={250}
            value={liveHorizonMs}
            disabled={running}
            onChange={(e) => setLiveHorizon({ ms: Number(e.target.value) })}
            style={{ width: 72, marginLeft: 6 }}
          />
        </Label>
        <Label>
          User track
          <Select
            value={dest || ''}
            onChange={(e) => setMidiDestinationTrackId(e.target.value || null)}
            data-testid="jam-user-track-select"
          >
            {tracks.map((t) => (
              <option key={t.id} value={t.id}>{t.name || t.id}</option>
            ))}
          </Select>
        </Label>
      </Row>
      {isJamMode(jamMode) ? (
        <>
          <Row data-testid="jam-instrument-set">
            {aiRoles.map((role) => {
              const defaultProgram = defaultMidiProgramForJamRole(role);
              const selectedProgram = instrumentSet[role]?.midi_program;
              const programValue = selectedProgram != null
                ? String(selectedProgram)
                : String(defaultProgram);
              return (
                <RoleBlock key={role}>
                  <RoleName>{role}</RoleName>
                  <Label>
                    Track
                    <Select
                      value={instrumentSet[role]?.track_id || ''}
                      onChange={onRoleTrackChange(role)}
                      data-testid={`jam-role-track-${role}`}
                    >
                      <option value="">(unmapped)</option>
                      {tracks.map((t) => (
                        <option key={t.id} value={t.id}>{t.name || t.id}</option>
                      ))}
                    </Select>
                  </Label>
                  <Label>
                    GM
                    <Select
                      value={programValue}
                      disabled={running}
                      onChange={onRoleProgramChange(role)}
                      data-testid={`jam-role-gm-${role}`}
                      title="GM program used when Ensure missing tracks creates a new track"
                    >
                      {gmOptionsForRole(role).map((opt) => (
                        <option key={opt.program} value={opt.program}>
                          {opt.label}
                        </option>
                      ))}
                    </Select>
                  </Label>
                </RoleBlock>
              );
            })}
          </Row>
          <Row data-testid="jam-commit-options">
            <CheckboxLabel>
              <input
                type="checkbox"
                checked={ensureMissingTracks}
                onChange={(e) => setEnsureMissingTracks(e.target.checked)}
                data-testid="jam-ensure-tracks"
              />
              Ensure missing tracks
            </CheckboxLabel>
            <CheckboxLabel>
              <input
                type="checkbox"
                checked={Boolean(harmonyToggle)}
                onChange={(e) => setCommitHarmonySpans(e.target.checked)}
                data-testid="jam-commit-harmony"
              />
              Commit harmony spans
            </CheckboxLabel>
          </Row>
        </>
      ) : null}
      <Meta>
        Phase: {livePhase}
        {deg?.active ? (
          <Badge $warn>
            degraded ({deg.code || 'pattern'}) ×{deg.count || 0}
          </Badge>
        ) : null}
        {belief?.held || holdCount > 0 ? (
          <Badge $warn>
            hold ×{holdCount || 1}
          </Badge>
        ) : null}
        {predictUnavailable ? (
          <Badge $warn>predict unavailable</Badge>
        ) : null}
      </Meta>
      <Meta>
        Clock: bar {clock?.bar ?? '—'} beat {clock?.beat ?? '—'} tick {clock?.tick ?? '—'}
        {' · '}
        Belief: {belief?.symbol || '—'}
        {belief?.confidence != null ? ` (${Number(belief.confidence).toFixed(2)})` : ''}
        {' · '}
        Stream ons: {liveStreamNoteOnCount}
      </Meta>
      <Meta>
        Latency ms — midi:
        {' '}
        {latency?.midi_input ?? '—'}
        {' / analysis: '}
        {latency?.analysis ?? '—'}
        {' / gen: '}
        {latency?.generation ?? '—'}
        {' / sched: '}
        {latency?.scheduling ?? '—'}
      </Meta>
      {!jamMode && !running ? (
        <ErrorText>Select a jam mode before Start.</ErrorText>
      ) : null}
      {midiBlocking ? (
        <ErrorText>
          MIDI record is active — stop recording before co-performance (
          {LIVE_MIDI_PHASE_EXCLUSION}
          ).
        </ErrorText>
      ) : null}
      {liveErrorCode === LIVE_ENGINE_UNAVAILABLE ? (
        <ErrorText>Playback engine unavailable — open transport playback first.</ErrorText>
      ) : null}
      {liveErrorMessage ? <ErrorText>{liveErrorMessage}</ErrorText> : null}
    </Panel>
  );
};

export default CoPerformancePanel;
