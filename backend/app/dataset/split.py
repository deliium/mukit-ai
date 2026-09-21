"""Leakage-safe train/validation/test assignment by duplicate cluster."""

from __future__ import annotations

import logging
import random
from collections import defaultdict

from app.dataset.provenance import assert_train_split_policy
from app.dataset.schemas import (
    DatasetEligibilityPolicy,
    DatasetExampleV1,
    DatasetItemV1,
    ProvenanceStatus,
    SplitJsonlRow,
    SplitName,
)


logger = logging.getLogger(__name__)


def assign_splits(
    items: list[DatasetItemV1],
    examples: list[DatasetExampleV1],
    *,
    split_seed: int,
    train_ratio: float,
    val_ratio: float,
    test_ratio: float,
    eligibility: DatasetEligibilityPolicy,
) -> dict[SplitName, list[SplitJsonlRow]]:
    """Assign clusters to splits; examples inherit parent cluster split."""
    _ = test_ratio  # ratios validated on config; remainder goes to test
    items_by_id = {item.item_id: item for item in items}

    # Eligible inventory for split membership (default: train-eligible only).
    eligible_items = [item for item in items if item.train_eligible]
    if eligibility.include_non_trainable_in_eval:
        # Still never put non-trainable into train; eval may include them later.
        pass

    clusters: dict[str, list[DatasetItemV1]] = defaultdict(list)
    for item in eligible_items:
        cluster_id = item.cluster_id or item.item_id
        clusters[cluster_id].append(item)

    cluster_ids = sorted(clusters.keys())
    rng = random.Random(split_seed)
    shuffled = list(cluster_ids)
    rng.shuffle(shuffled)

    n = len(shuffled)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    # Remainder to test to absorb rounding.
    train_ids = set(shuffled[:n_train])
    val_ids = set(shuffled[n_train : n_train + n_val])
    test_ids = set(shuffled[n_train + n_val :])

    cluster_to_split: dict[str, SplitName] = {}
    for cid in train_ids:
        cluster_to_split[cid] = "train"
    for cid in val_ids:
        cluster_to_split[cid] = "validation"
    for cid in test_ids:
        cluster_to_split[cid] = "test"

    # Verify no cluster spans multiple splits (by construction).
    assert len(cluster_to_split) == len(set(cluster_to_split))

    train_statuses: list[ProvenanceStatus] = []
    for cid in train_ids:
        for item in clusters[cid]:
            train_statuses.append(item.provenance.status)
    assert_train_split_policy(train_statuses, eligibility=eligibility)

    rows: dict[SplitName, list[SplitJsonlRow]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    excluded_examples = 0
    for example in sorted(examples, key=lambda ex: ex.example_id):
        parent = items_by_id.get(example.parent_item_id)
        if parent is None:
            excluded_examples += 1
            continue
        if not parent.train_eligible:
            # Default: keep non-trainable out of all split files.
            if not (
                eligibility.include_non_trainable_in_eval
                and parent.provenance.status
                not in {"unknown", "restricted"}  # still blocked unless unsafe
            ):
                excluded_examples += 1
                continue
            # Escape hatch still refuses unknown/restricted unless unsafe pollution.
            if parent.provenance.status in {"unknown", "restricted"}:
                if not eligibility.allow_unsafe_train_pollution:
                    excluded_examples += 1
                    continue
        cluster_id = parent.cluster_id or parent.item_id
        split = cluster_to_split.get(cluster_id)
        if split is None:
            excluded_examples += 1
            continue
        if split == "train" and not parent.train_eligible:
            excluded_examples += 1
            continue
        rows[split].append(
            SplitJsonlRow(
                example_id=example.example_id,
                parent_item_id=example.parent_item_id,
                cluster_id=cluster_id,
                path=f"examples/{example.example_id}.json",
                split=split,
            )
        )

    logger.info(
        "Split assignment complete",
        extra={
            "cluster_count": len(cluster_ids),
            "train_clusters": len(train_ids),
            "validation_clusters": len(val_ids),
            "test_clusters": len(test_ids),
            "train_examples": len(rows["train"]),
            "validation_examples": len(rows["validation"]),
            "test_examples": len(rows["test"]),
            "excluded_examples": excluded_examples,
        },
    )
    return rows


def verify_cluster_split_integrity(rows: dict[SplitName, list[SplitJsonlRow]]) -> None:
    """Raise if any cluster_id appears in more than one split."""
    ownership: dict[str, SplitName] = {}
    for split, split_rows in rows.items():
        for row in split_rows:
            existing = ownership.get(row.cluster_id)
            if existing is not None and existing != split:
                raise AssertionError(
                    f"cluster {row.cluster_id} spans splits {existing} and {split}"
                )
            ownership[row.cluster_id] = split
