"""Deterministic creative brief → project plan compiler."""

from __future__ import annotations

import logging

import pytest

from app.autonomous_composer_schemas import (
    AUTONOMOUS_CONSTRAINT_FAILED,
    AUTONOMOUS_INSTRUMENT_UNKNOWN,
    AutonomousPlanError,
    CreativeBriefV1,
    NarrativeBeat,
    STAGE_IDS,
)
from app.autonomous_composer_settings import load_autonomous_composer_settings
from app.services.autonomous_project_plan import compile_project_plan, merge_director_text


def _example_brief() -> CreativeBriefV1:
    return CreativeBriefV1(
        schema_version="creative.brief.v1",
        title="Cinematic",
        duration_seconds=150,
        narrative=[
            NarrativeBeat(intent="sparse_opening", text="cold sparse opening"),
            NarrativeBeat(intent="establish_theme", text="introduce Theme A"),
            NarrativeBeat(intent="build", text="increase tension"),
            NarrativeBeat(intent="climax", text="strong climax"),
            NarrativeBeat(intent="resolve", text="quiet transformed ending"),
        ],
        instrumentation=["piano", "cello", "strings"],
        forbidden_instrument_families=["drums"],
        opening_key="F# minor",
        final_section_key="F# major",
        motif_label="Theme A",
        motif_must_remain_recognizable=True,
    )


def test_example_brief_locks_forty_five_bars() -> None:
    plan = compile_project_plan(_example_brief())
    assert plan.constraints.duration_bars == 45
    assert plan.constraints.opening_tempo == 72
    assert plan.constraints.tempo_min == 60
    assert plan.constraints.tempo_max == 84
    assert plan.constraints.instruments == ["piano", "cello", "strings"]
    assert plan.constraints.opening_key == "F# minor"
    assert plan.constraints.motif_section_id == "sec-2"
    counts = [section.bar_count for section in plan.sections]
    assert counts == [7, 9, 9, 11, 9]
    assert [section.type for section in plan.sections] == [
        "intro",
        "verse",
        "bridge",
        "chorus",
        "outro",
    ]
    assert [section.start_bar for section in plan.sections] == [1, 8, 17, 26, 37]
    assert plan.sections[-1].key == "F# major"
    assert plan.sections[0].key == "F# minor"
    assert [stage.stage_id for stage in plan.stages] == list(STAGE_IDS)
    assert "final_section_mode_ok" in plan.stages[3].completion_codes
    assert sum(section.bar_count for section in plan.sections) == 45


def test_explicit_tempo_min_drives_bar_math() -> None:
    brief = _example_brief().model_copy(update={"tempo_min": 60, "tempo_max": 60})
    plan = compile_project_plan(brief)
    assert plan.constraints.opening_tempo == 60
    assert plan.constraints.duration_bars == 38


def test_merge_keeps_structure_when_director_drops_symbolic() -> None:
    compiled = compile_project_plan(_example_brief())
    director = compiled.model_dump()
    director["goals"][1]["summary"] = "Theme A arrives in the piano"
    director["sections"][4]["narrative"] = "the ending turns toward major"
    director["stages"] = [stage for stage in director["stages"] if stage["stage_id"] != "symbolic"]
    director["constraints"]["opening_key"] = "C major"
    director["sections"][0]["bar_count"] = 1
    merged = merge_director_text(compiled, director)
    assert [stage.stage_id for stage in merged.stages] == list(STAGE_IDS)
    assert merged.goals[1].summary == "Theme A arrives in the piano"
    assert merged.sections[4].narrative == "the ending turns toward major"
    assert merged.constraints.opening_key == "F# minor"
    assert merged.sections[0].bar_count == 7
    assert merged.sections[0].start_bar == compiled.sections[0].start_bar


def test_merge_logs_discarded_field_name_only(caplog: pytest.LogCaptureFixture) -> None:
    compiled = compile_project_plan(_example_brief())
    director = compiled.model_dump()
    director["stages"] = director["stages"][:3]
    caplog.set_level(logging.DEBUG)
    merge_director_text(compiled, director)
    discarded = [
        record
        for record in caplog.records
        if record.getMessage() == "Director structural field discarded"
    ]
    assert discarded
    assert all(getattr(record, "field", None) == "stages" for record in discarded)
    for record in caplog.records:
        assert "cold sparse opening" not in record.getMessage()
        assert "quiet transformed" not in record.getMessage()


def test_unknown_instrument_fails() -> None:
    brief = _example_brief().model_copy(update={"instrumentation": ["xylophone"]})
    with pytest.raises(AutonomousPlanError) as caught:
        compile_project_plan(brief)
    assert caught.value.code == AUTONOMOUS_INSTRUMENT_UNKNOWN


def test_forbidden_family_alias_fails() -> None:
    brief = _example_brief().model_copy(update={"instrumentation": ["piano", "drum kit"]})
    with pytest.raises(AutonomousPlanError) as caught:
        compile_project_plan(brief)
    assert caught.value.code == AUTONOMOUS_CONSTRAINT_FAILED


def test_playable_keys_rejected() -> None:
    payload = _example_brief().model_dump()
    payload["tracks"] = []
    with pytest.raises(Exception):
        CreativeBriefV1.model_validate(payload)


def test_settings_invalid_env_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    settings = load_autonomous_composer_settings(
        {
            "AUTONOMOUS_MAX_AGENT_OPERATIONS": "nope",
            "AUTONOMOUS_MAX_RUNS_PER_PROJECT": "0",
        }
    )
    assert settings.max_agent_operations == 24
    assert settings.max_runs_per_project == 20
    warned = {getattr(record, "env_key", None) for record in caplog.records}
    assert "AUTONOMOUS_MAX_AGENT_OPERATIONS" in warned
    assert "AUTONOMOUS_MAX_RUNS_PER_PROJECT" in warned


def test_zero_agent_operations_disables_ceiling() -> None:
    settings = load_autonomous_composer_settings({"AUTONOMOUS_MAX_AGENT_OPERATIONS": "0"})
    assert settings.max_agent_operations == 0
