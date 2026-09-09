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
from typing import Any, Iterable, Sequence, TYPE_CHECKING

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_CANDIDATES,
    AnalysisEvidence,
    InferenceMeta,
    KeyCandidateScore,
    KeySpanResult,
    make_derived_id,
    round_analysis_float,
)
from app.analysis_schemas import TonalityAnalysisResult as AnalysisTonalityResult
from ..schemas import Composition, CompositionV2, _midi_pitch_number
from .composition_timing import bar_duration_ticks, parse_time_signature

if TYPE_CHECKING:
    from .composition_analysis_context import CompositionAnalysisContext


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

# --- Analysis-path inference thresholds (duration mass in quarter-note units) ---
MIN_INFERENCE_MASS_QUARTERS = MIN_NOTE_EVIDENCE_WEIGHT
MIN_LOCAL_INFERENCE_MASS_QUARTERS = 2.0
# (winner - runner_up) / winner below this with relative keys => ambiguous.
RELATIVE_AMBIGUITY_MARGIN = 0.15
# Fixed local-key window size and smoothing penalty ratio of top raw score.
LOCAL_KEY_WINDOW_BARS = 2
LOCAL_TRANSITION_PENALTY_RATIO = 0.15
# Extra mass multipliers for optional bass / phrase-edge histograms.
BASS_MASS_BOOST = 0.5
EDGE_MASS_BOOST = 0.35
# Ranking: mode major before minor when scores and pitch_class tie.
_MODE_RANK = {"major": 0, "minor": 1}

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
    r"(maj7|maj9|maj13|maj|min7b5|min7|min9|min|m7b5|m7|m9|m|dim7|dim|aug|sus4|sus2|sus|add9|add2|7|9|11|13|6|°)?"
    r"(.*?)\s*$"
)

_SLASH_BASS_RE = re.compile(r"^(.*?)/([A-Ga-g][#b]?)\s*$")
_ALTERATION_TOKEN_RE = re.compile(r"(?:^|[(),\s])([#b]?(?:9|11|13|5|6))(?=$|[(),\s])")
_EXTENSION_TOKEN_RE = re.compile(r"(?:^|[(),\s])(add(?:2|4|6|9)|omit[35]|no[35]|alt)(?=$|[(),\s])", re.I)

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
    authored_spelling: str = ""
    bass_pc: int | None = None
    extensions: tuple[str, ...] = ()
    alterations: tuple[str, ...] = ()
    unknown_suffix: str | None = None
    pitch_classes: frozenset[int] = frozenset()

    @property
    def has_unknown_syntax(self) -> bool:
        return bool(self.unknown_suffix)


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
    authored = raw
    if not raw:
        return ParsedChord(
            root_pc=-1,
            quality="unknown",
            raw=raw,
            parseable=False,
            authored_spelling=authored,
        )

    bass_pc: int | None = None
    body = raw
    slash = _SLASH_BASS_RE.match(raw)
    if slash:
        body = slash.group(1).strip()
        bass_token = slash.group(2).replace("♯", "#").replace("♭", "b")
        bass_pc = _note_token_to_pc(bass_token)

    match = _CHORD_RE.match(body)
    if not match:
        return ParsedChord(
            root_pc=-1,
            quality="unknown",
            raw=raw,
            parseable=False,
            authored_spelling=authored,
            bass_pc=bass_pc,
            unknown_suffix=body or None,
        )

    letter, accidental, quality_raw, tail = match.groups()
    pc = _note_token_to_pc(f"{letter}{accidental or ''}")
    if pc is None:
        return ParsedChord(
            root_pc=-1,
            quality="unknown",
            raw=raw,
            parseable=False,
            authored_spelling=authored,
            bass_pc=bass_pc,
            unknown_suffix=body,
        )

    quality = _normalize_quality(quality_raw or "")
    alterations, extensions, remainder = _parse_chord_tail(tail or "")
    unknown_suffix = remainder.strip() or None
    # Unknown suffixes stay authored metadata; chord remains parseable from root/quality.
    pitch_classes = _chord_pitch_classes(pc, quality, alterations=alterations, bass_pc=bass_pc)
    return ParsedChord(
        root_pc=pc,
        quality=quality,
        raw=raw,
        parseable=True,
        authored_spelling=authored,
        bass_pc=bass_pc,
        extensions=tuple(extensions),
        alterations=tuple(alterations),
        unknown_suffix=unknown_suffix,
        pitch_classes=frozenset(pitch_classes),
    )


