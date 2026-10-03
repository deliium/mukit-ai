"""Reference analyze rights gate (registry + attestation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.reference_feature_schemas import ReferenceFeatureAnalyzeRequest, ReferenceFeatureError
from app.rights_governance_schemas import project_allowed_uses
from app.services.project_store import create_project
from app.services.reference_feature_analyze import analyze_reference_features
from app.services import rights_governance_store as rights_store

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset"))
    reset_database_initialization_cache()
    initialize_database()
    composition = CompositionV2.model_validate(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    project = create_project("Ref Rights", composition=composition, db_path=db_path)
    return {"db": db_path, "project_id": project.id, "composition": composition}


def test_reference_only_registry_analyze_ok(studio):
    rights_store.upsert_rights_entry(
        {
            "schema_version": "rights.registry.entry.v1",
            "entry_id": "rights_" + "cc" * 8,
            "source_kind": "project",
            "source_id": studio["project_id"],
            "ownership_class": "licensed",
            "use_policy": "reference_only",
            "allowed_uses": project_allowed_uses("reference_only"),
            "verification_status": "verified",
            "entry_version": 1,
            "created_at": "2026-10-03T00:00:00Z",
            "updated_at": "2026-10-03T00:00:00Z",
        },
        db_path=studio["db"],
    )
    report = analyze_reference_features(
        ReferenceFeatureAnalyzeRequest(
            project_id=studio["project_id"],
            requested_dimensions=["density"],
        )
    )
    assert "density" in report.dimensions


def test_attested_without_registry_ok(studio):
    report = analyze_reference_features(
        ReferenceFeatureAnalyzeRequest(
            project_id=studio["project_id"],
            requested_dimensions=["texture"],
            rights={"status": "user_owned", "user_owned_attested": True},
        )
    )
    assert "texture" in report.dimensions


def test_unknown_and_missing_attestation_refused(studio):
    with pytest.raises(ReferenceFeatureError) as missing:
        analyze_reference_features(
            ReferenceFeatureAnalyzeRequest(
                project_id=studio["project_id"],
                requested_dimensions=["density"],
            )
        )
    assert missing.value.code == "rights_reference_refused"

    with pytest.raises(ReferenceFeatureError) as unknown:
        analyze_reference_features(
            ReferenceFeatureAnalyzeRequest(
                project_id=studio["project_id"],
                requested_dimensions=["density"],
                rights={"status": "unknown"},
            )
        )
    assert unknown.value.code == "rights_reference_refused"


def test_inline_requires_attestation(studio):
    dump = studio["composition"].model_dump(mode="json")
    with pytest.raises(ReferenceFeatureError) as raised:
        analyze_reference_features(
            ReferenceFeatureAnalyzeRequest(
                composition=dump,
                requested_dimensions=["density"],
            )
        )
    assert raised.value.code == "rights_reference_refused"
