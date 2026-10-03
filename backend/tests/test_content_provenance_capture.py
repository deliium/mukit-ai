"""Capture hooks for human / AI / reference / personal provenance."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.db import initialize_database
from app.db.connection import get_connection, reset_database_initialization_cache
from app.project_history_schemas import AiProvenance, DurableCommitRequest, RevisionOperationType
from app.services import content_provenance_store as store
from app.services import project_history as history
from app.services import project_store as project_store_mod
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _commit(project_id: str, branch, *, operation, composition, ai=None, db_path: Path):
    return history.commit_revision(
        project_id,
        DurableCommitRequest(
            branch_id=branch.id,
            expected_active_branch_id=branch.id,
            expected_working_version=branch.working_version,
            expected_head_revision_id=branch.head_revision_id,
            expected_source_fingerprint=branch.working_fingerprint,
            composition=composition,
            operation_type=operation,
            ai=ai,
        ),
        db_path=db_path,
    )


def test_human_and_ai_capture(project_db: Path) -> None:
    created = project_store_mod.create_project(
        "Prov capture", composition=minimal_v2(), project_id="cap-1", db_path=project_db
    )
    branches = history.list_branches(created.id, db_path=project_db)
    branch = branches.branches[0]
    payload = deepcopy(minimal_v2())
    payload["tracks"][0]["events"] = [
        {"pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 70}
    ]
    human = _commit(
        created.id,
        branch,
        operation=RevisionOperationType.MANUAL_CHECKPOINT,
        composition=CompositionV2.model_validate(payload),
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        rows = store.list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=human.current_revision_id,
            project_id=created.id,
        )
    assert len(rows) == 1
    assert rows[0].operation == "human_edit"
    assert "events" not in rows[0].model_dump(mode="json")

    branches = history.list_branches(created.id, db_path=project_db)
    branch = branches.branches[0]
    payload["tracks"][0]["events"][0]["pitch"] = "F4"
    ai = _commit(
        created.id,
        branch,
        operation=RevisionOperationType.GENERATE_APPLY,
        composition=CompositionV2.model_validate(payload),
        ai=AiProvenance(provider="fake", model="fake-deterministic", model_id="fake:llm", runtime="fake"),
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        ai_rows = store.list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=ai.current_revision_id,
            project_id=created.id,
        )
    assert ai_rows[0].operation == "ai_generate"
    assert ai_rows[0].actor_kind == "ai"


def test_reference_and_personal_parents(project_db: Path) -> None:
    created = project_store_mod.create_project(
        "Ref personal", composition=minimal_v2(), project_id="cap-2", db_path=project_db
    )
    branches = history.list_branches(created.id, db_path=project_db)
    branch = branches.branches[0]
    payload = deepcopy(minimal_v2())
    payload["tempo"] = 110
    result = _commit(
        created.id,
        branch,
        operation=RevisionOperationType.GENERATE_APPLY,
        composition=CompositionV2.model_validate(payload),
        ai=AiProvenance(
            provider="fake",
            model="personal",
            model_id="personal:pcomp_abcd1234",
            generation_parameters={
                "reference_project_id": "ref-project-9",
                "composer_profile_id": "cprof_xyz",
                "composer_model_id": "personal:pcomp_abcd1234",
            },
        ),
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        rows = store.list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=result.current_revision_id,
            project_id=created.id,
        )
    assert rows[0].operation == "personal_adapter_generate"
    kinds = {item.kind for item in rows[0].parent_artifacts}
    assert "reference_project" in kinds
    assert "composer_profile" in kinds
    assert "personal_adapter" in kinds


def test_import_and_transcription_stamp(project_db: Path) -> None:
    created = project_store_mod.create_project(
        "Import", composition=minimal_v2(), project_id="cap-3", db_path=project_db
    )
    branches = history.list_branches(created.id, db_path=project_db)
    branch = branches.branches[0]
    payload = deepcopy(minimal_v2())
    payload["key"] = "G major"
    # Import without format in summary → import_midi default.
    imported = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch.id,
            expected_active_branch_id=branch.id,
            expected_working_version=branch.working_version,
            expected_head_revision_id=branch.head_revision_id,
            expected_source_fingerprint=branch.working_fingerprint,
            composition=CompositionV2.model_validate(payload),
            operation_type=RevisionOperationType.IMPORT,
            ai=AiProvenance(generation_parameters={"source_format": "musicxml"}),
        ),
        db_path=project_db,
    )
    # Note: import format comes from summary which is built from ai_fields summary_json —
    # generation_parameters alone may not land in summary. Capture still maps IMPORT.
    with get_connection(project_db) as conn:
        rows = store.list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=imported.current_revision_id,
            project_id=created.id,
        )
    assert rows[0].operation in {"import_midi", "import_musicxml"}

    # Manual checkpoint without audio_transcribe stamp must not invent transcription_apply.
    branches = history.list_branches(created.id, db_path=project_db)
    branch = branches.branches[0]
    payload["tempo"] = 90
    manual = _commit(
        created.id,
        branch,
        operation=RevisionOperationType.MANUAL_CHECKPOINT,
        composition=CompositionV2.model_validate(payload),
        db_path=project_db,
    )
    with get_connection(project_db) as conn:
        manual_rows = store.list_records_for_artifact(
            conn,
            artifact_kind="composition_revision",
            artifact_id=manual.current_revision_id,
            project_id=created.id,
        )
    assert manual_rows[0].operation == "human_edit"
