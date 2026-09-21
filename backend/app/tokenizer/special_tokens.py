"""Fixed special-token names and closed conditioning/harmony enums."""

from __future__ import annotations

# Fixed ids assigned in order during vocab build (PAD must be 0).
PAD = "PAD"
BOS = "BOS"
EOS = "EOS"
UNK = "UNK"
BAR = "BAR"
TRACK = "TRACK"  # generic track marker (slot tokens are TRACK_SLOT_*)
SECTION = "SECTION"  # generic section marker (type tokens are SECTION_TYPE_*)
DRUM = "DRUM"

SPECIAL_TOKEN_ORDER: tuple[str, ...] = (
    PAD,
    BOS,
    EOS,
    UNK,
    BAR,
    TRACK,
    SECTION,
    DRUM,
)

# Prefixes for factored families.
POS_PREFIX = "POS_"
DUR_PREFIX = "DUR_"
PITCH_PREFIX = "PITCH_"
VEL_PREFIX = "VEL_"
METER_PREFIX = "METER_"
TEMPO_PREFIX = "TEMPO_"
KEY_PREFIX = "KEY_"
TRACK_SLOT_PREFIX = "TRACK_SLOT_"
TRACK_PROG_PREFIX = "TRACK_PROG_"
SECTION_TYPE_PREFIX = "SECTION_TYPE_"
COND_KEY_PREFIX = "COND_KEY_"
COND_GENRE_PREFIX = "COND_GENRE_"
COND_MOOD_PREFIX = "COND_MOOD_"
COND_INSTSET_PREFIX = "COND_INSTSET_"
COND_SECTION_TYPE_PREFIX = "COND_SECTION_TYPE_"
HARM_PREFIX = "HARM_"

UNK_SUFFIX = "UNK"
