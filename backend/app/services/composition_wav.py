"""Server-side Composition V1 → WAV rendering via FluidSynth CLI.

WAV note content is always derived from ``render_midi`` so export fidelity
matches MIDI; this module only synthesizes PCM and enforces duration/silence.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

from app.schemas import Composition
from app.services.composition_midi import CompositionMidiError, render_midi

logger = logging.getLogger(__name__)

DEFAULT_FLUIDSYNTH_BIN = "fluidsynth"
DEFAULT_SOUNDFONT_PATH = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_GAIN = 0.5
DEFAULT_RENDER_TIMEOUT_SECONDS = 60.0
MAX_CAPTURED_OUTPUT_CHARS = 500
# Tolerate FluidSynth ending slightly early before padding (seconds).
DURATION_PAD_TOLERANCE_SECONDS = 0.02
# Hard fail if output is wildly longer than expected (runaway render).
DURATION_MAX_OVERSHOOT_RATIO = 2.0


class CompositionWavError(Exception):
    """Raised when WAV rendering fails.

    ``unavailable`` marks missing FluidSynth binary or SoundFont (HTTP 503).
    Other failures map to HTTP 500.
    """

    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.unavailable = unavailable


@dataclass(frozen=True)
class WavRendererConfig:
    fluidsynth_bin: str
    soundfont_path: str
    sample_rate: int
    gain: float
    timeout_seconds: float
    fluidsynth_exists: bool
    soundfont_exists: bool

    @property
    def fluidsynth_basename(self) -> str:
        return Path(self.fluidsynth_bin).name

    @property
    def soundfont_basename(self) -> str:
        return Path(self.soundfont_path).name


def load_wav_renderer_config() -> WavRendererConfig:
    """Resolve renderer settings from the environment at call time."""
    fluidsynth_bin = os.environ.get("FLUIDSYNTH_BIN", DEFAULT_FLUIDSYNTH_BIN).strip() or DEFAULT_FLUIDSYNTH_BIN
    soundfont_path = (
        os.environ.get("COMPOSITION_WAV_SOUNDFONT", DEFAULT_SOUNDFONT_PATH).strip() or DEFAULT_SOUNDFONT_PATH
    )
    sample_rate = _env_int("COMPOSITION_WAV_SAMPLE_RATE", DEFAULT_SAMPLE_RATE, minimum=8000, maximum=192000)
    gain = _env_float("COMPOSITION_WAV_GAIN", DEFAULT_GAIN, minimum=0.01, maximum=10.0)
    timeout_seconds = _env_float(
        "COMPOSITION_WAV_TIMEOUT_SECONDS",
        DEFAULT_RENDER_TIMEOUT_SECONDS,
        minimum=1.0,
        maximum=600.0,
    )

    if os.path.sep in fluidsynth_bin:
        fluidsynth_path = fluidsynth_bin
        fluidsynth_exists = Path(fluidsynth_bin).is_file() and os.access(fluidsynth_bin, os.X_OK)
    else:
        resolved = shutil.which(fluidsynth_bin)
        fluidsynth_exists = resolved is not None
        fluidsynth_path = resolved or fluidsynth_bin
    soundfont_exists = Path(soundfont_path).is_file()

    config = WavRendererConfig(
        fluidsynth_bin=fluidsynth_path,
        soundfont_path=soundfont_path,
        sample_rate=sample_rate,
        gain=gain,
        timeout_seconds=timeout_seconds,
        fluidsynth_exists=fluidsynth_exists,
        soundfont_exists=soundfont_exists,
    )
    logger.debug(
        "Resolved WAV renderer config",
        extra={
            "fluidsynth_bin": config.fluidsynth_basename,
            "soundfont": config.soundfont_basename,
            "soundfont_dir": str(Path(config.soundfont_path).parent),
            "sample_rate": config.sample_rate,
            "gain": config.gain,
            "timeout_seconds": config.timeout_seconds,
            "fluidsynth_exists": config.fluidsynth_exists,
            "soundfont_exists": config.soundfont_exists,
        },
    )
    return config


def expected_duration_seconds(composition: Composition) -> float:
    """Canonical composition duration in seconds from ticks and tempo."""
    if composition.tempo <= 0 or composition.ticks_per_quarter <= 0:
        raise CompositionWavError("Composition tempo/ticks_per_quarter must be positive")
    seconds = (composition.duration_ticks / composition.ticks_per_quarter) * (60.0 / composition.tempo)
    logger.debug(
        "Computed expected WAV duration",
        extra={
            "duration_ticks": composition.duration_ticks,
            "ticks_per_quarter": composition.ticks_per_quarter,
            "tempo": composition.tempo,
            "expected_seconds": round(seconds, 6),
        },
    )
    return seconds


def render_wav(composition: Composition) -> bytes:
    """Render canonical Composition V1 to WAV bytes via MIDI + FluidSynth."""
    event_count = sum(len(track.events) for track in composition.tracks)
    config = load_wav_renderer_config()
    expected_seconds = expected_duration_seconds(composition)

    logger.info(
        "WAV render started",
        extra={
            "format": "wav",
            "schema_version": composition.schema_version,
            "track_count": len(composition.tracks),
            "event_count": event_count,
            "duration_ticks": composition.duration_ticks,
            "tempo": composition.tempo,
            "fluidsynth": config.fluidsynth_basename,
            "soundfont": config.soundfont_basename,
            "sample_rate": config.sample_rate,
            "renderer_available": config.fluidsynth_exists and config.soundfont_exists,
        },
    )

    if event_count == 0:
        logger.debug(
            "WAV silence path selected",
            extra={"reason": "zero_note_events", "expected_seconds": round(expected_seconds, 6)},
        )
        wav_bytes = _build_silent_wav(expected_seconds, config.sample_rate)
        logger.info(
            "WAV render completed",
            extra={
                "format": "wav",
                "path": "silence",
                "byte_length": len(wav_bytes),
                "event_count": 0,
                "expected_seconds": round(expected_seconds, 6),
            },
        )
        return wav_bytes

    _ensure_renderer_available(config)

    try:
        midi_bytes = render_midi(composition)
    except CompositionMidiError as exc:
        logger.error(
            "WAV render failed during MIDI stage",
            extra={"error_type": type(exc).__name__, "event_count": event_count},
        )
        raise CompositionWavError("Failed to render MIDI for WAV export") from exc

    logger.debug(
        "MIDI bytes ready for FluidSynth",
        extra={
            "midi_byte_length": len(midi_bytes),
            "track_count": len(composition.tracks),
            "event_count": event_count,
            "duration_ticks": composition.duration_ticks,
            "sample_rate": config.sample_rate,
            "gain": config.gain,
            "timeout_seconds": config.timeout_seconds,
        },
    )

    wav_bytes = _synthesize_wav_with_fluidsynth(midi_bytes, config)
    wav_bytes = _ensure_wav_duration(wav_bytes, expected_seconds, config.sample_rate)

    logger.info(
        "WAV render completed",
        extra={
            "format": "wav",
            "path": "fluidsynth",
            "byte_length": len(wav_bytes),
            "event_count": event_count,
            "expected_seconds": round(expected_seconds, 6),
        },
    )
    return wav_bytes


def _ensure_renderer_available(config: WavRendererConfig) -> None:
    if not config.fluidsynth_exists:
        logger.error(
            "FluidSynth binary missing for WAV render",
            extra={"fluidsynth_bin": config.fluidsynth_basename, "unavailable": True},
        )
        raise CompositionWavError(
            f"WAV renderer unavailable: FluidSynth binary not found ({config.fluidsynth_basename})",
            unavailable=True,
        )
    if not config.soundfont_exists:
        logger.error(
            "SoundFont missing for WAV render",
            extra={
                "soundfont": config.soundfont_basename,
                "soundfont_dir": str(Path(config.soundfont_path).parent),
                "unavailable": True,
            },
        )
        raise CompositionWavError(
            f"WAV renderer unavailable: SoundFont not found ({config.soundfont_basename})",
            unavailable=True,
        )


def _synthesize_wav_with_fluidsynth(midi_bytes: bytes, config: WavRendererConfig) -> bytes:
    logger.debug(
        "Starting FluidSynth temp lifecycle",
        extra={"timeout_seconds": config.timeout_seconds, "temp_scoped": True},
    )
    try:
        with tempfile.TemporaryDirectory(prefix="mukit-wav-") as temp_dir:
            temp_path = Path(temp_dir)
            midi_path = temp_path / "input.mid"
            wav_path = temp_path / "output.wav"
            midi_path.write_bytes(midi_bytes)

            command = [
                config.fluidsynth_bin,
                "-ni",
                "-F",
                str(wav_path),
                "-r",
                str(config.sample_rate),
                "-g",
                str(config.gain),
                "-T",
                "wav",
                config.soundfont_path,
                str(midi_path),
            ]
            logger.debug(
                "Invoking FluidSynth",
                extra={
                    "argv": _redact_command_paths(command),
                    "sample_rate": config.sample_rate,
                    "gain": config.gain,
                    "timeout_seconds": config.timeout_seconds,
                },
            )
            try:
                completed = subprocess.run(
                    command,
                    shell=False,
                    check=False,
                    capture_output=True,
                    timeout=config.timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                logger.warning(
                    "FluidSynth render timed out",
                    extra={
                        "timeout_seconds": config.timeout_seconds,
                        "fluidsynth": config.fluidsynth_basename,
                    },
                )
                raise CompositionWavError(
                    f"WAV render timed out after {config.timeout_seconds:.0f}s"
                ) from exc

            if completed.returncode != 0:
                stderr_snippet = _bound_output(completed.stderr)
                stdout_snippet = _bound_output(completed.stdout)
                logger.error(
                    "FluidSynth exited non-zero",
                    extra={
                        "exit_code": completed.returncode,
                        "stderr_snippet": stderr_snippet,
                        "stdout_snippet": stdout_snippet,
                    },
                )
                raise CompositionWavError(
                    f"FluidSynth failed with exit code {completed.returncode}"
                )

            if not wav_path.is_file():
                logger.error("FluidSynth produced no WAV output file")
                raise CompositionWavError("FluidSynth did not produce a WAV file")

            wav_bytes = wav_path.read_bytes()
            if not _is_valid_wav(wav_bytes):
                logger.error(
                    "FluidSynth output is not a valid RIFF/WAVE file",
                    extra={"byte_length": len(wav_bytes)},
                )
                raise CompositionWavError("FluidSynth produced invalid WAV output")

            logger.debug(
                "FluidSynth temp lifecycle completed",
                extra={"byte_length": len(wav_bytes), "temp_scoped": True},
            )
            return wav_bytes
    except CompositionWavError:
        raise
    except OSError as exc:
        logger.error(
            "WAV temp filesystem failure",
            extra={"error_type": type(exc).__name__},
        )
        raise CompositionWavError("Failed to prepare temporary files for WAV render") from exc


def _ensure_wav_duration(wav_bytes: bytes, expected_seconds: float, sample_rate: int) -> bytes:
    try:
        measured = _wav_duration_seconds(wav_bytes)
    except CompositionWavError:
        raise
    except Exception as exc:
        logger.error(
            "Failed to inspect WAV duration",
            extra={"error_type": type(exc).__name__, "byte_length": len(wav_bytes)},
        )
        raise CompositionWavError("Rendered WAV metadata could not be read") from exc

    logger.debug(
        "WAV duration check",
        extra={
            "expected_seconds": round(expected_seconds, 6),
            "measured_seconds": round(measured, 6),
            "delta_seconds": round(measured - expected_seconds, 6),
        },
    )

    if measured > expected_seconds * DURATION_MAX_OVERSHOOT_RATIO and expected_seconds > 0:
        logger.error(
            "WAV duration greatly exceeds composition duration",
            extra={
                "expected_seconds": round(expected_seconds, 6),
                "measured_seconds": round(measured, 6),
            },
        )
        raise CompositionWavError("Rendered WAV duration is unexpectedly long")

    shortfall = expected_seconds - measured
    if shortfall <= DURATION_PAD_TOLERANCE_SECONDS:
        return wav_bytes

    logger.warning(
        "Padding short FluidSynth WAV to composition duration",
        extra={
            "expected_seconds": round(expected_seconds, 6),
            "measured_seconds": round(measured, 6),
            "pad_seconds": round(shortfall, 6),
        },
    )
    return _pad_wav_to_duration(wav_bytes, expected_seconds, fallback_sample_rate=sample_rate)


def _build_silent_wav(duration_seconds: float, sample_rate: int, *, channels: int = 2, sample_width: int = 2) -> bytes:
    n_frames = max(1, int(round(max(duration_seconds, 0.0) * sample_rate)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * (n_frames * channels * sample_width))
    wav_bytes = buffer.getvalue()
    if not _is_valid_wav(wav_bytes):
        raise CompositionWavError("Failed to build silent WAV")
    return wav_bytes


def _pad_wav_to_duration(wav_bytes: bytes, target_seconds: float, *, fallback_sample_rate: int) -> bytes:
    source = io.BytesIO(wav_bytes)
    try:
        with wave.open(source, "rb") as reader:
            channels = reader.getnchannels()
            sample_width = reader.getsampwidth()
            sample_rate = reader.getframerate() or fallback_sample_rate
            frames = reader.readframes(reader.getnframes())
            params = (
                channels,
                sample_width,
                sample_rate,
                reader.getcomptype(),
                reader.getcompname(),
            )
    except wave.Error as exc:
        logger.error("Malformed WAV header during padding", extra={"error_type": type(exc).__name__})
        raise CompositionWavError("Rendered WAV header is malformed") from exc

    if params[3] not in {"NONE", "not compressed"}:
        raise CompositionWavError("Only uncompressed PCM WAV can be duration-padded")

    target_frames = max(1, int(round(target_seconds * sample_rate)))
    frame_size = channels * sample_width
    current_frames = len(frames) // frame_size if frame_size else 0
    if current_frames >= target_frames:
        return wav_bytes

    pad_frames = target_frames - current_frames
    padded = frames + (b"\x00" * (pad_frames * frame_size))
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(padded)
    result = out.getvalue()
    logger.debug(
        "WAV padding applied",
        extra={
            "pad_frames": pad_frames,
            "target_frames": target_frames,
            "byte_length": len(result),
        },
    )
    return result


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    if not _is_valid_wav(wav_bytes):
        raise CompositionWavError("Rendered output is not a valid RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            rate = wf.getframerate()
            if rate <= 0:
                raise CompositionWavError("WAV sample rate is invalid")
            return wf.getnframes() / float(rate)
    except wave.Error as exc:
        logger.error("Malformed WAV header", extra={"error_type": type(exc).__name__})
        raise CompositionWavError("Rendered WAV header is malformed") from exc


def _is_valid_wav(wav_bytes: bytes) -> bool:
    return len(wav_bytes) >= 12 and wav_bytes[0:4] == b"RIFF" and wav_bytes[8:12] == b"WAVE"


def _redact_command_paths(command: list[str]) -> list[str]:
    redacted: list[str] = []
    for part in command:
        path = Path(part)
        if path.suffix.lower() in {".mid", ".midi", ".wav", ".sf2", ".sf3"} or "mukit-wav-" in part:
            redacted.append(path.name)
        else:
            redacted.append(part)
    return redacted


def _bound_output(raw: bytes | None) -> str:
    text = (raw or b"").decode("utf-8", errors="replace").strip()
    if len(text) <= MAX_CAPTURED_OUTPUT_CHARS:
        return text
    return text[:MAX_CAPTURED_OUTPUT_CHARS] + "…"


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid int env for WAV renderer; using default", extra={"env": name, "default": default})
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        logger.warning("Invalid float env for WAV renderer; using default", extra={"env": name, "default": default})
        return default
    return max(minimum, min(maximum, value))
