import React, { useEffect, useMemo, useState } from 'react';
import styled from 'styled-components';

import { useMusicStore } from '../store/musicStore.js';
import { formatAdaptiveLayerStatus } from '../utils/adaptiveLayerIntensity.js';
import { layoutAdaptiveScoreGraph } from '../utils/adaptiveScoreGraph.js';
import AdaptiveMusicalContextPanel from './AdaptiveMusicalContextPanel.jsx';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
`;

const Header = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Title = styled.h3`
  margin: 0;
  color: #1e293b;
`;

const GraphRegion = styled.div`
  overflow-x: auto;
  max-width: 100%;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  background: #f8fafc;
`;

const GraphStage = styled.div`
  position: relative;
`;

const StateCard = styled.button`
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  box-sizing: border-box;
  text-align: left;
  border-radius: 8px;
  border: 2px solid ${(props) => (props.$selected ? '#4f46e5' : '#cbd5e1')};
  background: ${(props) => (props.$selected ? '#eef2ff' : '#fff')};
  min-height: 44px;
  padding: 8px 10px;
  cursor: pointer;
`;

const FormStack = styled.form`
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: 420px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`;

const Control = styled.input`
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  box-sizing: border-box;
`;

const Select = styled.select`
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #fff;
`;

const Button = styled.button`
  min-height: 44px;
  padding: 8px 14px;
  border-radius: 6px;
  border: 1px solid #4f46e5;
  background: #4f46e5;
  color: #fff;
  cursor: pointer;
`;

const Secondary = styled(Button)`
  background: #fff;
  color: #312e81;
`;

const Finding = styled.button`
  display: block;
  width: 100%;
  text-align: left;
  min-height: 44px;
  border: 1px solid #fecaca;
  background: #fff1f2;
  color: #881337;
  border-radius: 6px;
  padding: 8px 10px;
`;

const Badge = styled.span`
  background: #e0e7ff;
  color: #3730a3;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 0.75rem;
