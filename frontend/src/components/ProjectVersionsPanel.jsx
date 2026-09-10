import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
  box-sizing: border-box;
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

const Status = styled.p`
  margin: 0;
  font-size: 0.85rem;
  color: ${(props) => (props.$error ? '#b91c1c' : '#334155')};
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Field = styled.label`
  display: grid;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
  min-width: min(100%, 180px);
  flex: 1;
`;

const Input = styled.input`
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
`;

const Select = styled.select`
  min-height: 44px;
  padding: 8px 10px;
  border: 1px solid #c7d2fe;
  border-radius: 8px;
  font-size: 0.95rem;
  background: #fff;
`;

const Button = styled.button`
  min-height: 44px;
  padding: 8px 12px;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  background: ${(props) => {
    if (props.$danger) return '#dc2626';
    if (props.$secondary) return '#e2e8f0';
    return '#4f46e5';
  }};
  color: ${(props) => (props.$secondary ? '#1e293b' : '#fff')};

  &:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
`;

const List = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
  max-height: min(52vh, 420px);
  overflow: auto;
`;

const Item = styled.li`
  border: 1px solid ${(props) => (props.$selected ? '#6366f1' : '#e2e8f0')};
  background: ${(props) => (props.$selected ? '#eef2ff' : '#fff')};
  border-radius: 10px;
  padding: 10px 12px;
`;

const ItemButton = styled.button`
  width: 100%;
  text-align: left;
  border: none;
  background: transparent;
  cursor: pointer;
  padding: 0;
  color: inherit;
`;

const Meta = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 0.78rem;
  color: #64748b;
  margin-top: 6px;
`;

const Badge = styled.span`
  display: inline-flex;
  padding: 2px 8px;
  border-radius: 999px;
  background: #e0e7ff;
  color: #3730a3;
  font-weight: 600;
  font-size: 0.72rem;
`;

const CompareBox = styled.div`
  padding: 10px 12px;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  font-size: 0.85rem;
  color: #334155;
`;

function truncateInstruction(summary) {
  const text = summary?.instruction_preview || summary?.user_instruction_preview || '';
  if (typeof text !== 'string' || !text.trim()) {
    return null;
  }
  return text.length > 80 ? `${text.slice(0, 77)}…` : text;
}

function formatRanges(ranges) {
  if (!Array.isArray(ranges) || !ranges.length) {
    return null;
  }
  return ranges
    .slice(0, 4)
    .map((range) => `b${range.start_bar}-${range.end_bar}`)
    .join(', ');
}

const ProjectVersionsPanel = () => {
  const currentProjectId = useMusicStore((state) => state.currentProjectId);
  const activeBranchId = useMusicStore((state) => state.activeBranchId);
  const activeBranchName = useMusicStore((state) => state.activeBranchName);
  const currentRevisionId = useMusicStore((state) => state.currentRevisionId);
  const saveStatus = useMusicStore((state) => state.saveStatus);
  const versionBranches = useMusicStore((state) => state.versionBranches);
  const versionBranchesStatus = useMusicStore((state) => state.versionBranchesStatus);
  const versionRevisions = useMusicStore((state) => state.versionRevisions);
  const versionRevisionsStatus = useMusicStore((state) => state.versionRevisionsStatus);
  const versionRevisionsNextBefore = useMusicStore((state) => state.versionRevisionsNextBefore);
  const versionSelectedRevisionId = useMusicStore((state) => state.versionSelectedRevisionId);
  const versionCompareRevisionId = useMusicStore((state) => state.versionCompareRevisionId);
  const versionCompareResult = useMusicStore((state) => state.versionCompareResult);
  const versionCompareStatus = useMusicStore((state) => state.versionCompareStatus);
  const versionAuditionActive = useMusicStore((state) => state.versionAuditionActive);
  const versionActionStatus = useMusicStore((state) => state.versionActionStatus);
  const versionActionError = useMusicStore((state) => state.versionActionError);
  const versionRestoreBlockReason = useMusicStore((state) => state.versionRestoreBlockReason);
  const versionRevisionsError = useMusicStore((state) => state.versionRevisionsError);

  const loadVersionBranches = useMusicStore((state) => state.loadVersionBranches);
  const loadVersionRevisions = useMusicStore((state) => state.loadVersionRevisions);
  const selectVersionRevision = useMusicStore((state) => state.selectVersionRevision);
  const selectVersionCompareRevision = useMusicStore((state) => state.selectVersionCompareRevision);
  const setVersionAuditionActive = useMusicStore((state) => state.setVersionAuditionActive);
  const nameVersionRevision = useMusicStore((state) => state.nameVersionRevision);
  const createVersionBranch = useMusicStore((state) => state.createVersionBranch);
  const renameVersionBranch = useMusicStore((state) => state.renameVersionBranch);
  const checkoutVersionBranch = useMusicStore((state) => state.checkoutVersionBranch);
  const restoreVersionRevision = useMusicStore((state) => state.restoreVersionRevision);
  const saveCurrentProject = useMusicStore((state) => state.saveCurrentProject);

  const [branchNameDraft, setBranchNameDraft] = useState('');
  const [renameDraft, setRenameDraft] = useState(activeBranchName || '');
  const [revisionNameDraft, setRevisionNameDraft] = useState('');
  const [statusMessage, setStatusMessage] = useState('');

  useEffect(() => {
    setRenameDraft(activeBranchName || '');
  }, [activeBranchName]);

  useEffect(() => {
    if (!currentProjectId) {
      return undefined;
    }
    console.debug('[ProjectVersionsPanel] Loading version history', {
      projectId: currentProjectId,
      branchId: activeBranchId,
    });
    loadVersionBranches().catch(() => {});
    loadVersionRevisions({ reset: true }).catch(() => {});
    return undefined;
  }, [currentProjectId, activeBranchId, loadVersionBranches, loadVersionRevisions]);

  useEffect(() => {
    const selected = versionRevisions.find((row) => row.id === versionSelectedRevisionId);
    setRevisionNameDraft(selected?.name || '');
  }, [versionSelectedRevisionId, versionRevisions]);

  if (!currentProjectId) {
    return (
      <Panel data-testid="project-versions-panel">
        <Title>Versions</Title>
        <Hint>Open a project to browse composition history.</Hint>
      </Panel>
    );
  }

  const busy = versionActionStatus === 'loading' || saveStatus === 'saving';
  const restoreBlocked = Boolean(versionRestoreBlockReason);
  const draftLabel = versionRestoreBlockReason
    ? `Restore needs a durable checkpoint (${versionRestoreBlockReason}). Autosave alone keeps a branch draft.`
    : saveStatus === 'saved'
      ? 'Working draft is clean for restore (head metadata may still be loading).'
      : 'Unsaved local edits are draft-only until Save checkpoint.';

  const announce = (message) => {
    setStatusMessage(message);
  };

  return (
    <Panel data-testid="project-versions-panel">
      <Title>Versions</Title>
      <Hint>
        Autosave keeps the active branch draft. Explicit Save checkpoint and AI Apply create durable history.
        Compare and Audition never mutate the working composition. Restore creates a new revision.
      </Hint>
      <Status role="status" aria-live="polite">
        Branch: {activeBranchName || '—'} · Head: {currentRevisionId || '—'} · {draftLabel}
      </Status>
      {(versionActionError || versionRevisionsError || statusMessage) ? (
        <Status $error={Boolean(versionActionError || versionRevisionsError)} role="status">
          {versionActionError || versionRevisionsError || statusMessage}
        </Status>
      ) : null}

      <Row>
        <Field>
          Switch branch
          <Select
            aria-label="Switch branch"
            data-testid="version-branch-select"
            value={activeBranchId || ''}
            disabled={busy || versionBranchesStatus === 'loading'}
            onChange={(event) => {
              const nextId = event.target.value;
              if (!nextId || nextId === activeBranchId) {
                return;
              }
              console.debug('[ProjectVersionsPanel] Checkout branch', { branchId: nextId });
              checkoutVersionBranch(nextId)
                .then(() => announce('Switched branch'))
                .catch(() => announce('Checkout failed'));
            }}
          >
            {(versionBranches || []).map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.name}{branch.is_active ? ' (active)' : ''}
              </option>
            ))}
          </Select>
        </Field>
        <Field>
          Rename active branch
          <Input
            aria-label="Rename active branch"
            value={renameDraft}
            onChange={(event) => setRenameDraft(event.target.value)}
          />
        </Field>
        <Button
          type="button"
          $secondary
          disabled={busy || !activeBranchId || !renameDraft.trim()}
          onClick={() => {
            console.debug('[ProjectVersionsPanel] Rename branch');
            renameVersionBranch(activeBranchId, renameDraft.trim())
              .then(() => announce('Branch renamed'))
              .catch(() => announce('Rename failed'));
          }}
        >
          Rename branch
        </Button>
      </Row>

      <Row>
        <Field>
          New branch name
          <Input
            aria-label="New branch name"
            data-testid="version-new-branch-name"
            value={branchNameDraft}
            onChange={(event) => setBranchNameDraft(event.target.value)}
          />
        </Field>
        <Button
          type="button"
          disabled={busy || !branchNameDraft.trim() || !versionSelectedRevisionId}
          onClick={() => {
            console.debug('[ProjectVersionsPanel] Create branch from revision');
            createVersionBranch({
              name: branchNameDraft.trim(),
              fromRevisionId: versionSelectedRevisionId,
              checkout: false,
            })
              .then(() => {
                setBranchNameDraft('');
                announce('Branch created');
              })
              .catch(() => announce('Create branch failed'));
          }}
        >
          Create from selected
        </Button>
        <Button
          type="button"
          $secondary
          disabled={busy}
          onClick={() => {
            console.debug('[ProjectVersionsPanel] Save checkpoint');
            saveCurrentProject({ reason: 'manual' })
              .then(() => {
                loadVersionRevisions({ reset: true }).catch(() => {});
                announce('Checkpoint saved');
              })
              .catch(() => announce('Checkpoint failed'));
          }}
        >
          Save checkpoint
        </Button>
      </Row>

      <Row>
        <Button
          type="button"
          $secondary
          disabled={!versionSelectedRevisionId || busy}
          data-testid="version-audition-toggle"
          onClick={() => {
            const next = !versionAuditionActive;
            console.debug('[ProjectVersionsPanel] Toggle audition', { active: next });
            setVersionAuditionActive(next).then((ok) => {
              announce(ok ? (next ? 'Auditioning selected revision' : 'Playing working draft') : 'Audition unavailable');
            });
          }}
        >
          {versionAuditionActive ? 'Play working' : 'Audition selected'}
        </Button>
        <Button
          type="button"
          $secondary
          disabled={!versionSelectedRevisionId || busy}
          data-testid="version-compare-working"
          onClick={() => {
            console.debug('[ProjectVersionsPanel] Compare selected vs working');
            selectVersionCompareRevision(null).then(() => announce('Compared selected vs working'));
          }}
        >
          Compare vs working
        </Button>
        <Button
          type="button"
          $danger
          disabled={!versionSelectedRevisionId || busy}
          data-testid="version-restore"
          onClick={() => {
            if (restoreBlocked) {
              const proceed = window.confirm(
                'Working draft differs from the durable head. Save a checkpoint, then restore? This creates a new revision.',
              );
              if (!proceed) {
                return;
              }
              console.info('[ProjectVersionsPanel] Restore with checkpoint');
              restoreVersionRevision(versionSelectedRevisionId, { checkpointIfDirty: true })
                .then((result) => announce(result.ok ? 'Restored as new revision' : `Restore blocked: ${result.reason}`))
                .catch(() => announce('Restore failed'));
              return;
            }
            const proceed = window.confirm(
              'Restore this revision onto the active branch? This creates a new durable revision and does not rewrite history.',
            );
            if (!proceed) {
              return;
            }
            console.info('[ProjectVersionsPanel] Restore revision');
            restoreVersionRevision(versionSelectedRevisionId)
              .then((result) => announce(result.ok ? 'Restored as new revision' : `Restore blocked: ${result.reason}`))
              .catch(() => announce('Restore failed'));
          }}
        >
          Restore…
        </Button>
      </Row>

      <Row>
        <Field>
          Name selected version
          <Input
            aria-label="Revision name"
            value={revisionNameDraft}
            onChange={(event) => setRevisionNameDraft(event.target.value)}
            disabled={!versionSelectedRevisionId || busy}
          />
        </Field>
        <Button
          type="button"
          $secondary
          disabled={!versionSelectedRevisionId || busy}
          onClick={() => {
            console.debug('[ProjectVersionsPanel] Name revision');
            nameVersionRevision(versionSelectedRevisionId, revisionNameDraft.trim() || null)
              .then(() => announce('Revision name updated'))
              .catch(() => announce('Name failed'));
          }}
        >
          Name version
        </Button>
      </Row>

      {versionCompareStatus === 'ready' && versionCompareResult ? (
        <CompareBox data-testid="version-compare-summary" aria-live="polite">
          Compare: {versionCompareResult.identical ? 'identical' : 'differences'}
          {' · '}
          +{versionCompareResult.events?.added || 0}
          {' / -'}
          {versionCompareResult.events?.removed || 0}
          {' / ~'}
          {versionCompareResult.events?.changed || 0}
          {' events'}
          {versionCompareRevisionId ? ` · vs revision ${versionCompareRevisionId.slice(0, 8)}` : ' · vs working'}
        </CompareBox>
      ) : null}

      <List aria-label="Revision history" data-testid="version-revision-list">
        {versionRevisionsStatus === 'loading' && !versionRevisions.length ? (
          <li><Hint>Loading revisions…</Hint></li>
        ) : null}
        {!versionRevisions.length && versionRevisionsStatus === 'ready' ? (
          <li><Hint>No revisions on this branch yet.</Hint></li>
        ) : null}
        {versionRevisions.map((revision) => {
          const selected = revision.id === versionSelectedRevisionId;
          const isHead = revision.id === currentRevisionId;
          const instruction = truncateInstruction(revision.summary);
          const ranges = formatRanges(revision.affected_ranges);
          return (
            <Item key={revision.id} $selected={selected}>
              <ItemButton
                type="button"
                role="radio"
                aria-checked={selected}
                data-testid={`version-revision-${revision.id}`}
                onClick={() => {
                  console.debug('[ProjectVersionsPanel] Select revision', {
                    revisionId: revision.id,
                  });
                  selectVersionRevision(revision.id).catch(() => {});
                }}
              >
                <Row>
                  <strong>{revision.name || `Revision ${revision.sequence}`}</strong>
                  {isHead ? <Badge>current head</Badge> : null}
                  {selected ? <Badge>selected</Badge> : null}
                </Row>
                <Meta>
                  <span>{revision.operation_type}</span>
                  <span>{revision.created_at}</span>
                  {revision.ai_provider ? <span>{revision.ai_provider}/{revision.ai_model || '—'}</span> : null}
                  {ranges ? <span>bars {ranges}</span> : null}
                  {Array.isArray(revision.affected_track_ids) && revision.affected_track_ids.length ? (
                    <span>tracks {revision.affected_track_ids.slice(0, 4).join(', ')}</span>
                  ) : null}
                  {instruction ? <span>note: {instruction}</span> : null}
                </Meta>
              </ItemButton>
              <Row style={{ marginTop: 8 }}>
                <Button
                  type="button"
                  $secondary
                  disabled={busy}
                  onClick={() => {
                    console.debug('[ProjectVersionsPanel] Set compare revision', {
                      revisionId: revision.id,
                    });
                    selectVersionCompareRevision(revision.id)
                      .then(() => announce('Compare pair updated'))
                      .catch(() => {});
                  }}
                >
                  Compare as right
                </Button>
              </Row>
            </Item>
          );
        })}
      </List>

      {versionRevisionsNextBefore != null ? (
        <Button
          type="button"
          $secondary
          disabled={busy || versionRevisionsStatus === 'loading'}
          data-testid="version-load-more"
          onClick={() => {
            console.debug('[ProjectVersionsPanel] Load more revisions');
            loadVersionRevisions({ reset: false }).catch(() => {});
          }}
        >
          Load older revisions
        </Button>
      ) : null}
    </Panel>
  );
};

export default ProjectVersionsPanel;
