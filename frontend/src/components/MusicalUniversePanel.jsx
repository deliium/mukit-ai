import React, { useEffect, useMemo, useState } from 'react';
import styled from 'styled-components';

import {
  acceptDependencyCurrent,
  addMusicalUniverseMember,
  createMusicalUniverse,
  getProjectMusicalUniverse,
  getUniverseDependencyGraph,
  reuseMusicalUniverseTheme,
} from '../api/musicalUniverseApi.js';
import DependencyGraph from './DependencyGraph.jsx';
import { getProject } from '../api/projectApi.js';
import { useMusicStore } from '../store/musicStore.js';
import {
  buildThemeReuseRequest,
  reuseParameters,
  universeReuseBlockReason,
} from '../utils/musicalUniverseReuse.js';

const KINDS = ['character', 'location', 'faction', 'concept', 'relationship', 'narrative_theme'];
const OPERATIONS = ['repeat', 'transpose', 'inversion', 'augmentation', 'diminution', 'sequence'];

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
`;

const Title = styled.h3`
  margin: 0;
  color: #1e293b;
`;

const Notice = styled.p`
  margin: 0;
  color: #9a3412;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

function branchFromProject(project) {
  return {
    branch_id: project.active_branch_id,
    expected_active_branch_id: project.active_branch_id,
    expected_working_version: project.working_version,
    expected_head_revision_id: project.current_revision_id,
    expected_source_fingerprint: project.working_fingerprint,
  };
}

