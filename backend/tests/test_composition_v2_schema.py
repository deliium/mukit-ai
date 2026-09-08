import pytest
from pydantic import ValidationError

from app.composition_schemas import (
    CompositionV2,
    UnsupportedSchemaVersionError,
    collect_ignored_v1_paths,
    parse_composition_document,
)
from app.services.composition_projection import (
    PROJECTION_ISSUE_CODES,
    ProjectionReport,
    empty_projection_report,
)


def minimal_v2(**overrides):
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 2,
        "duration_ticks": 3840,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            }
        ],
        "tracks": [
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def test_composition_v2_accepts_minimal_empty_expressive_defaults(caplog):
    with caplog.at_level("INFO"):
        composition = CompositionV2.model_validate(minimal_v2())

    assert composition.schema_version == "composition.v2"
    assert composition.tempo_changes == []
    assert composition.tracks[0].expression == 127
    assert composition.tracks[0].dynamic_marks == []
    assert composition.tracks[0].sustain_pedals == []
    assert composition.tracks[0].automation == []
    assert "Composition V2 validation completed" in caplog.text


def test_composition_v2_rejects_unknown_root_field():
    data = minimal_v2()
    data["mystery_field"] = True
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(data)


def test_composition_v2_rejects_unknown_schema_version():
    with pytest.raises(UnsupportedSchemaVersionError) as exc:
        parse_composition_document({"schema_version": "composition.v9", "tempo": 120})
    assert exc.value.code == "unsupported_schema_version"


def test_composition_v2_mixed_meter_map_and_section_coverage():
    # 1 bar 4/4 (1920) + 1 bar 3/4 (1440) + 1 bar 6/8 (1440) = 4800
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=3,
            duration_ticks=4800,
            time_signature="4/4",
            time_signature_changes=[
                {"tick": 1920, "time_signature": "3/4"},
                {"tick": 3360, "time_signature": "6/8"},
            ],
            sections=[
                {
                    "id": "sec-a",
                    "type": "intro",
                    "label": "A",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                },
                {
                    "id": "sec-b",
                    "type": "verse",
                    "start_bar": 2,
                    "bar_count": 2,
                    "start_tick": 1920,
                    "duration_ticks": 2880,
                },
            ],
            tracks=[
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "articulations": ["accent"],
                        }
                    ],
                }
            ],
        )
    )
    assert composition.duration_ticks == 4800
    assert composition.sections[1].duration_ticks == 2880


def test_composition_v2_rejects_incomplete_final_bar():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                bar_count=2,
                duration_ticks=3000,
            )
        )


def test_composition_v2_rejects_malformed_tie_chain():
    data = minimal_v2(
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "tie": {"group_id": "t1", "type": "start"},
                    },
                    {
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": 960,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "tie": {"group_id": "t1", "type": "stop"},
                    },
                ],
            }
        ]
    )
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(data)


def test_composition_v2_accepts_contiguous_tie_chain():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "type": "note",
                            "id": "n1",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "articulations": ["accent"],
                            "tie": {"group_id": "t1", "type": "start"},
                        },
                        {
                            "type": "note",
                            "id": "n2",
                            "pitch": "C4",
                            "start_tick": 480,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "tie": {"group_id": "t1", "type": "continue"},
                        },
                        {
                            "type": "note",
                            "id": "n3",
                            "pitch": "C4",
                            "start_tick": 960,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "tie": {"group_id": "t1", "type": "stop"},
                        },
                    ],
                }
            ]
        )
    )
    assert len(composition.tracks[0].events) == 3


def test_composition_v2_rejects_conflicting_articulations():
    data = minimal_v2(
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "articulations": ["staccato", "tenuto"],
                    }
                ],
            }
        ]
    )
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(data)


def test_composition_v2_rejects_overlapping_pedals_and_duplicate_automation():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[
                    {
                        "id": "piano-1",
                        "name": "Piano",
                        "instrument": "piano",
                        "role": "harmony",
                        "midi_program": 0,
                        "channel": 1,
                        "events": [],
                        "sustain_pedals": [
                            {"start_tick": 0, "duration_ticks": 480},
                            {"start_tick": 480, "duration_ticks": 480},
                        ],
                    }
                ]
            )
        )

    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[
                    {
                        "id": "piano-1",
                        "name": "Piano",
                        "instrument": "piano",
                        "role": "harmony",
                        "midi_program": 0,
                        "channel": 1,
                        "events": [],
                        "automation": [
                            {
                                "parameter": "volume",
                                "interpolation": "linear",
                                "points": [{"tick": 480, "value": 90}],
                            },
                            {
                                "parameter": "volume",
                                "interpolation": "step",
                                "points": [{"tick": 960, "value": 80}],
                            },
                        ],
                    }
                ]
            )
        )


def test_collect_ignored_v1_paths_reports_extras():
    paths = collect_ignored_v1_paths(
        {
            "schema_version": "composition.v1",
            "tempo": 100,
            "extra_root": 1,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 1, "start_tick": 0, "duration_ticks": 1, "label": "x"}],
            "tracks": [
                {
                    "id": "t1",
                    "name": "n",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "expression": 100,
                    "events": [{"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 1, "velocity": 80, "articulations": []}],
                }
            ],
        }
    )
    assert "extra_root" in paths
    assert "sections[0].label" in paths
    assert "tracks[0].expression" in paths
    assert "tracks[0].events[0].articulations" in paths


def test_projection_report_tracks_counts_and_registry():
    report = empty_projection_report()
    report.add_issue(code="automation_sampled", details={"sample_count": 3})
    report.add_issue(code="automation_omitted_from_notation", status="omitted", severity="info")
    assert report.status == "approximated"
    assert report.approximated_count == 1
    assert report.omitted_count == 1
    assert "automation_sampled" in PROJECTION_ISSUE_CODES
    assert "automation_sampled" in report.compact_codes()

    failed = ProjectionReport()
    failed.add_issue(code="midi_channel_control_conflict", status="failed", severity="error")
    assert failed.status == "failed"
