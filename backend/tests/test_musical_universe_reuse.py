"""Mechanical theme reuse across member projects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, reset_database_initialization_cache
from app.db import initialize_database
from app.musical_universe_schemas import (
    FORBIDDEN_NOTE_KEYS,
    MusicalUniverseError,
    UniverseTransformParameters,
)
from app.services.collaboration_permissions import revision_origin
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.musical_universe_commands import apply_command_payload
from app.services.musical_universe_reuse import ThemeReuseRequest, reuse_theme
from app.services import musical_universe_store as store
from app.services.project_store import create_project, get_project, update_project
from tests.test_musical_universe_bind import vector_a_score, vector_b_score


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _branch(db_path: Path, project_id: str) -> dict[str, object]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT b.id, b.working_version, b.head_revision_id, b.working_fingerprint,
                   p.active_branch_id
            FROM project_branches AS b
            JOIN projects AS p ON p.id = b.project_id AND p.active_branch_id = b.id
            WHERE b.project_id = ?
            """,
            (project_id,),
        ).fetchone()
    assert row is not None
    return {
        "branch_id": row["id"],
        "expected_active_branch_id": row["active_branch_id"],
        "expected_working_version": int(row["working_version"]),
        "expected_head_revision_id": row["head_revision_id"],
        "expected_source_fingerprint": row["working_fingerprint"],
    }


def _note_rows(composition: CompositionV2) -> list[tuple[str, int, int]]:
    rows = []
    for track in composition.tracks:
        for event in track.events:
            rows.append((event.pitch, event.start_tick, event.duration_ticks))
    return rows


def _load(db_path: Path, project_id: str) -> CompositionV2:
    record = get_project(project_id, db_path=db_path)
    assert record.composition_json
    return CompositionV2.model_validate(json.loads(record.composition_json))


def _walk_keys(payload: object) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found.update(_walk_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_walk_keys(item))
    return found


def _franchise(db_path: Path):
    score_a = vector_a_score()
    score_b = vector_b_score()
    create_project("Cue A", project_id="project-a", composition=score_a, db_path=db_path)
    create_project("Cue B", project_id="project-b", composition=score_b, db_path=db_path)
    created = store.create_universe("Franchise", "project-a", db_path=db_path)
    store.add_member(created.id, "project-b", db_path=db_path)
    themed = apply_command_payload(
        created.universe,
        {"op": "create_entity", "kind": "character", "label": "Ada"},
    )
    themed = apply_command_payload(
        themed,
        {
            "op": "create_theme",
            "entity_id": themed.entities[0].id,
            "label": "Theme A",
            "source": {
                "project_id": "project-a",
                "motif_id": "motif_theme_a",
                "occurrence_id": "occ_original",
            },
            "source_fingerprint": composition_snapshot_fingerprint(score_a),
            "source_checked": True,
        },
    )
    record = store.update_universe_document(
        themed,
        expected_revision=created.document_revision,
        db_path=db_path,
    )
    return record


def _reuse(db_path: Path, universe_id: str, theme_id: str, *, project_id: str, revision: int, operation: str = "transpose"):
    branch = _branch(db_path, project_id)
    return reuse_theme(
        universe_id,
        theme_id,
        ThemeReuseRequest(
            destination_project_id=project_id,
            destination_track_id="track_melody",
            destination_start_bar=2,
            operation=operation,
            parameters=UniverseTransformParameters(transpose_semitones=2) if operation == "transpose" else None,
            variant_id=None,
            expected_universe_revision=revision,
            branch_id=str(branch["branch_id"]),
            expected_active_branch_id=str(branch["expected_active_branch_id"]),
            expected_working_version=int(branch["expected_working_version"]),
            expected_head_revision_id=str(branch["expected_head_revision_id"]),
            expected_source_fingerprint=str(branch["expected_source_fingerprint"]),
        ),
        db_path=db_path,
    )


def test_vector_b_transposes_theme_into_project_b(project_db: Path) -> None:
    before_a = vector_a_score()
    record = _franchise(project_db)
    theme_id = record.universe.themes[0].id
    before_revision = record.document_revision
    before_b_fp = _branch(project_db, "project-b")["expected_source_fingerprint"]
    result = _reuse(project_db, record.id, theme_id, project_id="project-b", revision=before_revision)
    destination = _load(project_db, "project-b")
    rows = _note_rows(destination)
    assert ("G4", 0, 480) in rows
    assert ("D4", 1920, 480) in rows
    assert ("E4", 2400, 480) in rows
    assert ("F#4", 2880, 480) in rows
    assert ("G4", 3360, 480) in rows
    assert _note_rows(_load(project_db, "project-a")) == _note_rows(before_a)
    motif = next(item for item in destination.motifs if item.id == result.motif_id)
    assert motif.musical_universe_id == record.id
    assert motif.theme_id == theme_id
    assert motif.variant_id == result.variant_id
    assert motif.occurrences[0].relationship == "original"
    usage = result.universe.universe.themes[0].usages[0]
    assert usage.operation == "transpose"
    assert usage.parameters.transpose_semitones == 2
    assert usage.source_occurrence_id == "occ_original"
    assert usage.destination_occurrence_id == result.occurrence_id
    assert result.universe.document_revision == before_revision + 1
    with get_connection(project_db) as conn:
        revision = conn.execute(
            """
            SELECT operation_type FROM project_revisions
            WHERE project_id = 'project-b'
            ORDER BY sequence DESC LIMIT 1
            """
        ).fetchone()
        body = conn.execute(
            "SELECT body_json FROM musical_universes WHERE id = ?",
            (record.id,),
        ).fetchone()
    assert revision is not None
    assert revision["operation_type"] == "musical-universe-theme-apply"
    assert revision_origin("musical-universe-theme-apply") == "ai"
    assert body is not None
    keys = _walk_keys(json.loads(body["body_json"]))
    assert keys.isdisjoint(FORBIDDEN_NOTE_KEYS)
    assert before_b_fp != result.destination_fingerprint


