"""Canonical pitch/harmony identity digests and compare-metrics helpers.

Hash canonical Composition fields only — never performed tick deltas.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.composition_schemas import CompositionV2
from app.performance_schemas import RealizationMetricsV1

logger = logging.getLogger(__name__)


def _harmony_span_fingerprint(composition: CompositionV2) -> str:
    items = []
    for item in composition.harmony or []:
        if hasattr(item, "start_tick"):
            items.append(
                {
                    "start_tick": int(item.start_tick),
                    "duration_ticks": int(item.duration_ticks),
                    "chord": item.chord,
                }
            )
        else:
            items.append({"bar": int(getattr(item, "bar", 0)), "chord": item.chord})
    items.sort(key=lambda row: tuple(sorted(row.items())))
    encoded = json.dumps(items, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def identity_digest(composition: CompositionV2) -> str:
    """Digest of sorted (event_id, pitch, canonical start_tick, harmony fingerprint).

    Uses canonical Composition start ticks — not performed offsets.
    """
    rows: list[tuple[str, str, int]] = []
    for track in composition.tracks:
        for event in track.events:
            rows.append((event.id, event.pitch, int(event.start_tick)))
    rows.sort(key=lambda item: (item[0], item[1], item[2]))
    harmony_fp = _harmony_span_fingerprint(composition)
    payload = {
        "events": [
            {"event_id": eid, "pitch": pitch, "start_tick": start}
            for eid, pitch, start in rows
        ],
        "key": composition.key,
        "harmony_span_fingerprint": harmony_fp,
    }
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    logger.debug(
        "Computed performance identity digest",
        extra={"event_count": len(rows), "digest_prefix": digest[:12]},
    )
    return digest


def metrics_digest(metrics: RealizationMetricsV1 | dict[str, Any]) -> str:
    """Stable digest of summary metrics for distinguishability asserts."""
    if isinstance(metrics, RealizationMetricsV1):
        data = metrics.model_dump(mode="json")
    else:
        data = dict(metrics)
    # Round floats for stable cross-run digests.
    for key in (
        "mean_abs_tick_delta",
        "mean_abs_velocity_delta",
        "mean_abs_duration_delta",
    ):
        if key in data:
            data[key] = round(float(data[key]), 6)
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def compare_metric_summaries(
    mechanical: RealizationMetricsV1,
    performed: RealizationMetricsV1,
) -> dict[str, float]:
    return {
        "delta_mean_abs_tick": abs(
            performed.mean_abs_tick_delta - mechanical.mean_abs_tick_delta
        ),
        "delta_mean_abs_velocity": abs(
            performed.mean_abs_velocity_delta - mechanical.mean_abs_velocity_delta
        ),
        "delta_sustain_spans": float(
            abs(performed.sustain_span_count - mechanical.sustain_span_count)
        ),
    }
