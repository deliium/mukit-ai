"""In-memory pending slot tests."""

from __future__ import annotations

import pytest

from app.adaptive_score_schemas import AdaptiveScoreError, AdaptiveTransitionScheduleV1
from app.services.adaptive_score_transition_pending import TransitionPendingRegistry


def _schedule(request_id: str) -> AdaptiveTransitionScheduleV1:
    return AdaptiveTransitionScheduleV1(
        request_id=request_id,
        project_id="project-1",
        score_id="ascore_0123456789abcdef",
        document_revision=1,
        transition_id="to-combat",
        from_state_id="state-exploration",
        to_state_id="state-combat",
        quantization="bar",
        boundary_tick=3840,
        boundary_bar=3,
        latency_ticks=1440,
        latency_ms=1500,
        tempo_bpm=120,
        time_signature="4/4",
        aligned=True,
        realization={"kind": "cut"},
    )


def test_replace_returns_the_old_id_and_cancel_keeps_the_new_one() -> None:
    registry = TransitionPendingRegistry()
    assert registry.put(_schedule("treq_aaaaaaaa")) is None
    replaced = registry.put(_schedule("treq_bbbbbbbb"))
    assert replaced == "treq_aaaaaaaa"
    current = registry.get("project-1", "ascore_0123456789abcdef")
    assert current is not None
    assert current.request_id == "treq_bbbbbbbb"
    assert current.replaced_request_id == "treq_aaaaaaaa"
    with pytest.raises(AdaptiveScoreError) as captured:
        registry.cancel("project-1", "ascore_0123456789abcdef", "treq_aaaaaaaa")
    assert captured.value.code == "transition_request_not_pending"
    assert registry.get("project-1", "ascore_0123456789abcdef").request_id == "treq_bbbbbbbb"
    registry.cancel("project-1", "ascore_0123456789abcdef", "treq_bbbbbbbb")
    assert registry.get("project-1", "ascore_0123456789abcdef") is None
