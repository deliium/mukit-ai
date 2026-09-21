"""Token ids/strs → Composition V2 decoder."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.composition_schemas import (
    CompositionV2,
    CompositionV2HarmonyItem,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2TempoChange,
    CompositionV2TimeSignatureChange,
    CompositionV2Track,
    bar_duration_ticks,
)
from app.tokenizer import special_tokens as st
from app.tokenizer.conditioning import parse_conditioning_from_tokens
from app.tokenizer.errors import TokenizerDecodeError
from app.tokenizer.quantize import bin_to_velocity, key_token_slug, midi_to_pitch_name
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConfigV1,
    TokenizerDecodeReportV1,
    TokenizerIssueCode,
    TokenizerSequenceV1,
    default_tokenizer_config,
)
from app.tokenizer.validate import repair_token_sequence, validate_token_sequence
from app.tokenizer.vocab import Vocab, build_vocab


logger = logging.getLogger(__name__)


@dataclass
class _TrackHeader:
    slot: int
    midi_program: int = 0
    is_drum: bool = False
    role: str = "other"


@dataclass
class _NoteAccum:
    slot: int
    pitch: int
    vel_bin: int | None = None
    dur_steps: int | None = None


@dataclass
class _DecodeState:
    tempo: int = 120
    key: str = "C major"
    time_signature: str = "4/4"
    tracks: dict[int, _TrackHeader] = field(default_factory=dict)
    events: dict[int, list[CompositionV2NoteEvent]] = field(default_factory=dict)
    sections: list[tuple[int, str]] = field(default_factory=list)  # (bar_index0, type)
    tempo_changes: list[tuple[int, int]] = field(default_factory=list)  # (tick, bpm)
    meter_changes: list[tuple[int, str]] = field(default_factory=list)
    harmony: list[CompositionV2HarmonyItem] = field(default_factory=list)
    bar_meters: list[str] = field(default_factory=list)


def decode_tokens(
    ids_or_strs: list[int] | list[str] | TokenizerSequenceV1,
    config: TokenizerConfigV1 | None = None,
    vocab: Vocab | None = None,
    *,
    on_invalid: str | None = None,
) -> tuple[CompositionV2, TokenizerDecodeReportV1]:
    """Decode token ids or strings into a validated Composition V2 document."""
    cfg = config or default_tokenizer_config()
    if on_invalid is not None:
        cfg = cfg.model_copy(update={"on_invalid": on_invalid})
    active_vocab = vocab or build_vocab(cfg)

    token_ids, token_strs = _normalize_input(ids_or_strs, active_vocab)
    tokens_in = len(token_ids)

    validation = validate_token_sequence(token_ids, cfg, active_vocab, token_strs=token_strs)
    issue_codes: list[TokenizerIssueCode] = list(validation.issue_codes)

    if validation.result == "rejected" and cfg.on_invalid == "reject":
        logger.error(
            "Decode rejected invalid sequence",
            extra={"issue_code_count": len(issue_codes), "tokens_in": tokens_in},
        )
        raise TokenizerDecodeError(
            "sequence_rejected",
            "token sequence rejected by validation",
            details={"issue_codes": issue_codes[:32]},
        )

    if cfg.on_invalid == "repair" and validation.result != "ok":
        repaired = repair_token_sequence(token_ids, cfg, active_vocab, token_strs=token_strs)
        token_ids = repaired.token_ids
        token_strs = repaired.token_strs
        for code in repaired.issue_codes:
            if code not in issue_codes:
                issue_codes.append(code)
        result_code = "repaired"
    else:
        result_code = validation.result if validation.result != "rejected" else "ok"

    try:
        composition, clamped = _parse_tokens(token_strs, cfg)
    except TokenizerDecodeError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Decode failed",
            extra={"error_type": type(exc).__name__, "tokens_in": tokens_in},
        )
        raise TokenizerDecodeError(
            "decode_failed",
            f"failed to decode tokens: {exc}",
            details={"error_type": type(exc).__name__},
        ) from exc

    conditioning = parse_conditioning_from_tokens(token_strs)
    notes_out = sum(len(t.events) for t in composition.tracks)
    report = TokenizerDecodeReportV1(
        tokenizer_version=TOKENIZER_VERSION,
        profile=cfg.profile,
        result=result_code,  # type: ignore[arg-type]
        notes_out=notes_out,
        bars_out=composition.bar_count,
        tokens_in=tokens_in,
        tokens_after_repair=len(token_ids),
        issue_codes=issue_codes,
        conditioning=conditioning,
        clamped_fields=clamped,
    )
    if clamped:
        logger.warning(
            "Decode clamped fields",
            extra={"clamped_fields": clamped[:16], "notes_out": notes_out},
        )
    logger.info(
        "Tokens decoded",
        extra={
            "result": report.result,
            "notes_out": notes_out,
            "bars_out": composition.bar_count,
            "tokens_in": tokens_in,
            "tokenizer_version": TOKENIZER_VERSION,
        },
    )
    return composition, report


def _normalize_input(
    ids_or_strs: list[int] | list[str] | TokenizerSequenceV1,
    vocab: Vocab,
) -> tuple[list[int], list[str]]:
    if isinstance(ids_or_strs, TokenizerSequenceV1):
        ids = list(ids_or_strs.token_ids)
        if ids_or_strs.token_strs is not None:
            return ids, list(ids_or_strs.token_strs)
        return ids, [vocab.id_to_token.get(i, st.UNK) for i in ids]
    if not ids_or_strs:
        return [], []
    if isinstance(ids_or_strs[0], str):
        strs = list(ids_or_strs)  # type: ignore[arg-type]
        ids = [vocab.token_to_id.get(t, vocab.id(st.UNK)) for t in strs]
        return ids, strs
    ids = list(ids_or_strs)  # type: ignore[arg-type]
    strs = [vocab.id_to_token.get(i, st.UNK) for i in ids]
    return ids, strs


def _parse_tokens(
    token_strs: list[str],
    config: TokenizerConfigV1,
) -> tuple[CompositionV2, list[str]]:
    state = _DecodeState()
    clamped: list[str] = []
    i = 0
    n = len(token_strs)
    in_headers = True
    current_slot = 0
    bar_index = -1
    position_steps = 0
    pending: _NoteAccum | None = None
    known_keys = {key_token_slug(k): k for k in _all_keys()}

    def flush_pending(start_tick: int) -> None:
        nonlocal pending
        if pending is None:
            return
        if pending.vel_bin is None or pending.dur_steps is None:
            pending = None
            return
        vel = bin_to_velocity(pending.vel_bin, config.velocity_bins)
        dur_ticks = pending.dur_steps * config.grid_ticks
        pitch_name = midi_to_pitch_name(pending.pitch)
        event = CompositionV2NoteEvent(
            pitch=pitch_name,
            start_tick=start_tick,
            duration_ticks=dur_ticks,
            velocity=vel,
        )
        state.events.setdefault(pending.slot, []).append(event)
        pending = None

    while i < n:
        tok = token_strs[i]
        if tok in (st.BOS, st.EOS, st.PAD):
            i += 1
            continue
        if tok.startswith(st.COND_KEY_PREFIX) or tok.startswith(st.COND_GENRE_PREFIX):
            i += 1
            continue
        if tok.startswith(st.COND_MOOD_PREFIX) or tok.startswith(st.COND_INSTSET_PREFIX):
            i += 1
            continue
        if tok.startswith(st.COND_SECTION_TYPE_PREFIX):
            i += 1
            continue

        if tok == st.BAR:
            flush_pending(_bar_start_tick(state, bar_index, position_steps, config) if bar_index >= 0 else 0)
            pending = None
            bar_index += 1
            position_steps = 0
            in_headers = False
            # Inherit previous meter if not set for this bar yet
            if len(state.bar_meters) <= bar_index:
                prev = state.bar_meters[-1] if state.bar_meters else state.time_signature
                state.bar_meters.append(prev)
            i += 1
            continue

        if tok.startswith(st.TEMPO_PREFIX):
            bpm = int(tok[len(st.TEMPO_PREFIX) :])
            bpm = max(40, min(240, bpm))
            if in_headers and bar_index < 0:
                state.tempo = bpm
            elif bar_index >= 0:
                tick = _absolute_bar_start(state, bar_index, config)
                if tick > 0:
                    state.tempo_changes.append((tick, bpm))
                else:
                    state.tempo = bpm
            i += 1
            continue

        if tok.startswith(st.METER_PREFIX):
            meter = _meter_from_token(tok)
            if meter is None:
                i += 1
                continue
            if in_headers and bar_index < 0:
                state.time_signature = meter
            elif bar_index >= 0:
                while len(state.bar_meters) <= bar_index:
                    prev = state.bar_meters[-1] if state.bar_meters else state.time_signature
                    state.bar_meters.append(prev)
                state.bar_meters[bar_index] = meter
                tick = _absolute_bar_start(state, bar_index, config)
                if tick > 0:
                    state.meter_changes.append((tick, meter))
                else:
                    state.time_signature = meter
            i += 1
            continue

        if tok.startswith(st.KEY_PREFIX) and not tok.startswith(st.COND_KEY_PREFIX):
            slug = tok[len(st.KEY_PREFIX) :]
            if slug != st.UNK_SUFFIX:
                state.key = known_keys.get(slug, state.key)
            i += 1
            continue

        if tok == st.TRACK:
            # Expect TRACK_SLOT next
            i += 1
            slot = 0
            is_drum = False
            program = 0
            role = "other"
            if i < n and token_strs[i].startswith(st.TRACK_SLOT_PREFIX):
                slot = int(token_strs[i][len(st.TRACK_SLOT_PREFIX) :])
                i += 1
            if i < n and token_strs[i] == st.DRUM:
                is_drum = True
                i += 1
            if i < n and token_strs[i].startswith(st.TRACK_PROG_PREFIX):
                program = int(token_strs[i][len(st.TRACK_PROG_PREFIX) :])
                i += 1
            if i < n and token_strs[i].startswith("TRACK_ROLE_"):
                role = token_strs[i][len("TRACK_ROLE_") :]
                i += 1
            state.tracks[slot] = _TrackHeader(
                slot=slot, midi_program=program, is_drum=is_drum, role=role
            )
            continue

        if tok == st.SECTION:
            i += 1
            stype = "unsectioned"
            if i < n and token_strs[i].startswith(st.SECTION_TYPE_PREFIX):
                raw = token_strs[i][len(st.SECTION_TYPE_PREFIX) :]
                if raw != st.UNK_SUFFIX:
                    stype = raw
                i += 1
            if bar_index >= 0:
                state.sections.append((bar_index, stype))
            continue

        if tok.startswith(st.SECTION_TYPE_PREFIX):
            # Bare section type without SECTION marker
            raw = tok[len(st.SECTION_TYPE_PREFIX) :]
            if bar_index >= 0 and raw != st.UNK_SUFFIX:
                state.sections.append((bar_index, raw))
            i += 1
            continue

        if tok.startswith(st.POS_PREFIX):
            flush_pending(_bar_start_tick(state, bar_index, position_steps, config) if bar_index >= 0 else 0)
            pending = None
            position_steps = int(tok[len(st.POS_PREFIX) :])
            if position_steps >= config.max_bar_steps:
                position_steps = config.max_bar_steps - 1
                if "position_steps" not in clamped:
                    clamped.append("position_steps")
            i += 1
            continue

        if tok.startswith(st.HARM_PREFIX):
            if config.emit_harmony and bar_index >= 0:
                cls = tok[len(st.HARM_PREFIX) :]
                start = _bar_start_tick(state, bar_index, position_steps, config)
                state.harmony.append(
                    CompositionV2HarmonyItem(
                        start_tick=start,
                        duration_ticks=config.grid_ticks,
                        chord=_harmony_chord_label(cls),
                    )
                )
            i += 1
            continue

        if tok.startswith(st.TRACK_SLOT_PREFIX):
            flush_pending(_bar_start_tick(state, bar_index, position_steps, config) if bar_index >= 0 else 0)
            pending = None
            current_slot = int(tok[len(st.TRACK_SLOT_PREFIX) :])
            if current_slot not in state.tracks:
                state.tracks[current_slot] = _TrackHeader(slot=current_slot)
            i += 1
            continue

        if tok.startswith(st.PITCH_PREFIX):
            flush_pending(_bar_start_tick(state, bar_index, position_steps, config) if bar_index >= 0 else 0)
            pitch = int(tok[len(st.PITCH_PREFIX) :])
            pending = _NoteAccum(slot=current_slot, pitch=pitch)
            i += 1
            continue

        if tok.startswith(st.VEL_PREFIX) and pending is not None:
            pending.vel_bin = int(tok[len(st.VEL_PREFIX) :])
            i += 1
            continue

        if tok.startswith(st.DUR_PREFIX) and pending is not None:
            pending.dur_steps = int(tok[len(st.DUR_PREFIX) :])
            start = _bar_start_tick(state, max(bar_index, 0), position_steps, config)
            flush_pending(start)
            i += 1
            continue

        # Ignore TRACK_ROLE / TRACK_PROG outside TRACK header (already consumed)
        if tok.startswith(st.TRACK_PROG_PREFIX) or tok.startswith("TRACK_ROLE_"):
            i += 1
            continue

        i += 1

    flush_pending(_bar_start_tick(state, max(bar_index, 0), position_steps, config) if bar_index >= 0 else 0)

    bar_count = max(1, bar_index + 1)
    # Ensure bar_meters length
    while len(state.bar_meters) < bar_count:
        prev = state.bar_meters[-1] if state.bar_meters else state.time_signature
        state.bar_meters.append(prev)
    if not state.bar_meters:
        state.bar_meters = [state.time_signature] * bar_count

    # Compute duration from bar meters
    duration_ticks = 0
    boundaries = [0]
    for bi in range(bar_count):
        meter = state.bar_meters[bi]
        duration_ticks += bar_duration_ticks(meter, config.ticks_per_quarter)
        boundaries.append(duration_ticks)

    # Build meter changes (skip root)
    meter_change_models: list[CompositionV2TimeSignatureChange] = []
    seen_meters: set[int] = set()
    for tick, meter in state.meter_changes:
        if tick <= 0 or tick >= duration_ticks or tick in seen_meters:
            continue
        if tick not in boundaries:
            continue
        meter_change_models.append(
            CompositionV2TimeSignatureChange(tick=tick, time_signature=meter)
        )
        seen_meters.add(tick)

    tempo_change_models: list[CompositionV2TempoChange] = []
    seen_tempos: set[int] = set()
    for tick, bpm in state.tempo_changes:
        if tick <= 0 or tick >= duration_ticks or tick in seen_tempos:
            continue
        tempo_change_models.append(CompositionV2TempoChange(tick=tick, bpm=bpm))
        seen_tempos.add(tick)

    # Tracks
    if not state.tracks:
        state.tracks[0] = _TrackHeader(slot=0)

    track_models: list[CompositionV2Track] = []
    for slot in sorted(state.tracks):
        header = state.tracks[slot]
        events = sorted(
            state.events.get(slot, []),
            key=lambda e: (e.start_tick, e.pitch),
        )
        # Clip events that overrun duration
        clipped: list[CompositionV2NoteEvent] = []
        for ev in events:
            if ev.start_tick >= duration_ticks:
                clamped.append("event_start_tick")
                continue
            end = ev.start_tick + ev.duration_ticks
            if end > duration_ticks:
                clipped.append(
                    ev.model_copy(update={"duration_ticks": duration_ticks - ev.start_tick})
                )
                clamped.append("event_duration_ticks")
            else:
                clipped.append(ev)
        channel = 10 if header.is_drum else min(16, max(1, slot + 1 if slot + 1 != 10 else 11))
        track_models.append(
            CompositionV2Track(
                id=f"track_{slot}",
                name=f"Track {slot}",
                instrument="drums" if header.is_drum else f"program_{header.midi_program}",
                role=header.role if header.role else "other",
                midi_program=header.midi_program,
                channel=channel,
                is_drum=header.is_drum,
                events=clipped,
            )
        )

    sections = _build_sections(state.sections, bar_count, boundaries)

    # Harmony: extend last span duration best-effort; drop if overlapping badly
    harmony = _normalize_harmony(state.harmony, duration_ticks) if config.emit_harmony else []

    composition = CompositionV2(
        tempo=state.tempo,
        key=state.key,
        time_signature=state.time_signature,
        ticks_per_quarter=config.ticks_per_quarter,
        duration_ticks=duration_ticks,
        bar_count=bar_count,
        sections=sections,
        tracks=track_models,
        harmony=harmony,
        tempo_changes=tempo_change_models,
        time_signature_changes=meter_change_models,
    )
    return composition, sorted(set(clamped))


def _build_sections(
    section_starts: list[tuple[int, str]],
    bar_count: int,
    boundaries: list[int],
) -> list[CompositionV2Section]:
    if not section_starts:
        return [
            CompositionV2Section(
                id="section_0",
                type="unsectioned",
                start_bar=1,
                bar_count=bar_count,
                start_tick=0,
                duration_ticks=boundaries[-1],
            )
        ]
    # Deduplicate by bar_index0 keeping first
    by_bar: dict[int, str] = {}
    for bar_i, stype in section_starts:
        by_bar.setdefault(bar_i, stype)
    starts = sorted(by_bar.keys())
    sections: list[CompositionV2Section] = []
    for idx, bar_i in enumerate(starts):
        next_bar = starts[idx + 1] if idx + 1 < len(starts) else bar_count
        bar_count_sec = max(1, next_bar - bar_i)
        start_tick = boundaries[bar_i]
        end_tick = boundaries[min(bar_i + bar_count_sec, len(boundaries) - 1)]
        sections.append(
            CompositionV2Section(
                id=f"section_{idx}",
                type=by_bar[bar_i],
                start_bar=bar_i + 1,
                bar_count=bar_count_sec,
                start_tick=start_tick,
                duration_ticks=max(1, end_tick - start_tick),
            )
        )
    # Ensure coverage from bar 1
    if sections[0].start_bar != 1:
        gap_bars = sections[0].start_bar - 1
        lead = CompositionV2Section(
            id="section_lead",
            type="unsectioned",
            start_bar=1,
            bar_count=gap_bars,
            start_tick=0,
            duration_ticks=boundaries[gap_bars],
        )
        sections.insert(0, lead)
    # Ensure coverage to end
    last = sections[-1]
    covered = last.start_bar + last.bar_count - 1
    if covered < bar_count:
        start_bar = covered + 1
        start_tick = boundaries[start_bar - 1]
        sections.append(
            CompositionV2Section(
                id="section_tail",
                type="unsectioned",
                start_bar=start_bar,
                bar_count=bar_count - covered,
                start_tick=start_tick,
                duration_ticks=boundaries[-1] - start_tick,
            )
        )
    return sections


def _normalize_harmony(
    items: list[CompositionV2HarmonyItem],
    duration_ticks: int,
) -> list[CompositionV2HarmonyItem]:
    if not items:
        return []
    sorted_items = sorted(items, key=lambda h: h.start_tick)
    out: list[CompositionV2HarmonyItem] = []
    for idx, item in enumerate(sorted_items):
        if item.start_tick >= duration_ticks:
            continue
        next_start = (
            sorted_items[idx + 1].start_tick
            if idx + 1 < len(sorted_items)
            else duration_ticks
        )
        dur = max(1, min(item.duration_ticks, next_start - item.start_tick, duration_ticks - item.start_tick))
        if out and item.start_tick < out[-1].start_tick + out[-1].duration_ticks:
            continue
        out.append(CompositionV2HarmonyItem(start_tick=item.start_tick, duration_ticks=dur, chord=item.chord))
    return out


def _bar_start_tick(
    state: _DecodeState,
    bar_index: int,
    position_steps: int,
    config: TokenizerConfigV1,
) -> int:
    return _absolute_bar_start(state, bar_index, config) + position_steps * config.grid_ticks


def _absolute_bar_start(state: _DecodeState, bar_index: int, config: TokenizerConfigV1) -> int:
    tick = 0
    for bi in range(max(0, bar_index)):
        meter = state.bar_meters[bi] if bi < len(state.bar_meters) else state.time_signature
        tick += bar_duration_ticks(meter, config.ticks_per_quarter)
    return tick


def _meter_from_token(tok: str) -> str | None:
    raw = tok[len(st.METER_PREFIX) :]
    if raw == st.UNK_SUFFIX:
        return None
    parts = raw.split("_")
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def _harmony_chord_label(cls: str) -> str:
    mapping = {
        "maj": "C",
        "min": "Cm",
        "dim": "Cdim",
        "aug": "Caug",
        "sus": "Csus4",
        "dom7": "C7",
        "maj7": "Cmaj7",
        "min7": "Cm7",
        "other": "C",
        "UNK": "C",
    }
    return mapping.get(cls, "C")


def _all_keys() -> tuple[str, ...]:
    from app.tokenizer.schemas import supported_key_strings

    return supported_key_strings()
