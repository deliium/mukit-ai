"""Handcrafted symbolic feature extraction — profile ``symbolic.features.v1``.

All features are derived from ``tracks[].events[]`` note material (plus declared
meter/key/sections/motifs metadata when present). Artist / composer names are
never dimensions.

Vector similarity is distributional / structural affinity, not aesthetic quality.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Literal

from app.composition_schemas import (
    CompositionV2,
    CompositionV2NoteEvent,
    bar_duration_ticks,
    compile_bar_boundaries,
    midi_pitch_number,
)
from app.embeddings.errors import EmbeddingScopeError
from app.embeddings.schemas import (
    CompositionEmbedScope,
    CompositionEmbeddingV1,
    EmbedScopeBarRange,
    EmbedScopeComposition,
    EmbedScopeMotif,
    EmbedScopeSection,
    embed_scope_digest,
)
from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN,
    EMBEDDING_PROFILE_ID,
    EMBEDDING_PROJECTION_NONE,
    EMBEDDING_VECTOR_DEBUG_PREFIX_DIMS,
    load_embedding_settings,
)
from app.embeddings.vector import l2_normalize, vector_debug_prefix
from app.services.composition_fingerprint import composition_source_fingerprint


logger = logging.getLogger(__name__)

# Fixed-order feature layout for symbolic.features.v1 (must stay stable).
PC_DIMS = 12
RANGE_DIMS = 3  # min/max/mean midi / 127
DUR_BINS = 8
ONSET_BINS = 8
DENSITY_DIMS = 1
INTERVAL_BINS = 25  # -12..+12
CONTOUR_DIMS = 3  # up/down/same
CONCURRENT_BINS = 4
ROLE_DIMS = 5  # melody/bass/accompaniment|harmony/other/drum
TRACK_COUNT_DIMS = 1
FORM_POS_DIMS = 1
SECTION_TYPE_DIMS = 8
MOTIF_EXTRA_DIMS = 2

SYMBOLIC_FEATURES_V1_DIMS = (
    PC_DIMS
    + RANGE_DIMS
    + DUR_BINS
    + ONSET_BINS
    + DENSITY_DIMS
    + INTERVAL_BINS
    + CONTOUR_DIMS
    + CONCURRENT_BINS
    + ROLE_DIMS
    + TRACK_COUNT_DIMS
    + FORM_POS_DIMS
    + SECTION_TYPE_DIMS
    + MOTIF_EXTRA_DIMS
)

SECTION_TYPE_ORDER = (
    "intro",
    "verse",
    "chorus",
    "bridge",
    "outro",
    "solo",
    "break",
    "other",
)

ROLE_ORDER = ("melody", "bass", "accompaniment", "other", "drum")

ProjectionId = Literal["none"]


@dataclass(frozen=True)
class ScopedNote:
    pitch_midi: int
    start_tick: int
    duration_ticks: int
    track_id: str
    role: str
    is_drum: bool


@dataclass(frozen=True)
class ResolvedEmbedWindow:
    scope_kind: str
    start_tick: int
    end_tick: int
    start_bar: int
    end_bar_inclusive: int
    section_index: int | None
    section_type: str | None
    form_position: float  # 0..1 relative within composition
    motif_relative_pitch_span: float
    motif_relative_rhythm_span: float
    notes: tuple[ScopedNote, ...]


def symbolic_features_v1_dims() -> int:
    return SYMBOLIC_FEATURES_V1_DIMS


def extract_symbolic_features_v1(
    composition: CompositionV2,
    scope: CompositionEmbedScope,
    *,
    projection_id: ProjectionId = "none",
) -> tuple[list[float], ResolvedEmbedWindow]:
    """Return raw (pre-L2) fixed-order feature vector + resolved window."""
    if projection_id != "none":
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            f"Unsupported projection_id={projection_id!r}; V3 default is none",
            details={"projection_id": projection_id},
        )

    window = resolve_embed_window(composition, scope)
    if not window.notes:
        logger.info(
            "Embed scope empty",
            extra={
                "profile_id": EMBEDDING_PROFILE_ID,
                "scope_kind": window.scope_kind,
                "note_count": 0,
            },
        )
        raise EmbeddingScopeError(
            "embed_empty_scope",
            details={"scope_kind": window.scope_kind},
        )

    bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    ppq = max(1, composition.ticks_per_quarter)
    features = _build_feature_vector(window, bar_ticks=bar_ticks, ppq=ppq, bar_count=composition.bar_count)

    assert len(features) == SYMBOLIC_FEATURES_V1_DIMS
    group_norms = {
        "pitch_class": _slice_norm(features, 0, PC_DIMS),
        "rhythm": _slice_norm(features, PC_DIMS + RANGE_DIMS, DUR_BINS + ONSET_BINS + DENSITY_DIMS),
        "contour": _slice_norm(
            features,
            PC_DIMS + RANGE_DIMS + DUR_BINS + ONSET_BINS + DENSITY_DIMS,
            INTERVAL_BINS + CONTOUR_DIMS,
        ),
    }
    logger.debug(
        "Feature group norms",
        extra={
            "profile_id": EMBEDDING_PROFILE_ID,
            "scope_kind": window.scope_kind,
            "note_count": len(window.notes),
            **{f"norm_{k}": round(v, 6) for k, v in group_norms.items()},
        },
    )
    return features, window


def embed_composition_scope(
    composition: CompositionV2,
    scope: CompositionEmbedScope | None = None,
    *,
    model_id: str = EMBEDDING_DEFAULT_MODEL_ID,
    projection_id: ProjectionId = "none",
) -> CompositionEmbeddingV1:
    """Compute L2-normalized ``composition.embedding.v1`` for a scope."""
    resolved_scope: CompositionEmbedScope = scope or EmbedScopeComposition()
    settings = load_embedding_settings()
    raw, window = extract_symbolic_features_v1(
        composition, resolved_scope, projection_id=projection_id
    )
    if len(raw) > settings.max_dims:
        raise EmbeddingScopeError(
            "embed_dims_exceeded",
            details={"dims": len(raw), "max_dims": settings.max_dims},
        )

    vector = l2_normalize(raw)
    fingerprint = composition_source_fingerprint(composition)
    digest = embed_scope_digest(resolved_scope)

    card = CompositionEmbeddingV1(
        profile_id=EMBEDDING_PROFILE_ID,
        algorithm_version=EMBEDDING_ALGORITHM_VERSION,
        model_id=model_id,
        dims=len(vector),
        projection_id=EMBEDDING_PROJECTION_NONE,
        source_fingerprint=fingerprint,
        scope=resolved_scope,
        scope_digest=digest,
        note_count=len(window.notes),
        vector=vector,
        artist_label_used=False,
    )
    logger.info(
        "Embedded composition scope",
        extra={
            "profile_id": EMBEDDING_PROFILE_ID,
            "algorithm_version": EMBEDDING_ALGORITHM_VERSION,
            "model_id": model_id,
            "scope_kind": window.scope_kind,
            "dims": card.dims,
            "note_count": card.note_count,
            "fingerprint_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "scope_digest_prefix": digest[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "projection_id": EMBEDDING_PROJECTION_NONE,
        },
    )
    logger.debug(
        "Embedding vector prefix",
        extra={
            "vector_prefix": vector_debug_prefix(vector, dims=EMBEDDING_VECTOR_DEBUG_PREFIX_DIMS),
        },
    )
    return card


def resolve_embed_window(
    composition: CompositionV2,
    scope: CompositionEmbedScope,
) -> ResolvedEmbedWindow:
    """Resolve scope → tick window + note set (read-only; never invents pitches)."""
    boundaries = compile_bar_boundaries(
        time_signature=composition.time_signature,
        ticks_per_quarter=composition.ticks_per_quarter,
        bar_count=composition.bar_count,
        duration_ticks=composition.duration_ticks,
        time_signature_changes=list(composition.time_signature_changes),
    )

    if isinstance(scope, EmbedScopeComposition) or scope.kind == "composition":
        return _window_for_tick_range(
            composition,
            boundaries,
            start_tick=0,
            end_tick=composition.duration_ticks,
            start_bar=1,
            end_bar_inclusive=composition.bar_count,
            scope_kind="composition",
            section_index=None,
            section_type=None,
            form_position=0.5,
        )

    if isinstance(scope, EmbedScopeSection) or scope.kind == "section":
        return _resolve_section_window(composition, boundaries, scope)  # type: ignore[arg-type]

    if isinstance(scope, EmbedScopeBarRange) or scope.kind == "bar_range":
        return _resolve_bar_range_window(composition, boundaries, scope)  # type: ignore[arg-type]

    if isinstance(scope, EmbedScopeMotif) or scope.kind == "motif":
        return _resolve_motif_window(composition, boundaries, scope)  # type: ignore[arg-type]

    raise EmbeddingScopeError(
        "embed_scope_invalid",
        details={"scope_kind": getattr(scope, "kind", None)},
    )


def _resolve_section_window(
    composition: CompositionV2,
    boundaries: list[int],
    scope: EmbedScopeSection,
) -> ResolvedEmbedWindow:
    if scope.section_index >= len(composition.sections):
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={"section_index": scope.section_index, "section_count": len(composition.sections)},
        )
    section = composition.sections[scope.section_index]
    if scope.section_id is not None and section.id and section.id != scope.section_id:
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={"expected_section_id": scope.section_id, "actual_section_id": section.id},
        )
    if scope.expected_start_bar is not None and section.start_bar != scope.expected_start_bar:
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={"expected_start_bar": scope.expected_start_bar, "actual": section.start_bar},
        )
    if scope.expected_bar_count is not None and section.bar_count != scope.expected_bar_count:
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={"expected_bar_count": scope.expected_bar_count, "actual": section.bar_count},
        )

    end_bar = section.start_bar + section.bar_count - 1
    form_position = 0.0
    if composition.bar_count > 1:
        form_position = (section.start_bar - 1) / (composition.bar_count - 1)

    return _window_for_tick_range(
        composition,
        boundaries,
        start_tick=section.start_tick,
        end_tick=section.start_tick + section.duration_ticks,
        start_bar=section.start_bar,
        end_bar_inclusive=end_bar,
        scope_kind="section",
        section_index=scope.section_index,
        section_type=str(section.type) if section.type else None,
        form_position=form_position,
    )


def _resolve_bar_range_window(
    composition: CompositionV2,
    boundaries: list[int],
    scope: EmbedScopeBarRange,
) -> ResolvedEmbedWindow:
    if scope.end_bar > composition.bar_count or scope.start_bar < 1:
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={
                "start_bar": scope.start_bar,
                "end_bar": scope.end_bar,
                "bar_count": composition.bar_count,
            },
        )
    start_tick = boundaries[scope.start_bar - 1]
    end_tick = boundaries[scope.end_bar]
    form_position = 0.0
    if composition.bar_count > 1:
        form_position = (scope.start_bar - 1) / (composition.bar_count - 1)

    return _window_for_tick_range(
        composition,
        boundaries,
        start_tick=start_tick,
        end_tick=end_tick,
        start_bar=scope.start_bar,
        end_bar_inclusive=scope.end_bar,
        scope_kind="bar_range",
        section_index=None,
        section_type=None,
        form_position=form_position,
        track_id_filter=scope.track_id,
    )


def _resolve_motif_window(
    composition: CompositionV2,
    boundaries: list[int],
    scope: EmbedScopeMotif,
) -> ResolvedEmbedWindow:
    motif = next((m for m in composition.motifs if m.id == scope.motif_id), None)
    if motif is None:
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={"motif_id": scope.motif_id},
        )
    occurrence = None
    if scope.occurrence_id:
        occurrence = next((o for o in motif.occurrences if o.id == scope.occurrence_id), None)
        if occurrence is None:
            raise EmbeddingScopeError(
                "embed_scope_invalid",
                details={"motif_id": scope.motif_id, "occurrence_id": scope.occurrence_id},
            )
    else:
        occurrence = next((o for o in motif.occurrences if o.relationship == "original"), None)
        if occurrence is None:
            occurrence = motif.occurrences[0]

    track = next((t for t in composition.tracks if t.id == occurrence.track_id), None)
    if track is None:
        raise EmbeddingScopeError(
            "embed_scope_invalid",
            details={"track_id": occurrence.track_id},
        )

    id_set = set(occurrence.event_ids)
    events: list[CompositionV2NoteEvent] = []
    for event in track.events:
        event_id = getattr(event, "id", None)
        if event_id and event_id in id_set:
            events.append(event)

    if not events:
        raise EmbeddingScopeError(
            "embed_empty_scope",
            details={"motif_id": scope.motif_id, "occurrence_id": occurrence.id},
        )

    notes: list[ScopedNote] = []
    role = _normalize_role(getattr(track, "role", None), bool(getattr(track, "is_drum", False)))
    is_drum = bool(getattr(track, "is_drum", False))
    for event in events:
        try:
            pitch = midi_pitch_number(event.pitch)
        except (ValueError, TypeError):
            continue
        notes.append(
            ScopedNote(
                pitch_midi=pitch,
                start_tick=int(event.start_tick),
                duration_ticks=max(1, int(event.duration_ticks)),
                track_id=track.id,
                role=role,
                is_drum=is_drum,
            )
        )

    if not notes:
        raise EmbeddingScopeError("embed_empty_scope", details={"motif_id": scope.motif_id})

    start_tick = min(n.start_tick for n in notes)
    end_tick = max(n.start_tick + n.duration_ticks for n in notes)
    start_bar, end_bar = _bars_covering(boundaries, start_tick, end_tick)

    pitches = [n.pitch_midi for n in notes]
    anchor_pitch = pitches[0]
    rel_pitches = [p - anchor_pitch for p in pitches]
    pitch_span = (max(rel_pitches) - min(rel_pitches)) / 24.0 if rel_pitches else 0.0

    anchor_onset = notes[0].start_tick
    rel_onsets = [(n.start_tick - anchor_onset) for n in notes]
    rhythm_span = 0.0
    if rel_onsets and composition.ticks_per_quarter > 0:
        rhythm_span = (max(rel_onsets) - min(rel_onsets)) / float(
            4 * composition.ticks_per_quarter
        )

    form_position = 0.0
    if composition.bar_count > 1:
        form_position = (start_bar - 1) / (composition.bar_count - 1)

    return ResolvedEmbedWindow(
        scope_kind="motif",
        start_tick=start_tick,
        end_tick=end_tick,
        start_bar=start_bar,
        end_bar_inclusive=end_bar,
        section_index=None,
        section_type=None,
        form_position=max(0.0, min(1.0, form_position)),
        motif_relative_pitch_span=max(0.0, min(1.0, pitch_span)),
        motif_relative_rhythm_span=max(0.0, min(1.0, rhythm_span)),
        notes=tuple(notes),
    )


def _window_for_tick_range(
    composition: CompositionV2,
    boundaries: list[int],
    *,
    start_tick: int,
    end_tick: int,
    start_bar: int,
    end_bar_inclusive: int,
    scope_kind: str,
    section_index: int | None,
    section_type: str | None,
    form_position: float,
    track_id_filter: str | None = None,
) -> ResolvedEmbedWindow:
    notes: list[ScopedNote] = []
    for track in composition.tracks:
        if track_id_filter is not None and track.id != track_id_filter:
            continue
        is_drum = bool(getattr(track, "is_drum", False))
        role = _normalize_role(getattr(track, "role", None), is_drum)
        for event in track.events:
            onset = int(event.start_tick)
            if onset < start_tick or onset >= end_tick:
                continue
            try:
                pitch = midi_pitch_number(event.pitch)
            except (ValueError, TypeError):
                continue
            notes.append(
                ScopedNote(
                    pitch_midi=pitch,
                    start_tick=onset,
                    duration_ticks=max(1, int(event.duration_ticks)),
                    track_id=track.id,
                    role=role,
                    is_drum=is_drum,
                )
            )

    return ResolvedEmbedWindow(
        scope_kind=scope_kind,
        start_tick=start_tick,
        end_tick=end_tick,
        start_bar=start_bar,
        end_bar_inclusive=end_bar_inclusive,
        section_index=section_index,
        section_type=section_type,
        form_position=max(0.0, min(1.0, form_position)),
        motif_relative_pitch_span=0.0,
        motif_relative_rhythm_span=0.0,
        notes=tuple(notes),
    )


def _bars_covering(boundaries: list[int], start_tick: int, end_tick: int) -> tuple[int, int]:
    start_bar = 1
    end_bar = max(1, len(boundaries) - 1)
    for i in range(len(boundaries) - 1):
        if boundaries[i] <= start_tick < boundaries[i + 1]:
            start_bar = i + 1
            break
    for i in range(len(boundaries) - 1):
        if boundaries[i] < end_tick <= boundaries[i + 1] or (
            i == len(boundaries) - 2 and end_tick >= boundaries[i]
        ):
            end_bar = i + 1
    return start_bar, max(start_bar, end_bar)


def _normalize_role(role: Any, is_drum: bool) -> str:
    if is_drum:
        return "drum"
    text = str(role or "other").strip().lower()
    if text in {"melody", "lead"}:
        return "melody"
    if text in {"bass"}:
        return "bass"
    if text in {"accompaniment", "harmony", "pad", "chord"}:
        return "accompaniment"
    if text in {"drums", "percussion", "drum"}:
        return "drum"
    return "other"


def _build_feature_vector(
    window: ResolvedEmbedWindow,
    *,
    bar_ticks: int,
    ppq: int,
    bar_count: int,
) -> list[float]:
    notes = window.notes
    n = len(notes)
    pitched = [note for note in notes if not note.is_drum]
    sample = pitched if pitched else list(notes)

    # Pitch-class histogram
    pc = [0.0] * PC_DIMS
    for note in sample:
        pc[note.pitch_midi % 12] += 1.0
    _normalize_hist(pc)

    # Range stats
    midis = [note.pitch_midi for note in sample]
    midi_min = min(midis) / 127.0
    midi_max = max(midis) / 127.0
    midi_mean = (sum(midis) / len(midis)) / 127.0
    range_feats = [midi_min, midi_max, midi_mean]

    # Duration histogram (relative to PPQ)
    dur = [0.0] * DUR_BINS
    for note in notes:
        ratio = note.duration_ticks / float(ppq)
        dur[_duration_bin(ratio)] += 1.0
    _normalize_hist(dur)

    # Onset modulus histogram
    onset = [0.0] * ONSET_BINS
    safe_bar = max(1, bar_ticks)
    for note in notes:
        mod = note.start_tick % safe_bar
        idx = min(ONSET_BINS - 1, int((mod / safe_bar) * ONSET_BINS))
        onset[idx] += 1.0
    _normalize_hist(onset)

    # Density notes/bar (soft saturate ~32 notes/bar → 1.0)
    bar_span = max(1, window.end_bar_inclusive - window.start_bar + 1)
    density = [min(1.0, (n / bar_span) / 32.0)]

    # Interval histogram + contour (melody-ordered by onset within primary track)
    interval = [0.0] * INTERVAL_BINS
    contour = [0.0, 0.0, 0.0]
    ordered = sorted(sample, key=lambda note: (note.start_tick, note.pitch_midi))
    for left, right in zip(ordered, ordered[1:]):
        delta = right.pitch_midi - left.pitch_midi
        clamped = max(-12, min(12, delta))
        interval[clamped + 12] += 1.0
        if delta > 0:
            contour[0] += 1.0
        elif delta < 0:
            contour[1] += 1.0
        else:
            contour[2] += 1.0
    _normalize_hist(interval)
    _normalize_hist(contour)

    # Concurrent density bins (heuristic: notes overlapping at each unique onset)
    concurrent = [0.0] * CONCURRENT_BINS
    for note in notes:
        overlap = sum(
            1
            for other in notes
            if other.start_tick < note.start_tick + note.duration_ticks
            and note.start_tick < other.start_tick + other.duration_ticks
        )
        concurrent[_concurrent_bin(overlap)] += 1.0
    _normalize_hist(concurrent)

    # Role mix
    role_counts = {name: 0.0 for name in ROLE_ORDER}
    track_ids: set[str] = set()
    for note in notes:
        role_counts[note.role if note.role in role_counts else "other"] += 1.0
        track_ids.add(note.track_id)
    role_feats = [role_counts[name] for name in ROLE_ORDER]
    _normalize_hist(role_feats)
    track_count = [min(1.0, len(track_ids) / 8.0)]

    # Form
    form_pos = [window.form_position]
    section_one_hot = [0.0] * SECTION_TYPE_DIMS
    stype = (window.section_type or "other").strip().lower()
    if stype not in SECTION_TYPE_ORDER:
        stype = "other"
    section_one_hot[SECTION_TYPE_ORDER.index(stype)] = 1.0

    motif_extra = [
        window.motif_relative_pitch_span,
        window.motif_relative_rhythm_span,
    ]

    return (
        pc
        + range_feats
        + dur
        + onset
        + density
        + interval
        + contour
        + concurrent
        + role_feats
        + track_count
        + form_pos
        + section_one_hot
        + motif_extra
    )


def _duration_bin(ratio: float) -> int:
    # bins: <0.25, <0.5, <1, <2, <4, <8, <16, else
    thresholds = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0)
    for i, thr in enumerate(thresholds):
        if ratio < thr:
            return i
    return DUR_BINS - 1


def _concurrent_bin(overlap: int) -> int:
    if overlap <= 1:
        return 0
    if overlap <= 2:
        return 1
    if overlap <= 4:
        return 2
    return 3


def _normalize_hist(values: list[float]) -> None:
    total = sum(values)
    if total <= 0:
        return
    inv = 1.0 / total
    for i, value in enumerate(values):
        values[i] = value * inv


def _slice_norm(vector: list[float], start: int, length: int) -> float:
    chunk = vector[start : start + length]
    return math.sqrt(sum(v * v for v in chunk))
