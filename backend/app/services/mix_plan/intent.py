"""Deterministic natural-language → ``mix.intent.v1``.

The compiler, not a language model, chooses numeric mix values.
Optional LLM help may only echo this same intent object.
"""

from __future__ import annotations

import logging
import re

from app.mix_plan_schemas import (
    MIX_PLAN_INTENT_LLM_UNAVAILABLE,
    MIX_PLAN_INTENT_UNRECOGNIZED,
    MIX_PLAN_ROLE_UNAVAILABLE,
    MixIntentV1,
    MixPlanError,
    MixPlanWarning,
)

logger = logging.getLogger(__name__)

_ROLES = ("piano", "bass", "strings", "drums", "vocals", "other")
_ROLE_ALT = "|".join(_ROLES)
_DOMINANCE = re.compile(rf"^make(?: the)? ({_ROLE_ALT}) less dominant$")
_SPACE = re.compile(rf"^give(?: the)? ({_ROLE_ALT}) more space$")
_CLIMAX = re.compile(r"^make the climax wider$")
_MASKING = re.compile(r"^reduce low(?: |-)?frequency masking$")


def normalize_phrase(text: str) -> str:
    lowered = (text or "").lower()
    cleaned = re.sub(r"[^a-z0-9\s-]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def compile_phrase(text: str, active_roles: set[str] | list[str]) -> MixIntentV1:
    """Map a known phrase to an intent. Unknown phrases raise."""
    norm = normalize_phrase(text)
    roles_present = {str(role) for role in active_roles}
    dominance = _DOMINANCE.match(norm)
    if dominance:
        return _role_intent("reduce_dominance", dominance.group(1), "decrease", roles_present, norm)
    space = _SPACE.match(norm)
    if space:
        return _role_intent("more_space", space.group(1), "increase", roles_present, norm)
    if _CLIMAX.match(norm):
        intent = MixIntentV1(intent_code="wider_climax", roles=[], polarity="widen")
        _log_intent(intent, len(norm))
        return intent
    if _MASKING.match(norm):
        intent = MixIntentV1(intent_code="reduce_lf_masking", roles=[], polarity="carve")
        _log_intent(intent, len(norm))
        return intent
    logger.info(
        "Mix plan phrase unrecognized",
        extra={"char_count": len(text or ""), "norm_char_count": len(norm)},
    )
    raise MixPlanError(
        "Mix phrase is not a supported operation",
        code=MIX_PLAN_INTENT_UNRECOGNIZED,
        details={"char_count": len(text or "")},
    )


def resolve_phrase_intent(
    text: str,
    active_roles: set[str] | list[str],
    *,
    include_llm: bool,
    fake_mode: bool,
    llm_fake_mode: bool,
) -> tuple[MixIntentV1, list[MixPlanWarning]]:
    """Deterministic intent. Fake LLM returns the same object; unwired LLM warns."""
    intent = compile_phrase(text, active_roles)
    warnings: list[MixPlanWarning] = []
    if include_llm and not (fake_mode or llm_fake_mode):
        warnings.append(
            MixPlanWarning(
                code=MIX_PLAN_INTENT_LLM_UNAVAILABLE,
                message=(
                    "Language-model intent help is unavailable. "
                    "Parameters come from the deterministic phrase compiler."
                ),
            )
        )
        logger.info(
            "Mix plan intent LLM unavailable; using deterministic compiler",
            extra={"intent_code": intent.intent_code, "char_count": len(text or "")},
        )
    else:
        logger.info(
            "Mix plan intent resolved",
            extra={
                "intent_code": intent.intent_code,
                "role_count": len(intent.roles),
                "char_count": len(text or ""),
                "llm_fake": bool(fake_mode or llm_fake_mode),
            },
        )
    return intent, warnings


def _role_intent(
    code: str,
    role: str,
    polarity: str,
    active_roles: set[str],
    norm: str,
) -> MixIntentV1:
    if role not in active_roles:
        logger.info(
            "Mix plan role unavailable",
            extra={"intent_code": code, "role": role, "active_role_count": len(active_roles)},
        )
        raise MixPlanError(
            "Requested stem role is not on the active stem set",
            code=MIX_PLAN_ROLE_UNAVAILABLE,
            details={"role": role},
        )
    intent = MixIntentV1(intent_code=code, roles=[role], polarity=polarity)  # type: ignore[arg-type]
    _log_intent(intent, len(norm))
    return intent


def _log_intent(intent: MixIntentV1, char_count: int) -> None:
    logger.debug(
        "Mix plan phrase compiled",
        extra={
            "intent_code": intent.intent_code,
            "roles": list(intent.roles),
            "char_count": char_count,
        },
    )
