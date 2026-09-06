import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const Container = styled.div`
  h2 {
    color: #333;
    margin-bottom: 8px;
    font-size: 1.5rem;
    font-weight: 600;
  }
`;

const Lead = styled.p`
  color: #6b7280;
  margin-bottom: 20px;
`;

const Toolbar = styled.div`
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 20px;
`;

const Button = styled.button`
  padding: 10px 16px;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  background: ${(props) => (props.$secondary ? '#e5e7eb' : '#667eea')};
  color: ${(props) => (props.$secondary ? '#374151' : 'white')};

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
`;

const DangerButton = styled(Button)`
  background: #dc2626;
`;

const EmptyState = styled.div`
  padding: 28px;
  text-align: center;
  color: #6b7280;
  background: #f8fafc;
  border-radius: 12px;
  border: 1px dashed #cbd5e1;
`;

const List = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
`;

const Item = styled.li`
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 16px;
  background: #f8f9ff;
  border: 1px solid #e0e7ff;
  border-radius: 12px;

  @media (max-width: 720px) {
    grid-template-columns: 1fr;
  }
`;

const Meta = styled.div`
  color: #6b7280;
  font-size: 0.85rem;
  margin-top: 4px;
`;

const Actions = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`;

const RenameRow = styled.div`
  display: flex;
  gap: 8px;
  margin-top: 8px;
`;

const Input = styled.input`
  flex: 1;
  padding: 8px 10px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
`;

const ConfirmBox = styled.div`
  margin-top: 10px;
  padding: 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 8px;
  color: #991b1b;
`;

function formatTimestamp(value) {
  if (!value) {
    return '—';
  }
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

const ProjectBrowser = () => {
  const projectList = useMusicStore((state) => state.projectList);
  const projectListStatus = useMusicStore((state) => state.projectListStatus);
  const loadProjectList = useMusicStore((state) => state.loadProjectList);
  const createNewProject = useMusicStore((state) => state.createNewProject);
  const openProject = useMusicStore((state) => state.openProject);
  const renameProjectById = useMusicStore((state) => state.renameProjectById);
  const duplicateProjectById = useMusicStore((state) => state.duplicateProjectById);
  const deleteProjectById = useMusicStore((state) => state.deleteProjectById);
  const uiError = useMusicStore((state) => state.uiError);

  const [busyId, setBusyId] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState('');
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  useEffect(() => {
    loadProjectList().catch(() => {});
  }, [loadProjectList]);

  const runAction = async (projectId, action) => {
    setBusyId(projectId || 'new');
    try {
      await action();
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Container>
      <h2>Projects</h2>
      <Lead>Create or reopen a local project. Edits autosave to the backend SQLite database.</Lead>

      <Toolbar>
        <Button
          type="button"
          disabled={Boolean(busyId)}
          onClick={() => {
            console.debug('[ProjectBrowser] New Project clicked');
            runAction(null, () => createNewProject('Untitled Project'));
          }}
        >
          New Project
        </Button>
        <Button
          type="button"
          $secondary
          disabled={Boolean(busyId)}
          onClick={() => {
            console.debug('[ProjectBrowser] Refresh clicked');
            runAction(null, () => loadProjectList());
          }}
        >
          Refresh
        </Button>
      </Toolbar>

      {uiError && <p style={{ color: '#991b1b' }}>{uiError}</p>}
      {projectListStatus === 'loading' && <p>Loading projects…</p>}

      {!projectList.length && projectListStatus !== 'loading' ? (
        <EmptyState>
          No projects yet. Create one to generate music and keep edits after Docker restarts.
        </EmptyState>
      ) : (
        <List>
          {projectList.map((project) => (
            <Item key={project.id}>
              <div>
                <strong>{project.name}</strong>
                <Meta>
                  Updated {formatTimestamp(project.updated_at)}
                  {project.has_composition
                    ? ` · ${project.track_count} tracks · ${project.event_count} events · ${project.bar_count} bars`
                    : ' · empty'}
                </Meta>

                {renamingId === project.id && (
                  <RenameRow>
                    <Input
                      value={renameValue}
                      onChange={(event) => setRenameValue(event.target.value)}
                      aria-label="Project name"
                    />
                    <Button
                      type="button"
                      disabled={busyId === project.id}
                      onClick={() => {
                        console.debug('[ProjectBrowser] Rename confirm', { projectId: project.id });
                        runAction(project.id, async () => {
                          await renameProjectById(project.id, renameValue);
                          setRenamingId(null);
                        });
                      }}
                    >
                      Save
                    </Button>
                    <Button
                      type="button"
                      $secondary
                      onClick={() => {
                        console.debug('[ProjectBrowser] Rename cancel', { projectId: project.id });
                        setRenamingId(null);
                      }}
                    >
                      Cancel
                    </Button>
                  </RenameRow>
                )}

                {confirmDeleteId === project.id && (
                  <ConfirmBox>
                    Delete “{project.name}”? This cannot be undone.
                    <Actions style={{ marginTop: 10 }}>
                      <DangerButton
                        type="button"
                        disabled={busyId === project.id}
                        onClick={() => {
                          console.debug('[ProjectBrowser] Delete confirm', { projectId: project.id });
                          runAction(project.id, async () => {
                            await deleteProjectById(project.id);
                            setConfirmDeleteId(null);
                          });
                        }}
                      >
                        Confirm delete
                      </DangerButton>
                      <Button
                        type="button"
                        $secondary
                        onClick={() => {
                          console.debug('[ProjectBrowser] Delete cancel', { projectId: project.id });
                          setConfirmDeleteId(null);
                        }}
                      >
                        Cancel
                      </Button>
                    </Actions>
                  </ConfirmBox>
                )}
              </div>

              <Actions>
                <Button
                  type="button"
                  disabled={Boolean(busyId)}
                  onClick={() => {
                    console.debug('[ProjectBrowser] Open', { projectId: project.id });
                    runAction(project.id, () => openProject(project.id));
                  }}
                >
                  Open
                </Button>
                <Button
                  type="button"
                  $secondary
                  disabled={Boolean(busyId)}
                  onClick={() => {
                    console.debug('[ProjectBrowser] Rename start', { projectId: project.id });
                    setRenamingId(project.id);
                    setRenameValue(project.name);
                    setConfirmDeleteId(null);
                  }}
                >
                  Rename
                </Button>
                <Button
                  type="button"
                  $secondary
                  disabled={Boolean(busyId)}
                  onClick={() => {
                    console.debug('[ProjectBrowser] Duplicate', { projectId: project.id });
                    runAction(project.id, () => duplicateProjectById(project.id));
                  }}
                >
                  Duplicate
                </Button>
                <DangerButton
                  type="button"
                  disabled={Boolean(busyId)}
                  onClick={() => {
                    console.debug('[ProjectBrowser] Delete prompt', { projectId: project.id });
                    setConfirmDeleteId(project.id);
                    setRenamingId(null);
                  }}
                >
                  Delete
                </DangerButton>
              </Actions>
            </Item>
          ))}
        </List>
      )}
    </Container>
  );
};

export default ProjectBrowser;
