"""Scenarios D and E: recovery bind, harmony preview, stems, mix, export."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.services.neural_audio_render_store import absolute_audio_path, load_settings_and_root
from tests.studio_acceptance.invariants import assert_playable_v2, event_fingerprint
from tests.test_mix_analysis import _multi_stem_composition

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "audio" / "recovery" / "mixed_melody_bass.wav"
_SECRET_SENTINEL = "sk-studio-audio-sentinel"


def _stem_hashes(stem_set: dict) -> dict[str, str]:
    settings = load_settings_and_root()
    hashes: dict[str, str] = {}
    for stem in stem_set["stems"]:
        relpath = stem.get("audio_relpath")
        if not relpath:
            continue
        hashes[stem["id"]] = hashlib.sha256(absolute_audio_path(settings, relpath).read_bytes()).hexdigest()
    return hashes


def test_audio_to_mix_keeps_source_and_stems(
    studio_client: tuple[TestClient, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _db_path = studio_client
    fixture_bytes = _FIXTURE.read_bytes()
    source_sha = hashlib.sha256(fixture_bytes).hexdigest()
    composition = _multi_stem_composition()
    created = client.post("/projects", json={"name": "Audio mix", "composition": composition})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    before = event_fingerprint(created.json()["composition"])

    caplog.set_level(logging.INFO)
    uploaded = client.post(
        "/audio-recovery/jobs",
        data={"project_id": project_id},
        files={"file": ("mixed_melody_bass.wav", fixture_bytes, "audio/wav")},
    )
    assert uploaded.status_code == 200, uploaded.text
    job = uploaded.json()
    assert job["status"] == "complete"
    notes = (job.get("preview") or {}).get("notes") or []
    event_map = [
        {
            "provisional_id": note["provisional_id"],
            "event_id": f"ev-{index}",
            "track_id": "piano-1",
        }
        for index, note in enumerate(notes[:3])
    ]
    bound = client.post(
        f"/audio-recovery/jobs/{job['id']}/bind",
        json={
            "schema_version": "audio.recovery.bind.v1",
            "project_id": project_id,
            "preview_fingerprint": job["preview"]["preview_fingerprint"],
            "event_map": event_map,
        },
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["alignment_asset_id"]
    discovery = client.get(f"/audio-recovery/projects/{project_id}/bound")
    assert discovery.status_code == 200, discovery.text
    assert discovery.json()["bound"] is True
    assert hashlib.sha256(_FIXTURE.read_bytes()).hexdigest() == source_sha

    preview = client.post(
        "/harmony/reharmonize/preview",
        json={
            "composition": composition,
            "selection": {"start_bar": 1, "end_bar": 1},
            "operation": "simplify_harmony",
            "content_policy": "preserve_melody_adapt_harmony",
            "target_track_ids": ["bass-1"],
            "engine": "deterministic",
            "instruction": "leave the stored notes alone",
            "tonal_context": {"allow_modulation": False, "target_key": None, "target_chord": None},
            "selection_options": {"provider": None, "model": None},
        },
    )
    assert preview.status_code == 200, preview.text
    reopened = client.get(f"/projects/{project_id}")
    assert reopened.status_code == 200
    stored = reopened.json()["composition"]
    assert_playable_v2(stored)
    assert event_fingerprint(stored) == before

    stem_set = client.post(
        "/neural-audio/stem-sets",
        json={
            "project_id": project_id,
            "composition": composition,
            "engine": "neural",
            "stem_roles": ["piano", "bass", "strings"],
        },
    )
    assert stem_set.status_code == 200, stem_set.text
    stems = stem_set.json()
    assert stems["status"] == "complete"
    after_stems = client.get(f"/projects/{project_id}")
    assert event_fingerprint(after_stems.json()["composition"]) == before
    assert hashlib.sha256(_FIXTURE.read_bytes()).hexdigest() == source_sha
    hashes = _stem_hashes(stems)

    analyzed = client.post(
        "/mix-analysis/analyze",
        json={"stem_set_id": stems["id"], "include_ai_interpretation": False, "composition": composition},
    )
    assert analyzed.status_code == 200, analyzed.text
    report = analyzed.json()["report"]
    assert report["schema_version"] == "mix.analysis.v1"
    assert report["dsp_backend"] == "fake"

    mix_preview = client.post(
        "/mix-plan/preview",
        json={
            "project_id": project_id,
            "stem_set_id": stems["id"],
            "phrase": "make bass less dominant",
            "include_audio_preview": True,
            "master_target": "dynamic",
        },
    )
    assert mix_preview.status_code == 200, mix_preview.text
    plan = mix_preview.json()
    assert plan["plan"]["guarantee"] is False
    assert plan["plan"]["mutates_stems"] is False
    applied = client.post(
        "/mix-plan/apply",
        json={"preview_id": plan["preview_id"], "digest": plan["digest"], "plan": plan["plan"]},
    )
    assert applied.status_code == 200, applied.text
    revision = applied.json()["revision"]
    audio = client.get(f"/mix-plan/revisions/{revision['id']}/audio")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    assert _stem_hashes(stems) == hashes

    second = client.post(
        "/mix-plan/preview",
        json={
            "project_id": project_id,
            "stem_set_id": stems["id"],
            "phrase": "make bass less dominant",
            "master_target": "dynamic",
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    applied_again = client.post(
        "/mix-plan/apply",
        json={
            "preview_id": second_body["preview_id"],
            "digest": second_body["digest"],
            "plan": second_body["plan"],
        },
    )
    assert applied_again.status_code == 200, applied_again.text
    second_id = applied_again.json()["revision"]["id"]
    undo = client.post(f"/mix-plan/revisions/{second_id}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["head_revision_id"] == revision["id"]
    assert _stem_hashes(stems) == hashes
    assert event_fingerprint(client.get(f"/projects/{project_id}").json()["composition"]) == before

    midi = client.post("/export/midi", json=composition)
    assert midi.status_code == 200, midi.text
    assert midi.content.startswith(b"MThd")
    wav = client.post("/export/wav", json=composition)
    assert wav.status_code in {200, 503}

    assert _SECRET_SENTINEL not in caplog.text
    assert "RIFF" not in caplog.text
    assert any(getattr(record, "basename", None) == "mixed_melody_bass.wav" for record in caplog.records)
