"""Shared audio content-signature helpers for mono transcription and recovery.

Filenames are not authoritative — callers must sniff payload bytes.
Runtime verbosity remains controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging


logger = logging.getLogger(__name__)

# Prefer client-side WAV PCM encoding to avoid server ffmpeg dependency.
AUDIO_WAV_EXTENSIONS = frozenset({".wav"})
AUDIO_FLAC_EXTENSIONS = frozenset({".flac"})
AUDIO_OGG_EXTENSIONS = frozenset({".ogg", ".oga"})
AUDIO_MP3_EXTENSIONS = frozenset({".mp3"})
AUDIO_EXTENSIONS = (
    AUDIO_WAV_EXTENSIONS
    | AUDIO_FLAC_EXTENSIONS
    | AUDIO_OGG_EXTENSIONS
    | AUDIO_MP3_EXTENSIONS
)

# RIFF/WAVE: "RIFF...." + "WAVE" at offset 8
WAV_RIFF_SIGNATURE = b"RIFF"
WAV_WAVE_MARKER = b"WAVE"
FLAC_SIGNATURE = b"fLaC"
OGG_SIGNATURE = b"OggS"
# MPEG frame sync / ID3 tag — sniff only; decode may still fail.
MP3_ID3_SIGNATURE = b"ID3"
MP3_FRAME_SYNC_PREFIXES = (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2")


def sniff_audio_format(payload: bytes) -> str | None:
    """Return a format key (wav|flac|ogg|mp3) from content bytes, or None."""
    if len(payload) < 12:
        logger.debug(
            "Audio sniff skipped; payload too short",
            extra={"byte_count": len(payload)},
        )
        return None
    if payload.startswith(WAV_RIFF_SIGNATURE) and payload[8:12] == WAV_WAVE_MARKER:
        return "wav"
    if payload.startswith(FLAC_SIGNATURE):
        return "flac"
    if payload.startswith(OGG_SIGNATURE):
        return "ogg"
    if payload.startswith(MP3_ID3_SIGNATURE):
        return "mp3"
    if any(payload.startswith(prefix) for prefix in MP3_FRAME_SYNC_PREFIXES):
        return "mp3"
    logger.debug("Audio sniff unmatched signature", extra={"byte_count": len(payload)})
    return None
