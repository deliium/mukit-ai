"""Compile intents and mix-analysis observations into a bounded mix plan.

Observation ``suggested_action`` prose is never read. Bars are copied from
the locus and never invented.
"""

from __future__ import annotations

import logging
from typing import Sequence

from app.mix_plan_schemas import (
    MIX_PLAN_ANALYSIS_REPORT_ABSENT,
    MIX_PLAN_REVERB_ID,
    MIX_PLAN_SECTION_UNSPECIFIED,
    MIX_PLAN_STEREO_UNSIGNED,
    MixIntentV1,
    MixPlanAutomationPoint,
    MixPlanChange,
    MixPlanIntentCode,
    MixPlanObservationRef,
    MixPlanOp,
    MixPlanV1,
    MixPlanWarning,
)
from app.services.mix_plan.targets import master_target_document, streaming_unverified_warning

logger = logging.getLogger(__name__)

_SECTION_CODE = "section_loudness_flat"
_AUTO_OBSERVATION_CODES = frozenset(
    {
        "clipping_detected",
        "peak_hot",
        "low_headroom",
        "stereo_imbalance",
        "lf_buildup",
        "spectral_masking_proxy",
    }
)


def compile_plan(
    *,
    intent: MixIntentV1 | None,
    observations: Sequence[MixPlanObservationRef],
    active_stems: Sequence[dict],
    master_target: str,
    stem_set_id: str,
    project_id: str,
    fingerprint: str,
    duration_seconds: float,
    explicit_observation_codes: Sequence[str] | None = None,
) -> MixPlanV1:
    """Build ops already clamped to schema bounds."""
    explicit = {str(code) for code in (explicit_observation_codes or [])}
    selected_obs = _select_observations(observations, explicit=explicit, has_intent=intent is not None)
    intent_code: MixPlanIntentCode = intent.intent_code if intent else "observation_suggestion"
    warnings: list[MixPlanWarning] = []
    ops: list[MixPlanOp] = []

    if intent is not None:
        ops.extend(
            _ops_for_intent(
                intent,
                active_stems,
                selected_obs,
                duration_seconds,
                warnings,
            )
        )
    ops.extend(_ops_for_observations(selected_obs, active_stems, warnings))
    ops.extend(_master_ops(master_target, active_stems))
    if master_target == "streaming":
        warnings.append(streaming_unverified_warning())

    doc = master_target_document(master_target)
    logger.info(
        "Mix plan compiled",
        extra={
            "intent_code": intent_code,
            "op_count": len(ops),
            "observation_codes": sorted({obs.code for obs in selected_obs}),
            "warning_codes": [item.code for item in warnings],
            "master_target": doc.target_id,
            "guarantee": False,
        },
    )
    plan = MixPlanV1(
        stem_set_id=stem_set_id,
        project_id=project_id,
        master_target=doc.target_id,
        guarantee=False,
        intent_code=intent_code,
        source_stem_set_fingerprint=fingerprint or "",
        ops=ops,
        changes=[change_from_op(op) for op in ops],
        warnings=warnings,
        mutates_stems=False,
    )
    return plan


def overlay_head_before(plan: MixPlanV1, head: MixPlanV1 | None) -> tuple[MixPlanV1, int]:
    """Copy each head op's applied ``after`` into ``before`` when ``op_id`` matches."""
    if head is None:
        return plan, 0
    applied = {op.op_id: op.after for op in head.ops}
    matched = 0
    ops: list[MixPlanOp] = []
    for op in plan.ops:
        if op.op_id not in applied:
            ops.append(op)
            continue
        matched += 1
        ops.append(op.model_copy(update={"before": applied[op.op_id]}))
    return (
        plan.model_copy(update={"ops": ops, "changes": [change_from_op(op) for op in ops]}),
        matched,
    )


def change_from_op(op: MixPlanOp) -> MixPlanChange:
    if op.bus == "master":
        target = "master"
    elif op.stem_roles:
        target = ",".join(op.stem_roles)
    elif op.stem_ids:
        target = ",".join(op.stem_ids[:4])
    else:
        target = "stem"
    return MixPlanChange(
        op_id=op.op_id,
        type=op.type,
        target=target,
        unit=op.unit,
        before=op.before,
        after=op.after,
        reason=f"{op.reason_kind}:{op.reason_code}",
    )


