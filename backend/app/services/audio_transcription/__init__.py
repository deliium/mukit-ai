"""Monophonic audio transcription service package.

Ephemeral audio → transcription.preview.v1. Never mutates composition.v2 and
never persists audio or preview documents.
"""

from __future__ import annotations

from .service import transcribe_audio_bytes

__all__ = ["transcribe_audio_bytes"]