def test_cas_failure_leaves_destination_fingerprint(project_db: Path) -> None:
    record = _franchise(project_db)
    before = str(_branch(project_db, "project-b")["expected_source_fingerprint"])
    with pytest.raises(MusicalUniverseError) as captured:
        _reuse(
            project_db,
            record.id,
            record.universe.themes[0].id,
            project_id="project-b",
            revision=record.document_revision + 9,
        )
    assert captured.value.code == "musical_universe_conflict"
    assert str(_branch(project_db, "project-b")["expected_source_fingerprint"]) == before
    reloaded = store.get_universe(record.id, db_path=project_db)
    assert reloaded.universe.themes[0].usages == []


def test_vector_c_rejects_non_member(project_db: Path) -> None:
    record = _franchise(project_db)
    create_project("Cue C", project_id="project-c", composition=vector_b_score(), db_path=project_db)
    before = str(_branch(project_db, "project-c")["expected_source_fingerprint"])
    with pytest.raises(MusicalUniverseError) as captured:
        _reuse(
            project_db,
            record.id,
            record.universe.themes[0].id,
            project_id="project-c",
            revision=record.document_revision,
        )
    assert captured.value.code == "universe_project_not_member"
    assert str(_branch(project_db, "project-c")["expected_source_fingerprint"]) == before
    assert store.get_universe(record.id, db_path=project_db).universe.themes[0].usages == []


def test_vector_d_missing_source_occurrence(project_db: Path) -> None:
    record = _franchise(project_db)
    score = vector_a_score().model_copy(update={"motifs": []})
    update_project("project-a", composition=score, db_path=project_db)
    before = str(_branch(project_db, "project-b")["expected_source_fingerprint"])
    with pytest.raises(MusicalUniverseError) as captured:
        _reuse(
            project_db,
            record.id,
            record.universe.themes[0].id,
            project_id="project-b",
            revision=record.document_revision,
        )
    assert captured.value.code == "universe_source_missing"
    assert str(_branch(project_db, "project-b")["expected_source_fingerprint"]) == before


def test_same_project_transpose_appends_occurrence(project_db: Path) -> None:
    record = _franchise(project_db)
    result = _reuse(
        project_db,
        record.id,
        record.universe.themes[0].id,
        project_id="project-a",
        revision=record.document_revision,
    )
    destination = _load(project_db, "project-a")
    motif = next(item for item in destination.motifs if item.id == "motif_theme_a")
    original = next(item for item in motif.occurrences if item.id == "occ_original")
    added = next(item for item in motif.occurrences if item.id == result.occurrence_id)
    assert original.event_ids == ["a1", "a2", "a3", "a4"]
    assert original.relationship == "original"
    assert added.relationship == "transpose"
    assert added.transform is not None
    assert added.transform.operation == "transpose"


def test_rhythmic_variation_is_unsupported(project_db: Path) -> None:
    record = _franchise(project_db)
    before = str(_branch(project_db, "project-b")["expected_source_fingerprint"])
    with pytest.raises(MusicalUniverseError) as captured:
        _reuse(
            project_db,
            record.id,
            record.universe.themes[0].id,
            project_id="project-b",
            revision=record.document_revision,
            operation="rhythmic_variation",
        )
    assert captured.value.code == "universe_operation_unsupported"
    assert str(_branch(project_db, "project-b")["expected_source_fingerprint"]) == before


def test_realize_and_reuse_avoid_forbidden_calls() -> None:
    realize = Path(__file__).resolve().parents[1] / "app" / "services" / "musical_universe_realize.py"
    reuse = Path(__file__).resolve().parents[1] / "app" / "services" / "musical_universe_reuse.py"
    realize_text = realize.read_text(encoding="utf-8")
    reuse_text = reuse.read_text(encoding="utf-8")
    assert "composition_motif_editor" not in realize_text
    assert "ai_agents" not in realize_text
    assert "commit_revision(" not in reuse_text