def _select_observations(
    observations: Sequence[MixPlanObservationRef],
    *,
    explicit: set[str],
    has_intent: bool,
) -> list[MixPlanObservationRef]:
    """Phrase requests merge objective observations, never section-flat, unless asked."""
    if explicit:
        return [obs for obs in observations if obs.code in explicit]
    if has_intent:
        return [obs for obs in observations if obs.code in _AUTO_OBSERVATION_CODES]
    return [
        obs
        for obs in observations
        if obs.code in _AUTO_OBSERVATION_CODES or obs.code == _SECTION_CODE
    ]


def _ops_for_intent(
    intent: MixIntentV1,
    stems: Sequence[dict],
    observations: Sequence[MixPlanObservationRef],
    duration_seconds: float,
    warnings: list[MixPlanWarning],
) -> list[MixPlanOp]:
    if intent.intent_code == "reduce_dominance":
        role = intent.roles[0]
        return [_gain_op(role, stems, after=-4.5, reason_code=intent.intent_code, kind="intent")]
    if intent.intent_code == "more_space":
        role = intent.roles[0]
        ops = [
            _gain_op(role, stems, after=1.5, reason_code=intent.intent_code, kind="intent"),
            _send_op(role, stems, after=0.25, reason_code=intent.intent_code),
        ]
        masking = next((obs for obs in observations if obs.code == "spectral_masking_proxy"), None)
        if masking is not None:
            others = [s for s in stems if str(s.get("role")) != role]
            for stem in others:
                if _stem_in_locus(stem, masking):
                    ops.append(_eq_dip(stem, masking, reason_code="spectral_masking_proxy", kind="observation"))
                    break
        return ops
    if intent.intent_code == "wider_climax":
        window = _window_from_observations(observations)
        if window is None:
            duration = max(duration_seconds, 0.1)
            window = (round(duration * 2.0 / 3.0, 3), round(duration, 3))
            warnings.append(
                MixPlanWarning(
                    code=MIX_PLAN_SECTION_UNSPECIFIED,
                    message="No climax window was on the report. Width automation uses the last third of the stem.",
                )
            )
        start, end = window
        return [
            MixPlanOp(
                op_id="op-automation-master-width",
                type="automation",
                bus="master",
                unit="pan",
                before=0.0,
                after=0.45,
                automation=[
                    MixPlanAutomationPoint(t_seconds=start, value=0.0),
                    MixPlanAutomationPoint(t_seconds=max(end, start), value=0.45),
                ],
                start_seconds=start,
                end_seconds=max(end, start),
                reason_kind="intent",
                reason_code="wider_climax",
            )
        ]
    if intent.intent_code == "reduce_lf_masking":
        locus = next(
            (obs for obs in observations if obs.code in {"spectral_masking_proxy", "lf_buildup"}),
            None,
        )
        if locus is None and not observations:
            warnings.append(
                MixPlanWarning(
                    code=MIX_PLAN_ANALYSIS_REPORT_ABSENT,
                    message="No mix-analysis report was attached. Low-shelf cuts apply to non-bass stems.",
                )
            )
        targets = _non_bass(stems, locus)
        freq = 120.0
        if locus and locus.freq_hz_low is not None:
            freq = float(locus.freq_hz_low)
        ops = []
        for stem in targets:
            ops.append(
                _filter_op(
                    stem,
                    cutoff=min(400.0, max(40.0, freq)),
                    reason_code="reduce_lf_masking",
                    kind="intent",
                    locus=locus,
                )
            )
        return ops
    return []


def _pan_toward_center(measurement_value: float) -> float:
    """Positive L-R balance (left-heavy) pans right. Magnitude stays within 0.35."""
    magnitude = min(0.35, abs(measurement_value) / 24.0)
    signed = magnitude if measurement_value > 0 else -magnitude
    return round(signed, 4)


