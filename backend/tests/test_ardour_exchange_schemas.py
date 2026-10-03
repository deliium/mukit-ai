"""Schema contracts for Ardour exchange documents."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ardour_exchange_schemas import (
    ArdourExchangeIngestRequestV1,
    ArdourExchangeManifestV1,
    ArdourExchangePrepareRequestV1,
    ArdourExchangePreviewV1,
    ArdourExchangeRealizeRequestV1,
    FORBIDDEN_PAYLOAD_KEYS,
    reject_exchange_forbidden_payload,
    scan_forbidden_exchange_keys,
)
from app.composition_schemas import CompositionV2

_FIXTURE_V2 = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _manifest(**overrides) -> dict:
    payload = {
        "schema_version": "ardour.exchange.manifest.v1",
        "package_id": "aex_0123456789abcdef",
        "direction": "inbound",
        "track_name": "Idea",
        "tempo_bpm": 120,
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "start_bar": 1,
        "bar_count": 8,
        "start_samples": 0,
        "sample_rate": 48000,
        "material_relpath": "material.mid",
        "source_fingerprint": "abcdef0123456789",
        "created_at": "2026-10-03T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_manifest_round_trip() -> None:
    model = ArdourExchangeManifestV1.model_validate(_manifest())
    assert model.package_id == "aex_0123456789abcdef"
    assert model.bar_count == 8
    assert model.tempo_bpm == 120
    assert model.material_relpath == "material.mid"


def test_manifest_accepts_float_tempo_normalized() -> None:
    model = ArdourExchangeManifestV1.model_validate(_manifest(tempo_bpm=120.4))
    assert model.tempo_bpm == 120


def test_manifest_rejects_bad_package_id() -> None:
    with pytest.raises(ValidationError):
        ArdourExchangeManifestV1.model_validate(_manifest(package_id="bad"))


def test_manifest_rejects_events_key() -> None:
    payload = _manifest(events=[])
    with pytest.raises(ValidationError, match="ardour_exchange_forbidden_payload"):
        ArdourExchangeManifestV1.model_validate(payload)


def test_manifest_rejects_session_xml_key() -> None:
    payload = _manifest(session_xml="<Session/>")
    with pytest.raises(ValidationError, match="ardour_exchange_forbidden_payload"):
        ArdourExchangeManifestV1.model_validate(payload)


def test_prepare_request_rejects_composition_key() -> None:
    with pytest.raises(ValidationError, match="ardour_exchange_forbidden_payload"):
        ArdourExchangePrepareRequestV1.model_validate(
            {
                "schema_version": "ardour.exchange.prepare.v1",
                "composition": {"schema_version": "composition.v2"},
            }
        )


def test_ingest_request_rejects_prompt_key() -> None:
    with pytest.raises(ValidationError, match="ardour_exchange_forbidden_payload"):
        ArdourExchangeIngestRequestV1.model_validate(
            {"package_id": "aex_0123456789abcdef", "prompt": "x"}
        )


def test_preview_may_carry_draft_composition() -> None:
    composition = CompositionV2.model_validate(json.loads(_FIXTURE_V2.read_text(encoding="utf-8")))
    preview = ArdourExchangePreviewV1.model_validate(
        {
            "schema_version": "ardour.exchange.preview.v1",
            "preview_id": "prev_test_01",
            "package_id": "aex_0123456789abcdef",
            "manifest": _manifest(),
            "draft_composition": composition.model_dump(mode="json"),
            "import_report": {"issue_codes": [], "warning_codes": [], "note_count": 1, "track_count": 1},
            "alignment": {
                "start_bar": 1,
                "bar_count": 8,
                "tempo_bpm": 120,
                "time_signature": "4/4",
            },
        }
    )
    assert preview.draft_composition.schema_version == "composition.v2"
    # Preview model itself must not run the forbidden-key scan on draft_composition.
    assert "composition" in FORBIDDEN_PAYLOAD_KEYS
    assert scan_forbidden_exchange_keys({"draft_composition": {}}) == []


def test_realize_request_accepts_short_instruction() -> None:
    model = ArdourExchangeRealizeRequestV1.model_validate(
        {
            "schema_version": "ardour.exchange.realize_request.v1",
            "intent": "counter_melody",
            "instruction": "cello line",
            "candidate_count": 1,
        }
    )
    assert model.intent == "counter_melody"


@pytest.mark.parametrize(
    "intent",
    [
        "counter_melody",
        "arrangement_variation",
        "regenerate_region",
        "add_accompaniment",
        "orchestrate_selection",
        "reharmonize_selection",
    ],
)
def test_realize_request_accepts_supported_intents(intent: str) -> None:
    model = ArdourExchangeRealizeRequestV1.model_validate(
        {
            "schema_version": "ardour.exchange.realize_request.v1",
            "intent": intent,
            "candidate_count": 1,
        }
    )
    assert model.intent == intent


def test_realize_request_rejects_unknown_intent() -> None:
    with pytest.raises(ValidationError):
        ArdourExchangeRealizeRequestV1.model_validate(
            {
                "schema_version": "ardour.exchange.realize_request.v1",
                "intent": "render_stems",
                "candidate_count": 1,
            }
        )


def test_realize_request_rejects_events_key() -> None:
    with pytest.raises(ValidationError):
        ArdourExchangeRealizeRequestV1.model_validate(
            {
                "schema_version": "ardour.exchange.realize_request.v1",
                "intent": "counter_melody",
                "events": [],
            }
        )


def test_forbidden_keys_still_cover_manifest_and_ingest_scope() -> None:
    assert "composition" in FORBIDDEN_PAYLOAD_KEYS
    assert "events" in FORBIDDEN_PAYLOAD_KEYS
    assert "prompt" in FORBIDDEN_PAYLOAD_KEYS
    with pytest.raises(ValidationError, match="ardour_exchange_forbidden_payload"):
        ArdourExchangeManifestV1.model_validate(_manifest(composition={"x": 1}))
    with pytest.raises(ValidationError, match="ardour_exchange_forbidden_payload"):
        ArdourExchangeIngestRequestV1.model_validate(
            {"package_id": "aex_0123456789abcdef", "composition": {}}
        )


def test_reject_helper_raises_on_nested_forbidden() -> None:
    with pytest.raises(Exception) as excinfo:
        reject_exchange_forbidden_payload(
            {"outer": {"notes": []}},
            model_name="test",
        )
    assert excinfo.value.code == "ardour_exchange_forbidden_payload"
