import { useEffect, useState } from 'react';
import styled from 'styled-components';

import { useMusicStore } from '../store/musicStore.js';
import {
  CATALOG_PRESET_IDS,
  MAX_MOTION_KEYFRAMES,
} from '../utils/spatialMusic/constants.js';

const Wrap = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 720px;
`;

const Row = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
`;

const Button = styled.button`
  font: inherit;
  padding: 6px 12px;
  border: 1px solid #cbd5e1;
  background: ${(p) => (p.$active ? '#0f172a' : '#fff')};
  color: ${(p) => (p.$active ? '#fff' : '#0f172a')};
  cursor: pointer;
`;

const Select = styled.select`
  font: inherit;
  padding: 6px 8px;
  min-width: 200px;
`;

const Banner = styled.p`
  margin: 0;
  padding: 8px 10px;
  background: #fff7ed;
  border: 1px solid #fdba74;
  color: #9a3412;
  font-size: 0.9rem;
`;

const Muted = styled.p`
  margin: 0;
  color: #64748b;
  font-size: 0.9rem;
`;

const Metrics = styled.pre`
  margin: 0;
  padding: 8px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  font-size: 0.8rem;
  overflow: auto;
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #475569;
  min-width: 120px;
`;

const Input = styled.input`
  font: inherit;
  padding: 4px 6px;
`;

/**
 * Optional closed motion keyframe list (tick-primary). Does not edit note ticks or Transport.bpm.
 */
function MotionKeyframesEditor({ source, onChange }) {
  const motion = Array.isArray(source?.motion) ? source.motion : [];
  const atCap = motion.length >= MAX_MOTION_KEYFRAMES;

  const patchKeyframe = (index, patch) => {
    const next = motion.map((kf, i) => (i === index ? { ...kf, ...patch } : kf));
    onChange(next);
  };

  const removeKeyframe = (index) => {
    onChange(motion.filter((_, i) => i !== index));
  };

  const addKeyframe = () => {
    if (atCap) return;
    const lastTick = motion.length ? Number(motion[motion.length - 1].tick) || 0 : 0;
    onChange([
      ...motion,
      {
        tick: lastTick + 480,
        azimuth_deg: Number(source.azimuth_deg) || 0,
        elevation_deg: Number(source.elevation_deg) || 0,
        distance: Number(source.distance) || 1,
        spread: Number(source.spread) || 0,
      },
    ]);
  };

  return (
    <div style={{ width: '100%', marginTop: 4 }}>
      <Row>
        <Muted>Motion keyframes ({motion.length}/{MAX_MOTION_KEYFRAMES})</Muted>
        <Button type="button" onClick={addKeyframe} disabled={atCap}>
          Add keyframe
        </Button>
      </Row>
      {motion.map((kf, index) => (
        <Row key={`kf-${source.id}-${index}`} style={{ marginTop: 4 }}>
          <Field>
            Tick
            <Input
              type="number"
              min={0}
              step={1}
              value={kf.tick ?? 0}
              onChange={(e) => patchKeyframe(index, { tick: Number(e.target.value) })}
            />
          </Field>
          <Field>
            Az
            <Input
              type="number"
              min={-180}
              max={180}
              step={1}
              value={kf.azimuth_deg ?? 0}
              onChange={(e) => patchKeyframe(index, { azimuth_deg: Number(e.target.value) })}
            />
          </Field>
          <Field>
            El
            <Input
              type="number"
              min={-90}
              max={90}
              step={1}
              value={kf.elevation_deg ?? 0}
              onChange={(e) => patchKeyframe(index, { elevation_deg: Number(e.target.value) })}
            />
          </Field>
          <Field>
            Dist
            <Input
              type="number"
              min={0}
              max={100}
              step={0.1}
              value={kf.distance ?? 1}
              onChange={(e) => patchKeyframe(index, { distance: Number(e.target.value) })}
            />
          </Field>
          <Button type="button" onClick={() => removeKeyframe(index)}>
            Remove
          </Button>
        </Row>
      ))}
    </div>
  );
}

/**
 * Thin Spatial studio tab: catalog clone, scene select, SpatialMix controls, audition.
 * Opening the tab never auto-INSERTS scenes.
 */
