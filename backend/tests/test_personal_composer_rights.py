"""Rights gate, snapshot copy, and owner-only training."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.collaboration_schemas import CollaborationError
from app.composition_schemas import CompositionV2
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.personal_composer_schemas import PersonalComposerError
from app.services import personal_composer_service as service
from app.services.collaboration_access import (
    reset_request_actor_header,
    set_request_actor_header,
)
from app.services.collaboration_store import create_actor, grant_member
from app.services.project_store import create_project

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _owned(project_id: str) -> dict:
    return {"status": "user_owned", "user_owned_attested": True}


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    root = tmp_path / "personal_composers"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(root))
    monkeypatch.setenv("PERSONAL_COMPOSER_FAKE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset"))
    reset_database_initialization_cache()
    initialize_database()
    etude = CompositionV2.model_validate(_load("personal_composer_etude.v2.json"))
    sketch = CompositionV2.model_validate(_load("personal_composer_sketch.v2.json"))
    assert [event.pitch for event in etude.tracks[0].events] == ["C4", "E4", "G4"]
    assert [event.start_tick for event in etude.tracks[0].events] == [0, 480, 960]
    assert [event.pitch for event in sketch.tracks[0].events] == ["D4", "F4", "A4"]
    etude_id = create_project("Etude", composition=etude, db_path=db_path).id
    sketch_id = create_project("Sketch", composition=sketch, db_path=db_path).id
    return {"db": db_path, "root": root, "etude": etude_id, "sketch": sketch_id}


def _body(studio: dict, project_ids: list[str], rights: dict) -> dict:
    return {
        "display_name": "MyComposer-v1",
        "project_ids": project_ids,
        "rights": rights,
        "max_steps": 1,
    }


def test_attested_projects_write_a_snapshot_without_pitch_in_the_index(studio):
    body = _body(
        studio,
        [studio["etude"], studio["sketch"]],
        {studio["etude"]: _owned(studio["etude"]), studio["sketch"]: _owned(studio["sketch"])},
    )
    job = service.start_personal_composer(body)
    index_path = studio["root"] / job.adapter_id / "snapshot" / "index.json"
    assert index_path.is_file()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert "pitch" not in json.dumps(index)
    assert len(index["items"]) == 2
    for item in index["items"]:
        assert (studio["root"] / job.adapter_id / "snapshot" / item["relative_path"]).is_file()


def test_unknown_status_writes_no_snapshot_directory(studio):
    third = create_project(
        "Unknown",
        composition=_load("personal_composer_etude.v2.json"),
        db_path=studio["db"],
    )
    body = _body(
        studio,
        [studio["etude"], studio["sketch"], third.id],
        {
            studio["etude"]: _owned(studio["etude"]),
            studio["sketch"]: _owned(studio["sketch"]),
            third.id: {"status": "unknown"},
        },
    )
    before = list(studio["root"].glob("*")) if studio["root"].exists() else []
    with pytest.raises(PersonalComposerError) as exc:
        service.start_personal_composer(body)
    assert exc.value.code == "personal_rights_refused"
    assert exc.value.details["project_id"] == third.id
    after = list(studio["root"].glob("*")) if studio["root"].exists() else []
    assert after == before
    assert list(studio["root"].glob("*/snapshot")) == []


def test_empty_selection_does_not_load_projects(studio, monkeypatch: pytest.MonkeyPatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("project load")

    monkeypatch.setattr("app.services.personal_composer_service.get_project", _boom)
    with pytest.raises(PersonalComposerError) as exc:
        service.start_personal_composer(
            {"display_name": "MyComposer-v1", "project_ids": [], "rights": {}}
        )
    assert exc.value.code == "personal_projects_required"


def test_collaboration_off_skips_membership_lookup(studio, monkeypatch: pytest.MonkeyPatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("membership lookup")

    monkeypatch.setattr("app.services.collaboration_access.get_membership", _boom)
    body = _body(
        studio,
        [studio["etude"]],
        {studio["etude"]: _owned(studio["etude"])},
    )
    job = service.start_personal_composer(body)
    assert job.status == "complete"


def test_editor_and_non_member_are_refused_and_owner_passes(
    studio, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("COLLABORATION_ENABLED", "1")
    editor = create_actor("Editor", db_path=studio["db"])
    outsider = create_actor("Outsider", db_path=studio["db"])
    grant_member(studio["etude"], editor.id, "editor", db_path=studio["db"])
    body = _body(
        studio,
        [studio["etude"]],
        {studio["etude"]: _owned(studio["etude"])},
    )
    token = set_request_actor_header(editor.id)
    try:
        with pytest.raises(CollaborationError) as denied:
            service.start_personal_composer(body)
        assert denied.value.code == "collaboration_role_denied"
        assert denied.value.http_status == 403
    finally:
        reset_request_actor_header(token)

    token = set_request_actor_header(outsider.id)
    try:
        with pytest.raises(CollaborationError) as missing:
            service.start_personal_composer(body)
        assert missing.value.code == "collaboration_not_member"
        assert missing.value.http_status == 403
    finally:
        reset_request_actor_header(token)

    token = set_request_actor_header("local")
    try:
        job = service.start_personal_composer(body)
    finally:
        reset_request_actor_header(token)
    assert job.status == "complete"
    assert job.owner_actor_id == "local"