def _note_token_to_pc(token: str) -> int | None:
    text = token.strip().replace("♯", "#").replace("♭", "b")
    if not text:
        return None
    letter = text[0].upper()
    accidental = text[1:] if len(text) > 1 else ""
    if accidental == "b":
        lookup = f"{letter}B"
    elif accidental == "#":
        lookup = f"{letter}#"
    elif accidental == "":
        lookup = letter
    else:
        return None
    return _NOTE_NAME_TO_PC.get(lookup)


def _parse_chord_tail(tail: str) -> tuple[list[str], list[str], str]:
    text = tail.strip()
    if not text:
        return [], [], ""
    alterations = [token.lower() for token in _ALTERATION_TOKEN_RE.findall(text)]
    extensions = [token.lower() for token in _EXTENSION_TOKEN_RE.findall(text)]
    remainder = text
    for token in alterations + extensions:
        remainder = re.sub(re.escape(token), " ", remainder, flags=re.I)
    remainder = re.sub(r"[(),\s]+", " ", remainder).strip()
    return alterations, extensions, remainder


def _chord_pitch_classes(
    root_pc: int,
    quality: str,
    *,
    alterations: Sequence[str] = (),
    bass_pc: int | None = None,
) -> set[int]:
    intervals = {
        "maj": (0, 4, 7),
        "min": (0, 3, 7),
        "dom7": (0, 4, 7, 10),
        "maj7": (0, 4, 7, 11),
        "min7": (0, 3, 7, 10),
        "min7b5": (0, 3, 6, 10),
        "dim": (0, 3, 6),
        "dim7": (0, 3, 6, 9),
        "aug": (0, 4, 8),
        "sus": (0, 5, 7),
    }.get(quality, (0, 4, 7))
    pcs = {(root_pc + interval) % 12 for interval in intervals}
    for alteration in alterations:
        token = alteration.lower()
        if token in {"b9", "#9", "b5", "#5", "b13", "#11", "11", "13", "9"}:
            # Represent common tensions as pitch-class color without inventing voicing.
            mapping = {
                "b9": 1,
                "9": 2,
                "#9": 3,
                "b5": 6,
                "#5": 8,
                "#11": 6,
                "11": 5,
                "13": 9,
                "b13": 8,
            }
            pcs.add((root_pc + mapping[token]) % 12)
    if bass_pc is not None:
        pcs.add(bass_pc % 12)
    return pcs


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
    from app.services.composition_timeline import compile_timeline

    timeline = None
    try:
        timeline = compile_timeline(composition)
    except (TypeError, ValueError, KeyError, AttributeError):
        timeline = None
    for item in composition.harmony:
        chord_symbol = item.chord if hasattr(item, "chord") else item.get("chord")
        if hasattr(item, "start_tick"):
            bar = timeline.bar_at_tick(int(item.start_tick)) if timeline is not None else 1
        elif hasattr(item, "bar"):
            bar = item.bar
        else:
            bar = item.get("bar")
            if bar is None and item.get("start_tick") is not None and timeline is not None:
                bar = timeline.bar_at_tick(int(item["start_tick"]))
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
    if text in {"maj7", "maj9", "maj13"}:
        return "maj7"
    if text in {"m7", "min7", "m9", "min9"}:
        return "min7"
    if text in {"m7b5", "min7b5", "halfdim"}:
        return "min7b5"
    if text == "dim7":
        return "dim7"
    if text == "dim":
        return "dim"
    if text in {"aug"}:
        return "aug"
    if text.startswith("sus"):
        return "sus"
    if text in {"6", "9", "11", "13", "add9", "add2"}:
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


# ---------------------------------------------------------------------------
# Analysis-path histogram scoring and neutral key inference
# ---------------------------------------------------------------------------


