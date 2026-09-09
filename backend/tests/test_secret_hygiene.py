"""Secret-exposure acceptance checks across health, LLM discovery, projects, and config files."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import reset_database_initialization_cache
from app.main import app, get_llm_models, readiness_check
from tests.fixtures.load_fixture import load_minimal
from tests.secret_hygiene import assert_no_secret_leakage


ROOT = Path(__file__).resolve().parents[2]


def test_ready_and_models_omit_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "sec.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-this-must-never-leak-1234567890")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reset_database_initialization_cache()

    ready = asyncio.run(readiness_check())
    assert_no_secret_leakage(ready, context="/ready")
    assert "sk-this-must-never-leak" not in str(ready)

    models = asyncio.run(get_llm_models())
    assert_no_secret_leakage(models.model_dump(mode="json"), context="/llm/models")
    assert "sk-this-must-never-leak" not in models.model_dump_json()


def test_project_crud_omits_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "proj.db"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-project-leak-check-abcdefghij")
    reset_database_initialization_cache()

    client = TestClient(app)
    created = client.post(
        "/projects",
        json={
            "name": "Secret Check",
            "generation": {
                "provider": "fake",
                "model": "fake-deterministic",
                "prompt": {"genre": "ambient", "mood": "calm", "instruments": ["piano"]},
            },
            "composition": load_minimal().model_dump(mode="json"),
        },
    )
    assert created.status_code == 201
    assert_no_secret_leakage(created.json(), context="POST /projects")
    assert "sk-project-leak-check" not in created.text

    listed = client.get("/projects")
    assert listed.status_code == 200
    assert_no_secret_leakage(listed.json(), context="GET /projects")

    opened = client.get(f"/projects/{created.json()['id']}")
    assert opened.status_code == 200
    assert_no_secret_leakage(opened.json(), context="GET /projects/{id}")


def test_env_example_and_compose_have_no_literal_keys():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert_no_secret_leakage(env_example, context=".env.example")
    assert_no_secret_leakage(compose, context="docker-compose.yml")
    # Placeholders / empty assignments are fine; sk- literals are not.
    assert "sk-" not in env_example
    assert "sk-" not in compose


def test_import_routes_omit_source_sentinels_from_errors_and_logs(monkeypatch, tmp_path, caplog):
    from tests.fixtures.build_import_fixtures import SENTINEL_FILENAME

    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "import"
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    client = TestClient(app)
    with caplog.at_level("DEBUG"):
        response = client.post(
            "/imports/midi",
            files={
                "file": (
                    SENTINEL_FILENAME,
                    (fixture_dir / "truncated.mid").read_bytes(),
                    "audio/midi",
                )
            },
        )
    assert response.status_code == 422
    assert SENTINEL_FILENAME not in response.text
    assert_no_secret_leakage(response.json(), context="POST /imports/midi error")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert SENTINEL_FILENAME not in joined
    assert "MTrk" not in joined


def test_analysis_route_omits_fixture_sentinels_from_logs_and_errors(caplog):
    from tests.fixtures.analysis import load_analysis_expected_vectors, load_analysis_fixture

    sentinels = load_analysis_expected_vectors()["sentinels"]
    composition = load_analysis_fixture("hygiene_sentinel")
    client = TestClient(app)
    with caplog.at_level("DEBUG"):
        ok = client.post(
            "/analysis/composition",
            json={"composition": composition, "scope": {"kind": "composition"}},
        )
        bad = client.post(
            "/analysis/composition",
            json={
                "composition": {
                    **composition,
                    "duration_ticks": 10,
                },
                "scope": {"kind": "composition"},
            },
        )
    assert ok.status_code == 200, ok.text
    assert bad.status_code == 422
    assert_no_secret_leakage(ok.json(), context="POST /analysis/composition")
    assert_no_secret_leakage(bad.json(), context="POST /analysis/composition error")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert sentinels["joined_pitches"] not in joined
    assert sentinels["label"] not in joined
    assert sentinels["label"] not in ok.text
    assert sentinels["label"] not in bad.text
    assert sentinels["joined_pitches"] not in ok.text
    assert sentinels["joined_pitches"] not in bad.text
    for pitch in sentinels["pitch_sequence"]:
        assert pitch not in joined
        assert pitch not in bad.text
    assert '"events"' not in bad.text
    assert composition["schema_version"] and '"tracks"' not in bad.text


def test_motif_apply_fake_mode_omits_secrets_and_event_arrays(monkeypatch, caplog):
    from tests.test_composition_v2_schema import _motif_definition, _motif_track, minimal_v2

    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-motif-must-never-leak-abcdefghij")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    composition = minimal_v2(
        bar_count=4,
        duration_ticks=7680,
        sections=[
            {
                "id": "verse",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "id": "chorus",
                "type": "chorus",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        tracks=[_motif_track()],
        motifs=[_motif_definition()],
    )
    body = {
        "composition": composition,
        "source": {"motif_id": "motif-a", "occurrence_id": "occ-orig"},
        "destination": {"section_id": "chorus", "track_id": "melody-1", "start_bar": 3},
        "operation": "melodic_variation",
        "parameters": {},
        "variation_strength": 0.5,
        "selection": {"provider": "fake", "model": "fake-deterministic"},
    }

    client = TestClient(app)
    with caplog.at_level("DEBUG"):
        response = client.post("/motifs/apply", json=body)

    assert response.status_code == 200, response.text
    assert_no_secret_leakage(response.json(), context="POST /motifs/apply")
    assert "sk-motif-must-never-leak" not in response.text
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-motif-must-never-leak" not in joined
    assert '"events": [' not in joined
    assert "C4, D4, E4" not in joined
    motifs = response.json()["composition"]["motifs"]
    assert "events" not in str(motifs)
    for motif in motifs:
        assert "notes" not in motif
        for occurrence in motif["occurrences"]:
            assert "notes" not in occurrence
            assert "events" not in occurrence
            assert "pitch" not in occurrence


def test_composition_development_preview_omits_secrets_from_logs(monkeypatch, caplog):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dev-must-never-leak")
    from tests.test_composition_development_patch import _sixteen_bar_a

    body = {
        "composition": _sixteen_bar_a().model_dump(mode="json"),
        "operation": "continue",
        "output_bars": 8,
        "variation_strength": "balanced",
        "candidate_count": 1,
        "instruction": "secret-instruction-should-not-appear-in-logs",
        "selection": {"provider": "fake", "model": "fake-deterministic"},
    }
    client = TestClient(app)
    with caplog.at_level("DEBUG"):
        response = client.post("/composition/development/preview", json=body)
    assert response.status_code == 200, response.text
    assert_no_secret_leakage(response.json(), context="POST /composition/development/preview")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "sk-dev-must-never-leak" not in joined
    assert "secret-instruction-should-not-appear-in-logs" not in joined
