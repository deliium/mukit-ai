import pytest
from pydantic import ValidationError

from app.composition_schemas import (
    CompositionV2,
    MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
    MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
    UnsupportedSchemaVersionError,
    collect_ignored_v1_paths,
    parse_composition_document,
    reconcile_motifs_for_removed_event_ids,
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


def _motif_source_events():
    return [
        {"type": "note", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80, "id": "n1"},
        {"type": "note", "pitch": "D4", "start_tick": 480, "duration_ticks": 480, "velocity": 80, "id": "n2"},
        {"type": "note", "pitch": "E4", "start_tick": 960, "duration_ticks": 480, "velocity": 80, "id": "n3"},
        {"type": "note", "pitch": "F4", "start_tick": 1440, "duration_ticks": 480, "velocity": 80, "id": "n4"},
    ]


def _motif_track(**overrides):
    track = {
        "id": "melody-1",
        "name": "Melody",
        "instrument": "piano",
        "role": "melody",
        "midi_program": 0,
        "channel": 1,
        "events": _motif_source_events(),
    }
    track.update(overrides)
    return track


def _motif_definition(**overrides):
    motif = {
        "id": "motif-a",
        "label": "Motif A",
        "occurrences": [
            {
                "id": "occ-orig",
                "track_id": "melody-1",
                "event_ids": ["n1", "n2", "n3"],
                "relationship": "original",
            }
        ],
    }
    motif.update(overrides)
    return motif


def test_composition_v2_defaults_motifs_empty():
    composition = CompositionV2.model_validate(minimal_v2())
    assert composition.motifs == []


def test_composition_v2_accepts_old_payload_without_motifs_field():
    data = minimal_v2()
    assert "motifs" not in data
    composition = CompositionV2.model_validate(data)
    assert composition.motifs == []


def test_composition_v2_accepts_canonical_motif_references():
    composition = CompositionV2.model_validate(
        minimal_v2(tracks=[_motif_track()], motifs=[_motif_definition()])
    )
    assert len(composition.motifs) == 1
    occurrence = composition.motifs[0].occurrences[0]
    assert occurrence.event_ids == ["n1", "n2", "n3"]
    dumped = composition.model_dump(mode="json")
    assert "pitch" not in dumped["motifs"][0]
    assert "notes" not in dumped["motifs"][0]["occurrences"][0]
    assert "events" not in dumped["motifs"][0]["occurrences"][0]


def test_composition_v2_rejects_unknown_motif_fields():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[_motif_track()],
                motifs=[_motif_definition(pitch="C4")],
            )
        )


def test_composition_v2_rejects_dangling_motif_event_refs():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[_motif_track()],
                motifs=[
                    _motif_definition(
                        occurrences=[
                            {
                                "id": "occ-orig",
                                "track_id": "melody-1",
                                "event_ids": ["n1", "n2", "missing"],
                                "relationship": "original",
                            }
                        ]
                    )
                ],
            )
        )


def test_composition_v2_rejects_non_chronological_motif_event_ids():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[_motif_track()],
                motifs=[
                    _motif_definition(
                        occurrences=[
                            {
                                "id": "occ-orig",
                                "track_id": "melody-1",
                                "event_ids": ["n3", "n2", "n1"],
                                "relationship": "original",
                            }
                        ]
                    )
                ],
            )
        )


def test_composition_v2_rejects_incomplete_tie_chain_in_motif():
    events = _motif_source_events()
    events[0]["tie"] = {"group_id": "tie-1", "type": "start"}
    events[1]["tie"] = {"group_id": "tie-1", "type": "stop"}
    events[0]["pitch"] = "C4"
    events[1]["pitch"] = "C4"
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[_motif_track(events=events)],
                motifs=[
                    _motif_definition(
                        occurrences=[
                            {
                                "id": "occ-orig",
                                "track_id": "melody-1",
                                "event_ids": ["n1", "n3", "n4"],
                                "relationship": "original",
                            }
                        ]
                    )
                ],
            )
        )


def test_composition_v2_rejects_percussion_motif_source():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[_motif_track(id="drums-1", role="drums", is_drum=True, channel=10)],
                motifs=[
                    _motif_definition(
                        occurrences=[
                            {
                                "id": "occ-orig",
                                "track_id": "drums-1",
                                "event_ids": ["n1", "n2", "n3"],
                                "relationship": "original",
                            }
                        ]
                    )
                ],
            )
        )


def test_composition_v2_rejects_duplicate_motif_occurrence_ids():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            minimal_v2(
                tracks=[_motif_track()],
                motifs=[
                    _motif_definition(),
                    {
                        "id": "motif-b",
                        "label": "Motif B",
                        "occurrences": [
                            {
                                "id": "occ-orig",
                                "track_id": "melody-1",
                                "event_ids": ["n2", "n3", "n4"],
                                "relationship": "original",
                            }
                        ],
                    },
                ],
            )
        )


def test_reconcile_motifs_removes_definition_when_original_invalidated():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[_motif_track()],
            motifs=[
                _motif_definition(
                    occurrences=[
                        {
                            "id": "occ-orig",
                            "track_id": "melody-1",
                            "event_ids": ["n1", "n2", "n3"],
                            "relationship": "original",
                        },
                        {
                            "id": "occ-repeat",
                            "track_id": "melody-1",
                            "event_ids": ["n2", "n3", "n4"],
                            "relationship": "repeat",
                        },
                    ]
                )
            ],
        )
    )
    result = reconcile_motifs_for_removed_event_ids(composition.motifs, {"n1"})
    assert result.motifs == []
    assert {warning.code for warning in result.warnings} == {
        MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
        MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
    }


def test_reconcile_motifs_prunes_non_original_occurrence_only():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[_motif_track()],
            motifs=[
                _motif_definition(
                    occurrences=[
                        {
                            "id": "occ-orig",
                            "track_id": "melody-1",
                            "event_ids": ["n1", "n2", "n3"],
                            "relationship": "original",
                        },
                        {
                            "id": "occ-repeat",
                            "track_id": "melody-1",
                            "event_ids": ["n2", "n3", "n4"],
                            "relationship": "repeat",
                        },
                    ]
                )
            ],
        )
    )
    result = reconcile_motifs_for_removed_event_ids(composition.motifs, {"n4"})
    assert len(result.motifs) == 1
    assert [occ.id for occ in result.motifs[0].occurrences] == ["occ-orig"]
    assert result.warnings[0].code == MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED
