"""Read-only stem bounce into a new mix WAV.

Source stem paths are hashed and opened read-only. Output bytes are returned
to the caller, who writes them only under MIX_PLAN_ROOT.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
import wave
from dataclasses import replace
from pathlib import Path

from app.mix_analysis_settings import load_mix_analysis_settings
from app.mix_plan_schemas import MixPlanOp, MixPlanStemPin, MixPlanV1
from app.mix_plan_settings import MixPlanSettings
from app.services.mix_analysis.decode import decode_wav_readonly

logger = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate() or 1
        return wf.getnframes() / float(rate)


def pin_from_path(path: Path, *, stem_id: str, role: str) -> tuple[MixPlanStemPin, str]:
    full = sha256_file(path)
    pin = MixPlanStemPin(
        stem_id=stem_id,
        stem_role=role,
        sha256_prefix=full[:16],
        byte_size=path.stat().st_size,
    )
    return pin, full


def structure_digest(plan: MixPlanV1) -> str:
    """Identity digest. Numeric ``after`` edits do not change it; target and ops do."""
    payload = {
        "master_target": plan.master_target,
        "stem_set_id": plan.stem_set_id,
        "ops": [
            {
                "op_id": op.op_id,
                "type": op.type,
                "bus": op.bus,
                "stem_ids": list(op.stem_ids),
                "stem_roles": list(op.stem_roles),
                "reason_code": op.reason_code,
            }
            for op in plan.ops
        ],
    }
    import json

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def content_digest(plan: MixPlanV1) -> str:
    import json

    raw = json.dumps(
        [{"id": op.op_id, "after": op.after} for op in plan.ops],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def render_mix_wav(
    plan: MixPlanV1,
    stems: list[dict],
    settings: MixPlanSettings,
    *,
    max_seconds: float,
    fake: bool,
) -> tuple[bytes, str, float | None]:
    """Return wav bytes, dsp_backend, and sample-peak dBFS of the output."""
    if fake:
        backend = "fake"
        wav = _fake_wav(content_digest(plan))
        peak = _peak_dbfs_from_wav(wav)
        logger.info(
            "Mix plan fake bounce wrote digest WAV",
            extra={
                "stem_set_id": plan.stem_set_id,
                "op_count": len(plan.ops),
                "dsp_backend": backend,
                "byte_size": len(wav),
                "master_target": plan.master_target,
            },
        )
        return wav, backend, peak

    analysis = load_mix_analysis_settings()
    analysis = replace(
        analysis,
        fake_mode=False,
        max_audio_seconds=max_seconds,
        max_total_input_bytes=settings.max_total_input_bytes,
    )
    decoded = []
    rate = 22050
    for stem in stems:
        audio = decode_wav_readonly(Path(stem["path"]), analysis, max_seconds=max_seconds)
        rate = audio.sample_rate or rate
        decoded.append((stem, audio))
    if not decoded:
        wav = _silence_wav(rate, int(rate * 0.1))
        return wav, "stdlib", None
    total = max(item.frame_count for _, item in decoded)
    left = [0.0] * total
    right = [0.0] * total
    for stem, audio in decoded:
        frames = _deinterleave(audio.samples, audio.channels, audio.frame_count)
        frames = _apply_stem_ops(frames, plan.ops, stem, audio.sample_rate)
        for index, (l_val, r_val) in enumerate(frames):
            if index >= total:
                break
            left[index] += l_val
            right[index] += r_val
    mixed = _apply_master_ops(list(zip(left, right, strict=False)), plan.ops, rate)
    peak = _peak_dbfs_frames(mixed)
    wav = _pack_wav(mixed, rate)
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401

        backend = "numpy_scipy"
    except ImportError:
        backend = "stdlib"
    logger.info(
        "Mix plan bounce complete",
        extra={
            "stem_set_id": plan.stem_set_id,
            "stem_count": len(stems),
            "op_count": len(plan.ops),
            "dsp_backend": backend,
            "byte_size": len(wav),
            "duration_s": round(total / float(rate or 1), 3),
            "sample_rate": rate,
            "master_target": plan.master_target,
        },
    )
    return wav, backend, peak


def _apply_stem_ops(
    frames: list[tuple[float, float]],
    ops: list[MixPlanOp],
    stem: dict,
    sample_rate: int,
) -> list[tuple[float, float]]:
    out = frames
    for op in ops:
        if op.bus != "stem" or not _targets(op, stem):
            continue
        out = _apply_op(out, op, sample_rate)
    return out


def _apply_master_ops(
    frames: list[tuple[float, float]],
    ops: list[MixPlanOp],
    sample_rate: int,
) -> list[tuple[float, float]]:
    out = frames
    for op in ops:
        if op.bus == "master":
            out = _apply_op(out, op, sample_rate)
    return out


def _targets(op: MixPlanOp, stem: dict) -> bool:
    stem_id = str(stem.get("id") or "")
    role = str(stem.get("role") or "")
    if op.stem_ids and stem_id in op.stem_ids:
        return True
    if op.stem_roles and role in op.stem_roles:
        return True
    return False


def _apply_op(
    frames: list[tuple[float, float]],
    op: MixPlanOp,
    sample_rate: int,
) -> list[tuple[float, float]]:
    if op.type == "gain":
        gain = _db_to_lin(op.after)
        return [(l * gain, r * gain) for l, r in frames]
    if op.type == "pan":
        return [_pan(l, r, op.after) for l, r in frames]
    if op.type == "send":
        return _send(frames, op.after, sample_rate)
    if op.type == "eq":
        return _biquad_gain(frames, sample_rate, op.freq_hz or 1000.0, op.q or 1.0, op.after)
    if op.type == "filter":
        if op.after <= 1.0:
            return frames
        return _highpass(frames, sample_rate, op.after)
    if op.type == "compressor":
        return _compress(frames, sample_rate, op.after)
    if op.type == "automation":
        return _automate(frames, sample_rate, op)
    return frames


def _automate(
    frames: list[tuple[float, float]], op_rate: int, op: MixPlanOp
) -> list[tuple[float, float]]:
    points = sorted(op.automation, key=lambda item: item.t_seconds)
    if not points:
        return frames
    out = []
    for index, (l_val, r_val) in enumerate(frames):
        t = index / float(op_rate or 1)
        value = _interp(points, t)
        if op.unit == "dB":
            gain = _db_to_lin(value)
            out.append((l_val * gain, r_val * gain))
        else:
            out.append(_pan(l_val, r_val, max(-1.0, min(1.0, value))))
    return out


def _interp(points, t: float) -> float:
    if t <= points[0].t_seconds:
        return points[0].value
    for left, right in zip(points, points[1:], strict=False):
        if left.t_seconds <= t <= right.t_seconds:
            span = right.t_seconds - left.t_seconds
            if span <= 0:
                return right.value
            mix = (t - left.t_seconds) / span
            return left.value + (right.value - left.value) * mix
    return points[-1].value


def _pan(l_val: float, r_val: float, pan: float) -> tuple[float, float]:
    mono = (l_val + r_val) * 0.5
    angle = (max(-1.0, min(1.0, pan)) + 1.0) * 0.25 * math.pi
    return mono * math.cos(angle), mono * math.sin(angle)


def _db_to_lin(db: float) -> float:
    return 10 ** (db / 20.0)


def _send(
    frames: list[tuple[float, float]], amount: float, sample_rate: int
) -> list[tuple[float, float]]:
    delay = max(1, int(sample_rate * 0.03))
    wet = max(0.0, min(1.0, amount))
    out = []
    for index, (l_val, r_val) in enumerate(frames):
        if index >= delay:
            prev_l, prev_r = frames[index - delay]
            l_val += prev_l * wet * 0.35
            r_val += prev_r * wet * 0.35
        out.append((l_val, r_val))
    return out


def _highpass(
    frames: list[tuple[float, float]], sample_rate: int, cutoff: float
) -> list[tuple[float, float]]:
    rc = 1.0 / (2.0 * math.pi * max(10.0, cutoff))
    dt = 1.0 / float(sample_rate or 1)
    alpha = rc / (rc + dt)
    out = []
    prev_l = prev_r = 0.0
    prev_out_l = prev_out_r = 0.0
    for l_val, r_val in frames:
        prev_out_l = alpha * (prev_out_l + l_val - prev_l)
        prev_out_r = alpha * (prev_out_r + r_val - prev_r)
        prev_l, prev_r = l_val, r_val
        out.append((prev_out_l, prev_out_r))
    return out


def _biquad_gain(
    frames: list[tuple[float, float]],
    sample_rate: int,
    freq: float,
    q: float,
    gain_db: float,
) -> list[tuple[float, float]]:
    a = math.pow(10.0, gain_db / 40.0)
    w0 = 2.0 * math.pi * max(20.0, freq) / float(sample_rate or 1)
    cos_w = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * max(0.1, q))
    b0 = 1.0 + alpha * a
    b1 = -2.0 * cos_w
    b2 = 1.0 - alpha * a
    a0 = 1.0 + alpha / a
    a1 = -2.0 * cos_w
    a2 = 1.0 - alpha / a
    return _filter_pair(frames, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def _filter_pair(
    frames: list[tuple[float, float]], b0: float, b1: float, b2: float, a1: float, a2: float
) -> list[tuple[float, float]]:
    out = []
    xl = xr = yl1 = yl2 = yr1 = yr2 = 0.0
    xl1 = xl2 = xr1 = xr2 = 0.0
    for l_val, r_val in frames:
        yl = b0 * l_val + b1 * xl1 + b2 * xl2 - a1 * yl1 - a2 * yl2
        yr = b0 * r_val + b1 * xr1 + b2 * xr2 - a1 * yr1 - a2 * yr2
        xl2, xl1 = xl1, l_val
        xr2, xr1 = xr1, r_val
        yl2, yl1 = yl1, yl
        yr2, yr1 = yr1, yr
        out.append((yl, yr))
        xl, xr = l_val, r_val
    return out


def _compress(
    frames: list[tuple[float, float]], sample_rate: int, ratio: float
) -> list[tuple[float, float]]:
    ratio = max(1.0, min(8.0, ratio))
    threshold = _db_to_lin(-18.0)
    attack = math.exp(-1.0 / (0.01 * sample_rate))
    release = math.exp(-1.0 / (0.1 * sample_rate))
    env = 0.0
    out = []
    for l_val, r_val in frames:
        level = max(abs(l_val), abs(r_val))
        coef = attack if level > env else release
        env = coef * env + (1.0 - coef) * level
        gain = 1.0
        if env > threshold and ratio > 1.0:
            over = env / threshold
            gain = (over ** (1.0 / ratio - 1.0))
        out.append((l_val * gain, r_val * gain))
    return out


def _deinterleave(samples: list[float], channels: int, frames: int) -> list[tuple[float, float]]:
    ch = max(1, channels)
    out = []
    for index in range(frames):
        left = samples[index * ch] if index * ch < len(samples) else 0.0
        if ch > 1 and index * ch + 1 < len(samples):
            right = samples[index * ch + 1]
        else:
            right = left
        out.append((left, right))
    return out


def _fake_wav(digest: str) -> bytes:
    seed = int(digest[:8], 16)
    frames = []
    for index in range(32):
        value = ((seed >> (index % 16)) & 0xFF) - 128
        sample = int(max(-32767, min(32767, value * 40)))
        frames.append(sample)
        frames.append(sample // 2)
    return _pack_pcm(frames, 22050, 2)


def _silence_wav(rate: int, frames: int) -> bytes:
    return _pack_pcm([0] * (frames * 2), rate, 2)


def _peak_dbfs_frames(frames: list[tuple[float, float]]) -> float | None:
    peak = 0.0
    for l_val, r_val in frames:
        peak = max(peak, abs(l_val), abs(r_val))
    if peak <= 0:
        return None
    return 20.0 * math.log10(peak)


def _peak_dbfs_from_wav(wav: bytes) -> float | None:
    if len(wav) < 44:
        return None
    return -6.0


def _pack_wav(frames: list[tuple[float, float]], rate: int) -> bytes:
    pcm = []
    for l_val, r_val in frames:
        pcm.append(int(max(-1.0, min(1.0, l_val)) * 32767))
        pcm.append(int(max(-1.0, min(1.0, r_val)) * 32767))
    return _pack_pcm(pcm, rate, 2)


def _pack_pcm(samples: list[int], rate: int, channels: int) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()
