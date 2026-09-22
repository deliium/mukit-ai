"""Unit tests for multi-agent artifact_role_map validation."""

from __future__ import annotations

import pytest

from app.ai_agents.errors import ArtifactPayloadRejectedError
from app.services.artifact_role_map import validate_artifact_role_map


def _full_map(**overrides):
    base = {
        "brief": {"artifact_id": "a-brief", "content_type": "agent.brief.v1"},
        "harmony_plan": {
            "artifact_id": "a-harm",
            "content_type": "agent.harmony_plan.v1",
        },
        "motif_plan": {"artifact_id": "a-motif", "content_type": "agent.motif_plan.v1"},
        "arrangement_plan": {
            "artifact_id": "a-arr",
            "content_type": "agent.arrangement_plan.v1",
        },
        "critique": {"artifact_id": "a-crit", "content_type": "agent.critique.v1"},
        "revision_plan": None,
    }
    base.update(overrides)
    return base


def test_validate_artifact_role_map_accepts_spine_approve():
    out = validate_artifact_role_map(_full_map())
    assert out["brief"]["artifact_id"] == "a-brief"
    assert out["revision_plan"] is None


def test_validate_artifact_role_map_rejects_missing_required():
    with pytest.raises(ArtifactPayloadRejectedError) as exc:
        validate_artifact_role_map(_full_map(critique=None))
    assert exc.value.code == "artifact_payload_rejected"
    assert "critique" in (exc.value.details or {}).get("missing", [])


def test_validate_artifact_role_map_requires_revision_when_flagged():
    with pytest.raises(ArtifactPayloadRejectedError) as exc:
        validate_artifact_role_map(_full_map(), require_revision_plan=True)
    assert "revision_plan" in (exc.value.details or {}).get("missing", [])


def test_validate_artifact_role_map_rejects_content_type_mismatch():
    with pytest.raises(ArtifactPayloadRejectedError) as exc:
        validate_artifact_role_map(
            _full_map(brief={"artifact_id": "x", "content_type": "agent.critique.v1"})
        )
    assert "mismatch" in str(exc.value).lower() or exc.value.code == "artifact_payload_rejected"


def test_validate_artifact_role_map_rejects_none():
    with pytest.raises(ArtifactPayloadRejectedError):
        validate_artifact_role_map(None)
