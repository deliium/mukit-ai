"""Token buckets and a one-slot context holder for one engine session.

Pure relative to the state object the service stores. No threads, FastAPI,
or sample logging.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

_MILLI = 1000

T = TypeVar("T")


@dataclass
class TokenBucket:
    """Integer millitoken bucket. One token is 1000 units."""

    capacity_tokens: int
    hz: int
    tokens_milli: int
    updated_ms: int

    @classmethod
    def full(cls, *, capacity_tokens: int, hz: int, now_ms: int) -> TokenBucket:
        return cls(
            capacity_tokens=capacity_tokens,
            hz=hz,
            tokens_milli=capacity_tokens * _MILLI,
            updated_ms=now_ms,
        )


@dataclass(frozen=True)
class BucketTake:
    allowed: bool
    retry_after_ms: int | None


@dataclass(frozen=True)
class ContextOffer(Generic[T]):
    accepted: bool
    coalesced: bool
    replaced: bool
    retry_after_ms: int | None


@dataclass
class EnginePressure(Generic[T]):
    """Command bucket plus one unread context sample."""

    commands: TokenBucket
    context: TokenBucket
    slot: T | None = None
    dropped_count: int = 0

    @classmethod
    def create(
        cls,
        *,
        command_hz: int,
        command_burst: int,
        context_hz: int,
        context_burst: int,
        now_ms: int,
    ) -> EnginePressure[T]:
        return cls(
            commands=TokenBucket.full(capacity_tokens=command_burst, hz=command_hz, now_ms=now_ms),
            context=TokenBucket.full(capacity_tokens=context_burst, hz=context_hz, now_ms=now_ms),
        )


def _refill(bucket: TokenBucket, now_ms: int) -> None:
    elapsed = now_ms - bucket.updated_ms
    if elapsed < 0:
        bucket.updated_ms = now_ms
        return
    if elapsed == 0:
        return
    capacity = bucket.capacity_tokens * _MILLI
    gained = elapsed * bucket.hz
    bucket.tokens_milli = min(capacity, bucket.tokens_milli + gained)
    bucket.updated_ms = now_ms


def _retry_after(bucket: TokenBucket) -> int:
    missing = _MILLI - bucket.tokens_milli
    if missing <= 0:
        return 0
    if bucket.hz <= 0:
        return 1000
    return max(1, math.ceil(missing / bucket.hz))


def _take(bucket: TokenBucket, now_ms: int) -> BucketTake:
    _refill(bucket, now_ms)
    if bucket.tokens_milli >= _MILLI:
        bucket.tokens_milli -= _MILLI
        return BucketTake(allowed=True, retry_after_ms=None)
    return BucketTake(allowed=False, retry_after_ms=_retry_after(bucket))


def try_command(state: EnginePressure[T], now_ms: int) -> BucketTake:
    """Consume one command token, or report how long until one exists."""
    logger.debug(
        "Adaptive engine command bucket checked",
        extra={"allowed_capacity": state.commands.capacity_tokens},
    )
    return _take(state.commands, now_ms)


def offer_context(state: EnginePressure[T], sample: T, now_ms: int) -> ContextOffer[T]:
    """Accept while tokens remain. Past the burst, replace the single slot."""
    taken = _take(state.context, now_ms)
    if taken.allowed:
        return ContextOffer(accepted=True, coalesced=False, replaced=False, retry_after_ms=None)
    replaced = state.slot is not None
    state.slot = sample
    if replaced:
        state.dropped_count += 1
    return ContextOffer(
        accepted=False,
        coalesced=True,
        replaced=replaced,
        retry_after_ms=taken.retry_after_ms,
    )


def drain_context(state: EnginePressure[T], now_ms: int) -> T | None:
    """Return the slot when a context token is available. Otherwise leave it."""
    if state.slot is None:
        return None
    taken = _take(state.context, now_ms)
    if not taken.allowed:
        return None
    sample = state.slot
    state.slot = None
    logger.debug("Adaptive engine context slot drained")
    return sample
