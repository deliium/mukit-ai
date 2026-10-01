"""Edit lists must explain the stored picture within one frame."""

from __future__ import annotations

import pytest

from app.film_score_adapt_schemas import FilmAdaptError
from app.services.film_score_adapt import expected_timeline, require_explained_timeline
from tests.film_score_adapt_fixture import (
    base_previous,
    cue,
    delete_span,
    insert_span,
    move_hit,
    snapshot,
)


def _by_id(timeline):
    return {item.id: item.video_seconds for item in timeline.cues}


def test_vector_a_delete_moves_only_the_later_cue() -> None:
    previous = base_previous()
    expected = expected_timeline(previous, [delete_span(16, 22)])
    assert expected.duration_seconds == 58
    times = _by_id(expected)
    assert times["hit_aaaa0001"] == 6
    assert times["hit_bbbb0002"] == 30


def test_vector_c_one_second_delete_ripples_the_later_cue() -> None:
    previous = base_previous(duration=64)
    expected = expected_timeline(previous, [delete_span(16, 17)])
    assert expected.duration_seconds == 63
    times = _by_id(expected)
    assert times["hit_aaaa0001"] == 6
    assert times["hit_bbbb0002"] == 35


def test_cue_strictly_inside_a_delete_is_absent() -> None:
    previous = base_previous(
        [cue("hit_aaaa0001", 6), cue("hit_cccc0012", 18), cue("hit_bbbb0002", 36)]
    )
    expected = expected_timeline(previous, [delete_span(16, 22)])
    assert "hit_cccc0012" not in _by_id(expected)
    stored = snapshot(58, [cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 30)])
    require_explained_timeline(previous, [delete_span(16, 22)], stored)


def test_one_extra_frame_on_the_moved_cue_mismatches() -> None:
    previous = base_previous()
    edits = [delete_span(16, 22)]
    stored = snapshot(58, [cue("hit_aaaa0001", 6), cue("hit_bbbb0002", 30 + 1 / 24)])
    with pytest.raises(FilmAdaptError) as caught:
        require_explained_timeline(previous, edits, stored)
    assert caught.value.code == "film_adapt_timeline_mismatch"
    assert caught.value.candidate is None


def test_move_hit_leaves_duration_unchanged() -> None:
    previous = base_previous()
    expected = expected_timeline(previous, [move_hit("hit_bbbb0002", 36, 36 + 1 / 24)])
    assert expected.duration_seconds == 64
    assert _by_id(expected)["hit_bbbb0002"] == 36 + 1 / 24
    assert _by_id(expected)["hit_aaaa0001"] == 6


def test_insert_grows_duration_and_later_cues() -> None:
    previous = base_previous()
    expected = expected_timeline(previous, [insert_span(16, 4)])
    assert expected.duration_seconds == 68
    assert _by_id(expected)["hit_bbbb0002"] == 40
    assert _by_id(expected)["hit_aaaa0001"] == 6
