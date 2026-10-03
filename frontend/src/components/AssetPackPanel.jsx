import React, { useEffect, useState, useTransition } from 'react';
import styled from 'styled-components';

import {
  createAssetPack,
  generateAssetPack,
  getAssetPack,
  listAssetPacks,
  listAssetPackSlots,
  previewAssetPackPlan,
  regenerateAssetPackSlots,
} from '../api/assetPackApi.js';
import { listComposerProfiles } from '../api/composerProfileApi.js';
import { useMusicStore } from '../store/musicStore.js';
import {
  buildAssetPackBrief,
  canGenerateAssetPack,
  canPreviewAssetPackPlan,
  canRegenerateAssetPackSlots,
  canSaveAssetPack,
  generateRequestBody,
  planSlotRows,
  propagateRows,
  regenerateRequestBody,
} from '../utils/assetPackUi.js';
import { createAppLogger } from '../utils/appLogger.js';

const logger = createAppLogger('AssetPackPanel');

const Box = styled.section`
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
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

const Label = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
`;

const InlineLabel = styled.label`
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.85rem;
  color: #334155;
`;

const Input = styled.input`
  font-size: 0.85rem;
  padding: 4px 6px;
`;

const Select = styled.select`
  font-size: 0.85rem;
  padding: 4px 6px;
`;

const Button = styled.button`
  font-size: 0.85rem;
`;

const Table = styled.table`
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
  color: #1e1b4b;

  th,
  td {
    border: 1px solid #e2e8f0;
    padding: 4px 6px;
    text-align: left;
  }

  th {
    background: #f8fafc;
  }
`;

const ErrorText = styled.p`
  margin: 0;
  color: #b91c1c;
  font-size: 0.85rem;
`;

const Meta = styled.pre`
  margin: 0;
  padding: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 0.75rem;
  overflow: auto;
  max-height: 140px;
`;

/**
 * Agents-tab soundtrack asset pack: brief → plan preview → save → generate → regen.
 * Opening lists packs only; never auto-generates or joins a universe.
 */