const MusicalUniversePanel = () => {
  const projectId = useMusicStore((state) => state.currentProjectId);
  const saveStatus = useMusicStore((state) => state.saveStatus);
  const editedMusicJson = useMusicStore((state) => state.editedMusicJson);
  const generationMeta = useMusicStore((state) => state.generationMeta);
  const lastSavedPersistRevision = useMusicStore((state) => state.lastSavedPersistRevision);
  const activeBranchId = useMusicStore((state) => state.activeBranchId);
  const workingVersion = useMusicStore((state) => state.workingVersion);
  const currentRevisionId = useMusicStore((state) => state.currentRevisionId);
  const workingFingerprint = useMusicStore((state) => state.workingFingerprint);
  const openProject = useMusicStore((state) => state.openProject);

  const [loaded, setLoaded] = useState(null);
  const [name, setName] = useState('');
  const [joinId, setJoinId] = useState('');
  const [selectedThemeId, setSelectedThemeId] = useState('');
  const [destinationId, setDestinationId] = useState('');
  const [trackId, setTrackId] = useState('');
  const [startBar, setStartBar] = useState(2);
  const [operation, setOperation] = useState('transpose');
  const [semitones, setSemitones] = useState(2);
  const [axis, setAxis] = useState('');
  const [numerator, setNumerator] = useState(2);
  const [denominator, setDenominator] = useState(1);
  const [steps, setSteps] = useState(2);
  const [interval, setInterval] = useState(2);
  const [stepTicks, setStepTicks] = useState(1920);
  const [errorCode, setErrorCode] = useState('');
  const [dependencyGraph, setDependencyGraph] = useState(null);
  const [graphToken, setGraphToken] = useState(0);
  const [remoteTracks, setRemoteTracks] = useState([]);
  const [remoteBranch, setRemoteBranch] = useState(null);

  const draftState = useMemo(() => ({
    currentProjectId: projectId,
    saveStatus,
    editedMusicJson,
    generationMeta,
    lastSavedPersistRevision,
  }), [projectId, saveStatus, editedMusicJson, generationMeta, lastSavedPersistRevision]);
  const blockReason = universeReuseBlockReason(draftState);

  useEffect(() => {
    if (!projectId) {
      return undefined;
    }
    let cancelled = false;
    setErrorCode('');
    getProjectMusicalUniverse(projectId)
      .then((document) => {
        if (!cancelled) {
          setLoaded(document);
          setDestinationId(projectId);
        }
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setLoaded(null);
        if (error.code !== 'universe_not_linked') {
          setErrorCode(error.code || 'musical_universe_invalid');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (!destinationId || destinationId === projectId) {
      setRemoteBranch(null);
      setRemoteTracks([]);
      return undefined;
    }
    let cancelled = false;
    getProject(destinationId)
      .then((project) => {
        if (cancelled) {
          return;
        }
        setRemoteBranch(branchFromProject(project));
        setRemoteTracks(Array.isArray(project.composition?.tracks) ? project.composition.tracks : []);
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorCode(error.code || 'musical_universe_invalid');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [destinationId, projectId]);

  const universe = loaded?.universe;
  const universeId = universe?.id || '';

  useEffect(() => {
    if (!universeId) {
      setDependencyGraph(null);
      return undefined;
    }
    let cancelled = false;
    getUniverseDependencyGraph(universeId)
      .then((graph) => {
        if (!cancelled) {
          setDependencyGraph(graph);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorCode(error.code || 'dependency_invalid');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [universeId, graphToken]);

  const themes = universe?.themes || [];
  const selectedTheme = themes.find((theme) => theme.id === selectedThemeId) || themes[0] || null;

  function reviewDependency(edgeId) {
    if (!edgeId || !selectedTheme) {
      return;
    }
    const edge = (dependencyGraph?.edges || []).find((item) => item.edge_id === edgeId);
    const node = (dependencyGraph?.nodes || []).find((item) => item.node_key === edge?.downstream_node_key);
    const variant = (selectedTheme.variants || []).find((item) => item.id === node?.variant_id);
    if (!variant) {
      return;
    }
    setOperation(variant.operation || 'transpose');
    if (variant.parameters?.transpose_semitones != null) {
      setSemitones(variant.parameters.transpose_semitones);
    }
  }

  async function acceptDependency(edgeId) {
    if (!edgeId || !universeId) {
      return;
    }
    try {
      await acceptDependencyCurrent(edgeId);
      setGraphToken((token) => token + 1);
      setErrorCode('');
    } catch (error) {
      setErrorCode(error.code || 'dependency_invalid');
    }
  }
  const openTracks = Array.isArray(editedMusicJson?.tracks) ? editedMusicJson.tracks : [];
  const tracks = destinationId && destinationId !== projectId ? remoteTracks : openTracks;

  async function createUniverse(event) {
    event.preventDefault();
    try {
      const document = await createMusicalUniverse(name.trim(), projectId);
      setLoaded(document);
      setDestinationId(projectId);
      setErrorCode('');
    } catch (error) {
      setErrorCode(error.code || 'musical_universe_invalid');
    }
  }

  async function joinUniverse(event) {
    event.preventDefault();
    try {
      const document = await addMusicalUniverseMember(joinId.trim(), projectId);
      setLoaded(document);
      setErrorCode('');
    } catch (error) {
      setErrorCode(error.code || 'musical_universe_invalid');
    }
  }

  async function reuseTheme(event) {
    event.preventDefault();
    if (!selectedTheme || blockReason) {
      return;
    }
    const branch = destinationId === projectId
      ? {
        branch_id: activeBranchId,
        expected_active_branch_id: activeBranchId,
        expected_working_version: workingVersion,
        expected_head_revision_id: currentRevisionId,
        expected_source_fingerprint: workingFingerprint,
      }
      : remoteBranch;
    if (!branch?.branch_id) {
      return;
    }
    const request = buildThemeReuseRequest({
      operation,
      parameters: reuseParameters(operation, {
        semitones,
        axis,
        numerator,
        denominator,
        steps,
        interval,
        stepTicks,
      }),
      destinationProjectId: destinationId,
      destinationTrackId: trackId,
      destinationStartBar: Number(startBar),
      branch,
      expectedUniverseRevision: loaded.document_revision,
    });
    try {
      const document = await reuseMusicalUniverseTheme(universe.id, selectedTheme.id, request);
      setLoaded(document);
      setErrorCode('');
      if (destinationId === projectId) {
        await openProject(projectId);
      }
    } catch (error) {
      setErrorCode(error.code || 'musical_universe_invalid');
    }
  }

  if (!projectId) {
    return <Panel><Title>Universe</Title><p>Open a project to load its musical universe.</p></Panel>;
  }

  if (!loaded) {
    return (
      <Panel>
        <Title>Universe</Title>
        {errorCode ? <Notice>{errorCode}</Notice> : null}
        <form onSubmit={createUniverse}>
          <Row>
            <label htmlFor="universe-name">Name</label>
            <input id="universe-name" value={name} onChange={(event) => setName(event.target.value)} />
            <button type="submit">Create universe</button>
          </Row>
        </form>
        <form onSubmit={joinUniverse}>
          <Row>
            <label htmlFor="universe-join">Universe id</label>
            <input id="universe-join" value={joinId} onChange={(event) => setJoinId(event.target.value)} />
            <button type="submit">Join universe</button>
          </Row>
        </form>
      </Panel>
    );
  }

  return (
    <Panel>
      <Title>{universe.name}</Title>
      {errorCode ? <Notice>{errorCode}</Notice> : null}
      {KINDS.map((kind) => {
        const entities = (universe.entities || []).filter((entity) => entity.kind === kind);
        if (entities.length === 0) {
          return null;
        }
        return (
          <section key={kind}>
            <h4>{kind}</h4>
            <ul>
              {entities.map((entity) => <li key={entity.id}>{entity.label}</li>)}
            </ul>
          </section>
        );
      })}
      <section>
        <h4>Themes</h4>
        <ul>
          {themes.map((theme) => (
            <li key={theme.id}>
              <button type="button" onClick={() => setSelectedThemeId(theme.id)}>{theme.label}</button>
            </li>
          ))}
        </ul>
        {selectedTheme ? (
          <DependencyGraph
            graph={dependencyGraph}
            rootKey={`theme:${universe.id}:${selectedTheme.id}`}
            onReview={reviewDependency}
            onAccept={acceptDependency}
          />
        ) : null}
        {selectedTheme ? (
          <div>
            <p>
              Source {selectedTheme.source.project_id} {selectedTheme.source.motif_id} {selectedTheme.source.occurrence_id}
            </p>
            <h5>Variants</h5>
            <ul>
              {(selectedTheme.variants || []).map((variant) => (
                <li key={variant.id}>{variant.label} {variant.operation}</li>
              ))}
            </ul>
            <h5>Usages</h5>
            <ul>
              {(selectedTheme.usages || []).map((usage) => (
                <li key={usage.id}>{usage.destination_project_id} {usage.operation}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>
      <form onSubmit={reuseTheme}>
        <h4>Reuse</h4>
        {blockReason ? <Notice>Save the open project before reusing a theme.</Notice> : null}
        <Row>
          <label htmlFor="universe-operation">Operation</label>
          <select id="universe-operation" value={operation} onChange={(event) => setOperation(event.target.value)}>
            {OPERATIONS.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          {operation === 'transpose' ? (
            <>
              <label htmlFor="universe-semitones">Semitones</label>
              <input id="universe-semitones" type="number" min="-48" max="48" value={semitones} onChange={(event) => setSemitones(event.target.value)} />
            </>
          ) : null}
          {operation === 'inversion' ? (
            <>
              <label htmlFor="universe-axis">Axis pitch</label>
              <input id="universe-axis" value={axis} placeholder="optional" onChange={(event) => setAxis(event.target.value)} />
            </>
          ) : null}
          {operation === 'augmentation' || operation === 'diminution' ? (
            <>
              <label htmlFor="universe-numerator">Numerator</label>
              <input id="universe-numerator" type="number" min="1" max="8" value={numerator} onChange={(event) => setNumerator(event.target.value)} />
              <label htmlFor="universe-denominator">Denominator</label>
              <input id="universe-denominator" type="number" min="1" max="8" value={denominator} onChange={(event) => setDenominator(event.target.value)} />
            </>
          ) : null}
          {operation === 'sequence' ? (
            <>
              <label htmlFor="universe-steps">Steps</label>
              <input id="universe-steps" type="number" min="1" max="16" value={steps} onChange={(event) => setSteps(event.target.value)} />
              <label htmlFor="universe-interval">Interval semitones</label>
              <input id="universe-interval" type="number" min="-24" max="24" value={interval} onChange={(event) => setInterval(event.target.value)} />
              <label htmlFor="universe-step-ticks">Step ticks</label>
              <input id="universe-step-ticks" type="number" min="1" value={stepTicks} onChange={(event) => setStepTicks(event.target.value)} />
            </>
          ) : null}
        </Row>
        <Row>
          <label htmlFor="universe-destination">Destination</label>
          <select id="universe-destination" value={destinationId} onChange={(event) => setDestinationId(event.target.value)}>
            {(loaded.member_project_ids || []).map((memberId) => (
              <option key={memberId} value={memberId}>{memberId}</option>
            ))}
          </select>
          <label htmlFor="universe-track">Track</label>
          <select id="universe-track" value={trackId} onChange={(event) => setTrackId(event.target.value)}>
            <option value="">Select track</option>
            {tracks.map((track) => <option key={track.id} value={track.id}>{track.id}</option>)}
          </select>
          <label htmlFor="universe-bar">Start bar</label>
          <input id="universe-bar" type="number" min="1" value={startBar} onChange={(event) => setStartBar(event.target.value)} />
          <button type="submit" disabled={Boolean(blockReason)}>Reuse theme</button>
        </Row>
      </form>
    </Panel>
  );
};

export default MusicalUniversePanel;
