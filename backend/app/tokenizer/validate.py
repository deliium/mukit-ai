"""Token sequence validation and deterministic repair."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.tokenizer import special_tokens as st
from app.tokenizer.schemas import (
    TokenizerConfigV1,
    TokenizerIssueCode,
    TokenizerResultCode,
)
from app.tokenizer.vocab import Vocab


logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    result: TokenizerResultCode
    issue_codes: list[TokenizerIssueCode] = field(default_factory=list)
    token_ids: list[int] = field(default_factory=list)
    token_strs: list[str] = field(default_factory=list)
    garbage_count: int = 0


def validate_token_sequence(
    token_ids: list[int],
    config: TokenizerConfigV1,
    vocab: Vocab,
    *,
    token_strs: list[str] | None = None,
) -> ValidationResult:
    """Validate token ids; does not mutate. Returns issue codes + classified result."""
    strs = token_strs or [_safe_str(vocab, tid) for tid in token_ids]
    issues: list[TokenizerIssueCode] = []
    garbage = 0

    if config.require_bos_eos:
        if st.BOS not in strs:
            issues.append("missing_bos")
        if st.EOS not in strs:
            issues.append("missing_eos")
        if st.BOS in strs and strs.index(st.BOS) > 0:
            issues.append("tokens_before_bos")
        if st.EOS in strs:
            eos_i = strs.index(st.EOS)
            if any(t != st.PAD for t in strs[eos_i + 1 :]):
                issues.append("tokens_after_eos")

    for tok in strs:
        if tok == st.UNK or tok not in vocab.token_to_id:
            garbage += 1
            if "unknown_token" not in issues:
                issues.append("unknown_token")
        if tok.startswith(st.POS_PREFIX):
            try:
                pos = int(tok[len(st.POS_PREFIX) :])
            except ValueError:
                garbage += 1
                continue
            if pos < 0 or pos >= config.max_bar_steps:
                issues.append("pos_out_of_range")
        if tok.startswith(st.DUR_PREFIX):
            try:
                dur = int(tok[len(st.DUR_PREFIX) :])
            except ValueError:
                garbage += 1
                continue
            if dur < 1 or dur > config.max_dur_steps:
                issues.append("dur_out_of_range")
        if tok.startswith(st.TRACK_SLOT_PREFIX):
            try:
                slot = int(tok[len(st.TRACK_SLOT_PREFIX) :])
            except ValueError:
                garbage += 1
                continue
            if slot < 0 or slot >= config.max_tracks:
                issues.append("unknown_track_slot")
        if tok.startswith(st.HARM_PREFIX) and not config.emit_harmony:
            issues.append("harmony_disabled")

    rate = garbage / max(1, len(strs))
    if rate > config.max_garbage_rate:
        issues.append("garbage_rate_exceeded")

    hard_reject = {
        "missing_bos",
        "missing_eos",
        "garbage_rate_exceeded",
        "empty_note_payload",
        "conflicting_meter",
    }
    # Empty sequence
    if not strs or all(t == st.PAD for t in strs):
        issues.append("empty_note_payload")

    if hard_reject.intersection(issues) and config.on_invalid == "reject":
        result: TokenizerResultCode = "rejected"
    elif issues:
        result = "repaired" if config.on_invalid == "repair" else "rejected"
    else:
        result = "ok"

    logger.info(
        "Token sequence validated",
        extra={
            "result": result,
            "token_count": len(token_ids),
            "issue_code_count": len(issues),
            "garbage_count": garbage,
        },
    )
    logger.debug(
        "Validation issue codes",
        extra={"issue_codes": issues[:32]},
    )
    return ValidationResult(
        result=result,
        issue_codes=issues,
        token_ids=list(token_ids),
        token_strs=strs,
        garbage_count=garbage,
    )


def repair_token_sequence(
    token_ids: list[int],
    config: TokenizerConfigV1,
    vocab: Vocab,
    *,
    token_strs: list[str] | None = None,
) -> ValidationResult:
    """Deterministic repair: strip PAD, clip to BOS..EOS, clamp POS/DUR, drop orphans."""
    strs = token_strs or [_safe_str(vocab, tid) for tid in token_ids]
    issues: list[TokenizerIssueCode] = []
    original_len = len(strs)

    # Drop before first BOS / after first EOS
    if st.BOS in strs:
        bos_i = strs.index(st.BOS)
        if bos_i > 0:
            issues.append("tokens_before_bos")
            strs = strs[bos_i:]
    elif config.require_bos_eos:
        issues.append("missing_bos")
        strs = [st.BOS, *strs]

    if st.EOS in strs:
        eos_i = strs.index(st.EOS)
        if eos_i < len(strs) - 1:
            issues.append("tokens_after_eos")
            strs = strs[: eos_i + 1]
    elif config.require_bos_eos:
        issues.append("missing_eos")
        strs = [*strs, st.EOS]

    repaired: list[str] = []
    saw_pitch = False
    last_was_bar = False
    for tok in strs:
        if tok == st.PAD:
            if "pad_stripped" not in issues:
                issues.append("pad_stripped")
            continue
        if tok == st.BAR:
            if last_was_bar:
                if "duplicate_bar" not in issues:
                    issues.append("duplicate_bar")
                continue
            last_was_bar = True
            repaired.append(tok)
            saw_pitch = False
            continue
        last_was_bar = False

        if tok.startswith(st.HARM_PREFIX) and not config.emit_harmony:
            if "harmony_disabled" not in issues:
                issues.append("harmony_disabled")
            continue

        if tok.startswith(st.POS_PREFIX):
            try:
                pos = int(tok[len(st.POS_PREFIX) :])
            except ValueError:
                continue
            if pos < 0 or pos >= config.max_bar_steps:
                pos = max(0, min(config.max_bar_steps - 1, pos))
                if "pos_out_of_range" not in issues:
                    issues.append("pos_out_of_range")
            repaired.append(f"{st.POS_PREFIX}{pos}")
            saw_pitch = False
            continue

        if tok.startswith(st.DUR_PREFIX):
            if not saw_pitch:
                if "orphan_duration" not in issues:
                    issues.append("orphan_duration")
                continue
            try:
                dur = int(tok[len(st.DUR_PREFIX) :])
            except ValueError:
                continue
            if dur < 1 or dur > config.max_dur_steps:
                dur = max(1, min(config.max_dur_steps, dur))
                if "dur_out_of_range" not in issues:
                    issues.append("dur_out_of_range")
            repaired.append(f"{st.DUR_PREFIX}{dur}")
            saw_pitch = False
            continue

        if tok.startswith(st.VEL_PREFIX):
            if not saw_pitch:
                if "orphan_velocity" not in issues:
                    issues.append("orphan_velocity")
                continue
            try:
                vel = int(tok[len(st.VEL_PREFIX) :])
            except ValueError:
                continue
            vel = max(0, min(config.velocity_bins - 1, vel))
            repaired.append(f"{st.VEL_PREFIX}{vel}")
            continue

        if tok.startswith(st.PITCH_PREFIX):
            try:
                pitch = int(tok[len(st.PITCH_PREFIX) :])
            except ValueError:
                continue
            pitch = max(0, min(127, pitch))
            repaired.append(f"{st.PITCH_PREFIX}{pitch}")
            saw_pitch = True
            continue

        repaired.append(tok)

    new_ids = [vocab.id(t) if t in vocab.token_to_id else vocab.id(st.UNK) for t in repaired]
    result: TokenizerResultCode = "repaired" if issues or len(repaired) != original_len else "ok"
    if len(repaired) != original_len:
        logger.warning(
            "Repair mutated sequence length",
            extra={
                "original_len": original_len,
                "repaired_len": len(repaired),
                "issue_code_count": len(issues),
            },
        )
    logger.info(
        "Token sequence repaired",
        extra={"result": result, "token_count": len(new_ids), "issue_code_count": len(issues)},
    )
    logger.debug("Repair issue codes", extra={"issue_codes": issues[:32]})
    return ValidationResult(
        result=result,
        issue_codes=issues,
        token_ids=new_ids,
        token_strs=repaired,
        garbage_count=0,
    )


def _safe_str(vocab: Vocab, token_id: int) -> str:
    return vocab.id_to_token.get(token_id, st.UNK)
