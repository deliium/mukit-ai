"""Acceptance: reference_only analyze OK; train manifests exclude (store upsert)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.dataset.pipeline import run_build
from app.dataset.rights_index import load_rights_manifest
from app.dataset.store import DatasetVersionStore
from app.db import initialize_database
from app.db.connection import reset_database_initialization_cache
from app.music_transformer.rights_gate import verify_train_paths_against_rights
from app.personal_composer_schemas import PersonalComposerError
from app.reference_feature_schemas import ReferenceFeatureAnalyzeRequest, ReferenceFeatureError
from app.rights_governance_schemas import project_allowed_uses
from app.services import personal_composer_service as personal_service
from app.services import rights_governance_store as rights_store
from app.services.project_store import create_project
from app.services.reference_feature_analyze import analyze_reference_features

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)
_DATASET_PIPELINE = (
    Path(__file__).resolve().parent / "fixtures" / "dataset" / "pipeline.yaml"
)


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "projects.db"
    root = tmp_path / "personal_composers"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(root))
    monkeypatch.setenv("PERSONAL_COMPOSER_FAKE", "1")
    monkeypatch.setenv("COLLABORATION_ENABLED", "0")
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "datasets"))
    reset_database_initialization_cache()
    initialize_database()
    composition = CompositionV2.model_validate(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    project = create_project("Acceptance P", composition=composition, db_path=db_path)
    return {
        "db": db_path,
        "root": root,
        "project_id": project.id,
        "composition": composition,
        "tmp": tmp_path,
    }


def test_reference_only_analyze_ok_train_excluded(studio):
    project_id = studio["project_id"]
    rights_store.upsert_rights_entry(
        {
            "schema_version": "rights.registry.entry.v1",
            "entry_id": "rights_" + "dd" * 8,
            "source_kind": "project",
            "source_id": project_id,
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
            project_id=project_id,
            requested_dimensions=["density"],
        )
    )
    assert "density" in report.dimensions

    with pytest.raises(PersonalComposerError) as train_exc:
        personal_service.start_personal_composer(
            {
                "display_name": "Accept-v1",
                "project_ids": [project_id],
                "rights": {
                    project_id: {"status": "user_owned", "user_owned_attested": True}
                },
                "max_steps": 1,
            }
        )
    assert train_exc.value.code == "personal_rights_refused"

    version_dir = run_build(_DATASET_PIPELINE, out_root=studio["tmp"] / "datasets")
    store = DatasetVersionStore(version_dir)
    ref_items = [item for item in store.iter_items() if item.use_policy == "reference_only"]
    assert ref_items
    train_text = store.split_path("train").read_text(encoding="utf-8")
    for item in ref_items:
        assert item.item_id not in train_text
    rights_manifest = load_rights_manifest(version_dir)
    assert rights_manifest is not None
    assert rights_manifest.excluded_source_counts_by_use_policy.get("reference_only", 0) >= 1
    train_ids = {row.source_id for row in rights_manifest.sources}
    for item in ref_items:
        assert item.item_id not in train_ids

    train_paths = []
    for line in store.split_path("train").read_text(encoding="utf-8").splitlines():
        if line.strip():
            train_paths.append(version_dir / json.loads(line)["path"])
    mt_manifest = verify_train_paths_against_rights(version_dir, train_paths)
    assert mt_manifest.manifest_kind == "music_transformer_train"
    for item in ref_items:
        assert item.item_id not in {s.source_id for s in mt_manifest.sources}


def test_missing_and_unknown_refuse_reference_and_train(studio):
    project_id = studio["project_id"]
    with pytest.raises(ReferenceFeatureError) as missing:
        analyze_reference_features(
            ReferenceFeatureAnalyzeRequest(
                project_id=project_id,
                requested_dimensions=["density"],
            )
        )
    assert missing.value.code == "rights_reference_refused"

    with pytest.raises(ReferenceFeatureError) as unknown:
        analyze_reference_features(
            ReferenceFeatureAnalyzeRequest(
                project_id=project_id,
                requested_dimensions=["density"],
                rights={"status": "unknown"},
            )
        )
    assert unknown.value.code == "rights_reference_refused"

    with pytest.raises(PersonalComposerError) as train_unknown:
        personal_service.start_personal_composer(
            {
                "display_name": "Accept-v1",
                "project_ids": [project_id],
                "rights": {project_id: {"status": "unknown"}},
                "max_steps": 1,
            }
        )
    assert train_unknown.value.code == "personal_rights_refused"


def test_attested_request_without_registry_allows_reference(studio):
    report = analyze_reference_features(
        ReferenceFeatureAnalyzeRequest(
            project_id=studio["project_id"],
            requested_dimensions=["texture"],
            rights={"status": "user_owned", "user_owned_attested": True},
        )
    )
    assert "texture" in report.dimensions
