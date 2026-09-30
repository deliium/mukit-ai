import React, { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';

import {
  deleteVideoAsset,
  getVideoAsset,
  getVideoScoring,
  putVideoScoring,
  suggestVideoSpotting,
  uploadVideoAsset,
  verifyVideoSpotting,
  videoAssetMediaUrl,
  VideoScoringApiError,
} from '../api/videoScoringApi.js';
import { useMusicStore } from '../store/musicStore.js';
import { compileTimeline } from '../utils/compositionTimeline.js';
import { releaseToneForPicture } from '../utils/pictureToneRelease.js';
import {
  CLOSED_FRAME_RATES,
  cueRulerFraction,
  formatTimecode,
  hitPointOffMap,
  mapVideoToMusic,
  scoreSecondsOutsideComposition,
  verifyCueLandings,
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

const CUE_KINDS = [
  'music_start',
  'music_stop',
  'hit_point',
  'reveal',
  'cut',
  'action',
  'dialogue',
  'emotional_cue',
  'user_defined',
];

const CUE_IMPORTANCE = ['low', 'medium', 'high', 'critical'];

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

function statusText(rows, cueId) {
  const row = (rows || []).find((item) => item.id === cueId);
  if (!row) {
    return '—';
  }
  const delta = row.delta_frames == null ? '' : ` ${row.delta_frames}`;
  return `${row.status}${delta}`;
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
  const pictureSpottingSuggestions = useMusicStore((state) => state.pictureSpottingSuggestions);
  const pictureSeekRequest = useMusicStore((state) => state.pictureSeekRequest);
  const setPictureLeader = useMusicStore((state) => state.setPictureLeader);
  const setPictureScoring = useMusicStore((state) => state.setPictureScoring);
  const setPictureSpottingSuggestions = useMusicStore((state) => state.setPictureSpottingSuggestions);
  const clearPicture = useMusicStore((state) => state.clearPicture);
  const applyPictureTime = useMusicStore((state) => state.applyPictureTime);
  const videoRef = useRef(null);
  const cueRulerRef = useRef(null);
  const [asset, setAsset] = useState(null);
  const [form, setForm] = useState(() => emptyForm(null));
  const [videoSeconds, setVideoSeconds] = useState(0);
  const [status, setStatus] = useState('');
  const [selectedCueId, setSelectedCueId] = useState(null);
  const [cueDraft, setCueDraft] = useState(null);
  const [brief, setBrief] = useState('');
  const [workingLandings, setWorkingLandings] = useState([]);
  const [storedLandings, setStoredLandings] = useState([]);

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

  function timecodeForSeconds(seconds) {
    if (!mapOptions) {
      return null;
    }
    return formatTimecode(seconds, {
      frameRateNumerator: mapOptions.frameRateNumerator,
      frameRateDenominator: mapOptions.frameRateDenominator,
      timecodeMode: mapOptions.timecodeMode,
      startTimecode: mapOptions.startTimecode,
    });
  }

  function selectCue(hit) {
    setSelectedCueId(hit.id);
    setCueDraft({
      kind: hit.kind || 'hit_point',
      label: hit.label,
      timecode: hit.timecode || timecodeForSeconds(hit.video_seconds) || '',
      tolerance_frames: hit.tolerance_frames ?? 0,
      importance: hit.importance || 'medium',
      instruction: hit.instruction || '',
    });
  }

  function replaceCue(id, next) {
    const hits = pictureScoring?.hit_points || [];
    void save(hits.map((hit) => (hit.id === id ? { ...hit, ...next } : hit)));
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
    const seconds = element.currentTime || 0;
    const hit = {
      id: nextHitId(),
      kind: 'hit_point',
      label: `Hit ${pictureScoring.hit_points.length + 1}`,
      timecode: timecodeForSeconds(seconds),
      video_seconds: seconds,
      musical_tick: 0,
      tolerance_frames: 0,
      importance: 'medium',
      instruction: '',
    };
    void save([...(pictureScoring.hit_points || []), hit]);
  }

  function saveCueDraft() {
    if (!selectedCueId || !cueDraft || !/^\d{2}:[0-5]\d:[0-5]\d:\d{2}$/.test(cueDraft.timecode || '')) {
      console.warn('[VideoScoring] request failed', { code: 'video_timecode_invalid' });
      setStatus('video_timecode_invalid');
      return;
    }
    replaceCue(selectedCueId, {
      kind: cueDraft.kind,
      label: cueDraft.label,
      timecode: cueDraft.timecode,
      tolerance_frames: Number(cueDraft.tolerance_frames) || 0,
      importance: cueDraft.importance,
      instruction: cueDraft.instruction || '',
    });
  }

  function onCuePointerDown(event, hit) {
    if (!asset || !mapOptions || !cueRulerRef.current) {
      selectCue(hit);
      return;
    }
    selectCue(hit);
    const ruler = cueRulerRef.current;
    const pointerId = event.pointerId;
    let moved = false;
    function secondsFromEvent(pointerEvent) {
      const rect = ruler.getBoundingClientRect();
      const ratio = rect.width <= 0 ? 0 : (pointerEvent.clientX - rect.left) / rect.width;
      const clamped = Math.min(1, Math.max(0, ratio));
      return clamped * Number(asset.duration_seconds);
    }
    function move(pointerEvent) {
      if (pointerEvent.pointerId !== pointerId) {
        return;
      }
      moved = true;
      const seconds = secondsFromEvent(pointerEvent);
      const timecode = timecodeForSeconds(seconds);
      setCueDraft((current) => (current ? { ...current, timecode: timecode || current.timecode } : current));
    }
    function up(pointerEvent) {
      if (pointerEvent.pointerId !== pointerId) {
        return;
      }
      ruler.removeEventListener('pointermove', move);
      ruler.removeEventListener('pointerup', up);
      if (!moved) {
        return;
      }
      const seconds = secondsFromEvent(pointerEvent);
      const timecode = timecodeForSeconds(seconds);
      if (timecode) {
        replaceCue(hit.id, { timecode, video_seconds: seconds });
      }
    }
    ruler.addEventListener('pointermove', move);
    ruler.addEventListener('pointerup', up);
  }

  async function onSuggest() {
    if (!projectId || !asset || !mapOptions) {
      return;
    }
    try {
      const result = await suggestVideoSpotting(projectId, brief);
      const suggestions = result?.suggestions || [];
      setPictureSpottingSuggestions(suggestions);
      console.info('[VideoScoring] suggest', {
        cueCount: (pictureScoring?.hit_points || []).length,
        suggestionCount: suggestions.length,
      });
      setStatus(result?.warning || '');
    } catch (error) {
      warn(error);
    }
  }

  function acceptSuggestion(draft) {
    const hits = pictureScoring?.hit_points || [];
    if (hits.length >= 64) {
      console.warn('[VideoScoring] request failed', { code: 'video_scoring_invalid' });
      setStatus('video_scoring_invalid');
      return;
    }
    const hit = {
      id: nextHitId(),
      kind: draft.kind,
      label: draft.label,
      timecode: draft.timecode,
      video_seconds: 0,
      musical_tick: 0,
      tolerance_frames: draft.tolerance_frames ?? 0,
      importance: draft.importance || 'medium',
      instruction: draft.instruction || '',
    };
    console.info('[VideoScoring] accept', { cueCount: hits.length + 1 });
    void save([...hits, hit]);
  }

  function dismissSuggestions() {
    setPictureSpottingSuggestions([]);
  }

  function landingCounts(rows) {
    return rows.reduce((counts, row) => {
      counts[row.status] = (counts[row.status] || 0) + 1;
      return counts;
    }, { landed: 0, missed: 0, empty: 0 });
  }

  function verifyWorking() {
    if (!mapOptions || !composition) {
      return;
    }
    const rows = verifyCueLandings(pictureScoring?.hit_points || [], composition, mapOptions);
    setWorkingLandings(rows);
    rows.forEach((row) => {
      console.debug('[VideoScoring] verify cue', {
        cueId: row.id,
        status: row.status,
        deltaFrames: row.delta_frames,
      });
    });
    console.info('[VideoScoring] verify', {
      cueCount: rows.length,
      ...landingCounts(rows),
    });
  }

  async function verifyStored() {
    if (!projectId) {
      return;
    }
    try {
      const result = await verifyVideoSpotting(projectId, selectedCueId);
      const rows = result?.cues || [];
      setStoredLandings(rows);
      console.info('[VideoScoring] verify', {
        cueCount: rows.length,
        ...landingCounts(rows),
      });
    } catch (error) {
      warn(error);
    }
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
      let pastScore = false;
      try {
        offMap = hitPointOffMap(hit, mapOptions);
        pastScore = scoreSecondsOutsideComposition(hit.video_seconds, mapOptions);
      } catch {
        offMap = false;
      }
      let label = hit.label;
      if (pastScore) {
        label = `${hit.label} (past the score)`;
      } else if (offMap) {
        label = `${hit.label} (off map)`;
      }
      marks.push({
        key: hit.id,
        left: (hit.musical_tick / timeline.durationTicks) * 100,
        top: 28,
        color: pastScore || offMap ? '#b45309' : '#0f766e',
        label,
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
          <Ruler ref={cueRulerRef} aria-label="Cue ruler">
            {(pictureScoring?.hit_points || []).map((hit) => {
              const fraction = cueRulerFraction(hit.video_seconds, asset.duration_seconds);
              const pastPicture = Number(hit.video_seconds) > Number(asset.duration_seconds);
              return (
                <Mark
                  key={hit.id}
                  $left={fraction * 100}
                  $top={8}
                  $color={pastPicture ? '#b45309' : '#0f766e'}
                  onPointerDown={(event) => onCuePointerDown(event, hit)}
                >
                  <button type="button" onClick={() => selectCue(hit)}>
                    {pastPicture ? `${hit.label} (past the picture)` : hit.label}
                  </button>
                </Mark>
              );
            })}
          </Ruler>
          {cueDraft && selectedCueId ? (
            <FormGrid>
              <Field>
                Kind
                <select
                  value={cueDraft.kind}
                  onChange={(event) => setCueDraft((current) => ({ ...current, kind: event.target.value }))}
                >
                  {CUE_KINDS.map((kind) => <option key={kind} value={kind}>{kind}</option>)}
                </select>
              </Field>
              <Field>
                Label
                <input
                  value={cueDraft.label}
                  onChange={(event) => setCueDraft((current) => ({ ...current, label: event.target.value }))}
                />
              </Field>
              <Field>
                Timecode
                <input
                  value={cueDraft.timecode}
                  onChange={(event) => setCueDraft((current) => ({ ...current, timecode: event.target.value }))}
                />
              </Field>
              <Field>
                Tolerance frames
                <input
                  type="number"
                  min="0"
                  max="240"
                  value={cueDraft.tolerance_frames}
                  onChange={(event) => setCueDraft((current) => ({
                    ...current,
                    tolerance_frames: event.target.value,
                  }))}
                />
              </Field>
              <Field>
                Importance
                <select
                  value={cueDraft.importance}
                  onChange={(event) => setCueDraft((current) => ({ ...current, importance: event.target.value }))}
                >
                  {CUE_IMPORTANCE.map((level) => <option key={level} value={level}>{level}</option>)}
                </select>
              </Field>
              <Field>
                Instruction
                <input
                  value={cueDraft.instruction}
                  maxLength={240}
                  onChange={(event) => setCueDraft((current) => ({ ...current, instruction: event.target.value }))}
                />
              </Field>
              <button type="button" onClick={saveCueDraft}>Save cue</button>
              <button type="button" onClick={() => removeHit(selectedCueId)}>Remove</button>
            </FormGrid>
          ) : null}
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
                {mark.id ? (
                  <button type="button" onClick={() => {
                    const hit = (pictureScoring?.hit_points || []).find((item) => item.id === mark.id);
                    if (hit) {
                      selectCue(hit);
                    }
                  }}
                  >
                    {mark.label}
                  </button>
                ) : mark.label}
                {mark.id ? (
                  <button type="button" onClick={() => removeHit(mark.id)}>Remove</button>
                ) : null}
              </Mark>
            ))}
          </Ruler>
          {asset ? <button type="button" onClick={addHit}>Add hit point</button> : null}
          <button type="button" onClick={verifyWorking}>Verify working score</button>
          <button type="button" onClick={verifyStored}>Verify stored score</button>
          {selectedCueId ? (
            <Meta>
              Working {statusText(workingLandings, selectedCueId)}
              {' · '}
              Stored {statusText(storedLandings, selectedCueId)}
            </Meta>
          ) : null}
          <Field>
            Spotting brief
            <input
              value={brief}
              maxLength={500}
              onChange={(event) => setBrief(event.target.value)}
            />
          </Field>
          <button type="button" onClick={onSuggest} disabled={!asset}>Suggest cues</button>
          {(pictureSpottingSuggestions || []).map((draft) => (
            <Meta key={`${draft.timecode}-${draft.label}`}>
              {draft.kind} {draft.timecode}
              <button type="button" onClick={() => acceptSuggestion(draft)}>Accept</button>
            </Meta>
          ))}
          {(pictureSpottingSuggestions || []).length > 0 ? (
            <button type="button" onClick={dismissSuggestions}>Dismiss</button>
          ) : null}
        </>
      ) : null}
      {status ? <Meta>{status}</Meta> : null}
    </Panel>
  );
};

export default VideoScoringPanel;
