"""Capture edges beside reuse and history commits."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import struct
import wave
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from app.composition_schemas import CompositionV2
from app.db.connection import get_connection, reset_database_initialization_cache
from app.db import initialize_database
from app.musical_dependency_schemas import MusicalDependencyError
from app.project_history_schemas import AiProvenance, ApplyAsBranchRequest, DurableCommitRequest, RevisionOperationType
from app.services import musical_dependency_store as edge_store
from app.services import project_history as history
from app.services.composition_snapshot_encoding import motif_occurrence_fingerprint
from app.services.musical_dependency_impact import current_upstream_fingerprint
from app.services.musical_universe_store import add_member, get_universe
from app.services.project_store import create_project, get_project, update_project
from tests.test_musical_universe_bind import vector_a_score, vector_b_score
from tests.test_musical_universe_reuse import _franchise, _note_rows, _reuse


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    reset_database_initialization_cache()
    initialize_database()
    return db_path


def _edges(db_path: Path) -> list:
    with get_connection(db_path) as conn:
        return edge_store.list_dependency_edges(conn)


def _state(db_path: Path, project_id: str) -> tuple[object, dict]:
    record = get_project(project_id, db_path=db_path)
    with get_connection(db_path) as conn:
        branch = conn.execute(
            "SELECT * FROM project_branches WHERE id = ?",
            (record.active_branch_id,),
        ).fetchone()
    return record, dict(branch)


def test_vector_a_reuse_records_one_edge_per_destination(project_db: Path) -> None:
    record = _franchise(project_db)
    create_project("Cue C", project_id="project-c", composition=vector_b_score(), db_path=project_db)
    create_project("Cue D", project_id="project-d", composition=vector_b_score(), db_path=project_db)
    add_member(record.id, "project-c", db_path=project_db)
    add_member(record.id, "project-d", db_path=project_db)
    theme_id = record.universe.themes[0].id
    revision = record.document_revision
    results = []
    for project_id in ("project-b", "project-c", "project-d"):
        latest = get_universe(record.id, db_path=project_db)
        results.append(
            _reuse(project_db, record.id, theme_id, project_id=project_id, revision=latest.document_revision)
        )
    edges = [edge for edge in _edges(project_db) if edge.dependency_type == "variation_of"]
    assert len(edges) == 3
    by_project = {edge.downstream_project_id: edge.variant_id for edge in edges}
    assert by_project == {
        "project-b": results[0].variant_id,
        "project-c": results[1].variant_id,
        "project-d": results[2].variant_id,
    }
    for project_id in ("project-b", "project-c", "project-d"):
        rows = _note_rows(CompositionV2.model_validate(json.loads(get_project(project_id, db_path=project_db).composition_json)))
        assert ("G4", 0, 480) in rows
        assert ("D4", 1920, 480) in rows
        assert ("F#4", 2880, 480) in rows
    assert _note_rows(CompositionV2.model_validate(json.loads(get_project("project-a", db_path=project_db).composition_json))) == _note_rows(vector_a_score())
    assert revision == record.document_revision


def test_failed_edge_insert_rolls_back_reuse(project_db: Path) -> None:
    record = _franchise(project_db)
    before = get_project("project-b", db_path=project_db).composition_json
    with get_connection(project_db) as conn:
        fingerprint = conn.execute(
            "SELECT working_fingerprint FROM project_branches WHERE project_id = 'project-b'"
        ).fetchone()["working_fingerprint"]

    def _fail(*_args, **_kwargs):
        raise MusicalDependencyError("dependency_cycle", "cycle", http_status=422)

    with patch("app.services.musical_dependency_capture.record_dependency_edge", side_effect=_fail):
        with pytest.raises(MusicalDependencyError) as captured:
            _reuse(
                project_db,
                record.id,
                record.universe.themes[0].id,
                project_id="project-b",
                revision=record.document_revision,
            )
    assert captured.value.code == "dependency_cycle"
    assert get_project("project-b", db_path=project_db).composition_json == before
    with get_connection(project_db) as conn:
        after = conn.execute(
            "SELECT working_fingerprint FROM project_branches WHERE project_id = 'project-b'"
        ).fetchone()["working_fingerprint"]
    assert after == fingerprint
    reloaded = get_universe(record.id, db_path=project_db)
    assert reloaded.universe.themes[0].usages == []
    assert _edges(project_db) == []


def test_arrangement_and_branch_edges_stay_fresh_after_unrelated_edit(project_db: Path) -> None:
    score = vector_b_score()
    created = create_project("Cue B", project_id="project-b", composition=score, db_path=project_db)
    other = create_project("Cue A", project_id="project-a", composition=vector_a_score(), db_path=project_db)
    changed = score.model_copy(deep=True)
    changed.tracks[0].events[0].velocity = 90
    record, branch = _state(project_db, created.id)
    history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=changed,
            operation_type=RevisionOperationType.ARRANGEMENT_APPLY,
        ),
        db_path=project_db,
    )
    arranged = [edge for edge in _edges(project_db) if edge.dependency_type == "arrangement_of"]
    assert len(arranged) == 1
    stored = get_project(created.id, db_path=project_db)
    assert json.loads(stored.composition_json)["tracks"][0]["events"][0]["velocity"] == 90
    assert get_project(other.id, db_path=project_db).composition_json

    branched = changed.model_copy(deep=True)
    branched.tracks[0].events[0].velocity = 70
    record, branch = _state(project_db, created.id)
    history.apply_as_branch_command(
        created.id,
        ApplyAsBranchRequest(
            name="Arranged",
            source_branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=branched,
            operation_type=RevisionOperationType.ARRANGEMENT_APPLY,
        ),
        db_path=project_db,
    )
    assert len([edge for edge in _edges(project_db) if edge.dependency_type == "arrangement_of"]) == 2
    edited = branched.model_copy(deep=True)
    edited.tracks[0].events[0].pitch = "A4"
    update_project(created.id, composition=edited, db_path=project_db)
    with get_connection(project_db) as conn:
        for edge in _edges(project_db):
            if edge.dependency_type != "arrangement_of":
                continue
            live = current_upstream_fingerprint(
                conn,
                edge,
                theme_source_project_id=None,
                db_path=project_db,
            )
            assert live is not None


def test_motif_edge_uses_occurrence_fingerprint(project_db: Path) -> None:
    score = vector_a_score()
    created = create_project("Cue A", project_id="project-a", composition=score, db_path=project_db)
    derived = score.model_dump(mode="json")
    derived["motifs"][0]["occurrences"].append(
        {
            "id": "occ_answer",
            "track_id": "track_melody",
            "event_ids": ["a1", "a2", "a3", "a4"],
            "relationship": "transpose",
            "transform": {
                "operation": "transpose",
                "source_occurrence_id": "occ_original",
                "transpose_semitones": 2,
            },
        }
    )
    record, branch = _state(project_db, created.id)
    history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=CompositionV2.model_validate(derived),
            operation_type=RevisionOperationType.CREATIVE_MOTIF_APPLY,
        ),
        db_path=project_db,
    )
    edges = _edges(project_db)
    assert len(edges) == 1
    assert edges[0].dependency_type == "motif_derived_from"
    source_events = score.tracks[0].events
    assert edges[0].upstream_fingerprint == motif_occurrence_fingerprint(source_events)

    extra = deepcopy(derived)
    extra["tracks"][0]["events"].append(
        {
            "id": "z9",
            "type": "note",
            "pitch": "G4",
            "start_tick": 3840,
            "duration_ticks": 480,
            "velocity": 80,
        }
    )
    update_project(created.id, composition=extra, db_path=project_db)
    with get_connection(project_db) as conn:
        fresh = current_upstream_fingerprint(conn, edges[0], theme_source_project_id=None, db_path=project_db)
    assert fresh == edges[0].upstream_fingerprint

    extra["tracks"][0]["events"][3]["pitch"] = "G4"
    update_project(created.id, composition=extra, db_path=project_db)
    with get_connection(project_db) as conn:
        stale = current_upstream_fingerprint(conn, edges[0], theme_source_project_id=None, db_path=project_db)
    assert stale != edges[0].upstream_fingerprint


def test_vary_section_records_variation_and_continue_does_not(project_db: Path) -> None:
    score = vector_b_score()
    created = create_project("Cue B", project_id="project-b", composition=score, db_path=project_db)
    continued = score.model_copy(deep=True)
    continued.tracks[0].events[0].velocity = 60
    record, branch = _state(project_db, created.id)
    history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=continued,
            operation_type=RevisionOperationType.DEVELOPMENT_APPLY,
            ai=AiProvenance(operation="continue"),
        ),
        db_path=project_db,
    )
    assert [edge for edge in _edges(project_db) if edge.dependency_type == "variation_of"] == []

    varied = continued.model_copy(deep=True)
    varied.tracks[0].events[0].velocity = 40
    record, branch = _state(project_db, created.id)
    history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch["id"],
            expected_active_branch_id=branch["id"],
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=varied,
            operation_type=RevisionOperationType.DEVELOPMENT_APPLY,
            ai=AiProvenance(operation="vary_section"),
        ),
        db_path=project_db,
    )
    variations = [edge for edge in _edges(project_db) if edge.dependency_type == "variation_of"]
    assert len(variations) == 1
    assert variations[0].universe_id is None
    assert variations[0].upstream_kind == "revision"


def _tiny_wav() -> bytes:
    sample_rate = 22050
    count = sample_rate // 2
    samples = [int(8000 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(count)]
    buffer = io.BytesIO()
    with wave.open(buffer, "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<" + "h" * count, *samples))
    return buffer.getvalue()


def test_vector_e_render_edge_survives_note_edit_and_refreshes(
    project_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.ai_runtime.registry import reload_registry
    from app.ai_runtime.runtimes.fake_neural_audio import FAKE_NEURAL_AUDIO_MODEL_ID
    from app.neural_audio_schemas import NeuralAudioEnqueueRequest, NeuralAudioStemEnqueueRequest
    from app.services.musical_dependency_capture import capture_rendered_edge
    from app.services.neural_audio_render import enqueue_neural_audio_render
    from app.services.neural_audio_stems import enqueue_stem_set

    monkeypatch.setenv("NEURAL_AUDIO_RENDER_ROOT", str(tmp_path / "renders"))
    monkeypatch.setenv("NEURAL_AUDIO_FAKE_MODE", "1")
    monkeypatch.setenv("NEURAL_AUDIO_ENGINE", "fake:neural-audio")
    monkeypatch.setenv("AI_OP_AUDIO_RENDER", FAKE_NEURAL_AUDIO_MODEL_ID)
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    reload_registry()

    score = vector_b_score()
    created = create_project("Cue B", project_id="project-b", composition=score, db_path=project_db)
    job = enqueue_neural_audio_render(
        NeuralAudioEnqueueRequest(
            project_id=created.id,
            composition=score.model_dump(mode="json"),
            model_id=FAKE_NEURAL_AUDIO_MODEL_ID,
            seed=1,
        ),
        db_path=project_db,
    )
    assert job.status == "complete"
    rendered = [edge for edge in _edges(project_db) if edge.dependency_type == "rendered_from"]
    assert len(rendered) == 1
    assert rendered[0].downstream_asset_id == job.id
    assert rendered[0].upstream_fingerprint == job.source_fingerprint
    first_id = rendered[0].id

    edited = score.model_copy(deep=True)
    edited.tracks[0].events[0].pitch = "A4"
    update_project(created.id, composition=edited, db_path=project_db)
    with get_connection(project_db) as conn:
        job_count = conn.execute("SELECT COUNT(*) AS n FROM neural_audio_renders").fetchone()["n"]
        status = conn.execute(
            "SELECT status FROM neural_audio_renders WHERE id = ?",
            (job.id,),
        ).fetchone()["status"]
    assert job_count == 1
    assert status == "complete"
    assert len([edge for edge in _edges(project_db) if edge.dependency_type == "rendered_from"]) == 1
    stored = CompositionV2.model_validate(json.loads(get_project(created.id, db_path=project_db).composition_json))
    assert stored.tracks[0].events[0].pitch == "A4"

    refreshed = "ab" * 32
    with get_connection(project_db) as conn:
        conn.execute(
            "UPDATE neural_audio_renders SET source_fingerprint = ? WHERE id = ?",
            (refreshed, job.id),
        )
        capture_rendered_edge(
            conn,
            project_id=created.id,
            asset_id=job.id,
            downstream_kind="neural_render",
            source_fingerprint=refreshed,
        )
    again = [edge for edge in _edges(project_db) if edge.downstream_asset_id == job.id]
    assert len(again) == 1
    assert again[0].id == first_id
    assert again[0].upstream_fingerprint == refreshed

    stem = enqueue_stem_set(
        NeuralAudioStemEnqueueRequest(
            project_id=created.id,
            composition=score.model_dump(mode="json"),
            model_id=FAKE_NEURAL_AUDIO_MODEL_ID,
            stem_roles=["strings"],
            seed=2,
        ),
        db_path=project_db,
    )
    assert stem.status == "complete"
    stem_edges = [
        edge
        for edge in _edges(project_db)
        if edge.downstream_kind == "neural_stem_set" and edge.downstream_asset_id == stem.id
    ]
    assert len(stem_edges) == 1
    stem_id = stem_edges[0].id
    stem_refresh = "cd" * 32
    with get_connection(project_db) as conn:
        conn.execute(
            "UPDATE neural_audio_stem_sets SET source_fingerprint = ? WHERE id = ?",
            (stem_refresh, stem.id),
        )
        capture_rendered_edge(
            conn,
            project_id=created.id,
            asset_id=stem.id,
            downstream_kind="neural_stem_set",
            source_fingerprint=stem_refresh,
        )
    stem_again = [
        edge for edge in _edges(project_db) if edge.downstream_asset_id == stem.id
    ]
    assert len(stem_again) == 1
    assert stem_again[0].id == stem_id
    assert stem_again[0].upstream_fingerprint == stem_refresh

    before_count = len(_edges(project_db))
    with caplog.at_level(logging.WARNING):
        with get_connection(project_db) as conn:
            capture_rendered_edge(
                conn,
                project_id=created.id,
                asset_id=job.id,
                downstream_kind="neural_render",
                source_fingerprint="not-hex",
            )
            status = conn.execute(
                "SELECT status FROM neural_audio_renders WHERE id = ?",
                (job.id,),
            ).fetchone()["status"]
    assert status == "complete"
    assert len(_edges(project_db)) == before_count
    assert again[0].upstream_fingerprint == refreshed
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert any(getattr(record, "code", None) == "dependency_fingerprint_unusable" for record in warnings)
    assert any(getattr(record, "job_id", None) == job.id for record in warnings)
    assert "A4" not in " ".join(record.getMessage() for record in warnings)


def test_vector_f_bind_hashes_source_payload(project_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.audio_recovery_schemas import AudioRecoveryBindRequestV1
    from app.services.audio_recovery.pipeline import bind_audio_recovery_job, enqueue_audio_recovery_job

    monkeypatch.setenv("AUDIO_RECOVERY_ASSET_ROOT", str(tmp_path / "recovery"))
    monkeypatch.setenv("AUDIO_RECOVERY_FAKE_MODE", "1")
    monkeypatch.setenv("AUDIO_RECOVERY_SEPARATION_ENGINE", "fake:stems")
    monkeypatch.setenv("AUDIO_RECOVERY_ENGINE", "fake:audio-recovery")

    created = create_project("Cue B", project_id="project-b", composition=vector_b_score(), db_path=project_db)
    payload = _tiny_wav()
    job = enqueue_audio_recovery_job(
        payload,
        display_filename="tiny.wav",
        project_id=created.id,
        run_inline=True,
        db_path=project_db,
    )
    assert job.preview is not None
    notes = job.preview.notes[:1]
    result = bind_audio_recovery_job(
        job.id,
        AudioRecoveryBindRequestV1(
            project_id=created.id,
            preview_fingerprint=job.preview.preview_fingerprint,
            event_map=[
                {
                    "provisional_id": notes[0].provisional_id,
                    "event_id": "b0",
                    "track_id": "track_melody",
                }
            ],
        ),
        db_path=project_db,
    )
    edges = [edge for edge in _edges(project_db) if edge.dependency_type == "transcribed_from"]
    assert len(edges) == 1
    digest = hashlib.sha256(payload).hexdigest()
    assert edges[0].upstream_fingerprint == digest
    assert edges[0].downstream_asset_id == result.source_audio_asset_id
    with get_connection(project_db) as conn:
        prefix = conn.execute(
            "SELECT sha256_prefix FROM audio_recovery_assets WHERE id = ?",
            (result.source_audio_asset_id,),
        ).fetchone()["sha256_prefix"]
    assert edges[0].upstream_fingerprint != prefix
    assert "pitch" not in edges[0].model_dump()


def test_vector_g_reference_edge_does_not_rewrite_conditioned_score(
    project_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    reference = create_project("Cue A", project_id="project-a", composition=vector_a_score(), db_path=project_db)
    conditioned = create_project("Cue B", project_id="project-b", composition=vector_b_score(), db_path=project_db)
    before_ids = [
        event.id
        for event in CompositionV2.model_validate(
            json.loads(get_project(conditioned.id, db_path=project_db).composition_json)
        ).tracks[0].events
    ]
    changed = vector_b_score().model_copy(deep=True)
    changed.tracks[0].events[0].velocity = 70
    record, branch = _state(project_db, conditioned.id)
    with caplog.at_level(logging.WARNING):
        committed = history.commit_revision(
            conditioned.id,
            DurableCommitRequest(
                branch_id=branch["id"],
                expected_active_branch_id=branch["id"],
                expected_working_version=int(branch["working_version"]),
                expected_head_revision_id=record.current_revision_id,
                expected_source_fingerprint=branch["working_fingerprint"],
                composition=changed,
                operation_type=RevisionOperationType.GENERATE_APPLY,
                ai=AiProvenance(
                    operation="generate",
                    user_instruction="do not log this prompt",
                    generation_parameters={
                        "reference_project_id": reference.id,
                        "reference_features": [{"project_id": "missing-ref"}],
                    },
                ),
            ),
            db_path=project_db,
        )
    assert committed.current_revision_id
    edges = [edge for edge in _edges(project_db) if edge.dependency_type == "reference_conditioned_by"]
    assert len(edges) == 1
    assert edges[0].upstream_project_id == reference.id
    assert edges[0].downstream_project_id == conditioned.id
    after = CompositionV2.model_validate(json.loads(get_project(conditioned.id, db_path=project_db).composition_json))
    assert [event.id for event in after.tracks[0].events] == before_ids

    drifted = vector_a_score().model_copy(deep=True)
    drifted.tracks[0].events[3].pitch = "G4"
    update_project(reference.id, composition=drifted, db_path=project_db)
    after_edit = CompositionV2.model_validate(
        json.loads(get_project(conditioned.id, db_path=project_db).composition_json)
    )
    assert [event.id for event in after_edit.tracks[0].events] == before_ids
    assert after_edit.tracks[0].events[0].pitch == "G4"
    warnings = " ".join(record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING)
    extras = [
        record
        for record in caplog.records
        if getattr(record, "code", None) == "dependency_reference_unresolved"
    ]
    assert extras
    assert extras[0].reference_project_id == "missing-ref"
    assert "do not log this prompt" not in warnings
