"""Store gates for preference choices and the linear ranker."""

from __future__ import annotations

import sqlite3

import pytest

from app.db import initialize_database, reset_database_initialization_cache
from app.preference_schemas import (
    PreferenceCandidateFeaturesV1,
    PreferenceContextV1,
    PreferenceLearningError,
    PreferencePendingBallotV1,
)
from app.services import preference_store as store


def _ballot() -> PreferencePendingBallotV1:
    def row(label: str, index: int, density: float) -> PreferenceCandidateFeaturesV1:
        vector = [0.0] * 16
        vector[3] = density
        return PreferenceCandidateFeaturesV1(
            candidate_id=label,
            candidate_fingerprint="f" * 16,
            original_index=index,
            feature_vector=vector,
        )

    return PreferencePendingBallotV1(
        context=PreferenceContextV1(
            surface="development",
            operation="continue",
            source_fingerprint="s" * 16,
            request_digest="a" * 64,
        ),
        candidates=[
            row("cand_sparse_00001", 0, 1 / 32),
            row("cand_middle_00001", 1, 4 / 32),
            row("cand_dense_000001", 2, 8 / 32),
        ],
    )


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(path))
    monkeypatch.delenv("PREFERENCE_LEARNING_ENABLED", raising=False)
    reset_database_initialization_cache()
    initialize_database()
    return path


def test_get_settings_does_not_insert(db_path) -> None:
    settings = store.get_settings(db_path=db_path)
    assert settings.collection_enabled is False
    assert settings.ranking_enabled is False
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM preference_settings").fetchone()[0]
    assert count == 0


def test_flag_off_refuses_true_settings_and_choices(db_path, monkeypatch) -> None:
    monkeypatch.delenv("PREFERENCE_LEARNING_ENABLED", raising=False)
    store.put_settings(collection_enabled=False, ranking_enabled=False, db_path=db_path)
    with pytest.raises(PreferenceLearningError) as exc:
        store.put_settings(collection_enabled=True, ranking_enabled=False, db_path=db_path)
    assert exc.value.code == "preference_learning_disabled"
    assert exc.value.http_status == 409
    stored = store.get_settings(db_path=db_path)
    assert stored.collection_enabled is False
    store.stash_pending(_ballot(), db_path=db_path)
    with pytest.raises(PreferenceLearningError) as recorded:
        store.record_choice(
            surface="development",
            chosen_candidate_id="cand_dense_000001",
            db_path=db_path,
        )
    assert recorded.value.code == "preference_learning_disabled"
    assert store.choice_count(db_path=db_path) == 0
    assert store.get_pending("development", db_path=db_path) is not None


def test_collection_off_refuses_a_choice(db_path, monkeypatch) -> None:
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    store.put_settings(collection_enabled=False, ranking_enabled=True, db_path=db_path)
    store.stash_pending(_ballot(), db_path=db_path)
    with pytest.raises(PreferenceLearningError) as exc:
        store.record_choice(
            surface="development",
            chosen_candidate_id="cand_dense_000001",
            db_path=db_path,
        )
    assert exc.value.code == "preference_collection_disabled"
    assert store.choice_count(db_path=db_path) == 0
    assert store.get_ranker(db_path=db_path) is None


def test_successful_dense_choice_updates_ranker_and_reset_clears_it(db_path, monkeypatch) -> None:
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    store.put_settings(collection_enabled=True, ranking_enabled=True, db_path=db_path)
    store.stash_pending(_ballot(), db_path=db_path)
    choice = store.record_choice(
        surface="development",
        chosen_candidate_id="cand_dense_000001",
        db_path=db_path,
    )
    assert choice.chosen_candidate_id == "cand_dense_000001"
    assert store.choice_count(db_path=db_path) == 1
    assert store.get_pending("development", db_path=db_path) is None
    ranker = store.get_ranker(db_path=db_path)
    assert ranker is not None
    assert ranker.pair_count == 2
    assert ranker.weights[3] > 0
    summaries = store.list_choices(db_path=db_path)
    assert len(summaries) == 1
    assert "feature_vector" not in summaries[0].model_dump()
    assert summaries[0].source_fingerprint_prefix == "s" * 12
    deleted = store.reset_preference_data(db_path=db_path)
    assert deleted == 1
    assert store.choice_count(db_path=db_path) == 0
    assert store.get_ranker(db_path=db_path) is None
    assert store.get_settings(db_path=db_path).collection_enabled is True


def test_choice_limit_writes_nothing(db_path, monkeypatch) -> None:
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    store.put_settings(collection_enabled=True, ranking_enabled=False, db_path=db_path)
    store.stash_pending(_ballot(), db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        for index in range(store.CHOICE_LIMIT):
            conn.execute(
                """
                INSERT INTO preference_choices (id, surface, created_at, body_json)
                VALUES (?, 'development', '2026-10-02T00:00:00Z', '{}')
                """,
                (f"pref_{index:016x}",),
            )
    with pytest.raises(PreferenceLearningError) as exc:
        store.record_choice(
            surface="development",
            chosen_candidate_id="cand_dense_000001",
            db_path=db_path,
        )
    assert exc.value.code == "preference_choice_limit"
    assert store.choice_count(db_path=db_path) == store.CHOICE_LIMIT
    assert store.get_ranker(db_path=db_path) is None
    assert store.get_pending("development", db_path=db_path) is not None


def test_ranker_failure_rolls_back_the_choice(db_path, monkeypatch) -> None:
    monkeypatch.setenv("PREFERENCE_LEARNING_ENABLED", "1")
    store.put_settings(collection_enabled=True, ranking_enabled=True, db_path=db_path)
    store.stash_pending(_ballot(), db_path=db_path)

    def _boom(self, model, choice):
        raise RuntimeError("ranker failed")

    monkeypatch.setattr(store.LinearPairwiseRanker, "update", _boom)
    with pytest.raises(RuntimeError, match="ranker failed"):
        store.record_choice(
            surface="development",
            chosen_candidate_id="cand_dense_000001",
            db_path=db_path,
        )
    assert store.choice_count(db_path=db_path) == 0
    assert store.get_ranker(db_path=db_path) is None
    assert store.get_pending("development", db_path=db_path) is not None
