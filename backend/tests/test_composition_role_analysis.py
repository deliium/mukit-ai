"""Track-role analysis unit coverage."""

from __future__ import annotations

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_role_analysis import (
    ROLE_METHOD,
    analyze_roles_from_context,
    select_melodic_candidates_from_roles,
)


def _v2_shell(*, bar_count: int = 2, tracks=None, **overrides):
    duration = bar_count * 1920
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": [
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": duration,
            }
        ],
        "tracks": tracks or [],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def _track(
    events,
    *,
    track_id: str,
    role: str,
    instrument: str,
    channel: int = 1,
    is_drum: bool = False,
):
    track = {
        "id": track_id,
        "name": track_id,
        "instrument": instrument,
        "role": role,
        "midi_program": 0,
        "channel": channel,
        "events": events,
    }
    if is_drum:
        track["is_drum"] = True
        track["channel"] = 10
    return track


def _melody_line():
    # High monophonic ascending line
    return [
        {"pitch": "C5", "start_tick": tick, "duration_ticks": 480, "velocity": 80}
        for tick in range(0, 1920, 480)
    ]


def _bass_line():
    return [
        {"pitch": "C2", "start_tick": tick, "duration_ticks": 960, "velocity": 75}
        for tick in (0, 960)
    ]


def _pad_block():
    return [
        {"pitch": "C4", "start_tick": 0, "duration_ticks": 3840, "velocity": 60},
        {"pitch": "E4", "start_tick": 0, "duration_ticks": 3840, "velocity": 58},
        {"pitch": "G4", "start_tick": 0, "duration_ticks": 3840, "velocity": 56},
    ]


def _rhythm_hits():
    return [
        {"pitch": "E4", "start_tick": tick, "duration_ticks": 120, "velocity": 90}
        for tick in range(0, 1920, 240)
    ]


def _drum_hits():
    return [
        {"pitch": "C2", "start_tick": tick, "duration_ticks": 120, "velocity": 100}
        for tick in range(0, 1920, 480)
    ]


def test_melody_bass_pad_rhythm_drum_roles():
    raw = _v2_shell(
        bar_count=2,
        tracks=[
            _track(_melody_line(), track_id="mel", role="other", instrument="flute"),
            _track(_bass_line(), track_id="bas", role="other", instrument="electric bass"),
            _track(_pad_block(), track_id="pad", role="other", instrument="synth pad"),
            _track(_rhythm_hits(), track_id="rhy", role="other", instrument="guitar"),
            _track(_drum_hits(), track_id="drm", role="other", instrument="drums", is_drum=True),
        ],
    )
    result = analyze_roles_from_context(build_analysis_context(raw))
    by_id = {item.track_id: item for item in result.tracks}
    assert by_id["mel"].inferred_role in {"melody", "lead"}
    assert by_id["bas"].inferred_role == "bass"
    assert by_id["pad"].inferred_role in {"pad", "harmony"}
    assert by_id["rhy"].inferred_role == "rhythm" or (
        by_id["rhy"].candidates
        and by_id["rhy"].candidates[0].get("role") == "rhythm"
    )
    assert by_id["drm"].inferred_role in {"drums", "percussion"}
    assert result.inference.method == ROLE_METHOD
    assert result.inference.method_version == ANALYSIS_ALGORITHM_VERSION
    # Import roles are not mutated on the composition.
    assert raw["tracks"][0]["role"] == "other"


def test_declared_role_preserved_on_abstain():
    # Sparse ambiguous dyad — weak evidence should abstain and keep declared.
    events = [
        {"pitch": "C4", "start_tick": 0, "duration_ticks": 240, "velocity": 70},
        {"pitch": "G4", "start_tick": 960, "duration_ticks": 240, "velocity": 70},
    ]
    raw = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[_track(events, track_id="amb", role="harmony", instrument="unknown zither")],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    estimate = analyze_roles_from_context(build_analysis_context(raw)).tracks[0]
    assert estimate.declared_role == "harmony"
    if estimate.inference.status != "ok":
        assert estimate.effective_role == "harmony"
        assert estimate.inferred_role is None


def test_select_melodic_candidates_from_roles():
    raw = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[
            _track(_melody_line(), track_id="mel", role="melody", instrument="flute"),
            _track(_bass_line(), track_id="bas", role="bass", instrument="bass"),
        ],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    roles = analyze_roles_from_context(build_analysis_context(raw))
    candidates = select_melodic_candidates_from_roles(roles)
    assert candidates
    assert all(item.role in {"melody", "lead", "countermelody"} for item in candidates)
    assert candidates[0].track_id == "mel"
    # Declared-only fallback when inference weak: still exposes declared melody.
    declared_only = select_melodic_candidates_from_roles(
        [
            type(roles.tracks[0])(
                track_id="declared-mel",
                declared_role="melody",
                inferred_role=None,
                effective_role="melody",
                candidates=[],
                inference=roles.tracks[0].inference.model_copy(
                    update={"status": "ambiguous", "confidence": 0.2}
                ),
            )
        ]
    )
    assert len(declared_only) == 1
    assert declared_only[0].source == "declared"

    # Task 5 connection: inferred features only inside MelodicRoleHints.
    from app.services.composition_role_analysis import melodic_role_hints_from_roles

    hints = melodic_role_hints_from_roles(roles)
    assert hasattr(hints, "features")
    for feature in hints.features:
        assert feature.track_id
        assert isinstance(feature.score, float)


def test_empty_track_insufficient_evidence():
    raw = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[_track([], track_id="empty", role="melody", instrument="flute")],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    estimate = analyze_roles_from_context(build_analysis_context(raw)).tracks[0]
    assert estimate.inference.status == "insufficient_evidence"
    assert estimate.effective_role == "melody"
