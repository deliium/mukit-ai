"""Tests for immutable agent artifact workspace store."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai_agents.errors import (
    ArtifactImmutableViolationError,
    ArtifactNotFoundError,
    WorkspaceQuotaExceededError,
)
from app.ai_agents.artifact_schemas import AgentHarmonyPlanV1
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentBriefV1,
)
from app.services import agent_artifact_workspace as workspace
from app.services import project_store as store
from app.services.agent_artifact_workspace import (
    canonical_payload_digest,
    gc_expired_temporary,
    get_artifact,
    insert_temporary,
    list_project_artifacts,
    promote_and_link_revision,
    reject_payload_update,
    summarize_revision_artifacts,
)
from app.db.connection import get_connection


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "artifact-workspace.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT", "3")
    monkeypatch.setenv("AGENT_ARTIFACT_TEMP_TTL_HOURS", "24")
    return db_path


def _brief_art(**kwargs) -> AgentArtifactV1:
    return AgentArtifactV1(
        kind=AgentArtifactKind.BRIEF,
        producer_agent_id="creative_director",
        content_type="agent.brief.v1",
        payload=AgentBriefV1(intent="workspace test").model_dump(mode="json"),
        source_fingerprint="f" * 32,
        retention_class="temporary",
        **kwargs,
    )


def test_insert_get_list_and_digest(project_db):
    created = store.create_project("Art", db_path=project_db)
    art = _brief_art()
    digest = canonical_payload_digest(art.payload)
    artifact_id = insert_temporary(created.id, art, db_path=project_db)
    assert artifact_id == art.artifact_id
    meta = get_artifact(created.id, artifact_id, include_payload=True, db_path=project_db)
    assert meta["content_digest"] == digest
    assert meta["payload"]["intent"] == "workspace test"
    assert meta["retention_class"] == "temporary"
    listed = list_project_artifacts(created.id, db_path=project_db)
    assert len(listed) == 1
    assert "payload" not in listed[0]


def test_immutable_update_rejected(project_db):
    created = store.create_project("Imm", db_path=project_db)
    art = _brief_art()
    insert_temporary(created.id, art, db_path=project_db)
    with pytest.raises(ArtifactImmutableViolationError):
        reject_payload_update(created.id, art.artifact_id, db_path=project_db)


def test_quota_enforced(project_db, monkeypatch):
    monkeypatch.setenv("AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT", "2")
    created = store.create_project("Quota", db_path=project_db)
    insert_temporary(created.id, _brief_art(), db_path=project_db)
    insert_temporary(created.id, _brief_art(), db_path=project_db)
    with pytest.raises(WorkspaceQuotaExceededError):
        insert_temporary(created.id, _brief_art(), db_path=project_db)


def test_gc_expired_temporary(project_db):
    created = store.create_project("GC", db_path=project_db)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    expired = _brief_art(expires_at=past)
    living = _brief_art(expires_at=future)
    # Insert via connection without auto-GC so both rows exist before cleanup.
    with get_connection(project_db) as conn:
        from app.services.agent_artifact_workspace import _insert_row, _validate_for_persist

        for art, expires in ((expired, past), (living, future)):
            payload = _validate_for_persist(art)
            _insert_row(
                conn,
                project_id=created.id,
                artifact=art,
                payload=payload,
                retention_class="temporary",
                expires_at=expires,
            )
    deleted = gc_expired_temporary(project_id=created.id, db_path=project_db)
    assert deleted == 1
    with pytest.raises(ArtifactNotFoundError):
        get_artifact(created.id, expired.artifact_id, db_path=project_db)
    assert get_artifact(created.id, living.artifact_id, db_path=project_db)["artifact_id"]


def test_promote_and_link_summary(project_db):
    created = store.create_project("Promote", db_path=project_db)
    brief = _brief_art()
    harmony = AgentArtifactV1(
        kind=AgentArtifactKind.PLAN,
        producer_agent_id="harmony",
        content_type="agent.harmony_plan.v1",
        payload=AgentHarmonyPlanV1(key="C major").model_dump(mode="json"),
        source_fingerprint="g" * 32,
    )
    revision_id = "rev-promote-1"
    with get_connection(project_db) as conn:
        # Soft FK: revision row not required for artifact tables.
        linked = promote_and_link_revision(
            created.id,
            revision_id,
            {
                "brief": {
                    "artifact_id": brief.artifact_id,
                    "content_type": "agent.brief.v1",
                },
                "harmony_plan": {
                    "artifact_id": harmony.artifact_id,
                    "content_type": "agent.harmony_plan.v1",
                },
                "motif_plan": None,
                "arrangement_plan": None,
                "critique": None,
                "revision_plan": None,
            },
            [brief, harmony],
            conn=conn,
        )
        assert linked["brief"] == brief.artifact_id
        summary = summarize_revision_artifacts(revision_id, conn=conn)
    assert summary["schema_version"] == "revision.ai_artifact_summary.v1"
    assert summary["roles"]["brief"]["artifact_id"] == brief.artifact_id
    assert summary["roles"]["harmony_plan"]["content_type"] == "agent.harmony_plan.v1"
    assert summary["roles"]["motif_plan"] is None
    durable = get_artifact(created.id, brief.artifact_id, db_path=project_db)
    assert durable["retention_class"] == "durable"
    assert durable["expires_at"] is None


def test_workspace_writes_enabled():
    assert workspace.workspace_writes_enabled() is True
    assert "no_composition_v4" in workspace.inventory_checklist()
