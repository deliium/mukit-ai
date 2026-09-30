import React, { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';

import {
  deleteVideoAsset,
  getVideoAsset,
  getVideoScoring,
  putVideoScoring,
  uploadVideoAsset,
  videoAssetMediaUrl,
  VideoScoringApiError,
} from '../api/videoScoringApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { compileTimeline } from '../utils/compositionTimeline.js';
import { releaseToneForPicture } from '../utils/pictureToneRelease.js';
import {
  CLOSED_FRAME_RATES,
  hitPointOffMap,
  mapVideoToMusic,
} from '../utils/videoScoringMap.js';

const Panel = styled.section`
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
`;

const Meta = styled.p`
  margin: 0;
  color: #475569;
  font-size: 0.9rem;
`;

const FormGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
`;

const Field = styled.label`
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 0.85rem;
  color: #334155;
`;

const Ruler = styled.div`
  position: relative;
  height: 72px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #f8fafc;
  overflow: hidden;
`;

const Mark = styled.span`
  position: absolute;
  top: ${(props) => props.$top}px;
  left: ${(props) => props.$left}%;
  transform: translateX(-50%);
  font-size: 0.75rem;
  white-space: nowrap;
  color: ${(props) => props.$color};
`;

let hitCounter = 0;

function nextHitId() {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.getRandomValues === 'function') {
    const bytes = new Uint8Array(4);
    cryptoApi.getRandomValues(bytes);
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return `hit_${hex}`;
  }
  hitCounter += 1;
  return `hit_${hitCounter.toString(16).padStart(8, '0')}`;
}

function rateKey(numerator, denominator) {
  if (numerator == null || denominator == null) {
    return '';
  }
  return `${numerator}/${denominator}`;
}

function emptyForm(scoring) {
  return {
    rate: rateKey(scoring?.frame_rate_numerator, scoring?.frame_rate_denominator),
    source: scoring?.frame_rate_source || 'explicit',
    timecodeMode: scoring?.timecode_mode || 'non_drop',
    startTimecode: scoring?.start_timecode || '00:00:00:00',
    videoOriginSeconds: scoring?.video_origin_seconds ?? 0,
    musicalOriginTick: scoring?.musical_origin_tick ?? 0,
  };
}

const VideoScoringPanel = () => {
  const projectId = useMusicStore((state) => state.currentProjectId);
  const composition = useMusicStore((state) => state.editedMusicJson);
  const pictureScoring = useMusicStore((state) => state.pictureScoring);
  const pictureSeekRequest = useMusicStore((state) => state.pictureSeekRequest);
  const setPictureLeader = useMusicStore((state) => state.setPictureLeader);
  const setPictureScoring = useMusicStore((state) => state.setPictureScoring);
  const clearPicture = useMusicStore((state) => state.clearPicture);
  const applyPictureTime = useMusicStore((state) => state.applyPictureTime);
  const videoRef = useRef(null);
  const [asset, setAsset] = useState(null);
  const [form, setForm] = useState(() => emptyForm(null));
  const [videoSeconds, setVideoSeconds] = useState(0);
  const [status, setStatus] = useState('');

  useEffect(() => {
    let cancelled = false;
    clearPicture();
    setAsset(null);
    setForm(emptyForm(null));
    if (!projectId) {
      return undefined;
    }
    (async () => {
      try {
        const scoring = await getVideoScoring(projectId);
        if (cancelled) {
          return;
        }
        setPictureScoring(scoring);
        setForm(emptyForm(scoring));
        try {
          const loaded = await getVideoAsset(projectId);
          if (!cancelled) {
            setAsset(loaded);
          }
        } catch (error) {
          if (error instanceof VideoScoringApiError && error.code === 'video_asset_missing') {
            if (!cancelled) {
              setAsset(null);
            }
            return;
          }
          throw error;
        }
      } catch (error) {
        const code = error instanceof VideoScoringApiError ? error.code : 'video_scoring_invalid';
        console.warn('[VideoScoring] load failed', { code });
        if (!cancelled) {
          setStatus(code);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, clearPicture, setPictureScoring]);

  useEffect(() => {
    const element = videoRef.current;
    if (!element || !pictureSeekRequest) {
      return;
    }
    const seconds = Number(pictureSeekRequest.seconds);
    if (!Number.isFinite(seconds)) {
      return;
    }
    try {
      element.pause();
      element.currentTime = seconds;
    } catch {
      console.warn('[VideoScoring] seek failed', { code: 'video_seek_failed' });
    }
  }, [pictureSeekRequest]);

  const timeline = useMemo(() => compileTimeline(composition), [composition]);
  const selectedRate = CLOSED_FRAME_RATES.find((rate) => rateKey(rate.numerator, rate.denominator) === form.rate);
  const mapOptions = useMemo(() => {
    if (!timeline || !selectedRate) {
      return null;
    }
    return {
      timeline,
      durationSeconds: asset?.duration_seconds ?? Number.POSITIVE_INFINITY,
      videoOriginSeconds: Number(form.videoOriginSeconds) || 0,
      musicalOriginTick: Number(form.musicalOriginTick) || 0,
      frameRateNumerator: selectedRate.numerator,
      frameRateDenominator: selectedRate.denominator,
      timecodeMode: form.timecodeMode,
      startTimecode: form.startTimecode,
    };
  }, [
    timeline,
    selectedRate,
    asset,
    form.videoOriginSeconds,
    form.musicalOriginTick,
    form.timecodeMode,
    form.startTimecode,
  ]);

  const timecode = useMemo(() => {
    if (!selectedRate) {
      return 'Frame rate required';
    }
    if (!mapOptions) {
      return '00:00:00:00';
    }
    try {
      return mapVideoToMusic(videoSeconds, mapOptions).timecode;
    } catch {
      return 'Frame rate required';
    }
  }, [mapOptions, selectedRate, videoSeconds]);

  function warn(error) {
    const code = error instanceof VideoScoringApiError ? error.code : 'video_scoring_invalid';
    console.warn('[VideoScoring] request failed', { code });
    setStatus(code);
  }

  async function onUpload(event) {
    const file = event.target.files && event.target.files[0];
    if (!file || !projectId) {
      return;
    }
    try {
      const result = await uploadVideoAsset(projectId, file);
      setAsset(result.asset);
      setPictureScoring(result.scoring);
      setForm(emptyForm(result.scoring));
      setStatus('');
      console.info('[VideoScoring] upload', {
        assetId: result.asset.asset_id,
        durationSeconds: result.asset.duration_seconds,
        width: result.asset.width,
        height: result.asset.height,
      });
    } catch (error) {
      warn(error);
    }
  }

  async function save(nextHits = pictureScoring?.hit_points || []) {
    if (!projectId || pictureScoring == null) {
      return;
    }
    const [numerator, denominator] = form.rate ? form.rate.split('/').map(Number) : [null, null];
    const payload = {
      expected_document_revision: pictureScoring.document_revision,
      frame_rate_numerator: form.rate ? numerator : null,
      frame_rate_denominator: form.rate ? denominator : null,
      frame_rate_source: form.rate ? 'explicit' : null,
      timecode_mode: form.timecodeMode,
      start_timecode: form.startTimecode,
      video_origin_seconds: Number(form.videoOriginSeconds) || 0,
      musical_origin_tick: Number(form.musicalOriginTick) || 0,
      hit_points: nextHits,
    };
    try {
      const saved = await putVideoScoring(projectId, payload);
      setPictureScoring(saved);
      setForm(emptyForm(saved));
      setStatus('');
    } catch (error) {
      warn(error);
    }
  }

  function onPlay() {
    releaseToneForPicture();
    setPictureLeader('picture');
    console.info('[VideoScoring] leader', { leader: 'picture' });
    const element = videoRef.current;
    if (element) {
      applyPictureTime(element.currentTime || 0);
    }
  }

  function onMediaTime() {
    const element = videoRef.current;
    if (!element) {
      return;
    }
    const seconds = element.currentTime || 0;
    setVideoSeconds(seconds);
    applyPictureTime(seconds);
  }

  function onPause() {
    if (useMusicStore.getState().pictureSyncStatus !== 'playing') {
      return;
    }
    setPictureLeader('tone');
    console.info('[VideoScoring] leader', { leader: 'tone' });
  }

  function addHit() {
    const element = videoRef.current;
    if (!element || !mapOptions || !pictureScoring) {
      return;
    }
    if ((pictureScoring.hit_points || []).length >= 64) {
      console.warn('[VideoScoring] request failed', { code: 'video_scoring_invalid' });
      return;
    }
    const mapped = mapVideoToMusic(element.currentTime || 0, mapOptions);
    const hit = {
      id: nextHitId(),
      label: `Hit ${pictureScoring.hit_points.length + 1}`,
      video_seconds: element.currentTime || 0,
      musical_tick: mapped.tick,
    };
    void save([...(pictureScoring.hit_points || []), hit]);
  }

  function removeHit(id) {
    const remaining = (pictureScoring?.hit_points || []).filter((hit) => hit.id !== id);
    void save(remaining);
  }

  async function removeAsset() {
    if (!projectId) {
      return;
    }
    try {
      await deleteVideoAsset(projectId);
      const scoring = await getVideoScoring(projectId);
      setAsset(null);
      setPictureScoring(scoring);
      setForm(emptyForm(scoring));
      setPictureLeader('tone');
    } catch (error) {
      warn(error);
    }
  }

  const marks = [];
  if (timeline && timeline.durationTicks > 0) {
    const barCount = timeline.barBoundaries.length - 1;
    const step = barCount > 32 ? 4 : 1;
    for (let bar = 1; bar <= barCount; bar += step) {
      const tick = timeline.barBoundaries[bar - 1];
      marks.push({
        key: `bar-${bar}`,
        left: (tick / timeline.durationTicks) * 100,
        top: 52,
        color: '#64748b',
        label: String(bar),
      });
    }
  }
  if (timeline && timeline.durationTicks > 0) {
    for (const marker of composition?.markers || []) {
      marks.push({
        key: `marker-${marker.tick}-${marker.label}`,
        left: (marker.tick / timeline.durationTicks) * 100,
        top: 4,
        color: '#1d4ed8',
        label: marker.label,
      });
    }
  }
  if (timeline && mapOptions && timeline.durationTicks > 0) {
    for (const hit of pictureScoring?.hit_points || []) {
      let offMap = false;
      try {
        offMap = hitPointOffMap(hit, mapOptions);
      } catch {
        offMap = false;
      }
      marks.push({
        key: hit.id,
        left: (hit.musical_tick / timeline.durationTicks) * 100,
        top: 28,
        color: offMap ? '#b45309' : '#0f766e',
        label: offMap ? `${hit.label} (off map)` : hit.label,
        id: hit.id,
      });
    }
  }

  return (
    <Panel>
      <h2>Picture</h2>
      {!projectId ? <Meta>Open a project to add a picture.</Meta> : null}
      {projectId && !asset ? (
        <Field>
          Upload one MP4 or MOV
          <input type="file" accept="video/mp4,video/quicktime,.mp4,.mov" onChange={onUpload} />
        </Field>
      ) : null}
      {asset ? (
        <>
          <Meta>
            {asset.duration_seconds}s · {asset.frame_rate_numerator}/{asset.frame_rate_denominator}
            {' · '}
            {asset.has_audio ? 'audio' : 'silent'}
            {' · '}
            {asset.width}×{asset.height}
          </Meta>
          <video
            ref={videoRef}
            src={videoAssetMediaUrl(projectId)}
            controls
            onPlay={onPlay}
            onTimeUpdate={onMediaTime}
            onSeeked={onMediaTime}
            onPause={onPause}
            onEnded={onPause}
          />
          <output aria-label="Timecode">{timecode}</output>
          <button type="button" onClick={removeAsset}>Remove picture</button>
        </>
      ) : null}
      {projectId && pictureScoring ? (
        <>
          <FormGrid>
            <Field>
              Frame rate
              <select
                value={form.rate}
                onChange={(event) => {
                  const rate = event.target.value;
                  setForm((current) => ({
                    ...current,
                    rate,
                    timecodeMode: rate === '30000/1001' ? current.timecodeMode : 'non_drop',
                  }));
                }}
              >
                <option value="">Choose a rate</option>
                {CLOSED_FRAME_RATES.map((rate) => (
                  <option key={rateKey(rate.numerator, rate.denominator)} value={rateKey(rate.numerator, rate.denominator)}>
                    {rateKey(rate.numerator, rate.denominator)}
                  </option>
                ))}
              </select>
            </Field>
            <Field>
              <span>Drop frame</span>
              <input
                type="checkbox"
                checked={form.timecodeMode === 'drop_frame'}
                disabled={form.rate !== '30000/1001'}
                onChange={(event) => setForm((current) => ({
                  ...current,
                  timecodeMode: event.target.checked ? 'drop_frame' : 'non_drop',
                }))}
              />
            </Field>
            <Field>
              Start timecode
              <input
                value={form.startTimecode}
                onChange={(event) => setForm((current) => ({ ...current, startTimecode: event.target.value }))}
              />
            </Field>
            <Field>
              Video origin seconds
              <input
                type="number"
                min="0"
                step="0.01"
                value={form.videoOriginSeconds}
                onChange={(event) => setForm((current) => ({ ...current, videoOriginSeconds: event.target.value }))}
              />
            </Field>
            <Field>
              Musical origin tick
              <input
                type="number"
                min="0"
                step="1"
                value={form.musicalOriginTick}
                onChange={(event) => setForm((current) => ({ ...current, musicalOriginTick: event.target.value }))}
              />
            </Field>
          </FormGrid>
          <button type="button" onClick={() => save()}>Save scoring</button>
          <Ruler aria-label="Bar ruler">
            {marks.map((mark) => (
              <Mark key={mark.key} $left={mark.left} $top={mark.top} $color={mark.color}>
                {mark.label}
                {mark.id ? (
                  <button type="button" onClick={() => removeHit(mark.id)}>Remove</button>
                ) : null}
              </Mark>
            ))}
          </Ruler>
          {asset ? <button type="button" onClick={addHit}>Add hit point</button> : null}
        </>
      ) : null}
      {status ? <Meta>{status}</Meta> : null}
    </Panel>
  );
};

export default VideoScoringPanel;
