"""Mix analysis schemas, active-head selection, DSP/API coverage."""

from __future__ import annotations

import hashlib
import os
import struct
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai_runtime.registry import reload_registry
from app.ai_runtime.runtimes.fake_neural_audio import FAKE_NEURAL_AUDIO_MODEL_ID
from app.db.connection import get_connection, reset_database_initialization_cache, run_alembic_upgrade
from app.main import app
from app.mix_analysis_schemas import (
    MixAnalysisAnalyzeRequest,
    MixAnalysisLocus,
    MixAnalysisMeasurement,
    MixAnalysisObservation,
    MixAnalysisReport,
)
from app.mix_analysis_settings import load_mix_analysis_settings
from app.services.mix_analysis.active_stems import select_active_head_stems
from app.services.mix_analysis.observations import build_observations
from app.services.neural_audio_render_store import absolute_audio_path, load_settings_and_root
from tests.test_composition_v2_schema import minimal_v2


def _multi_stem_composition() -> dict:
    return minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "acoustic piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [
                    {"pitch": "C2", "start_tick": 0, "duration_ticks": 480, "velocity": 70}
                ],
            },
            {
                "id": "strings-1",
                "name": "Strings",
                "instrument": "string ensemble",
                "role": "harmony",
                "midi_program": 48,
                "channel": 3,
                "events": [
                    {"pitch": "E4", "start_tick": 0, "duration_ticks": 960, "velocity": 60}
                ],
            },
        ]
    )


def _write_wav(path: Path, *, amplitude: float = 0.5, frames: int = 2205, stereo: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    channels = 2 if stereo else 1
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        samples = []
        for i in range(frames):
            value = int(max(-1.0, min(1.0, amplitude)) * 32767)
            samples.append(value)
            if stereo:
                samples.append(int(value * 0.2))
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


@pytest.fixture
def mix_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db_path = tmp_path / "projects.db"
    render_root = tmp_path / "neural_audio_renders"
    report_root = tmp_path / "mix_analysis_reports"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(render_root))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    monkeypatch.setenv("MIX_ANALYSIS_ROOT", str(report_root))
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "1")
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reset_database_initialization_cache()
    run_alembic_upgrade(db_path)
    reload_registry()
    return tmp_path


@pytest.fixture
def client(mix_env: Path):
    with TestClient(app) as test_client:
        yield test_client


def test_schema_accepts_report_layers() -> None:
    report = MixAnalysisReport.model_validate(
        {
            "stem_set_id": "set-1",
            "source_stem_set_fingerprint": "abc",
            "dsp_backend": "fake",
            "measurements": [
                {
                    "code": "peak_dbfs",
                    "unit": "dbfs",
                    "value": -1.0,
                    "locus": {"stem_ids": ["s1"], "stem_roles": ["bass"]},
                }
            ],
            "observations": [
                {
                    "code": "peak_hot",
                    "severity": "warning",
                    "message": "Bass peak is hot.",
                    "locus": {
                        "stem_ids": ["s1"],
                        "stem_roles": ["bass"],
                        "source_track_ids": ["bass-1"],
                    },
                    "reason": "peak_dbfs above threshold",
                    "evidence": {"measurement_codes": ["peak_dbfs"]},
                }
            ],
            "interpretations": [],
        }
    )
    assert report.schema_version == "mix.analysis.v1"
    assert report.mutates_audio is False
    assert report.observations[0].locus.source_track_ids == ["bass-1"]


def test_schema_rejects_unknown_dimension() -> None:
    with pytest.raises(ValidationError):
        MixAnalysisAnalyzeRequest.model_validate(
            {"stem_set_id": "x", "dimensions": ["not_a_dimension"]}
        )


def test_active_head_ignores_superseded() -> None:
    members = [
        {
            "id": "old",
            "stem_role": "strings",
            "status": "complete",
            "created_at": "2026-09-24T00:00:00Z",
        },
        {
            "id": "new",
            "stem_role": "strings",
            "status": "complete",
            "created_at": "2026-09-24T01:00:00Z",
            "supersedes_stem_id": "old",
        },
        {
            "id": "piano",
            "stem_role": "piano",
            "status": "complete",
            "created_at": "2026-09-24T00:00:00Z",
        },
    ]
    selected = select_active_head_stems(members)
    ids = {r["id"] for r in selected}
    assert "new" in ids
    assert "old" not in ids
    assert "piano" in ids


