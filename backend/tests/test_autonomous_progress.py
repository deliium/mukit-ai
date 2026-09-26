"""Musical board rows. No store and no agents."""

from __future__ import annotations

from app.autonomous_composer_schemas import STAGE_IDS
from app.services.autonomous_progress import musical_progress


def _stages(**overrides: str) -> list[dict[str, str]]:
    return [
        {"stage_id": stage_id, "status": overrides.get(stage_id, "pending")}
        for stage_id in STAGE_IDS
    ]


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["step_id"]: row for row in rows}


def test_theme_is_current_only_while_waiting_on_motif() -> None:
    stages = _stages(
        plan="completed",
        harmony_plan="completed",
        motif_plan="completed",
        symbolic="completed",
    )
    waiting = _by_id(
        musical_progress(
            stages,
            "Theme A",
            run_status="awaiting_approval",
            checkpoint_id="motif",
        )
    )
    assert waiting["theme"]["status"] == "current"
    assert waiting["theme"]["label"] == "Theme A"
    assert waiting["harmony"]["status"] == "done"
    assert waiting["composition_plan"]["status"] == "done"
    assert waiting["critique"]["status"] == "pending"

    running = _by_id(musical_progress(stages, "Theme A", run_status="running"))
    assert running["theme"]["status"] == "done"


def test_running_critique_marks_critique_current_and_theme_done() -> None:
    stages = _stages(
        plan="completed",
        harmony_plan="completed",
        motif_plan="completed",
        symbolic="completed",
        critique="running",
    )
    rows = _by_id(musical_progress(stages, "Theme A", run_status="running"))
    assert rows["critique"]["status"] == "current"
    assert rows["theme"]["status"] == "done"
    assert rows["arrangement"]["status"] == "pending"


def test_arrangement_checkpoint_marks_arrangement_current() -> None:
    stages = _stages(
        plan="completed",
        harmony_plan="completed",
        motif_plan="completed",
        symbolic="completed",
        critique="completed",
        revision="skipped",
        arrangement="completed",
    )
    rows = _by_id(
        musical_progress(
            stages,
            "Theme A",
            run_status="awaiting_approval",
            checkpoint_id="arrangement",
        )
    )
    assert rows["critique"]["status"] == "done"
    assert rows["arrangement"]["status"] == "current"
    assert rows["performance"]["status"] == "pending"
    assert [row["label"] for row in musical_progress(stages, "Theme A")] == [
        "Composition plan",
        "Harmony",
        "Theme A",
        "Critique",
        "Arrangement",
        "Final performance",
    ]
