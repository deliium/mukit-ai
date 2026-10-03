"""Thin re-export of composition identity digests for spatial immutability asserts.

Canonical pitch/harmony/event ids only — same algorithm as the performance conductor.
"""

from __future__ import annotations

from app.services.performance_identity import (  # noqa: F401
    compare_metric_summaries,
    identity_digest,
    metrics_digest,
)

__all__ = ["identity_digest", "metrics_digest", "compare_metric_summaries"]
