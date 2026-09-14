"""Tests for shared ChatOpenAI client construction and streaming invoke."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm_chat_client import (
    ainvoke_chat_text,
    build_chat_openai,
    _is_retryable_transport_error,
)


def test_build_chat_openai_disables_sdk_transport_retries():
    fake_client = MagicMock(name="ChatOpenAI")
    with patch("langchain_openai.ChatOpenAI", return_value=fake_client) as ctor:
        client = build_chat_openai(
            api_key="sk-test",
            model="deepseek-chat",
            temperature=0.7,
            timeout_seconds=180,
            base_url="https://api.deepseek.com",
            purpose="music_generation",
        )

    assert client is fake_client
    kwargs = ctor.call_args.kwargs
    assert kwargs["timeout"] == 180
    assert kwargs["max_retries"] == 0
    assert kwargs["http_socket_options"] == ()
    assert kwargs["streaming"] is True
    assert kwargs["stream_chunk_timeout"] == 180.0
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["model"] == "deepseek-chat"
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs["temperature"] == 0.7


def test_build_chat_openai_omits_base_url_when_unset():
    fake_client = MagicMock(name="ChatOpenAI")
    with patch("langchain_openai.ChatOpenAI", return_value=fake_client) as ctor:
        build_chat_openai(
            api_key="sk-test",
            model="gpt-4o-mini",
            temperature=0.2,
            timeout_seconds=90,
        )

    assert "base_url" not in ctor.call_args.kwargs
    assert ctor.call_args.kwargs["max_retries"] == 0
    assert ctor.call_args.kwargs["timeout"] == 90
    assert ctor.call_args.kwargs["http_socket_options"] == ()


def test_build_chat_openai_disables_langchain_tcp_keepalive_profile():
    """Non-streaming DeepSeek bodies are idle after headers; keepidle=60 drops them."""
    fake_client = MagicMock(name="ChatOpenAI")
    with patch("langchain_openai.ChatOpenAI", return_value=fake_client) as ctor:
        build_chat_openai(
            api_key="sk-test",
            model="deepseek-chat",
            temperature=0.7,
            timeout_seconds=180,
        )

    assert ctor.call_args.kwargs["http_socket_options"] == ()


def test_ainvoke_chat_text_concatenates_stream_chunks():
    async def _gen(_prompt):
        yield SimpleNamespace(content="{\"a\":")
        yield SimpleNamespace(content=" 1}")

    client = MagicMock()
    client.astream = _gen
    output = asyncio.run(ainvoke_chat_text(client, "prompt", purpose="unit"))
    assert output == '{"a": 1}'


def test_ainvoke_chat_text_retries_incomplete_chunked_read():
    calls = {"n": 0}

    async def _gen(_prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("peer closed connection without sending complete message body (incomplete chunked read)")
        yield SimpleNamespace(content="ok")

    client = MagicMock()
    client.astream = _gen
    output = asyncio.run(ainvoke_chat_text(client, "prompt", purpose="unit"))
    assert output == "ok"
    assert calls["n"] == 2


def test_ainvoke_chat_text_does_not_retry_validation_style_errors():
    async def _gen(_prompt):
        raise ValueError("invalid json shape")
        yield  # pragma: no cover

    client = MagicMock()
    client.astream = _gen
    with pytest.raises(ValueError, match="invalid json shape"):
        asyncio.run(ainvoke_chat_text(client, "prompt", purpose="unit"))


def test_is_retryable_transport_error_matches_deepseek_drop():
    assert _is_retryable_transport_error(ConnectionError("incomplete chunked read"))
    assert not _is_retryable_transport_error(ValueError("bad schema"))
