"""Deterministic fake symbolic composers for tests and LLM_FAKE_MODE hybrid.

Produces valid ``composition.v2`` note events from ``CompositionPlan`` form metadata
only. Pitch rule (documented fixture rule, not harmony invention):

- Parse ``form.key`` tonic pitch class via ``parse_key``.
- Emit diatonic scale degrees ``(bar_index + seed + role_offset) % 7``, octave by
  role (melody=4, bass=2, else=3). Density variants change note count / duration
  only — never invent pitches from harmony chord symbols.

Model ids (always ready in fake/pytest paths):

- ``fake:symbolic-tiny`` — one half-bar note per bar (legacy shape)
- ``fake:symbolic-sparse`` — fewer/longer notes (every other bar, full-bar)
- ``fake:symbolic-dense`` — more/shorter notes (four sixteenths per bar)
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.ensemble_arbitration_schemas import (
    FAKE_ENSEMBLE_MODEL_IDS,
    FAKE_SYMBOLIC_DENSE_MODEL_ID,
    FAKE_SYMBOLIC_SPARSE_MODEL_ID,
    FAKE_SYMBOLIC_TINY_MODEL_ID,
)

from ..composition_plan_schemas import CompositionPlan, summarize_composition_plan
from ..composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V2,
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
)
from .composition_normalizer import INSTRUMENT_PROGRAMS
from .composition_timing import bar_duration_ticks
from .composition_tonality import parse_key
from .composition_validator import validate_composition_integrity


logger = logging.getLogger(__name__)

# Backward-compatible aliases (tiny is the historical default).
FAKE_SYMBOLIC_MODEL_ID = FAKE_SYMBOLIC_TINY_MODEL_ID
FAKE_SYMBOLIC_RUNTIME = "fake_symbolic"
FAKE_SYMBOLIC_VERSION = "fake.symbolic.tiny.v1"
FAKE_SYMBOLIC_SPARSE_VERSION = "fake.symbolic.sparse.v1"
FAKE_SYMBOLIC_DENSE_VERSION = "fake.symbolic.dense.v1"
DEFAULT_TICKS_PER_QUARTER = 480

FakeDensityProfile = Literal["tiny", "sparse", "dense"]

_FAKE_MODEL_META: dict[str, tuple[FakeDensityProfile, str]] = {
    FAKE_SYMBOLIC_TINY_MODEL_ID: ("tiny", FAKE_SYMBOLIC_VERSION),
    FAKE_SYMBOLIC_SPARSE_MODEL_ID: ("sparse", FAKE_SYMBOLIC_SPARSE_VERSION),
    FAKE_SYMBOLIC_DENSE_MODEL_ID: ("dense", FAKE_SYMBOLIC_DENSE_VERSION),
}

_PC_TO_NAME = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

_ROLE_OCTAVE = {
    "melody": 4,
    "lead": 4,
    "countermelody": 4,
    "bass": 2,
    "harmony": 3,
    "pad": 3,
    "rhythm": 3,
    "accompaniment": 3,
}

_ROLE_PRIORITY = ("melody", "bass", "harmony", "pad", "rhythm", "countermelody", "lead")


class FakeSymbolicComposerError(RuntimeError):
    """Raised when the fake symbolic composer cannot build a valid V2."""


def is_fake_symbolic_model_id(model_id: str | None) -> bool:
    """Return True when ``model_id`` is a known fake ensemble composer id."""
    return bool(model_id) and str(model_id) in _FAKE_MODEL_META


def resolve_fake_symbolic_model_id(model_id: str | None) -> str:
    """Normalize a requested fake id; unknown/None → tiny."""
    if model_id and str(model_id) in _FAKE_MODEL_META:
        return str(model_id)
    return FAKE_SYMBOLIC_TINY_MODEL_ID


def generate_fake_symbolic_composition(
    plan: CompositionPlan,
    *,
    seed: int | None = 0,
    prefix_composition: CompositionV2 | None = None,
    model_id: str | None = None,
) -> tuple[CompositionV2, dict[str, Any]]:
    """Build a deterministic valid Composition V2 from plan form metadata."""
    effective_seed = 0 if seed is None else int(seed)
    resolved_model_id = resolve_fake_symbolic_model_id(model_id)
    density, model_version = _FAKE_MODEL_META[resolved_model_id]
    form = plan.form
    logger.info(
        "Fake symbolic generate started",
        extra={
            "model_id": resolved_model_id,
            "seed": effective_seed,
            "density": density,
            "bar_count": form.bar_count,
            "instrumentation_count": len(form.instrumentation),
            "has_prefix": prefix_composition is not None,
            "plan_digest_prefix": (plan.constraints_digest or "")[:20] or None,
        },
    )
    logger.debug(
        "Fake symbolic plan field counts",
        extra=summarize_composition_plan(plan),
    )

    ticks_pq = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks_pq)
    duration_ticks = form.bar_count * bar_ticks
    parsed = parse_key(form.key)
    tonic_pc = parsed.tonic_pc if parsed and parsed.tonic_pc >= 0 else 0
    mode = parsed.mode if parsed else "major"
    scale = _MAJOR_SCALE if mode == "major" else _MINOR_SCALE

    sections = _sections_from_form(form, bar_ticks)
    tracks = _tracks_from_plan(
        plan,
        bar_count=form.bar_count,
        bar_ticks=bar_ticks,
        tonic_pc=tonic_pc,
        scale=scale,
        seed=effective_seed,
        density=density,
    )
    tracks = _ensure_required_roles(
        tracks,
        bar_count=form.bar_count,
        bar_ticks=bar_ticks,
        tonic_pc=tonic_pc,
        scale=scale,
        seed=effective_seed,
        density=density,
    )
    if prefix_composition is not None and prefix_composition.tracks:
        # Continuation/variation: keep prefix tracks and append a short continuation
        # bar region when plan bar_count exceeds prefix (else replace with plan material).
        tracks = _merge_prefix_tracks(prefix_composition, tracks, plan_bar_count=form.bar_count)

    payload = {
        "schema_version": COMPOSITION_SCHEMA_VERSION_V2,
        "tempo": form.tempo,
        "key": form.key,
        "time_signature": form.time_signature,
        "ticks_per_quarter": ticks_pq,
        "duration_ticks": duration_ticks,
        "bar_count": form.bar_count,
        "sections": [section.model_dump(mode="json") for section in sections],
        "tracks": [track.model_dump(mode="json") for track in tracks],
        "harmony": [],
        "markers": [],
        "key_changes": [],
        "motifs": [],
    }
    try:
        music = CompositionV2.model_validate(payload)
        integrity = validate_composition_integrity(
            music,
            complexity="simple",
            profile="generation",
        )
        if not integrity.ok:
            codes = [item.code for item in integrity.errors]
            raise FakeSymbolicComposerError(
                f"Fake symbolic composition failed integrity: {', '.join(codes)}"
            )
    except FakeSymbolicComposerError:
        raise
    except Exception as exc:
        logger.error(
            "Fake symbolic composition failed integrity",
            extra={
                "model_id": resolved_model_id,
                "error_type": type(exc).__name__,
                "detail": str(exc)[:200],
            },
        )
        raise FakeSymbolicComposerError(str(exc)) from exc

    note_count = sum(len(track.events) for track in music.tracks)
    report = {
        "model_id": resolved_model_id,
        "runtime": FAKE_SYMBOLIC_RUNTIME,
        "model_version": model_version,
        "seed": effective_seed,
        "status": "ok",
        "prompt_tokens": 0,
        "generated_tokens": note_count,
        "note_count": note_count,
        "track_count": len(music.tracks),
        "decode_result": "ok",
        "fallback_applied": False,
        "pitch_rule": "diatonic_scale_degree_from_form_key",
        "density": density,
    }
    logger.debug(
        "Fake symbolic generate event count",
        extra={
            "model_id": resolved_model_id,
            "seed": effective_seed,
            "event_count": note_count,
            "density": density,
        },
    )
    logger.info(
        "Fake symbolic generate completed",
        extra={
            "model_id": resolved_model_id,
            "seed": effective_seed,
            "note_count": note_count,
            "track_count": len(music.tracks),
            "bar_count": music.bar_count,
            "density": density,
        },
    )
    return music, report


def _sections_from_form(form, bar_ticks: int) -> list[CompositionV2Section]:
    sections: list[CompositionV2Section] = []
    for index, section in enumerate(form.sections):
        start_tick = (section.start_bar - 1) * bar_ticks
        sections.append(
            CompositionV2Section(
                id=f"section-{index + 1}",
                type=section.type,
                label=section.type.replace("_", " ").title(),
                start_bar=section.start_bar,
                bar_count=section.bar_count,
                start_tick=start_tick,
                duration_ticks=section.bar_count * bar_ticks,
            )
        )
    return sections


def _tracks_from_plan(
    plan: CompositionPlan,
    *,
    bar_count: int,
    bar_ticks: int,
    tonic_pc: int,
    scale: tuple[int, ...],
    seed: int,
    density: FakeDensityProfile = "tiny",
) -> list[CompositionV2Track]:
    instruments = list(plan.form.instrumentation) or ["piano"]
    hints_by_family = {
        hint.family.strip().lower(): hint.role for hint in plan.instrumentation.hints if hint.role
    }
    tracks: list[CompositionV2Track] = []
    for index, instrument in enumerate(instruments):
        role = hints_by_family.get(instrument.strip().lower())
        if role is None:
            role = _ROLE_PRIORITY[min(index, len(_ROLE_PRIORITY) - 1)]
        is_drum = "drum" in instrument.lower() or "perc" in instrument.lower()
        events = _events_for_track(
            role=role,
            bar_count=bar_count,
            bar_ticks=bar_ticks,
            tonic_pc=tonic_pc,
            scale=scale,
            seed=seed,
            track_index=index,
            is_drum=is_drum,
            density=density,
        )
        program = INSTRUMENT_PROGRAMS.get(instrument.strip().lower(), 0)
        tracks.append(
            CompositionV2Track(
                id=f"{role}-{index + 1}",
                name=instrument.strip().title() or role.title(),
                instrument=instrument.strip() or "piano",
                role=role,
                midi_program=0 if is_drum else program,
                channel=10 if is_drum else (index + 1),
                is_drum=is_drum,
                volume=100,
                pan=0,
                events=events,
            )
        )
    return tracks


def _events_for_track(
    *,
    role: str,
    bar_count: int,
    bar_ticks: int,
    tonic_pc: int,
    scale: tuple[int, ...],
    seed: int,
    track_index: int,
    is_drum: bool,
    density: FakeDensityProfile = "tiny",
) -> list[CompositionV2NoteEvent]:
    octave = _ROLE_OCTAVE.get(role, 3)
    role_offset = _ROLE_PRIORITY.index(role) if role in _ROLE_PRIORITY else track_index
    events: list[CompositionV2NoteEvent] = []
    for bar_index in range(bar_count):
        # Sparse: skip odd bars so note counts differ from tiny/dense.
        if density == "sparse" and (bar_index % 2) == 1:
            continue
        subdivisions = 4 if density == "dense" else 1
        slot = max(bar_ticks // subdivisions, 1)
        for sub in range(subdivisions):
            degree = (bar_index + seed + role_offset + sub) % len(scale)
            if is_drum:
                pitch = "C2"
            else:
                pc = (tonic_pc + scale[degree]) % 12
                pitch = f"{_PC_TO_NAME[pc]}{octave}"
            start = bar_index * bar_ticks + sub * slot
            if density == "sparse":
                duration = bar_ticks
            elif density == "dense":
                duration = max(slot // 2, 1)
            else:
                duration = min(bar_ticks // 2, bar_ticks)
            if duration < 1:
                duration = bar_ticks
            # Keep note inside the bar (sparse full-bar is exact; others clamp).
            if start + duration > (bar_index + 1) * bar_ticks:
                duration = max((bar_index + 1) * bar_ticks - start, 1)
            events.append(
                CompositionV2NoteEvent(
                    type="note",
                    pitch=pitch,
                    start_tick=start,
                    duration_ticks=duration,
                    velocity=80 + (degree % 8),
                    id=f"{role}-{track_index + 1}-b{bar_index + 1}-s{sub}",
                )
            )
    return events


def _ensure_required_roles(
    tracks: list[CompositionV2Track],
    *,
    bar_count: int,
    bar_ticks: int,
    tonic_pc: int,
    scale: tuple[int, ...],
    seed: int,
    density: FakeDensityProfile = "tiny",
) -> list[CompositionV2Track]:
    """Guarantee melody + bass + harmony/accompaniment roles for generation integrity."""
    roles = {track.role for track in tracks}
    out = list(tracks)
    if "melody" not in roles and "lead" not in roles:
        out.insert(
            0,
            CompositionV2Track(
                id="melody-auto",
                name="Melody",
                instrument="piano",
                role="melody",
                midi_program=0,
                channel=1,
                is_drum=False,
                volume=100,
                pan=0,
                events=_events_for_track(
                    role="melody",
                    bar_count=bar_count,
                    bar_ticks=bar_ticks,
                    tonic_pc=tonic_pc,
                    scale=scale,
                    seed=seed,
                    track_index=0,
                    is_drum=False,
                    density=density,
                ),
            ),
        )
    if "bass" not in roles:
        out.append(
            CompositionV2Track(
                id="bass-auto",
                name="Bass",
                instrument="bass",
                role="bass",
                midi_program=INSTRUMENT_PROGRAMS.get("bass", 32),
                channel=2,
                is_drum=False,
                volume=100,
                pan=0,
                events=_events_for_track(
                    role="bass",
                    bar_count=bar_count,
                    bar_ticks=bar_ticks,
                    tonic_pc=tonic_pc,
                    scale=scale,
                    seed=seed,
                    track_index=len(out),
                    is_drum=False,
                    density=density,
                ),
            )
        )
    harmony_roles = {"harmony", "pad", "accompaniment", "rhythm"}
    if not roles.intersection(harmony_roles):
        out.append(
            CompositionV2Track(
                id="harmony-auto",
                name="Harmony",
                instrument="piano",
                role="harmony",
                midi_program=0,
                channel=3,
                is_drum=False,
                volume=90,
                pan=10,
                events=_events_for_track(
                    role="harmony",
                    bar_count=bar_count,
                    bar_ticks=bar_ticks,
                    tonic_pc=tonic_pc,
                    scale=scale,
                    seed=seed + 1,
                    track_index=len(out),
                    is_drum=False,
                    density=density,
                ),
            )
        )
    return out


def _merge_prefix_tracks(
    prefix: CompositionV2,
    plan_tracks: list[CompositionV2Track],
    *,
    plan_bar_count: int,
) -> list[CompositionV2Track]:
    """Prefer plan-generated tracks when continuing beyond prefix length."""
    if plan_bar_count <= prefix.bar_count:
        return list(prefix.tracks)
    return plan_tracks


__all__ = [
    "FAKE_ENSEMBLE_MODEL_IDS",
    "FAKE_SYMBOLIC_DENSE_MODEL_ID",
    "FAKE_SYMBOLIC_DENSE_VERSION",
    "FAKE_SYMBOLIC_MODEL_ID",
    "FAKE_SYMBOLIC_RUNTIME",
    "FAKE_SYMBOLIC_SPARSE_MODEL_ID",
    "FAKE_SYMBOLIC_SPARSE_VERSION",
    "FAKE_SYMBOLIC_TINY_MODEL_ID",
    "FAKE_SYMBOLIC_VERSION",
    "FakeSymbolicComposerError",
    "generate_fake_symbolic_composition",
    "is_fake_symbolic_model_id",
    "resolve_fake_symbolic_model_id",
]
