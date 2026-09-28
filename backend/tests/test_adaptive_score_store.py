"""Store tests: CAS, default swap, cascade, and untouched composition bytes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.adaptive_score_schemas import AdaptiveScoreError, parse_adaptive_score
from app.db import reset_database_initialization_cache
from app.services.adaptive_score_store import create_score, get_score, replace_score
from app.services.project_store import create_project, delete_project
from tests.test_adaptive_score_schema import adventure_score
from tests.test_composition_schema import valid_composition


def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    return db_path


def _composition_text(db_path: Path, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_store_preserves_composition_and_assigns_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _db(tmp_path, monkeypatch)
    project = create_project("Cue", composition=valid_composition(), db_path=db_path)
    before = _composition_text(db_path, project.id)
    score = parse_adaptive_score(adventure_score())
    record = create_score(project.id, score, is_default=True, db_path=db_path)
    assert record.id.startswith("ascore_")
    assert len(record.id) == len("ascore_") + 16
    assert record.score.id == record.id
    assert record.document_revision == 1
    assert _composition_text(db_path, project.id) == before
    with sqlite3.connect(db_path) as conn:
        body = conn.execute(
            "SELECT body_json FROM adaptive_scores WHERE id = ?",
            (record.id,),
        ).fetchone()[0]
    stored = json.loads(body)
    assert "events" not in json.dumps(stored)
    assert "expected_document_revision" not in stored
    assert stored["id"] == record.id


def test_cas_conflict_and_default_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _db(tmp_path, monkeypatch)
    project = create_project("Cue", composition=valid_composition(), db_path=db_path)
    first = create_score(
        project.id,
        parse_adaptive_score(adventure_score()),
        is_default=True,
        db_path=db_path,
    )
    other = adventure_score()
    other["name"] = "Menu cue"
    second = create_score(
        project.id,
        parse_adaptive_score(other),
        is_default=True,
        db_path=db_path,
    )
    stored_first = get_score(project.id, first.id, db_path=db_path)
    assert stored_first.is_default is False
    assert second.is_default is True
    stale = second.score.model_copy(update={"name": "Menu cue revised"})
    with pytest.raises(AdaptiveScoreError) as captured:
        replace_score(
            project.id,
            second.id,
            stale,
            expected_document_revision=9,
            db_path=db_path,
        )
    assert captured.value.code == "adaptive_score_conflict"
    replaced = replace_score(
        project.id,
        second.id,
        stale,
        expected_document_revision=1,
        db_path=db_path,
    )
    assert replaced.document_revision == 2
    assert replaced.score.name == "Menu cue revised"


def test_delete_project_cascades_adaptive_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _db(tmp_path, monkeypatch)
    project = create_project("Cue", composition=valid_composition(), db_path=db_path)
    record = create_score(project.id, parse_adaptive_score(adventure_score()), db_path=db_path)
    delete_project(project.id, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        count = conn.execute(
            "SELECT COUNT(*) FROM adaptive_scores WHERE id = ?",
            (record.id,),
        ).fetchone()[0]
    assert count == 0
