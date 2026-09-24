"""Optional source separation adapters for audio recovery.

Product stems: vocals | melody | bass | drums | harmonic | other.
Demucs-style sidecar maps vocals/bass/drums/other → product stems.
FastAPI never imports torch/Demucs — HTTP sidecar only.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.audio_recovery_schemas import AudioRecoveryIssue, AudioRecoveryStemRole
from app.audio_recovery_settings import AudioRecoverySettings


logger = logging.getLogger(__name__)

DEFAULT_PROBE_TIMEOUT_SECONDS = 2.0
DEFAULT_SEPARATE_TIMEOUT_SECONDS = 120.0

# Demucs-style stem names → product stem roles (Part D lock).
DEMUCS_TO_PRODUCT_STEM_MAP: dict[str, list[AudioRecoveryStemRole]] = {
    "vocals": ["vocals", "melody"],
    "bass": ["bass"],
    "drums": ["drums"],
    "other": ["harmonic", "other"],
}

SeparationStatus = Literal[
    "skipped_mono",
    "unavailable",
    "partial",
    "complete",
    "off",
]


@dataclass(frozen=True)
class SeparatedStem:
    """One product stem with optional PCM path under the job workdir."""

    role: AudioRecoveryStemRole
    path: Path | None
    derived_from: str | None = None
    byte_size: int = 0


@dataclass
class SeparationResult:
    status: SeparationStatus
    engine_id: str
    stems: list[SeparatedStem] = field(default_factory=list)
    issues: list[AudioRecoveryIssue] = field(default_factory=list)
    fake: bool = False


@runtime_checkable
class SeparationEngine(Protocol):
    engine_id: str

    def is_available(self) -> bool: ...

    def separate(
        self,
        source_path: Path,
        *,
        work_dir: Path,
        settings: AudioRecoverySettings,
    ) -> SeparationResult: ...


def _sha256_prefix(data: bytes, *, length: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:length]


AUDIO_RECOVERY_ISSUE_MSG_MELODY = (
    "Melody destination derived from vocals stem (no separate lead stem)."
)


def map_demucs_stems_to_product(
    demucs_roles: list[str],
) -> tuple[list[AudioRecoveryStemRole], list[AudioRecoveryIssue]]:
    """Apply locked Demucs→product map; never invent empty roles."""
    product: list[AudioRecoveryStemRole] = []
    issues: list[AudioRecoveryIssue] = []
    seen: set[str] = set()
    for demucs_role in demucs_roles:
        mapped = DEMUCS_TO_PRODUCT_STEM_MAP.get(demucs_role)
        if not mapped:
            logger.warning(
                "Unknown Demucs stem role ignored",
                extra={"demucs_role": demucs_role},
            )
            continue
        for role in mapped:
            if role in seen:
                continue
            seen.add(role)
            product.append(role)
            if demucs_role == "vocals" and role == "melody":
                issues.append(
                    AudioRecoveryIssue(
                        code="melody_derived_from_vocals",
                        severity="info",
                        message=AUDIO_RECOVERY_ISSUE_MSG_MELODY,
                        stem="melody",
                    )
                )
            if demucs_role == "other" and role in {"harmonic", "other"}:
                # Ambiguous other → both harmonic and other flagged partial once.
                pass
    if "other" in demucs_roles and ("harmonic" in seen or "other" in seen):
        issues.append(
            AudioRecoveryIssue(
                code="separation_partial",
                severity="warning",
                message="Demucs 'other' mapped to harmonic/other with ambiguity.",
            )
        )
    logger.info(
        "Demucs stems mapped to product roles",
        extra={
            "demucs_roles": sorted(demucs_roles),
            "product_stems": sorted(product),
            "issue_count": len(issues),
        },
    )
    return product, issues


def estimate_mono_enough(
    samples: list[float],
    *,
    sample_rate: int,
    channels: int = 1,
) -> bool:
    """Heuristic: skip separation when energy looks mono / single-source enough."""
    if not samples or sample_rate <= 0:
        return True
    if channels <= 1:
        # Single-channel: still check for sparse/simple energy.
        rms = math.sqrt(sum(s * s for s in samples) / len(samples))
        if rms < 1e-4:
            return True
        # Zero-crossing rate: very stable tones / hums → mono-enough.
        zc = 0
        for i in range(1, min(len(samples), sample_rate * 2)):
            if (samples[i - 1] >= 0) != (samples[i] >= 0):
                zc += 1
        zcr = zc / max(1, min(len(samples), sample_rate * 2) - 1)
        mono_enough = zcr < 0.15 or rms < 0.02
        logger.debug(
            "Mono-enough heuristic (mono channel)",
            extra={"rms": round(rms, 6), "zcr": round(zcr, 4), "mono_enough": mono_enough},
        )
        return mono_enough
    return False


def read_wav_mono_floats(path: Path) -> tuple[list[float], int, int]:
    """Return (mono samples, sample_rate, channel_count)."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        raw = handle.readframes(frame_count)
    if sample_width != 2 or sample_rate <= 0 or frame_count <= 0:
        return [], sample_rate, channels
    fmt = f"<{frame_count * channels}h"
    ints = struct.unpack(fmt, raw)
    if channels == 1:
        samples = [v / 32768.0 for v in ints]
    else:
        samples = []
        for i in range(0, len(ints), channels):
            frame = ints[i : i + channels]
            samples.append(sum(frame) / (channels * 32768.0))
    return samples, sample_rate, channels


