"""Climax acceptance + architecture/logging safety for critique engine."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import pytest

from app.ai_agents.schemas import CritiqueRecommendation
from app.services.composition_critique import evaluate_composition
from app.services.composition_critique_policy import recommend_from_findings
from app.critique_schemas import CritiqueFindingV1
from app.services.llm_composition_critique import filter_subjective_findings
from tests.test_composition_critique_engine import _two_section_composition


def test_climax_ac_fingerprint_unchanged(caplog):
    composition = _two_section_composition(climax_notes=3, prior_notes=3)
    before = composition.model_dump(mode="json")
    with caplog.at_level(logging.INFO):
        result = evaluate_composition(copy.deepcopy(before))
    after = composition.model_dump(mode="json")
    assert before == after
    codes = [f.code for f in result.critique.findings]
    assert "climax_lacks_contrast" in codes
    climax = next(f for f in result.critique.findings if f.code == "climax_lacks_contrast")
    assert climax.stratum == "stylistic"
    assert result.critique.recommendation == CritiqueRecommendation.APPROVE
    # INFO must not dump full findings prose lists.
    for record in caplog.records:
        if record.name.startswith("app.services.composition_critique"):
            msg = record.getMessage().lower()
            assert "nearly identical note density" not in msg


def test_stylistic_climax_alone_does_not_revise():
    findings = [
        CritiqueFindingV1(
            stratum="stylistic",
            category="contrast",
            code="climax_lacks_contrast",
            severity="info",
            explanation="obs",
        )
    ]
    assert recommend_from_findings(findings) == CritiqueRecommendation.APPROVE


def test_generic_model_finding_rejected():
    accepted, rejected = filter_subjective_findings(
        [
            {
                "stratum": "subjective",
                "category": "other",
                "code": "model_subjective_observation",
                "severity": "info",
                "explanation": "needs more emotion",
            }
        ]
    )
    assert accepted == []
    assert rejected == 1


def test_ai_agents_still_forbid_workspace_imports():
    package = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    forbidden = (
        "agent_artifact_workspace",
        "agent_artifact_settings",
        "from app.services.project_store",
        "from app.services.project_history_store",
        'os.environ.get("PROJECT_DB_PATH"',
        "os.environ['PROJECT_DB_PATH']",
    )
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} contains {token}"


def test_findings_cannot_embed_playable_score():
    with pytest.raises(Exception):
        CritiqueFindingV1(
            stratum="technical",
            category="other",
            code="excessive_duplicate_notes",
            evidence={"metrics": {"tracks": []}},
        )
