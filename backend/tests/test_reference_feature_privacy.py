"""Privacy/architecture guards for reference features."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.reference_feature_schemas import ReferenceFeatureAnalyzeRequest

BACKEND_APP = Path(__file__).resolve().parents[1] / "app"


def test_reference_feature_services_do_not_import_dataset_cli():
    forbidden_prefixes = ("app.dataset",)
    service_files = [
        BACKEND_APP / "services" / "reference_feature_analyze.py",
        BACKEND_APP / "services" / "reference_feature_condition.py",
        BACKEND_APP / "services" / "reference_feature_affinity.py",
        BACKEND_APP / "routers" / "reference_features.py",
    ]
    for path in service_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(
                        alias.name == p or alias.name.startswith(p + ".")
                        for p in forbidden_prefixes
                    ), path.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(
                    mod == p or mod.startswith(p + ".") for p in forbidden_prefixes
                ), path.name


def test_analyze_request_rejects_event_bearing_top_level():
    with pytest.raises(ValidationError):
        ReferenceFeatureAnalyzeRequest.model_validate(
            {
                "composition": {"schema": "composition.v2"},
                "events": [{"pitch": 60}],
            }
        )
