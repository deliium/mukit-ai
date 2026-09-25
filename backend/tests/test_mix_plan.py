"""Mix plan schemas, compilers, preview/apply/reject/undo, stem immutability."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai_runtime.registry import reload_registry
from app.ai_runtime.runtimes.fake_neural_audio import FAKE_NEURAL_AUDIO_MODEL_ID
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.mix_plan_schemas import MixPlanV1
from app.mix_plan_settings import load_mix_plan_settings
from app.services.mix_plan.compile import compile_plan
from app.services.mix_plan.intent import compile_phrase
from app.services.mix_plan_store import absolute_under_root
from app.services.neural_audio_render_store import absolute_audio_path, load_settings_and_root
from app.mix_plan_schemas import MixPlanObservationRef
from tests.fixtures.audio.mix_plan.builders import build_role_wav
from tests.test_composition_v2_schema import minimal_v2
from tests.test_mix_analysis import _multi_stem_composition


@pytest.fixture
def mix_plan_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "neural_audio_renders"))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    monkeypatch.setenv("MIX_ANALYSIS_ROOT", str(tmp_path / "mix_analysis_reports"))
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "1")
    monkeypatch.setenv("MIX_PLAN_ROOT", str(tmp_path / "mix_plans"))
    monkeypatch.setenv("MIX_PLAN_FAKE_MODE", "1")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    reload_registry()
    return tmp_path


@pytest.fixture
def client(mix_plan_env: Path):
    with TestClient(app) as test_client:
        yield test_client


def test_fixture_builder_writes_distinct_role_wavs(tmp_path: Path) -> None:
    bass = build_role_wav(tmp_path / "bass.wav", role="bass")
    piano = build_role_wav(tmp_path / "piano.wav", role="piano")
    assert bass.stat().st_size > 44
    assert hashlib.sha256(bass.read_bytes()).hexdigest() != hashlib.sha256(piano.read_bytes()).hexdigest()


def test_schema_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        MixPlanV1.model_validate(
            {
                "stem_set_id": "s",
                "project_id": "p",
                "master_target": "dynamic",
                "intent_code": "reduce_dominance",
                "unexpected": 1,
            }
        )


def test_phrase_compilers() -> None:
    roles = {"bass", "piano", "strings"}
    bass = compile_phrase("make bass less dominant", roles)
    assert bass.intent_code == "reduce_dominance"
    assert bass.roles == ["bass"]
    assert compile_phrase("Make the strings less dominant.", roles).roles == ["strings"]
    assert compile_phrase("Give the piano more space.", roles).intent_code == "more_space"
    assert compile_phrase("Make the climax wider.", roles).intent_code == "wider_climax"
    assert compile_phrase("Reduce low-frequency masking.", roles).intent_code == "reduce_lf_masking"


def test_masking_observation_compiles_eq_on_second_role() -> None:
    stems = [
        {"id": "b", "role": "bass", "track_ids": ["bass-1"]},
        {"id": "s", "role": "strings", "track_ids": ["strings-1"]},
    ]
    obs = MixPlanObservationRef(
        code="spectral_masking_proxy",
        stem_roles=["bass", "strings"],
        freq_hz_low=80,
        freq_hz_high=300,
    )
    plan = compile_plan(
        intent=None,
        observations=[obs],
        active_stems=stems,
        master_target="dynamic",
        stem_set_id="set",
        project_id="proj",
        fingerprint="fp",
        duration_seconds=2.0,
    )
    eq = next(op for op in plan.ops if op.type == "eq")
    assert eq.stem_roles == ["strings"]
    assert eq.reason_code == "spectral_masking_proxy"
    assert eq.after < eq.before
    assert plan.guarantee is False


def test_stereo_pan_follows_signed_balance_and_send_names_room() -> None:
    stems = [{"id": "p", "role": "piano", "track_ids": ["piano-1"]}]
    signed = compile_plan(
        intent=None,
        observations=[
            MixPlanObservationRef(
                code="stereo_imbalance",
                stem_roles=["piano"],
                measurement_value=6.0,
            )
        ],
        active_stems=stems,
        master_target="dynamic",
        stem_set_id="set",
        project_id="proj",
        fingerprint="fp",
        duration_seconds=2.0,
    )
    pan = next(op for op in signed.ops if op.type == "pan")
    assert pan.after > 0
    assert abs(pan.after) <= 0.35
    unsigned = compile_plan(
        intent=None,
        observations=[MixPlanObservationRef(code="stereo_imbalance", stem_roles=["piano"])],
        active_stems=stems,
        master_target="dynamic",
        stem_set_id="set",
        project_id="proj",
        fingerprint="fp",
        duration_seconds=2.0,
    )
    assert not any(op.type == "pan" for op in unsigned.ops)
    assert any(item.code == "stereo_imbalance_unsigned" for item in unsigned.warnings)
    intent = compile_phrase("Give the piano more space.", {"piano"})
    space = compile_plan(
        intent=intent,
        observations=[],
        active_stems=stems,
        master_target="dynamic",
        stem_set_id="set",
        project_id="proj",
        fingerprint="fp",
        duration_seconds=2.0,
    )
    send = next(op for op in space.ops if op.type == "send")
    assert send.reverb_id == "algorithmic_room"


def test_migration_creates_mix_plan_table(mix_plan_env: Path) -> None:
    path = Path(os.environ["PROJECT_DB_PATH"])
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mix_plan_revisions'"
        ).fetchone()
    assert row is not None


def test_path_confinement_rejects_escape(mix_plan_env: Path) -> None:
    settings = load_mix_plan_settings()
    with pytest.raises(Exception) as exc:
        absolute_under_root(settings, "../outside.wav")
    assert getattr(exc.value, "code", "") == "mix_plan_internal_error"


def _project_and_stems(client: TestClient) -> tuple[str, dict]:
    created = client.post("/projects", json={"name": "mix-plan", "composition": minimal_v2()})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    stem_set = client.post(
        "/neural-audio/stem-sets",
        json={
            "project_id": project_id,
            "composition": _multi_stem_composition(),
            "engine": "neural",
            "stem_roles": ["piano", "bass", "strings"],
        },
    )
    assert stem_set.status_code == 200, stem_set.text
    body = stem_set.json()
    assert body["status"] == "complete"
    return project_id, body


def _stem_hashes(stem_set: dict) -> dict[str, str]:
    settings = load_settings_and_root()
    hashes = {}
    for stem in stem_set["stems"]:
        if not stem.get("audio_relpath"):
            continue
        path = absolute_audio_path(settings, stem["audio_relpath"])
        hashes[stem["id"]] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _preview(client: TestClient, project_id: str, stem_set_id: str, **extra) -> dict:
    payload = {
        "project_id": project_id,
        "stem_set_id": stem_set_id,
        "include_audio_preview": True,
        "master_target": "dynamic",
    }
    payload.update(extra)
    response = client.post("/mix-plan/preview", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_acceptance_preview_apply_preserves_stems(client: TestClient, mix_plan_env: Path) -> None:
    project_id, stem_set = _project_and_stems(client)
    before = _stem_hashes(stem_set)
    preview = _preview(
        client,
        project_id,
        stem_set["id"],
        phrase="make bass less dominant",
    )
    bass = next(change for change in preview["changes"] if change["target"] == "bass" and change["type"] == "gain")
    assert bass["after"] < bass["before"]
    assert "reduce_dominance" in bass["reason"]
    assert preview["plan"]["guarantee"] is False
    assert preview["plan"]["mutates_stems"] is False
    assert preview["dry_audio"] is True
    assert preview["processed_audio"] is True
    dry = client.get(f"/mix-plan/previews/{preview['preview_id']}/audio", params={"which": "dry"})
    processed = client.get(
        f"/mix-plan/previews/{preview['preview_id']}/audio", params={"which": "processed"}
    )
    assert dry.status_code == 200
    assert processed.status_code == 200
    assert dry.content != processed.content

    applied = client.post(
        "/mix-plan/apply",
        json={"preview_id": preview["preview_id"], "digest": preview["digest"], "plan": preview["plan"]},
    )
    assert applied.status_code == 200, applied.text
    revision = applied.json()["revision"]
    assert revision["is_head"] is True
    audio = client.get(f"/mix-plan/revisions/{revision['id']}/audio")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    after = _stem_hashes(stem_set)
    assert after == before

    first_path = Path(os.environ["MIX_PLAN_ROOT"]) / revision["mix_relpath"]
    second = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    applied_again = client.post(
        "/mix-plan/apply",
        json={"preview_id": second["preview_id"], "digest": second["digest"], "plan": second["plan"]},
    )
    assert applied_again.status_code == 200, applied_again.text
    second_rev = applied_again.json()["revision"]
    second_path = Path(os.environ["MIX_PLAN_ROOT"]) / second_rev["mix_relpath"]
    assert first_path.is_file()
    assert second_path.is_file()
    assert first_path != second_path
    undo = client.post(f"/mix-plan/revisions/{second_rev['id']}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["head_revision_id"] == revision["id"]
    assert first_path.is_file() and second_path.is_file()
    assert _stem_hashes(stem_set) == before


def test_phrase_examples_and_streaming_disclaimer(client: TestClient) -> None:
    project_id, stem_set = _project_and_stems(client)
    strings = _preview(client, project_id, stem_set["id"], phrase="Make the strings less dominant.")
    assert any(c["target"] == "strings" and c["type"] == "gain" and c["after"] < c["before"] for c in strings["changes"])
    piano = _preview(client, project_id, stem_set["id"], phrase="Give the piano more space.")
    assert any(c["target"] == "piano" and c["type"] == "send" and c["after"] > c["before"] for c in piano["changes"])
    climax = _preview(client, project_id, stem_set["id"], phrase="Make the climax wider.")
    assert any(c["code"] == "section_unspecified" for c in climax["warnings"])
    masking = _preview(client, project_id, stem_set["id"], phrase="Reduce low-frequency masking.")
    assert any(c["code"] == "analysis_report_absent" for c in masking["warnings"])
    assert any(c["type"] == "filter" and c["target"] != "bass" for c in masking["changes"])
    streaming = _preview(
        client,
        project_id,
        stem_set["id"],
        phrase="make bass less dominant",
        master_target="streaming",
    )
    assert streaming["master_outcome"]["guarantee"] is False
    assert streaming["master_outcome"]["loudness_goal_lufs"] == -14.0
    assert any(c["code"] == "master_target_unverified" for c in streaming["warnings"])


def test_unknown_phrase_and_missing_role(client: TestClient) -> None:
    project_id, stem_set = _project_and_stems(client)
    unknown = client.post(
        "/mix-plan/preview",
        json={"project_id": project_id, "stem_set_id": stem_set["id"], "phrase": "make it slap harder"},
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "mix_plan_intent_unrecognized"
    missing = client.post(
        "/mix-plan/preview",
        json={"project_id": project_id, "stem_set_id": stem_set["id"], "phrase": "make vocals less dominant"},
    )
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "mix_plan_role_unavailable"


def test_reject_preview_leaves_stems(client: TestClient) -> None:
    project_id, stem_set = _project_and_stems(client)
    before = _stem_hashes(stem_set)
    preview = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    rejected = client.delete(f"/mix-plan/previews/{preview['preview_id']}")
    assert rejected.status_code == 200
    missing = client.get(
        f"/mix-plan/previews/{preview['preview_id']}/audio", params={"which": "processed"}
    )
    assert missing.status_code == 404
    assert _stem_hashes(stem_set) == before


def test_stem_change_between_preview_and_apply(client: TestClient) -> None:
    project_id, stem_set = _project_and_stems(client)
    preview = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    neural = load_settings_and_root()
    bass = next(stem for stem in stem_set["stems"] if stem["stem_role"] == "bass")
    path = absolute_audio_path(neural, bass["audio_relpath"])
    path.write_bytes(path.read_bytes() + b"\x00")
    applied = client.post(
        "/mix-plan/apply",
        json={"preview_id": preview["preview_id"], "digest": preview["digest"], "plan": preview["plan"]},
    )
    assert applied.status_code == 409
    assert applied.json()["detail"]["code"] == "mix_plan_stem_changed"
    root = Path(os.environ["MIX_PLAN_ROOT"]) / project_id
    revision_wavs = [p for p in root.rglob("mix.wav")] if root.exists() else []
    assert revision_wavs == []


def test_undo_without_parent(client: TestClient) -> None:
    project_id, stem_set = _project_and_stems(client)
    preview = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    applied = client.post(
        "/mix-plan/apply",
        json={"preview_id": preview["preview_id"], "digest": preview["digest"], "plan": preview["plan"]},
    )
    revision_id = applied.json()["revision"]["id"]
    undo = client.post(f"/mix-plan/revisions/{revision_id}/undo")
    assert undo.status_code == 409
    assert undo.json()["detail"]["code"] == "mix_plan_undo_empty"
    audio = client.get(f"/mix-plan/revisions/{revision_id}/audio")
    assert audio.status_code == 200


def test_second_preview_before_matches_applied_head(client: TestClient) -> None:
    project_id, stem_set = _project_and_stems(client)
    first = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    applied = client.post(
        "/mix-plan/apply",
        json={"preview_id": first["preview_id"], "digest": first["digest"], "plan": first["plan"]},
    )
    assert applied.status_code == 200, applied.text
    head_gain = next(
        op for op in first["plan"]["ops"] if op["type"] == "gain" and op["stem_roles"] == ["bass"]
    )
    second = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    again = next(
        op for op in second["plan"]["ops"] if op["op_id"] == head_gain["op_id"]
    )
    assert again["before"] == head_gain["after"]


def test_section_chip_uses_persisted_report_without_phrase(client: TestClient) -> None:
    from app.mix_analysis_schemas import (
        MixAnalysisLocus,
        MixAnalysisMeasurement,
        MixAnalysisObservation,
        MixAnalysisReport,
    )
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis_store import insert_report

    project_id, stem_set = _project_and_stems(client)
    report = MixAnalysisReport(
        stem_set_id=stem_set["id"],
        source_stem_set_fingerprint="fp-section",
        observations=[
            MixAnalysisObservation(
                code="section_loudness_flat",
                message="Section loudness is flat.",
                locus=MixAnalysisLocus(stem_roles=["piano"]),
                reason="section delta is flat",
            ),
            MixAnalysisObservation(
                code="spectral_masking_proxy",
                message="Masking between bass and strings.",
                locus=MixAnalysisLocus(stem_roles=["bass", "strings"], freq_hz_low=80, freq_hz_high=200),
                reason="masking proxy",
            ),
            MixAnalysisObservation(
                code="stereo_imbalance",
                message="Piano leans left.",
                locus=MixAnalysisLocus(stem_roles=["piano"]),
                reason="stereo balance exceeds threshold",
            ),
        ],
        measurements=[
            MixAnalysisMeasurement(
                code="stereo_lr_rms_balance_db",
                unit="db",
                value=6.0,
                locus=MixAnalysisLocus(stem_roles=["piano"]),
            )
        ],
    )
    settings = load_mix_analysis_settings()
    with get_connection(Path(os.environ["PROJECT_DB_PATH"])) as conn:
        meta = insert_report(conn, settings, report, project_id=project_id)
    section = _preview(
        client,
        project_id,
        stem_set["id"],
        report_id=meta.report_id,
        observation_codes=["section_loudness_flat"],
    )
    assert any("section_loudness_flat" in change["reason"] for change in section["changes"])
    assert not any(change["type"] == "eq" for change in section["changes"])
    stereo = _preview(
        client,
        project_id,
        stem_set["id"],
        report_id=meta.report_id,
        observation_codes=["stereo_imbalance"],
    )
    pan = next(change for change in stereo["changes"] if change["type"] == "pan")
    assert pan["after"] > 0
    assert abs(pan["after"]) <= 0.35


def test_project_delete_removes_mix_plan_files(client: TestClient, mix_plan_env: Path) -> None:
    project_id, stem_set = _project_and_stems(client)
    preview = _preview(client, project_id, stem_set["id"], phrase="make bass less dominant")
    client.post(
        "/mix-plan/apply",
        json={"preview_id": preview["preview_id"], "digest": preview["digest"], "plan": preview["plan"]},
    )
    root = Path(os.environ["MIX_PLAN_ROOT"]) / project_id
    assert root.is_dir()
    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 204, deleted.text
    assert not root.exists()
