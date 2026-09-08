import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.services.composition_timeline import compile_timeline


FIXTURE = Path(__file__).parent / "fixtures" / "timeline_mixed_meter_tempo.json"


def _load_fixture():
    return json.loads(FIXTURE.read_text())


def test_compile_timeline_mixed_meter_and_tempo_golden():
    raw = _load_fixture()
    expectations = raw.pop("expectations")
    composition = CompositionV2.model_validate(raw)
    timeline = compile_timeline(composition)

    assert list(timeline.bar_boundaries) == expectations["bar_boundaries"]
    assert timeline.tick_to_seconds(1920) == pytest.approx(expectations["seconds_at_1920"])
    assert timeline.tick_to_seconds(3360) == pytest.approx(expectations["seconds_at_3360"])
    assert timeline.total_duration_seconds() == pytest.approx(expectations["total_seconds"])
    assert timeline.bar_at_tick(2000) == expectations["bar_at_2000"]
    assert timeline.active_tempo(2000) == expectations["active_tempo_at_2000"]
    assert timeline.active_time_signature(2000) == expectations["active_meter_at_2000"]
    assert timeline.active_key(2000) == expectations["active_key_at_2000"]
    assert timeline.seconds_to_tick(1.5) == pytest.approx(expectations["tick_at_1_5_seconds"])

    # Inverse round-trip at a tempo boundary.
    assert timeline.seconds_to_tick(timeline.tick_to_seconds(1920)) == pytest.approx(1920)
    assert timeline.bar_range_ticks(2, 3) == (1920, 4800)


def test_compile_timeline_rejects_incomplete_final_bar():
    raw = _load_fixture()
    raw.pop("expectations")
    raw["duration_ticks"] = 4700
    with pytest.raises(Exception):
        CompositionV2.model_validate(raw)


def test_notes_and_sections_spanning_meter_changes():
    raw = _load_fixture()
    raw.pop("expectations")
    composition = CompositionV2.model_validate(raw)
    timeline = compile_timeline(composition)
    # Note starting in bar 2 and lasting into bar 3 remains within duration.
    spanning = composition.tracks[0].events[1]
    assert spanning.start_tick < timeline.bar_start_tick(3)
    assert spanning.start_tick + spanning.duration_ticks > timeline.bar_start_tick(2)
    assert composition.sections[1].start_tick == timeline.bar_start_tick(2)
    assert composition.sections[1].duration_ticks == (
        timeline.bar_end_tick(3) - timeline.bar_start_tick(2)
    )
