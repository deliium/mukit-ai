"""SQLite edges, cycle refusal, and project-delete behavior."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.db.connection import get_connection, reset_database_initialization_cache
from app.db import initialize_database
from app.musical_dependency_schemas import MusicalDependencyError
from app.services import musical_dependency_store as store
from app.services.composition_snapshot_encoding import motif_occurrence_fingerprint
from app.services.project_store import create_project, delete_project

_FINGERPRINT = "ab" * 32
_OTHER_FINGERPRINT = "cd" * 32
_UNIVERSE = "muniv_" + "a1" * 8
_THEME = "theme_bbbb2222"


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _variation(index: int, *, project_id: str, fingerprint: str = _FINGERPRINT) -> dict:
    return {
        "schema_version": "musical.dependency.edge.v1",
        "id": f"dep_{index:016x}",
        "dependency_type": "variation_of",
        "upstream_kind": "theme",
        "downstream_kind": "motif_occurrence",
        "universe_id": _UNIVERSE,
        "upstream_theme_id": _THEME,
        "variant_id": f"var_{index:08x}",
        "downstream_project_id": project_id,
        "motif_id": f"motif_{index}",
        "occurrence_id": f"occ_{index}",
        "upstream_fingerprint": fingerprint,
        "created_at": "2026-10-02T00:00:00Z",
    }


def _count(db_path: Path) -> int:
    with get_connection(db_path) as conn:
        return store.count_dependency_edges(conn)


def test_three_theme_edges_cycle_and_self_edge(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.musical_dependency_store"):
        for index, project_id in enumerate(("project-b", "project-c", "project-d"), start=1):
            store.record_dependency_edge(None, _variation(index, project_id=project_id), db_path=project_db)
    assert _count(project_db) == 3
    inserted = [
        record
        for record in caplog.records
        if record.name == "app.services.musical_dependency_store"
        and record.getMessage() == "Dependency edge inserted"
    ]
    assert len(inserted) == 3
    assert inserted[0].edge_id == "dep_0000000000000001"
    assert inserted[0].dependency_type == "variation_of"
    assert inserted[0].upstream_fingerprint_prefix == _FINGERPRINT[:12]
    assert "Theme A" not in caplog.text

    with get_connection(project_db) as conn:
        with pytest.raises(MusicalDependencyError) as cycle:
            store._reject_cycle(
                conn,
                "motif:project-b:motif_1:occ_1",
                f"theme:{_UNIVERSE}:{_THEME}",
            )
    assert cycle.value.code == "dependency_cycle"
    assert cycle.value.http_status == 422
    assert _count(project_db) == 3

    with pytest.raises(MusicalDependencyError) as self_edge:
        store.record_dependency_edge(
            None,
            {
                "schema_version": "musical.dependency.edge.v1",
                "id": "dep_0000000000000099",
                "dependency_type": "motif_derived_from",
                "upstream_kind": "motif_occurrence",
                "downstream_kind": "motif_occurrence",
                "upstream_project_id": "project-b",
                "source_motif_id": "motif_1",
                "source_occurrence_id": "occ_1",
                "downstream_project_id": "project-b",
                "motif_id": "motif_1",
                "occurrence_id": "occ_1",
                "upstream_fingerprint": _FINGERPRINT,
            },
            db_path=project_db,
        )
    assert self_edge.value.code == "dependency_cycle"
    assert _count(project_db) == 3


def test_project_cap_and_stem_fingerprint_refresh(project_db: Path) -> None:
    for index in range(1, store.PROJECT_EDGE_CAP + 1):
        store.record_dependency_edge(
            None,
            _variation(index, project_id="project-cap"),
            db_path=project_db,
        )
    assert _count(project_db) == store.PROJECT_EDGE_CAP
    with pytest.raises(MusicalDependencyError) as limited:
        store.record_dependency_edge(
            None,
            _variation(store.PROJECT_EDGE_CAP + 1, project_id="project-cap"),
            db_path=project_db,
        )
    assert limited.value.code == "dependency_graph_limit"
    assert _count(project_db) == store.PROJECT_EDGE_CAP

    stem = {
        "schema_version": "musical.dependency.edge.v1",
        "id": "dep_ffffffffffffffff",
        "dependency_type": "rendered_from",
        "upstream_kind": "project",
        "downstream_kind": "neural_stem_set",
        "upstream_project_id": "project-stems",
        "downstream_asset_id": "stems-1",
        "upstream_fingerprint": _FINGERPRINT,
    }
    first = store.record_dependency_edge(None, stem, db_path=project_db)
    stem["id"] = "dep_fffffffffffffffe"
    stem["upstream_fingerprint"] = _OTHER_FINGERPRINT
    second = store.record_dependency_edge(None, stem, db_path=project_db)
    assert second.id == first.id
    assert second.upstream_fingerprint == _OTHER_FINGERPRINT
    with get_connection(project_db) as conn:
        rows = conn.execute(
            """
            SELECT id FROM musical_dependency_edges
            WHERE dependency_type = 'rendered_from' AND downstream_asset_id = 'stems-1'
            """
        ).fetchall()
    assert len(rows) == 1


def test_project_delete_keeps_upstream_only_edge(project_db: Path) -> None:
    create_project("Cue A", project_id="project-a", db_path=project_db)
    create_project("Cue B", project_id="project-b", db_path=project_db)
    create_project("Cue C", project_id="project-c", db_path=project_db)
    downstream_gone = {
        "schema_version": "musical.dependency.edge.v1",
        "id": "dep_0000000000000011",
        "dependency_type": "reference_conditioned_by",
        "upstream_kind": "project",
        "downstream_kind": "project",
        "upstream_project_id": "project-a",
        "downstream_project_id": "project-b",
        "upstream_fingerprint": _FINGERPRINT,
    }
    upstream_remains = {
        "schema_version": "musical.dependency.edge.v1",
        "id": "dep_0000000000000012",
        "dependency_type": "reference_conditioned_by",
        "upstream_kind": "project",
        "downstream_kind": "project",
        "upstream_project_id": "project-b",
        "downstream_project_id": "project-c",
        "upstream_fingerprint": _FINGERPRINT,
    }
    store.record_dependency_edge(None, downstream_gone, db_path=project_db)
    store.record_dependency_edge(None, upstream_remains, db_path=project_db)
    delete_project("project-b", db_path=project_db)
    with get_connection(project_db) as conn:
        remaining = {
            row["id"]
            for row in conn.execute("SELECT id FROM musical_dependency_edges").fetchall()
        }
    assert remaining == {"dep_0000000000000012"}


def test_motif_occurrence_fingerprint_ignores_unlisted_events() -> None:
    original = [{"id": "a1", "pitch": "C4"}, {"id": "a2", "pitch": "D4"}]
    edited = [{"id": "a1", "pitch": "C4"}, {"id": "a2", "pitch": "G4"}]
    same = motif_occurrence_fingerprint(original)
    assert same == motif_occurrence_fingerprint(list(original))
    assert same != motif_occurrence_fingerprint(edited)
    assert len(same) == 64
