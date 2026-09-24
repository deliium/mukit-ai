/**
 * Lightweight bound-source waveform with playhead + selection highlight.
 * Scrub updates HTMLAudio via store seek (alignment sync clock).
 * Does not register a playbackSource.
 */

import React, { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';
import { useMusicStore } from '../store/musicStore.js';
import {
  alignmentMapOpts,
  sourceSecondsToTick,
  tickToSourceSeconds,
} from '../utils/audioAlignment.js';
import {
  decodeBlobUrlToPeaks,
  pointerXToSourceSeconds,
} from '../utils/audioWaveformPeaks.js';
import { compileTimeline } from '../utils/compositionTimeline.js';
import { createAppLogger } from '../utils/appLogger.js';

const log = createAppLogger('audioAlignment');

const Wrap = styled.div`
  position: relative;
  width: 100%;
  height: 56px;
  background: #0f172a0a;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  overflow: hidden;
  cursor: crosshair;
`;

const Canvas = styled.canvas`
  display: block;
  width: 100%;
  height: 100%;
`;

function AudioAlignmentWaveform() {
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const [peaksInfo, setPeaksInfo] = useState(null);

  const blobUrl = useMusicStore((s) => s.recoverySourceObjectUrl);
  const alignmentDocument = useMusicStore((s) => s.alignmentDocument);
  const sourcePlayheadTick = useMusicStore((s) => s.sourcePlayheadTick);
  const audioWindowHighlight = useMusicStore((s) => s.audioWindowHighlight);
  const composition = useMusicStore((s) => s.editedMusicJson);
  const seekSourceAudioToTick = useMusicStore((s) => s.seekSourceAudioToTick);
  const setSourceAuditionMode = useMusicStore((s) => s.setSourceAuditionMode);

  useEffect(() => {
    let cancelled = false;
    if (!blobUrl) {
      setPeaksInfo(null);
      return undefined;
    }
    decodeBlobUrlToPeaks(blobUrl, { maxPeaks: 600 }).then((info) => {
      if (!cancelled) setPeaksInfo(info);
    });
    return () => {
      cancelled = true;
    };
  }, [blobUrl]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || !peaksInfo?.peaks?.length) return;

    const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;
    const width = wrap.clientWidth || 300;
    const height = wrap.clientHeight || 56;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const { peaks, durationSeconds } = peaksInfo;
    const mid = height / 2;

    if (audioWindowHighlight && durationSeconds > 0) {
      const x0 = (audioWindowHighlight.startSeconds / durationSeconds) * width;
      const x1 = (audioWindowHighlight.endSeconds / durationSeconds) * width;
      ctx.fillStyle = 'rgba(13, 148, 136, 0.18)';
      ctx.fillRect(Math.min(x0, x1), 0, Math.abs(x1 - x0), height);
    }

    ctx.strokeStyle = '#0f766e';
    ctx.beginPath();
    for (let i = 0; i < peaks.length; i += 1) {
      const x = (i / Math.max(1, peaks.length - 1)) * width;
      const amp = peaks[i] * (height * 0.42);
      ctx.moveTo(x, mid - amp);
      ctx.lineTo(x, mid + amp);
    }
    ctx.stroke();

    let playheadSeconds = null;
    if (sourcePlayheadTick != null && alignmentDocument && composition) {
      const timeline = compileTimeline(composition);
      if (timeline) {
        playheadSeconds = tickToSourceSeconds(
          timeline,
          sourcePlayheadTick,
          alignmentMapOpts(alignmentDocument),
        );
      }
    }
    if (playheadSeconds != null && durationSeconds > 0) {
      const x = (playheadSeconds / durationSeconds) * width;
      ctx.strokeStyle = '#b45309';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
  }, [
    peaksInfo,
    sourcePlayheadTick,
    audioWindowHighlight,
    alignmentDocument,
    composition,
  ]);

  const onPointer = (event) => {
    if (!peaksInfo?.durationSeconds || !alignmentDocument || !composition) {
      return;
    }
    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const seconds = pointerXToSourceSeconds(
      event.clientX,
      rect,
      peaksInfo.durationSeconds,
    );
    if (seconds == null) return;
    const timeline = compileTimeline(composition);
    if (!timeline) return;
    const tick = sourceSecondsToTick(
      timeline,
      seconds,
      alignmentMapOpts(alignmentDocument),
    );
    if (tick == null) return;
    setSourceAuditionMode('scrubbing');
    seekSourceAudioToTick(tick, { reason: 'waveform-scrub' });
    log.debug('waveform scrub', {
      seconds: Number(seconds.toFixed(3)),
      tick,
    });
  };

  if (!blobUrl || !alignmentDocument) {
    return null;
  }

  return (
    <Wrap
      ref={wrapRef}
      data-testid="audio-alignment-waveform"
      onPointerDown={onPointer}
      role="presentation"
    >
      <Canvas ref={canvasRef} />
    </Wrap>
  );
}

export default AudioAlignmentWaveform;
