"""Locked tempo vectors for the film-score compiler."""

from __future__ import annotations

import math

import pytest

from app.film_score_schemas import FilmCueSnapshot, FilmScoreError
from app.services.film_score_tempo import compile_film_score_plan


def _cue(hit_id: str, kind: str, seconds: float, *, importance: str = "critical", tolerance: int = 0) -> FilmCueSnapshot:
    return FilmCueSnapshot(
        id=hit_id,
        kind=kind,  # type: ignore[arg-type]
        importance=importance,  # type: ignore[arg-type]
        video_seconds=seconds,
        tolerance_frames=tolerance,
    )


def _compile(cues: list[FilmCueSnapshot], *, window: float, opening: int = 120):
    return compile_film_score_plan(
        cues,
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        opening_tempo=opening,
        tempo_min=96,
        tempo_max=132,
        time_signature="4/4",
        key="C major",
        target_duration_seconds=window,
        project_id="proj_film",
        source_fingerprint="source-fingerprint",
        scoring_document_revision=2,
        ticks_per_quarter=480,
    )


def _frame(seconds: float) -> int:
    return math.floor(seconds * 24 + 1e-9)


def test_vector_a_keeps_one_tempo_across_barline_hits() -> None:
    plan = _compile(
        [
            _cue("hit_0000000a", "hit_point", 10),
            _cue("hit_0000000b", "hit_point", 40),
            _cue("hit_0000000c", "hit_point", 60),
        ],
        window=180,
    )
    assert sum(section.bar_count for section in plan.sections) == 90
    assert plan.root_tempo == 120
    assert plan.tempo_strategy.changes == []
    by_id = {row.cue_id: row for row in plan.hit_alignments}
    for cue_id in ("hit_0000000a", "hit_0000000b", "hit_0000000c"):
        assert by_id[cue_id].status == "aligned"
        assert by_id[cue_id].tempo_change_added is False
    assert plan.sync_origin.musical_origin_tick == 0
    assert plan.sync_origin.video_origin_seconds == plan.music_start_seconds


def test_vector_b_changes_tempo_at_section_start() -> None:
    plan = _compile(
        [
            _cue("hit_0000000a", "hit_point", 10),
            _cue("hit_0000000b", "hit_point", 17.875),
        ],
        window=32,
    )
    assert len(plan.tempo_strategy.changes) == 1
    change = plan.tempo_strategy.changes[0]
    assert change.tick == 15360
    assert change.bpm == 128
    assert change.reason_code == "phrase_fit"
    assert plan.sections[0].tempo_bpm == 120
    assert plan.sections[0].start_bar == 1
    assert plan.sections[1].start_bar == 9
    hit = next(row for row in plan.hit_alignments if row.cue_id == "hit_0000000b")
    assert hit.status == "aligned"
    assert hit.tempo_change_added is True
    first = next(row for row in plan.hit_alignments if row.cue_id == "hit_0000000a")
    assert first.tempo_change_added is False
    assert all(item.tick != int(17.875 * 480) for item in plan.tempo_strategy.changes)
    assert _frame(17.875) == math.floor(17.875 * 24 + 1e-9)


def test_vector_c_leaves_one_frame_miss_unsatisfiable() -> None:
    plan = _compile(
        [_cue("hit_0000000c", "hit_point", 16.05)],
        window=32,
    )
    assert plan.tempo_strategy.changes == []
    assert plan.root_tempo == 120
    row = plan.hit_alignments[0]
    assert row.status == "unsatisfiable"
    assert row.tempo_change_added is False
    assert "hit_unsatisfiable" in plan.warnings
    assert _frame(16.05) == _frame(16.0) + 1


def test_vector_d_dialogue_is_sparse_density_without_tempo_change() -> None:
    plan = _compile(
        [
            _cue("hit_0000000a", "hit_point", 10),
            _cue("hit_0000000d", "dialogue", 20, importance="medium"),
            _cue("hit_0000000b", "hit_point", 40),
        ],
        window=180,
    )
    assert plan.tempo_strategy.changes == []
    assert len(plan.density_regions) == 1
    region = plan.density_regions[0]
    assert region.density == "sparse"
    assert region.start_bar == 11
    assert region.end_bar == 11
    dialogue = next(row for row in plan.hit_alignments if row.cue_id == "hit_0000000d")
    assert dialogue.status in {"soft", "aligned"}
    assert dialogue.tempo_change_added is False
    sparse = [section for section in plan.sections if section.density == "sparse"]
    assert sparse
    assert any(section.start_bar <= 11 < section.start_bar + section.bar_count for section in sparse)


def test_music_stop_ends_the_window() -> None:
    plan = _compile(
        [_cue("hit_0000000e", "music_stop", 30, importance="medium")],
        window=180,
    )
    assert plan.music_end_seconds == 30
    assert sum(section.bar_count for section in plan.sections) == 15
    assert plan.hit_alignments[0].status == "boundary"
    assert plan.sync_origin.musical_origin_tick == 0
    assert plan.sync_origin.video_origin_seconds == plan.music_start_seconds


def test_window_shorter_than_one_bar_at_tempo_min() -> None:
    with pytest.raises(FilmScoreError) as caught:
        _compile([], window=2.0, opening=96)
    assert caught.value.code == "film_duration_invalid"
