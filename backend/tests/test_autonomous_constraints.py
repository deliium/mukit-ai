"""Completion codes for plan, harmony, motif, and critique stages."""

from __future__ import annotations

import pytest

from app.autonomous_composer_schemas import CreativeBriefV1
from app.ai_agents.agents.typed_emit import harmony_plan_from_project, motif_plan_from_project
from app.services.autonomous_constraints import AutonomousConstraintError, assert_stage_completion
from app.services.autonomous_project_plan import compile_project_plan, merge_director_text


def _plan():
    brief = CreativeBriefV1.model_validate(
        {
            "schema_version": "creative.brief.v1",
            "duration_seconds": 150,
            "narrative": [
                {"intent": "sparse_opening", "text": "cold sparse opening"},
                {"intent": "establish_theme", "text": "introduce Theme A"},
                {"intent": "build", "text": "increase tension"},
                {"intent": "climax", "text": "strong climax"},
                {"intent": "resolve", "text": "quiet transformed ending"},
            ],
            "instrumentation": ["piano", "cello", "strings"],
            "forbidden_instrument_families": ["drums"],
            "opening_key": "F# minor",
            "final_section_key": "F# major",
            "motif_label": "Theme A",
        }
    )
    return compile_project_plan(brief)


def test_plan_harmony_motif_and_critique_codes_pass() -> None:
    compiled = _plan()
    merged = merge_director_text(compiled, compiled)
    assert_stage_completion("project_plan_valid", plan=compiled, merged_plan=merged)
    assert_stage_completion(
        "harmony_plan_key",
        plan=compiled,
        artifact_payload=harmony_plan_from_project(compiled),
    )
    assert_stage_completion(
        "motif_plan_present",
        plan=compiled,
        artifact_payload=motif_plan_from_project(compiled),
    )
    assert_stage_completion(
        "critique_stored",
        artifact_content_type="agent.critique.v1",
        artifact_id="art-1",
    )


def test_dropped_stage_fails_project_plan_valid() -> None:
    compiled = _plan()
    payload = compiled.model_dump(mode="json")
    payload["stages"] = payload["stages"][1:]
    with pytest.raises(AutonomousConstraintError) as caught:
        assert_stage_completion("project_plan_valid", plan=compiled, merged_plan=payload)
    assert caught.value.completion_code == "project_plan_valid"
    assert caught.value.code == "autonomous_constraint_failed"


def test_wrong_harmony_key_fails() -> None:
    compiled = _plan()
    payload = harmony_plan_from_project(compiled)
    payload["key"] = "C major"
    with pytest.raises(AutonomousConstraintError) as caught:
        assert_stage_completion("harmony_plan_key", plan=compiled, artifact_payload=payload)
    assert caught.value.completion_code == "harmony_plan_key"


def test_motif_missing_theme_section_fails() -> None:
    compiled = _plan()
    payload = motif_plan_from_project(compiled)
    payload["motifs"][0]["section_labels"] = ["not-the-theme"]
    with pytest.raises(AutonomousConstraintError) as caught:
        assert_stage_completion("motif_plan_present", plan=compiled, artifact_payload=payload)
    assert caught.value.completion_code == "motif_plan_present"


def test_critique_without_artifact_id_fails() -> None:
    with pytest.raises(AutonomousConstraintError) as caught:
        assert_stage_completion(
            "critique_stored",
            artifact_content_type="agent.critique.v1",
            artifact_id=None,
        )
    assert caught.value.completion_code == "critique_stored"