const SpatialScenePanel = () => {
  const currentProjectId = useMusicStore((s) => s.currentProjectId);
  const loadSpatialStudio = useMusicStore((s) => s.loadSpatialStudio);
  const cloneSpatialPreset = useMusicStore((s) => s.cloneSpatialPreset);
  const selectSpatialScene = useMusicStore((s) => s.selectSpatialScene);
  const setSpatialAuditionActive = useMusicStore((s) => s.setSpatialAuditionActive);
  const updateSpatialSourceMix = useMusicStore((s) => s.updateSpatialSourceMix);
  const scenes = useMusicStore((s) => s.spatialScenes);
  const presets = useMusicStore((s) => s.spatialPresets);
  const selectedSceneId = useMusicStore((s) => s.spatialSelectedSceneId);
  const selectedScene = useMusicStore((s) => s.spatialSelectedScene);
  const status = useMusicStore((s) => s.spatialStatus);
  const error = useMusicStore((s) => s.spatialError);
  const auditionActive = useMusicStore((s) => s.spatialAuditionActive);
  const stale = useMusicStore((s) => s.spatialStale);
  const preview = useMusicStore((s) => s.spatialPreview);
  const [inspectFoa, setInspectFoa] = useState(false);

  useEffect(() => {
    if (!currentProjectId) return undefined;
    loadSpatialStudio();
    return undefined;
  }, [currentProjectId, loadSpatialStudio]);

  if (!currentProjectId) {
    return (
      <Wrap data-testid="spatial-panel">
        <Muted>Open a project to manage spatial scenes.</Muted>
      </Wrap>
    );
  }

  const sources = selectedScene?.sources || [];
  const metrics = preview?.metrics;

  return (
    <Wrap data-testid="spatial-panel">
      <div>
        <h3 style={{ margin: '0 0 4px' }}>Spatial</h3>
        <Muted>
          Position tracks (and optional neural stems) for immersive stereo preview without changing
          composition.v2 notes or stem WAVs. Not a Dolby Atmos workstation. FOA coefficients come
          from the API compile — there is no browser FOA twin.
        </Muted>
      </div>

      <div>
        <Muted style={{ marginBottom: 6 }}>Clone preset into a scene (catalog is read-only)</Muted>
        <Row>
          {(presets.length ? presets.map((p) => p.preset_id) : CATALOG_PRESET_IDS).map((id) => (
            <Button
              key={id}
              type="button"
              onClick={() => cloneSpatialPreset(id)}
              disabled={status === 'loading'}
            >
              Clone {id}
            </Button>
          ))}
        </Row>
      </div>

      <div>
        <Muted style={{ marginBottom: 6 }}>Saved scenes</Muted>
        {scenes.length === 0 ? (
          <Muted>No scenes yet. Clone a preset to create one. Opening this tab never writes.</Muted>
        ) : (
          <Select
            aria-label="Spatial scene"
            value={selectedSceneId || ''}
            onChange={(event) => selectSpatialScene(event.target.value || null)}
          >
            {scenes.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.name} ({scene.source_count ?? 0} sources)
              </option>
            ))}
          </Select>
        )}
      </div>

      {sources.length > 0 ? (
        <div>
          <Muted style={{ marginBottom: 6 }}>SpatialMix sources</Muted>
          {sources.map((src) => (
            <Row key={src.id} style={{ marginBottom: 8 }}>
              <Muted style={{ minWidth: 100 }}>
                {src.source_kind}:{src.track_id || src.stem_id}
              </Muted>
              <Field>
                Azimuth
                <Input
                  type="number"
                  min={-180}
                  max={180}
                  step={1}
                  value={src.azimuth_deg}
                  onChange={(e) => updateSpatialSourceMix(src.id, {
                    azimuth_deg: Number(e.target.value),
                  })}
                />
              </Field>
              <Field>
                Elevation
                <Input
                  type="number"
                  min={-90}
                  max={90}
                  step={1}
                  value={src.elevation_deg}
                  onChange={(e) => updateSpatialSourceMix(src.id, {
                    elevation_deg: Number(e.target.value),
                  })}
                />
              </Field>
              <Field>
                Distance
                <Input
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  value={src.distance}
                  onChange={(e) => updateSpatialSourceMix(src.id, {
                    distance: Number(e.target.value),
                  })}
                />
              </Field>
              <Field>
                Spread
                <Input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={src.spread}
                  onChange={(e) => updateSpatialSourceMix(src.id, {
                    spread: Number(e.target.value),
                  })}
                />
              </Field>
              <MotionKeyframesEditor
                source={src}
                onChange={(motion) => updateSpatialSourceMix(src.id, { motion })}
              />
            </Row>
          ))}
        </div>
      ) : null}

      <div>
        <Muted style={{ marginBottom: 6 }}>Audition</Muted>
        <Row>
          <Button
            type="button"
            $active={!auditionActive}
            onClick={() => setSpatialAuditionActive(false)}
          >
            Working (off)
          </Button>
          <Button
            type="button"
            $active={auditionActive}
            onClick={() => setSpatialAuditionActive(true)}
            disabled={!selectedSceneId}
          >
            Spatial preview
          </Button>
          <Button
            type="button"
            $active={inspectFoa}
            onClick={() => setInspectFoa((v) => !v)}
            disabled={!preview}
          >
            {inspectFoa ? 'Hide FOA inspect' : 'Ambisonic inspect'}
          </Button>
        </Row>
      </div>

      {stale?.composition || stale?.stem_set ? (
        <Banner role="status">
          Soft-stale: scene fingerprint does not match the current request composition
          {stale.stem_set ? ' and/or stem-set' : ''}. Audition still uses the request body and does
          not rewrite the project score or stem bytes.
        </Banner>
      ) : null}

      {error ? <Banner role="alert">{error}</Banner> : null}

      {metrics ? (
        <Metrics>
          {JSON.stringify(
            {
              source_count: metrics.source_count,
              mean_distance: metrics.mean_distance,
              stereo_imbalance: metrics.stereo_imbalance,
              foa_energy: metrics.foa_energy,
              audition: auditionActive ? 'spatial' : 'working',
            },
            null,
            2,
          )}
        </Metrics>
      ) : null}

      {inspectFoa && preview?.sources ? (
        <Metrics>
          {JSON.stringify(
            preview.sources.map((s) => ({
              source_id: s.source_id,
              foa: s.foa,
              stereo: s.stereo,
            })),
            null,
            2,
          )}
        </Metrics>
      ) : null}
    </Wrap>
  );
};

export default SpatialScenePanel;
