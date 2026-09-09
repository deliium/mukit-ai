import copy
import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV1, CompositionV2
from app.services.composition_migration import CompositionMigrationError, migrate_v1_to_v2
from app.services.composition_normalizer import normalize_composition_json
from tests.test_composition_schema import valid_composition


FIXTURES = Path(__file__).parent / "fixtures"


def test_migrate_v1_to_v2_preserves_note_sequence_and_is_source_immutable():
    source_dict = valid_composition()
    source_dict["tracks"][0]["events"][0]["id"] = "note-a"
    original = copy.deepcopy(source_dict)
    v1 = CompositionV1.model_validate(source_dict)

    result = migrate_v1_to_v2(v1)
    v2 = result.composition

    assert source_dict == original
    assert v2.schema_version == "composition.v2"
    assert [track.id for track in v2.tracks] == [track.id for track in v1.tracks]
    assert len(v2.tracks[0].events) == len(v1.tracks[0].events)
    for left, right in zip(v1.tracks[0].events, v2.tracks[0].events, strict=True):
        assert left.id == right.id
        assert left.pitch == right.pitch
        assert left.start_tick == right.start_tick
        assert left.duration_ticks == right.duration_ticks
        assert left.velocity == right.velocity
        assert left.staff == right.staff
        assert left.voice == right.voice
    assert v2.sections[0].id == "section-1"
    assert v2.tempo_changes == []
    assert v2.motifs == []
    assert v2.tracks[0].expression == 127
    assert result.default_field_counts["motifs"] == 0


def test_migrate_v1_to_v2_never_assigns_motif_event_ids():
    source = valid_composition()
    for event in source["tracks"][0]["events"]:
        event.pop("id", None)
    result = migrate_v1_to_v2(source)
    assert result.composition.motifs == []
    assert all(event.id is None for track in result.composition.tracks for event in track.events)


def test_migrate_v1_to_v2_is_idempotent_via_normalizer():
    first = normalize_composition_json(valid_composition())
    second = normalize_composition_json(first.model_dump(mode="json"))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert normalize_composition_json(first) is first


def test_migrate_golden_fixture_note_equality():
    raw = json.loads((FIXTURES / "composition_v1_minimal.json").read_text())
    v1 = CompositionV1.model_validate(raw)
    v2 = migrate_v1_to_v2(raw).composition
    assert isinstance(v2, CompositionV2)
    for track_v1, track_v2 in zip(v1.tracks, v2.tracks, strict=True):
        assert track_v1.id == track_v2.id
        for event_v1, event_v2 in zip(track_v1.events, track_v2.events, strict=True):
            assert (
                event_v1.pitch,
                event_v1.start_tick,
                event_v1.duration_ticks,
                event_v1.velocity,
                event_v1.staff,
                event_v1.voice,
                event_v1.id,
            ) == (
                event_v2.pitch,
                event_v2.start_tick,
                event_v2.duration_ticks,
                event_v2.velocity,
                event_v2.staff,
                event_v2.voice,
                event_v2.id,
            )


def test_fidelity_gate_rejects_mutated_projection(monkeypatch):
    v1 = CompositionV1.model_validate(valid_composition())

    def broken_project(composition):
        payload = {
            "schema_version": "composition.v1",
            "tempo": composition.tempo,
            "key": composition.key,
            "time_signature": composition.time_signature,
            "ticks_per_quarter": composition.ticks_per_quarter,
            "duration_ticks": composition.duration_ticks,
            "bar_count": composition.bar_count,
            "sections": [],
            "tracks": [],
            "harmony": [],
        }
        return payload

    monkeypatch.setattr(
        "app.services.composition_migration._project_v2_onto_v1_fields",
        broken_project,
    )
    with pytest.raises(CompositionMigrationError) as exc:
        migrate_v1_to_v2(v1)
    assert exc.value.code == "v1_v2_migration_fidelity_failed"
