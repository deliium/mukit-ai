"""Deterministic vocabulary builder for ``tokenizer.v1``."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from app.composition_schemas import SUPPORTED_SECTION_TYPES, SUPPORTED_TRACK_ROLES
from app.tokenizer.errors import TokenizerVocabError
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConfigV1,
    TokenizerVocabFamilyCounts,
    TokenizerVocabV1,
    canonical_json_dumps,
    supported_key_strings,
    supported_meter_strings,
    COND_GENRES,
    COND_INSTSETS,
    COND_MOODS,
    HARM_CLASSES,
)
from app.tokenizer import special_tokens as st


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Vocab:
    """In-memory token ↔ id maps with stable hash."""

    token_to_id: dict[str, int]
    id_to_token: dict[int, str]
    vocab_hash: str
    family_counts: TokenizerVocabFamilyCounts
    special_token_ids: dict[str, int]
    tokenizer_version: str = TOKENIZER_VERSION

    @property
    def size(self) -> int:
        return len(self.token_to_id)

    def token(self, token_id: int) -> str:
        try:
            return self.id_to_token[token_id]
        except KeyError as exc:
            raise TokenizerVocabError(
                "unknown_token_id",
                f"token id {token_id} not in vocabulary",
                details={"token_id": token_id},
            ) from exc

    def id(self, token_str: str) -> int:
        try:
            return self.token_to_id[token_str]
        except KeyError as exc:
            raise TokenizerVocabError(
                "unknown_token_str",
                f"token {token_str!r} not in vocabulary",
                details={"token_str": token_str[:64]},
            ) from exc

    def get_id(self, token_str: str, default: int | None = None) -> int | None:
        return self.token_to_id.get(token_str, default)

    def to_schema(self) -> TokenizerVocabV1:
        return TokenizerVocabV1(
            tokenizer_version=self.tokenizer_version,
            vocab_hash=self.vocab_hash,
            token_to_id=dict(self.token_to_id),
            family_counts=self.family_counts,
            special_token_ids=dict(self.special_token_ids),
            size=self.size,
        )


def build_vocab(config: TokenizerConfigV1) -> Vocab:
    """Build a closed, deterministic token→id map.

    Practical size guard: ``config.max_vocab_size`` (default 8192). Reject if
    the closed families would exceed that limit.
    """
    token_to_id: dict[str, int] = {}
    counts = TokenizerVocabFamilyCounts()

    def add(token: str, family: str) -> None:
        if token in token_to_id:
            return
        token_to_id[token] = len(token_to_id)
        if family == "special":
            counts.special += 1
        elif family == "conditioning":
            counts.conditioning += 1
        elif family == "structure":
            counts.structure += 1
        elif family == "track":
            counts.track += 1
        elif family == "time":
            counts.time += 1
        elif family == "note":
            counts.note += 1
        elif family == "harmony":
            counts.harmony += 1

    # --- Special (PAD must be id 0) ---
    for name in st.SPECIAL_TOKEN_ORDER:
        add(name, "special")
    if token_to_id[st.PAD] != 0:
        raise TokenizerVocabError("pad_not_zero", "PAD must be assigned id 0")

    # --- Conditioning (optional closed enums + UNK buckets) ---
    if config.conditioning_enabled:
        for key in supported_key_strings():
            add(f"{st.COND_KEY_PREFIX}{_slug(key)}", "conditioning")
        add(f"{st.COND_KEY_PREFIX}{st.UNK_SUFFIX}", "conditioning")
        for genre in COND_GENRES:
            add(f"{st.COND_GENRE_PREFIX}{genre}", "conditioning")
        add(f"{st.COND_GENRE_PREFIX}{st.UNK_SUFFIX}", "conditioning")
        for mood in COND_MOODS:
            add(f"{st.COND_MOOD_PREFIX}{mood}", "conditioning")
        add(f"{st.COND_MOOD_PREFIX}{st.UNK_SUFFIX}", "conditioning")
        for inst in COND_INSTSETS:
            add(f"{st.COND_INSTSET_PREFIX}{inst}", "conditioning")
        add(f"{st.COND_INSTSET_PREFIX}{st.UNK_SUFFIX}", "conditioning")
        for section in sorted(SUPPORTED_SECTION_TYPES):
            add(f"{st.COND_SECTION_TYPE_PREFIX}{section}", "conditioning")
        add(f"{st.COND_SECTION_TYPE_PREFIX}{st.UNK_SUFFIX}", "conditioning")

    # --- Structure ---
    if config.include_meter:
        for meter in supported_meter_strings():
            add(f"{st.METER_PREFIX}{_slug(meter)}", "structure")
        add(f"{st.METER_PREFIX}{st.UNK_SUFFIX}", "structure")
    if config.include_tempo:
        for bpm in range(40, 241):
            add(f"{st.TEMPO_PREFIX}{bpm}", "structure")
    if config.include_key_context:
        for key in supported_key_strings():
            add(f"{st.KEY_PREFIX}{_slug(key)}", "structure")
        add(f"{st.KEY_PREFIX}{st.UNK_SUFFIX}", "structure")
    if config.include_sections:
        for section in sorted(SUPPORTED_SECTION_TYPES):
            add(f"{st.SECTION_TYPE_PREFIX}{section}", "structure")
        add(f"{st.SECTION_TYPE_PREFIX}{st.UNK_SUFFIX}", "structure")
    for role in sorted(SUPPORTED_TRACK_ROLES):
        add(f"TRACK_ROLE_{role}", "structure")

    # --- Track ---
    for slot in range(config.max_tracks):
        add(f"{st.TRACK_SLOT_PREFIX}{slot}", "track")
    if config.include_track_program:
        for program in range(128):
            add(f"{st.TRACK_PROG_PREFIX}{program}", "track")

    # --- Time ---
    for pos in range(config.max_bar_steps):
        add(f"{st.POS_PREFIX}{pos}", "time")
    for dur in range(1, config.max_dur_steps + 1):
        add(f"{st.DUR_PREFIX}{dur}", "time")

    # --- Note ---
    for pitch in range(128):
        add(f"{st.PITCH_PREFIX}{pitch}", "note")
    for vel in range(config.velocity_bins):
        add(f"{st.VEL_PREFIX}{vel}", "note")

    # --- Harmony (optional) ---
    if config.emit_harmony:
        for cls in HARM_CLASSES:
            add(f"{st.HARM_PREFIX}{cls}", "harmony")
        add(f"{st.HARM_PREFIX}{st.UNK_SUFFIX}", "harmony")

    size = len(token_to_id)
    if size > config.max_vocab_size:
        raise TokenizerVocabError(
            "vocab_too_large",
            f"vocab size {size} exceeds max_vocab_size {config.max_vocab_size}",
            details={"vocab_size": size, "max_vocab_size": config.max_vocab_size},
        )

    vocab_hash = _hash_token_map(token_to_id)
    # Self-check: re-hash must be identical.
    check_hash = _hash_token_map(token_to_id)
    if check_hash != vocab_hash:
        logger.error(
            "Vocab hash non-deterministic",
            extra={"hash_a": vocab_hash[:12], "hash_b": check_hash[:12]},
        )
        raise TokenizerVocabError("vocab_hash_unstable", "vocab hash self-check failed")

    special_token_ids = {name: token_to_id[name] for name in st.SPECIAL_TOKEN_ORDER}
    id_to_token = {idx: tok for tok, idx in token_to_id.items()}

    logger.info(
        "Tokenizer vocab built",
        extra={
            "tokenizer_version": TOKENIZER_VERSION,
            "vocab_size": size,
            "vocab_hash_prefix": vocab_hash[:12],
            "profile": config.profile,
        },
    )
    logger.debug(
        "Tokenizer vocab family counts",
        extra={
            "special": counts.special,
            "conditioning": counts.conditioning,
            "structure": counts.structure,
            "track": counts.track,
            "time": counts.time,
            "note": counts.note,
            "harmony": counts.harmony,
            "max_bar_steps": config.max_bar_steps,
        },
    )
    return Vocab(
        token_to_id=token_to_id,
        id_to_token=id_to_token,
        vocab_hash=vocab_hash,
        family_counts=counts,
        special_token_ids=special_token_ids,
    )


def _hash_token_map(token_to_id: dict[str, int]) -> str:
    material = canonical_json_dumps(token_to_id)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    return value.strip().replace(" ", "_").replace("/", "_").replace("#", "s")
