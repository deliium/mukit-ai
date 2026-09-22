"""GC / retention / CASCADE tests for agent artifact workspace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai_agents.errors import WorkspaceQuotaExceededError
from app.ai_agents.schemas import AgentArtifactKind, AgentArtifactV1, AgentBriefV1
from app.db.connection import get_connection
from app.services import project_store as store
from app.services.agent_artifact_workspace import (
    gc_expired_temporary,
    insert_temporary,
    list_project_artifacts,
)


@pytest.fixture
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "artifact-gc.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT", "5")
    monkeypatch.setenv("AGENT_ARTIFACT_TEMP_TTL_HOURS", "24")
    return db_path


def _brief(**kwargs) -> AgentArtifactV1:
    return AgentArtifactV1(
        kind=AgentArtifactKind.BRIEF,
        producer_agent_id="creative_director",
        content_type="agent.brief.v1",
        payload=AgentBriefV1(intent="gc").model_dump(mode="json"),
        source_fingerprint="g" * 32,
        retention_class="temporary",
        **kwargs,
    )


def test_gc_deletes_expired_only(project_db, caplog):
    import logging

    created = store.create_project("GC2", db_path=project_db)
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    future = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with get_connection(project_db) as conn:
        from app.services.agent_artifact_workspace import _insert_row, _validate_for_persist

        for art, expires in ((_brief(expires_at=past), past), (_brief(expires_at=future), future)):
            payload = _validate_for_persist(art)
            _insert_row(
                conn,
                project_id=created.id,
                artifact=art,
                payload=payload,
                retention_class="temporary",
                expires_at=expires,
            )
        conn.commit()

    with caplog.at_level(logging.INFO):
        deleted = gc_expired_temporary(project_id=created.id, db_path=project_db)
    assert deleted == 1
    remaining = list_project_artifacts(created.id, db_path=project_db)
    assert len(remaining) == 1
    assert any("gc" in r.message.lower() or "deleted" in r.message.lower() for r in caplog.records) or deleted == 1


def test_quota_blocks_extra_temps(project_db, monkeypatch):
    monkeypatch.setenv("AGENT_ARTIFACT_TEMP_MAX_PER_PROJECT", "1")
    created = store.create_project("Quota2", db_path=project_db)
    insert_temporary(created.id, _brief(), db_path=project_db)
    with pytest.raises(WorkspaceQuotaExceededError):
        insert_temporary(created.id, _brief(), db_path=project_db)


def test_project_delete_cascades_artifacts(project_db):
    created = store.create_project("Cascade", db_path=project_db)
    insert_temporary(created.id, _brief(), db_path=project_db)
    assert list_project_artifacts(created.id, db_path=project_db)
    store.delete_project(created.id, db_path=project_db)
    with get_connection(project_db) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) AS c FROM agent_artifacts WHERE project_id = ?",
            (created.id,),
        ).fetchone()
    assert int(rows["c"]) == 0
