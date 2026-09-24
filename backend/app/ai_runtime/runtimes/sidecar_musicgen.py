"""HTTP client for optional MusicGen-shaped neural audio sidecar.

FastAPI never loads model weights. Soft health probe mirrors local LLM pattern.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.ai_runtime.capabilities import ModelCapability
from app.ai_runtime.errors import ModelUnavailableError
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.types import ModelDescriptor, ModelHealth
from app.neural_audio_settings import load_neural_audio_settings


logger = logging.getLogger(__name__)

SIDECAR_MUSICGEN_MODEL_ID = "sidecar:musicgen"
SIDECAR_MUSICGEN_RUNTIME = "sidecar_musicgen"
SIDECAR_MUSICGEN_VERSION = "musicgen-sidecar-1"
DEFAULT_PROBE_TIMEOUT_SECONDS = 2.5


class SidecarMusicGenAudioModel:
    """Bounded HTTP render against ``NEURAL_AUDIO_SIDECAR_BASE_URL``."""

    def __init__(self, descriptor: ModelDescriptor, *, base_url: str, timeout_seconds: int) -> None:
        self._descriptor = descriptor
        self._base_url = (base_url or "").rstrip("/")
        self._timeout_seconds = timeout_seconds
        logger.info(
            "Constructed sidecar MusicGen audio model",
            extra={
                "model_id": descriptor.id,
                "runtime": descriptor.runtime,
                "status": descriptor.status,
                "has_base_url": bool(self._base_url),
            },
        )

    @property
    def model_id(self) -> str:
        return self._descriptor.id

    @property
    def descriptor(self) -> ModelDescriptor:
        return self._descriptor

    @property
    def model_version(self) -> str:
        return self._descriptor.model_version or SIDECAR_MUSICGEN_VERSION

    @property
    def fidelity_class(self) -> str:
        return "generative"

    @property
    def preferred_adapter_kind(self) -> str:
        return "melody_conditioning"

    async def render(self, composition_or_spec: Any) -> dict[str, Any]:
        if not self._base_url:
            raise ModelUnavailableError(
                "MusicGen sidecar base URL is not configured",
                code="model_unavailable",
            )
        payload = _build_sidecar_payload(composition_or_spec)
        url = f"{self._base_url}/v1/audio/render"
        body = json.dumps(payload).encode("utf-8")
        started = time.monotonic()
        logger.info(
            "Sidecar MusicGen render request start",
            extra={
                "model_id": self.model_id,
                "adapter_kind": payload.get("adapter_kind"),
                "prompt_chars": len(str(payload.get("prompt") or "")),
            },
        )
        try:
            request = Request(
                url,
                data=body,
                method="POST",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                raw = response.read(16 * 1024 * 1024)
                http_status = getattr(response, "status", 200)
        except HTTPError as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "Sidecar MusicGen HTTP error",
                extra={
                    "model_id": self.model_id,
                    "http_status": int(getattr(exc, "code", 0) or 0),
                    "latency_ms": latency_ms,
                    "error_type": "HTTPError",
                },
            )
            raise ModelUnavailableError(
                "MusicGen sidecar render failed",
                code="model_unavailable",
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            logger.warning(
                "Sidecar MusicGen unreachable",
                extra={
                    "model_id": self.model_id,
                    "latency_ms": latency_ms,
                    "error_type": type(exc).__name__,
                },
            )
            raise ModelUnavailableError(
                "MusicGen sidecar unreachable",
                code="model_unavailable",
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        if http_status != 200:
            logger.warning(
                "Sidecar MusicGen non-200",
                extra={"model_id": self.model_id, "http_status": http_status, "latency_ms": latency_ms},
            )
            raise ModelUnavailableError(
                "MusicGen sidecar returned an error status",
                code="model_unavailable",
            )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelUnavailableError(
                "MusicGen sidecar returned invalid JSON",
                code="model_unavailable",
            ) from exc
        audio_b64 = parsed.get("audio_base64") if isinstance(parsed, dict) else None
        if not isinstance(audio_b64, str) or not audio_b64:
            raise ModelUnavailableError(
                "MusicGen sidecar response missing audio",
                code="model_unavailable",
            )
        try:
            audio_bytes = base64.b64decode(audio_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ModelUnavailableError(
                "MusicGen sidecar audio decode failed",
                code="model_unavailable",
            ) from exc
        content_type = str(parsed.get("content_type") or "audio/wav")
        logger.info(
            "Sidecar MusicGen render complete",
            extra={
                "model_id": self.model_id,
                "latency_ms": latency_ms,
                "byte_size": len(audio_bytes),
                "http_status": http_status,
            },
        )
        return {
            "audio_bytes": audio_bytes,
            "content_type": content_type,
            "model_version": str(parsed.get("model_version") or self.model_version),
            "fidelity_class": "generative",
            "ext": "wav" if "wav" in content_type else "flac",
        }


def probe_sidecar_musicgen_health(
    *,
    base_url: str,
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """GET ``{base}/health`` with a short timeout. Never logs secrets."""
    root = (base_url or "").rstrip("/")
    if not root:
        return {"status": "unavailable", "reachable": False, "latency_ms": None}
    url = f"{root}/health"
    started = time.monotonic()
    try:
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            _ = response.read(2048)
            http_status = getattr(response, "status", 200)
        latency_ms = int((time.monotonic() - started) * 1000)
        ready = http_status == 200
        result = {
            "status": "ready" if ready else "unavailable",
            "reachable": True,
            "latency_ms": latency_ms,
        }
        logger.info(
            "Neural audio sidecar health probe",
            extra={**result, "model_id": SIDECAR_MUSICGEN_MODEL_ID},
        )
        return result
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        result = {
            "status": "unavailable",
            "reachable": False,
            "latency_ms": latency_ms,
            "error_type": type(exc).__name__,
        }
        logger.info(
            "Neural audio sidecar health probe",
            extra={**result, "model_id": SIDECAR_MUSICGEN_MODEL_ID},
        )
        return result


def sidecar_musicgen_descriptor(
    env: Mapping[str, str] | None = None,
) -> ModelDescriptor | None:
    settings = load_neural_audio_settings(env)
    # Explicit fake pin: skip sidecar discovery/probe noise in CI.
    if settings.engine == "fake:neural-audio":
        return None
    if settings.engine == "local:midi-ddsp":
        return None
    probe = probe_sidecar_musicgen_health(base_url=settings.sidecar_base_url)
    if settings.engine == "sidecar:musicgen" or probe.get("status") == "ready":
        status = "ready" if probe.get("status") == "ready" else "unavailable"
        return ModelDescriptor(
            id=SIDECAR_MUSICGEN_MODEL_ID,
            display_name="MusicGen sidecar (generative)",
            provider="sidecar",
            runtime=SIDECAR_MUSICGEN_RUNTIME,  # type: ignore[arg-type]
            primary_capability=ModelCapability.AUDIO_GENERATION,
            locality="local",
            model_version=SIDECAR_MUSICGEN_VERSION,
            supported_operations=(AiOperation.AUDIO_RENDER,),
            status=status,  # type: ignore[arg-type]
            health=ModelHealth(
                status=status,  # type: ignore[arg-type]
                detail="sidecar_health",
                credentials_present=True,
            ),
            limits={
                "fidelity_class": "generative",
                "preferred_adapter": "melody_conditioning",
                "note_perfect": False,
                "max_concurrency": settings.max_concurrency,
                "stem_capabilities": [
                    "per_track",
                    "grouped_tracks",
                    "section_symbolic_filter",
                ],
            },
            provider_model="musicgen",
        )
    if settings.engine == "auto":
        # Soft discovery: list as unavailable so UI can explain optional profile.
        return ModelDescriptor(
            id=SIDECAR_MUSICGEN_MODEL_ID,
            display_name="MusicGen sidecar (optional)",
            provider="sidecar",
            runtime=SIDECAR_MUSICGEN_RUNTIME,  # type: ignore[arg-type]
            primary_capability=ModelCapability.AUDIO_GENERATION,
            locality="local",
            model_version=SIDECAR_MUSICGEN_VERSION,
            supported_operations=(AiOperation.AUDIO_RENDER,),
            status="unconfigured",
            health=ModelHealth(
                status="unconfigured",
                detail="sidecar_not_reachable",
                credentials_present=False,
            ),
            limits={
                "fidelity_class": "generative",
                "preferred_adapter": "melody_conditioning",
                "note_perfect": False,
                "stem_capabilities": [
                    "per_track",
                    "grouped_tracks",
                    "section_symbolic_filter",
                ],
            },
            provider_model="musicgen",
        )
    return None


def build_sidecar_musicgen_model(
    descriptor: ModelDescriptor,
    env: Mapping[str, str] | None = None,
) -> SidecarMusicGenAudioModel:
    settings = load_neural_audio_settings(env)
    return SidecarMusicGenAudioModel(
        descriptor,
        base_url=settings.sidecar_base_url,
        timeout_seconds=settings.job_timeout_seconds,
    )


def _build_sidecar_payload(composition_or_spec: Any) -> dict[str, Any]:
    if not isinstance(composition_or_spec, dict):
        return {"prompt": "", "adapter_kind": "text_prompt"}
    # Never forward full composition JSON / event arrays to the sidecar log path.
    prompt = str(composition_or_spec.get("prompt") or composition_or_spec.get("instructions") or "")
    return {
        "prompt": prompt[:2000],
        "adapter_kind": composition_or_spec.get("adapter_kind") or "text_prompt",
        "tempo_bpm": composition_or_spec.get("tempo_bpm"),
        "genre": composition_or_spec.get("genre"),
        "mood": composition_or_spec.get("mood"),
        "seed": composition_or_spec.get("seed"),
        "melody_midi_base64": composition_or_spec.get("melody_midi_base64"),
        "max_audio_seconds": composition_or_spec.get("max_audio_seconds"),
    }
