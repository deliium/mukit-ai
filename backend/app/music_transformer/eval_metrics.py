"""Locked symbolic eval metrics (validity / distributional only — not quality).

Metrics consume token ids and/or decoded ``CompositionV2``. Playable notes come
**only** from ``tracks[].events[]`` — never from ``harmony``.
"""

from __future__ import annotations

import logging
import statistics
from collections import Counter
from typing import Any, Sequence

from app.composition_schemas import CompositionV2, bar_duration_ticks, midi_pitch_number
from app.music_transformer.errors import MusicTransformerEvalError
from app.music_transformer.experiment_schemas import (
    MetricValueV1,
    MusicTransformerEvalReportV1,
    SymbolicMetricName,
)
from app.services.composition_validator import validate_composition_integrity
from app.tokenizer.decode import decode_tokens
from app.tokenizer.schemas import TokenizerConfigV1, default_tokenizer_config
from app.tokenizer.validate import validate_token_sequence
from app.tokenizer.vocab import Vocab, build_vocab


logger = logging.getLogger(__name__)

# Major / natural-minor diatonic intervals; minor also admits harmonic/melodic
# leading tones so short passages in minor are not over-penalized.
_MAJOR_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
_MINOR_NATURAL = (0, 2, 3, 5, 7, 8, 10)
_MINOR_EXTRA = (9, 11)  # raised 6 / raised 7

_TONAL_LIMITS_DETAIL = (
    "tonal_consistency is a crude diatonic pitch-class coverage ratio against "
    "the composition's declared key (or a pitch-mass inferred tonic when key "
    "is missing/unparseable). It ignores modulation, chromatic ornament, "
    "non-diatonic harmony function, and does not score musical quality."
)

_DENSITY_BINS = ("0", "1-2", "3-4", "5+")


def compute_metrics_for_samples(
    samples: Sequence[dict[str, Any]],
    *,
    metrics: Sequence[SymbolicMetricName] | None = None,
    tokenizer_config: TokenizerConfigV1 | None = None,
    experiment_id: str | None = None,
    global_step: int | None = None,
) -> MusicTransformerEvalReportV1:
    """Aggregate locked symbolic metrics over sample dicts.

    Each sample may include:
    - ``token_ids``: list[int]
    - ``composition``: CompositionV2 or dict
    - ``status``: optional opaque status string
    """
    metric_names: list[SymbolicMetricName] = list(
        metrics
        or [
            "valid_token_rate",
            "valid_composition_decode_rate",
            "pitch_class_distribution",
            "note_density_distribution",
            "rhythmic_distribution",
            "repetition",
            "interval_distribution",
            "instrument_range_violations",
            "tonal_consistency",
        ]
    )
    tok_cfg = tokenizer_config or default_tokenizer_config()
    vocab = build_vocab(tok_cfg)

    prepared: list[_PreparedSample] = []
    for raw in samples:
        prepared.append(_prepare_sample(raw, tok_cfg=tok_cfg, vocab=vocab))

    values: list[MetricValueV1] = []
    for name in metric_names:
        try:
            values.append(_compute_one(name, prepared, tok_cfg=tok_cfg, vocab=vocab))
        except Exception as exc:  # noqa: BLE001 — isolate per-metric failures
            logger.debug(
                "Metric computation failed",
                extra={"metric": name, "error_type": type(exc).__name__},
            )
            values.append(
                MetricValueV1(
                    name=name,
                    status="error",
                    value=None,
                    detail=f"{type(exc).__name__}: {exc}"[:240],
                )
            )

    report = MusicTransformerEvalReportV1(
        experiment_id=experiment_id,
        global_step=global_step,
        sample_count=len(prepared),
        musical_quality_claim=False,
        metrics=values,
    )

    decode_ok = next((m for m in values if m.name == "valid_composition_decode_rate"), None)
    valid_tok = next((m for m in values if m.name == "valid_token_rate"), None)
    range_m = next((m for m in values if m.name == "instrument_range_violations"), None)
    logger.info(
        "Symbolic eval metrics computed",
        extra={
            "sample_count": len(prepared),
            "metric_count": len(values),
            "valid_token_rate": None if valid_tok is None else valid_tok.value,
            "valid_composition_decode_rate": None if decode_ok is None else decode_ok.value,
            "instrument_range_violations": None
            if range_m is None or not isinstance(range_m.value, dict)
            else range_m.value.get("violation_count"),
            "musical_quality_claim": False,
        },
    )
    return report


class _PreparedSample:
    __slots__ = (
        "token_ids",
        "composition",
        "status",
        "token_valid",
        "decode_ok",
        "decode_attempted",
    )

    def __init__(
        self,
        *,
        token_ids: list[int] | None,
        composition: CompositionV2 | None,
        status: str | None,
        token_valid: bool | None,
        decode_ok: bool | None,
        decode_attempted: bool,
    ) -> None:
        self.token_ids = token_ids
        self.composition = composition
        self.status = status
        self.token_valid = token_valid
        self.decode_ok = decode_ok
        self.decode_attempted = decode_attempted


