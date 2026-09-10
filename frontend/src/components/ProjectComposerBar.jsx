import React from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const Bar = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
`;

const Group = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
`;

const Chip = styled.span`
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
  background: ${(props) => props.$bg};
  color: ${(props) => props.$color};
`;

const Button = styled.button`
  padding: 8px 12px;
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

const NameInput = styled.input`
  min-width: 180px;
  padding: 8px 10px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
`;

const STATUS_STYLES = {
  saved: { bg: '#dcfce7', color: '#166534', label: 'Saved' },
  saving: { bg: '#dbeafe', color: '#1e40af', label: 'Saving…' },
  unsaved: { bg: '#fef3c7', color: '#92400e', label: 'Unsaved' },
  error: { bg: '#fee2e2', color: '#991b1b', label: 'Save error' },
  conflict: { bg: '#ffedd5', color: '#9a3412', label: 'Conflict' },
};

const ProjectComposerBar = () => {
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const currentProjectName = useMusicStore((state) => state.currentProjectName);
  const activeBranchName = useMusicStore((state) => state.activeBranchName);
  const saveStatus = useMusicStore((state) => state.saveStatus);
  const saveError = useMusicStore((state) => state.saveError);
  const goHome = useMusicStore((state) => state.goHome);
  const saveCurrentProject = useMusicStore((state) => state.saveCurrentProject);
  const reloadCurrentProject = useMusicStore((state) => state.reloadCurrentProject);
  const saveConflictAsNewBranch = useMusicStore((state) => state.saveConflictAsNewBranch);
  const renameCurrentProject = useMusicStore((state) => state.renameCurrentProject);
  const [nameDraft, setNameDraft] = React.useState(currentProjectName);
  const [conflictBranchName, setConflictBranchName] = React.useState('');
  const [conflictBusy, setConflictBusy] = React.useState(false);

  React.useEffect(() => {
    setNameDraft(currentProjectName);
  }, [currentProjectName]);

  React.useEffect(() => {
    if (saveStatus !== 'conflict') {
      setConflictBranchName('');
      setConflictBusy(false);
    }
  }, [saveStatus]);

  if (!currentProjectId) {
    return null;
  }

  const status = STATUS_STYLES[saveStatus] || STATUS_STYLES.unsaved;
  const openVersions = () => {
    console.debug('[ProjectComposerBar] Open Versions tab', { projectId: currentProjectId });
    useMusicStore.setState((state) => ({
      composerTabRequest: 'versions',
      composerTabRequestSeq: (state.composerTabRequestSeq || 0) + 1,
    }));
  };

  const onSaveAsBranch = async () => {
    const name = conflictBranchName.trim();
    if (!name || conflictBusy) {
      return;
    }
    setConflictBusy(true);
    console.info('[FIX:conflict-branch] ProjectComposerBar save as branch', {
      projectId: currentProjectId,
      nameLength: name.length,
    });
    try {
      await saveConflictAsNewBranch(name);
      setConflictBranchName('');
    } catch {
      // store records saveError / conflict
    } finally {
      setConflictBusy(false);
    }
  };

  return (
    <Bar>
      <Group>
        <Button
          type="button"
          $secondary
          onClick={() => {
            console.debug('[ProjectComposerBar] Back to projects', { projectId: currentProjectId });
            goHome();
          }}
        >
          Projects
        </Button>
        <NameInput
          value={nameDraft}
          aria-label="Project name"
          onChange={(event) => setNameDraft(event.target.value)}
          onBlur={() => {
            if (nameDraft.trim() && nameDraft.trim() !== currentProjectName) {
              console.debug('[ProjectComposerBar] Rename on blur', { projectId: currentProjectId });
              renameCurrentProject(nameDraft.trim()).catch(() => {});
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.currentTarget.blur();
            }
          }}
        />
        {activeBranchName ? (
          <Chip $bg="#e0e7ff" $color="#3730a3">{activeBranchName}</Chip>
        ) : null}
        <Chip $bg={status.bg} $color={status.color}>{status.label}</Chip>
        {(saveStatus === 'error' || saveStatus === 'conflict') && saveError && (
          <span style={{ color: '#991b1b', fontSize: '0.85rem' }}>{saveError}</span>
        )}
      </Group>
      <Group>
        {saveStatus === 'conflict' ? (
          <>
            <Button
              type="button"
              $secondary
              data-testid="reload-project"
              disabled={conflictBusy}
              onClick={() => {
                console.debug('[ProjectComposerBar] Reload after conflict', { projectId: currentProjectId });
                reloadCurrentProject().catch(() => {});
              }}
            >
              Reload
            </Button>
            <NameInput
              value={conflictBranchName}
              aria-label="Save conflict as new branch name"
              data-testid="conflict-branch-name"
              placeholder="New branch name"
              disabled={conflictBusy}
              onChange={(event) => setConflictBranchName(event.target.value)}
              style={{ minWidth: 140 }}
            />
            <Button
              type="button"
              data-testid="save-conflict-as-branch"
              disabled={conflictBusy || !conflictBranchName.trim()}
              onClick={onSaveAsBranch}
            >
              Save as branch
            </Button>
          </>
        ) : null}
        <Button
          type="button"
          $secondary
          data-testid="open-versions"
          onClick={openVersions}
        >
          History
        </Button>
        <Button
          type="button"
          data-testid="save-project"
          disabled={saveStatus === 'saving' || saveStatus === 'conflict'}
          onClick={() => {
            console.debug('[ProjectComposerBar] Manual save', { projectId: currentProjectId });
            saveCurrentProject({ reason: 'manual-force' }).catch(() => {});
          }}
        >
          Save
        </Button>
      </Group>
    </Bar>
  );
};

export default ProjectComposerBar;
