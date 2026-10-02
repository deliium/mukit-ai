import React, { useCallback, useEffect, useMemo, useState } from 'react';
import styled from 'styled-components';
import {
  deletePersonalComposer,
  evaluatePersonalComposer,
  listPersonalComposers,
  resumePersonalComposer,
  startPersonalComposer,
  stopPersonalComposer,
} from '../api/personalComposerApi.js';
import { listProjects } from '../api/projectApi.js';
import {
  rightsForRequest,
  selectionEligible,
} from '../utils/personalComposerForm.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #e2e8f0;
`;

const Title = styled.h4`
  margin: 0;
  color: #312e81;
  font-size: 1rem;
`;

const Hint = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #64748b;
  line-height: 1.4;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  min-height: 40px;
  padding: 8px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #fff;
  color: #334155;
  font-weight: 600;
  cursor: pointer;

  &:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }
`;

const PrimaryButton = styled(Button)`
  border-color: #4f46e5;
  background: #eef2ff;
  color: #312e81;
`;

const Input = styled.input`
  min-height: 40px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  min-width: 160px;
`;

const Select = styled.select`
  min-height: 40px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
`;

const ProjectBlock = styled.label`
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
`;

const Status = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: #334155;
`;

const STATUSES = [
  'unknown',
  'user_owned',
  'verified_redistributable',
  'public_domain',
  'restricted',
];

function emptyRights() {
  return {
    status: 'unknown',
    user_owned_attested: false,
    license: '',
    license_spdx: '',
    source_url: '',
    source_reference: '',
  };
}

function projectRows(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.projects)) return payload.projects;
  return [];
}

export default function PersonalComposerPanel() {
  const [projects, setProjects] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [selected, setSelected] = useState({});
  const [rights, setRights] = useState({});
  const [displayName, setDisplayName] = useState('MyComposer-v1');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [projectPayload, jobPayload] = await Promise.all([
      listProjects().catch(() => ({ projects: [] })),
      listPersonalComposers().catch(() => []),
    ]);
    const nextJobs = Array.isArray(jobPayload) ? jobPayload : [];
    setProjects(projectRows(projectPayload));
    setJobs(nextJobs);
    console.debug('[PersonalComposer] listed adapters', { count: nextJobs.length });
  }, []);

  useEffect(() => {
    let cancelled = false;
    load().catch((error) => {
      if (!cancelled) setMessage(error.message || 'Could not list personal composers');
    });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const selectedIds = useMemo(
    () => projects.map((project) => project.id).filter((id) => selected[id]),
    [projects, selected],
  );
  const rightsById = useMemo(() => {
    const mapped = {};
    selectedIds.forEach((projectId) => {
      mapped[projectId] = rightsForRequest(rights[projectId] || emptyRights());
    });
    return mapped;
  }, [rights, selectedIds]);
  const eligible = selectionEligible(selectedIds, rightsById, displayName);

  const toggle = (projectId) => {
    setSelected((current) => ({ ...current, [projectId]: !current[projectId] }));
    setRights((current) => (
      current[projectId] ? current : { ...current, [projectId]: emptyRights() }
    ));
  };

  const patchRights = (projectId, patch) => {
    setRights((current) => ({
      ...current,
      [projectId]: { ...(current[projectId] || emptyRights()), ...patch },
    }));
  };

  const train = async () => {
    console.debug('[PersonalComposer] train clicked', {
      projectCount: selectedIds.length,
      eligible,
    });
    if (!eligible || busy) return;
    setBusy(true);
    setMessage('');
    try {
      await startPersonalComposer({
        display_name: displayName,
        project_ids: selectedIds,
        rights: rightsById,
      });
      setMessage('Training finished.');
      await load();
    } catch (error) {
      setMessage(error.message || 'Training failed');
    } finally {
      setBusy(false);
    }
  };

  const runJob = async (action, adapterId) => {
    setBusy(true);
    setMessage('');
    try {
      if (action === 'stop') await stopPersonalComposer(adapterId);
      if (action === 'resume') await resumePersonalComposer(adapterId);
      if (action === 'evaluate') await evaluatePersonalComposer(adapterId);
      if (action === 'delete') await deletePersonalComposer(adapterId);
      await load();
    } catch (error) {
      setMessage(error.message || 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel data-testid="personal-composer-panel">
      <Title>Personal composer</Title>
      <Hint>
        Train starts only when you submit this form. Opening the tab lists adapters.
      </Hint>
      <Row>
        <Input
          aria-label="Display name"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
        <PrimaryButton type="button" disabled={!eligible || busy} onClick={train}>
          Train adapter
        </PrimaryButton>
      </Row>
      {projects.map((project) => {
        const draft = rights[project.id] || emptyRights();
        const checked = Boolean(selected[project.id]);
        return (
          <ProjectBlock key={project.id}>
            <span>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(project.id)}
              />
              {' '}
              {project.name || project.id}
            </span>
            {checked ? (
              <Row>
                <Select
                  aria-label={`Provenance for ${project.name || project.id}`}
                  value={draft.status}
                  onChange={(event) => patchRights(project.id, { status: event.target.value })}
                >
                  {STATUSES.map((status) => (
                    <option key={status} value={status}>{status}</option>
                  ))}
                </Select>
                {draft.status === 'user_owned' ? (
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.user_owned_attested === true}
                      onChange={(event) => patchRights(project.id, {
                        user_owned_attested: event.target.checked,
                      })}
                    />
                    {' '}
                    I own this score
                  </label>
                ) : null}
                {draft.status === 'verified_redistributable' || draft.status === 'public_domain' ? (
                  <>
                    <Input
                      aria-label="License"
                      placeholder="License"
                      value={draft.license}
                      onChange={(event) => patchRights(project.id, { license: event.target.value })}
                    />
                    <Input
                      aria-label="Source"
                      placeholder="Source"
                      value={draft.source_reference}
                      onChange={(event) => patchRights(project.id, {
                        source_reference: event.target.value,
                      })}
                    />
                  </>
                ) : null}
              </Row>
            ) : null}
          </ProjectBlock>
        );
      })}
      {jobs.map((job) => (
        <Row key={job.adapter_id}>
          <span>{job.display_name}</span>
          <span>{job.status}</span>
          {job.status === 'running' ? (
            <Button type="button" disabled={busy} onClick={() => runJob('stop', job.adapter_id)}>
              Stop
            </Button>
          ) : null}
          {job.status === 'stopped' || job.status === 'failed' ? (
            <Button type="button" disabled={busy} onClick={() => runJob('resume', job.adapter_id)}>
              Resume
            </Button>
          ) : null}
          {job.status === 'complete' || job.status === 'stopped' ? (
            <Button type="button" disabled={busy} onClick={() => runJob('evaluate', job.adapter_id)}>
              Evaluate
            </Button>
          ) : null}
          <Button type="button" disabled={busy} onClick={() => runJob('delete', job.adapter_id)}>
            Delete
          </Button>
        </Row>
      ))}
      {message ? <Status role="status">{message}</Status> : null}
    </Panel>
  );
}