export default function AssetPackPanel() {
  const openProject = useMusicStore((s) => s.openProject);
  const [pending, startTransition] = useTransition();
  const [title, setTitle] = useState('Game Soundtrack Pack');
  const [useGamePreset, setUseGamePreset] = useState(true);
  const [profileId, setProfileId] = useState('');
  const [profileStrength, setProfileStrength] = useState('normal');
  const [masterTarget, setMasterTarget] = useState('cinematic');
  const [includeAdaptive, setIncludeAdaptive] = useState(true);
  const [profiles, setProfiles] = useState([]);
  const [packs, setPacks] = useState([]);
  const [plan, setPlan] = useState(null);
  const [pack, setPack] = useState(null);
  const [slots, setSlots] = useState([]);
  const [selectedRegen, setSelectedRegen] = useState(() => new Set());
  const [status, setStatus] = useState('idle');
  const [errorCode, setErrorCode] = useState(null);

  const busy = pending || status === 'loading' || status === 'generating';

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [packList, profileList] = await Promise.all([
          listAssetPacks(),
          listComposerProfiles().catch(() => []),
        ]);
        if (cancelled) return;
        setPacks(Array.isArray(packList?.packs) ? packList.packs : []);
        setProfiles(Array.isArray(profileList) ? profileList : []);
        logger.debug('Asset pack panel opened', {
          packCount: Array.isArray(packList?.packs) ? packList.packs.length : 0,
        });
      } catch (error) {
        if (!cancelled) {
          setErrorCode(error?.code || 'asset_pack_invalid');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const brief = buildAssetPackBrief({
    title,
    useGamePreset,
    profileId,
    profileStrength,
    masterTarget,
    includeAdaptiveScaffolds: includeAdaptive,
    includeRendering: false,
  });

  const refreshSlots = async (packId) => {
    const body = await listAssetPackSlots(packId);
    setSlots(Array.isArray(body?.slots) ? body.slots : []);
  };

  const handlePreview = () => {
    if (!canPreviewAssetPackPlan(brief)) return;
    setErrorCode(null);
    setStatus('loading');
    startTransition(async () => {
      try {
        const body = await previewAssetPackPlan(brief);
        setPlan(body?.plan || null);
        setStatus('idle');
        logger.info('Asset pack plan previewed', {
          slotCount: body?.plan?.slots?.length ?? 0,
          digestPrefix: String(body?.plan?.plan_digest || '').slice(0, 12),
        });
      } catch (error) {
        setStatus('error');
        setErrorCode(error?.code || 'asset_pack_invalid');
      }
    });
  };

  const handleSave = () => {
    if (!canSaveAssetPack(plan)) return;
    setErrorCode(null);
    setStatus('loading');
    startTransition(async () => {
      try {
        const body = await createAssetPack({ plan });
        setPack(body?.pack || null);
        setPlan(body?.plan || plan);
        setPacks((prev) => [body.pack, ...prev.filter((p) => p.id !== body.pack?.id)]);
        await refreshSlots(body.pack.id);
        setStatus('idle');
        logger.info('Asset pack saved', {
          packId: String(body?.pack?.id || '').slice(0, 16),
          status: body?.pack?.status,
        });
      } catch (error) {
        setStatus('error');
        setErrorCode(error?.code || 'asset_pack_invalid');
      }
    });
  };

  const handleGenerate = () => {
    const payload = generateRequestBody(pack, plan);
    if (!payload) return;
    setErrorCode(null);
    setStatus('generating');
    startTransition(async () => {
      try {
        const body = await generateAssetPack(pack.id, payload);
        setPack(body?.pack || null);
        setPlan(body?.plan || plan);
        await refreshSlots(body.pack.id);
        setStatus('idle');
        logger.info('Asset pack generate finished', {
          packId: String(body?.pack?.id || '').slice(0, 16),
          status: body?.pack?.status,
        });
      } catch (error) {
        setStatus('error');
        setErrorCode(error?.code || 'asset_pack_generate_failed');
        if (pack?.id) {
          try {
            const latest = await getAssetPack(pack.id);
            setPack(latest?.pack || pack);
            await refreshSlots(pack.id);
          } catch {
            /* ignore refresh failure */
          }
        }
      }
    });
  };

  const handleRegenerate = () => {
    const ids = [...selectedRegen];
    const payload = regenerateRequestBody(pack, ids);
    if (!payload) return;
    setErrorCode(null);
    setStatus('generating');
    startTransition(async () => {
      try {
        const body = await regenerateAssetPackSlots(pack.id, payload);
        setPack(body?.pack || null);
        await refreshSlots(body.pack.id);
        setSelectedRegen(new Set());
        setStatus('idle');
        logger.info('Asset pack regenerate finished', {
          packId: String(body?.pack?.id || '').slice(0, 16),
          slotCount: ids.length,
        });
      } catch (error) {
        setStatus('error');
        setErrorCode(error?.code || 'asset_pack_generate_failed');
      }
    });
  };

  const handleSelectPack = (packId) => {
    if (!packId) return;
    setErrorCode(null);
    setStatus('loading');
    startTransition(async () => {
      try {
        const body = await getAssetPack(packId);
        setPack(body?.pack || null);
        setPlan(body?.plan || null);
        await refreshSlots(packId);
        setStatus('idle');
      } catch (error) {
        setStatus('error');
        setErrorCode(error?.code || 'asset_pack_not_found');
      }
    });
  };

  const toggleRegen = (slotId) => {
    setSelectedRegen((prev) => {
      const next = new Set(prev);
      if (next.has(slotId)) next.delete(slotId);
      else next.add(slotId);
      return next;
    });
  };

  const slotRows = planSlotRows(plan);
  const propRows = propagateRows(plan);

  return (
    <Box data-testid="asset-pack-panel">
      <Title>Asset Pack</Title>
      <Hint>
        One franchise brief → AssetPackPlan → independent projects per slot. Shared Musical
        Universe holds Theme A; Composer Profile soft-conditions each generate. Opening this
        panel never auto-generates.
      </Hint>

      <Row>
        <Label>
          Existing packs
          <Select
            data-testid="asset-pack-list"
            value={pack?.id || ''}
            disabled={busy}
            onChange={(e) => handleSelectPack(e.target.value)}
          >
            <option value="">— new brief —</option>
            {packs.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title || item.id} ({item.status})
              </option>
            ))}
          </Select>
        </Label>
      </Row>

      <Row>
        <Label>
          Title
          <Input
            data-testid="asset-pack-title"
            value={title}
            disabled={busy}
            onChange={(e) => setTitle(e.target.value)}
          />
        </Label>
        <InlineLabel>
          <input
            type="checkbox"
            checked={useGamePreset}
            disabled={busy}
            data-testid="asset-pack-game-preset"
            onChange={(e) => setUseGamePreset(e.target.checked)}
          />
          game_soundtrack_v1 preset
        </InlineLabel>
        <InlineLabel>
          <input
            type="checkbox"
            checked={includeAdaptive}
            disabled={busy}
            data-testid="asset-pack-adaptive"
            onChange={(e) => setIncludeAdaptive(e.target.checked)}
          />
          adaptive scaffolds
        </InlineLabel>
      </Row>

      <Row>
        <Label>
          Composer profile
          <Select
            data-testid="asset-pack-profile"
            value={profileId}
            disabled={busy}
            onChange={(e) => setProfileId(e.target.value)}
          >
            <option value="">— none —</option>
            {profiles.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name || item.id}
              </option>
            ))}
          </Select>
        </Label>
        <Label>
          Strength
          <Select
            data-testid="asset-pack-profile-strength"
            value={profileStrength}
            disabled={busy || !profileId}
            onChange={(e) => setProfileStrength(e.target.value)}
          >
            <option value="off">off</option>
            <option value="light">light</option>
            <option value="normal">normal</option>
            <option value="strong">strong</option>
          </Select>
        </Label>
        <Label>
          Master target
          <Select
            data-testid="asset-pack-master-target"
            value={masterTarget}
            disabled={busy}
            onChange={(e) => setMasterTarget(e.target.value)}
          >
            <option value="cinematic">cinematic</option>
            <option value="streaming">streaming</option>
            <option value="dynamic">dynamic</option>
            <option value="demo">demo</option>
          </Select>
        </Label>
      </Row>

      <Row>
        <Button
          type="button"
          data-testid="asset-pack-preview"
          disabled={busy || !canPreviewAssetPackPlan(brief)}
          onClick={handlePreview}
        >
          Preview plan
        </Button>
        <Button
          type="button"
          data-testid="asset-pack-save"
          disabled={busy || !canSaveAssetPack(plan)}
          onClick={handleSave}
        >
          Save pack
        </Button>
        <Button
          type="button"
          data-testid="asset-pack-generate"
          disabled={busy || !canGenerateAssetPack(pack, plan)}
          onClick={handleGenerate}
        >
          Generate
        </Button>
        <Button
          type="button"
          data-testid="asset-pack-regenerate"
          disabled={busy || !canRegenerateAssetPackSlots(pack, [...selectedRegen])}
          onClick={handleRegenerate}
        >
          Regenerate selected
        </Button>
      </Row>

      {errorCode ? <ErrorText data-testid="asset-pack-error">{errorCode}</ErrorText> : null}

      {plan ? (
        <>
          <Hint data-testid="asset-pack-plan-meta">
            Plan digest {String(plan.plan_digest || '').slice(0, 12)}… ·{' '}
            {plan.slots?.length || 0} slots · seed {plan.theme_policy?.seed_slot_id} · master{' '}
            {plan.production?.master_target}
            {pack ? ` · pack ${pack.status} rev ${pack.document_revision}` : ''}
          </Hint>
          <Table data-testid="asset-pack-plan-slots">
            <thead>
              <tr>
                <th>Slot</th>
                <th>Label</th>
                <th>Adaptive</th>
                <th>Duration</th>
                <th>Density</th>
              </tr>
            </thead>
            <tbody>
              {slotRows.map((row) => (
                <tr key={row.slotId}>
                  <td>{row.slotId}</td>
                  <td>{row.label}</td>
                  <td>{row.adaptiveLabel}</td>
                  <td>{row.durationSeconds}s</td>
                  <td>{row.density}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          {propRows.length > 0 ? (
            <Meta data-testid="asset-pack-propagate">
              {propRows
                .map(
                  (row) =>
                    `${row.slotId}: ${row.operation}${
                      row.transposeSemitones != null ? ` ${row.transposeSemitones}` : ''
                    }`,
                )
                .join('\n')}
            </Meta>
          ) : null}
        </>
      ) : null}

      {slots.length > 0 ? (
        <Table data-testid="asset-pack-slot-status">
          <thead>
            <tr>
              <th>Regen</th>
              <th>Slot</th>
              <th>Status</th>
              <th>Project</th>
              <th>Adaptive</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((slot) => (
              <tr key={slot.slot_id}>
                <td>
                  <input
                    type="checkbox"
                    checked={selectedRegen.has(slot.slot_id)}
                    disabled={busy || slot.status === 'running'}
                    data-testid={`asset-pack-regen-${slot.slot_id}`}
                    onChange={() => toggleRegen(slot.slot_id)}
                  />
                </td>
                <td>{slot.slot_id}</td>
                <td>{slot.status}</td>
                <td>
                  {slot.project_id ? (
                    <Button
                      type="button"
                      data-testid={`asset-pack-open-${slot.slot_id}`}
                      disabled={busy || typeof openProject !== 'function'}
                      onClick={() => {
                        if (typeof openProject === 'function') {
                          openProject(slot.project_id);
                        }
                      }}
                    >
                      Open
                    </Button>
                  ) : (
                    '—'
                  )}
                </td>
                <td>{slot.adaptive_score_id ? 'yes' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
    </Box>
  );
}