`;

function submitCommand(event, run, op, payload) {
  event.preventDefault();
  console.info('[AdaptiveScorePanel] command', { op });
  return run(op, payload);
}

const LAYER_ROLES = ['harmony', 'bass', 'percussion', 'strings', 'brass', 'ambient', 'other'];

function parsedPlayhead(value) {
  if (typeof value !== 'string' || value.trim() === '') {
    return 0;
  }
  const tick = Number.parseInt(value, 10);
  if (!Number.isInteger(tick) || tick < 0 || String(tick) !== value.trim()) {
    return 0;
  }
  return tick;
}

const TRANSITION_QUANTIZATIONS = [
  'immediate',
  'beat',
  'bar',
  'next_exit',
  'custom',
  'phrase',
  'loop_end',
  'cue',
];

const AdaptiveScorePanel = () => {
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const score = useMusicStore((state) => state.adaptiveScore);
  const selectedStateId = useMusicStore((state) => state.adaptiveSelectedStateId);
  const findings = useMusicStore((state) => state.adaptiveFindings);
  const status = useMusicStore((state) => state.adaptiveStatus);
  const commandError = useMusicStore((state) => state.adaptiveCommandError);
  const sections = useMusicStore((state) => state.editedMusicJson?.sections || []);
  const revisions = useMusicStore((state) => state.versionRevisions || []);
  const loadAdaptiveScore = useMusicStore((state) => state.loadAdaptiveScore);
  const createEmptyAdaptiveScore = useMusicStore((state) => state.createEmptyAdaptiveScore);
  const runAdaptiveScoreCommand = useMusicStore((state) => state.runAdaptiveScoreCommand);
  const validateLoadedAdaptiveScore = useMusicStore((state) => state.validateLoadedAdaptiveScore);
  const selectAdaptiveState = useMusicStore((state) => state.selectAdaptiveState);
  const scheduled = useMusicStore((state) => state.adaptiveScheduledTransition);
  const scheduleAdaptiveTransition = useMusicStore((state) => state.scheduleAdaptiveTransition);
  const cancelAdaptiveTransition = useMusicStore((state) => state.cancelAdaptiveTransition);
  const layerIntensity = useMusicStore((state) => state.adaptiveLayerIntensity);
  const layerIntensityError = useMusicStore((state) => state.adaptiveLayerIntensityError);
  const layerPlanPreview = useMusicStore((state) => state.adaptiveLayerPlanPreview);
  const mapAdaptiveLayers = useMusicStore((state) => state.mapAdaptiveLayers);
  const previewLayerPlan = useMusicStore((state) => state.previewAdaptiveLayerPlan);
  const applyLayerPlan = useMusicStore((state) => state.applyAdaptiveLayerPlan);
  const applySessionMute = useMusicStore((state) => state.applyAdaptiveLayerSessionMute);
  const restoreSessionMute = useMusicStore((state) => state.restoreAdaptiveLayerMute);
  const playback = useMusicStore((state) => state.adaptivePlayback);
  const playbackError = useMusicStore((state) => state.adaptivePlaybackError);
  const startAdaptivePlayback = useMusicStore((state) => state.startAdaptivePlayback);
  const stopAdaptivePlayback = useMusicStore((state) => state.stopAdaptivePlayback);
  const requestAdaptivePlaybackState = useMusicStore((state) => state.requestAdaptivePlaybackState);
  const setAdaptivePlaybackIntensity = useMusicStore((state) => state.setAdaptivePlaybackIntensity);

  const [stateName, setStateName] = useState('');
  const [materialKind, setMaterialKind] = useState('section');
  const [sectionId, setSectionId] = useState('');
  const [startBar, setStartBar] = useState('1');
  const [endBar, setEndBar] = useState('1');
  const [revisionId, setRevisionId] = useState('');
  const [toStateId, setToStateId] = useState('');
  const [transitionId, setTransitionId] = useState('');
  const [quantization, setQuantization] = useState('bar');
  const [intensity, setIntensity] = useState('0');
  const [loopStart, setLoopStart] = useState('1');
  const [loopEnd, setLoopEnd] = useState('1');
  const [boundaryKind, setBoundaryKind] = useState('bar');
  const [boundaryValue, setBoundaryValue] = useState('1');
  const [scheduleToStateId, setScheduleToStateId] = useState('');
  const [positionTick, setPositionTick] = useState('0');
  const [runtimeIntensity, setRuntimeIntensity] = useState('0');
  const [playheadTick, setPlayheadTick] = useState('');
  const [planRole, setPlanRole] = useState('ambient');
  const [planMin, setPlanMin] = useState('0');
  const [planMax, setPlanMax] = useState('1');
  const [planProposals, setPlanProposals] = useState([]);

  useEffect(() => {
    if (!currentProjectId) {
      return undefined;
    }
    console.debug('[AdaptiveScorePanel] mounted', {
      scoreId: useMusicStore.getState().adaptiveScoreId,
      stateCount: useMusicStore.getState().adaptiveScore?.states?.length || 0,
    });
    loadAdaptiveScore();
    return undefined;
  }, [currentProjectId, loadAdaptiveScore]);

  const layout = useMemo(
    () => layoutAdaptiveScoreGraph(score, selectedStateId),
    [score, selectedStateId],
  );
  const states = score?.states || [];
  const selected = states.find((state) => state.id === selectedStateId) || null;
  const sectionOptions = sections.filter((section) => section?.id);

  useEffect(() => {
    if (typeof selected?.intensity === 'number') {
      setRuntimeIntensity(String(selected.intensity));
    }
  }, [selected]);

  useEffect(() => {
    const transition = (score?.transitions || []).find((item) => item.id === transitionId);
    if (transition?.quantization) {
      setQuantization(transition.quantization);
    }
  }, [transitionId, score]);

  useEffect(() => {
    if (!sectionId && sectionOptions[0]?.id) {
      setSectionId(sectionOptions[0].id);
    }
  }, [sectionId, sectionOptions]);

  const run = (op, payload) => runAdaptiveScoreCommand(op, payload);

  const commitRuntimeIntensity = (value) => {
    const playing = useMusicStore.getState().adaptivePlayback?.transport === 'playing';
    if (playing) {
      setAdaptivePlaybackIntensity(Number(value));
      return;
    }
    mapAdaptiveLayers(Number(value), parsedPlayhead(playheadTick));
  };

  const onValidate = async () => {
    const detail = await validateLoadedAdaptiveScore();
    const nextFindings = detail?.findings || useMusicStore.getState().adaptiveFindings || [];
    console.debug('[AdaptiveScorePanel] validate', {
      scoreId: score?.id || null,
      stateCount: score?.states?.length || 0,
      errorCount: nextFindings.filter((item) => item.severity === 'error').length,
      warningCount: nextFindings.filter((item) => item.severity === 'warning').length,
    });
  };

  const focusFinding = (finding) => {
    if (!finding?.target_id) {
      return;
    }
    const node = document.querySelector(`[data-testid="adaptive-state-${finding.target_id}"]`);
    if (node && typeof node.scrollIntoView === 'function') {
      node.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  };

  if (!currentProjectId) {
    return <Panel data-testid="adaptive-score-panel">Open a project to author an adaptive score.</Panel>;
  }

  if (!score) {
    return (
      <Panel data-testid="adaptive-score-panel">
        <Title>Adaptive score</Title>
        <p>{status === 'loading' ? 'Loading adaptive score…' : 'No adaptive score on this project.'}</p>
        <Button type="button" data-testid="adaptive-new-score" onClick={() => createEmptyAdaptiveScore()}>
          New adaptive score
        </Button>
        <Button type="button" data-testid="adaptive-playback-play" disabled>
          Play
        </Button>
        {commandError ? <p>{commandError}</p> : null}
      </Panel>
    );
  }

  return (
    <Panel data-testid="adaptive-score-panel">
      <Header>
        <Title>Adaptive score</Title>
        <span data-testid="adaptive-current-state">
          Current state: {selected?.name || 'None'}
        </span>
        {score.initial_state_id ? <Badge>Initial {score.initial_state_id}</Badge> : null}
        <span>
          {score.variants?.length || 0} variants, {score.layers?.length || 0} layers, {score.stingers?.length || 0} stingers
        </span>
      </Header>
      <section data-testid="adaptive-playback">
        <strong>Playback</strong>
        <Row>
          <Button
            type="button"
            data-testid="adaptive-playback-play"
            disabled={!score}
            onClick={() => startAdaptivePlayback('live')}
          >
            Play
          </Button>
          <Secondary type="button" data-testid="adaptive-playback-stop" onClick={() => stopAdaptivePlayback()}>
            Stop
          </Secondary>
        </Row>
        <span data-testid="adaptive-playback-runtime-state">
          Runtime state: {states.find((state) => state.id === playback?.runtime_state_id)?.name || 'None'}
        </span>
        <span data-testid="adaptive-playback-position">
          {playback ? `${playback.bar}:${playback.beat}` : '—'}
        </span>
        <span data-testid="adaptive-playback-transport">{playback?.transport || 'stopped'}</span>
        <span data-testid="adaptive-playback-queue">{playback?.queue?.length || 0}</span>
        <span data-testid="adaptive-playback-pending">
          {playback?.pending_transition ? `boundary ${playback.pending_transition.boundary_tick}` : 'boundary none'}
        </span>
        <Row>
          {states.map((state) => (
            <Secondary
              key={state.id}
              type="button"
              data-testid={`adaptive-playback-request-${state.id}`}
              disabled={!playback || playback.transport === 'stopped'}
              onClick={() => requestAdaptivePlaybackState(state.id)}
            >
              {state.name}
            </Secondary>
          ))}
        </Row>
        {playbackError ? <p>{playbackError}</p> : null}
      </section>
      <AdaptiveMusicalContextPanel />
      <section>
        <strong>Runtime intensity</strong>
        <Row>
          <Control
            type="range"
            min="0"
            max="1"
            step="0.01"
            aria-label="Runtime intensity"
            data-testid="adaptive-intensity"
            value={runtimeIntensity}
            onChange={(event) => {
              setRuntimeIntensity(event.target.value);
              useMusicStore.setState({ adaptiveLayerIntensity: null });
            }}
            onMouseUp={(event) => commitRuntimeIntensity(event.currentTarget.value)}
            onKeyUp={(event) => commitRuntimeIntensity(event.currentTarget.value)}
          />
          <Control
            aria-label="Playhead tick"
            data-testid="adaptive-playhead-tick"
            value={playheadTick}
            onChange={(event) => setPlayheadTick(event.target.value)}
          />
          <Button
            type="button"
            data-testid="adaptive-map-layers"
            onClick={() => mapAdaptiveLayers(Number(runtimeIntensity), parsedPlayhead(playheadTick))}
          >
            Map layers
          </Button>
        </Row>
        {(layerIntensity?.layers || []).map((row) => {
          const stored = (score.layers || []).find((layer) => layer.id === row.layer_id);
          const windowLabel = stored
            ? `${stored.intensity_min}–${stored.intensity_max}`
            : '';
          return (
            <div key={row.layer_id} data-testid={`adaptive-layer-row-${row.layer_id}`}>
              <span data-testid={`adaptive-layer-active-${row.layer_id}`}>
                {row.active ? 'active' : 'inactive'}
              </span>
              <span>{formatAdaptiveLayerStatus(row)}</span>
              <span>{windowLabel}</span>
              <span>{row.audible ? 'audible' : 'waiting'}</span>
              <span>{row.reason}</span>
              <span>{row.fade_end_tick}</span>
            </div>
          );
        })}
        {layerIntensityError ? <p>{layerIntensityError}</p> : null}
        <Row>
          <Button
            type="button"
            data-testid="adaptive-session-mute"
            disabled={!(layerIntensity?.layers || []).length || (layerIntensity?.layers || []).some((row) => row.material_kind !== 'track_range')}
            onClick={() => applySessionMute()}
          >
            Session mute
          </Button>
          <Secondary type="button" data-testid="adaptive-restore-mute" onClick={() => restoreSessionMute()}>
            Restore mute
          </Secondary>
        </Row>
        <details>
          <summary>Plan proposals</summary>
          <Row>
            <Select aria-label="Proposal role" value={planRole} onChange={(event) => setPlanRole(event.target.value)}>
              {LAYER_ROLES.map((role) => (
                <option key={role} value={role}>{role}</option>
              ))}
            </Select>
            <Control aria-label="Proposal minimum" value={planMin} onChange={(event) => setPlanMin(event.target.value)} />
            <Control aria-label="Proposal maximum" value={planMax} onChange={(event) => setPlanMax(event.target.value)} />
            <Secondary
              type="button"
              onClick={() => {
                const material = selected?.material
                  || (sectionOptions[0]?.id
                    ? { kind: 'section', section_id: sectionOptions[0].id }
                    : { kind: 'bar_range', start_bar: 1, end_bar: 1 });
                setPlanProposals((current) => [
                  ...current,
                  {
                    name: planRole,
                    role: planRole,
                    intensity_min: Number(planMin),
                    intensity_max: Number(planMax),
                    mix_hint: 'bed',
                    material,
                  },
                ]);
              }}
            >
              Add proposal
            </Secondary>
            <Secondary
              type="button"
              data-testid="adaptive-preview-plan"
              disabled={!planProposals.length}
              onClick={() => previewLayerPlan(planProposals, Number(runtimeIntensity))}
            >
              Preview plan
            </Secondary>
            <Button
              type="button"
              data-testid="adaptive-apply-plan"
              disabled={!planProposals.length}
              onClick={() => applyLayerPlan(planProposals)}
            >
              Apply plan
            </Button>
          </Row>
          {(layerPlanPreview?.layers || []).map((row) => (
            <div key={row.layer_id} data-testid={`adaptive-plan-row-${row.layer_id}`}>
              {formatAdaptiveLayerStatus(row)}
            </div>
          ))}
        </details>
      </section>
      <GraphRegion data-testid="adaptive-graph">
        <GraphStage style={{ width: Math.max(layout.width, 180), height: Math.max(layout.height, 96) }}>
          <svg width={Math.max(layout.width, 180)} height={Math.max(layout.height, 96)} aria-hidden="true">
            {layout.edges.map((edge) => (
              <line
                key={edge.id}
                data-testid={`adaptive-transition-${edge.id}`}
                x1={edge.x1}
                y1={edge.y1}
                x2={edge.x2}
                y2={edge.y2}
                stroke="#64748b"
                strokeWidth="2"
              />
            ))}
          </svg>
          {layout.nodes.map((node) => (
            <StateCard
              key={node.id}
              type="button"
              data-testid={`adaptive-state-${node.id}`}
              $selected={node.selected}
              style={{ left: node.x, top: node.y, width: node.width }}
              onClick={() => selectAdaptiveState(node.id)}
            >
              <strong>{node.name}</strong>
              <span>{node.materialLabel}</span>
            </StateCard>
          ))}
        </GraphStage>
      </GraphRegion>
      <FormStack
        data-testid="adaptive-transition-schedule-form"
        onSubmit={(event) => {
          event.preventDefault();
          const tick = Number.parseInt(positionTick, 10);
          scheduleAdaptiveTransition(scheduleToStateId, Number.isNaN(tick) ? 0 : tick);
        }}
      >
        <strong>Scheduled transition</strong>
        <Select
          aria-label="Schedule destination"
          data-testid="adaptive-schedule-to"
          value={scheduleToStateId}
          onChange={(event) => setScheduleToStateId(event.target.value)}
        >
          <option value="">Choose destination</option>
          {states.filter((state) => state.id !== selectedStateId).map((state) => (
            <option key={state.id} value={state.id}>{state.name}</option>
          ))}
        </Select>
        <Control
          aria-label="Position tick"
          data-testid="adaptive-schedule-position"
          value={positionTick}
          onChange={(event) => setPositionTick(event.target.value)}
        />
        <Button type="submit" data-testid="adaptive-transition-schedule" disabled={!scheduleToStateId || !selected}>
          Schedule
        </Button>
        {scheduled ? (
          <div data-testid="adaptive-scheduled-status">
            <span>{scheduled.quantization}</span>
            <span>boundary {scheduled.boundary_tick}</span>
            <span>bar {scheduled.boundary_bar}</span>
            <span data-testid="adaptive-transition-latency">{scheduled.latency_ms} ms</span>
            <span>{scheduled.tempo_bpm} bpm</span>
            <span>{scheduled.time_signature}</span>
            <span>{scheduled.realization?.kind}</span>
            <Secondary type="button" data-testid="adaptive-transition-cancel" onClick={() => cancelAdaptiveTransition()}>
              Cancel
            </Secondary>
          </div>
        ) : null}
      </FormStack>
      <FormStack
        onSubmit={(event) => {
          const name = stateName.trim();
          if (!name) {
            event.preventDefault();
            return;
          }
          setStateName('');
          submitCommand(event, run, 'create_state', { name });
        }}
      >
        <Control
          aria-label="State name"
          data-testid="adaptive-state-name"
          value={stateName}
          onChange={(event) => setStateName(event.target.value)}
          placeholder="State name"
        />
        <Button type="submit" data-testid="adaptive-create-state">Add state</Button>
      </FormStack>
      {selected ? (
        <>
          <Row>
            <Secondary type="button" onClick={() => run('delete_state', { state_id: selected.id })}>
              Delete state
            </Secondary>
            <Secondary type="button" onClick={() => run('duplicate_state', { state_id: selected.id })}>
              Duplicate state
            </Secondary>
          </Row>
          <FormStack
            onSubmit={(event) => {
              const material = materialKind === 'section'
                ? {
                  kind: 'section',
                  section_id: sectionId,
                  ...(revisionId ? { revision_id: revisionId } : {}),
                }
                : {
                  kind: 'bar_range',
                  start_bar: Number(startBar),
                  end_bar: Number(endBar),
                  ...(revisionId ? { revision_id: revisionId } : {}),
                };
              submitCommand(event, run, 'assign_material', { state_id: selected.id, material });
            }}
          >
            <Select aria-label="Material kind" data-testid="adaptive-material-kind" value={materialKind} onChange={(event) => setMaterialKind(event.target.value)}>
              <option value="section">Section</option>
              <option value="bar_range">Bar range</option>
            </Select>
            {materialKind === 'section' ? (
              <Select aria-label="Section" data-testid="adaptive-material-section" value={sectionId} onChange={(event) => setSectionId(event.target.value)}>
                {sectionOptions.map((section) => (
                  <option key={section.id} value={section.id}>{section.id}</option>
                ))}
              </Select>
            ) : (
              <Row>
                <Control aria-label="Start bar" data-testid="adaptive-bar-start" value={startBar} onChange={(event) => setStartBar(event.target.value)} />
                <Control aria-label="End bar" data-testid="adaptive-bar-end" value={endBar} onChange={(event) => setEndBar(event.target.value)} />
              </Row>
            )}
            <Select aria-label="Revision" value={revisionId} onChange={(event) => setRevisionId(event.target.value)}>
              <option value="">Working composition</option>
              {revisions.map((revision) => (
                <option key={revision.id} value={revision.id}>{revision.id}</option>
              ))}
            </Select>
            <Button type="submit" data-testid="adaptive-assign-material">Assign material</Button>
          </FormStack>
          <FormStack
            onSubmit={(event) => submitCommand(event, run, 'create_transition', {
              from_state_id: selected.id,
              to_state_id: toStateId || selected.id,
              quantization: 'bar',
              conditions: [{ kind: 'manual' }],
            })}
          >
            <Select aria-label="Transition destination" data-testid="adaptive-transition-to" value={toStateId} onChange={(event) => setToStateId(event.target.value)}>
              <option value="">Choose destination</option>
              {states.map((state) => (
                <option key={state.id} value={state.id}>{state.name}</option>
              ))}
            </Select>
            <Button type="submit" data-testid="adaptive-create-transition">Add transition</Button>
          </FormStack>
          <FormStack
            onSubmit={(event) => submitCommand(event, run, 'edit_transition', {
              transition_id: transitionId,
              quantization,
            })}
          >
            <Select aria-label="Transition" value={transitionId} onChange={(event) => setTransitionId(event.target.value)}>
              <option value="">Choose transition</option>
              {(score.transitions || []).map((transition) => (
                <option key={transition.id} value={transition.id}>{transition.id}</option>
              ))}
            </Select>
            <Select aria-label="Quantization" value={quantization} onChange={(event) => setQuantization(event.target.value)}>
              {TRANSITION_QUANTIZATIONS.map((token) => (
                <option key={token} value={token}>{token}</option>
              ))}
            </Select>
            <Button type="submit" disabled={!transitionId}>Edit transition</Button>
          </FormStack>
          <FormStack
            onSubmit={(event) => submitCommand(event, run, 'assign_intensity', {
              state_id: selected.id,
              intensity: Number(intensity),
            })}
          >
            <Control aria-label="Intensity" value={intensity} onChange={(event) => setIntensity(event.target.value)} />
            <Button type="submit">Assign intensity</Button>
          </FormStack>
          <FormStack
            onSubmit={(event) => submitCommand(event, run, 'assign_loop', {
              state_id: selected.id,
              enabled: true,
              start_bar: Number(loopStart),
              end_bar: Number(loopEnd),
            })}
          >
            <Row>
              <Control aria-label="Loop start bar" data-testid="adaptive-loop-start" value={loopStart} onChange={(event) => setLoopStart(event.target.value)} />
              <Control aria-label="Loop end bar" data-testid="adaptive-loop-end" value={loopEnd} onChange={(event) => setLoopEnd(event.target.value)} />
            </Row>
            <Button type="submit" data-testid="adaptive-assign-loop">Assign loop</Button>
          </FormStack>
          <FormStack
            onSubmit={(event) => {
              const boundary = boundaryKind === 'tick'
                ? { kind: 'tick', tick: Number(boundaryValue) }
                : { kind: 'bar', bar: Number(boundaryValue) };
              submitCommand(event, run, 'assign_boundary', {
                state_id: selected.id,
                which: event.nativeEvent.submitter?.value || 'entry',
                boundary,
              });
            }}
          >
            <Select aria-label="Boundary unit" value={boundaryKind} onChange={(event) => setBoundaryKind(event.target.value)}>
              <option value="bar">bar</option>
              <option value="tick">tick</option>
            </Select>
            <Control aria-label="Boundary value" value={boundaryValue} onChange={(event) => setBoundaryValue(event.target.value)} />
            <Row>
              <Button type="submit" value="entry">Assign entry</Button>
              <Button type="submit" value="exit">Assign exit</Button>
            </Row>
          </FormStack>
        </>
      ) : null}
      <Secondary type="button" data-testid="adaptive-validate-button" onClick={onValidate}>
        Validate
      </Secondary>
      {commandError ? <p>{commandError}</p> : null}
      <div>
        {findings.map((finding, index) => (
          <Finding
            key={`${finding.code}-${finding.target_id || index}`}
            type="button"
            data-testid={`adaptive-finding-${finding.code}`}
            onClick={() => focusFinding(finding)}
          >
            {finding.message || finding.code}
          </Finding>
        ))}
      </div>
    </Panel>
  );
};

export default AdaptiveScorePanel;
