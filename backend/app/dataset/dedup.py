"""Exact / near-duplicate clustering for leakage-resistant splits."""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import Any

from app.composition_schemas import CompositionV2, midi_pitch_number
from app.dataset.schemas import DatasetItemV1, DatasetNearDupConfig


logger = logging.getLogger(__name__)


def cluster_items(
    items: list[DatasetItemV1],
    *,
    compositions: dict[str, CompositionV2],
    near_dup: DatasetNearDupConfig,
) -> list[DatasetItemV1]:
    """Assign cluster_id; mark exact/near duplicates; keep one canonical per cluster."""
    by_exact: dict[str, list[DatasetItemV1]] = defaultdict(list)
    for item in items:
        if item.source_bytes_hash:
            key = f"src:{item.source_bytes_hash}|v2:{item.content_hash or ''}"
        else:
            key = f"v2:{item.content_hash or item.item_id}"
        by_exact[key].append(item)

    clustered: list[DatasetItemV1] = []
    exact_dup_count = 0
    for _key, group in sorted(by_exact.items(), key=lambda pair: pair[0]):
        group_sorted = sorted(group, key=lambda item: item.item_id)
        canonical = group_sorted[0]
        cluster_id = _cluster_id("exact", canonical.item_id)
        for index, item in enumerate(group_sorted):
            if index == 0:
                clustered.append(
                    item.model_copy(
                        update={
                            "cluster_id": cluster_id,
                            "is_canonical": True,
                            "exact_duplicate_of": None,
                            "near_duplicate_of": None,
                        }
                    )
                )
            else:
                exact_dup_count += 1
                clustered.append(
                    item.model_copy(
                        update={
                            "cluster_id": cluster_id,
                            "is_canonical": False,
                            "exact_duplicate_of": canonical.item_id,
                            "near_duplicate_of": None,
                        }
                    )
                )

    if near_dup.enabled:
        clustered = _apply_near_dup(
            clustered,
            compositions=compositions,
            near_dup=near_dup,
        )

    cluster_ids = {item.cluster_id for item in clustered if item.cluster_id}
    near_count = sum(1 for item in clustered if item.near_duplicate_of)
    logger.info(
        "Dedup clustering complete",
        extra={
            "item_count": len(clustered),
            "cluster_count": len(cluster_ids),
            "exact_duplicate_count": exact_dup_count,
            "near_duplicate_count": near_count,
        },
    )
    for item in clustered[:5]:
        if item.cluster_id:
            logger.debug(
                "Cluster sample",
                extra={
                    "cluster_id_prefix": item.cluster_id[:12],
                    "item_id": item.item_id,
                    "is_canonical": item.is_canonical,
                },
            )
    return clustered


def _apply_near_dup(
    items: list[DatasetItemV1],
    *,
    compositions: dict[str, CompositionV2],
    near_dup: DatasetNearDupConfig,
) -> list[DatasetItemV1]:
    canonicals = [item for item in items if item.is_canonical]
    buckets: dict[tuple[Any, ...], list[DatasetItemV1]] = defaultdict(list)
    for item in canonicals:
        doc = compositions.get(item.item_id)
        if doc is None:
            continue
        buckets[_near_bucket_key(doc)].append(item)

    remaps: dict[str, str] = {}
    near_links: dict[str, str] = {}

    for _bucket, group in buckets.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda item: item.item_id)
        sequences = {
            item.item_id: _onset_pitch_sequence(
                compositions[item.item_id],
                grid=near_dup.onset_grid_ticks,
            )
            for item in group_sorted
            if item.item_id in compositions
        }
        parent_of: dict[str, str] = {item.item_id: item.item_id for item in group_sorted}

        def find(x: str) -> str:
            while parent_of[x] != x:
                parent_of[x] = parent_of[parent_of[x]]
                x = parent_of[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if ra < rb:
                parent_of[rb] = ra
            else:
                parent_of[ra] = rb

        ids = list(sequences.keys())
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                score = _jaccard(sequences[left], sequences[right])
                if score >= near_dup.sequence_jaccard_threshold:
                    union(left, right)

        roots: dict[str, list[str]] = defaultdict(list)
        for item_id in ids:
            roots[find(item_id)].append(item_id)
        for _root, members in roots.items():
            if len(members) < 2:
                continue
            members_sorted = sorted(members)
            canonical_id = members_sorted[0]
            canonical_item = next(i for i in group_sorted if i.item_id == canonical_id)
            cluster_id = canonical_item.cluster_id or _cluster_id("near", canonical_id)
            for member_id in members_sorted:
                remaps[member_id] = cluster_id
                if member_id != canonical_id:
                    near_links[member_id] = canonical_id

    updated: list[DatasetItemV1] = []
    for item in items:
        cluster_id = remaps.get(item.item_id, item.cluster_id)
        if item.exact_duplicate_of and item.exact_duplicate_of in remaps:
            cluster_id = remaps[item.exact_duplicate_of]
        near_of = near_links.get(item.item_id, item.near_duplicate_of)
        is_canonical = item.is_canonical and near_of is None
        if near_of is not None:
            is_canonical = False
        updated.append(
            item.model_copy(
                update={
                    "cluster_id": cluster_id,
                    "near_duplicate_of": near_of,
                    "is_canonical": is_canonical,
                }
            )
        )
    return updated


def _near_bucket_key(doc: CompositionV2) -> tuple[Any, ...]:
    note_count = sum(len(track.events) for track in doc.tracks)
    hist = [0] * 12
    for track in doc.tracks:
        for event in track.events:
            hist[midi_pitch_number(event.pitch) % 12] += 1
    coarse = tuple(count // 4 for count in hist)
    return (doc.bar_count, note_count, doc.time_signature, coarse)


def _onset_pitch_sequence(doc: CompositionV2, *, grid: int) -> set[tuple[int, int]]:
    seq: set[tuple[int, int]] = set()
    for track in doc.tracks:
        for event in track.events:
            onset = (event.start_tick // grid) * grid
            seq.add((onset, midi_pitch_number(event.pitch)))
    return seq


def _jaccard(left: set[tuple[int, int]], right: set[tuple[int, int]]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    inter = len(left & right)
    union = len(left | right)
    return inter / union if union else 0.0


def _cluster_id(kind: str, seed: str) -> str:
    return hashlib.sha256(f"{kind}|{seed}".encode("utf-8")).hexdigest()[:20]
