"""Impact lists stale dependents and leaves their scores unchanged."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, reset_database_initialization_cache
from app.db import initialize_database
from app.services import musical_dependency_impact as impact
from app.services import musical_dependency_store as edge_store
from app.services import musical_universe_store as universe_store
from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint
from app.services.musical_universe_store import update_universe_document
from app.services.project_store import create_project, get_project, update_project
from tests.test_musical_universe_bind import vector_a_score, vector_b_score

_LABELS = ("Exploration variation", "Combat variation", "Finale transformation")
_PROJECTS = ("project-b", "project-c", "project-d")


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _destination(project_id: str, universe_id: str, theme_id: str, variant_id: str, motif_id: str) -> CompositionV2:
    payload = vector_b_score().model_dump(mode="json")
    pitches = ("D4", "E4", "F#4", "G4")
    events = payload["tracks"][0]["events"]
    for index, pitch in enumerate(pitches):
        events.append(
            {
                "id": f"{project_id[-1]}{index + 1}",
                "type": "note",
                "pitch": pitch,
                "start_tick": 1920 + index * 480,
                "duration_ticks": 480,
                "velocity": 80,
            }
        )
    payload["motifs"] = [
        {
            "id": motif_id,
            "label": motif_id,
            "musical_universe_id": universe_id,
            "theme_id": theme_id,
            "variant_id": variant_id,
            "occurrences": [
                {
                    "id": f"occ_{motif_id}",
                    "track_id": "track_melody",
                    "event_ids": [f"{project_id[-1]}{index + 1}" for index in range(4)],
                    "relationship": "original",
                }
            ],
        }
    ]
    return CompositionV2.model_validate(payload)


def _notes(composition: CompositionV2) -> list[tuple[str, int, int]]:
    rows = []
    for track in composition.tracks:
        for event in track.events:
            rows.append((event.pitch, event.start_tick, event.duration_ticks))
    return rows


def _load(db_path: Path, project_id: str) -> CompositionV2:
    record = get_project(project_id, db_path=db_path)
    assert record.composition_json
    return CompositionV2.model_validate(json.loads(record.composition_json))


def test_theme_edit_marks_variations_stale_and_render_downstream(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = inspect.getsource(impact)
    assert "musical_universe_reuse" not in source
    assert "neural_audio_render" not in source
    assert "llm_composition_arrangement" not in source
    assert "ai_agents" not in source

    score_a = vector_a_score()
    source_fingerprint = composition_snapshot_fingerprint(score_a)
    create_project("Cue A", project_id="project-a", composition=score_a, db_path=project_db)
    created = universe_store.create_universe("Franchise", "project-a", db_path=project_db)
    universe_id = created.id
    theme_id = "theme_bbbb2222"
    variants = []
    for index, label in enumerate(_LABELS, start=1):
        variants.append(
            {
                "id": f"var_{index:08x}",
                "label": label,
                "operation": "transpose",
                "parameters": {"transpose_semitones": 2},
                "source_project_id": "project-a",
                "source_motif_id": "motif_theme_a",
                "source_occurrence_id": "occ_original",
            }
        )
    body = {
        "schema_version": "musical.universe.v1",
        "id": universe_id,
        "name": "Franchise",
        "entities": [{"id": "ent_aaaa1111", "kind": "character", "label": "Ada"}],
        "themes": [
            {
                "id": theme_id,
                "entity_id": "ent_aaaa1111",
                "label": "Theme A",
                "source": {
                    "project_id": "project-a",
                    "motif_id": "motif_theme_a",
                    "occurrence_id": "occ_original",
                },
                "source_fingerprint": source_fingerprint,
                "variants": variants,
            }
        ],
    }
    from app.musical_universe_schemas import parse_musical_universe

    drafted = parse_musical_universe(body)
    update_universe_document(drafted, expected_revision=1, db_path=project_db)

    before = {}
    for index, (project_id, label) in enumerate(zip(_PROJECTS, _LABELS, strict=True), start=1):
        motif_id = f"motif_{index}"
        variant_id = f"var_{index:08x}"
        score = _destination(project_id, universe_id, theme_id, variant_id, motif_id)
        create_project(label, project_id=project_id, composition=score, db_path=project_db)
        universe_store.add_member(universe_id, project_id, db_path=project_db)
        before[project_id] = score.model_dump(mode="json")
        edge_store.record_dependency_edge(
            None,
            {
                "schema_version": "musical.dependency.edge.v1",
                "id": f"dep_{index:016x}",
                "dependency_type": "variation_of",
                "upstream_kind": "theme",
                "downstream_kind": "motif_occurrence",
                "universe_id": universe_id,
                "upstream_theme_id": theme_id,
                "variant_id": variant_id,
                "downstream_project_id": project_id,
                "motif_id": motif_id,
                "occurrence_id": f"occ_{motif_id}",
                "upstream_fingerprint": source_fingerprint,
                "created_at": f"2026-10-02T00:00:0{index}Z",
            },
            db_path=project_db,
        )

    project_b = _load(project_db, "project-b")
    render_fingerprint = composition_snapshot_fingerprint(project_b)
    edge_store.record_dependency_edge(
        None,
        {
            "schema_version": "musical.dependency.edge.v1",
            "id": "dep_00000000000000f1",
            "dependency_type": "rendered_from",
            "upstream_kind": "project",
            "downstream_kind": "neural_render",
            "upstream_project_id": "project-b",
            "downstream_project_id": "project-b",
            "downstream_asset_id": "job-b",
            "upstream_fingerprint": render_fingerprint,
            "created_at": "2026-10-02T00:00:09Z",
        },
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        revision = conn.execute(
            "SELECT current_revision_id FROM projects WHERE id = 'project-b'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO neural_audio_renders (
                id, project_id, source_revision_id, source_fingerprint, status,
                model_id, adapter_kind, fidelity_class, created_at
            ) VALUES ('job-b', 'project-b', ?, ?, 'complete', 'fake:tiny',
                      'text_prompt', 'deterministic', '2026-10-02T00:00:00Z')
            """,
            (revision["current_revision_id"], render_fingerprint),
        )

    edited = score_a.model_dump(mode="json")
    edited["tracks"][0]["events"][3]["pitch"] = "G4"
    update_project("project-a", composition=edited, db_path=project_db)

    with (
        patch("app.services.musical_universe_reuse.reuse_theme") as reuse,
        patch("app.services.neural_audio_render.enqueue_neural_audio_render") as enqueue,
        caplog.at_level("INFO", logger="app.services.musical_dependency_impact"),
    ):
        report = impact.impact_for_theme(universe_id, theme_id, db_path=project_db)
    reuse.assert_not_called()
    enqueue.assert_not_called()

    by_label = {item.label: item.status for item in report.dependents}
    assert by_label["Exploration variation"] == "stale"
    assert by_label["Combat variation"] == "stale"
    assert by_label["Finale transformation"] == "stale"
    render = next(item for item in report.dependents if item.dependency_type == "rendered_from")
    assert render.status == "downstream_of_stale"
    assert render.update_offer.action == "open_neural_render"
    assert "Exploration" not in caplog.text

    for project_id in _PROJECTS:
        assert _load(project_db, project_id).model_dump(mode="json") == before[project_id]
    assert _load(project_db, "project-a").tracks[0].events[3].pitch == "G4"
    with get_connection(project_db) as conn:
        rows = conn.execute("SELECT id, status FROM neural_audio_renders").fetchall()
    assert [(row["id"], row["status"]) for row in rows] == [("job-b", "complete")]
