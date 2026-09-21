"""Composition V2 / dataset.example.v1 → token sequence encoder."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.composition_schemas import CompositionV2, midi_pitch_number
from app.dataset.schemas import DatasetExampleV1
from app.tokenizer import special_tokens as st
from app.tokenizer.conditioning import (
    conditioning_from_mapping,
    emit_conditioning_tokens,
)
from app.tokenizer.errors import TokenizerEncodeError
from app.tokenizer.quantize import (
    key_token_slug,
    meter_token_slug,
    snap_duration_steps,
    velocity_to_bin,
)
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConditioningV1,
    TokenizerConfigV1,
    TokenizerEncodeReportV1,
    TokenizerIssueCode,
    TokenizerSequenceV1,
    default_tokenizer_config,
)
from app.tokenizer.timeline_adapter import compile_tokenizer_timeline, tick_to_bar_position
from app.tokenizer.vocab import Vocab, build_vocab


logger = logging.getLogger(__name__)


def encode_composition(
    doc: CompositionV2 | dict[str, Any] | DatasetExampleV1,
    config: TokenizerConfigV1 | None = None,
    *,
    conditioning: TokenizerConditioningV1 | dict[str, Any] | None = None,
    vocab: Vocab | None = None,
    include_token_strs: bool = True,
) -> TokenizerSequenceV1:
    """Encode a Composition V2 (or dataset example) into a versioned token sequence.

    Does not mutate the input document. Playable notes are read only from
    ``tracks[].events[]``.
    """
    cfg = config or default_tokenizer_config()
    composition, derived_cond = _resolve_input(doc)
    cond = (
        conditioning_from_mapping(conditioning)
        if isinstance(conditioning, dict)
        else conditioning
    )
    if cond is None:
        cond = derived_cond

    if composition.ticks_per_quarter != cfg.ticks_per_quarter:
        raise TokenizerEncodeError(
            "ppq_mismatch",
            "composition ticks_per_quarter does not match tokenizer config",
            details={
                "composition_ppq": composition.ticks_per_quarter,
                "config_ppq": cfg.ticks_per_quarter,
            },
        )

    active_vocab = vocab or build_vocab(cfg)
    timeline = compile_tokenizer_timeline(composition, cfg)

    issue_codes: list[TokenizerIssueCode] = []
    notes_in = 0
    notes_emitted = 0
    snapped_onset = 0
    snapped_duration = 0
    dropped_invalid = 0
    omitted_expressive = 0

    token_strs: list[str] = []

    if cfg.require_bos_eos:
        token_strs.append(st.BOS)

    cond_tokens, unknown_cond = emit_conditioning_tokens(cond, cfg, active_vocab)
    token_strs.extend(cond_tokens)

    # Root structure headers
    if cfg.include_tempo:
        token_strs.append(f"{st.TEMPO_PREFIX}{int(composition.tempo)}")
    if cfg.include_meter:
        meter_tok = f"{st.METER_PREFIX}{meter_token_slug(composition.time_signature)}"
        if meter_tok in active_vocab.token_to_id:
            token_strs.append(meter_tok)
        else:
            token_strs.append(f"{st.METER_PREFIX}{st.UNK_SUFFIX}")
            issue_codes.append("unsupported_meter")
    if cfg.include_key_context:
        key_tok = f"{st.KEY_PREFIX}{key_token_slug(composition.key)}"
        if key_tok in active_vocab.token_to_id:
            token_strs.append(key_tok)
        else:
            token_strs.append(f"{st.KEY_PREFIX}{st.UNK_SUFFIX}")

    tracks = list(composition.tracks)
    if len(tracks) > cfg.max_tracks:
        raise TokenizerEncodeError(
            "track_limit_exceeded",
            f"composition has {len(tracks)} tracks; max_tracks={cfg.max_tracks}",
            details={"track_count": len(tracks), "max_tracks": cfg.max_tracks},
        )

    # Track headers: TRACK TRACK_SLOT_i [DRUM] TRACK_PROG_p TRACK_ROLE_r
    for slot, track in enumerate(tracks):
        token_strs.append(st.TRACK)
        token_strs.append(f"{st.TRACK_SLOT_PREFIX}{slot}")
        if track.is_drum:
            token_strs.append(st.DRUM)
        if cfg.include_track_program:
            token_strs.append(f"{st.TRACK_PROG_PREFIX}{int(track.midi_program)}")
        role_tok = f"TRACK_ROLE_{track.role}"
        if role_tok in active_vocab.token_to_id:
            token_strs.append(role_tok)

    # Collect quantized notes grouped by (bar, pos)
    # value: list of (slot, pitch_midi, vel_bin, dur_steps)
    by_bar_pos: dict[tuple[int, int], list[tuple[int, int, int, int]]] = defaultdict(list)
    section_by_bar: dict[int, str] = {}
    for section in composition.sections:
        start0 = int(section.start_bar) - 1
        section_by_bar[start0] = section.type

    for slot, track in enumerate(tracks):
        for event in track.events:
            if getattr(event, "type", "note") != "note":
                continue
            notes_in += 1
            articulations = list(getattr(event, "articulations", None) or [])
            tie = getattr(event, "tie", None)
            if articulations or tie:
                omitted_expressive += 1
                if "omitted_expressive" not in issue_codes:
                    issue_codes.append("omitted_expressive")
            try:
                pitch_midi = midi_pitch_number(event.pitch)
            except ValueError:
                dropped_invalid += 1
                if "invalid_pitch" not in issue_codes:
                    issue_codes.append("invalid_pitch")
                continue
            if pitch_midi < 0 or pitch_midi > 127:
                dropped_invalid += 1
                if "invalid_pitch" not in issue_codes:
                    issue_codes.append("invalid_pitch")
                continue

            bar_pos, onset_snapped = tick_to_bar_position(
                int(event.start_tick), timeline, cfg
            )
            if onset_snapped:
                snapped_onset += 1
                if "snapped_onset" not in issue_codes:
                    issue_codes.append("snapped_onset")

            dur_steps, dur_snapped, dur_clamped = snap_duration_steps(
                int(event.duration_ticks), cfg
            )
            if dur_snapped or dur_clamped:
                snapped_duration += 1
                if "snapped_duration" not in issue_codes:
                    issue_codes.append("snapped_duration")

            vel = int(getattr(event, "velocity", 64) or 64)
            if vel < 1 or vel > 127:
                dropped_invalid += 1
                if "invalid_velocity" not in issue_codes:
                    issue_codes.append("invalid_velocity")
                continue
            vel_bin = velocity_to_bin(vel, cfg.velocity_bins)
            by_bar_pos[(bar_pos.bar_index0, bar_pos.position_steps)].append(
                (slot, pitch_midi, vel_bin, dur_steps)
            )
            notes_emitted += 1

    # Optional harmony context (non-audible)
    harmony_by_bar_pos: dict[tuple[int, int], str] = {}
    if cfg.emit_harmony:
        for item in composition.harmony or []:
            bar_pos, _ = tick_to_bar_position(int(item.start_tick), timeline, cfg)
            chord = str(getattr(item, "chord", "") or "")
            harmony_by_bar_pos[(bar_pos.bar_index0, bar_pos.position_steps)] = (
                _coarse_harmony_class(chord)
            )

    # Meter / tempo changes keyed by bar start tick
    meter_at_bar: dict[int, str] = {0: composition.time_signature}
    for change in composition.time_signature_changes or []:
        tick = int(change.tick)
        bar_1 = timeline.bar_at_tick(tick)
        if timeline.bar_start_tick(bar_1) == tick:
            meter_at_bar[bar_1 - 1] = change.time_signature

    tempo_at_bar: dict[int, int] = {0: int(composition.tempo)}
    for change in composition.tempo_changes or []:
        tick = int(change.tick)
        bar_1 = timeline.bar_at_tick(min(tick, timeline.duration_ticks - 1 if timeline.duration_ticks else 0))
        if timeline.bar_start_tick(bar_1) == tick or tick == 0:
            tempo_at_bar[bar_1 - 1] = int(change.bpm)

    prev_meter = composition.time_signature
    prev_tempo = int(composition.tempo)
    bars_emitted = 0
    current_slot: int | None = None

    for bar_i in range(timeline.bar_count):
        token_strs.append(st.BAR)
        bars_emitted += 1

        if cfg.include_meter and bar_i in meter_at_bar and meter_at_bar[bar_i] != prev_meter:
            meter = meter_at_bar[bar_i]
            meter_tok = f"{st.METER_PREFIX}{meter_token_slug(meter)}"
            token_strs.append(
                meter_tok if meter_tok in active_vocab.token_to_id else f"{st.METER_PREFIX}{st.UNK_SUFFIX}"
            )
            prev_meter = meter

        if cfg.include_tempo and bar_i in tempo_at_bar and tempo_at_bar[bar_i] != prev_tempo:
            bpm = tempo_at_bar[bar_i]
            token_strs.append(f"{st.TEMPO_PREFIX}{bpm}")
            prev_tempo = bpm

        if cfg.include_sections and bar_i in section_by_bar:
            token_strs.append(st.SECTION)
            stype = section_by_bar[bar_i]
            sec_tok = f"{st.SECTION_TYPE_PREFIX}{stype}"
            token_strs.append(
                sec_tok if sec_tok in active_vocab.token_to_id else f"{st.SECTION_TYPE_PREFIX}{st.UNK_SUFFIX}"
            )

        # Positions in this bar (stable sorted)
        positions = sorted({pos for (b, pos) in by_bar_pos if b == bar_i})
        harm_positions = sorted({pos for (b, pos) in harmony_by_bar_pos if b == bar_i})
        all_pos = sorted(set(positions) | set(harm_positions))

        for pos in all_pos:
            token_strs.append(f"{st.POS_PREFIX}{pos}")
            if cfg.emit_harmony and (bar_i, pos) in harmony_by_bar_pos:
                token_strs.append(f"{st.HARM_PREFIX}{harmony_by_bar_pos[(bar_i, pos)]}")

            notes = sorted(
                by_bar_pos.get((bar_i, pos), []),
                key=lambda n: (n[0], n[1]),
            )
            for slot, pitch_midi, vel_bin, dur_steps in notes:
                if current_slot != slot:
                    token_strs.append(f"{st.TRACK_SLOT_PREFIX}{slot}")
                    current_slot = slot
                token_strs.append(f"{st.PITCH_PREFIX}{pitch_midi}")
                token_strs.append(f"{st.VEL_PREFIX}{vel_bin}")
                token_strs.append(f"{st.DUR_PREFIX}{dur_steps}")

    if cfg.require_bos_eos:
        token_strs.append(st.EOS)

    # Map to ids
    token_ids: list[int] = []
    for tok in token_strs:
        tid = active_vocab.get_id(tok)
        if tid is None:
            token_ids.append(active_vocab.id(st.UNK))
            dropped_invalid += 1
            if "unknown_token" not in issue_codes:
                issue_codes.append("unknown_token")
        else:
            token_ids.append(tid)

    report = TokenizerEncodeReportV1(
        tokenizer_version=TOKENIZER_VERSION,
        profile=cfg.profile,
        notes_in=notes_in,
        notes_emitted=notes_emitted,
        bars_emitted=bars_emitted,
        tokens_emitted=len(token_ids),
        snapped_onset=snapped_onset,
        snapped_duration=snapped_duration,
        dropped_invalid=dropped_invalid,
        omitted_expressive=omitted_expressive,
        unknown_conditioning=unknown_cond,
        issue_codes=issue_codes,
    )
    digest = cfg.config_digest()
    logger.info(
        "Composition encoded",
        extra={
            "tokenizer_version": TOKENIZER_VERSION,
            "vocab_hash_prefix": active_vocab.vocab_hash[:12],
            "profile": cfg.profile,
            "notes_in": notes_in,
            "notes_emitted": notes_emitted,
            "bars_emitted": bars_emitted,
            "tokens_emitted": len(token_ids),
            "snapped_onset": snapped_onset,
            "snapped_duration": snapped_duration,
            "dropped_invalid": dropped_invalid,
        },
    )
    logger.debug(
        "Encode snap/drop detail",
        extra={
            "omitted_expressive": omitted_expressive,
            "unknown_conditioning": unknown_cond,
            "issue_code_count": len(issue_codes),
            "token_prefix": token_strs[:64],
        },
    )
    return TokenizerSequenceV1(
        tokenizer_version=TOKENIZER_VERSION,
        vocab_hash=active_vocab.vocab_hash,
        config_digest=digest,
        profile=cfg.profile,
        token_ids=token_ids,
        token_strs=token_strs if include_token_strs else None,
        encode_report=report,
    )


def _resolve_input(
    doc: CompositionV2 | dict[str, Any] | DatasetExampleV1,
) -> tuple[CompositionV2, TokenizerConditioningV1 | None]:
    if isinstance(doc, DatasetExampleV1):
        return doc.composition, None
    if isinstance(doc, CompositionV2):
        return doc, None
    if isinstance(doc, dict):
        schema = doc.get("schema_version")
        if schema == "dataset.example.v1":
            example = DatasetExampleV1.model_validate(doc)
            return example.composition, None
        if schema == "dataset.item.v1" and doc.get("composition"):
            composition = CompositionV2.model_validate(doc["composition"])
            labels = doc.get("labels") or {}
            cond = None
            if isinstance(labels, dict) and labels:
                cond = TokenizerConditioningV1(
                    genre=labels.get("genre"),
                    mood=None,
                    key=composition.key,
                )
            elif isinstance(labels, dict):
                cond = TokenizerConditioningV1(genre=labels.get("genre"), key=composition.key)
            return composition, cond
        return CompositionV2.model_validate(doc), None
    raise TokenizerEncodeError(
        "unsupported_input",
        f"unsupported encode input type {type(doc).__name__}",
    )


def _coarse_harmony_class(chord: str) -> str:
    text = chord.strip().lower()
    if not text:
        return st.UNK_SUFFIX
    if "maj7" in text or "Δ" in text:
        return "maj7"
    if "min7" in text or "m7" in text:
        return "min7"
    if "dim" in text or "°" in text:
        return "dim"
    if "aug" in text or "+" in text:
        return "aug"
    if "sus" in text:
        return "sus"
    if "7" in text:
        return "dom7"
    if "min" in text or text.endswith("m") or "minor" in text:
        return "min"
    if "maj" in text or "major" in text:
        return "maj"
    return "other"