def _prepare_sample(
    raw: dict[str, Any],
    *,
    tok_cfg: TokenizerConfigV1,
    vocab: Vocab,
) -> _PreparedSample:
    token_ids = raw.get("token_ids")
    if token_ids is not None and not isinstance(token_ids, list):
        raise MusicTransformerEvalError(
            "invalid_sample",
            "sample.token_ids must be a list of ints",
        )
    ids: list[int] | None = [int(x) for x in token_ids] if token_ids is not None else None

    composition = _coerce_composition(raw.get("composition"))
    token_valid: bool | None = None
    if ids is not None:
        validation = validate_token_sequence(ids, tok_cfg, vocab)
        token_valid = len(validation.issue_codes) == 0

    decode_ok: bool | None = None
    decode_attempted = False
    if composition is not None:
        integrity = validate_composition_integrity(composition, profile="canonical")
        decode_ok = bool(integrity.ok)
        decode_attempted = True
    elif ids is not None:
        decode_attempted = True
        composition, decode_ok = _try_decode_and_integrity(ids, tok_cfg=tok_cfg, vocab=vocab)

    return _PreparedSample(
        token_ids=ids,
        composition=composition,
        status=str(raw["status"]) if raw.get("status") is not None else None,
        token_valid=token_valid,
        decode_ok=decode_ok,
        decode_attempted=decode_attempted,
    )


def _coerce_composition(value: Any) -> CompositionV2 | None:
    if value is None:
        return None
    if isinstance(value, CompositionV2):
        return value
    if isinstance(value, dict):
        return CompositionV2.model_validate(value)
    raise MusicTransformerEvalError(
        "invalid_sample",
        "sample.composition must be CompositionV2 or dict",
    )


def _try_decode_and_integrity(
    token_ids: list[int],
    *,
    tok_cfg: TokenizerConfigV1,
    vocab: Vocab,
) -> tuple[CompositionV2 | None, bool]:
    try:
        composition, _report = decode_tokens(
            token_ids,
            tok_cfg,
            vocab=vocab,
            on_invalid="repair",
        )
    except Exception:  # noqa: BLE001
        return None, False
    integrity = validate_composition_integrity(composition, profile="canonical")
    if not integrity.ok:
        return composition, False
    return composition, True


def _compute_one(
    name: SymbolicMetricName,
    samples: Sequence[_PreparedSample],
    *,
    tok_cfg: TokenizerConfigV1,
    vocab: Vocab,
) -> MetricValueV1:
    del tok_cfg, vocab  # reserved for future metric-specific knobs
    if name == "valid_token_rate":
        return _metric_valid_token_rate(samples)
    if name == "valid_composition_decode_rate":
        return _metric_valid_composition_decode_rate(samples)
    if name == "pitch_class_distribution":
        return _metric_pitch_class_distribution(samples)
    if name == "note_density_distribution":
        return _metric_note_density_distribution(samples)
    if name == "rhythmic_distribution":
        return _metric_rhythmic_distribution(samples)
    if name == "repetition":
        return _metric_repetition(samples)
    if name == "interval_distribution":
        return _metric_interval_distribution(samples)
    if name == "instrument_range_violations":
        return _metric_instrument_range_violations(samples)
    if name == "tonal_consistency":
        return _metric_tonal_consistency(samples)
    return MetricValueV1(name=name, status="skipped", value=None, detail="unknown metric")


