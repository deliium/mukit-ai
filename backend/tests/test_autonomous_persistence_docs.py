"""The persistence page must keep the stage-branch contract next to history."""

from pathlib import Path

_DOC = Path(__file__).resolve().parents[2] / "docs" / "project-persistence.md"


def test_stage_branch_uses_existing_route_and_leaves_the_run() -> None:
    text = _DOC.read_text(encoding="utf-8")
    assert "POST /projects/{id}/branches" in text
    assert "does not check out" in text
    assert "branch_id" in text
