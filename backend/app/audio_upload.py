"""Chunked multipart reads shared by audio ingress routes.

Stops once the configured byte limit is exceeded. Does not import routers.
"""

from __future__ import annotations

import logging
from typing import Protocol

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024


class UploadTooLargeError(Exception):
    """Raised when an upload passes the configured byte limit."""

    def __init__(self, *, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        self.code = "audio_payload_too_large"
        super().__init__(self.code)


class _AsyncByteUpload(Protocol):
    async def read(self, size: int = -1) -> bytes: ...

    async def close(self) -> None: ...


async def read_upload_bounded(upload: _AsyncByteUpload, *, max_bytes: int) -> bytes:
    """Read ``upload`` in at most 64 KiB chunks. Stop when ``total > max_bytes``."""
    logger.debug("read_upload_bounded start", extra={"limit_bytes": max_bytes})
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await upload.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                logger.info(
                    "audio_payload_too_large",
                    extra={"code": "audio_payload_too_large", "limit_bytes": max_bytes},
                )
                raise UploadTooLargeError(limit_bytes=max_bytes)
            chunks.append(chunk)
    finally:
        await upload.close()
    logger.debug("read_upload_bounded done", extra={"byte_size": total})
    return b"".join(chunks)
