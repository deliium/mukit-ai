"""Golden semantic-vector coverage for deterministic composition analysis."""

from __future__ import annotations

import copy
import json

import pytest

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION, ANALYSIS_SCHEMA_VERSION
from app.services.composition_analysis import analyze_composition
from tests.fixtures.analysis import (
    ANALYSIS_FIXTURE_NAMES,
    load_analysis_expected_vectors,
    load_analysis_fixture,
    permute_object_keys,
    semantic_vector,
)


@pytest.fixture(scope="module")
def expected_vectors():
    return load_analysis_expected_vectors()


def _assert_vector_matches(actual: dict, expected: dict, *, ignore_fingerprint: bool = False):
    """Compare golden semantic fields; fingerprints optional for order permutations."""
    keys = [
        "status",
        "algorithm_version",
        "schema_version",
        "scope_kind",
        "scope_start_tick",
        "scope_end_tick",
        "warning_codes",
        "tonality_status",
        "global_key",
        "key_candidates",
        "harmony_symbols",
        "harmony_bass_pcs",
        "cadence_kinds",
        "motif_kinds",
        "motif_transpositions",
        "max_simultaneity",
        "tension_status",
    ]
    for key in keys:
        assert actual[key] == expected[key], f"mismatch on {key}: {actual[key]!r} != {expected[key]!r}"
    if not ignore_fingerprint:
        assert actual["fingerprint"] == expected["fingerprint"]
        assert len(actual["fingerprint"]) == 64


@pytest.mark.parametrize(
    "name",
    [name for name in ANALYSIS_FIXTURE_NAMES if name != "hygiene_sentinel"],
)
def test_fixture_golden_semantic_vector(name, expected_vectors):
    raw = load_analysis_fixture(name)
    report = analyze_composition(raw, {"kind": "composition"})
    actual = semantic_vector(report)
    expected = expected_vectors["vectors"][name]
    _assert_vector_matches(actual, expected)
    assert report.schema_version == ANALYSIS_SCHEMA_VERSION
    assert report.algorithm_version == ANALYSIS_ALGORITHM_VERSION


def test_major_and_minor_tonality_keys(expected_vectors):
    major = expected_vectors["vectors"]["major_tonality"]
    minor = expected_vectors["vectors"]["minor_tonality"]
    assert major["global_key"] == "C major"
    assert minor["global_key"] == "A minor"
    assert major["key_candidates"][0]["key"] == "C major"
    assert minor["key_candidates"][0]["key"] == "A minor"
    # Candidate ordering: winner first, scores non-increasing.
    for vector in (major, minor):
        scores = [item["score"] for item in vector["key_candidates"]]
        assert scores == sorted(scores, reverse=True)


def test_modulation_local_and_global(expected_vectors):
    raw = load_analysis_fixture("modulation")
    report = analyze_composition(raw)
    vector = semantic_vector(report)
    _assert_vector_matches(vector, expected_vectors["vectors"]["modulation"])
    # Local spans cover both halves; at least one accepted A minor window.
    assert any(
        span.key == "A minor" and span.inference.status == "ok"
        for span in report.tonality.local_spans
    )
    assert report.resolved_scope.end_tick == 7680


def test_chord_inversion_and_nct_section_bass(expected_vectors):
    raw = load_analysis_fixture("chord_inversions_nct")
    root = analyze_composition(raw, {"kind": "section", "section_index": 0, "section_id": "root"})
    inv = analyze_composition(raw, {"kind": "section", "section_index": 1, "section_id": "inv1"})
    _assert_vector_matches(
        semantic_vector(root),
        expected_vectors["vectors"]["chord_inversions_nct_root_section"],
    )
    _assert_vector_matches(
        semantic_vector(inv),
        expected_vectors["vectors"]["chord_inversions_nct_inv_section"],
    )
    assert semantic_vector(root)["harmony_bass_pcs"] == [0]
    assert semantic_vector(inv)["harmony_bass_pcs"] == [4]
    assert semantic_vector(root)["harmony_symbols"] == ["C"]
    assert semantic_vector(inv)["harmony_symbols"] == ["C"]


def test_cadence_types_cover_four_kinds(expected_vectors):
    kinds = set(expected_vectors["vectors"]["cadences"]["cadence_kinds"])
    assert {"authentic_perfect", "plagal", "half", "deceptive"} <= kinds
    report = analyze_composition(load_analysis_fixture("cadences"))
    assert set(semantic_vector(report)["cadence_kinds"]) == kinds


