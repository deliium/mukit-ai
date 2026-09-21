"""Corpus statistics aggregator for ``dataset.stats.v1``."""

from __future__ import annotations

import logging
from collections import Counter

from app.composition_schemas import CompositionV2
from app.dataset.schemas import (
    DatasetExampleV1,
    DatasetItemV1,
    DatasetStatsSection,
    DatasetStatsV1,
)


logger = logging.getLogger(__name__)


def compute_stats(
    *,
    dataset_name: str,
    dataset_version_id: str,
    items: list[DatasetItemV1],
    examples: list[DatasetExampleV1],
    compositions: dict[str, CompositionV2],
    file_count: int,
) -> DatasetStatsV1:
    inventory = _section_for(
        items=items,
        examples=examples,
        compositions=compositions,
        file_count=file_count,
        eligible_only=False,
    )
    eligible_items = [item for item in items if item.train_eligible]
    eligible_ids = {item.item_id for item in eligible_items}
    eligible_examples = [ex for ex in examples if ex.parent_item_id in eligible_ids]
    eligible = _section_for(
        items=eligible_items,
        examples=eligible_examples,
        compositions=compositions,
        file_count=file_count,
        eligible_only=True,
    )
    stats = DatasetStatsV1(
        dataset_name=dataset_name,
        dataset_version_id=dataset_version_id,
        inventory=inventory,
        eligible=eligible,
    )
    logger.info(
        "Dataset stats computed",
        extra={
            "version_id_prefix": dataset_version_id[:12],
            "inventory_items": inventory.item_count,
            "eligible_items": eligible.item_count,
            "inventory_notes": inventory.note_count,
            "duration_hours": round(inventory.duration_hours, 4),
        },
    )
    logger.debug(
        "Stats histogram buckets",
        extra={
            "note_hist_buckets": len(inventory.example_note_length_histogram),
            "tick_hist_buckets": len(inventory.example_tick_length_histogram),
        },
    )
    return stats


def _section_for(
    *,
    items: list[DatasetItemV1],
    examples: list[DatasetExampleV1],
    compositions: dict[str, CompositionV2],
    file_count: int,
    eligible_only: bool,
) -> DatasetStatsSection:
    _ = eligible_only
    program_counts: Counter[str] = Counter()
    key_counts: Counter[str] = Counter()
    meter_counts: Counter[str] = Counter()
    provenance_counts: Counter[str] = Counter()
    note_hist: Counter[str] = Counter()
    tick_hist: Counter[str] = Counter()

    total_bars = 0
    note_count = 0
    duration_ticks_weighted = 0.0
    train_eligible = 0
    excluded = 0
    clusters = {item.cluster_id for item in items if item.cluster_id}
    exact_dups = sum(1 for item in items if item.exact_duplicate_of)
    near_dups = sum(1 for item in items if item.near_duplicate_of)

    for item in items:
        provenance_counts[item.provenance.status] += 1
        if item.train_eligible:
            train_eligible += 1
        else:
            excluded += 1
        doc = compositions.get(item.item_id) or item.composition
        if doc is None:
            continue
        total_bars += doc.bar_count
        notes = sum(len(track.events) for track in doc.tracks)
        note_count += notes
        duration_ticks_weighted += _approx_seconds(doc)
        key_counts[doc.key] += 1
        meter_counts[doc.time_signature] += 1
        for track in doc.tracks:
            program_counts[str(track.midi_program)] += 1

    for example in examples:
        ex_notes = sum(len(track.events) for track in example.composition.tracks)
        ex_ticks = example.end_tick - example.start_tick
        note_hist[_hist_bucket(ex_notes, edges=(8, 16, 32, 64, 128, 256, 512))] += 1
        tick_hist[_hist_bucket(ex_ticks, edges=(480, 960, 1920, 3840, 7680, 15360))] += 1

    return DatasetStatsSection(
        file_count=file_count,
        item_count=len(items),
        example_count=len(examples),
        duration_hours=duration_ticks_weighted / 3600.0,
        total_bars=total_bars,
        note_count=note_count,
        instrument_program_distribution=dict(sorted(program_counts.items())),
        key_distribution=dict(sorted(key_counts.items())),
        time_signature_distribution=dict(sorted(meter_counts.items())),
        example_note_length_histogram=dict(sorted(note_hist.items())),
        example_tick_length_histogram=dict(sorted(tick_hist.items())),
        provenance_status_counts=dict(sorted(provenance_counts.items())),
        train_eligible_count=train_eligible,
        excluded_count=excluded,
        duplicate_cluster_count=len(clusters),
        exact_duplicate_count=exact_dups,
        near_duplicate_count=near_dups,
    )


def _approx_seconds(doc: CompositionV2) -> float:
    """Approximate wall duration from tempo map + duration_ticks."""
    tpq = doc.ticks_per_quarter
    changes = sorted([(0, doc.tempo)] + [(c.tick, c.bpm) for c in doc.tempo_changes])
    total = 0.0
    for index, (tick, bpm) in enumerate(changes):
        next_tick = changes[index + 1][0] if index + 1 < len(changes) else doc.duration_ticks
        span = max(0, next_tick - tick)
        seconds_per_tick = (60.0 / max(1, bpm)) / tpq
        total += span * seconds_per_tick
    return total


def _hist_bucket(value: int, *, edges: tuple[int, ...]) -> str:
    previous = 0
    for edge in edges:
        if value < edge:
            return f"{previous}-{edge - 1}"
        previous = edge
    return f"{previous}+"
