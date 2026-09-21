"""Optional conditioning prefix tokens for encode/decode."""

from __future__ import annotations

import logging
from typing import Any

from app.composition_schemas import SUPPORTED_SECTION_TYPES
from app.tokenizer import special_tokens as st
from app.tokenizer.quantize import key_token_slug
from app.tokenizer.schemas import (
    COND_GENRES,
    COND_INSTSETS,
    COND_MOODS,
    TokenizerConditioningV1,
    TokenizerConfigV1,
    supported_key_strings,
)
from app.tokenizer.vocab import Vocab


logger = logging.getLogger(__name__)


def conditioning_from_mapping(data: dict[str, Any] | None) -> TokenizerConditioningV1 | None:
    if not data:
        return None
    return TokenizerConditioningV1(
        key=data.get("key"),
        genre=data.get("genre"),
        mood=data.get("mood"),
        instrument_set=data.get("instrument_set") or data.get("instset"),
        section_type=data.get("section_type"),
    )


def emit_conditioning_tokens(
    conditioning: TokenizerConditioningV1 | None,
    config: TokenizerConfigV1,
    vocab: Vocab,
) -> tuple[list[str], int]:
    """Return token strings and unknown_conditioning count."""
    if not config.conditioning_enabled or conditioning is None:
        return [], 0

    tokens: list[str] = []
    unknown = 0

    if conditioning.key:
        slug = key_token_slug(conditioning.key)
        name = f"{st.COND_KEY_PREFIX}{slug}"
        if name in vocab.token_to_id:
            tokens.append(name)
        else:
            tokens.append(f"{st.COND_KEY_PREFIX}{st.UNK_SUFFIX}")
            unknown += 1
            logger.debug("Unknown conditioning key", extra={"token_family": "COND_KEY"})

    if conditioning.genre:
        g = conditioning.genre.strip().lower()
        name = f"{st.COND_GENRE_PREFIX}{g if g in COND_GENRES else st.UNK_SUFFIX}"
        if g not in COND_GENRES:
            unknown += 1
        tokens.append(name)

    if conditioning.mood:
        m = conditioning.mood.strip().lower()
        name = f"{st.COND_MOOD_PREFIX}{m if m in COND_MOODS else st.UNK_SUFFIX}"
        if m not in COND_MOODS:
            unknown += 1
        tokens.append(name)

    if conditioning.instrument_set:
        i = conditioning.instrument_set.strip().lower()
        name = f"{st.COND_INSTSET_PREFIX}{i if i in COND_INSTSETS else st.UNK_SUFFIX}"
        if i not in COND_INSTSETS:
            unknown += 1
        tokens.append(name)

    if conditioning.section_type:
        s = conditioning.section_type.strip().lower()
        if s in SUPPORTED_SECTION_TYPES:
            tokens.append(f"{st.COND_SECTION_TYPE_PREFIX}{s}")
        else:
            tokens.append(f"{st.COND_SECTION_TYPE_PREFIX}{st.UNK_SUFFIX}")
            unknown += 1

    if unknown:
        logger.info(
            "Conditioning UNK tokens applied",
            extra={"unknown_conditioning": unknown, "token_count": len(tokens)},
        )
    else:
        logger.debug(
            "Conditioning tokens applied",
            extra={"token_names": tokens, "token_count": len(tokens)},
        )
    return tokens, unknown


def parse_conditioning_from_tokens(token_strs: list[str]) -> TokenizerConditioningV1:
    """Extract conditioning sidecar from a token string prefix (best-effort)."""
    key = genre = mood = instset = section = None
    known_keys = {key_token_slug(k): k for k in supported_key_strings()}
    for tok in token_strs:
        if tok.startswith(st.COND_KEY_PREFIX) and not tok.endswith(st.UNK_SUFFIX):
            slug = tok[len(st.COND_KEY_PREFIX) :]
            key = known_keys.get(slug, slug.replace("_", " ").replace("s", "#", 1))
        elif tok.startswith(st.COND_GENRE_PREFIX) and not tok.endswith(st.UNK_SUFFIX):
            genre = tok[len(st.COND_GENRE_PREFIX) :]
        elif tok.startswith(st.COND_MOOD_PREFIX) and not tok.endswith(st.UNK_SUFFIX):
            mood = tok[len(st.COND_MOOD_PREFIX) :]
        elif tok.startswith(st.COND_INSTSET_PREFIX) and not tok.endswith(st.UNK_SUFFIX):
            instset = tok[len(st.COND_INSTSET_PREFIX) :]
        elif tok.startswith(st.COND_SECTION_TYPE_PREFIX) and not tok.endswith(st.UNK_SUFFIX):
            section = tok[len(st.COND_SECTION_TYPE_PREFIX) :]
    return TokenizerConditioningV1(
        key=key,
        genre=genre,
        mood=mood,
        instrument_set=instset,
        section_type=section,
    )
