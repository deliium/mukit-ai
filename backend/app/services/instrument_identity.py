"""Pure instrument identity normalization and instrumentation analysis.

Requested instruments are required sound sources. Satisfaction is existential
across `track.instrument` only (never `track.name`). Canonical sound identity
is separate from requirement-compatibility/family matching.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Protocol, Sequence


ContentRelationship = Literal["exact", "high_overlap", "distinct"]

# Overlap ratio (intersection / union of event fingerprint multisets) at or above
# this threshold is treated as high-overlap / actionable duplicate content.
_HIGH_OVERLAP_THRESHOLD = 0.8

_STRING_COMPATIBILITY = frozenset({"strings", "violin", "viola", "cello"})
_DRUM_IDENTITIES = frozenset({"drums"})

# Longer / more specific alias tokens first.
# Maps normalized label substrings / exact phrases → canonical sound identity.
_IDENTITY_ALIASES: tuple[tuple[str, str], ...] = (
    ("bassoon", "bassoon"),
    ("contrabass", "bass"),
    ("double bass", "bass"),
    ("electric bass", "bass"),
    ("bass guitar", "bass"),
    ("acoustic bass", "bass"),
    ("upright bass", "bass"),
    ("grand piano", "piano"),
    ("acoustic piano", "piano"),
    ("string ensemble", "strings"),
    ("synth pad", "pad"),
    ("drum kit", "drums"),
    ("percussion", "drums"),
    ("drums", "drums"),
    ("drum", "drums"),
    ("keyboard", "piano"),
    ("piano", "piano"),
    ("strings", "strings"),
    ("string", "strings"),
    ("violin", "violin"),
    ("viola", "viola"),
    ("cello", "cello"),
    ("guitar", "guitar"),
    ("organ", "organ"),
    ("harp", "harp"),
    ("choir", "choir"),
    ("voice", "choir"),
    ("vocal", "choir"),
    ("trumpet", "trumpet"),
    ("trombone", "trombone"),
    ("french horn", "horn"),
    ("horn", "horn"),
    ("brass", "brass"),
    ("saxophone", "sax"),
    ("sax", "sax"),
    ("oboe", "oboe"),
    ("clarinet", "clarinet"),
    ("flute", "flute"),
    ("woodwind", "woodwind"),
    ("synth", "synth"),
    ("pad", "pad"),
    ("bass", "bass"),
)

# Compatibility/family buckets used for unexpected-family policy and string matching.
# Distinct sound identities (trumpet vs trombone) stay separate above; family may group.
_IDENTITY_TO_FAMILY: dict[str, str] = {
    "bassoon": "woodwind",
    "bass": "bass",
    "piano": "piano",
    "strings": "strings",
    "violin": "violin",
    "viola": "viola",
    "cello": "cello",
    "pad": "pad",
    "drums": "drums",
    "guitar": "guitar",
    "organ": "organ",
    "harp": "harp",
    "choir": "choir",
    "trumpet": "brass",
    "trombone": "brass",
    "horn": "brass",
    "brass": "brass",
    "sax": "woodwind",
    "oboe": "woodwind",
    "clarinet": "woodwind",
    "flute": "woodwind",
    "woodwind": "woodwind",
    "synth": "synth",
}


class TrackLike(Protocol):
    """Minimal track shape for instrumentation analysis."""

    id: str
    instrument: str
    role: str
    events: Sequence[Any]


@dataclass(frozen=True)
class NormalizedInstrument:
    """Normalized instrument label with sound identity and compatibility family."""

    raw: str
    identity: str
    family: str
    is_drum: bool


@dataclass(frozen=True)
class InstrumentRequirement:
    """One deduplicated requested sound-source requirement (request order preserved)."""

    key: str
    identity: str
    family: str
    raw_labels: tuple[str, ...]
    order_index: int


@dataclass(frozen=True)
class SatisfiedRequirement:
    """Requirement satisfied by one or more tracks."""

    key: str
    identity: str
    family: str
    track_ids: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateGroup:
    """Suspicious same-identity/same-role track group with event evidence."""

    identity: str
    role: str
    track_ids: tuple[str, ...]
    event_counts: tuple[int, ...]
    content_relationship: ContentRelationship
    actionable: bool


@dataclass(frozen=True)
class InstrumentationAnalysis:
    """Deterministic instrumentation analysis over requested labels and tracks."""

    requirements: tuple[InstrumentRequirement, ...]
    satisfied: tuple[SatisfiedRequirement, ...]
    missing: tuple[InstrumentRequirement, ...]
    present_identities: tuple[str, ...]
    unexpected_identities: tuple[str, ...]
    duplicate_groups: tuple[DuplicateGroup, ...]

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.missing)

    @property
    def satisfied_keys(self) -> tuple[str, ...]:
        return tuple(item.key for item in self.satisfied)

    @property
    def actionable_duplicate_groups(self) -> tuple[DuplicateGroup, ...]:
        return tuple(group for group in self.duplicate_groups if group.actionable)


def normalize_label_text(label: str) -> str:
    """Collapse case, surrounding/internal whitespace, and common separators."""
    text = label.strip().lower()
    for sep in ("_", "-", "/", ",", ";"):
        text = text.replace(sep, " ")
    return " ".join(text.split())


def normalize_role(role: str) -> str:
    """Normalize role labels for duplicate comparison only."""
    return normalize_label_text(role)


def normalize_instrument_identity(instrument: str) -> str | None:
    """Map an instrument label to a canonical sound identity, or None if empty."""
    text = normalize_label_text(instrument)
    if not text:
        return None
    # Prefer exact full-label aliases first.
    for token, identity in _IDENTITY_ALIASES:
        if token == text:
            return identity
    # Multi-word aliases may appear inside longer labels ("fender bass guitar").
    for token, identity in _IDENTITY_ALIASES:
        if " " in token and token in text:
            return identity
    # Single-word aliases match whole words only so "synth" does not steal
    # unrelated compounds that should stay unknown full-label identities.
    words = set(text.split())
    for token, identity in _IDENTITY_ALIASES:
        if " " not in token and token in words:
            return identity
    # Unknown labels use the complete normalized label (not first-word fallback).
    slug = "".join(ch if ch.isalnum() else "_" for ch in text)
    slug = "_".join(part for part in slug.split("_") if part)
    return slug or None


def family_for_identity(identity: str | None) -> str | None:
    """Return the compatibility/family token for a canonical identity."""
    if not identity:
        return None
    if identity in _IDENTITY_TO_FAMILY:
        return _IDENTITY_TO_FAMILY[identity]
    return identity


def normalize_instrument_family(instrument: str) -> str | None:
    """Map an instrument label to a compatibility/family token (legacy helpers)."""
    identity = normalize_instrument_identity(instrument)
    return family_for_identity(identity)


def is_drum_identity(identity: str | None) -> bool:
    return identity in _DRUM_IDENTITIES if identity else False


def is_drum_family(family: str | None) -> bool:
    return family in _DRUM_IDENTITIES if family else False


def normalize_instrument(instrument: str) -> NormalizedInstrument | None:
    """Return full normalized instrument metadata, or None for empty labels."""
    raw = instrument.strip()
    identity = normalize_instrument_identity(instrument)
    if identity is None:
        return None
    family = family_for_identity(identity) or identity
    return NormalizedInstrument(
        raw=raw,
        identity=identity,
        family=family,
        is_drum=is_drum_identity(identity),
    )


def identities_compatible(left: str, right: str) -> bool:
    """True when two sound identities satisfy the same requested requirement."""
    if left == right:
        return True
    if left in _STRING_COMPATIBILITY and right in _STRING_COMPATIBILITY:
        return True
    return False


def instrument_satisfies_requirement(instrument: str, requirement_key: str) -> bool:
    """Return True when a track instrument label covers a requirement key."""
    normalized = normalize_instrument(instrument)
    if normalized is None or normalized.is_drum:
        return False
    req_identity = normalize_instrument_identity(requirement_key) or requirement_key
    if identities_compatible(normalized.identity, req_identity):
        return True
    # Also allow matching by legacy family token when callers pass family keys.
    req_family = family_for_identity(req_identity) or req_identity
    if normalized.family == req_family and normalized.identity == req_identity:
        return True
    if identities_compatible(normalized.family, req_family) and (
        normalized.identity in _STRING_COMPATIBILITY or req_identity in _STRING_COMPATIBILITY
    ):
        return True
    return False


def instrument_satisfies_family(instrument: str, family: str) -> bool:
    """Compatibility helper used by generation constraint callers."""
    return instrument_satisfies_requirement(instrument, family)


def collect_instrument_requirements(instruments: Iterable[str]) -> tuple[InstrumentRequirement, ...]:
    """Deduplicate requested labels into ordered sound-source requirements."""
    requirements: list[InstrumentRequirement] = []
    seen: dict[str, int] = {}
    for raw in instruments:
        normalized = normalize_instrument(raw)
        if normalized is None or normalized.is_drum:
            continue
        key = normalized.identity
        if key in seen:
            index = seen[key]
            existing = requirements[index]
            requirements[index] = InstrumentRequirement(
                key=existing.key,
                identity=existing.identity,
                family=existing.family,
                raw_labels=existing.raw_labels + (raw.strip(),),
                order_index=existing.order_index,
            )
            continue
        seen[key] = len(requirements)
        requirements.append(
            InstrumentRequirement(
                key=key,
                identity=normalized.identity,
                family=normalized.family,
                raw_labels=(raw.strip(),),
                order_index=len(requirements),
            )
        )
    return tuple(requirements)


def collect_instrument_families(instruments: Iterable[str]) -> tuple[str, ...]:
    """Deduplicate requirement keys preserving request order (legacy name)."""
    return tuple(item.key for item in collect_instrument_requirements(instruments))


def event_content_fingerprint(event: Any) -> tuple[Any, ...]:
    """Stable event fingerprint ignoring display names and event IDs."""
    if isinstance(event, Mapping):
        pitch = event.get("pitch")
        start_tick = event.get("start_tick")
        duration_ticks = event.get("duration_ticks")
        velocity = event.get("velocity")
        staff = event.get("staff")
        voice = event.get("voice")
        event_type = event.get("type", "note")
    else:
        pitch = getattr(event, "pitch", None)
        start_tick = getattr(event, "start_tick", None)
        duration_ticks = getattr(event, "duration_ticks", None)
        velocity = getattr(event, "velocity", None)
        staff = getattr(event, "staff", None)
        voice = getattr(event, "voice", None)
        event_type = getattr(event, "type", "note")
    return (event_type, pitch, start_tick, duration_ticks, velocity, staff, voice)


def _fingerprint_counter(events: Sequence[Any]) -> Counter[tuple[Any, ...]]:
    return Counter(event_content_fingerprint(event) for event in events)


def classify_content_relationship(
    left_events: Sequence[Any],
    right_events: Sequence[Any],
) -> ContentRelationship:
    """Classify playable-content relationship between two event sequences."""
    left = _fingerprint_counter(left_events)
    right = _fingerprint_counter(right_events)
    if left == right:
        return "exact"
    if not left or not right:
        # One empty and one non-empty (or both empty with different empty tracks):
        # both-empty is exact (handled above); empty vs non-empty is distinct.
        return "distinct"
    intersection = sum((left & right).values())
    union = sum((left | right).values())
    if union <= 0:
        return "exact"
    ratio = intersection / union
    if ratio >= _HIGH_OVERLAP_THRESHOLD:
        return "high_overlap"
    return "distinct"


def _group_content_relationship(tracks: Sequence[TrackLike]) -> ContentRelationship:
    """Worst pairwise relationship across a same-identity/role group."""
    if len(tracks) < 2:
        return "exact"
    relationships: list[ContentRelationship] = []
    for index, left in enumerate(tracks):
        for right in tracks[index + 1 :]:
            relationships.append(classify_content_relationship(left.events, right.events))
    if "distinct" in relationships:
        # Prefer high_overlap when any pair is overlapping and none are merely distinct-only;
        # if any pair is distinct, the group is distinct unless another pair is exact/high.
        if all(item == "distinct" for item in relationships):
            return "distinct"
        if "exact" in relationships or "high_overlap" in relationships:
            # Mixed evidence: treat as high_overlap when any strong overlap exists,
            # else distinct. Actionable only when no pair is clearly divergent? Plan:
            # "clearly divergent ... reported as suspicious but not actionable".
            # If ANY pair is distinct, mark group distinct (not actionable).
            return "distinct"
        return "distinct"
    if "high_overlap" in relationships:
        return "high_overlap"
    return "exact"


def analyze_instrumentation(
    requested_labels: Sequence[str],
    tracks: Sequence[TrackLike],
    *,
    allow_extra: bool = False,
) -> InstrumentationAnalysis:
    """Analyze requested sound sources against track instruments and roles.

    Pure: no I/O and no logging. Callers should DEBUG-log summary fields.
    """
    requirements = collect_instrument_requirements(requested_labels)

    present: list[str] = []
    present_seen: set[str] = set()
    track_identities: dict[str, NormalizedInstrument] = {}
    for track in tracks:
        normalized = normalize_instrument(track.instrument)
        if normalized is None:
            continue
        track_identities[track.id] = normalized
        if normalized.identity not in present_seen:
            present_seen.add(normalized.identity)
            present.append(normalized.identity)

    satisfied: list[SatisfiedRequirement] = []
    missing: list[InstrumentRequirement] = []
    for requirement in requirements:
        matching_ids = [
            track.id
            for track in tracks
            if instrument_satisfies_requirement(track.instrument, requirement.key)
        ]
        if matching_ids:
            satisfied.append(
                SatisfiedRequirement(
                    key=requirement.key,
                    identity=requirement.identity,
                    family=requirement.family,
                    track_ids=tuple(matching_ids),
                )
            )
        else:
            missing.append(requirement)

    unexpected: list[str] = []
    if not allow_extra:
        unexpected_seen: set[str] = set()
        for identity in present:
            if is_drum_identity(identity):
                continue
            if any(identities_compatible(identity, req.key) for req in requirements):
                continue
            if identity in unexpected_seen:
                continue
            unexpected_seen.add(identity)
            unexpected.append(identity)

    duplicate_groups = _find_duplicate_groups(tracks, track_identities)

    return InstrumentationAnalysis(
        requirements=requirements,
        satisfied=tuple(satisfied),
        missing=tuple(missing),
        present_identities=tuple(present),
        unexpected_identities=tuple(unexpected),
        duplicate_groups=duplicate_groups,
    )


def _find_duplicate_groups(
    tracks: Sequence[TrackLike],
    track_identities: Mapping[str, NormalizedInstrument],
) -> tuple[DuplicateGroup, ...]:
    buckets: dict[tuple[str, str], list[TrackLike]] = {}
    for track in tracks:
        normalized = track_identities.get(track.id)
        if normalized is None or normalized.is_drum:
            continue
        role = normalize_role(track.role)
        if not role:
            continue
        key = (normalized.identity, role)
        buckets.setdefault(key, []).append(track)

    groups: list[DuplicateGroup] = []
    for (identity, role), group_tracks in buckets.items():
        if len(group_tracks) < 2:
            continue
        relationship = _group_content_relationship(group_tracks)
        actionable = relationship in {"exact", "high_overlap"}
        groups.append(
            DuplicateGroup(
                identity=identity,
                role=role,
                track_ids=tuple(track.id for track in group_tracks),
                event_counts=tuple(len(track.events) for track in group_tracks),
                content_relationship=relationship,
                actionable=actionable,
            )
        )
    return tuple(groups)


def missing_required_identities(
    present_instruments: Iterable[str],
    required_keys: Sequence[str],
) -> list[str]:
    """Return required keys not satisfied by any present instrument label."""
    present = list(present_instruments)
    missing: list[str] = []
    for key in required_keys:
        identity = normalize_instrument_identity(key)
        if identity is None or is_drum_identity(identity):
            continue
        if not any(instrument_satisfies_requirement(instrument, key) for instrument in present):
            missing.append(key)
    return missing


def unexpected_instrument_identities(
    present_instruments: Iterable[str],
    required_keys: Sequence[str],
    *,
    allow_extra: bool = False,
) -> list[str]:
    """Return present identities not covering any required key (non-drums)."""
    if allow_extra:
        return []
    analysis = analyze_instrumentation(
        list(required_keys),
        _tracks_from_instruments(present_instruments),
        allow_extra=False,
    )
    return list(analysis.unexpected_identities)


@dataclass
class _SimpleTrack:
    id: str
    instrument: str
    role: str
    events: list[Any]


def _tracks_from_instruments(instruments: Iterable[str]) -> list[_SimpleTrack]:
    tracks: list[_SimpleTrack] = []
    for index, instrument in enumerate(instruments):
        tracks.append(
            _SimpleTrack(
                id=f"present-{index}",
                instrument=instrument,
                role="unknown",
                events=[],
            )
        )
    return tracks
