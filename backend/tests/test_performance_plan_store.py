"""Store tests: CAS, cascade, and untouched composition bytes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.db import reset_database_initialization_cache
from app.performance_schemas import PerformancePlanError
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.performance_plan_store import (
    create_plan,
    get_plan,
    is_stale_fingerprint,
    list_plans,
    replace_plan,
)
from app.services.performance_presets import clone_preset
from app.services.project_store import create_project, delete_project

_V2 = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.delenv("COLLABORATION_ENABLED", raising=False)
    reset_database_initialization_cache()
    return db_path


def _composition() -> CompositionV2:
    return CompositionV2.model_validate(json.loads(_V2.read_text(encoding="utf-8")))


def _composition_text(db_path: Path, project_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT composition_json FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_store_preserves_composition_and_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _db(tmp_path, monkeypatch)
    composition = _composition()
    project = create_project("Perf", composition=composition, db_path=db_path)
    before = _composition_text(db_path, project.id)
    fp = composition_snapshot_fingerprint(composition)
    plan = clone_preset("intimate", source_composition_fingerprint=fp)
    record = create_plan(project.id, plan, db_path=db_path)
    assert record.id.startswith("pplan_")
    assert record.document_revision == 1
    assert _composition_text(db_path, project.id) == before
    assert list_plans(project.id, db_path=db_path)

    stale = record.plan.model_copy(update={"name": "Intimate revised"})
    with pytest.raises(PerformancePlanError) as captured:
        replace_plan(
            project.id,
            record.id,
            stale,
            expected_document_revision=9,
            db_path=db_path,
        )
    assert captured.value.code == "performance_plan_conflict"
    replaced = replace_plan(
        project.id,
        record.id,
        stale,
        expected_document_revision=1,
        db_path=db_path,
    )
    assert replaced.document_revision == 2
    assert replaced.plan.name == "Intimate revised"
    assert _composition_text(db_path, project.id) == before


def test_project_delete_cascades_plans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _db(tmp_path, monkeypatch)
    composition = _composition()
    project = create_project("Perf", composition=composition, db_path=db_path)
    fp = composition_snapshot_fingerprint(composition)
    create_plan(
        project.id,
        clone_preset("dramatic", source_composition_fingerprint=fp),
        db_path=db_path,
    )
    delete_project(project.id, db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM performance_plans").fetchone()[0]
    assert count == 0


def test_soft_stale_helper() -> None:
    assert is_stale_fingerprint("aaa", "bbb") is True
    assert is_stale_fingerprint("aaa", "aaa") is False


def test_get_missing_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _db(tmp_path, monkeypatch)
    composition = _composition()
    project = create_project("Perf", composition=composition, db_path=db_path)
    with pytest.raises(PerformancePlanError) as captured:
        get_plan(project.id, "pplan_0123456789abcdef", db_path=db_path)
    assert captured.value.code == "performance_plan_not_found"
