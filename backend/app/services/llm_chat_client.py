"""Shared LangChain ChatOpenAI construction for provider calls.

App-level repair/retry owns retry budgets. The OpenAI SDK default of
``max_retries=2`` multiplies wall-clock wait on timeouts (e.g. 60s × 3),
so clients are built with ``max_retries=0``.

LangChain OpenAI defaults also inject Linux ``TCP_KEEPIDLE=60`` /
``TCP_USER_TIMEOUT``. Non-streaming completions often stay silent on the
wire after response headers until the full body arrives; keepalive probes
can then drop a healthy slow provider call around 60s as
``APIConnectionError``. Empty ``http_socket_options`` disables that profile
so ``timeout_seconds`` alone bounds the wait.

Prefer ``ainvoke_chat_text`` (SSE streaming) for long JSON stages: DeepSeek
has been observed to close non-streaming chunked bodies around ~120s with
``incomplete chunked read`` even when the app timeout is higher.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Disable OpenAI SDK transport retries; staged/repair loops decide retries.
_PROVIDER_TRANSPORT_MAX_RETRIES = 0
# Disable LangChain's default TCP keepalive / USER_TIMEOUT injection.
_PROVIDER_HTTP_SOCKET_OPTIONS: tuple = ()
# One transport reconnect for incomplete provider bodies (not schema repairs).
_STREAM_TRANSPORT_ATTEMPTS = 2


def build_chat_openai(
    *,
    api_key: str,
    model: str,
    temperature: float,
    timeout_seconds: int,
    base_url: str | None = None,
    purpose: str | None = None,
) -> Any:
    """Return a ChatOpenAI client with project timeout and no SDK retries."""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise ImportError("LangChain OpenAI dependencies are not installed") from exc

    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "temperature": temperature,
        "timeout": timeout_seconds,
        "max_retries": _PROVIDER_TRANSPORT_MAX_RETRIES,
        "http_socket_options": _PROVIDER_HTTP_SOCKET_OPTIONS,
        # Keep SSE streaming available for ainvoke_chat_text; per-call stream flag still applies.
        "streaming": True,
        # Default LangChain value is 120s and aborts slow DeepSeek first-token waits.
        "stream_chunk_timeout": float(timeout_seconds),
    }
    if base_url:
        kwargs["base_url"] = base_url

    client = ChatOpenAI(**kwargs)
    logger.info(
        "[FIX] Built ChatOpenAI client for long-lived provider calls",
        extra={
            "purpose": purpose,
            "model": model,
            "timeout_seconds": timeout_seconds,
            "stream_chunk_timeout": timeout_seconds,
            "max_retries": _PROVIDER_TRANSPORT_MAX_RETRIES,
            "http_socket_options_disabled": True,
            "streaming": True,
            "has_base_url": bool(base_url),
        },
    )
    return client


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", chunk)
    if content is None:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


async def ainvoke_chat_text(client: Any, prompt: str, *, purpose: str | None = None) -> str:
    """Stream a chat completion and concatenate text (keeps idle providers alive)."""
    last_exc: BaseException | None = None
    for attempt in range(1, _STREAM_TRANSPORT_ATTEMPTS + 1):
        started = time.monotonic()
        parts: list[str] = []
        chunk_count = 0
        try:
            async for chunk in client.astream(prompt):
                text = _chunk_text(chunk)
                if text:
                    parts.append(text)
                    chunk_count += 1
            output = "".join(parts)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "[FIX] Streamed LLM chat completion",
                extra={
                    "purpose": purpose,
                    "attempt": attempt,
                    "chunk_count": chunk_count,
                    "response_length": len(output),
                    "elapsed_ms": elapsed_ms,
                },
            )
            if not output.strip():
                raise RuntimeError("LLM streaming completion returned empty content")
            return output
        except Exception as exc:  # noqa: BLE001 — classified for transport retry only
            last_exc = exc
            elapsed_ms = int((time.monotonic() - started) * 1000)
            retryable = _is_retryable_transport_error(exc)
            logger.warning(
                "[FIX] Streamed LLM chat completion failed",
                extra={
                    "purpose": purpose,
                    "attempt": attempt,
                    "retryable": retryable,
                    "chunk_count": chunk_count,
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:200],
                },
            )
            if not retryable or attempt >= _STREAM_TRANSPORT_ATTEMPTS:
                raise
    assert last_exc is not None
    raise last_exc


def _is_retryable_transport_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {
        "APIConnectionError",
        "APITimeoutError",
        "RemoteProtocolError",
        "ReadTimeout",
        "ConnectError",
        "StreamChunkTimeoutError",
        "TimeoutError",
    }:
        return True
    message = str(exc).lower()
    return any(
        needle in message
        for needle in (
            "incomplete chunked read",
            "peer closed connection",
            "connection reset",
            "connection error",
            "timed out",
            "stream_chunk_timeout",
        )
    )
