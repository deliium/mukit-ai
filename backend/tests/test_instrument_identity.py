"""Unit tests for instrument identity normalization and instrumentation analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.instrument_identity import (
    analyze_instrumentation,
    classify_content_relationship,
    collect_instrument_families,
    collect_instrument_requirements,
    instrument_satisfies_requirement,
    normalize_instrument_identity,
    normalize_instrument_family,
)


@dataclass
class FakeTrack:
    id: str
    instrument: str
    role: str
    events: list[dict[str, Any]] = field(default_factory=list)
    name: str = "Display Name"


def _note(
    pitch: str,
    start: int,
    duration: int = 480,
    velocity: int = 80,
    *,
    event_id: str | None = None,
    staff: str | None = None,
    voice: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "note",
        "pitch": pitch,
        "start_tick": start,
        "duration_ticks": duration,
        "velocity": velocity,
    }
    if event_id is not None:
        payload["id"] = event_id
    if staff is not None:
        payload["staff"] = staff
    if voice is not None:
        payload["voice"] = voice
    return payload


def test_one_instrument_one_track_satisfies():
    analysis = analyze_instrumentation(
        ["piano"],
        [FakeTrack(id="t1", instrument="piano", role="melody", events=[_note("C4", 0)])],
    )
    assert analysis.missing_keys == ()
    assert analysis.satisfied_keys == ("piano",)
    assert analysis.satisfied[0].track_ids == ("t1",)


def test_aliases_normalize_to_canonical_identities():
    assert normalize_instrument_identity("Acoustic Piano") == "piano"
    assert normalize_instrument_identity("grand-piano") == "piano"
    assert normalize_instrument_identity("Bass Guitar") == "bass"
    assert normalize_instrument_identity("electric bass") == "bass"
    assert normalize_instrument_identity("String Ensemble") == "strings"
    assert normalize_instrument_identity("keyboard") == "piano"
    # Distinct brass instruments stay distinct sound identities.
    assert normalize_instrument_identity("trumpet") == "trumpet"
    assert normalize_instrument_identity("trombone") == "trombone"
    assert normalize_instrument_identity("trumpet") != normalize_instrument_identity("trombone")


def test_name_independent_matching_uses_instrument_only():
    analysis = analyze_instrumentation(
        ["piano"],
        [
            FakeTrack(
                id="melody-1",
                name="Melody",
                instrument="piano",
                role="melody",
                events=[_note("A4", 0)],
            )
        ],
    )
    assert analysis.satisfied_keys == ("piano",)
    assert analysis.missing_keys == ()


def test_repeated_aliases_collapse_to_one_requirement():
    requirements = collect_instrument_requirements(
        ["piano", "Acoustic Piano", "grand piano", "bass", "electric bass"]
    )
    assert [item.key for item in requirements] == ["piano", "bass"]
    assert collect_instrument_families(["piano", "keyboard", "bass guitar"]) == ("piano", "bass")


def test_missing_instruments_reported():
    analysis = analyze_instrumentation(
        ["piano", "bass", "strings"],
        [
            FakeTrack(id="m1", instrument="piano", role="melody", events=[_note("C4", 0)]),
            FakeTrack(id="b1", instrument="bass", role="bass", events=[_note("C2", 0)]),
        ],
    )
    assert analysis.missing_keys == ("strings",)
    assert set(analysis.satisfied_keys) == {"piano", "bass"}


def test_strings_matching_bidirectional():
    assert instrument_satisfies_requirement("violin", "strings")
    assert instrument_satisfies_requirement("strings", "violin")
    analysis = analyze_instrumentation(
        ["strings"],
        [FakeTrack(id="v1", instrument="violin", role="pad", events=[_note("E4", 0)])],
    )
    assert analysis.missing_keys == ()
    assert analysis.satisfied_keys == ("strings",)


def test_same_instrument_different_roles_valid_not_duplicate():
    analysis = analyze_instrumentation(
        ["piano", "bass"],
        [
            FakeTrack(id="m1", instrument="piano", role="melody", events=[_note("C5", 0)]),
            FakeTrack(id="h1", instrument="piano", role="harmony", events=[_note("C3", 0)]),
            FakeTrack(id="b1", instrument="bass", role="bass", events=[_note("C2", 0)]),
        ],
    )
    assert analysis.missing_keys == ()
    assert analysis.duplicate_groups == ()


def test_empty_tracks_do_not_satisfy_and_fingerprint_distinct_from_notes():
    empty = FakeTrack(id="empty-1", instrument="piano", role="melody", events=[])
    analysis = analyze_instrumentation(["piano"], [empty])
    # Instrument presence still satisfies the sound-source requirement.
    assert analysis.satisfied_keys == ("piano",)
    assert classify_content_relationship([], [_note("C4", 0)]) == "distinct"
    assert classify_content_relationship([], []) == "exact"


def test_exact_equivalent_same_role_is_actionable_duplicate():
    events = [_note("C4", 0, event_id="a"), _note("E4", 480, event_id="b")]
    twin_events = [_note("C4", 0, event_id="x"), _note("E4", 480, event_id="y")]
    analysis = analyze_instrumentation(
        ["piano"],
        [
            FakeTrack(id="t1", instrument="piano", role="melody", events=events),
            FakeTrack(id="t2", instrument="piano", role="Melody", events=twin_events),
        ],
    )
    assert len(analysis.duplicate_groups) == 1
    group = analysis.duplicate_groups[0]
    assert group.identity == "piano"
    assert group.role == "melody"
    assert group.track_ids == ("t1", "t2")
    assert group.event_counts == (2, 2)
    assert group.content_relationship == "exact"
    assert group.actionable is True


def test_high_overlap_same_role_is_actionable():
    left = [
        _note("C4", 0),
        _note("E4", 480),
        _note("G4", 960),
        _note("B4", 1440),
        _note("C5", 1920),
        _note("D5", 2400),
        _note("E5", 2880),
        _note("F5", 3360),
        _note("G5", 3840),
    ]
    # 8/9 shared → intersection/union = 8/10 = 0.8
    right = [
        _note("C4", 0),
        _note("E4", 480),
        _note("G4", 960),
        _note("B4", 1440),
        _note("C5", 1920),
        _note("D5", 2400),
        _note("E5", 2880),
        _note("F5", 3360),
        _note("A5", 3840),
    ]
    analysis = analyze_instrumentation(
        ["piano"],
        [
            FakeTrack(id="a", instrument="piano", role="harmony", events=left),
            FakeTrack(id="b", instrument="piano", role="harmony", events=right),
        ],
    )
    group = analysis.duplicate_groups[0]
    assert group.content_relationship == "high_overlap"
    assert group.actionable is True


def test_distinct_content_same_role_reported_not_actionable():
    analysis = analyze_instrumentation(
        ["bass"],
        [
            FakeTrack(
                id="b1",
                instrument="bass",
                role="bass",
                events=[_note("A2", 0), _note("E2", 1920)],
            ),
            FakeTrack(
                id="b2",
                instrument="Electric Bass",
                role="bass",
                events=[_note("C3", 0), _note("G2", 960), _note("F2", 1920)],
            ),
        ],
    )
    assert len(analysis.duplicate_groups) == 1
    group = analysis.duplicate_groups[0]
    assert group.content_relationship == "distinct"
    assert group.actionable is False


def test_unknown_label_uses_full_normalized_identity():
    assert normalize_instrument_identity("Custom Lead Instrument") == "custom_lead_instrument"
    # Family helper still returns the identity when no family bucket exists.
    assert normalize_instrument_family("Custom Lead Instrument") == "custom_lead_instrument"
    # Known alias tokens still win for labels that include them as whole words.
    assert normalize_instrument_identity("Custom Synth Lead") == "synth"


def test_fingerprint_ignores_event_ids_preserves_staff_voice_multiplicity():
    left = [
        _note("C4", 0, event_id="one", staff="treble", voice=1),
        _note("C4", 0, event_id="two", staff="treble", voice=1),
    ]
    right = [
        _note("C4", 0, event_id="other", staff="treble", voice=1),
        _note("C4", 0, event_id="other2", staff="treble", voice=1),
    ]
    assert classify_content_relationship(left, right) == "exact"
    different_staff = [_note("C4", 0, staff="bass", voice=1), _note("C4", 0, staff="bass", voice=1)]
    assert classify_content_relationship(left, different_staff) == "distinct"


def test_fingerprint_includes_articulations_and_tie_when_present():
    from app.services.instrument_identity import event_content_fingerprint

    plain = {"pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
    empty_expr = {**plain, "articulations": [], "tie": None}
    accented = {**plain, "articulations": ["accent"]}
    tied = {**plain, "tie": {"group_id": "t1", "type": "start"}}
    assert event_content_fingerprint(plain) == event_content_fingerprint(empty_expr)
    assert event_content_fingerprint(plain) != event_content_fingerprint(accented)
    assert event_content_fingerprint(plain) != event_content_fingerprint(tied)
    assert classify_content_relationship([accented], [accented]) == "exact"
    assert classify_content_relationship([plain], [accented]) == "distinct"


def test_unexpected_identities_when_extras_disallowed():
    analysis = analyze_instrumentation(
        ["piano", "bass"],
        [
            FakeTrack(id="m1", instrument="piano", role="melody", events=[_note("C4", 0)]),
            FakeTrack(id="b1", instrument="bass", role="bass", events=[_note("C2", 0)]),
            FakeTrack(id="t1", instrument="trumpet", role="melody", events=[_note("C5", 0)]),
        ],
        allow_extra=False,
    )
    assert analysis.unexpected_identities == ("trumpet",)


def test_drums_skipped_from_requirements_and_unexpected():
    analysis = analyze_instrumentation(
        ["piano", "drums"],
        [
            FakeTrack(id="m1", instrument="piano", role="melody", events=[_note("C4", 0)]),
            FakeTrack(id="d1", instrument="drums", role="drums", events=[]),
        ],
        allow_extra=False,
    )
    assert analysis.requirements[0].key == "piano"
    assert "drums" not in analysis.missing_keys
    assert analysis.unexpected_identities == ()