def _ops_for_observations(
    observations: Sequence[MixPlanObservationRef],
    stems: Sequence[dict],
    warnings: list[MixPlanWarning],
) -> list[MixPlanOp]:
    ops: list[MixPlanOp] = []
    for obs in observations:
        matched = [s for s in stems if _stem_in_locus(s, obs)] or list(stems)
        if obs.code in {"clipping_detected", "peak_hot", "low_headroom"}:
            for stem in matched:
                ops.append(
                    _gain_op(
                        str(stem.get("role") or "other"),
                        [stem],
                        after=-3.0 if obs.code != "clipping_detected" else -6.0,
                        reason_code=obs.code,
                        kind="observation",
                        locus=obs,
                        op_suffix=str(stem.get("id") or stem.get("role")),
                    )
                )
            if obs.code == "clipping_detected":
                for stem in matched:
                    ops.append(_compressor_op(stem, ratio=2.0, reason_code=obs.code, locus=obs))
        elif obs.code == "stereo_imbalance":
            if obs.measurement_value is None or obs.measurement_value == 0:
                warnings.append(
                    MixPlanWarning(
                        code=MIX_PLAN_STEREO_UNSIGNED,
                        message="Stereo imbalance has no signed balance, so no pan change was added.",
                    )
                )
                logger.warning(
                    "Mix plan stereo observation has no signed balance",
                    extra={"warning_code": MIX_PLAN_STEREO_UNSIGNED, "op_count": len(ops)},
                )
                continue
            after = _pan_toward_center(obs.measurement_value)
            for stem in matched:
                ops.append(
                    MixPlanOp(
                        op_id=f"op-pan-{_slug(stem)}",
                        type="pan",
                        bus="stem",
                        stem_ids=_ids(stem),
                        stem_roles=_roles(stem),
                        source_track_ids=_tracks(stem, obs),
                        unit="pan",
                        before=0.0,
                        after=after,
                        reason_kind="observation",
                        reason_code=obs.code,
                        **_locus_kwargs(obs),
                    )
                )
        elif obs.code == "lf_buildup":
            for stem in _non_bass(matched, obs):
                ops.append(
                    _filter_op(stem, cutoff=90.0, reason_code=obs.code, kind="observation", locus=obs)
                )
        elif obs.code == "spectral_masking_proxy":
            roles = list(obs.stem_roles)
            target_role = roles[1] if len(roles) >= 2 else (roles[0] if roles else None)
            chosen = [
                s
                for s in matched
                if target_role is None or str(s.get("role")) == target_role
            ]
            # Never cut every role in the pair with the same EQ.
            if len(roles) >= 2:
                chosen = [s for s in chosen if str(s.get("role")) == roles[1]]
            for stem in chosen[:1]:
                ops.append(_eq_dip(stem, obs, reason_code=obs.code, kind="observation"))
        elif obs.code == _SECTION_CODE:
            for stem in matched[:1]:
                ops.append(
                    _gain_op(
                        str(stem.get("role") or "other"),
                        [stem],
                        after=1.5,
                        reason_code=obs.code,
                        kind="observation",
                        locus=obs,
                        op_suffix=str(stem.get("id") or "section"),
                    )
                )
    return ops


def _master_ops(master_target: str, stems: Sequence[dict]) -> list[MixPlanOp]:
    if master_target == "dynamic":
        return [_master_compressor(1.4, "dynamic")]
    if master_target == "streaming":
        return [_master_compressor(3.5, "streaming")]
    if master_target == "cinematic":
        return [
            MixPlanOp(
                op_id="op-send-master",
                type="send",
                bus="master",
                unit="send",
                before=0.0,
                after=0.28,
                reason_kind="master_target",
                reason_code="cinematic",
            )
        ]
    ops = [_master_compressor(2.0, "demo")]
    for stem in stems:
        if str(stem.get("role")) == "bass":
            continue
        ops.append(
            _filter_op(
                stem,
                cutoff=80.0,
                reason_code="demo",
                kind="master_target",
                locus=None,
            )
        )
    return ops


def _master_compressor(ratio: float, code: str) -> MixPlanOp:
    return MixPlanOp(
        op_id=f"op-compressor-master-{code}",
        type="compressor",
        bus="master",
        unit="ratio",
        before=1.0,
        after=ratio,
        reason_kind="master_target",
        reason_code=code,
    )


def _gain_op(
    role: str,
    stems: Sequence[dict],
    *,
    after: float,
    reason_code: str,
    kind: str,
    locus: MixPlanObservationRef | None = None,
    op_suffix: str | None = None,
) -> MixPlanOp:
    chosen = [s for s in stems if str(s.get("role")) == role] or list(stems)
    stem = chosen[0]
    suffix = op_suffix or role
    return MixPlanOp(
        op_id=f"op-gain-{_slug_text(suffix)}",
        type="gain",
        bus="stem",
        stem_ids=_ids(stem),
        stem_roles=_roles(stem) or [role],
        source_track_ids=_tracks(stem, locus),
        unit="dB",
        before=0.0,
        after=after,
        reason_kind=kind,  # type: ignore[arg-type]
        reason_code=reason_code,
        **_locus_kwargs(locus),
    )


def _send_op(role: str, stems: Sequence[dict], *, after: float, reason_code: str) -> MixPlanOp:
    chosen = [s for s in stems if str(s.get("role")) == role]
    stem = chosen[0] if chosen else {"id": role, "role": role, "track_ids": []}
    return MixPlanOp(
        op_id=f"op-send-{role}",
        type="send",
        bus="stem",
        stem_ids=_ids(stem),
        stem_roles=[role],
        source_track_ids=_tracks(stem, None),
        unit="send",
        before=0.0,
        after=after,
        reason_kind="intent",
        reason_code=reason_code,
        reverb_id=MIX_PLAN_REVERB_ID,
    )