def score_keys_from_pitch_histogram(
    pitch_class_mass: Sequence[float],
    *,
    bass_mass: Sequence[float] | None = None,
    edge_mass: Sequence[float] | None = None,
    harmony_evidence: Sequence[tuple[ParsedChord, float]] | None = None,
) -> list[KeyCandidateScore]:
    """Score all 24 major/minor keys from duration-weighted pitch-class mass.

    Ranking is deterministic: score descending, then pitch_class ascending, then
    mode major-before-minor. Floats are rounded via ``round_analysis_float``.
    """
    ranked = _score_key_candidates(
        pitch_class_mass,
        bass_mass=bass_mass,
        edge_mass=edge_mass,
        harmony_evidence=harmony_evidence,
    )
    return [_key_score_to_candidate(item) for item in ranked[:ANALYSIS_MAX_CANDIDATES]]


def infer_tonality_from_context(
    context: CompositionAnalysisContext,
) -> AnalysisTonalityResult:
    """Infer global/local tonality from a request-scoped analysis context.

    Does not require a requested key. Declared ``composition.key`` / ``key_changes``
    are compared for labeling and contradiction logging only.
    """
    composition = context.composition
    scope = context.resolved_scope
    declared_key = composition.key
    tpq = float(composition.ticks_per_quarter)
    start_bar = scope.start_bar
    end_bar_exclusive = scope.end_bar_exclusive

    pitched = context.notes_overlapping_scope(drums=False)
    drum_only = context.notes_overlapping_scope(drums=True)
    if not pitched and drum_only:
        logger.warning(
            "Tonality inference abstained",
            extra={"code": "percussion_only_scope", "scope_kind": scope.kind},
        )
        empty = _abstain_result(
            declared_key=declared_key,
            status="not_applicable",
            start_tick=scope.start_tick,
            end_tick=scope.end_tick,
            start_bar=start_bar,
            end_bar_exclusive=end_bar_exclusive,
            evidence_count=len(drum_only),
            evidence_mass=0.0,
        )
        logger.info(
            "Tonality inference complete",
            extra={
                "status": "not_applicable",
                "accepted_global": 0,
                "accepted_local_spans": 0,
            },
        )
        return empty

    pc_mass = _scoped_pitch_class_mass(context, start_bar, end_bar_exclusive)
    total_mass = sum(pc_mass)
    mass_quarters = total_mass / tpq if tpq > 0 else 0.0
    edge_mass = _edge_mass_for_range(context, start_bar, end_bar_exclusive)
    bass_mass = _bass_mass_for_range(context, start_bar, end_bar_exclusive)
    harmony_evidence = _harmony_evidence_for_bars(composition, start_bar, end_bar_exclusive)

    if mass_quarters < MIN_INFERENCE_MASS_QUARTERS and not harmony_evidence:
        logger.warning(
            "Tonality inference abstained",
            extra={
                "code": "insufficient_tonal_evidence",
                "mass_quarters": round_analysis_float(mass_quarters),
            },
        )
        result = _abstain_result(
            declared_key=declared_key,
            status="insufficient_evidence",
            start_tick=scope.start_tick,
            end_tick=scope.end_tick,
            start_bar=start_bar,
            end_bar_exclusive=end_bar_exclusive,
            evidence_count=len(pitched),
            evidence_mass=mass_quarters,
        )
        logger.info(
            "Tonality inference complete",
            extra={
                "status": "insufficient_evidence",
                "accepted_global": 0,
                "accepted_local_spans": 0,
            },
        )
        return result

    ranked = _score_key_candidates(
        pc_mass,
        bass_mass=bass_mass,
        edge_mass=edge_mass,
        harmony_evidence=harmony_evidence or None,
    )
    candidates = [_key_score_to_candidate(item) for item in ranked[:ANALYSIS_MAX_CANDIDATES]]
    logger.debug(
        "Global key candidate summary",
        extra={
            "top_labels": [item.key for item in candidates[:5]],
            "candidate_count": len(candidates),
            "mass_quarters": round_analysis_float(mass_quarters),
        },
    )

    global_status, global_key_label, confidence, margin = _decide_inference_status(ranked, mass_quarters)
    coverage = _coverage_ratio(pc_mass, ranked[0].key) if ranked else 0.0
    declared_parsed = parse_key(declared_key)
    global_span = KeySpanResult(
        id=make_derived_id("tonality", "global", scope.kind, start_bar, end_bar_exclusive),
        start_tick=scope.start_tick,
        end_tick=scope.end_tick,
        start_bar=start_bar,
        end_bar_exclusive=end_bar_exclusive,
        key=global_key_label if global_status in {"ok", "ambiguous"} else None,
        declared_key=declared_key,
        candidates=candidates,
        inference=InferenceMeta(
            status=global_status,
            confidence=confidence,
            evidence=AnalysisEvidence(
                count=len(pitched),
                mass=round_analysis_float(mass_quarters),
                coverage=round_analysis_float(coverage),
            ),
            method=ANALYSIS_ALGORITHM_VERSION,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )

    if (
        global_status == "ok"
        and global_key_label
        and declared_parsed
        and ranked
        and not (
            ranked[0].key.tonic_pc == declared_parsed.tonic_pc
            and ranked[0].key.mode == declared_parsed.mode
        )
    ):
        winning_abs = ranked[0].combined_score
        declared_score = next(
            (
                item.combined_score
                for item in ranked
                if item.key.tonic_pc == declared_parsed.tonic_pc
                and item.key.mode == declared_parsed.mode
            ),
            0.0,
        )
        relative_margin = (
            (winning_abs - declared_score) / winning_abs if winning_abs > 0 else 0.0
        )
        if (
            mass_quarters >= MIN_INFERENCE_MASS_QUARTERS
            and winning_abs >= MIN_COMPETITOR_ABS_SCORE
            and relative_margin >= CONTRADICTION_SCORE_MARGIN
        ):
            logger.warning(
                "Declared key conflicts with inference",
                extra={
                    "code": "declared_key_conflicts_with_inference",
                    "declared_key": declared_key,
                    "inferred_key": global_key_label,
                    "margin": round_analysis_float(relative_margin),
                },
            )

    if global_status == "ambiguous":
        logger.warning(
            "Tonality inference ambiguous",
            extra={
                "code": "relative_key_ambiguity",
                "top_labels": [item.key for item in candidates[:2]],
                "margin": round_analysis_float(margin),
            },
        )

    local_spans = _infer_local_key_spans(context, start_bar, end_bar_exclusive)
    accepted_local = sum(1 for span in local_spans if span.inference.status == "ok" and span.key)
    _log_key_change_contradictions(context, local_spans)

    effective_key = global_key_label if global_status == "ok" and global_key_label else declared_key
    top_status = global_status
    result = AnalysisTonalityResult(
        global_key=global_span,
        local_spans=local_spans,
        declared_key=declared_key,
        effective_key=effective_key,
        inference=InferenceMeta(
            status=top_status,
            confidence=confidence,
            evidence=AnalysisEvidence(
                count=len(pitched),
                mass=round_analysis_float(mass_quarters),
                coverage=round_analysis_float(coverage),
            ),
            method=ANALYSIS_ALGORITHM_VERSION,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )
    logger.info(
        "Tonality inference complete",
        extra={
            "status": top_status,
            "accepted_global": 1 if global_status == "ok" and global_key_label else 0,
            "accepted_local_spans": accepted_local,
            "local_span_count": len(local_spans),
            "effective_key": effective_key,
        },
    )
    return result


def _score_key_candidates(
    pitch_class_mass: Sequence[float],
    *,
    bass_mass: Sequence[float] | None = None,
    edge_mass: Sequence[float] | None = None,
    harmony_evidence: Sequence[tuple[ParsedChord, float]] | None = None,
) -> list[KeyScore]:
    masses = _normalize_pc_vector(pitch_class_mass)
    bass = _normalize_pc_vector(bass_mass) if bass_mass is not None else None
    edge = _normalize_pc_vector(edge_mass) if edge_mass is not None else None
    candidates = _all_candidate_keys()
    scores = {key.label: KeyScore(key=key) for key in candidates}

    for key in candidates:
        note_score = 0.0
        note_weight = 0.0
        for pc, mass in enumerate(masses):
            if mass <= 0:
                continue
            weight = mass
            if bass is not None and bass[pc] > 0:
                weight += bass[pc] * BASS_MASS_BOOST
            if edge is not None and edge[pc] > 0:
                weight += edge[pc] * EDGE_MASS_BOOST
            note_score += _note_weight_for_key(pc, key) * weight
            note_weight += weight
        scores[key.label].note_score = note_score
        scores[key.label].note_weight = note_weight

    if harmony_evidence:
        for chord, weight in harmony_evidence:
            if not chord.parseable:
                continue
            for key in candidates:
                scores[key.label].harmony_score += _harmony_weight_for_key(chord, key) * weight
                scores[key.label].harmony_count += 1

    return _rank_key_scores(list(scores.values()))


def _rank_key_scores(scores: Sequence[KeyScore]) -> list[KeyScore]:
    return sorted(
        scores,
        key=lambda item: (
            -round_analysis_float(item.combined_score if item.harmony_score else item.note_score),
            item.key.tonic_pc,
            _MODE_RANK.get(item.key.mode, 1),
        ),
    )


def _key_score_to_candidate(score: KeyScore) -> KeyCandidateScore:
    raw = score.combined_score if score.harmony_score else score.note_score
    return KeyCandidateScore(
        key=score.key.label,
        score=round_analysis_float(raw),
        pitch_class=score.key.tonic_pc,
        mode=score.key.mode,  # type: ignore[arg-type]
    )


def _normalize_pc_vector(values: Sequence[float] | None) -> list[float]:
    if values is None:
        return [0.0] * 12
    vector = [float(values[i]) if i < len(values) else 0.0 for i in range(12)]
    if len(values) != 12:
        logger.debug(
            "Pitch-class mass length normalized to 12",
            extra={"provided_length": len(values)},
        )
    return vector


def _decide_inference_status(
    ranked: Sequence[KeyScore],
    mass_quarters: float,
    *,
    min_mass_quarters: float = MIN_INFERENCE_MASS_QUARTERS,
) -> tuple[str, str | None, float | None, float]:
    if not ranked:
        return "insufficient_evidence", None, None, 0.0
    if mass_quarters < min_mass_quarters and ranked[0].harmony_count == 0:
        return "insufficient_evidence", None, None, 0.0

    winner = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    winner_score = winner.combined_score if winner.harmony_score else winner.note_score
    second_score = (
        (second.combined_score if second.harmony_score else second.note_score) if second else 0.0
    )
    top_mass = winner_score + second_score
    confidence = round_analysis_float(winner_score / top_mass) if top_mass > 0 else 0.0
    margin = (
        round_analysis_float((winner_score - second_score) / winner_score)
        if winner_score > 0
        else 0.0
    )

    if (
        second is not None
        and _are_relative_keys(winner.key, second.key)
        and margin < RELATIVE_AMBIGUITY_MARGIN
    ):
        return "ambiguous", winner.key.label, confidence, margin

    if winner_score <= 0:
        return "insufficient_evidence", None, confidence, margin

    return "ok", winner.key.label, confidence, margin


def _are_relative_keys(left: ParsedKey, right: ParsedKey) -> bool:
    if left.mode == right.mode:
        return False
    if left.mode == "major":
        return right.mode == "minor" and right.tonic_pc == left.relative_minor_pc
    return right.mode == "major" and right.tonic_pc == left.relative_major_pc


def _coverage_ratio(pc_mass: Sequence[float], key: ParsedKey) -> float:
    total = sum(pc_mass)
    if total <= 0:
        return 0.0
    diatonic = _diatonic_pitch_classes(key)
    covered = sum(mass for pc, mass in enumerate(pc_mass) if pc in diatonic)
    return covered / total


def _scoped_pitch_class_mass(
    context: CompositionAnalysisContext,
    start_bar: int,
    end_bar_exclusive: int,
) -> list[float]:
    scope = context.resolved_scope
    if scope.kind == "track":
        mass = [0.0] * 12
        for track_idx, _note_idx, note in context.notes_overlapping_scope(drums=False):
            if scope.track_index is not None and track_idx != scope.track_index:
                continue
            if scope.track_id is not None and context.tracks[track_idx].track_id != scope.track_id:
                continue
            for bar in range(start_bar, end_bar_exclusive):
                clipped = context.clip_note_to_bar(note, bar)
                if clipped > 0:
                    mass[note.pitch_class] += float(clipped)
        return mass
    return context.pitch_class_mass_for_bars(start_bar, end_bar_exclusive)


def _edge_mass_for_range(
    context: CompositionAnalysisContext,
    start_bar: int,
    end_bar_exclusive: int,
) -> list[float]:
    if end_bar_exclusive <= start_bar:
        return [0.0] * 12
    edge_bars = {start_bar, end_bar_exclusive - 1}
    mass = [0.0] * 12
    for bar in sorted(edge_bars):
        bar_mass = _scoped_pitch_class_mass(context, bar, bar + 1)
        for pc in range(12):
            mass[pc] += bar_mass[pc]
    return mass


def _bass_mass_for_range(
    context: CompositionAnalysisContext,
    start_bar: int,
    end_bar_exclusive: int,
) -> list[float]:
    from .composition_logical_notes import clip_occupancy

    mass = [0.0] * 12
    if end_bar_exclusive <= start_bar:
        return mass
    start_tick = context.timeline.bar_start_tick(start_bar)
    end_tick = context.timeline.bar_end_tick(end_bar_exclusive - 1)
    for track in context.tracks:
        if track.is_drum or track.role not in {"bass"}:
            continue
        if (
            context.resolved_scope.kind == "track"
            and track.track_id != context.resolved_scope.track_id
        ):
            continue
        for note in track.logical_notes:
            clipped = clip_occupancy(note, start_tick, end_tick)
            if clipped > 0:
                mass[note.pitch_class] += float(clipped)
    return mass


def _harmony_evidence_for_bars(
    composition: CompositionV2,
    start_bar: int,
    end_bar_exclusive: int,
) -> list[tuple[ParsedChord, float]]:
    evidence: list[tuple[ParsedChord, float]] = []
    boundary_bars = {section.start_bar for section in composition.sections}
    from app.services.composition_timeline import compile_timeline

    timeline = compile_timeline(composition)
    for item in composition.harmony:
        bar = timeline.bar_at_tick(int(item.start_tick))
        if bar < start_bar or bar >= end_bar_exclusive:
            continue
        chord = parse_chord_symbol(item.chord)
        if not chord.parseable:
            continue
        weight = 1.5 if bar in boundary_bars else 1.0
        if bar == 1 or bar == composition.bar_count:
            weight *= 1.25
        evidence.append((chord, weight))
    return evidence


def _infer_local_key_spans(
    context: CompositionAnalysisContext,
    start_bar: int,
    end_bar_exclusive: int,
) -> list[KeySpanResult]:
    """Per-section and fixed 2-bar windows with transition smoothing and merge."""
    window_results: list[KeySpanResult] = []

    # Section windows overlapping the scope.
    for section_index, section in enumerate(context.sections):
        sec_start = section.start_bar
        sec_end = section.start_bar + section.bar_count
        overlap_start = max(start_bar, sec_start)
        overlap_end = min(end_bar_exclusive, sec_end)
        if overlap_end <= overlap_start:
            continue
        span = _analyze_bar_window(
            context,
            overlap_start,
            overlap_end,
            span_kind="section",
            span_index=section_index,
            previous_key=None,
            min_mass_quarters=MIN_LOCAL_INFERENCE_MASS_QUARTERS,
        )
        if span is not None:
            window_results.append(span)

    # Fixed 2-bar windows across the scoped bar range.
    previous_key: ParsedKey | None = None
    fixed_windows: list[KeySpanResult] = []
    bar = start_bar
    window_index = 0
    while bar < end_bar_exclusive:
        win_end = min(bar + LOCAL_KEY_WINDOW_BARS, end_bar_exclusive)
        span = _analyze_bar_window(
            context,
            bar,
            win_end,
            span_kind="window",
            span_index=window_index,
            previous_key=previous_key,
            min_mass_quarters=MIN_LOCAL_INFERENCE_MASS_QUARTERS,
        )
        if span is not None:
            fixed_windows.append(span)
            if span.key and span.inference.status in {"ok", "ambiguous"}:
                previous_key = parse_key(span.key)
            logger.debug(
                "Local key window scored",
                extra={
                    "start_bar": bar,
                    "end_bar_exclusive": win_end,
                    "key": span.key,
                    "status": span.inference.status,
                },
            )
        bar = win_end
        window_index += 1

    merged_fixed = _merge_adjacent_key_spans(fixed_windows)
    # Stable order: sections first (canonical), then merged fixed windows by tick.
    combined = list(window_results) + merged_fixed
    combined.sort(key=lambda item: (item.start_tick, item.end_tick, item.id))
    return combined[:512]


def _analyze_bar_window(
    context: CompositionAnalysisContext,
    start_bar: int,
    end_bar_exclusive: int,
    *,
    span_kind: str,
    span_index: int,
    previous_key: ParsedKey | None,
    min_mass_quarters: float,
) -> KeySpanResult | None:
    if end_bar_exclusive <= start_bar:
        return None
    tpq = float(context.composition.ticks_per_quarter)
    pc_mass = _scoped_pitch_class_mass(context, start_bar, end_bar_exclusive)
    mass_quarters = sum(pc_mass) / tpq if tpq > 0 else 0.0
    start_tick = context.timeline.bar_start_tick(start_bar)
    end_tick = context.timeline.bar_end_tick(end_bar_exclusive - 1)
    declared = context.timeline.active_key(start_tick)

    if mass_quarters < min_mass_quarters:
        return KeySpanResult(
            id=make_derived_id("tonality", span_kind, span_index, start_bar, end_bar_exclusive),
            start_tick=start_tick,
            end_tick=end_tick,
            start_bar=start_bar,
            end_bar_exclusive=end_bar_exclusive,
            key=None,
            declared_key=declared,
            candidates=[],
            inference=InferenceMeta(
                status="insufficient_evidence",
                confidence=None,
                evidence=AnalysisEvidence(
                    count=0,
                    mass=round_analysis_float(mass_quarters),
                    coverage=0.0,
                ),
                method=ANALYSIS_ALGORITHM_VERSION,
                method_version=ANALYSIS_ALGORITHM_VERSION,
            ),
        )

    edge_mass = _edge_mass_for_range(context, start_bar, end_bar_exclusive)
    bass_mass = _bass_mass_for_range(context, start_bar, end_bar_exclusive)
    ranked = _score_key_candidates(pc_mass, bass_mass=bass_mass, edge_mass=edge_mass)
    if previous_key is not None and ranked:
        top_raw = ranked[0].note_score
        penalty = top_raw * LOCAL_TRANSITION_PENALTY_RATIO
        for item in ranked:
            if (
                item.key.tonic_pc != previous_key.tonic_pc
                or item.key.mode != previous_key.mode
            ):
                item.note_score = max(0.0, item.note_score - penalty)
        ranked = _rank_key_scores(ranked)

    status, key_label, confidence, _margin = _decide_inference_status(
        ranked,
        mass_quarters,
        min_mass_quarters=min_mass_quarters,
    )
    candidates = [_key_score_to_candidate(item) for item in ranked[:ANALYSIS_MAX_CANDIDATES]]
    coverage = _coverage_ratio(pc_mass, ranked[0].key) if ranked else 0.0
    note_count = 0
    for _t, _n, note in context.notes_overlapping_scope(drums=False):
        if note.start_tick < end_tick and note.end_tick > start_tick:
            note_count += 1
    return KeySpanResult(
        id=make_derived_id("tonality", span_kind, span_index, start_bar, end_bar_exclusive),
        start_tick=start_tick,
        end_tick=end_tick,
        start_bar=start_bar,
        end_bar_exclusive=end_bar_exclusive,
        key=key_label if status in {"ok", "ambiguous"} else None,
        declared_key=declared,
        candidates=candidates,
        inference=InferenceMeta(
            status=status,
            confidence=confidence,
            evidence=AnalysisEvidence(
                count=note_count,
                mass=round_analysis_float(mass_quarters),
                coverage=round_analysis_float(coverage),
            ),
            method=ANALYSIS_ALGORITHM_VERSION,
            method_version=ANALYSIS_ALGORITHM_VERSION,
        ),
    )


def _merge_adjacent_key_spans(spans: Sequence[KeySpanResult]) -> list[KeySpanResult]:
    """Merge adjacent accepted windows that share the same inferred key."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda item: (item.start_tick, item.end_tick, item.id))
    merged: list[KeySpanResult] = []
    for span in ordered:
        if (
            merged
            and span.key
            and merged[-1].key
            and span.key == merged[-1].key
            and span.inference.status == "ok"
            and merged[-1].inference.status == "ok"
            and span.start_tick == merged[-1].end_tick
        ):
            prev = merged[-1]
            prev_mass = prev.inference.evidence.mass or 0.0
            span_mass = span.inference.evidence.mass or 0.0
            merged[-1] = KeySpanResult(
                id=make_derived_id(
                    "tonality",
                    "merged",
                    prev.start_bar,
                    span.end_bar_exclusive,
                ),
                start_tick=prev.start_tick,
                end_tick=span.end_tick,
                start_bar=prev.start_bar,
                end_bar_exclusive=span.end_bar_exclusive,
                key=prev.key,
                declared_key=prev.declared_key,
                candidates=prev.candidates,
                inference=InferenceMeta(
                    status="ok",
                    confidence=prev.inference.confidence,
                    evidence=AnalysisEvidence(
                        count=(prev.inference.evidence.count or 0)
                        + (span.inference.evidence.count or 0),
                        mass=round_analysis_float(prev_mass + span_mass),
                        coverage=prev.inference.evidence.coverage,
                    ),
                    method=ANALYSIS_ALGORITHM_VERSION,
                    method_version=ANALYSIS_ALGORITHM_VERSION,
                ),
            )
        else:
            merged.append(span)
    return merged


def _log_key_change_contradictions(
    context: CompositionAnalysisContext,
    local_spans: Sequence[KeySpanResult],
) -> None:
    for span in local_spans:
        if span.inference.status != "ok" or not span.key or not span.declared_key:
            continue
        inferred = parse_key(span.key)
        declared = parse_key(span.declared_key)
        if not inferred or not declared:
            continue
        if inferred.tonic_pc == declared.tonic_pc and inferred.mode == declared.mode:
            continue
        mass = span.inference.evidence.mass or 0.0
        if mass < MIN_LOCAL_INFERENCE_MASS_QUARTERS:
            continue
        if not span.candidates:
            continue
        winner_score = span.candidates[0].score
        declared_score = next(
            (
                item.score
                for item in span.candidates
                if item.pitch_class == declared.tonic_pc and item.mode == declared.mode
            ),
            0.0,
        )
        if winner_score <= 0:
            continue
        margin = (winner_score - declared_score) / winner_score
        if (
            winner_score >= MIN_COMPETITOR_ABS_SCORE
            and margin >= CONTRADICTION_SCORE_MARGIN
        ):
            logger.warning(
                "Declared key change conflicts with inference",
                extra={
                    "code": "declared_key_change_conflicts_with_inference",
                    "declared_key": span.declared_key,
                    "inferred_key": span.key,
                    "start_bar": span.start_bar,
                    "margin": round_analysis_float(margin),
                },
            )


def _abstain_result(
    *,
    declared_key: str | None,
    status: str,
    start_tick: int,
    end_tick: int,
    start_bar: int,
    end_bar_exclusive: int,
    evidence_count: int,
    evidence_mass: float,
) -> AnalysisTonalityResult:
    meta = InferenceMeta(
        status=status,  # type: ignore[arg-type]
        confidence=None,
        evidence=AnalysisEvidence(
            count=evidence_count,
            mass=round_analysis_float(evidence_mass),
            coverage=0.0,
        ),
        method=ANALYSIS_ALGORITHM_VERSION,
        method_version=ANALYSIS_ALGORITHM_VERSION,
    )
    global_key = KeySpanResult(
        id=make_derived_id("tonality", "global", start_bar, end_bar_exclusive),
        start_tick=start_tick,
        end_tick=end_tick,
        start_bar=start_bar,
        end_bar_exclusive=end_bar_exclusive,
        key=None,
        declared_key=declared_key,
        candidates=[],
        inference=meta,
    )
    return AnalysisTonalityResult(
        global_key=global_key,
        local_spans=[],
        declared_key=declared_key,
        effective_key=declared_key,
        inference=meta,
    )
