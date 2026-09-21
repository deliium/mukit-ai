"""Tests for progressive realize trust boundary."""

from __future__ import annotations

import pytest

from app.ai_agents.errors import WorkingDraftInvalidError
from app.ai_agents.progressive_realize import (
    RealizeService,
    apply_realized_composition,
    ensure_playable_source_for_realize,
    initial_working_draft,
    reject_metadata_only_realize,
)
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from tests.test_composition_v2_schema import minimal_v2


def _v2_with_notes(**overrides) -> CompositionV2:
    data = minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ]
    )
    data.update(overrides)
    return CompositionV2.model_validate(data)


def test_initial_working_draft_is_deep_copy():
    source = _v2_with_notes()
    draft = initial_working_draft(source)
    assert composition_edit_fingerprint(draft) == composition_edit_fingerprint(source)
    assert draft is not source
    assert draft.tracks is not source.tracks


def test_apply_realized_updates_fingerprint():
    draft = initial_working_draft(_v2_with_notes())
    before = composition_edit_fingerprint(draft)
    realized = _v2_with_notes(tempo=128)
    updated = apply_realized_composition(
        current_draft=draft,
        realized=realized,
        service=RealizeService.REHARMONIZE_CANDIDATE,
    )
    after = composition_edit_fingerprint(updated)
    assert after != before
    assert updated.tempo == 128
    assert draft.tempo == 100


def test_metadata_only_realize_rejected():
    with pytest.raises(WorkingDraftInvalidError) as exc:
        reject_metadata_only_realize(source="brief", reason="no notes from brief")
    assert exc.value.code == "working_draft_invalid"


def test_unknown_realize_service_rejected():
    draft = initial_working_draft(_v2_with_notes())
    with pytest.raises(WorkingDraftInvalidError):
        apply_realized_composition(
            current_draft=draft,
            realized=_v2_with_notes(),
            service="invent_from_plan",
        )


def test_ensure_playable_rejects_empty_events():
    empty = CompositionV2.model_validate(minimal_v2())
    with pytest.raises(WorkingDraftInvalidError):
        ensure_playable_source_for_realize(empty)