def test_observations_from_hot_peak() -> None:
    settings = load_mix_analysis_settings({"MIX_ANALYSIS_FAKE_MODE": "1"})
    locus = MixAnalysisLocus(
        stem_ids=["s1"],
        stem_roles=["bass"],
        source_track_ids=["bass-1"],
        start_seconds=0.0,
        end_seconds=2.0,
    )
    measurements = [
        MixAnalysisMeasurement(code="peak_dbfs", unit="dbfs", value=-0.2, locus=locus),
        MixAnalysisMeasurement(code="headroom_db", unit="db", value=0.2, locus=locus),
        MixAnalysisMeasurement(code="clip_ratio", unit="ratio", value=0.002, locus=locus),
    ]
    obs = build_observations(measurements, settings)
    codes = {o.code for o in obs}
    assert "peak_hot" in codes or "clipping_detected" in codes or "low_headroom" in codes
    assert any(o.locus.source_track_ids == ["bass-1"] for o in obs)


def test_migration_creates_mix_analysis_table(mix_env: Path) -> None:
    path = Path(os.environ["PROJECT_DB_PATH"])
    with get_connection(path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='mix_analysis_reports'"
        ).fetchone()
    assert row is not None


def _enqueue_complete_stem_set(client: TestClient) -> dict:
    response = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": _multi_stem_composition(),
            "engine": "neural",
            "stem_roles": ["piano", "bass", "strings"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "complete"
    return body


def test_analyze_fake_mode_emits_observation_with_locus(client: TestClient) -> None:
    stem_set = _enqueue_complete_stem_set(client)
    # Collect sha before
    settings = load_settings_and_root()
    hashes_before = {}
    for stem in stem_set["stems"]:
        if stem.get("audio_relpath"):
            path = absolute_audio_path(settings, stem["audio_relpath"])
            hashes_before[stem["id"]] = hashlib.sha256(path.read_bytes()).hexdigest()

    response = client.post(
        "/mix-analysis/analyze",
        json={
            "stem_set_id": stem_set["id"],
            "include_ai_interpretation": True,
            "composition": _multi_stem_composition(),
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    report = data["report"]
    assert report["schema_version"] == "mix.analysis.v1"
    assert report["mutates_audio"] is False
    assert report["mutates_composition"] is False
    assert report["dsp_backend"] == "fake"
    assert len(report["measurements"]) >= 1
    assert len(report["observations"]) >= 1
    series_kinds = {s["kind"] for s in report.get("series") or []}
    assert "peak_envelope" in series_kinds
    assert "loudness_envelope" in series_kinds
    assert "band_energy" in series_kinds
    obs = report["observations"][0]
    assert obs["locus"]["stem_ids"] or obs["locus"]["stem_roles"]
    assert obs.get("reason")
    # AI interpretations under fake
    assert len(report["interpretations"]) >= 1
    assert report["interpretations"][0]["kind"] == "interpretation"
    cite = report["interpretations"][0].get("cites_observation_codes") or []
    assert cite

    # source_track_ids present when stems carry them
    assert any(
        m.get("locus", {}).get("source_track_ids") for m in report["measurements"]
    )

    # Stems unchanged
    for stem in stem_set["stems"]:
        if stem["id"] in hashes_before:
            path = absolute_audio_path(settings, stem["audio_relpath"])
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            assert after == hashes_before[stem["id"]]


def test_analyze_incomplete_stem_set_422(client: TestClient, mix_env: Path) -> None:
    stem_set = _enqueue_complete_stem_set(client)
    # Mark one stem incomplete in DB
    db_path = Path(os.environ["PROJECT_DB_PATH"])
    with get_connection(db_path) as conn:
        stem_id = stem_set["stems"][0]["id"]
        conn.execute(
            "UPDATE neural_audio_stems SET status = 'running' WHERE id = ?",
            (stem_id,),
        )
        conn.execute(
            "UPDATE neural_audio_stem_sets SET status = 'running' WHERE id = ?",
            (stem_set["id"],),
        )
    response = client.post(
        "/mix-analysis/analyze",
        json={"stem_set_id": stem_set["id"]},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "mix_analysis_stem_set_incomplete"


def test_persist_list_delete_report(client: TestClient) -> None:
    # Need a project for persist
    create = client.post("/projects", json={"name": "mix-analysis-test"})
    assert create.status_code in (200, 201)
    project_id = create.json()["id"]

    stem_set = client.post(
        "/neural-audio/stem-sets",
        json={
            "project_id": project_id,
            "composition": _multi_stem_composition(),
            "engine": "neural",
            "stem_roles": ["piano", "bass"],
        },
    ).json()

    analyze = client.post(
        "/mix-analysis/analyze",
        json={
            "project_id": project_id,
            "stem_set_id": stem_set["id"],
            "persist": True,
        },
    )
    assert analyze.status_code == 200, analyze.text
    assert analyze.json()["persisted"] is True
    report_id = analyze.json()["report"]["report_id"]
    assert report_id

    listed = client.get("/mix-analysis/reports", params={"project_id": project_id})
    assert listed.status_code == 200
    assert any(item["report_id"] == report_id for item in listed.json()["items"])

    got = client.get(f"/mix-analysis/reports/{report_id}")
    assert got.status_code == 200
    assert got.json()["report"]["report_id"] == report_id

    deleted = client.delete(f"/mix-analysis/reports/{report_id}")
    assert deleted.status_code == 200
    missing = client.get(f"/mix-analysis/reports/{report_id}")
    assert missing.status_code == 404


def test_active_head_after_rerender(client: TestClient) -> None:
    composition = _multi_stem_composition()
    stem_set = client.post(
        "/neural-audio/stem-sets",
        json={
            "composition": composition,
            "engine": "neural",
            "stem_roles": ["piano", "bass", "strings"],
        },
    ).json()
    strings = next(s for s in stem_set["stems"] if s["stem_role"] == "strings")
    rerender = client.post(
        f"/neural-audio/stem-sets/{stem_set['id']}/stems/{strings['id']}/rerender",
        json={
            "composition": composition,
            "seed": 99,
            "model_id": FAKE_NEURAL_AUDIO_MODEL_ID,
        },
    )
    assert rerender.status_code == 200, rerender.text
    updated = client.get(f"/neural-audio/stem-sets/{stem_set['id']}").json()
    analyze = client.post(
        "/mix-analysis/analyze",
        json={"stem_set_id": stem_set["id"]},
    )
    assert analyze.status_code == 200
    analyzed_ids = {s["stem_id"] for s in analyze.json()["report"]["analyzed_stems"]}
    # Superseded old strings id should not be analyzed
    superseded = [
        s["id"]
        for s in updated["stems"]
        if s["stem_role"] == "strings"
        and any(
            other.get("supersedes_stem_id") == s["id"]
            for other in updated["stems"]
            if other["status"] == "complete"
        )
    ]
    for old_id in superseded:
        assert old_id not in analyzed_ids
    assert strings["id"] in superseded or strings["id"] not in analyzed_ids


def _fixture_paths() -> dict[str, Path]:
    from tests.fixtures.audio.mix_analysis.builders import ensure_all_fixtures

    return ensure_all_fixtures()


def test_stdlib_metrics_on_hot_peak_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "0")
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis.metrics import measure_stem_file

    hot = _fixture_paths()["hot_peak"]
    settings = load_mix_analysis_settings({})
    result = measure_stem_file(
        hot,
        settings,
        stem_id="s-hot",
        stem_role="bass",
        source_track_ids=["bass-1"],
        dimensions=["peak", "clipping", "headroom", "loudness", "spectral_balance"],
    )
    assert result.dsp_backend in {"stdlib", "numpy_scipy"}
    peak = next(m for m in result.measurements if m.code == "peak_dbfs")
    assert peak.value is not None
    assert peak.value >= -1.0
    assert peak.locus.source_track_ids == ["bass-1"]
    kinds = {s.kind for s in result.series}
    assert "peak_envelope" in kinds
    assert "loudness_envelope" in kinds
    assert "band_energy" in kinds
    band_series = [s for s in result.series if s.kind == "band_energy"]
    assert band_series
    assert any(s.points for s in band_series)
    obs = build_observations(result.measurements, settings)
    assert any(o.code in {"peak_hot", "low_headroom"} for o in obs)
    before = hot.read_bytes()
    measure_stem_file(
        hot, settings, stem_id="s-hot", stem_role="bass", dimensions=["peak"]
    )
    assert hot.read_bytes() == before


def test_stdlib_clipping_fixture_emits_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "0")
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis.metrics import measure_stem_file

    clipped = _fixture_paths()["clipped"]
    settings = load_mix_analysis_settings({})
    before = clipped.read_bytes()
    result = measure_stem_file(
        clipped,
        settings,
        stem_id="s-clip",
        stem_role="bass",
        source_track_ids=["bass-1"],
        dimensions=["clipping", "peak", "headroom"],
    )
    assert clipped.read_bytes() == before
    clip_ratio = next(m for m in result.measurements if m.code == "clip_ratio")
    assert clip_ratio.value is not None
    assert clip_ratio.value >= settings.clip_ratio
    obs = build_observations(result.measurements, settings)
    assert any(o.code == "clipping_detected" for o in obs)
    assert any(o.locus.source_track_ids == ["bass-1"] for o in obs)


def test_stdlib_stereo_imbalance_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "0")
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis.metrics import measure_stem_file

    stereo = _fixture_paths()["stereo_imbalance"]
    settings = load_mix_analysis_settings({})
    before = stereo.read_bytes()
    result = measure_stem_file(
        stereo,
        settings,
        stem_id="s-stereo",
        stem_role="piano",
        source_track_ids=["piano-1"],
        dimensions=["stereo_balance", "peak"],
    )
    assert stereo.read_bytes() == before
    balance = next(m for m in result.measurements if m.code == "stereo_lr_rms_balance_db")
    assert balance.value is not None
    assert abs(balance.value) >= settings.stereo_imbalance_db
    obs = build_observations(result.measurements, settings)
    assert any(o.code == "stereo_imbalance" for o in obs)


def test_stdlib_masking_proxy_two_stem_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "0")
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis.metrics import measure_stem_file, measure_stem_pair_masking

    paths = _fixture_paths()
    bass_path = paths["masking_bass"]
    strings_path = paths["masking_strings"]
    settings = load_mix_analysis_settings({})
    before_bass = bass_path.read_bytes()
    before_strings = strings_path.read_bytes()
    dims = ["spectral_balance", "masking_proxy", "lf_buildup"]
    bass = measure_stem_file(
        bass_path,
        settings,
        stem_id="s-bass",
        stem_role="bass",
        source_track_ids=["bass-1"],
        dimensions=dims,
    )
    strings = measure_stem_file(
        strings_path,
        settings,
        stem_id="s-strings",
        stem_role="strings",
        source_track_ids=["cello-1"],
        dimensions=dims,
    )
    assert bass_path.read_bytes() == before_bass
    assert strings_path.read_bytes() == before_strings
    masking = measure_stem_pair_masking(
        bass,
        strings,
        stem_id_a="s-bass",
        stem_id_b="s-strings",
        role_a="bass",
        role_b="strings",
        tracks_a=["bass-1"],
        tracks_b=["cello-1"],
    )
    assert masking.code == "masking_proxy"
    assert masking.value is not None
    assert masking.value >= settings.masking_proxy
    assert set(masking.locus.stem_roles) == {"bass", "strings"}
    assert "bass-1" in masking.locus.source_track_ids
    assert "cello-1" in masking.locus.source_track_ids
    assert masking.locus.freq_hz_low is not None
    assert masking.locus.freq_hz_high is not None
    obs = build_observations([*bass.measurements, *strings.measurements, masking], settings)
    assert any(o.code == "spectral_masking_proxy" for o in obs)
    mask_obs = next(o for o in obs if o.code == "spectral_masking_proxy")
    assert mask_obs.reason
    assert "masking_proxy" in (mask_obs.evidence.measurement_codes if mask_obs.evidence else [])


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("numpy") is None
    or __import__("importlib").util.find_spec("scipy") is None,
    reason="numpy/scipy extras not installed",
)
def test_numpy_backend_available_when_extras_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIX_ANALYSIS_FAKE_MODE", "0")
    from app.mix_analysis_settings import load_mix_analysis_settings
    from app.services.mix_analysis.decode import detect_dsp_backend
    from app.services.mix_analysis.metrics import measure_stem_file

    assert detect_dsp_backend() == "numpy_scipy"
    path = tmp_path / "x.wav"
    _write_wav(path, amplitude=0.4)
    settings = load_mix_analysis_settings({})
    result = measure_stem_file(
        path, settings, stem_id="s1", stem_role="piano", dimensions=["peak", "loudness"]
    )
    assert result.dsp_backend == "numpy_scipy"
