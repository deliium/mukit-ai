"""Schema tests for musical.dependency documents."""

from __future__ import annotations

import json
import logging

import pytest

from app.musical_dependency_schemas import (
    FORBIDDEN_NOTE_KEYS,
    MusicalDependencyError,
    parse_musical_dependency_edge,
    parse_musical_dependency_graph,
    parse_musical_dependency_update_offer,
)

_FINGERPRINT = "ab" * 32
_UNIVERSE = "muniv_" + "a1" * 8
_EDGE = "dep_" + "c3" * 8


def _variation_edge(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "schema_version": "musical.dependency.edge.v1",
        "id": _EDGE,
        "dependency_type": "variation_of",
        "upstream_kind": "theme",
        "downstream_kind": "motif_occurrence",
        "universe_id": _UNIVERSE,
        "upstream_theme_id": "theme_bbbb2222",
        "variant_id": "var_cccc3333",
        "downstream_project_id": "project-b",
        "motif_id": "motif_explore",
        "occurrence_id": "occ_explore",
        "upstream_fingerprint": _FINGERPRINT,
        "created_at": "2026-10-02T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_variation_edge_and_theme_a_graph() -> None:
    edge = parse_musical_dependency_edge(_variation_edge())
    assert edge.dependency_type == "variation_of"
    assert edge.upstream_kind == "theme"
    assert edge.downstream_kind == "motif_occurrence"

    graph = parse_musical_dependency_graph(
        {
            "schema_version": "musical.dependency.graph.v1",
            "nodes": [
                {
                    "node_key": f"theme:{_UNIVERSE}:theme_bbbb2222",
                    "kind": "theme",
                    "label": "Theme A",
                    "universe_id": _UNIVERSE,
                    "theme_id": "theme_bbbb2222",
                },
                {
                    "node_key": "motif:project-b:motif_explore:occ_explore",
                    "kind": "motif_occurrence",
                    "label": "Exploration variation",
                },
                {
                    "node_key": "motif:project-c:motif_combat:occ_combat",
                    "kind": "motif_occurrence",
                    "label": "Combat variation",
                },
                {
                    "node_key": "motif:project-d:motif_finale:occ_finale",
                    "kind": "motif_occurrence",
                    "label": "Finale transformation",
                },
            ],
            "edges": [
                {
                    "edge_id": _EDGE,
                    "dependency_type": "variation_of",
                    "upstream_node_key": f"theme:{_UNIVERSE}:theme_bbbb2222",
                    "downstream_node_key": "motif:project-b:motif_explore:occ_explore",
                    "status": "fresh",
                },
                {
                    "edge_id": "dep_" + "d4" * 8,
                    "dependency_type": "variation_of",
                    "upstream_node_key": f"theme:{_UNIVERSE}:theme_bbbb2222",
                    "downstream_node_key": "motif:project-c:motif_combat:occ_combat",
                    "status": "fresh",
                },
                {
                    "edge_id": "dep_" + "e5" * 8,
                    "dependency_type": "variation_of",
                    "upstream_node_key": f"theme:{_UNIVERSE}:theme_bbbb2222",
                    "downstream_node_key": "motif:project-d:motif_finale:occ_finale",
                    "status": "fresh",
                },
            ],
        }
    )
    assert graph.nodes[0].label == "Theme A"
    assert len(graph.nodes) == 4
    assert len(graph.edges) == 3
    encoded = json.dumps(graph.model_dump(mode="json"))
    for key in FORBIDDEN_NOTE_KEYS:
        assert f'"{key}"' not in encoded


def test_rejects_events_and_pitch(caplog: pytest.LogCaptureFixture) -> None:
    with_events = _variation_edge()
    with_events["events"] = [{"pitch": "C4"}]
    with caplog.at_level(logging.DEBUG, logger="app.musical_dependency_schemas"):
        with pytest.raises(MusicalDependencyError) as events:
            parse_musical_dependency_edge(with_events)
    assert events.value.code == "embedded_note_material"
    assert events.value.http_status == 422
    assert any(
        record.model == "MusicalDependencyEdgeV1" and record.code == "embedded_note_material"
        for record in caplog.records
        if record.name == "app.musical_dependency_schemas"
    )

    with_pitch = _variation_edge()
    with_pitch["pitch"] = "C4"
    with pytest.raises(MusicalDependencyError) as pitch:
        parse_musical_dependency_edge(with_pitch)
    assert pitch.value.code == "embedded_note_material"


def test_rejects_seventh_type_wrong_kind_and_short_fingerprint() -> None:
    seventh = _variation_edge(dependency_type="retrograde")
    with pytest.raises(MusicalDependencyError) as bad_type:
        parse_musical_dependency_edge(seventh)
    assert bad_type.value.code == "dependency_invalid"

    rendered = _variation_edge(
        dependency_type="rendered_from",
        upstream_kind="theme",
        downstream_kind="neural_render",
        downstream_asset_id="job-1",
    )
    with pytest.raises(MusicalDependencyError) as kind:
        parse_musical_dependency_edge(rendered)
    assert kind.value.code == "dependency_endpoint_kind"

    short = _variation_edge(upstream_fingerprint="abc")
    with pytest.raises(MusicalDependencyError) as digest:
        parse_musical_dependency_edge(short)
    assert digest.value.code == "dependency_invalid"


def test_rejects_update_offer_notes() -> None:
    with pytest.raises(MusicalDependencyError) as offer:
        parse_musical_dependency_update_offer(
            {
                "schema_version": "musical.dependency.update_offer.v1",
                "dependency_type": "variation_of",
                "action": "reuse_theme",
                "edge_id": _EDGE,
                "notes": ["C4"],
            }
        )
    assert offer.value.code == "embedded_note_material"
    body = json.dumps({"code": offer.value.code, "message": offer.value.message})
    assert "C4" not in body
