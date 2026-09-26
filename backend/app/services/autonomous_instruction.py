"""Closed interpreter for one arrangement instruction.

The sentence is never written to logs. Effects are ``thin_strings`` or
``unparsed``. Unsafe text raises before any column is stored.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence

from app.arrangement_schemas import ARRANGEMENT_MAX_INSTRUCTION_CHARS
from app.autonomous_composer_schemas import (
    AUTONOMOUS_INSTRUCTION_UNPARSED,
    AUTONOMOUS_INSTRUCTION_UNSAFE,
    AutonomousPlanError,
)
from app.composition_schemas import KEY_PATTERN

logger = logging.getLogger(__name__)

THIN_STRING_EFFECT = "thin_strings"
UNPARSED_EFFECT = "unparsed"

_THIN_TOKENS = ("smaller", "thinner", "reduce", "less", "sparse")
_MELODY_PHRASES = (
    "new melody",
    "replace the melody",
    "change the melody",
    "different melody",
    "new theme",
)
_ADD_SHAPES = ("add {family}", "with {family}", "include {family}")
_KEY_TOKEN = re.compile(r"\b[A-G](?:#|b)?\s+(?:major|minor)\b", re.IGNORECASE)


def interpret_arrangement_instruction(
    text: str,
    *,
    forbidden_families: Sequence[str],
    opening_key: str,
    final_section_key: str | None,
) -> str:
    """Return ``thin_strings`` or ``unparsed``. Raise when the text is unsafe."""
    instruction_len = len(text)
    logger.debug(
        "Interpreting arrangement instruction",
        extra={"instruction_len": instruction_len},
    )
    if instruction_len > ARRANGEMENT_MAX_INSTRUCTION_CHARS:
        _refuse(instruction_len)
    folded = " ".join(text.casefold().split())
    if _adds_forbidden_family(folded, forbidden_families) or _rewrites_melody(folded):
        _refuse(instruction_len)
    if _changes_key(
        text,
        opening_key=opening_key,
        final_section_key=final_section_key,
    ):
        _refuse(instruction_len)
    if _requests_thinner_strings(folded):
        logger.info(
            "Arrangement instruction interpreted",
            extra={"instruction_len": instruction_len, "effect": THIN_STRING_EFFECT},
        )
        return THIN_STRING_EFFECT
    logger.debug(
        "Arrangement instruction unparsed",
        extra={"instruction_len": instruction_len, "effect": UNPARSED_EFFECT},
    )
    return UNPARSED_EFFECT



def _refuse(instruction_len: int) -> None:
    logger.warning(
        "Arrangement instruction refused",
        extra={
            "code": AUTONOMOUS_INSTRUCTION_UNSAFE,
            "instruction_len": instruction_len,
        },
    )
    raise AutonomousPlanError(
        "instruction unsafe",
        code=AUTONOMOUS_INSTRUCTION_UNSAFE,
    )


def _requests_thinner_strings(folded: str) -> bool:
    if "string" not in folded:
        return False
    return any(token in folded for token in _THIN_TOKENS)


def _rewrites_melody(folded: str) -> bool:
    return any(phrase in folded for phrase in _MELODY_PHRASES)


def _adds_forbidden_family(folded: str, forbidden_families: Sequence[str]) -> bool:
    for family in forbidden_families:
        label = " ".join(str(family).casefold().split())
        if not label:
            continue
        for shape in _ADD_SHAPES:
            if shape.format(family=label) in folded:
                return True
    return False


def _changes_key(
    text: str,
    *,
    opening_key: str,
    final_section_key: str | None,
) -> bool:
    allowed = {_canonical_key(opening_key)}
    if final_section_key:
        allowed.add(_canonical_key(final_section_key))
    for sentence in re.split(r"[.!?]+", text):
        folded = f" {sentence.casefold()} "
        if "key" not in folded and " in " not in folded:
            continue
        for match in _KEY_TOKEN.finditer(sentence):
            label = _canonical_key(match.group(0))
            if not KEY_PATTERN.match(label):
                continue
            if label.casefold() not in {item.casefold() for item in allowed}:
                return True
    return False


def _canonical_key(value: str) -> str:
    return " ".join(value.strip().split())
