"""Deterministic tonal-center analysis for harmony metadata and note events.

Assesses aggregate tonal center rather than strict diatonic membership. A few
chromatic tones, borrowed chords, secondary dominants, and harmonic/melodic-minor
alterations remain valid. Hard contradiction requires explicit metadata mismatch
or sufficiently strong evidence for a competing tonal center.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from ..schemas import Composition, CompositionV2, _midi_pitch_number
from .composition_timing import bar_duration_ticks, parse_time_signature


logger = logging.getLogger(__name__)

# Minimum chord events / note weight before hard tonal rejection is allowed.
MIN_HARMONY_EVIDENCE_COUNT = 3
MIN_NOTE_EVIDENCE_WEIGHT = 8.0
# Relative score margin a competitor must beat the requested key by:
# (winner - requested) / winner >= margin.
CONTRADICTION_SCORE_MARGIN = 0.35
# Minimum absolute combined score for a competitor to count as confident.
MIN_COMPETITOR_ABS_SCORE = 4.0
# Below this absolute evidence mass, prefer warning over hard failure.
MIN_TOTAL_EVIDENCE_MASS = 4.0

_NOTE_NAME_TO_PC = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}

_PC_TO_NAME = {
    0: "C",
    1: "C#",
    2: "D",
    3: "Eb",
    4: "E",
    5: "F",
    6: "F#",
    7: "G",
    8: "Ab",
    9: "A",
    10: "Bb",
    11: "B",
}

_CHORD_RE = re.compile(
    r"^\s*([A-Ga-g])([#b]?)"
    r"(maj7|maj9|maj|min7|min9|min|m7|m9|m|dim7|dim|aug|sus4|sus2|sus|7|9|6|°)?"
    r"(.*)?\s*$"
)

# Relative scale degrees for major / natural minor (pitch-class offsets).
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_NATURAL = (0, 2, 3, 5, 7, 8, 10)
_MINOR_HARMONIC_EXTRA = (11,)  # raised leading tone
_MINOR_MELODIC_EXTRA = (9, 11)  # raised 6 and 7


@dataclass(frozen=True)
class ParsedKey:
    tonic_pc: int
    mode: str  # "major" | "minor"
    label: str

    @property
    def relative_major_pc(self) -> int:
        return self.tonic_pc if self.mode == "major" else (self.tonic_pc + 3) % 12

    @property
    def relative_minor_pc(self) -> int:
        return self.tonic_pc if self.mode == "minor" else (self.tonic_pc + 9) % 12


@dataclass(frozen=True)
class ParsedChord:
    root_pc: int
    quality: str
    raw: str
    parseable: bool = True


@dataclass
class KeyScore:
    key: ParsedKey
    harmony_score: float = 0.0
    note_score: float = 0.0
    harmony_count: int = 0
    note_weight: float = 0.0

    @property
    def combined_score(self) -> float:
        return (self.harmony_score * 0.55) + (self.note_score * 0.45)


@dataclass
class TonalityAnalysisResult:
    requested_key: str
    requested_center: ParsedKey | None
    metadata_key: str | None
    metadata_matches: bool
    winning_candidate: str | None
    requested_score: float
    winning_score: float
    confidence: float
    margin: float
    unparseable_chord_count: int
    harmony_evidence_count: int
    note_evidence_weight: float
    status: str  # "ok" | "warning" | "contradiction"
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def contradicts(self) -> bool:
        return self.status == "contradiction"

    def summary(self) -> dict[str, Any]:
        return {
            "requested_key": self.requested_key,
            "metadata_key": self.metadata_key,
            "metadata_matches": self.metadata_matches,
            "winning_candidate": self.winning_candidate,
            "requested_score": round(self.requested_score, 4),
            "winning_score": round(self.winning_score, 4),
            "confidence": round(self.confidence, 4),
            "margin": round(self.margin, 4),
            "unparseable_chord_count": self.unparseable_chord_count,
            "harmony_evidence_count": self.harmony_evidence_count,
            "note_evidence_weight": round(self.note_evidence_weight, 3),
            "status": self.status,
            "reason": self.reason,
        }


def parse_key(key: str) -> ParsedKey | None:
    text = " ".join(key.strip().split())
    parts = text.split()
    if len(parts) != 2:
        return None
    root, mode = parts[0], parts[1].lower()
    if mode not in {"major", "minor"}:
        return None
    root_token = root.replace("♯", "#").replace("♭", "b")
    if len(root_token) >= 2 and root_token[1] == "b":
        lookup = f"{root_token[0].upper()}B"
    elif len(root_token) >= 2 and root_token[1] == "#":
        lookup = f"{root_token[0].upper()}#"
    else:
        lookup = root_token[0].upper()
    pc = _NOTE_NAME_TO_PC.get(lookup)
    if pc is None:
        return None
    label = f"{_PC_TO_NAME[pc]} {mode}"
    return ParsedKey(tonic_pc=pc, mode=mode, label=label)


def parse_chord_symbol(symbol: str) -> ParsedChord:
    raw = symbol.strip()
    match = _CHORD_RE.match(raw)
    if not match:
        return ParsedChord(root_pc=-1, quality="unknown", raw=raw, parseable=False)
    letter, accidental, quality_raw, _tail = match.groups()
    name = f"{letter.upper()}{accidental or ''}"
    if accidental == "b":
        name = f"{letter.upper()}B"
    elif accidental == "#":
        name = f"{letter.upper()}#"
    else:
        name = letter.upper()
    pc = _NOTE_NAME_TO_PC.get(name)
    if pc is None and accidental == "b":
        pc = _NOTE_NAME_TO_PC.get(f"{letter.upper()}B")
    if pc is None:
        return ParsedChord(root_pc=-1, quality="unknown", raw=raw, parseable=False)
    quality = _normalize_quality(quality_raw or "")
    return ParsedChord(root_pc=pc, quality=quality, raw=raw, parseable=True)


def analyze_composition_tonality(
    composition: Composition | CompositionV2,
    requested_key: str,
    *,
    section_boundary_bars: Iterable[int] | None = None,
) -> TonalityAnalysisResult:
    """Score requested vs competing tonal centers from harmony and non-drum notes."""
    requested = parse_key(requested_key)
    metadata_key = composition.key
    metadata_parsed = parse_key(metadata_key)
    metadata_matches = bool(
        requested
        and metadata_parsed
        and requested.tonic_pc == metadata_parsed.tonic_pc
        and requested.mode == metadata_parsed.mode
    )

    boundary_bars = set(section_boundary_bars or (section.start_bar for section in composition.sections))
    bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    numerator, _denominator = parse_time_signature(composition.time_signature)

    candidates = _all_candidate_keys()
    scores = {key.label: KeyScore(key=key) for key in candidates}

    unparseable = 0
    harmony_count = 0
    for item in composition.harmony:
        chord_symbol = item.chord if hasattr(item, "chord") else item.get("chord")
        bar = item.bar if hasattr(item, "bar") else item.get("bar")
        chord = parse_chord_symbol(str(chord_symbol))
        if not chord.parseable:
            unparseable += 1
            continue
        harmony_count += 1
        weight = 1.5 if bar in boundary_bars else 1.0
        # Phrase-edge emphasis for first/last bars.
        if bar == 1 or bar == composition.bar_count:
            weight *= 1.25
        for key in candidates:
            scores[key.label].harmony_score += _harmony_weight_for_key(chord, key) * weight
            scores[key.label].harmony_count += 1

    note_weight_total = 0.0
    for track in composition.tracks:
        if track.is_drum or track.role in {"drums", "percussion"}:
            continue
        for event in track.events:
            try:
                midi = _midi_pitch_number(event.pitch)
            except ValueError:
                continue
            pc = midi % 12
            metric = _metric_weight(event.start_tick, bar_ticks, numerator)
            # Phrase edges: start of composition / near end.
            if event.start_tick < bar_ticks or event.start_tick >= composition.duration_ticks - bar_ticks:
                metric *= 1.2
            weight = max(0.25, event.duration_ticks / float(composition.ticks_per_quarter)) * metric
            note_weight_total += weight
            for key in candidates:
                scores[key.label].note_score += _note_weight_for_key(pc, key) * weight
                scores[key.label].note_weight += weight

    harmony_total = sum(score.harmony_score for score in scores.values()) or 0.0
    note_total = sum(score.note_score for score in scores.values()) or 0.0

    ranked = sorted(
        scores.values(),
        key=lambda item: item.combined_score,
        reverse=True,
    )
    winner = ranked[0] if ranked else None
    requested_score_obj = scores.get(requested.label) if requested else None
    requested_abs = requested_score_obj.combined_score if requested_score_obj else 0.0
    winning_abs = winner.combined_score if winner else 0.0
    relative_margin = (
        (winning_abs - requested_abs) / winning_abs if winning_abs > 0 else 0.0
    )
    # Confidence: share of top-two absolute mass captured by the winner.
    second = ranked[1].combined_score if len(ranked) > 1 else 0.0
    top_mass = winning_abs + second
    confidence = (winning_abs / top_mass) if top_mass > 0 else 0.0

    evidence_mass = float(harmony_count) + (note_weight_total / 4.0)
    status, reason = _decide_status(
        requested=requested,
        metadata_matches=metadata_matches,
        winner=winner,
        requested_abs=requested_abs,
        winning_abs=winning_abs,
        relative_margin=relative_margin,
        confidence=confidence,
        harmony_count=harmony_count,
        note_weight_total=note_weight_total,
        evidence_mass=evidence_mass,
    )

    result = TonalityAnalysisResult(
        requested_key=requested_key,
        requested_center=requested,
        metadata_key=metadata_key,
        metadata_matches=metadata_matches,
        winning_candidate=winner.key.label if winner else None,
        requested_score=requested_abs,
        winning_score=winning_abs,
        confidence=confidence,
        margin=relative_margin,
        unparseable_chord_count=unparseable,
        harmony_evidence_count=harmony_count,
        note_evidence_weight=note_weight_total,
        status=status,
        reason=reason,
        evidence={
            "top_candidates": [
                {
                    "key": item.key.label,
                    "score": round(item.combined_score, 4),
                    "harmony_score": round(item.harmony_score, 3),
                    "note_score": round(item.note_score, 3),
                }
                for item in ranked[:5]
            ],
            "harmony_total": round(harmony_total, 3),
            "note_total": round(note_total, 3),
            "thresholds": {
                "min_harmony_evidence": MIN_HARMONY_EVIDENCE_COUNT,
                "min_note_evidence_weight": MIN_NOTE_EVIDENCE_WEIGHT,
                "contradiction_margin": CONTRADICTION_SCORE_MARGIN,
                "min_competitor_abs_score": MIN_COMPETITOR_ABS_SCORE,
                "min_total_evidence_mass": MIN_TOTAL_EVIDENCE_MASS,
            },
        },
    )
    logger.debug(
        "Completed tonal-center analysis",
        extra={
            "requested_key": requested_key,
            "winning_candidate": result.winning_candidate,
            "confidence": round(confidence, 4),
            "margin": round(relative_margin, 4),
            "unparseable_chord_count": unparseable,
            "harmony_evidence_count": harmony_count,
            "note_evidence_weight": round(note_weight_total, 3),
            "status": status,
            "threshold_decision": reason,
        },
    )
    if status == "contradiction":
        logger.warning(
            "Tonal-center contradiction detected",
            extra={
                "requested_key": requested_key,
                "winning_candidate": result.winning_candidate,
                "margin": round(relative_margin, 4),
                "reason": reason,
            },
        )
    elif status == "warning":
        logger.warning(
            "Tonal-center analysis ambiguous",
            extra={
                "requested_key": requested_key,
                "winning_candidate": result.winning_candidate,
                "reason": reason,
            },
        )
    else:
        logger.info(
            "Tonal-center analysis completed",
            extra={
                "requested_key": requested_key,
                "winning_candidate": result.winning_candidate,
                "status": status,
                "harmony_evidence_count": harmony_count,
            },
        )
    return result


def analyze_harmony_tonality(
    chords: Sequence[tuple[int, str]],
    requested_key: str,
    *,
    bar_count: int = 1,
    boundary_bars: Iterable[int] | None = None,
) -> TonalityAnalysisResult:
    """Lightweight harmony-only analysis for stage checks before assembly."""
    resolved_bars = max(1, bar_count, max((bar for bar, _chord in chords), default=1))
    duration_ticks = resolved_bars * 1920
    validated = Composition(
        tempo=120,
        key=requested_key if parse_key(requested_key) else "C major",
        time_signature="4/4",
        ticks_per_quarter=480,
        duration_ticks=duration_ticks,
        bar_count=resolved_bars,
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": resolved_bars,
                "start_tick": 0,
                "duration_ticks": duration_ticks,
            }
        ],
        tracks=[
            {
                "id": "placeholder",
                "name": "placeholder",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        harmony=[{"bar": bar, "chord": chord} for bar, chord in chords],
    )
    return analyze_composition_tonality(
        validated,
        requested_key,
        section_boundary_bars=boundary_bars,
    )


def _decide_status(
    *,
    requested: ParsedKey | None,
    metadata_matches: bool,
    winner: KeyScore | None,
    requested_abs: float,
    winning_abs: float,
    relative_margin: float,
    confidence: float,
    harmony_count: int,
    note_weight_total: float,
    evidence_mass: float,
) -> tuple[str, str]:
    if requested is None:
        return "warning", "requested_key_unparseable"

    if not metadata_matches:
        return "contradiction", "metadata_key_mismatch"

    if winner is None:
        return "warning", "no_candidates"

    same_center = (
        winner.key.tonic_pc == requested.tonic_pc and winner.key.mode == requested.mode
    )
    if same_center:
        return "ok", "requested_center_wins"

    insufficient_harmony = harmony_count < MIN_HARMONY_EVIDENCE_COUNT
    insufficient_notes = note_weight_total < MIN_NOTE_EVIDENCE_WEIGHT
    if insufficient_harmony and insufficient_notes:
        return "warning", "insufficient_evidence"
    if evidence_mass < MIN_TOTAL_EVIDENCE_MASS:
        return "warning", "sparse_evidence"

    competitor_strong = (
        winning_abs >= MIN_COMPETITOR_ABS_SCORE
        and relative_margin >= CONTRADICTION_SCORE_MARGIN
    )
    if competitor_strong:
        return "contradiction", "competing_tonal_center"

    if relative_margin > 0.2 and winning_abs >= 2.0:
        return "warning", "ambiguous_competing_center"

    return "ok", "requested_center_within_tolerance"


def _all_candidate_keys() -> list[ParsedKey]:
    keys: list[ParsedKey] = []
    for pc in range(12):
        for mode in ("major", "minor"):
            keys.append(ParsedKey(tonic_pc=pc, mode=mode, label=f"{_PC_TO_NAME[pc]} {mode}"))
    return keys


def _normalize_quality(raw: str) -> str:
    text = raw.lower().replace("°", "dim")
    if text in {"", "maj", "major"}:
        return "maj"
    if text in {"m", "min", "minor"}:
        return "min"
    if text in {"7"}:
        return "dom7"
    if text in {"maj7", "maj9"}:
        return "maj7"
    if text in {"m7", "min7", "m9", "min9"}:
        return "min7"
    if text in {"dim", "dim7"}:
        return "dim"
    if text in {"aug"}:
        return "aug"
    if text.startswith("sus"):
        return "sus"
    if text in {"6", "9"}:
        return "maj"
    return text or "maj"


def _diatonic_pitch_classes(key: ParsedKey) -> set[int]:
    base = _MAJOR_SCALE if key.mode == "major" else _MINOR_NATURAL
    pcs = {(key.tonic_pc + interval) % 12 for interval in base}
    if key.mode == "minor":
        for interval in _MINOR_HARMONIC_EXTRA + _MINOR_MELODIC_EXTRA:
            pcs.add((key.tonic_pc + interval) % 12)
    return pcs


def _harmony_weight_for_key(chord: ParsedChord, key: ParsedKey) -> float:
    degree = (chord.root_pc - key.tonic_pc) % 12
    quality = chord.quality
    diatonic = _diatonic_pitch_classes(key)

    # Tonic
    if degree == 0:
        if key.mode == "minor" and quality in {"min", "min7"}:
            return 3.0
        if key.mode == "major" and quality in {"maj", "maj7", "dom7"}:
            return 3.0
        return 2.2

    # Dominant / V and V7 (including raised leading-tone dominant in minor)
    if degree == 7:
        if quality in {"dom7", "maj", "maj7"}:
            return 2.6
        return 2.0

    # Subdominant IV / iv
    if degree == 5:
        return 2.0

    # Relative major tonic (III in minor) — weak support, not a center steal alone
    if key.mode == "minor" and degree == 3 and quality in {"maj", "maj7", "dom7"}:
        return 0.6

    # Relative minor tonic (vi in major)
    if key.mode == "major" and degree == 9 and quality in {"min", "min7"}:
        return 0.6

    # Secondary dominant tendency: major/dom7 on non-diatonic or II/VI etc.
    if quality in {"dom7", "maj"} and degree in {2, 4, 9, 11, 1, 3, 6, 8, 10}:
        return 1.1

    # Borrowed / chromatic but root still nearby
    if chord.root_pc in diatonic:
        return 1.0

    return 0.25


def _note_weight_for_key(pc: int, key: ParsedKey) -> float:
    degree = (pc - key.tonic_pc) % 12
    if degree == 0:
        return 2.2
    if degree == 7:
        return 1.7
    if degree == 3 and key.mode == "minor":
        return 1.5
    if degree == 4 and key.mode == "major":
        return 1.5
    if pc in _diatonic_pitch_classes(key):
        return 1.0
    return 0.25


def _metric_weight(start_tick: int, bar_ticks: int, numerator: int) -> float:
    if bar_ticks <= 0:
        return 1.0
    pos = start_tick % bar_ticks
    beat_ticks = bar_ticks / float(max(1, numerator))
    beat_index = int(pos / beat_ticks) if beat_ticks else 0
    if beat_index == 0 and pos < beat_ticks * 0.15:
        return 1.5
    if beat_index == 2 and numerator >= 4:
        return 1.2
    return 1.0
