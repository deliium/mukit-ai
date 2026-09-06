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
};

const ProjectComposerBar = () => {
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const currentProjectName = useMusicStore((state) => state.currentProjectName);
  const saveStatus = useMusicStore((state) => state.saveStatus);
  const saveError = useMusicStore((state) => state.saveError);
  const goHome = useMusicStore((state) => state.goHome);
  const saveCurrentProject = useMusicStore((state) => state.saveCurrentProject);
  const renameCurrentProject = useMusicStore((state) => state.renameCurrentProject);
  const [nameDraft, setNameDraft] = React.useState(currentProjectName);

  React.useEffect(() => {
    setNameDraft(currentProjectName);
  }, [currentProjectName]);

  if (!currentProjectId) {
    return null;
  }

  const status = STATUS_STYLES[saveStatus] || STATUS_STYLES.unsaved;

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
        <Chip $bg={status.bg} $color={status.color}>{status.label}</Chip>
        {saveStatus === 'error' && saveError && (
          <span style={{ color: '#991b1b', fontSize: '0.85rem' }}>{saveError}</span>
        )}
      </Group>
      <Group>
        <Button
          type="button"
          disabled={saveStatus === 'saving'}
          onClick={() => {
            console.debug('[ProjectComposerBar] Manual save', { projectId: currentProjectId });
            saveCurrentProject({ reason: 'manual' }).catch(() => {});
          }}
        >
          Save
        </Button>
      </Group>
    </Bar>
  );
};

export default ProjectComposerBar;
