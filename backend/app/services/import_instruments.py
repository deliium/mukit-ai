"""General MIDI Level-1 program map and high-confidence import role inference.

Preserves source ``midi_program`` when present. Role inference never analyzes
note content for melody/harmony/form/key — only explicit metadata, channel-10
percussion, and unambiguous instrument identities.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.composition_schemas import SUPPORTED_TRACK_ROLES
from app.import_settings import IMPORT_NEUTRAL_TRACK_ROLE
from app.services.instrument_identity import (
    is_drum_identity,
    normalize_instrument,
    normalize_instrument_identity,
    normalize_label_text,
)


logger = logging.getLogger(__name__)

RoleDecisionSource = Literal[
    "explicit_role",
    "channel_percussion",
    "instrument_identity",
    "default",
]


@dataclass(frozen=True)
class GmProgramInfo:
    program: int
    name: str
    family: str


@dataclass(frozen=True)
class ImportInstrumentResolution:
    instrument: str
    midi_program: int
    is_drum: bool
    role: str
    role_source: RoleDecisionSource
    instrument_defaulted: bool


# Full General MIDI Level-1 program map (0..127): (name, family).
_GM_PROGRAMS: tuple[tuple[str, str], ...] = (
    ("Acoustic Grand Piano", "piano"),
    ("Bright Acoustic Piano", "piano"),
    ("Electric Grand Piano", "piano"),
    ("Honky-tonk Piano", "piano"),
    ("Electric Piano 1", "piano"),
    ("Electric Piano 2", "piano"),
    ("Harpsichord", "piano"),
    ("Clavi", "piano"),
    ("Celesta", "chromatic_percussion"),
    ("Glockenspiel", "chromatic_percussion"),
    ("Music Box", "chromatic_percussion"),
    ("Vibraphone", "chromatic_percussion"),
    ("Marimba", "chromatic_percussion"),
    ("Xylophone", "chromatic_percussion"),
    ("Tubular Bells", "chromatic_percussion"),
    ("Dulcimer", "chromatic_percussion"),
    ("Drawbar Organ", "organ"),
    ("Percussive Organ", "organ"),
    ("Rock Organ", "organ"),
    ("Church Organ", "organ"),
    ("Reed Organ", "organ"),
    ("Accordion", "organ"),
    ("Harmonica", "organ"),
    ("Tango Accordion", "organ"),
    ("Acoustic Guitar (nylon)", "guitar"),
    ("Acoustic Guitar (steel)", "guitar"),
    ("Electric Guitar (jazz)", "guitar"),
    ("Electric Guitar (clean)", "guitar"),
    ("Electric Guitar (muted)", "guitar"),
    ("Overdriven Guitar", "guitar"),
    ("Distortion Guitar", "guitar"),
    ("Guitar harmonics", "guitar"),
    ("Acoustic Bass", "bass"),
    ("Electric Bass (finger)", "bass"),
    ("Electric Bass (pick)", "bass"),
    ("Fretless Bass", "bass"),
    ("Slap Bass 1", "bass"),
    ("Slap Bass 2", "bass"),
    ("Synth Bass 1", "bass"),
    ("Synth Bass 2", "bass"),
    ("Violin", "strings"),
    ("Viola", "strings"),
    ("Cello", "strings"),
    ("Contrabass", "bass"),
    ("Tremolo Strings", "strings"),
    ("Pizzicato Strings", "strings"),
    ("Orchestral Harp", "harp"),
    ("Timpani", "percussion"),
    ("String Ensemble 1", "strings"),
    ("String Ensemble 2", "strings"),
    ("SynthStrings 1", "strings"),
    ("SynthStrings 2", "strings"),
    ("Choir Aahs", "choir"),
    ("Voice Oohs", "choir"),
    ("Synth Voice", "choir"),
    ("Orchestra Hit", "ensemble"),
    ("Trumpet", "brass"),
    ("Trombone", "brass"),
    ("Tuba", "brass"),
    ("Muted Trumpet", "brass"),
    ("French Horn", "brass"),
    ("Brass Section", "brass"),
    ("SynthBrass 1", "brass"),
    ("SynthBrass 2", "brass"),
    ("Soprano Sax", "woodwind"),
    ("Alto Sax", "woodwind"),
    ("Tenor Sax", "woodwind"),
    ("Baritone Sax", "woodwind"),
    ("Oboe", "woodwind"),
    ("English Horn", "woodwind"),
    ("Bassoon", "woodwind"),
    ("Clarinet", "woodwind"),
    ("Piccolo", "woodwind"),
    ("Flute", "woodwind"),
    ("Recorder", "woodwind"),
    ("Pan Flute", "woodwind"),
    ("Blown Bottle", "woodwind"),
    ("Shakuhachi", "woodwind"),
    ("Whistle", "woodwind"),
    ("Ocarina", "woodwind"),
    ("Lead 1 (square)", "synth"),
    ("Lead 2 (sawtooth)", "synth"),
    ("Lead 3 (calliope)", "synth"),
    ("Lead 4 (chiff)", "synth"),
    ("Lead 5 (charang)", "synth"),
    ("Lead 6 (voice)", "synth"),
    ("Lead 7 (fifths)", "synth"),
    ("Lead 8 (bass + lead)", "synth"),
    ("Pad 1 (new age)", "pad"),
    ("Pad 2 (warm)", "pad"),
    ("Pad 3 (polysynth)", "pad"),
    ("Pad 4 (choir)", "pad"),
    ("Pad 5 (bowed)", "pad"),
    ("Pad 6 (metallic)", "pad"),
    ("Pad 7 (halo)", "pad"),
    ("Pad 8 (sweep)", "pad"),
    ("FX 1 (rain)", "synth"),
    ("FX 2 (soundtrack)", "synth"),
    ("FX 3 (crystal)", "synth"),
    ("FX 4 (atmosphere)", "synth"),
    ("FX 5 (brightness)", "synth"),
    ("FX 6 (goblins)", "synth"),
    ("FX 7 (echoes)", "synth"),
    ("FX 8 (sci-fi)", "synth"),
    ("Sitar", "ethnic"),
    ("Banjo", "ethnic"),
    ("Shamisen", "ethnic"),
    ("Koto", "ethnic"),
    ("Kalimba", "ethnic"),
    ("Bagpipe", "ethnic"),
    ("Fiddle", "strings"),
    ("Shanai", "ethnic"),
    ("Tinkle Bell", "percussion"),
    ("Agogo", "percussion"),
    ("Steel Drums", "percussion"),
    ("Woodblock", "percussion"),
    ("Taiko Drum", "percussion"),
    ("Melodic Tom", "percussion"),
    ("Synth Drum", "percussion"),
    ("Reverse Cymbal", "percussion"),
    ("Guitar Fret Noise", "sound_effects"),
    ("Breath Noise", "sound_effects"),
    ("Seashore", "sound_effects"),
    ("Bird Tweet", "sound_effects"),
    ("Telephone Ring", "sound_effects"),
    ("Helicopter", "sound_effects"),
    ("Applause", "sound_effects"),
    ("Gunshot", "sound_effects"),
)

assert len(_GM_PROGRAMS) == 128, "GM Level-1 map must contain 128 programs"

GM_PROGRAM_BY_NUMBER: dict[int, GmProgramInfo] = {
    index: GmProgramInfo(program=index, name=name, family=family)
    for index, (name, family) in enumerate(_GM_PROGRAMS)
}

# High-confidence role mapping from canonical instrument identity → track role.
_IDENTITY_TO_ROLE: dict[str, str] = {
    "bass": "bass",
    "drums": "drums",
}

_EXPLICIT_ROLE_ALIASES: dict[str, str] = {
    "counter_melody": "countermelody",
    "counter melody": "countermelody",
    "drum": "drums",
    "perc": "percussion",
    "kick": "drums",
}

_ROLE_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def gm_program_info(program: int) -> GmProgramInfo | None:
    if program < 0 or program > 127:
        return None
    return GM_PROGRAM_BY_NUMBER[program]


def gm_program_name(program: int) -> str | None:
    info = gm_program_info(program)
    return info.name if info else None


def resolve_import_instrument(
    *,
    source_program: int | None,
    instrument_name: str | None,
    explicit_role: str | None,
    channel: int | None,
    is_drum: bool = False,
    default_program: int = 0,
) -> ImportInstrumentResolution:
    """Resolve instrument label, program, and role for an imported track."""
    instrument_defaulted = False
    midi_program = source_program if source_program is not None else default_program
    if source_program is None:
        instrument_defaulted = True
    if midi_program < 0 or midi_program > 127:
        midi_program = default_program
        instrument_defaulted = True

    gm = gm_program_info(midi_program)
    preferred_name = (instrument_name or "").strip() or (gm.name if gm else "Acoustic Grand Piano")
    normalized = normalize_instrument(preferred_name)
    if normalized is None and gm is not None:
        normalized = normalize_instrument(gm.name)
    if normalized is None:
        instrument = preferred_name.lower()
        drum_flag = bool(is_drum or channel == 10)
    else:
        instrument = normalized.identity
        drum_flag = bool(is_drum or channel == 10 or normalized.is_drum)

    role, role_source = infer_import_role(
        explicit_role=explicit_role,
        channel=channel,
        is_drum=drum_flag,
        instrument=instrument,
        instrument_name=preferred_name,
    )
    logger.debug(
        "Resolved import instrument",
        extra={
            "midi_program": midi_program,
            "instrument": instrument,
            "role": role,
            "role_source": role_source,
            "is_drum": drum_flag,
            "instrument_defaulted": instrument_defaulted,
            "channel": channel,
        },
    )
    return ImportInstrumentResolution(
        instrument=instrument,
        midi_program=midi_program,
        is_drum=drum_flag,
        role=role,
        role_source=role_source,
        instrument_defaulted=instrument_defaulted,
    )


def infer_import_role(
    *,
    explicit_role: str | None,
    channel: int | None,
    is_drum: bool,
    instrument: str,
    instrument_name: str | None = None,
) -> tuple[str, RoleDecisionSource]:
    """Return ``(role, source)`` using only high-confidence signals."""
    explicit = _normalize_explicit_role(explicit_role)
    if explicit is not None:
        return explicit, "explicit_role"

    if is_drum or channel == 10:
        return "drums", "channel_percussion"

    identity = normalize_instrument_identity(instrument) or normalize_instrument_identity(
        instrument_name or ""
    )
    if identity and is_drum_identity(identity):
        return "drums", "instrument_identity"
    if identity in _IDENTITY_TO_ROLE:
        return _IDENTITY_TO_ROLE[identity], "instrument_identity"

    # Unambiguous bass family from GM names / aliases already covered by identity.
    # Do not infer melody/harmony/pad/lead from content or vague names.
    return IMPORT_NEUTRAL_TRACK_ROLE, "default"


def _normalize_explicit_role(value: str | None) -> str | None:
    if value is None:
        return None
    raw = normalize_label_text(value)
    if not raw:
        return None
    aliased = _EXPLICIT_ROLE_ALIASES.get(raw, raw.replace(" ", "_").replace("-", "_"))
    if aliased in SUPPORTED_TRACK_ROLES:
        return aliased
    # Allow compact tokens embedded in part names like "Role: Bass".
    tokens = _ROLE_TOKEN_RE.findall(aliased)
    for token in tokens:
        mapped = _EXPLICIT_ROLE_ALIASES.get(token, token)
        if mapped in SUPPORTED_TRACK_ROLES:
            return mapped
    return None
