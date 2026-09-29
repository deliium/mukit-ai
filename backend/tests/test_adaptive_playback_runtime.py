"""In-memory playback session registry."""

from __future__ import annotations

from app.adaptive_score_schemas import AdaptiveScoreV1
from app.services.adaptive_playback import PlaybackClock, PlaybackInputs
from app.services.adaptive_playback_runtime import AdaptivePlaybackRegistry
from app.services.composition_timeline import CompiledTimeline


def _held(playback_id: str) -> tuple[PlaybackClock, PlaybackInputs]:
    score = AdaptiveScoreV1.model_validate(
        {
            "schema_version": "adaptive.score.v1",
            "name": "slot",
            "initial_state_id": "state-a",
            "default_state_id": "state-a",
            "states": [
                {
                    "id": "state-a",
                    "name": "A",
                    "intensity": 0,
                    "material": {"kind": "bar_range", "start_bar": 1, "end_bar": 1},
                }
            ],
        }
    )
    timeline = CompiledTimeline(
        ticks_per_quarter=480,
        duration_ticks=1920,
        bar_count=1,
        bar_boundaries=(0, 1920),
        root_tempo=120,
        root_time_signature="4/4",
        root_key="C major",
        tempo_changes=(),
        time_signature_changes=(),
        key_changes=(),
    )
    clock = PlaybackClock(
        playback_id=playback_id,
        mode="simulation",
        document_revision=1,
        runtime_state_id="state-a",
    )
    return clock, PlaybackInputs(score=score, timeline=timeline, sections=(), markers=(), layers=())


def test_two_starts_leave_one_session_and_another_score_keeps_its_own() -> None:
    from app.services.adaptive_playback_runtime import HeldPlayback

    registry = AdaptivePlaybackRegistry()
    first_clock, inputs = _held("pbr_aaaaaaaa")
    second_clock, _ = _held("pbr_bbbbbbbb")
    other_clock, other_inputs = _held("pbr_cccccccc")
    assert registry.put("project-1", "score-1", HeldPlayback(first_clock, inputs)) is None
    replaced = registry.put("project-1", "score-1", HeldPlayback(second_clock, inputs))
    assert replaced == "pbr_aaaaaaaa"
    registry.put("project-1", "score-2", HeldPlayback(other_clock, other_inputs))
    current = registry.get("project-1", "score-1")
    assert current is not None
    assert current.clock.playback_id == "pbr_bbbbbbbb"
    assert registry.get("project-1", "score-2") is not None
    assert registry.clear("project-1", "score-1") == "pbr_bbbbbbbb"
    assert registry.get("project-1", "score-1") is None
    assert registry.get("project-1", "score-2") is not None