def _write_silence_wav(path: Path, *, duration_s: float, sample_rate: int = 22050) -> int:
    n = max(1, int(sample_rate * duration_s))
    samples = [0] * n
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<" + "h" * n, *samples))
    return path.stat().st_size


def _write_slice_wav(
    source_path: Path,
    dest_path: Path,
    *,
    start_ratio: float,
    end_ratio: float,
) -> int:
    """Copy a time slice of source WAV as a deterministic fake stem snippet."""
    with wave.open(str(source_path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frame_count = handle.getnframes()
        raw = handle.readframes(frame_count)
    start = int(frame_count * start_ratio)
    end = max(start + 1, int(frame_count * end_ratio))
    frame_bytes = channels * sample_width
    chunk = raw[start * frame_bytes : end * frame_bytes]
    if not chunk:
        chunk = raw[:frame_bytes] if raw else b"\x00" * frame_bytes
    with wave.open(str(dest_path), "w") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(sample_rate)
        out.writeframes(chunk)
    return dest_path.stat().st_size


# Digest prefix → product stem roles for fake CI fixtures.
FAKE_STEM_DIGEST_TABLE: dict[str, list[AudioRecoveryStemRole]] = {
    # Default mixed demo → full-ish stem set (no empty invented WAVs beyond roles present).
    "default": ["melody", "bass", "drums", "harmonic"],
    "mono": ["melody"],
    "vocals_bass": ["vocals", "melody", "bass"],
}


def resolve_fake_stem_roles(digest_prefix: str) -> list[AudioRecoveryStemRole]:
    for key, roles in FAKE_STEM_DIGEST_TABLE.items():
        if key != "default" and digest_prefix.startswith(key):
            return list(roles)
    # Stable partition by digest nibble.
    nibble = int(digest_prefix[:1], 16) if digest_prefix else 0
    if nibble < 4:
        return list(FAKE_STEM_DIGEST_TABLE["mono"])
    if nibble < 8:
        return list(FAKE_STEM_DIGEST_TABLE["vocals_bass"])
    return list(FAKE_STEM_DIGEST_TABLE["default"])


class FakeStemsEngine:
    engine_id = "fake:stems"

    def is_available(self) -> bool:
        return True

    def separate(
        self,
        source_path: Path,
        *,
        work_dir: Path,
        settings: AudioRecoverySettings,
    ) -> SeparationResult:
        payload = source_path.read_bytes()
        digest = _sha256_prefix(payload)
        roles = resolve_fake_stem_roles(digest)
        issues: list[AudioRecoveryIssue] = []
        stems: list[SeparatedStem] = []
        work_dir.mkdir(parents=True, exist_ok=True)
        ratios = {
            "vocals": (0.0, 0.35),
            "melody": (0.0, 0.4),
            "bass": (0.35, 0.55),
            "drums": (0.55, 0.7),
            "harmonic": (0.7, 0.9),
            "other": (0.85, 1.0),
        }
        for role in roles:
            start, end = ratios.get(role, (0.0, 0.25))
            dest = work_dir / f"stem_{role}.wav"
            try:
                byte_size = _write_slice_wav(source_path, dest, start_ratio=start, end_ratio=end)
            except (wave.Error, OSError, struct.error) as exc:
                logger.warning(
                    "Fake stem slice failed; writing silence",
                    extra={"stem": role, "error_type": type(exc).__name__},
                )
                byte_size = _write_silence_wav(dest, duration_s=0.25)
            derived = "vocals" if role == "melody" and "vocals" not in roles else None
            if derived:
                issues.append(
                    AudioRecoveryIssue(
                        code="melody_derived_from_vocals",
                        severity="info",
                        message=AUDIO_RECOVERY_ISSUE_MSG_MELODY,
                        stem="melody",
                    )
                )
            stems.append(
                SeparatedStem(
                    role=role,
                    path=dest,
                    derived_from=derived,
                    byte_size=byte_size,
                )
            )
        logger.info(
            "Fake separation complete",
            extra={
                "engine_id": self.engine_id,
                "digest_prefix": digest,
                "stem_keys": [s.role for s in stems],
            },
        )
        return SeparationResult(
            status="complete",
            engine_id=self.engine_id,
            stems=stems,
            issues=issues,
            fake=True,
        )


def probe_demucs_sidecar_health(
    *,
    base_url: str,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> bool:
    root = base_url.rstrip("/")
    url = f"{root}/health"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            ok = 200 <= int(response.status) < 300
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.warning(
            "Audio recovery separation sidecar unhealthy",
            extra={"error_type": type(exc).__name__, "has_base_url": bool(base_url)},
        )
        return False
    logger.debug(
        "Audio recovery separation sidecar health",
        extra={"healthy": ok},
    )
    return ok


class DemucsSidecarEngine:
    engine_id = "sidecar:demucs"

    def is_available(self) -> bool:
        return True  # Availability checked with settings URL at separate() time.

    def separate(
        self,
        source_path: Path,
        *,
        work_dir: Path,
        settings: AudioRecoverySettings,
    ) -> SeparationResult:
        base = settings.sidecar_base_url
        if not probe_demucs_sidecar_health(base_url=base):
            return SeparationResult(
                status="unavailable",
                engine_id=self.engine_id,
                issues=[
                    AudioRecoveryIssue(
                        code="separation_unavailable",
                        severity="warning",
                        message="Demucs sidecar health check failed.",
                    )
                ],
                fake=False,
            )
        # HTTP contract: POST /v1/separate returns JSON with stem role → base64 WAV.
        # Real sidecar optional; when unhealthy we already returned. When healthy but
        # endpoint missing, treat as unavailable (no crash).
        url = f"{base.rstrip('/')}/v1/separate"
        try:
            payload = source_path.read_bytes()
            request = Request(
                url,
                data=payload,
                method="POST",
                headers={"Content-Type": "audio/wav", "Accept": "application/json"},
            )
            with urlopen(  # noqa: S310
                request, timeout=min(settings.job_timeout_seconds, DEFAULT_SEPARATE_TIMEOUT_SECONDS)
            ) as response:
                body = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "Demucs sidecar separate failed",
                extra={"error_type": type(exc).__name__},
            )
            return SeparationResult(
                status="unavailable",
                engine_id=self.engine_id,
                issues=[
                    AudioRecoveryIssue(
                        code="separation_unavailable",
                        severity="warning",
                        message="Demucs sidecar separate request failed.",
                    )
                ],
                fake=False,
            )
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return SeparationResult(
                status="unavailable",
                engine_id=self.engine_id,
                issues=[
                    AudioRecoveryIssue(
                        code="separation_unavailable",
                        severity="warning",
                        message="Demucs sidecar returned invalid JSON.",
                    )
                ],
                fake=False,
            )
        demucs_stems = data.get("stems") if isinstance(data, dict) else None
        if not isinstance(demucs_stems, dict) or not demucs_stems:
            return SeparationResult(
                status="partial",
                engine_id=self.engine_id,
                issues=[
                    AudioRecoveryIssue(
                        code="separation_partial",
                        severity="warning",
                        message="Demucs sidecar returned no stems.",
                    )
                ],
                fake=False,
            )

        product_roles, map_issues = map_demucs_stems_to_product(list(demucs_stems.keys()))
        work_dir.mkdir(parents=True, exist_ok=True)
        stems: list[SeparatedStem] = []
        # Write demucs files first, then alias product roles to those files.
        demucs_paths: dict[str, Path] = {}
        for demucs_role, b64 in demucs_stems.items():
            if not isinstance(b64, str):
                continue
            try:
                wav_bytes = base64.b64decode(b64)
            except (ValueError, TypeError):
                continue
            path = work_dir / f"demucs_{demucs_role}.wav"
            path.write_bytes(wav_bytes)
            demucs_paths[demucs_role] = path
        for demucs_role, roles in DEMUCS_TO_PRODUCT_STEM_MAP.items():
            src = demucs_paths.get(demucs_role)
            if src is None:
                continue
            for role in roles:
                if role not in product_roles:
                    continue
                dest = work_dir / f"stem_{role}.wav"
                if not dest.exists():
                    dest.write_bytes(src.read_bytes())
                stems.append(
                    SeparatedStem(
                        role=role,
                        path=dest,
                        derived_from="vocals" if role == "melody" and demucs_role == "vocals" else None,
                        byte_size=dest.stat().st_size,
                    )
                )
        status: SeparationStatus = "complete" if stems else "partial"
        if status == "partial":
            map_issues.append(
                AudioRecoveryIssue(
                    code="separation_partial",
                    severity="warning",
                    message="Demucs mapping produced incomplete product stems.",
                )
            )
        logger.info(
            "Demucs sidecar separation finished",
            extra={
                "engine_id": self.engine_id,
                "status": status,
                "stem_keys": [s.role for s in stems],
            },
        )
        return SeparationResult(
            status=status,
            engine_id=self.engine_id,
            stems=stems,
            issues=map_issues,
            fake=False,
        )


def select_separation_engine(
    settings: AudioRecoverySettings,
    *,
    user_disable: bool = False,
) -> SeparationEngine | None:
    if user_disable or settings.separation_engine == "off":
        logger.info(
            "Separation engine off",
            extra={"separation_engine": settings.separation_engine, "user_disable": user_disable},
        )
        return None
    if settings.separation_engine == "fake:stems" or (
        settings.separation_engine == "auto" and settings.fake_mode
    ):
        return FakeStemsEngine()
    if settings.separation_engine in {"sidecar:demucs", "auto"}:
        if settings.fake_mode and settings.separation_engine == "auto":
            return FakeStemsEngine()
        return DemucsSidecarEngine()
    return None


def run_separation(
    source_path: Path,
    *,
    work_dir: Path,
    settings: AudioRecoverySettings,
    user_disable: bool = False,
    force_combined: bool = False,
) -> SeparationResult:
    """Run separation policy: off / mono-skip / fake / sidecar / unavailable."""
    if user_disable or settings.separation_engine == "off" or force_combined:
        logger.info(
            "Separation skipped by policy",
            extra={
                "reason": "off" if not force_combined else "force_combined",
                "separation_engine": settings.separation_engine,
            },
        )
        return SeparationResult(
            status="off",
            engine_id="off",
            issues=[],
            fake=False,
        )

    # Fake stems must exercise product roles in CI — do not skip via mono heuristic.
    skip_mono_heuristic = settings.fake_mode or settings.separation_engine == "fake:stems"
    if not skip_mono_heuristic:
        samples, sample_rate, channels = read_wav_mono_floats(source_path)
        if samples and estimate_mono_enough(
            samples, sample_rate=sample_rate, channels=channels
        ):
            logger.info(
                "Separation skipped mono-enough",
                extra={"sample_rate": sample_rate, "channels": channels},
            )
            return SeparationResult(
                status="skipped_mono",
                engine_id="policy:mono",
                issues=[
                    AudioRecoveryIssue(
                        code="separation_skipped_mono",
                        severity="info",
                        message="Source looked mono-enough; separation was skipped.",
                    )
                ],
                fake=False,
            )

    engine = select_separation_engine(settings, user_disable=user_disable)
    if engine is None:
        return SeparationResult(
            status="unavailable",
            engine_id="none",
            issues=[
                AudioRecoveryIssue(
                    code="separation_unavailable",
                    severity="warning",
                    message="No separation engine selected.",
                )
            ],
            fake=False,
        )

    if isinstance(engine, DemucsSidecarEngine) and settings.fake_mode is False:
        if not probe_demucs_sidecar_health(base_url=settings.sidecar_base_url):
            logger.warning(
                "Separation unavailable; sidecar unhealthy",
                extra={"engine_id": engine.engine_id},
            )
            return SeparationResult(
                status="unavailable",
                engine_id=engine.engine_id,
                issues=[
                    AudioRecoveryIssue(
                        code="separation_unavailable",
                        severity="warning",
                        message="Separation sidecar unavailable; combined path will be used.",
                    )
                ],
                fake=False,
            )

    logger.info(
        "Separation engine selected",
        extra={"engine_id": engine.engine_id, "fake_mode": settings.fake_mode},
    )
    return engine.separate(source_path, work_dir=work_dir, settings=settings)