def test_motifs_exact_and_transposed(expected_vectors):
    vector = expected_vectors["vectors"]["motifs"]
    assert "exact" in vector["motif_kinds"]
    assert "transposed" in vector["motif_kinds"]
    assert 2 in vector["motif_transpositions"]
    # Deterministic kind ordering: exact before transposed before rhythm_only.
    ranks = {"exact": 0, "transposed": 1, "rhythm_only": 2}
    kind_ranks = [ranks[kind] for kind in vector["motif_kinds"]]
    assert kind_ranks == sorted(kind_ranks)


def test_mixed_meter_scope_bounds(expected_vectors):
    vector = expected_vectors["vectors"]["mixed_meter"]
    assert vector["scope_start_tick"] == 0
    assert vector["scope_end_tick"] == 4800
    report = analyze_composition(load_analysis_fixture("mixed_meter"))
    assert report.resolved_scope.end_bar_exclusive == 4
    assert report.density.metrics.attacks_per_bar is not None


def test_polyphony_dissonance_metrics(expected_vectors):
    vector = expected_vectors["vectors"]["polyphony_dissonance"]
    assert vector["max_simultaneity"] == 5
    assert vector["tension_status"] in {"ok", "partial", "insufficient_evidence"}
    report = analyze_composition(load_analysis_fixture("polyphony_dissonance"))
    assert report.density.metrics.note_load >= 1.0


def test_warning_code_fixtures(expected_vectors):
    assert "note_outside_instrument_range" in expected_vectors["vectors"]["range_violation"]["warning_codes"]
    assert "excessive_duplicate_notes" in expected_vectors["vectors"]["duplicate_notes"]["warning_codes"]
    assert "percussion_only_scope" in expected_vectors["vectors"]["percussion_only"]["warning_codes"]
    assert expected_vectors["vectors"]["percussion_only"]["tonality_status"] == "not_applicable"
    assert "empty_analysis_scope" in expected_vectors["vectors"]["empty_section_track"]["warning_codes"]
    empty_scope = expected_vectors["vectors"]["empty_section_scope"]
    assert empty_scope["status"] == "empty"
    assert empty_scope["scope_kind"] == "section"
    assert empty_scope["scope_start_tick"] == 3840
    assert empty_scope["scope_end_tick"] == 7680


def test_repeated_runs_byte_equivalent_reports():
    raw = load_analysis_fixture("cadences")
    first = analyze_composition(raw)
    second = analyze_composition(raw)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )


def test_object_key_permutation_same_fingerprint_and_vector(expected_vectors):
    raw = load_analysis_fixture("minor_tonality")
    permuted = permute_object_keys(raw)
    report_a = analyze_composition(raw)
    report_b = analyze_composition(permuted)
    assert semantic_vector(report_a) == semantic_vector(report_b)
    assert report_a.source_fingerprint == report_b.source_fingerprint
    _assert_vector_matches(semantic_vector(report_a), expected_vectors["vectors"]["minor_tonality"])


def test_event_order_permutation_preserves_non_semantic_metrics():
    """Authored event order is non-semantic for metrics/warnings; fingerprint may differ."""
    raw_a = load_analysis_fixture("polyphony_dissonance")
    raw_b = copy.deepcopy(raw_a)
    raw_b["tracks"][0]["events"] = list(reversed(raw_b["tracks"][0]["events"]))
    report_a = analyze_composition(raw_a)
    report_b = analyze_composition(raw_b)
    vec_a = semantic_vector(report_a)
    vec_b = semantic_vector(report_b)
    assert vec_a["warning_codes"] == vec_b["warning_codes"]
    assert vec_a["status"] == vec_b["status"]
    assert vec_a["max_simultaneity"] == vec_b["max_simultaneity"]
    assert vec_a["harmony_symbols"] == vec_b["harmony_symbols"]
    assert vec_a["fingerprint"] != vec_b["fingerprint"] or vec_a == vec_b


def test_input_dump_unchanged_after_analysis():
    raw = load_analysis_fixture("motifs")
    before = copy.deepcopy(raw)
    analyze_composition(raw)
    assert raw == before
    assert json.dumps(raw, sort_keys=True) == json.dumps(before, sort_keys=True)
