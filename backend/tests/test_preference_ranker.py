"""Linear pairwise ranking on Sparse, Middle, and Dense rhythms."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.preference_schemas import (
    PreferenceCandidateFeaturesV1,
    PreferenceChoiceV1,
    PreferenceContextV1,
    PreferenceLearningError,
    reject_preference_forbidden_payload,
)
from app.services.preference_features import project_preference_features
from app.services.preference_ranker import LinearPairwiseRanker, zero_ranker

_FEATURES = Path(__file__).resolve().parents[1] / "app" / "services" / "preference_features.py"


def _score(notes: list[tuple[str, int, int]]) -> CompositionV2:
    return CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 1920,
            "bar_count": 1,
            "sections": [
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "acoustic_grand_piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": pitch,
                            "start_tick": start,
                            "duration_ticks": duration,
                            "velocity": 80,
                        }
                        for pitch, start, duration in notes
                    ],
                }
            ],
            "harmony": [],
        }
    )


def _transpose(pitch: str, semitones: int) -> str:
    from app.composition_schemas import midi_pitch_number

    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    midi = midi_pitch_number(pitch) + semitones
    return f"{names[midi % 12]}{(midi // 12) - 1}"


_SPARSE = [("C4", 0, 1920)]
_MIDDLE = [("C4", 0, 480), ("E4", 480, 480), ("G4", 960, 480), ("C5", 1440, 480)]
_DENSE = [
    ("C4", 0, 240),
    ("D4", 240, 240),
    ("E4", 480, 240),
    ("F4", 720, 240),
    ("G4", 960, 240),
    ("A4", 1200, 240),
    ("B4", 1440, 240),
    ("C5", 1680, 240),
]


def _shift(notes: list[tuple[str, int, int]], semitones: int) -> list[tuple[str, int, int]]:
    return [(_transpose(pitch, semitones), start, duration) for pitch, start, duration in notes]


def _row(candidate_id: str, composition: CompositionV2, index: int, *, chosen: bool = False):
    vector = project_preference_features(composition, candidate_id=candidate_id)
    payload = {
        "candidate_id": candidate_id,
        "candidate_fingerprint": "f" * 16,
        "original_index": index,
        "feature_vector": vector,
    }
    if chosen:
        return payload | {"chosen": chosen}
    return payload


def _choice(chosen_id: str, rows: list[dict]) -> PreferenceChoiceV1:
    return PreferenceChoiceV1(
        id="pref_0123456789abcdef",
        context=PreferenceContextV1(
            surface="development",
            operation="continue",
            source_fingerprint="s" * 16,
            request_digest="a" * 64,
        ),
        candidates=rows,
        chosen_candidate_id=chosen_id,
        created_at="2026-10-02T12:00:00Z",
    )


def test_density_and_interval_indexes_stay_in_unit_interval() -> None:
    sparse = project_preference_features(_score(_SPARSE), candidate_id="cand_sparse_00001")
    middle = project_preference_features(_score(_MIDDLE), candidate_id="cand_middle_00001")
    dense = project_preference_features(_score(_DENSE), candidate_id="cand_dense_000001")
    assert sparse[3] < middle[3] < dense[3]
    assert sparse[9] == pytest.approx(0.0)
    assert middle[9] == pytest.approx(4 / 12)
    assert dense[9] == pytest.approx((2 + 2 + 1 + 2 + 2 + 2 + 1) / 7 / 12)
    assert dense[9] <= 1.0
    assert max(sparse + middle + dense) <= 1.0
    assert min(sparse + middle + dense) >= 0.0


def test_identical_scores_project_identical_vectors() -> None:
    left = _score(_DENSE)
    right = _score(_DENSE)
    assert project_preference_features(left, candidate_id="cand_dense_000001") == (
        project_preference_features(right, candidate_id="cand_dense_000001")
    )


def test_dense_choice_ranks_transposed_rhythms_then_sparse_choice_flips() -> None:
    ranker = LinearPairwiseRanker()
    cold_items = [
        PreferenceCandidateFeaturesV1.model_validate(_row("cand_sparse_00001", _score(_SPARSE), 0)),
        PreferenceCandidateFeaturesV1.model_validate(_row("cand_middle_00001", _score(_MIDDLE), 1)),
        PreferenceCandidateFeaturesV1.model_validate(_row("cand_dense_000001", _score(_DENSE), 2)),
    ]
    cold = ranker.rank(cold_items, zero_ranker())
    assert cold.ranking_applied is False
    assert cold.ordered_candidate_ids == [
        "cand_sparse_00001",
        "cand_middle_00001",
        "cand_dense_000001",
    ]
    assert cold.scores == [0.0, 0.0, 0.0]
    assert ranker.rank(cold_items, None).ranking_applied is False

    dense_choice = _choice(
        "cand_dense_000001",
        [
            _row("cand_sparse_00001", _score(_SPARSE), 0, chosen=False) | {"chosen": False},
            _row("cand_middle_00001", _score(_MIDDLE), 1, chosen=False) | {"chosen": False},
            _row("cand_dense_000001", _score(_DENSE), 2, chosen=True),
        ],
    )
    fitted = ranker.update(zero_ranker(), dense_choice)
    assert fitted.pair_count == 2

    transposed = [
        PreferenceCandidateFeaturesV1.model_validate(
            _row("cand_sparse_00001", _score(_shift(_SPARSE, 5)), 0)
        ),
        PreferenceCandidateFeaturesV1.model_validate(
            _row("cand_middle_00001", _score(_shift(_MIDDLE, 5)), 1)
        ),
        PreferenceCandidateFeaturesV1.model_validate(
            _row("cand_dense_000001", _score(_shift(_DENSE, 5)), 2)
        ),
    ]
    ranked = ranker.rank(transposed, fitted)
    assert ranked.ranking_applied is True
    assert ranked.ordered_candidate_ids == [
        "cand_dense_000001",
        "cand_middle_00001",
        "cand_sparse_00001",
    ]
    assert transposed[0].feature_vector[0] == pytest.approx(
        project_preference_features(_score(_shift(_SPARSE, 5)), candidate_id="cand_sparse_00001")[0]
    )
    assert _transpose("C4", 5) == "F4"

    sparse_choice = _choice(
        "cand_sparse_00001",
        [
            _row("cand_sparse_00001", _score(_SPARSE), 0, chosen=True),
            _row("cand_middle_00001", _score(_MIDDLE), 1, chosen=False) | {"chosen": False},
            _row("cand_dense_000001", _score(_DENSE), 2, chosen=False) | {"chosen": False},
        ],
    )
    flipped = ranker.update(fitted, sparse_choice)
    third = ranker.rank(cold_items, flipped)
    sparse_at = third.ordered_candidate_ids.index("cand_sparse_00001")
    dense_at = third.ordered_candidate_ids.index("cand_dense_000001")
    assert sparse_at < dense_at


def test_genre_payload_is_forbidden() -> None:
    with pytest.raises(PreferenceLearningError) as exc:
        reject_preference_forbidden_payload({"genre": "waltz"}, model_name="PreferenceChoiceV1")
    assert exc.value.code == "preference_forbidden_payload"


def test_feature_module_does_not_import_analysis() -> None:
    source = _FEATURES.read_text(encoding="utf-8")
    assert "analyze_composition" not in source
    assert "composition_analysis" not in source
