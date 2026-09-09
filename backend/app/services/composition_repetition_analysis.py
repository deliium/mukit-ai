"""Bounded motif-family detection with actionable note references.

Uses skyline monophonic projection (highest pitch at attack), normalized onset
rhythm + duration tokens, rolling hashes with exact verification, family
grouping (reference + matched occurrences), redundancy suppression, section
fingerprints, deterministic ordering, and explicit search truncation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_EVIDENCE_ITEMS,
    ANALYSIS_MAX_MOTIFS,
    AnalysisEvidence,
    DetectedMotifFamily,
    DetectedMotifOccurrence,
    DetectedMotifRelationshipKind,
    DetectedNoteReference,
    InferenceMeta,
    MotifOccurrence,
    RepetitionAnalysisResult,
    make_derived_id,
    make_sha256_derived_id,
    round_analysis_float,
)
from app.services.composition_analysis_context import CompositionAnalysisContext
from app.services.composition_logical_notes import (
    CollapsedLogicalNote,
    attack_in_interval,
    clip_occupancy,
    note_overlaps_interval,
)
from app.services.composition_motif_similarity import (
    compute_identity_score,
    quantize_duration,
    quantize_ioi,
)


logger = logging.getLogger(__name__)

REPETITION_METHOD = "repetition.native.v2:skyline"

# Search / motif bounds (documented fixed policy).
MIN_MOTIF_NOTES = 3
MAX_MOTIF_NOTES = 12
MAX_HASH_COMPARISONS = 50_000
MAX_CANDIDATES_PER_TRACK = 256
# Secondary inversion/scaling passes are bounded to keep large-scope analysis practical.
MAX_SECONDARY_PASS_NOTES = 128
MAX_SECONDARY_PAIR_CHECKS = 8_192

LegacyMotifKind = Literal["exact", "transposed", "rhythm_only"]
ExtendedMotifKind = DetectedMotifRelationshipKind

_KIND_RANK: dict[str, int] = {
    "exact": 0,
    "transposed": 1,
    "inversion": 2,
    "augmentation": 3,
    "diminution": 3,
    "sequence": 4,
    "rhythm_only": 5,
}

_LEGACY_KINDS: frozenset[str] = frozenset({"exact", "transposed", "rhythm_only"})


@dataclass(frozen=True)
class _ProjectedNote:
    source: CollapsedLogicalNote
    event_indexes: tuple[int, ...]
    midi_number: int
    start_tick: int
    duration_ticks: int


@dataclass(frozen=True)
class _TokenStream:
    track_id: str
    track_index: int
    notes: tuple[_ProjectedNote, ...]
    midis: tuple[int, ...]
    onsets: tuple[int, ...]
    durations: tuple[int, ...]
    intervals: tuple[int, ...]
    rhythm: tuple[int, ...]
    dur_tokens: tuple[int, ...]
    skyline_reduced: bool


@dataclass(frozen=True)
class _SpanKey:
    track_id: str
    start_index: int
    length: int


@dataclass
class _RawMatch:
    kind: ExtendedMotifKind
    reference: _SpanKey
    target: _SpanKey
    transposition_semitones: int | None = None
    time_scale_numerator: int | None = None
    time_scale_denominator: int | None = None
    identity_score: float = 0.0


def analyze_repetition_from_context(
    context: CompositionAnalysisContext,
) -> RepetitionAnalysisResult:
    """Detect bounded repeated material grouped into motif families."""
    try:
        return _analyze_repetition_from_context(context)
    except Exception as exc:
        logger.error(
            "Repetition analyzer failure",
            extra={"error_type": type(exc).__name__},
        )
        raise


def _analyze_repetition_from_context(
    context: CompositionAnalysisContext,
) -> RepetitionAnalysisResult:
    streams, any_skyline = _build_token_streams(context)
    logger.debug(
        "Repetition token streams",
        extra={
            "stream_count": len(streams),
            "total_notes": sum(len(stream.midis) for stream in streams),
            "projection_method": "skyline",
            "skyline_reduced": any_skyline,
        },
    )

    section_ids = _section_fingerprint_ids(context)
    if not streams:
        status = "insufficient_evidence"
        logger.info(
            "Repetition analysis complete",
            extra={
                "status": status,
                "motif_count": 0,
                "family_count": 0,
                "section_fingerprint_count": len(section_ids),
            },
        )
        return RepetitionAnalysisResult(
            motifs=[],
            motif_families=[],
            section_fingerprint_ids=section_ids,
            inference=InferenceMeta(
                status=status,
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=REPETITION_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    raw_matches, truncated, comparisons = _detect_matches(streams, context.composition.ticks_per_quarter)
    scaled_matches, scaled_truncated, scaled_comparisons = _detect_scaled_matches(
        streams, context.composition.ticks_per_quarter
    )
    inversion_matches, inversion_truncated, inversion_comparisons = _detect_inversion_matches(
        streams, context.composition.ticks_per_quarter
    )
    comparisons += scaled_comparisons + inversion_comparisons
    raw_matches.extend(scaled_matches)
    raw_matches.extend(inversion_matches)
    truncated = truncated or scaled_truncated or inversion_truncated
    logger.debug(
        "Repetition search summary",
        extra={
            "hash_comparisons": comparisons,
            "raw_match_count": len(raw_matches),
            "truncated": truncated,
        },
    )

    raw_matches = _promote_sequences(raw_matches, streams)
    families = _group_into_families(raw_matches, streams)
    families = _suppress_redundant_families(families)
    families.sort(
        key=lambda item: (
            min(_KIND_RANK.get(kind, 9) for kind in item.relationship_kinds) if item.relationship_kinds else 9,
            item.reference.start_tick,
            item.reference.end_tick,
            item.reference.track_id or "",
            item.id,
        )
    )

    status: str = "ok"
    if truncated:
        status = "truncated"
        logger.warning(
            "Motif search truncated",
            extra={"code": "motif_search_truncated", "kept": min(len(families), ANALYSIS_MAX_MOTIFS)},
        )

    if len(families) > ANALYSIS_MAX_MOTIFS:
        families = families[:ANALYSIS_MAX_MOTIFS]
        status = "truncated"
        logger.warning(
            "Motif family results truncated",
            extra={"code": "result_truncated", "kept": ANALYSIS_MAX_MOTIFS},
        )

    flat_motifs = _flatten_legacy_motifs(families)
    flat_motifs = _suppress_redundant_flat(flat_motifs)
    flat_motifs.sort(
        key=lambda item: (
            _KIND_RANK.get(item.kind, 9),
            item.start_tick,
            item.end_tick,
            item.track_id or "",
            item.id,
        )
    )
    if len(flat_motifs) > ANALYSIS_MAX_MOTIFS:
        flat_motifs = flat_motifs[:ANALYSIS_MAX_MOTIFS]
        status = "truncated"

    confidence = None
    if families:
        confidence = round_analysis_float(min(1.0, 0.4 + 0.05 * len(families)))

    occurrence_total = sum(1 + len(family.matched_occurrences) for family in families)
    logger.info(
        "Repetition analysis complete",
        extra={
            "status": status,
            "motif_count": len(flat_motifs),
            "family_count": len(families),
            "occurrence_total": occurrence_total,
            "section_fingerprint_count": len(section_ids),
            "truncated": truncated or status == "truncated",
        },
    )

    return RepetitionAnalysisResult(
        motifs=flat_motifs,
        motif_families=families,
        section_fingerprint_ids=section_ids,
        inference=InferenceMeta(
            status=status,  # type: ignore[arg-type]
            confidence=confidence,
            evidence=AnalysisEvidence(count=len(families)),
            method=REPETITION_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _build_token_streams(
    context: CompositionAnalysisContext,
) -> tuple[list[_TokenStream], bool]:
    scope = context.resolved_scope
    streams: list[_TokenStream] = []
    any_skyline = False
    for track in context.tracks:
        if track.is_drum:
            continue
        if scope.kind == "track" and track.track_id != scope.track_id:
            continue
        notes = [
            note
            for note in track.logical_notes
            if attack_in_interval(note, scope.start_tick, scope.end_tick)
        ]
        if len(notes) < MIN_MOTIF_NOTES:
            continue
        projected, skyline_reduced = _project_skyline(
            notes,
            scope.start_tick,
            scope.end_tick,
            track_index=track.track_index,
            track_events=context.composition.tracks[track.track_index].events,
        )
        if skyline_reduced:
            any_skyline = True
        if len(projected) < MIN_MOTIF_NOTES:
            continue
        midis = tuple(note.midi_number for note in projected)
        onsets = tuple(note.start_tick for note in projected)
        durations = tuple(note.duration_ticks for note in projected)
        tpq = context.composition.ticks_per_quarter
        intervals = tuple(midis[i + 1] - midis[i] for i in range(len(midis) - 1))
        rhythm = tuple(quantize_ioi(onsets[i + 1] - onsets[i], tpq) for i in range(len(onsets) - 1))
        dur_tokens = tuple(quantize_duration(d, tpq) for d in durations)
        streams.append(
            _TokenStream(
                track_id=track.track_id,
                track_index=track.track_index,
                notes=tuple(projected),
                midis=midis,
                onsets=onsets,
                durations=durations,
                intervals=intervals,
                rhythm=rhythm,
                dur_tokens=dur_tokens,
                skyline_reduced=skyline_reduced,
            )
        )
    streams.sort(key=lambda item: item.track_index)
    return streams, any_skyline


def _project_skyline(
    notes: Sequence[CollapsedLogicalNote],
    scope_start: int,
    scope_end: int,
    *,
    track_index: int,
    track_events: Sequence,
) -> tuple[list[_ProjectedNote], bool]:
    """Skyline reduction: highest MIDI at each attack onset (melody-analyzer aligned)."""
    scoped = [
        note
        for note in notes
        if note_overlaps_interval(note, scope_start, scope_end)
        and clip_occupancy(note, scope_start, scope_end) > 0
    ]
    if not scoped:
        return [], False

    skyline_needed = _track_is_polyphonic(scoped)
    event_index_map = _build_event_index_map(track_events)

    if not skyline_needed:
        projected: list[_ProjectedNote] = []
        for note in sorted(scoped, key=lambda item: (item.start_tick, -item.midi_number, item.pitch)):
            if not (scope_start <= note.start_tick < scope_end):
                continue
            projected.append(
                _ProjectedNote(
                    source=note,
                    event_indexes=_resolve_event_indexes(note, event_index_map, track_events),
                    midi_number=note.midi_number,
                    start_tick=note.start_tick,
                    duration_ticks=clip_occupancy(note, scope_start, scope_end),
                )
            )
        return projected, False

    onsets = sorted(
        {note.start_tick for note in scoped if scope_start <= note.start_tick < scope_end}
    )
    projected = []
    for index, onset in enumerate(onsets):
        sounding = [note for note in scoped if note.start_tick <= onset < note.end_tick]
        if not sounding:
            continue
        chosen = max(sounding, key=lambda note: (note.midi_number, -note.start_tick, note.pitch))
        next_onset = onsets[index + 1] if index + 1 < len(onsets) else None
        clipped_end = min(chosen.end_tick, scope_end)
        if next_onset is not None:
            clipped_end = min(clipped_end, next_onset)
        duration = max(0, clipped_end - onset)
        if duration <= 0:
            continue
        projected.append(
            _ProjectedNote(
                source=chosen,
                event_indexes=_resolve_event_indexes(chosen, event_index_map, track_events),
                midi_number=chosen.midi_number,
                start_tick=onset,
                duration_ticks=duration,
            )
        )
    return projected, True


def _track_is_polyphonic(notes: Sequence[CollapsedLogicalNote]) -> bool:
    ordered = sorted(notes, key=lambda note: (note.start_tick, note.end_tick, note.midi_number))
    active_end = -1
    for note in ordered:
        if note.start_tick < active_end:
            return True
        active_end = max(active_end, note.end_tick)
    return False


def _build_event_index_map(track_events: Sequence) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, event in enumerate(track_events):
        if event.id is not None:
            mapping[event.id] = index
    return mapping


def _resolve_event_indexes(
    note: CollapsedLogicalNote,
    event_index_map: dict[str, int],
    track_events: Sequence,
) -> tuple[int, ...]:
    if note.source_event_ids:
        indexes = [event_index_map[eid] for eid in note.source_event_ids if eid in event_index_map]
        if indexes:
            return tuple(indexes)
    # Fallback: match by pitch/start for tie members or id-less events.
    indexes = []
    if note.tie_group_id is not None:
        for index, event in enumerate(track_events):
            if getattr(event, "tie", None) is not None and event.tie.group_id == note.tie_group_id:
                indexes.append(index)
    else:
        for index, event in enumerate(track_events):
            if event.start_tick == note.start_tick and event.pitch == note.pitch:
                indexes.append(index)
                break
    return tuple(indexes)


def _detect_matches(
    streams: Sequence[_TokenStream],
    ticks_per_quarter: int,
) -> tuple[list[_RawMatch], bool, int]:
    matches: list[_RawMatch] = []
    comparisons = 0
    truncated = False
    tables: dict[tuple[str, int, int], list[tuple[int, int]]] = {}

    for stream_idx, stream in enumerate(streams):
        n = len(stream.midis)
        for length in range(MIN_MOTIF_NOTES, min(MAX_MOTIF_NOTES, n) + 1):
            window = length - 1
            if window < 2:
                continue
            for start in range(0, n - length + 1):
                token_sets = _build_hash_token_sets(stream, start, length)
                for kind, tokens in token_sets:
                    digest = _rolling_hash(tokens)
                    key = (kind, length, digest)
                    bucket = tables.setdefault(key, [])
                    for prev_stream_idx, prev_start in bucket:
                        comparisons += 1
                        if comparisons > MAX_HASH_COMPARISONS:
                            truncated = True
                            return matches, truncated, comparisons
                        if len(matches) >= MAX_CANDIDATES_PER_TRACK * max(1, len(streams)):
                            truncated = True
                            return matches, truncated, comparisons

                        prev = streams[prev_stream_idx]
                        if prev_stream_idx == stream_idx:
                            prev_end = prev_start + length
                            if not (start + length <= prev_start or start >= prev_end):
                                continue
                        left, left_start, right, right_start = _ordered_spans(
                            prev,
                            prev_start,
                            stream,
                            start,
                        )
                        verified = None
                        if kind == "transposed":
                            for check_kind in (
                                "exact",
                                "transposed",
                                "inversion",
                                "augmentation",
                                "diminution",
                            ):
                                verified = _verify_match(
                                    check_kind,
                                    left,
                                    left_start,
                                    right,
                                    right_start,
                                    length,
                                    ticks_per_quarter,
                                )
                                if verified is not None:
                                    break
                        else:
                            verified = _verify_match(
                                kind,
                                left,
                                left_start,
                                right,
                                right_start,
                                length,
                                ticks_per_quarter,
                            )
                        if verified is None:
                            continue
                        ref_key = _SpanKey(left.track_id, left_start, length)
                        tgt_key = _SpanKey(right.track_id, right_start, length)
                        matches.append(
                            _RawMatch(
                                kind=verified[0],
                                reference=ref_key,
                                target=tgt_key,
                                transposition_semitones=verified[1],
                                time_scale_numerator=verified[2],
                                time_scale_denominator=verified[3],
                                identity_score=verified[4],
                            )
                        )
                    bucket.append((stream_idx, start))
                    if len(bucket) > MAX_CANDIDATES_PER_TRACK:
                        truncated = True
                        bucket[:] = bucket[-MAX_CANDIDATES_PER_TRACK:]
    return matches, truncated, comparisons


def _detect_scaled_matches(
    streams: Sequence[_TokenStream],
    ticks_per_quarter: int,
) -> tuple[list[_RawMatch], bool, int]:
    """Hash-indexed 2:1 / 1:2 time-scale detection (same pitch intervals)."""
    matches: list[_RawMatch] = []
    comparisons = 0
    truncated = False
    interval_tables: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for stream_idx, stream in enumerate(streams):
        n = len(stream.midis)
        for length in range(MIN_MOTIF_NOTES, min(MAX_MOTIF_NOTES, n) + 1):
            window = length - 1
            if window < 2:
                continue
            for start in range(0, n - length + 1):
                iv = stream.intervals[start : start + window]
                digest = _rolling_hash(iv)
                key = (length, digest)
                bucket = interval_tables.setdefault(key, [])
                for prev_stream_idx, prev_start in bucket:
                    comparisons += 1
                    if comparisons > MAX_HASH_COMPARISONS:
                        truncated = True
                        return matches, truncated, comparisons
                    if len(matches) >= MAX_CANDIDATES_PER_TRACK * max(1, len(streams)):
                        truncated = True
                        return matches, truncated, comparisons
                    prev = streams[prev_stream_idx]
                    if prev_stream_idx == stream_idx:
                        prev_end = prev_start + length
                        if not (start + length <= prev_start or start >= prev_end):
                            continue
                    left, left_start, right, right_start = _ordered_spans(
                        prev, prev_start, stream, start
                    )
                    for check_kind in ("augmentation", "diminution"):
                        verified = _verify_match(
                            check_kind,
                            left,
                            left_start,
                            right,
                            right_start,
                            length,
                            ticks_per_quarter,
                        )
                        if verified is None:
                            continue
                        matches.append(
                            _RawMatch(
                                kind=verified[0],
                                reference=_SpanKey(left.track_id, left_start, length),
                                target=_SpanKey(right.track_id, right_start, length),
                                transposition_semitones=verified[1],
                                time_scale_numerator=verified[2],
                                time_scale_denominator=verified[3],
                                identity_score=verified[4],
                            )
                        )
                bucket.append((stream_idx, start))
                if len(bucket) > MAX_CANDIDATES_PER_TRACK:
                    truncated = True
                    bucket[:] = bucket[-MAX_CANDIDATES_PER_TRACK:]
    return matches, truncated, comparisons


def _detect_inversion_matches(
    streams: Sequence[_TokenStream],
    ticks_per_quarter: int,
) -> tuple[list[_RawMatch], bool, int]:
    """Hash-indexed inversion: query negated intervals against a plain interval index."""
    matches: list[_RawMatch] = []
    comparisons = 0
    truncated = False
    tables: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for stream_idx, stream in enumerate(streams):
        n = len(stream.midis)
        for length in range(MIN_MOTIF_NOTES, min(MAX_MOTIF_NOTES, n) + 1):
            window = length - 1
            if window < 2:
                continue
            for start in range(0, n - length + 1):
                end = start + length
                plain = (
                    stream.intervals[start : start + window]
                    + stream.rhythm[start : start + window]
                    + stream.dur_tokens[start:end]
                )
                inverted = (
                    tuple(-iv for iv in stream.intervals[start : start + window])
                    + stream.rhythm[start : start + window]
                    + stream.dur_tokens[start:end]
                )
                inv_key = (length, _rolling_hash(inverted))
                for prev_stream_idx, prev_start in tables.get(inv_key, []):
                    comparisons += 1
                    if comparisons > MAX_HASH_COMPARISONS:
                        truncated = True
                        return matches, truncated, comparisons
                    if len(matches) >= MAX_CANDIDATES_PER_TRACK * max(1, len(streams)):
                        truncated = True
                        return matches, truncated, comparisons
                    prev = streams[prev_stream_idx]
                    if prev_stream_idx == stream_idx:
                        prev_end = prev_start + length
                        if not (start + length <= prev_start or start >= prev_end):
                            continue
                    left, left_start, right, right_start = _ordered_spans(
                        prev, prev_start, stream, start
                    )
                    verified = _verify_match(
                        "inversion",
                        left,
                        left_start,
                        right,
                        right_start,
                        length,
                        ticks_per_quarter,
                    )
                    if verified is None:
                        continue
                    matches.append(
                        _RawMatch(
                            kind=verified[0],
                            reference=_SpanKey(left.track_id, left_start, length),
                            target=_SpanKey(right.track_id, right_start, length),
                            transposition_semitones=verified[1],
                            time_scale_numerator=verified[2],
                            time_scale_denominator=verified[3],
                            identity_score=verified[4],
                        )
                    )
                plain_key = (length, _rolling_hash(plain))
                plain_bucket = tables.setdefault(plain_key, [])
                plain_bucket.append((stream_idx, start))
                if len(plain_bucket) > MAX_CANDIDATES_PER_TRACK:
                    truncated = True
                    plain_bucket[:] = plain_bucket[-MAX_CANDIDATES_PER_TRACK:]
    return matches, truncated, comparisons


def _build_hash_token_sets(
    stream: _TokenStream,
    start: int,
    length: int,
) -> list[tuple[str, tuple[int, ...]]]:
    window = length - 1
    end = start + length
    exact_tokens = stream.midis[start:end] + stream.rhythm[start : start + window] + stream.dur_tokens[start:end]
    transp_tokens = (
        stream.intervals[start : start + window]
        + stream.rhythm[start : start + window]
        + stream.dur_tokens[start:end]
    )
    rhythm_tokens = stream.rhythm[start : start + window] + stream.dur_tokens[start:end]
    inv_tokens = tuple(-iv for iv in stream.intervals[start : start + window]) + stream.rhythm[
        start : start + window
    ] + stream.dur_tokens[start:end]
    return [
        ("exact", exact_tokens),
        ("transposed", transp_tokens),
        ("inversion", inv_tokens),
        ("rhythm_only", rhythm_tokens),
    ]


def _verify_match(
    kind: str,
    left: _TokenStream,
    left_start: int,
    right: _TokenStream,
    right_start: int,
    length: int,
    ticks_per_quarter: int,
) -> tuple[ExtendedMotifKind, int | None, int | None, int | None, float] | None:
    window = length - 1
    left_end = left_start + length
    right_end = right_start + length
    if left_end > len(left.midis) or right_end > len(right.midis):
        return None

    left_rhythm = left.rhythm[left_start : left_start + window]
    right_rhythm = right.rhythm[right_start : right_start + window]
    left_dur = left.dur_tokens[left_start:left_end]
    right_dur = right.dur_tokens[right_start:right_end]
    left_iv = left.intervals[left_start : left_start + window]
    right_iv = right.intervals[right_start : right_start + window]

    score = compute_identity_score(
        left_rhythm=left_rhythm,
        right_rhythm=right_rhythm,
        left_intervals=left_iv,
        right_intervals=right_iv,
        left_durations=left_dur,
        right_durations=right_dur,
        left_count=length,
        right_count=length,
    )

    if kind == "rhythm_only":
        if left_rhythm != right_rhythm or left_dur != right_dur:
            return None
        return ("rhythm_only", None, None, None, score)

    if kind == "exact":
        if left.midis[left_start:left_end] != right.midis[right_start:right_end]:
            return None
        if left_rhythm != right_rhythm or left_dur != right_dur:
            return None
        return ("exact", 0, None, None, score)

    if kind == "transposed":
        if left_rhythm != right_rhythm or left_dur != right_dur:
            return None
        if left_iv != right_iv:
            return None
        transposition = right.midis[right_start] - left.midis[left_start]
        return ("transposed", transposition, None, None, score)

    if kind == "inversion":
        if left_rhythm != right_rhythm or left_dur != right_dur:
            return None
        if tuple(-iv for iv in left_iv) != right_iv:
            return None
        transposition = right.midis[right_start] - left.midis[left_start]
        return ("inversion", transposition, None, None, score)

    if kind == "augmentation":
        if left_iv != right_iv:
            return None
        if not _scaled_rhythm_match(left_rhythm, right_rhythm, numerator=2, denominator=1):
            return None
        if not _scaled_dur_match(left_dur, right_dur, numerator=2, denominator=1):
            return None
        transposition = right.midis[right_start] - left.midis[left_start]
        return ("augmentation", transposition, 2, 1, max(score, 0.6))

    if kind == "diminution":
        if left_iv != right_iv:
            return None
        if not _scaled_rhythm_match(left_rhythm, right_rhythm, numerator=1, denominator=2):
            return None
        if not _scaled_dur_match(left_dur, right_dur, numerator=1, denominator=2):
            return None
        transposition = right.midis[right_start] - left.midis[left_start]
        return ("diminution", transposition, 1, 2, max(score, 0.6))

    return None


def _scaled_rhythm_match(
    reference: Sequence[int],
    target: Sequence[int],
    *,
    numerator: int,
    denominator: int,
) -> bool:
    if len(reference) != len(target):
        return False
    for left, right in zip(reference, target, strict=True):
        expected = max(1, int(round(left * numerator / denominator)))
        if right != expected:
            return False
    return True


def _scaled_dur_match(
    reference: Sequence[int],
    target: Sequence[int],
    *,
    numerator: int,
    denominator: int,
) -> bool:
    if len(reference) != len(target):
        return False
    for left, right in zip(reference, target, strict=True):
        expected = max(1, int(round(left * numerator / denominator)))
        if right != expected:
            return False
    return True


def _ordered_spans(
    left: _TokenStream,
    left_start: int,
    right: _TokenStream,
    right_start: int,
) -> tuple[_TokenStream, int, _TokenStream, int]:
    left_tick = left.onsets[left_start]
    right_tick = right.onsets[right_start]
    if (left_tick, left.track_index, left_start) <= (right_tick, right.track_index, right_start):
        return left, left_start, right, right_start
    return right, right_start, left, left_start


def _promote_sequences(
    matches: list[_RawMatch],
    streams: Sequence[_TokenStream],
) -> list[_RawMatch]:
    """Upgrade transposed matches to sequence when 3+ spans share consistent steps."""
    by_signature: dict[tuple[str, int, tuple[int, ...], tuple[int, ...]], list[_RawMatch]] = {}
    for match in matches:
        if match.kind != "transposed":
            continue
        ref_stream = _stream_for_span(streams, match.reference)
        if ref_stream is None:
            continue
        start = match.reference.start_index
        length = match.reference.length
        sig = (
            ref_stream.track_id,
            length,
            ref_stream.intervals[start : start + length - 1],
            ref_stream.rhythm[start : start + length - 1],
        )
        by_signature.setdefault(sig, []).append(match)

    sequence_signatures: set[tuple[str, int, tuple[int, ...], tuple[int, ...]]] = set()
    for sig, group in by_signature.items():
        if len(group) < 2:
            continue
        span_entries: list[tuple[int, int | None]] = []
        track_id, length, _, _ = sig
        for match in group:
            ref_stream = _stream_for_span(streams, match.reference)
            tgt_stream = _stream_for_span(streams, match.target)
            if ref_stream is not None:
                span_entries.append(
                    (ref_stream.onsets[match.reference.start_index], match.transposition_semitones)
                )
            if tgt_stream is not None:
                span_entries.append(
                    (tgt_stream.onsets[match.target.start_index], match.transposition_semitones)
                )
        unique_ticks = sorted({tick for tick, _ in span_entries})
        if len(unique_ticks) < 3:
            continue
        tick_step = unique_ticks[1] - unique_ticks[0]
        if tick_step <= 0 or any(
            unique_ticks[i + 1] - unique_ticks[i] != tick_step for i in range(len(unique_ticks) - 1)
        ):
            continue
        transpositions = sorted(
            {trans for _, trans in span_entries if trans is not None}
        )
        if len(transpositions) < 2:
            continue
        trans_step = transpositions[1] - transpositions[0]
        if trans_step == 0 or any(
            transpositions[i + 1] - transpositions[i] != trans_step
            for i in range(len(transpositions) - 1)
        ):
            continue
        sequence_signatures.add(sig)

    promoted: list[_RawMatch] = []
    for match in matches:
        if match.kind != "transposed":
            promoted.append(match)
            continue
        ref_stream = _stream_for_span(streams, match.reference)
        if ref_stream is None:
            promoted.append(match)
            continue
        start = match.reference.start_index
        length = match.reference.length
        sig = (
            ref_stream.track_id,
            length,
            ref_stream.intervals[start : start + length - 1],
            ref_stream.rhythm[start : start + length - 1],
        )
        if sig in sequence_signatures:
            promoted.append(
                _RawMatch(
                    kind="sequence",
                    reference=match.reference,
                    target=match.target,
                    transposition_semitones=match.transposition_semitones,
                    time_scale_numerator=match.time_scale_numerator,
                    time_scale_denominator=match.time_scale_denominator,
                    identity_score=match.identity_score,
                )
            )
        else:
            promoted.append(match)
    return promoted


def _stream_for_span(streams: Sequence[_TokenStream], span: _SpanKey) -> _TokenStream | None:
    for stream in streams:
        if stream.track_id == span.track_id:
            return stream
    return None


def _group_into_families(
    matches: list[_RawMatch],
    streams: Sequence[_TokenStream],
) -> list[DetectedMotifFamily]:
    family_map: dict[tuple[str, int, int, int], DetectedMotifFamily] = {}
    stream_by_track = {stream.track_id: stream for stream in streams}

    for match in matches:
        ref_stream = stream_by_track.get(match.reference.track_id)
        if ref_stream is None:
            continue
        family_key = (
            match.reference.track_id,
            match.reference.start_index,
            match.reference.length,
            ref_stream.onsets[match.reference.start_index],
        )
        ref_occ = _build_occurrence(
            match.reference,
            ref_stream,
            kind=match.kind,
            transposition=None,
            time_scale_num=match.time_scale_numerator,
            time_scale_den=match.time_scale_denominator,
            identity_score=1.0,
            role="reference",
        )
        if family_key not in family_map:
            family_id = make_sha256_derived_id(
                "motif_family",
                match.reference.track_id,
                match.reference.start_index,
                match.reference.length,
                ref_stream.onsets[match.reference.start_index],
            )
            family_map[family_key] = DetectedMotifFamily(
                id=family_id,
                reference=ref_occ,
                matched_occurrences=[],
                relationship_kinds=[],
                note_count=match.reference.length,
                inference=InferenceMeta(
                    status="ok",
                    confidence=0.7,
                    evidence=AnalysisEvidence(count=1),
                    method=REPETITION_METHOD,
                    method_version=ANALYSIS_ALGORITHM_VERSION,
                ),
            )

        tgt_stream = stream_by_track.get(match.target.track_id)
        if tgt_stream is None:
            continue
        tgt_occ = _build_occurrence(
            match.target,
            tgt_stream,
            kind=match.kind,
            transposition=match.transposition_semitones,
            time_scale_num=match.time_scale_numerator,
            time_scale_den=match.time_scale_denominator,
            identity_score=match.identity_score,
            role="matched",
        )
        family = family_map[family_key]
        if any(item.id == tgt_occ.id for item in family.matched_occurrences):
            continue
        if tgt_occ.id == family.reference.id:
            continue
        if len(family.matched_occurrences) >= ANALYSIS_MAX_EVIDENCE_ITEMS:
            continue
        family.matched_occurrences.append(tgt_occ)
        kinds = set(family.relationship_kinds)
        kinds.add(match.kind)
        family.relationship_kinds = sorted(kinds, key=lambda k: _KIND_RANK.get(k, 9))

    return list(family_map.values())


def _build_occurrence(
    span: _SpanKey,
    stream: _TokenStream,
    *,
    kind: ExtendedMotifKind,
    transposition: int | None,
    time_scale_num: int | None,
    time_scale_den: int | None,
    identity_score: float,
    role: str,
) -> DetectedMotifOccurrence:
    start = span.start_index
    length = span.length
    end_index = start + length - 1
    start_tick = stream.onsets[start]
    end_tick = stream.onsets[end_index] + stream.durations[end_index]
    note_refs = [_note_reference(stream.notes[start + i]) for i in range(length)]
    # Keep actionable note refs on the family reference; omit from matched copies
    # so analysis.v1 payloads stay within response size caps.
    if role != "reference":
        note_refs = []
    occ_id = make_sha256_derived_id(
        "motif_occ",
        role,
        kind,
        span.track_id,
        start_tick,
        end_tick,
        length,
        transposition if transposition is not None else 0,
    )
    confidence = 0.7 if kind == "exact" else (0.65 if kind in {"transposed", "sequence"} else 0.55)
    return DetectedMotifOccurrence(
        id=occ_id,
        kind=kind,
        track_id=span.track_id,
        start_tick=start_tick,
        end_tick=end_tick,
        note_count=length,
        identity_score=round_analysis_float(identity_score),
        transposition_semitones=transposition,
        time_scale_numerator=time_scale_num,
        time_scale_denominator=time_scale_den,
        notes=note_refs,
        inference=InferenceMeta(
            status="ok",
            confidence=round_analysis_float(confidence),
            evidence=AnalysisEvidence(count=length),
            method=REPETITION_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _note_reference(note: _ProjectedNote) -> DetectedNoteReference:
    source = note.source
    if source.source_event_ids:
        return DetectedNoteReference(event_ids=list(source.source_event_ids))
    if note.event_indexes:
        return DetectedNoteReference(event_indexes=list(note.event_indexes))
    return DetectedNoteReference(event_indexes=[0])


def _flatten_legacy_motifs(families: Sequence[DetectedMotifFamily]) -> list[MotifOccurrence]:
    """Flatten matched targets for backward-compatible consumers."""
    motifs: list[MotifOccurrence] = []
    for family in families:
        for matched in family.matched_occurrences:
            if matched.kind not in _LEGACY_KINDS:
                continue
            motifs.append(
                MotifOccurrence(
                    id=make_derived_id(
                        "motif",
                        matched.kind,
                        matched.track_id,
                        matched.start_tick,
                        matched.end_tick,
                        matched.note_count,
                        matched.transposition_semitones if matched.transposition_semitones is not None else 0,
                    ),
                    kind=matched.kind,  # type: ignore[arg-type]
                    track_id=matched.track_id,
                    start_tick=matched.start_tick,
                    end_tick=matched.end_tick,
                    transposition_semitones=matched.transposition_semitones,
                    inference=matched.inference,
                )
            )
    return motifs


def _suppress_redundant_families(families: Sequence[DetectedMotifFamily]) -> list[DetectedMotifFamily]:
    ordered = sorted(
        families,
        key=lambda item: (
            min(_KIND_RANK.get(kind, 9) for kind in item.relationship_kinds) if item.relationship_kinds else 9,
            -item.note_count,
            item.reference.start_tick,
            item.reference.track_id or "",
            item.id,
        ),
    )
    kept: list[DetectedMotifFamily] = []
    for family in ordered:
        redundant = False
        for existing in kept:
            if existing.reference.track_id != family.reference.track_id:
                continue
            if not set(existing.relationship_kinds) & set(family.relationship_kinds):
                continue
            if (
                existing.reference.start_tick == family.reference.start_tick
                and existing.reference.end_tick == family.reference.end_tick
            ):
                redundant = True
                break
            if (
                existing.reference.start_tick <= family.reference.start_tick
                and family.reference.end_tick <= existing.reference.end_tick
                and existing.note_count >= family.note_count
            ):
                redundant = True
                break
        if not redundant:
            kept.append(family)
    return kept


def _suppress_redundant_flat(motifs: Sequence[MotifOccurrence]) -> list[MotifOccurrence]:
    ordered = sorted(
        motifs,
        key=lambda item: (
            _KIND_RANK.get(item.kind, 9),
            -(item.end_tick - item.start_tick),
            item.start_tick,
            item.track_id or "",
            item.id,
        ),
    )
    kept: list[MotifOccurrence] = []
    for motif in ordered:
        redundant = False
        for existing in kept:
            if existing.track_id != motif.track_id:
                continue
            if existing.kind != motif.kind:
                continue
            if motif.start_tick == existing.start_tick and motif.end_tick == existing.end_tick:
                redundant = True
                break
            if existing.start_tick <= motif.start_tick and motif.end_tick <= existing.end_tick:
                redundant = True
                break
        if not redundant:
            kept.append(motif)
    return kept


def _rolling_hash(tokens: Sequence[int]) -> int:
    value = 0
    base = 257
    for token in tokens:
        value = value * base + (int(token) + 1024)
    return value


def _section_fingerprint_ids(context: CompositionAnalysisContext) -> list[str]:
    ids: list[str] = []
    for index, section in enumerate(context.sections):
        start = section.start_tick
        end = section.start_tick + section.duration_ticks
        tokens: list[int] = []
        for track in context.tracks:
            if track.is_drum:
                continue
            for note in track.logical_notes:
                if not attack_in_interval(note, start, end):
                    continue
                tokens.append(note.pitch_class)
                tokens.append(quantize_duration(note.duration_ticks, context.composition.ticks_per_quarter))
                if len(tokens) >= 128:
                    break
            if len(tokens) >= 128:
                break
        digest = _rolling_hash(tokens) if tokens else 0
        ids.append(
            make_derived_id(
                "section_fp",
                index,
                section.id or section.type,
                digest,
            )
        )
    return ids
