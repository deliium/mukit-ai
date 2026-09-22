import React, { useCallback, useEffect, useState } from 'react';
import styled from 'styled-components';
import {
  compareComposerProfiles,
  createComposerProfile,
  deleteComposerProfile,
  deriveComposerProfile,
  exportComposerProfile,
  getComposerProfile,
  importComposerProfile,
  listComposerProfiles,
  loadComposerProfileSessionPreference,
  previewComposerProfile,
  promoteComposerProfile,
  resetComposerProfile,
  saveComposerProfileSessionPreference,
  updateComposerProfile,
} from '../api/composerProfileApi.js';
import { listProjects } from '../api/projectApi.js';
import { useMusicStore } from '../store/musicStore.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
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

const TextArea = styled.textarea`
  width: 100%;
  min-height: 72px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  box-sizing: border-box;
  font-family: inherit;
`;

const List = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
  max-height: 220px;
  overflow: auto;
`;

const ListItem = styled.li`
  padding: 8px 10px;
  border: 1px solid ${(p) => (p.$active ? '#4f46e5' : '#e2e8f0')};
  border-radius: 8px;
  background: ${(p) => (p.$active ? '#eef2ff' : '#f8fafc')};
  cursor: pointer;
`;

const Split = styled.div`
  display: grid;
  gap: 12px;
  @media (min-width: 900px) {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  }
`;

const Card = styled.article`
  padding: 12px;
  background: #fff;
  border: 1px solid #e0e7ff;
  border-radius: 10px;
`;

const PrefBlock = styled.pre`
  margin: 0;
  padding: 8px;
  background: #f8fafc;
  border-radius: 8px;
  font-size: 0.75rem;
  overflow: auto;
  max-height: 180px;
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

const StatusText = styled.p`
  margin: 0;
  color: #334155;
  font-size: 0.85rem;
`;

function sparsePrefs(fields) {
  if (!fields || typeof fields !== 'object') return {};
  const out = {};
  for (const [key, value] of Object.entries(fields)) {
    if (value == null) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    if (typeof value === 'object' && !Array.isArray(value)) {
      const nested = sparsePrefs(value);
      if (Object.keys(nested).length === 0) continue;
      out[key] = nested;
      continue;
    }
    out[key] = value;
  }
  return out;
}

/**
 * Visible Profiles tab: create / derive / promote / preview / compare / export.
 * Composer profiles are musical preferences — never "in the style of ⟨Artist⟩".
 */
