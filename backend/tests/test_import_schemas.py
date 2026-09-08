"""Focused tests for import DTOs, issue codes, and domain errors."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.import_schemas import (
    IMPORT_ERROR_CODES,
    IMPORT_ISSUE_CODES,
    CompositionImportError,
    CompositionImportResponse,
    ImportIssue,
    ImportReport,
    ImportSourceSummary,
    empty_import_report,
)


def _minimal_v2(**overrides):
    data = {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 1,
        "duration_ticks": 1920,
        "sections": [
            {
                "type": "unsectioned",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
        "tracks": [
            {
                "id": "track-1",
                "name": "Imported",
                "instrument": "piano",
                "role": "other",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return CompositionV2.model_validate(data)


def _summary(**overrides) -> ImportSourceSummary:
    data = {
        "detected_format": "midi",
        "display_filename": "sample.mid",
        "input_bytes": 128,
        "source_ppq": 480,
        "target_ppq": 480,
        "source_track_count": 1,
        "result_track_count": 1,
        "source_note_count": 1,
        "result_note_count": 1,
        "bar_count": 1,
        "duration_ticks": 1920,
    }
    data.update(overrides)
    return ImportSourceSummary.model_validate(data)


def test_import_issue_code_registry_is_closed_and_non_empty():
    assert len(IMPORT_ISSUE_CODES) >= 20
    assert "tempo_defaulted" in IMPORT_ISSUE_CODES
    assert "source_id_collision" in IMPORT_ISSUE_CODES
    assert set(IMPORT_ERROR_CODES) >= {
        "import_payload_too_large",
        "import_unsupported_media_type",
        "import_malformed_source",
        "import_dependency_unavailable",
    }


def test_import_report_status_transitions_to_approximated_and_partial():
    report = empty_import_report(summary=_summary())
    assert report.status == "exact"

    report.add_issue(code="tempo_defaulted", action="defaulted", severity="info")
    assert report.status == "approximated"
    assert report.summary.issue_counts_by_action["defaulted"] == 1

    report.add_issue(code="grace_note_omitted", action="omitted", count=3)
    assert report.status == "partial"
    assert report.summary.issue_counts_by_action["omitted"] == 3


def test_import_issue_rejects_unknown_code_and_oversized_details():
    with pytest.raises(ValidationError):
        ImportIssue.model_validate(
            {
                "code": "not_a_real_code",
                "action": "defaulted",
                "message": "x",
            }
        )

    with pytest.raises(ValidationError):
        ImportIssue.model_validate(
            {
                "code": "tempo_defaulted",
                "action": "defaulted",
                "message": "tempo missing",
                "details": {f"k{i}": i for i in range(20)},
            }
        )


def test_display_filename_strips_path_segments():
    summary = _summary(display_filename="../secret/dir/piece.mid")
    assert summary.display_filename == "piece.mid"


def test_composition_import_response_requires_strict_v2():
    composition = _minimal_v2()
    report = empty_import_report(summary=_summary())
    response = CompositionImportResponse(
        composition=composition,
        musicxml="<score-partwise version='3.1'/>",
        import_report=report,
    )
    assert response.composition.schema_version == "composition.v2"
    assert response.composition.tracks[0].role == "other"
    assert response.composition.sections[0].type == "unsectioned"
    assert response.composition.harmony == []


def test_composition_import_error_carries_stable_http_mapping():
    err = CompositionImportError(
        "import_payload_too_large",
        "upload exceeded limit",
        http_status=413,
        details={"limit_bytes": 100},
    )
    assert err.code == "import_payload_too_large"
    assert err.http_status == 413
    assert err.details["limit_bytes"] == 100


def test_neutral_vocabulary_accepted_by_composition_v2():
    composition = _minimal_v2()
    dumped = composition.model_dump(mode="json")
    assert dumped["sections"][0]["type"] == "unsectioned"
    assert dumped["tracks"][0]["role"] == "other"


def test_unknown_role_and_section_still_rejected():
    with pytest.raises(ValidationError):
        _minimal_v2(
            sections=[
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ],
            tracks=[
                {
                    "id": "track-1",
                    "name": "Imported",
                    "instrument": "piano",
                    "role": "synth_lead_custom",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [],
                }
            ],
        )
    with pytest.raises(ValidationError):
        _minimal_v2(
            sections=[
                {
                    "type": "coda_custom",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ]
        )
