"""Focused tests for composition.analysis.v1 DTOs, scopes, and validation helpers."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    ANALYSIS_WARNING_CODES,
    AnalysisEvidence,
    AnalysisWarning,
    CompositionAnalysisError,
    CompositionAnalysisReport,
    CompositionAnalysisRequest,
    CompositionAnalysisScopeComposition,
    CompositionAnalysisScopeSection,
    CompositionAnalysisScopeTrack,
    InferenceMeta,
    ResolvedAnalysisScope,
    default_warning,
    make_derived_id,
    prepare_analysis_request,
    revalidate_composition_v2,
    resolve_analysis_scope,
    round_analysis_float,
)
from app.composition_schemas import CompositionV2


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


def test_request_rejects_unknown_fields():
    composition = CompositionV2.model_validate(minimal_v2())
    with pytest.raises(ValidationError):
        CompositionAnalysisRequest.model_validate(
            {"composition": composition, "scope": {"kind": "composition"}, "mystery": True}
        )


def test_scope_composition_section_track_combinations(caplog):
    composition = CompositionV2.model_validate(minimal_v2())

    whole = CompositionAnalysisRequest(composition=composition)
    assert whole.scope.kind == "composition"

    section = CompositionAnalysisRequest(
        composition=composition,
        scope=CompositionAnalysisScopeSection(section_index=0),
    )
    assert section.scope.kind == "section"

    track = CompositionAnalysisRequest(
        composition=composition,
        scope=CompositionAnalysisScopeTrack(track_id="piano-1"),
    )
    assert track.scope.kind == "track"

    with caplog.at_level("INFO"):
        _, _, resolved = prepare_analysis_request(composition, {"kind": "track", "track_id": "piano-1"})
    assert resolved.track_id == "piano-1"
    assert "Analysis request prepared" in caplog.text


def test_id_less_section_resolves_by_index_and_optional_bounds():
    composition = CompositionV2.model_validate(minimal_v2())
    assert composition.sections[0].id is None

    resolved = resolve_analysis_scope(
        composition,
        CompositionAnalysisScopeSection(
            section_index=0,
            expected_start_bar=1,
            expected_bar_count=2,
            expected_start_tick=0,
            expected_duration_ticks=3840,
        ),
    )
    assert resolved.kind == "section"
    assert resolved.section_index == 0
    assert resolved.section_id is None
    assert resolved.start_tick == 0
    assert resolved.end_tick == 3840


def test_section_id_mismatch_raises_invalid_scope():
    data = minimal_v2(
        sections=[
            {
                "id": "sec-a",
                "type": "intro",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            }
        ]
    )
    composition = CompositionV2.model_validate(data)
    with pytest.raises(CompositionAnalysisError) as exc:
        resolve_analysis_scope(
            composition,
            CompositionAnalysisScopeSection(section_index=0, section_id="sec-other"),
        )
    assert exc.value.code == "analysis_invalid_scope"


def test_revalidate_rejects_v1_and_structurally_invalid_payloads():
    with pytest.raises(CompositionAnalysisError) as exc:
        revalidate_composition_v2({"schema_version": "composition.v1", "tempo": 120})
    assert exc.value.code == "analysis_invalid_composition"

    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
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
    )
    again = revalidate_composition_v2(composition)
    assert again.schema_version == "composition.v2"
    assert len(again.tracks[0].events) == 1

    broken = composition.model_dump(mode="json")
    broken["duration_ticks"] = 100
    with pytest.raises(CompositionAnalysisError):
        revalidate_composition_v2(broken)


def test_round_analysis_float_rejects_nan_infinity_and_is_deterministic():
    assert round_analysis_float(1.23456789) == 1.234568
    assert round_analysis_float(0.5) == 0.5
    # Python round half-even at the chosen precision boundary.
    assert round_analysis_float(1.2345675) == round(1.2345675, 6)
    with pytest.raises(ValueError):
        round_analysis_float(math.nan)
    with pytest.raises(ValueError):
        round_analysis_float(math.inf)


def test_inference_meta_and_evidence_forbid_extras_and_cap_locators():
    with pytest.raises(ValidationError):
        InferenceMeta.model_validate({"status": "ok", "extra": 1})

    meta = InferenceMeta(status="insufficient_evidence", confidence=0.333333333)
    assert meta.confidence == 0.333333

    locators = [
        {"track_index": i, "start_tick": i * 10, "end_tick": i * 10 + 5} for i in range(33)
    ]
    with pytest.raises(ValidationError):
        AnalysisEvidence.model_validate({"count": 33, "locators": locators})


def test_warning_details_bounded_and_registry_covers_required_codes():
    required = {
        "note_outside_instrument_range",
        "dense_overlapping_material",
        "empty_analysis_scope",
        "timing_grid_anomaly",
        "overlapping_same_pitch_timing",
        "declared_key_conflicts_with_inference",
        "declared_key_change_conflicts_with_inference",
        "declared_harmony_conflicts_with_inference",
        "declared_harmony_unparseable",
        "excessive_duplicate_notes",
    }
    assert required.issubset(ANALYSIS_WARNING_CODES.keys())

    warning = default_warning("empty_analysis_scope")
    assert warning.code == "empty_analysis_scope"

    with pytest.raises(ValidationError):
        AnalysisWarning.model_validate(
            {
                "code": "empty_analysis_scope",
                "message": "x",
                "details": {f"k{i}": i for i in range(20)},
            }
        )


def test_report_is_derived_contract_without_timestamp_fields():
    composition = CompositionV2.model_validate(minimal_v2())
    resolved = resolve_analysis_scope(composition, CompositionAnalysisScopeComposition())
    report = CompositionAnalysisReport(
        source_fingerprint="a" * 64,
        resolved_scope=resolved,
    )
    dumped = report.model_dump(mode="json")
    assert dumped["schema_version"] == ANALYSIS_SCHEMA_VERSION
    assert dumped["algorithm_version"] == ANALYSIS_ALGORITHM_VERSION
    assert dumped["source_schema_version"] == "composition.v2"
    assert "timestamp" not in dumped
    assert "created_at" not in dumped
    assert "updated_at" not in dumped

    with pytest.raises(ValidationError):
        CompositionAnalysisReport.model_validate(
            {
                "source_fingerprint": "a" * 64,
                "resolved_scope": resolved.model_dump(mode="json"),
                "mystery": True,
            }
        )


def test_make_derived_id_is_deterministic():
    assert make_derived_id("keyspan", 0, 480, "C major") == make_derived_id(
        "keyspan", 0, 480, "C major"
    )
    assert make_derived_id("a", "b") != make_derived_id("a", "c")


def test_make_sha256_derived_id_is_deterministic():
    from app.analysis_schemas import make_sha256_derived_id

    assert make_sha256_derived_id("motif_family", "a", 1) == make_sha256_derived_id(
        "motif_family", "a", 1
    )
    assert make_sha256_derived_id("motif_family", "a", 1) != make_sha256_derived_id(
        "motif_family", "a", 2
    )


def test_repetition_result_accepts_motif_families():
    from app.analysis_schemas import (
        DetectedMotifFamily,
        DetectedMotifOccurrence,
        DetectedNoteReference,
        RepetitionAnalysisResult,
    )

    reference = DetectedMotifOccurrence(
        id="motif_occ:abc",
        kind="exact",
        track_id="t1",
        start_tick=0,
        end_tick=1920,
        note_count=4,
        identity_score=1.0,
        notes=[DetectedNoteReference(event_ids=["e1"])],
    )
    matched = DetectedMotifOccurrence(
        id="motif_occ:def",
        kind="exact",
        track_id="t1",
        start_tick=1920,
        end_tick=3840,
        note_count=4,
        identity_score=0.95,
        notes=[DetectedNoteReference(event_indexes=[4])],
    )
    family = DetectedMotifFamily(
        id="motif_family:abc123",
        reference=reference,
        matched_occurrences=[matched],
        relationship_kinds=["exact"],
        note_count=4,
    )
    result = RepetitionAnalysisResult(motif_families=[family])
    assert len(result.motif_families) == 1
    assert result.motif_families[0].reference.id == "motif_occ:abc"


def test_resolved_scope_model_forbids_inverted_ticks():
    with pytest.raises(ValidationError):
        ResolvedAnalysisScope(
            kind="composition",
            start_tick=100,
            end_tick=50,
            start_bar=1,
            end_bar_exclusive=2,
        )