const ComposerProfilesPanel = () => {
  const composerProfileId = useMusicStore((s) => s.composerProfileId);
  const composerProfileStrength = useMusicStore((s) => s.composerProfileStrength);
  const composerProfileList = useMusicStore((s) => s.composerProfileList);
  const composerProfilePreview = useMusicStore((s) => s.composerProfilePreview);
  const setComposerProfileSelection = useMusicStore((s) => s.setComposerProfileSelection);
  const setComposerProfileList = useMusicStore((s) => s.setComposerProfileList);
  const setComposerProfilePreview = useMusicStore((s) => s.setComposerProfilePreview);

  const [detail, setDetail] = useState(null);
  const [projects, setProjects] = useState([]);
  const [selectedProjectIds, setSelectedProjectIds] = useState([]);
  const [newName, setNewName] = useState('');
  const [notesDraft, setNotesDraft] = useState('');
  const [compareId, setCompareId] = useState('');
  const [compareResult, setCompareResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');

  const refreshList = useCallback(async () => {
    const items = await listComposerProfiles();
    setComposerProfileList(items || []);
    return items || [];
  }, [setComposerProfileList]);

  useEffect(() => {
    const session = loadComposerProfileSessionPreference();
    if (session.profileId || session.strength !== 'off') {
      setComposerProfileSelection(session);
    }
    let cancelled = false;
    (async () => {
      try {
        const [items, projectRows] = await Promise.all([
          refreshList(),
          listProjects().catch(() => []),
        ]);
        if (cancelled) return;
        setProjects(Array.isArray(projectRows) ? projectRows : []);
        if (session.profileId && items.some((item) => item.id === session.profileId)) {
          const full = await getComposerProfile(session.profileId);
          if (!cancelled) {
            setDetail(full);
            setNotesDraft(full.notes || '');
          }
        }
      } catch (err) {
        if (!cancelled) setError(err.message || 'Failed to load profiles');
      }
    })();
    return () => { cancelled = true; };
  }, [refreshList, setComposerProfileSelection]);

  useEffect(() => {
    saveComposerProfileSessionPreference({
      profileId: composerProfileId,
      strength: composerProfileStrength,
    });
  }, [composerProfileId, composerProfileStrength]);

  const selectProfile = async (id) => {
    setError('');
    setBusy(true);
    try {
      const full = await getComposerProfile(id);
      setDetail(full);
      setNotesDraft(full.notes || '');
      setComposerProfileSelection({
        profileId: id,
        strength: composerProfileStrength === 'off' ? 'normal' : composerProfileStrength,
      });
      console.debug('[ComposerProfilesPanel] Selected profile', { profileId: id });
    } catch (err) {
      setError(err.message || 'Failed to load profile');
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = async () => {
    const name = newName.trim() || 'Untitled Profile';
    setBusy(true);
    setError('');
    try {
      const created = await createComposerProfile({ name });
      setNewName('');
      await refreshList();
      await selectProfile(created.id);
      setStatus(`Created “${created.name}”.`);
      console.debug('[ComposerProfilesPanel] Created', { profileId: created.id });
    } catch (err) {
      setError(err.message || 'Create failed');
    } finally {
      setBusy(false);
    }
  };

  const handleSaveMeta = async () => {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      const updated = await updateComposerProfile(detail.id, {
        expected_updated_at: detail.updated_at,
        notes: notesDraft || null,
      });
      setDetail(updated);
      await refreshList();
      setStatus('Saved notes.');
    } catch (err) {
      setError(err.message || 'Save failed');
    } finally {
      setBusy(false);
    }
  };

  const handleDerive = async ({ save = false } = {}) => {
    if (!selectedProjectIds.length) {
      setError('Select at least one project to derive from.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload = {
        sources: selectedProjectIds.map((project_id) => ({ project_id })),
      };
      if (save) {
        payload.save_as = (newName.trim() || detail?.name || 'My Cinematic Style');
      }
      const result = await deriveComposerProfile(payload);
      const profile = result.profile;
      setDetail(profile);
      setNotesDraft(profile.notes || '');
      if (result.persisted) {
        await refreshList();
        setComposerProfileSelection({
          profileId: profile.id,
          strength: composerProfileStrength === 'off' ? 'normal' : composerProfileStrength,
        });
      }
      setStatus(
        result.persisted
          ? `Derived and saved “${profile.name}” (${profile.source_projects?.length || 0} sources).`
          : `Derive preview ready (${profile.source_projects?.length || 0} sources; not saved).`,
      );
      console.debug('[ComposerProfilesPanel] Derive', {
        persisted: result.persisted,
        sourceCount: profile.source_projects?.length || 0,
        warningCount: (result.warnings || []).length,
      });
    } catch (err) {
      setError(err.message || 'Derive failed');
    } finally {
      setBusy(false);
    }
  };

  const handlePromote = async () => {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      const updated = await promoteComposerProfile(detail.id, {
        expected_updated_at: detail.updated_at,
      });
      setDetail(updated);
      setStatus('Promoted derived preferences → explicit.');
      console.debug('[ComposerProfilesPanel] Promote', { profileId: detail.id });
    } catch (err) {
      setError(err.message || 'Promote failed');
    } finally {
      setBusy(false);
    }
  };

  const handlePreview = async () => {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      const strength = composerProfileStrength === 'off' ? 'normal' : composerProfileStrength;
      const preview = await previewComposerProfile(detail.id, { strength });
      setComposerProfilePreview(preview);
      setStatus(`Preview at strength=${strength} (${preview.applied_field_count} fields).`);
    } catch (err) {
      setError(err.message || 'Preview failed');
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      const updated = await resetComposerProfile(detail.id, {
        expected_updated_at: detail.updated_at,
        clear_explicit: true,
        clear_derived: false,
        clear_sources: false,
      });
      setDetail(updated);
      setStatus('Cleared explicit preferences.');
    } catch (err) {
      setError(err.message || 'Reset failed');
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      await deleteComposerProfile(detail.id);
      setDetail(null);
      setComposerProfileSelection({ profileId: null, strength: 'off' });
      setComposerProfilePreview(null);
      await refreshList();
      setStatus('Profile deleted.');
    } catch (err) {
      setError(err.message || 'Delete failed');
    } finally {
      setBusy(false);
    }
  };

  const handleExport = async () => {
    if (!detail) return;
    setBusy(true);
    setError('');
    try {
      const envelope = await exportComposerProfile(detail.id);
      const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `composer-profile-${detail.id}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setStatus('Exported profile envelope (no compositions).');
    } catch (err) {
      setError(err.message || 'Export failed');
    } finally {
      setBusy(false);
    }
  };

  const handleImportFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const text = await file.text();
      const envelope = JSON.parse(text);
      const result = await importComposerProfile({ envelope });
      await refreshList();
      await selectProfile(result.profile.id);
      setStatus(`Imported “${result.profile.name}”.`);
    } catch (err) {
      setError(err.message || 'Import failed');
    } finally {
      setBusy(false);
    }
  };

  const handleCompare = async () => {
    if (!detail || !compareId) {
      setError('Select a second profile to compare.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await compareComposerProfiles({
        left_id: detail.id,
        right_id: compareId,
      });
      setCompareResult(result);
      setStatus(result.equal ? 'Profiles are equal.' : `${result.diffs.length} field differences.`);
    } catch (err) {
      setError(err.message || 'Compare failed');
    } finally {
      setBusy(false);
    }
  };

  const toggleProject = (id) => {
    setSelectedProjectIds((prev) => (
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    ));
  };

  const explicitSparse = sparsePrefs(detail?.explicit);
  const derivedSparse = sparsePrefs(detail?.derived);

  return (
    <Panel data-testid="composer-profiles-panel">
      <Title>Composer Profiles</Title>
      <Hint>
        Named musical preference documents (abstract stats only). Soft-condition generation at a
        chosen strength — project prompt / hard constraints always win. Not the same as a musical
        style reference, and never “in the style of” an artist.
      </Hint>

      <Row>
        <label htmlFor="composer-profile-strength">
          Generate strength
          <Select
            id="composer-profile-strength"
            value={composerProfileStrength}
            onChange={(event) => setComposerProfileSelection({
              profileId: composerProfileId,
              strength: event.target.value,
            })}
            data-testid="composer-profile-strength"
          >
            <option value="off">Off</option>
            <option value="light">Light</option>
            <option value="normal">Normal</option>
            <option value="strong">Strong</option>
          </Select>
        </label>
      </Row>

      <Split>
        <Card>
          <Row>
            <Input
              placeholder="New profile name"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              data-testid="composer-profile-new-name"
            />
            <PrimaryButton type="button" disabled={busy} onClick={handleCreate}>
              Create
            </PrimaryButton>
            <label>
              Import
              <Input type="file" accept="application/json,.json" onChange={handleImportFile} />
            </label>
          </Row>
          <List>
            {(composerProfileList || []).map((item) => (
              <ListItem
                key={item.id}
                $active={item.id === composerProfileId}
                onClick={() => selectProfile(item.id)}
                data-testid={`composer-profile-item-${item.id}`}
              >
                <strong>{item.name}</strong>
                <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
                  sources={item.source_count} · updated {item.updated_at}
                </div>
              </ListItem>
            ))}
          </List>
        </Card>

        <Card>
          {!detail ? (
            <Hint>Select or create a profile to edit.</Hint>
          ) : (
            <>
              <StatusText>
                <strong>{detail.name}</strong> · id={detail.id}
              </StatusText>
              <label>
                Notes
                <TextArea
                  value={notesDraft}
                  onChange={(event) => setNotesDraft(event.target.value)}
                />
              </label>
              <Row>
                <Button type="button" disabled={busy} onClick={handleSaveMeta}>Save notes</Button>
                <Button type="button" disabled={busy} onClick={handlePromote}>
                  Promote derived → explicit
                </Button>
                <Button type="button" disabled={busy} onClick={handlePreview}>Preview soft fragment</Button>
                <Button type="button" disabled={busy} onClick={handleExport}>Export</Button>
                <Button type="button" disabled={busy} onClick={handleReset}>Reset explicit</Button>
                <Button type="button" disabled={busy} onClick={handleDelete}>Delete</Button>
              </Row>
              <div>
                <Hint>Explicit preferences (authoritative inside the profile)</Hint>
                <PrefBlock>{JSON.stringify(explicitSparse, null, 2)}</PrefBlock>
              </div>
              <div>
                <Hint>Derived statistics (advisory until promoted)</Hint>
                <PrefBlock>{JSON.stringify(derivedSparse, null, 2)}</PrefBlock>
              </div>
              {composerProfilePreview ? (
                <div>
                  <Hint>
                    Soft fragment preview ({composerProfilePreview.strength},
                    {' '}
                    {composerProfilePreview.fragment_chars}
                    {' '}
                    chars)
                  </Hint>
                  <PrefBlock>{composerProfilePreview.soft_fragment}</PrefBlock>
                </div>
              ) : null}
            </>
          )}
        </Card>
      </Split>

      <Card>
        <Hint>Derive from selected projects (abstract stats only; never copies melodies)</Hint>
        <List>
          {projects.map((project) => (
            <ListItem
              key={project.id}
              $active={selectedProjectIds.includes(project.id)}
              onClick={() => toggleProject(project.id)}
            >
              <input
                type="checkbox"
                readOnly
                checked={selectedProjectIds.includes(project.id)}
              />
              {' '}
              {project.name || project.id}
            </ListItem>
          ))}
        </List>
        <Row>
          <Button type="button" disabled={busy} onClick={() => handleDerive({ save: false })}>
            Preview derive
          </Button>
          <PrimaryButton type="button" disabled={busy} onClick={() => handleDerive({ save: true })}>
            Derive &amp; save
          </PrimaryButton>
        </Row>
      </Card>

      <Card>
        <Hint>Compare with another profile</Hint>
        <Row>
          <Select value={compareId} onChange={(event) => setCompareId(event.target.value)}>
            <option value="">—</option>
            {(composerProfileList || [])
              .filter((item) => item.id !== detail?.id)
              .map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
          </Select>
          <Button type="button" disabled={busy} onClick={handleCompare}>Compare</Button>
        </Row>
        {compareResult ? (
          <PrefBlock>{JSON.stringify(compareResult.diffs?.slice(0, 40) || [], null, 2)}</PrefBlock>
        ) : null}
      </Card>

      {status ? <StatusText>{status}</StatusText> : null}
      {error ? <ErrorText role="alert">{error}</ErrorText> : null}
    </Panel>
  );
};

export default ComposerProfilesPanel;
