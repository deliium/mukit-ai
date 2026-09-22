"""Apply-path binding: promote role map into revision_artifact_links."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.ai_agents.artifact_schemas import (
    AgentArrangementPlanV1,
    AgentHarmonyPlanV1,
    AgentMotifPlanV1,
)
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentBriefV1,
    AgentCritiqueV1,
    CritiqueRecommendation,
)
from app.db.connection import get_connection
from app.services import project_store as store
from app.services.agent_artifact_workspace import (
    promote_and_link_revision,
    summarize_revision_artifacts,
)
from tests.test_composition_v2_schema import minimal_v2


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "artifact-apply-bind.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    return db_path


def _art(kind, content_type, payload, producer="creative_director") -> AgentArtifactV1:
    return AgentArtifactV1(
        kind=kind,
        producer_agent_id=producer,
        content_type=content_type,
        payload=payload,
        source_fingerprint="b" * 32,
        retention_class="temporary",
    )


def test_promote_and_link_revision_binds_roles(project_db):
    created = store.create_project(
        "Bind",
        composition=deepcopy(minimal_v2()),
        db_path=project_db,
    )
    brief = _art(
        AgentArtifactKind.BRIEF,
        "agent.brief.v1",
        AgentBriefV1(intent="bind").model_dump(mode="json"),
    )
    harmony = _art(
        AgentArtifactKind.PLAN,
        "agent.harmony_plan.v1",
        AgentHarmonyPlanV1(key="C major").model_dump(mode="json"),
        producer="harmony",
    )
    motif = _art(
        AgentArtifactKind.PLAN,
        "agent.motif_plan.v1",
        AgentMotifPlanV1().model_dump(mode="json"),
        producer="melody_motif",
    )
    arrangement = _art(
        AgentArtifactKind.PLAN,
        "agent.arrangement_plan.v1",
        AgentArrangementPlanV1(texture_summary="sparse").model_dump(mode="json"),
        producer="arrangement",
    )
    critique = _art(
        AgentArtifactKind.CRITIQUE,
        "agent.critique.v1",
        AgentCritiqueV1(
            recommendation=CritiqueRecommendation.APPROVE,
            reason_codes=["ok"],
            summary="ok",
        ).model_dump(mode="json"),
        producer="critic",
    )
    envelopes = [brief, harmony, motif, arrangement, critique]
    role_map = {
        "brief": {"artifact_id": brief.artifact_id, "content_type": brief.content_type},
        "harmony_plan": {
            "artifact_id": harmony.artifact_id,
            "content_type": harmony.content_type,
        },
        "motif_plan": {"artifact_id": motif.artifact_id, "content_type": motif.content_type},
        "arrangement_plan": {
            "artifact_id": arrangement.artifact_id,
            "content_type": arrangement.content_type,
        },
        "critique": {
            "artifact_id": critique.artifact_id,
            "content_type": critique.content_type,
        },
        "revision_plan": None,
    }
    revision_id = "rev-bind-1"
    with get_connection(project_db) as conn:
        promote_and_link_revision(
            created.id,
            revision_id,
            role_map,
            envelopes,
            conn=conn,
        )
        conn.commit()
        summary = summarize_revision_artifacts(revision_id, conn=conn)
    roles = summary["roles"]
    assert roles["brief"]["artifact_id"] == brief.artifact_id
    assert roles["harmony_plan"]["artifact_id"] == harmony.artifact_id
    assert roles["motif_plan"]["artifact_id"] == motif.artifact_id
    assert roles["arrangement_plan"]["artifact_id"] == arrangement.artifact_id
    assert roles["critique"]["artifact_id"] == critique.artifact_id
    assert roles.get("revision_plan") is None
