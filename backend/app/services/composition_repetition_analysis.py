"""Bounded exact / transposed / rhythm-only motif detection.

Uses normalized interval and rhythm tokens with rolling hashes plus exact
verification, non-overlap preference, redundancy suppression, section
fingerprints, deterministic ordering, and explicit search truncation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Sequence

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_MOTIFS,
    AnalysisEvidence,
    InferenceMeta,
    MotifOccurrence,
    RepetitionAnalysisResult,
    make_derived_id,
    round_analysis_float,
)
from app.services.composition_analysis_context import CompositionAnalysisContext
from app.services.composition_logical_notes import CollapsedLogicalNote, attack_in_interval


logger = logging.getLogger(__name__)

REPETITION_METHOD = "repetition.native.v1"

# Search / motif bounds (documented fixed policy).
MIN_MOTIF_NOTES = 3
MAX_MOTIF_NOTES = 12
MAX_HASH_COMPARISONS = 50_000
MAX_CANDIDATES_PER_TRACK = 256

MotifKind = Literal["exact", "transposed", "rhythm_only"]

_KIND_RANK = {"exact": 0, "transposed": 1, "rhythm_only": 2}


@dataclass(frozen=True)
class _TokenStream:
    track_id: str
    track_index: int
    # Per-note absolute pitch / onset / duration for verification
    midis: tuple[int, ...]
    onsets: tuple[int, ...]
    durations: tuple[int, ...]
    # Interval between consecutive pitches (len = n-1)
    intervals: tuple[int, ...]
    # Quantized IOI tokens between consecutive onsets (len = n-1)
    rhythm: tuple[int, ...]


def analyze_repetition_from_context(
    context: CompositionAnalysisContext,
) -> RepetitionAnalysisResult:
    """Detect bounded repeated material and section fingerprints."""
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
    streams = _build_token_streams(context)
    logger.debug(
        "Repetition token streams",
        extra={
            "stream_count": len(streams),
            "total_notes": sum(len(stream.midis) for stream in streams),
        },
    )

    section_ids = _section_fingerprint_ids(context)
    if not streams:
        status = "insufficient_evidence"
        logger.info(
            "Repetition analysis complete",
            extra={"status": status, "motif_count": 0, "section_fingerprint_count": len(section_ids)},
        )
        return RepetitionAnalysisResult(
            motifs=[],
            section_fingerprint_ids=section_ids,
            inference=InferenceMeta(
                status=status,
                confidence=None,
                evidence=AnalysisEvidence(count=0),
                method=REPETITION_METHOD,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    motifs, truncated, comparisons = _detect_motifs(streams)
    logger.debug(
        "Repetition search summary",
        extra={
            "hash_comparisons": comparisons,
            "raw_motif_count": len(motifs),
            "truncated": truncated,
        },
    )

    motifs = _suppress_redundant(motifs)
    motifs.sort(
        key=lambda item: (
            _KIND_RANK.get(item.kind, 9),
            item.start_tick,
            item.end_tick,
            item.track_id or "",
            item.id,
        )
    )

    status: str = "ok"
    if truncated:
        status = "truncated"
        logger.warning(
            "Motif search truncated",
            extra={"code": "motif_search_truncated", "kept": min(len(motifs), ANALYSIS_MAX_MOTIFS)},
        )

    if len(motifs) > ANALYSIS_MAX_MOTIFS:
        motifs = motifs[:ANALYSIS_MAX_MOTIFS]
        status = "truncated"
        logger.warning(
            "Motif results truncated",
            extra={"code": "result_truncated", "kept": ANALYSIS_MAX_MOTIFS},
        )

    confidence = None
    if motifs:
        confidence = round_analysis_float(min(1.0, 0.4 + 0.1 * len(motifs)))

    logger.info(
        "Repetition analysis complete",
        extra={
            "status": status,
            "motif_count": len(motifs),
            "section_fingerprint_count": len(section_ids),
            "truncated": truncated or status == "truncated",
        },
    )

    return RepetitionAnalysisResult(
        motifs=motifs,
        section_fingerprint_ids=section_ids,
        inference=InferenceMeta(
            status=status,  # type: ignore[arg-type]
            confidence=confidence,
            evidence=AnalysisEvidence(count=len(motifs)),
            method=REPETITION_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _build_token_streams(context: CompositionAnalysisContext) -> list[_TokenStream]:
    scope = context.resolved_scope
    streams: list[_TokenStream] = []
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
        # Monophonic projection: keep lowest pitch at shared onsets for stability.
        by_onset: dict[int, CollapsedLogicalNote] = {}
        for note in sorted(notes, key=lambda item: (item.start_tick, item.midi_number)):
            existing = by_onset.get(note.start_tick)
            if existing is None or note.midi_number < existing.midi_number:
                by_onset[note.start_tick] = note
        ordered = [by_onset[tick] for tick in sorted(by_onset)]
        if len(ordered) < MIN_MOTIF_NOTES:
            continue
        midis = tuple(note.midi_number for note in ordered)
        onsets = tuple(note.start_tick for note in ordered)
        durations = tuple(note.duration_ticks for note in ordered)
        intervals = tuple(midis[i + 1] - midis[i] for i in range(len(midis) - 1))
        rhythm = tuple(_quantize_ioi(onsets[i + 1] - onsets[i], context.composition.ticks_per_quarter)
                       for i in range(len(onsets) - 1))
        streams.append(
            _TokenStream(
                track_id=track.track_id,
                track_index=track.track_index,
                midis=midis,
                onsets=onsets,
                durations=durations,
                intervals=intervals,
                rhythm=rhythm,
            )
        )
    streams.sort(key=lambda item: item.track_index)
    return streams


def _quantize_ioi(delta: int, ticks_per_quarter: int) -> int:
    """Map IOI to nearest sixteenth of a quarter (stable rhythm token)."""
    step = max(1, ticks_per_quarter // 4)
    return int(round(delta / float(step)))


def _detect_motifs(
    streams: Sequence[_TokenStream],
) -> tuple[list[MotifOccurrence], bool, int]:
    motifs: list[MotifOccurrence] = []
    comparisons = 0
    truncated = False
    # Hash tables keyed by (kind, length, hash) → list of (stream_idx, start_note_idx)
    tables: dict[tuple[str, int, int], list[tuple[int, int]]] = {}

    for stream_idx, stream in enumerate(streams):
        n = len(stream.midis)
        for length in range(MIN_MOTIF_NOTES, min(MAX_MOTIF_NOTES, n) + 1):
            window = length - 1  # interval/rhythm token length
            if window < 2:
                continue
            for start in range(0, n - length + 1):
                end = start + length
                exact_tokens = stream.midis[start:end] + stream.rhythm[start : start + window]
                transp_tokens = stream.intervals[start : start + window] + stream.rhythm[start : start + window]
                rhythm_tokens = stream.rhythm[start : start + window]

                for kind, tokens in (
                    ("exact", exact_tokens),
                    ("transposed", transp_tokens),
                    ("rhythm_only", rhythm_tokens),
                ):
                    digest = _rolling_hash(tokens)
                    key = (kind, length, digest)
                    bucket = tables.setdefault(key, [])
                    for prev_stream_idx, prev_start in bucket:
                        comparisons += 1
                        if comparisons > MAX_HASH_COMPARISONS:
                            truncated = True
                            return motifs, truncated, comparisons
                        if len(motifs) >= MAX_CANDIDATES_PER_TRACK * max(1, len(streams)):
                            truncated = True
                            return motifs, truncated, comparisons

                        prev = streams[prev_stream_idx]
                        if not _exact_verify(kind, prev, prev_start, stream, start, length):  # type: ignore[arg-type]
                            continue
                        # Prefer non-overlapping occurrences within same track.
                        if prev_stream_idx == stream_idx:
                            prev_end = prev_start + length
                            if not (end <= prev_start or start >= prev_end):
                                continue
                        occurrence = _make_occurrence(
                            kind=kind,  # type: ignore[arg-type]
                            stream=stream,
                            start=start,
                            length=length,
                            reference=prev,
                            reference_start=prev_start,
                        )
                        motifs.append(occurrence)
                    bucket.append((stream_idx, start))
                    if len(bucket) > MAX_CANDIDATES_PER_TRACK:
                        truncated = True
                        bucket[:] = bucket[-MAX_CANDIDATES_PER_TRACK:]
    return motifs, truncated, comparisons


def _exact_verify(
    kind: MotifKind,
    left: _TokenStream,
    left_start: int,
    right: _TokenStream,
    right_start: int,
    length: int,
) -> bool:
    window = length - 1
    left_end = left_start + length
    right_end = right_start + length
    if left_end > len(left.midis) or right_end > len(right.midis):
        return False
    left_rhythm = left.rhythm[left_start : left_start + window]
    right_rhythm = right.rhythm[right_start : right_start + window]
    if left_rhythm != right_rhythm:
        return False
    if kind == "rhythm_only":
        return True
    left_iv = left.intervals[left_start : left_start + window]
    right_iv = right.intervals[right_start : right_start + window]
    if kind == "transposed":
        return left_iv == right_iv
    # exact: same absolute pitches and rhythm
    return left.midis[left_start:left_end] == right.midis[right_start:right_end]


def _make_occurrence(
    *,
    kind: MotifKind,
    stream: _TokenStream,
    start: int,
    length: int,
    reference: _TokenStream,
    reference_start: int,
) -> MotifOccurrence:
    start_tick = stream.onsets[start]
    last = start + length - 1
    end_tick = stream.onsets[last] + stream.durations[last]
    transposition = None
    if kind == "transposed":
        transposition = stream.midis[start] - reference.midis[reference_start]
    return MotifOccurrence(
        id=make_derived_id(
            "motif",
            kind,
            stream.track_id,
            start_tick,
            end_tick,
            length,
            transposition if transposition is not None else 0,
        ),
        kind=kind,
        track_id=stream.track_id,
        start_tick=start_tick,
        end_tick=end_tick,
        transposition_semitones=transposition,
        inference=InferenceMeta(
            status="ok",
            confidence=0.7 if kind == "exact" else (0.6 if kind == "transposed" else 0.5),
            evidence=AnalysisEvidence(count=2),
            method=REPETITION_METHOD,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _suppress_redundant(motifs: Sequence[MotifOccurrence]) -> list[MotifOccurrence]:
    """Drop contained / duplicate occurrences preferring longer exact matches."""
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
            # Same span or fully contained in a longer kept motif.
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
    """Deterministic polynomial hash over integer tokens (native int, no overflow)."""
    value = 0
    base = 257
    for token in tokens:
        value = value * base + (int(token) + 1024)
    return value


def _section_fingerprint_ids(context: CompositionAnalysisContext) -> list[str]:
    """Stable section content fingerprints (IDs only — not form labels)."""
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
                tokens.append(_quantize_ioi(note.duration_ticks, context.composition.ticks_per_quarter))
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