def _eq_dip(stem: dict, locus: MixPlanObservationRef, *, reason_code: str, kind: str) -> MixPlanOp:
    low = locus.freq_hz_low
    high = locus.freq_hz_high
    if low is not None and high is not None:
        freq = (float(low) + float(high)) / 2.0
    elif low is not None:
        freq = float(low)
    else:
        freq = 250.0
    return MixPlanOp(
        op_id=f"op-eq-{_slug(stem)}",
        type="eq",
        bus="stem",
        stem_ids=_ids(stem),
        stem_roles=_roles(stem),
        source_track_ids=_tracks(stem, locus),
        unit="dB",
        before=0.0,
        after=-3.0,
        freq_hz=max(20.0, min(16000.0, freq)),
        q=1.0,
        reason_kind=kind,  # type: ignore[arg-type]
        reason_code=reason_code,
        **_locus_kwargs(locus),
    )


def _filter_op(
    stem: dict,
    *,
    cutoff: float,
    reason_code: str,
    kind: str,
    locus: MixPlanObservationRef | None,
) -> MixPlanOp:
    return MixPlanOp(
        op_id=f"op-filter-{_slug(stem)}-{_slug_text(reason_code)}",
        type="filter",
        bus="stem",
        stem_ids=_ids(stem),
        stem_roles=_roles(stem),
        source_track_ids=_tracks(stem, locus),
        unit="Hz",
        before=0.0,
        after=cutoff,
        freq_hz=cutoff,
        reason_kind=kind,  # type: ignore[arg-type]
        reason_code=reason_code,
        **_locus_kwargs(locus),
    )


def _compressor_op(
    stem: dict, *, ratio: float, reason_code: str, locus: MixPlanObservationRef
) -> MixPlanOp:
    return MixPlanOp(
        op_id=f"op-compressor-{_slug(stem)}",
        type="compressor",
        bus="stem",
        stem_ids=_ids(stem),
        stem_roles=_roles(stem),
        source_track_ids=_tracks(stem, locus),
        unit="ratio",
        before=1.0,
        after=ratio,
        reason_kind="observation",
        reason_code=reason_code,
        **_locus_kwargs(locus),
    )


def _window_from_observations(
    observations: Sequence[MixPlanObservationRef],
) -> tuple[float, float] | None:
    for obs in observations:
        if obs.start_seconds is not None and obs.end_seconds is not None:
            return float(obs.start_seconds), float(obs.end_seconds)
    return None


def _non_bass(
    stems: Sequence[dict], locus: MixPlanObservationRef | None
) -> list[dict]:
    pool = [s for s in stems if _stem_in_locus(s, locus)] if locus else list(stems)
    if locus and not any(_stem_in_locus(s, locus) for s in stems):
        pool = list(stems)
    return [s for s in pool if str(s.get("role")) != "bass"]


def _stem_in_locus(stem: dict, locus: MixPlanObservationRef | None) -> bool:
    if locus is None:
        return True
    stem_id = str(stem.get("id") or "")
    role = str(stem.get("role") or "")
    if locus.stem_ids and stem_id in locus.stem_ids:
        return True
    if locus.stem_roles and role in locus.stem_roles:
        return True
    if not locus.stem_ids and not locus.stem_roles:
        return True
    return False


def _ids(stem: dict) -> list[str]:
    stem_id = str(stem.get("id") or "").strip()
    return [stem_id] if stem_id else []


def _roles(stem: dict) -> list[str]:
    role = str(stem.get("role") or "").strip()
    return [role] if role else []


def _tracks(stem: dict, locus: MixPlanObservationRef | None) -> list[str]:
    if locus and locus.source_track_ids:
        return list(locus.source_track_ids)
    raw = stem.get("track_ids") or []
    return [str(item) for item in raw if str(item).strip()]


def _locus_kwargs(locus: MixPlanObservationRef | None) -> dict:
    if locus is None:
        return {}
    return {
        "start_seconds": locus.start_seconds,
        "end_seconds": locus.end_seconds,
        "start_bar": locus.start_bar,
        "end_bar": locus.end_bar,
    }


def _slug(stem: dict) -> str:
    return _slug_text(str(stem.get("role") or stem.get("id") or "stem"))


def _slug_text(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value.lower())
    return cleaned[:40] or "stem"