def _metric_valid_token_rate(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    judged = [s for s in samples if s.token_valid is not None]
    if not judged:
        return MetricValueV1(
            name="valid_token_rate",
            status="unavailable",
            value=None,
            detail="no samples with token_ids",
        )
    rate = sum(1 for s in judged if s.token_valid) / len(judged)
    return MetricValueV1(name="valid_token_rate", status="ok", value=float(rate))


def _metric_valid_composition_decode_rate(
    samples: Sequence[_PreparedSample],
) -> MetricValueV1:
    judged = [s for s in samples if s.decode_attempted]
    if not judged:
        return MetricValueV1(
            name="valid_composition_decode_rate",
            status="unavailable",
            value=None,
            detail="no samples with token_ids or composition",
        )
    rate = sum(1 for s in judged if s.decode_ok) / len(judged)
    return MetricValueV1(
        name="valid_composition_decode_rate",
        status="ok",
        value=float(rate),
    )


def _iter_note_events(composition: CompositionV2):
    for track in composition.tracks:
        for event in track.events:
            if getattr(event, "type", "note") == "note":
                yield track, event


def _midi_events(composition: CompositionV2) -> list[tuple[Any, int, int, int]]:
    """Return (track, midi, start_tick, duration_ticks) for note events only."""
    out: list[tuple[Any, int, int, int]] = []
    for track, event in _iter_note_events(composition):
        try:
            midi = midi_pitch_number(event.pitch)
        except ValueError:
            continue
        out.append((track, midi, int(event.start_tick), int(event.duration_ticks)))
    return out


def _compositions(samples: Sequence[_PreparedSample]) -> list[CompositionV2]:
    return [s.composition for s in samples if s.composition is not None]


def _metric_pitch_class_distribution(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="pitch_class_distribution",
            status="unavailable",
            value=None,
            detail="no decoded compositions",
        )
    counts: Counter[str] = Counter()
    for comp in comps:
        for _track, midi, _start, _dur in _midi_events(comp):
            counts[str(midi % 12)] += 1
    total = sum(counts.values()) or 1
    normalized = {k: counts[k] / total for k in (str(i) for i in range(12))}
    return MetricValueV1(
        name="pitch_class_distribution",
        status="ok",
        value={"counts": dict(sorted(counts.items())), "normalized": normalized},
    )


def _density_bin(notes_in_bar: int) -> str:
    if notes_in_bar <= 0:
        return "0"
    if notes_in_bar <= 2:
        return "1-2"
    if notes_in_bar <= 4:
        return "3-4"
    return "5+"


def _metric_note_density_distribution(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="note_density_distribution",
            status="unavailable",
            value=None,
            detail="no decoded compositions",
        )
    per_bar: list[float] = []
    bins: Counter[str] = Counter({b: 0 for b in _DENSITY_BINS})
    for comp in comps:
        bar_ticks = bar_duration_ticks(comp.time_signature, comp.ticks_per_quarter)
        bar_count = max(1, int(comp.bar_count or 1))
        counts = [0] * bar_count
        for _track, _midi, start, _dur in _midi_events(comp):
            bar_i = min(bar_count - 1, start // max(1, bar_ticks))
            counts[bar_i] += 1
        for n in counts:
            per_bar.append(float(n))
            bins[_density_bin(n)] += 1
    mean = statistics.fmean(per_bar) if per_bar else 0.0
    std = statistics.pstdev(per_bar) if len(per_bar) > 1 else 0.0
    return MetricValueV1(
        name="note_density_distribution",
        status="ok",
        value={
            "mean_notes_per_bar": mean,
            "std_notes_per_bar": std,
            "bins": dict(bins),
        },
    )


def _metric_rhythmic_distribution(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="rhythmic_distribution",
            status="unavailable",
            value=None,
            detail="no decoded compositions",
        )
    dur_hist: Counter[str] = Counter()
    onset_mod: Counter[str] = Counter()
    for comp in comps:
        bar_ticks = max(1, bar_duration_ticks(comp.time_signature, comp.ticks_per_quarter))
        for _track, _midi, start, dur in _midi_events(comp):
            dur_hist[str(dur)] += 1
            onset_mod[str(start % bar_ticks)] += 1
    return MetricValueV1(
        name="rhythmic_distribution",
        status="ok",
        value={
            "duration_histogram": dict(sorted(dur_hist.items(), key=lambda kv: int(kv[0]))),
            "onset_mod_bar": dict(sorted(onset_mod.items(), key=lambda kv: int(kv[0]))),
        },
    )


def _metric_repetition(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="repetition",
            status="unavailable",
            value=None,
            detail="no decoded compositions",
        )
    scores: list[float] = []
    for comp in comps:
        pitches = [midi for _t, midi, _s, _d in _midi_events(comp)]
        scores.append(_ngram_self_similarity(pitches, n=3))
    if not scores:
        return MetricValueV1(name="repetition", status="ok", value=0.0)
    return MetricValueV1(
        name="repetition",
        status="ok",
        value=float(statistics.fmean(scores)),
        detail="mean pitch trigram self-similarity across samples (0..1)",
    )


def _ngram_self_similarity(pitches: list[int], *, n: int) -> float:
    if len(pitches) < n:
        return 0.0
    grams = [tuple(pitches[i : i + n]) for i in range(len(pitches) - n + 1)]
    if not grams:
        return 0.0
    counts = Counter(grams)
    # Fraction of n-gram occurrences that are repeats of a previously seen gram.
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    return max(0.0, min(1.0, repeated / len(grams)))


def _metric_interval_distribution(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="interval_distribution",
            status="unavailable",
            value=None,
            detail="no decoded compositions",
        )
    hist: Counter[str] = Counter()
    for comp in comps:
        # Per-track consecutive pitch intervals (MIDI semitones).
        by_track: dict[str, list[int]] = {}
        for track, midi, _s, _d in _midi_events(comp):
            tid = getattr(track, "id", None) or id(track)
            by_track.setdefault(str(tid), []).append(midi)
        for seq in by_track.values():
            for a, b in zip(seq, seq[1:]):
                hist[str(b - a)] += 1
    return MetricValueV1(
        name="interval_distribution",
        status="ok",
        value={"counts": dict(sorted(hist.items(), key=lambda kv: int(kv[0])))},
    )


def _metric_instrument_range_violations(
    samples: Sequence[_PreparedSample],
) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="instrument_range_violations",
            status="unavailable",
            value=None,
            detail="no decoded compositions",
        )
    try:
        from app.services.instrument_catalog import (
            get_catalog,
            resolve_profile_for_track,
        )
    except Exception as exc:  # noqa: BLE001
        return MetricValueV1(
            name="instrument_range_violations",
            status="unavailable",
            value=None,
            detail=f"instrument catalog import failed: {type(exc).__name__}",
        )
    try:
        catalog = get_catalog()
    except Exception as exc:  # noqa: BLE001
        return MetricValueV1(
            name="instrument_range_violations",
            status="unavailable",
            value=None,
            detail=f"instrument catalog unavailable: {type(exc).__name__}",
        )

    violation_count = 0
    resolved_tracks = 0
    unresolved_tracks = 0
    for comp in comps:
        for track in comp.tracks:
            profile = resolve_profile_for_track(track, catalog=catalog)
            if profile is None or profile.range_policy != "absolute":
                unresolved_tracks += 1
                continue
            if profile.playable_low is None or profile.playable_high is None:
                unresolved_tracks += 1
                continue
            resolved_tracks += 1
            low, high = int(profile.playable_low), int(profile.playable_high)
            for event in track.events:
                if getattr(event, "type", "note") != "note":
                    continue
                try:
                    midi = midi_pitch_number(event.pitch)
                except ValueError:
                    violation_count += 1
                    continue
                if midi < low or midi > high:
                    violation_count += 1

    if resolved_tracks == 0:
        return MetricValueV1(
            name="instrument_range_violations",
            status="unavailable",
            value={
                "violation_count": 0,
                "resolved_tracks": 0,
                "unresolved_tracks": unresolved_tracks,
            },
            detail="no tracks resolved to absolute catalog ranges",
        )
    return MetricValueV1(
        name="instrument_range_violations",
        status="ok",
        value={
            "violation_count": violation_count,
            "resolved_tracks": resolved_tracks,
            "unresolved_tracks": unresolved_tracks,
        },
    )


def _metric_tonal_consistency(samples: Sequence[_PreparedSample]) -> MetricValueV1:
    comps = _compositions(samples)
    if not comps:
        return MetricValueV1(
            name="tonal_consistency",
            status="unavailable",
            value=None,
            detail=_TONAL_LIMITS_DETAIL,
        )
    ratios: list[float] = []
    for comp in comps:
        midis = [midi for _t, midi, _s, _d in _midi_events(comp)]
        if not midis:
            continue
        diatonic = _diatonic_set_for_composition(comp, midis)
        if not diatonic:
            continue
        in_set = sum(1 for m in midis if (m % 12) in diatonic)
        ratios.append(in_set / len(midis))
    if not ratios:
        return MetricValueV1(
            name="tonal_consistency",
            status="unavailable",
            value=None,
            detail=_TONAL_LIMITS_DETAIL,
        )
    return MetricValueV1(
        name="tonal_consistency",
        status="ok",
        value=float(statistics.fmean(ratios)),
        detail=_TONAL_LIMITS_DETAIL,
    )


def _diatonic_set_for_composition(comp: CompositionV2, midis: list[int]) -> set[int] | None:
    parsed = _parse_key_label(getattr(comp, "key", None))
    if parsed is None:
        # Infer tonic as most common pitch class; assume major.
        pcs = Counter(m % 12 for m in midis)
        if not pcs:
            return None
        tonic = pcs.most_common(1)[0][0]
        return {(tonic + i) % 12 for i in _MAJOR_INTERVALS}
    tonic_pc, mode = parsed
    if mode == "minor":
        base = {(tonic_pc + i) % 12 for i in _MINOR_NATURAL}
        for i in _MINOR_EXTRA:
            base.add((tonic_pc + i) % 12)
        return base
    return {(tonic_pc + i) % 12 for i in _MAJOR_INTERVALS}


def _parse_key_label(key: str | None) -> tuple[int, str] | None:
    if not key or not isinstance(key, str):
        return None
    try:
        from app.services.composition_tonality import parse_key

        parsed = parse_key(key)
    except Exception:  # noqa: BLE001
        return None
    if parsed is None:
        return None
    return int(parsed.tonic_pc), str(parsed.mode)
