"""Deterministic reharmonization preview for composition.v2.

Produces a validated candidate composition without mutating projects. Audible
music still comes only from ``tracks[].events[]``; harmony spans remain metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from app.analysis_schemas import fingerprint_log_prefix
from app.composition_schemas import CompositionV2, CompositionV2HarmonyItem, midi_pitch_number
from app.harmony_schemas import (
    HarmonyChangeSummary,
    HarmonyReplaceOperation,
    HarmonySpanInput,
    PreservationAssertion,
    ReharmonizeContentPolicy,
    ReharmonizeError,
    ReharmonizeOperation,
    ReharmonizePreviewRequest,
    ReharmonizePreviewResponse,
    TrackChangeSummary,
)
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services.composition_harmony_compatibility import analyze_harmony_compatibility
from app.services.composition_harmony_timeline import apply_harmony_timeline_operation
from app.services.composition_import import midi_number_to_pitch
from app.services.composition_region_patch import (
    CompositionRegionPatchError,
    RegionTickBounds,
    reject_boundary_crossing_content,
)
from app.services.composition_timeline import CompiledTimeline, compile_timeline
from app.services.composition_tonality import ParsedKey, parse_chord_symbol, parse_key


logger = logging.getLogger(__name__)

MELODY_ROLES = frozenset({"melody", "lead"})
HARMONIC_SUPPORT_ROLES = frozenset({"bass", "harmony", "pad", "rhythm"})
DRUM_ROLES = frozenset({"drums", "percussion"})
OPT_IN_ROLES = frozenset({"countermelody", "other"})

_PC_NAMES_SHARP = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PC_NAMES_FLAT = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")


def recommend_target_track_ids(
    composition: CompositionV2,
    content_policy: ReharmonizeContentPolicy,
) -> list[str]:
    """Role-based recommendations only — never silent authorization."""
    recommended: list[str] = []
    for track in composition.tracks:
        role = str(track.role or "other")
        if role in DRUM_ROLES:
            continue
        if content_policy == "preserve_melody_adapt_harmony":
            if role in HARMONIC_SUPPORT_ROLES:
                recommended.append(track.id)
        elif content_policy == "preserve_harmony_adapt_melody":
            if role in MELODY_ROLES:
                recommended.append(track.id)
        elif content_policy == "adapt_accompaniment_only":
            if role in HARMONIC_SUPPORT_ROLES:
                recommended.append(track.id)
    return recommended


def preview_reharmonization(request: ReharmonizePreviewRequest) -> ReharmonizePreviewResponse:
    """Build a validated reharmonization candidate (stateless)."""
    composition = request.composition
    timeline = compile_timeline(composition)
    recommended = recommend_target_track_ids(composition, request.content_policy)

    try:
        start_tick, end_tick = timeline.bar_range_ticks(
            request.selection.start_bar,
            request.selection.end_bar,
        )
    except ValueError as exc:
        raise ReharmonizeError(
            "reharmonize_invalid_selection",
            details={"start_bar": request.selection.start_bar, "end_bar": request.selection.end_bar},
        ) from exc

    active_key_label = timeline.active_key(start_tick)
    active_key = parse_key(active_key_label)
    if active_key is None:
        raise ReharmonizeError(
            "reharmonize_invalid_selection",
            message="Active key at selection start is not parseable.",
            details={"active_key": active_key_label},
        )

    logger.info(
        "Reharmonization preview started",
        extra={
            "operation": request.operation,
            "engine": request.engine,
            "content_policy": request.content_policy,
            "start_bar": request.selection.start_bar,
            "end_bar": request.selection.end_bar,
            "target_count": len(request.target_track_ids),
            "instruction_len": len(request.instruction or ""),
        },
    )

    if request.engine != "deterministic":
        raise ReharmonizeError(
            "reharmonize_internal_error",
            message="AI engine must be invoked via the LLM reharmonizer wrapper.",
            http_status=500,
        )

    _validate_tonal_options(request)
    authorized = _authorize_targets(composition, request)
    _ensure_realizable_targets(composition, request, authorized)

    base_fingerprint = composition_source_fingerprint(composition)
    bounds = RegionTickBounds(
        start_bar=request.selection.start_bar,
        end_bar=request.selection.end_bar,
        start_tick=start_tick,
        end_tick=end_tick,
        bar_ticks=max(1, end_tick - start_tick),
    )

    # Crossing notes only matter on tracks that will be rewritten.
    rewrite_ids = _rewrite_track_ids(request.content_policy, authorized)
    if rewrite_ids:
        try:
            reject_boundary_crossing_content(composition, bounds, rewrite_ids)
        except CompositionRegionPatchError as exc:
            code = "reharmonize_crossing_note"
            raise ReharmonizeError(code, details={"region_code": getattr(exc, "code", None)}) from exc

    warnings: list[str] = []
    candidate = composition.model_copy(deep=True)
    harmony_changes: list[HarmonyChangeSummary] = []

    if request.content_policy != "adapt_accompaniment_only":
        if request.content_policy == "preserve_harmony_adapt_melody":
            # Harmony exact-preserved; only melody retargeting below.
            pass
        else:
            candidate, harmony_changes, transform_warnings = _transform_harmony_spans(
                candidate,
                timeline=timeline,
                start_tick=start_tick,
                end_tick=end_tick,
                operation=request.operation,
                active_key=active_key,
                tonal_context=request.tonal_context,
            )
            warnings.extend(transform_warnings)

    if request.tonal_context.allow_modulation and request.tonal_context.target_key:
        candidate = _apply_modulation_metadata(
            candidate,
            start_tick=start_tick,
            target_key=request.tonal_context.target_key,
        )

    track_changes: list[TrackChangeSummary] = []
    if rewrite_ids:
        candidate, track_changes = _realize_target_tracks(
            source=composition,
            candidate=candidate,
            track_ids=rewrite_ids,
            start_tick=start_tick,
            end_tick=end_tick,
            content_policy=request.content_policy,
            operation=request.operation,
            active_key_label=active_key_label,
        )

    _assert_audible_effect(
        request=request,
        source=composition,
        candidate=candidate,
        start_tick=start_tick,
        end_tick=end_tick,
        harmony_changes=harmony_changes,
        track_changes=track_changes,
    )

    # Full model validation via CompositionV2 round-trip.
    candidate = CompositionV2.model_validate(candidate.model_dump(mode="json"))

    preserve_ids = _preserve_track_ids(composition, request.content_policy, authorized)
    compatibility = analyze_harmony_compatibility(
        candidate,
        start_tick=start_tick,
        end_tick=end_tick,
        source_composition=composition,
        authorized_target_ids=list(authorized),
        preserve_track_ids=list(preserve_ids),
    )
    if compatibility.status == "incompatible":
        raise ReharmonizeError(
            "reharmonize_preservation_failed",
            details={
                "finding_codes": [
                    item.code for item in compatibility.findings if item.severity == "error"
                ][:12],
            },
        )

    preservation = _build_preservation_assertions(
        source=composition,
        candidate=candidate,
        start_tick=start_tick,
        end_tick=end_tick,
        content_policy=request.content_policy,
        authorized=authorized,
        allow_modulation=bool(request.tonal_context.allow_modulation),
    )
    if any(not item.satisfied for item in preservation):
        raise ReharmonizeError(
            "reharmonize_preservation_failed",
            details={"failed": [item.kind for item in preservation if not item.satisfied][:8]},
        )

    proposal_fingerprint = composition_source_fingerprint(candidate)
    logger.info(
        "Reharmonization preview complete",
        extra={
            "operation": request.operation,
            "engine": request.engine,
            "content_policy": request.content_policy,
            "start_bar": request.selection.start_bar,
            "end_bar": request.selection.end_bar,
            "target_count": len(authorized),
            "changed_span_count": len(harmony_changes),
            "changed_track_count": sum(1 for item in track_changes if item.events_changed > 0),
            "compatibility_status": compatibility.status,
            "base_fingerprint_prefix": fingerprint_log_prefix(base_fingerprint),
            "proposal_fingerprint_prefix": fingerprint_log_prefix(proposal_fingerprint),
        },
    )
    logger.debug(
        "Reharmonization preview findings",
        extra={
            "finding_code_counts": dict(compatibility.finding_code_counts),
            "warning_count": len(warnings),
        },
    )

    return ReharmonizePreviewResponse(
        base_fingerprint=base_fingerprint,
        proposal_fingerprint=proposal_fingerprint,
        composition=candidate,
        harmony_changes=harmony_changes,
        track_changes=track_changes,
        preservation=preservation,
        compatibility=compatibility,
        provider="deterministic",
        model=None,
        warnings=warnings[:32],
        recommended_target_track_ids=recommended,
        start_tick=start_tick,
        end_tick=end_tick,
        active_key=active_key_label,
    )


def _validate_tonal_options(request: ReharmonizePreviewRequest) -> None:
    ctx = request.tonal_context
    if request.operation == "tonicize_target":
        if not ctx.target_chord:
            raise ReharmonizeError("reharmonize_tonicize_required")
        parsed = parse_chord_symbol(ctx.target_chord)
        if not parsed.parseable:
            raise ReharmonizeError(
                "reharmonize_tonicize_required",
                details={"code": "unparseable_target_chord"},
            )
    if ctx.allow_modulation:
        if not ctx.target_key or parse_key(ctx.target_key) is None:
            raise ReharmonizeError("reharmonize_modulation_required")
    elif ctx.target_key:
        raise ReharmonizeError(
            "reharmonize_modulation_required",
            message="target_key requires allow_modulation=true.",
        )


def _authorize_targets(
    composition: CompositionV2,
    request: ReharmonizePreviewRequest,
) -> list[str]:
    by_id = {track.id: track for track in composition.tracks}
    if not request.target_track_ids:
        raise ReharmonizeError(
            "reharmonize_invalid_targets",
            message="target_track_ids must be explicit; use recommended IDs after review.",
        )
    authorized: list[str] = []
    for track_id in request.target_track_ids:
        track = by_id.get(track_id)
        if track is None:
            raise ReharmonizeError(
                "reharmonize_invalid_targets",
                details={"unknown_track_id": track_id},
            )
        role = str(track.role or "other")
        if role in DRUM_ROLES:
            raise ReharmonizeError(
                "reharmonize_invalid_targets",
                message="Drum/percussion tracks cannot be reharmonization targets.",
                details={"track_id": track_id, "role": role},
            )
        if role in OPT_IN_ROLES:
            # Explicit listing is the opt-in.
            authorized.append(track_id)
            continue
        if request.content_policy == "preserve_melody_adapt_harmony":
            if role not in HARMONIC_SUPPORT_ROLES | OPT_IN_ROLES:
                raise ReharmonizeError(
                    "reharmonize_invalid_targets",
                    message="Target role is not authorized for this content policy.",
                    details={"track_id": track_id, "role": role},
                )
        elif request.content_policy == "preserve_harmony_adapt_melody":
            if role not in MELODY_ROLES | OPT_IN_ROLES:
                raise ReharmonizeError(
                    "reharmonize_invalid_targets",
                    message="Only melody/lead targets are authorized for this policy.",
                    details={"track_id": track_id, "role": role},
                )
        elif request.content_policy == "adapt_accompaniment_only":
            if role not in HARMONIC_SUPPORT_ROLES | OPT_IN_ROLES:
                raise ReharmonizeError(
                    "reharmonize_invalid_targets",
                    message="Only harmonic-support targets are authorized for this policy.",
                    details={"track_id": track_id, "role": role},
                )
        authorized.append(track_id)
    return authorized


def _ensure_realizable_targets(
    composition: CompositionV2,
    request: ReharmonizePreviewRequest,
    authorized: Sequence[str],
) -> None:
    if request.content_policy == "preserve_harmony_adapt_melody":
        if not any(
            str(track.role or "") in MELODY_ROLES
            for track in composition.tracks
            if track.id in authorized
        ):
            raise ReharmonizeError("reharmonize_no_realizable_target")
        return
    # Policies that rewrite harmonic support need at least one such track.
    if request.content_policy in {"preserve_melody_adapt_harmony", "adapt_accompaniment_only"}:
        if not any(
            str(track.role or "") in HARMONIC_SUPPORT_ROLES
            for track in composition.tracks
            if track.id in authorized
        ):
            raise ReharmonizeError("reharmonize_no_realizable_target")


def _rewrite_track_ids(policy: ReharmonizeContentPolicy, authorized: Sequence[str]) -> list[str]:
    return list(authorized)


def _preserve_track_ids(
    composition: CompositionV2,
    policy: ReharmonizeContentPolicy,
    authorized: Sequence[str],
) -> list[str]:
    authorized_set = set(authorized)
    preserved: list[str] = []
    for track in composition.tracks:
        role = str(track.role or "other")
        if track.id in authorized_set:
            continue
        if policy == "preserve_melody_adapt_harmony" and role in MELODY_ROLES:
            preserved.append(track.id)
        elif policy == "preserve_harmony_adapt_melody" and role in HARMONIC_SUPPORT_ROLES:
            preserved.append(track.id)
        elif policy == "adapt_accompaniment_only" and role in MELODY_ROLES:
            preserved.append(track.id)
        elif track.id not in authorized_set:
            # All non-authorized tracks must be event-preserved.
            preserved.append(track.id)
    return preserved


def _transform_harmony_spans(
    composition: CompositionV2,
    *,
    timeline: CompiledTimeline,
    start_tick: int,
    end_tick: int,
    operation: ReharmonizeOperation,
    active_key: ParsedKey,
    tonal_context: Any,
) -> tuple[CompositionV2, list[HarmonyChangeSummary], list[str]]:
    warnings: list[str] = []
    before_by_start = {
        int(item.start_tick): item
        for item in composition.harmony
        if item.start_tick < end_tick and item.start_tick + item.duration_ticks > start_tick
    }
    replacement: list[HarmonySpanInput] = []
    changes: list[HarmonyChangeSummary] = []

    overlapping = [
        item
        for item in composition.harmony
        if item.start_tick < end_tick and item.start_tick + item.duration_ticks > start_tick
    ]
    if not overlapping:
        # Invent a simple progression covering the selection when empty.
        invented = _invent_progression(start_tick, end_tick, operation, active_key, tonal_context)
        replacement = invented
        for span in invented:
            changes.append(
                HarmonyChangeSummary(
                    start_tick=span.start_tick,
                    duration_ticks=span.duration_ticks,
                    before_chord=None,
                    after_chord=span.chord,
                )
            )
    else:
        ordered = sorted(overlapping, key=lambda item: item.start_tick)
        for index, item in enumerate(ordered):
            clipped_start = max(int(item.start_tick), start_tick)
            clipped_end = min(int(item.start_tick) + int(item.duration_ticks), end_tick)
            if clipped_end <= clipped_start:
                continue
            next_item = ordered[index + 1] if index + 1 < len(ordered) else None
            try:
                new_chord = _transform_chord_symbol(
                    item.chord,
                    operation=operation,
                    active_key=active_key,
                    next_chord=next_item.chord if next_item else None,
                    is_last=index == len(ordered) - 1,
                    tonal_context=tonal_context,
                    span_index=index,
                )
            except ReharmonizeError:
                raise
            except Exception as exc:
                raise ReharmonizeError(
                    "reharmonize_unsupported_symbol",
                    details={"stage": "transform"},
                ) from exc
            if new_chord != item.chord:
                changes.append(
                    HarmonyChangeSummary(
                        start_tick=clipped_start,
                        duration_ticks=clipped_end - clipped_start,
                        before_chord=item.chord,
                        after_chord=new_chord,
                    )
                )
            replacement.append(
                HarmonySpanInput(
                    start_tick=clipped_start,
                    duration_ticks=clipped_end - clipped_start,
                    chord=new_chord,
                )
            )

    result = apply_harmony_timeline_operation(
        composition,
        HarmonyReplaceOperation(
            start_tick=start_tick,
            duration_ticks=end_tick - start_tick,
            spans=replacement,
        ),
    )
    _ = before_by_start
    _ = timeline
    return result.composition, changes, warnings


def _invent_progression(
    start_tick: int,
    end_tick: int,
    operation: ReharmonizeOperation,
    active_key: ParsedKey,
    tonal_context: Any,
) -> list[HarmonySpanInput]:
    duration = end_tick - start_tick
    quarter = max(1, duration // 4)
    roots = _progression_roots(operation, active_key, tonal_context)
    spans: list[HarmonySpanInput] = []
    cursor = start_tick
    for index, (pc, quality) in enumerate(roots):
        span_end = end_tick if index == len(roots) - 1 else min(end_tick, cursor + quarter)
        if span_end <= cursor:
            break
        spans.append(
            HarmonySpanInput(
                start_tick=cursor,
                duration_ticks=span_end - cursor,
                chord=_spell_chord(pc, quality, key=active_key),
            )
        )
        cursor = span_end
    if not spans:
        spans.append(
            HarmonySpanInput(
                start_tick=start_tick,
                duration_ticks=duration,
                chord=_spell_chord(active_key.tonic_pc, "maj" if active_key.mode == "major" else "min", key=active_key),
            )
        )
    return spans


def _progression_roots(
    operation: ReharmonizeOperation,
    key: ParsedKey,
    tonal_context: Any,
) -> list[tuple[int, str]]:
    t = key.tonic_pc
    if operation in {"increase_tension", "use_secondary_dominants", "reharmonize"}:
        return [
            ((t + 2) % 12, "dom7"),  # V/V-ish
            ((t + 7) % 12, "dom7"),
            ((t + 0) % 12, "maj" if key.mode == "major" else "min"),
            ((t + 7) % 12, "dom7"),
        ]
    if operation == "use_modal_interchange":
        return [
            (t, "maj" if key.mode == "major" else "min"),
            ((t + 5) % 12, "min" if key.mode == "major" else "maj"),
            ((t + 8) % 12, "maj"),
            ((t + 7) % 12, "dom7"),
        ]
    if operation == "strengthen_cadence":
        return [
            ((t + 5) % 12, "maj" if key.mode == "major" else "min"),
            ((t + 7) % 12, "dom7"),
            (t, "maj" if key.mode == "major" else "min"),
            (t, "maj" if key.mode == "major" else "min"),
        ]
    if operation == "tonicize_target" and tonal_context.target_chord:
        parsed = parse_chord_symbol(tonal_context.target_chord)
        return [
            ((parsed.root_pc + 7) % 12, "dom7"),
            (parsed.root_pc, parsed.quality if parsed.quality != "unknown" else "maj"),
            ((parsed.root_pc + 7) % 12, "dom7"),
            (parsed.root_pc, parsed.quality if parsed.quality != "unknown" else "maj"),
        ]
    if operation in {"decrease_tension", "simplify_harmony", "suggest_progression"}:
        return [
            (t, "maj" if key.mode == "major" else "min"),
            ((t + 5) % 12, "maj" if key.mode == "major" else "min"),
            ((t + 7) % 12, "maj"),
            (t, "maj" if key.mode == "major" else "min"),
        ]
    return [(t, "maj" if key.mode == "major" else "min")]


def _transform_chord_symbol(
    chord: str,
    *,
    operation: ReharmonizeOperation,
    active_key: ParsedKey,
    next_chord: str | None,
    is_last: bool,
    tonal_context: Any,
    span_index: int,
) -> str:
    parsed = parse_chord_symbol(chord)
    if not parsed.parseable:
        raise ReharmonizeError(
            "reharmonize_unsupported_symbol",
            details={"stage": "parse_declared"},
        )
    if parsed.has_unknown_syntax and operation not in {"simplify_harmony", "decrease_tension"}:
        # Deterministic engines refuse to guess unknown tails for tension ops.
        if operation in {"increase_tension", "use_secondary_dominants", "reharmonize"}:
            raise ReharmonizeError(
                "reharmonize_unsupported_symbol",
                details={"stage": "unknown_suffix"},
            )

    root = parsed.root_pc
    quality = parsed.quality
    alterations: tuple[str, ...] = parsed.alterations
    bass = parsed.bass_pc

    if operation == "increase_tension":
        if is_last:
            root = (active_key.tonic_pc + 7) % 12
            quality = "dom7"
            alterations = ("b9",)
        elif quality in {"maj", "min", "sus"}:
            # Secondary dominant toward next chord or V.
            target = parse_chord_symbol(next_chord).root_pc if next_chord else (active_key.tonic_pc + 7) % 12
            if next_chord and parse_chord_symbol(next_chord).parseable:
                root = (target + 7) % 12
            else:
                root = (active_key.tonic_pc + 2) % 12
            quality = "dom7"
            alterations = ("b9",) if span_index % 2 == 0 else ("#9",)
        elif quality == "dom7":
            alterations = ("b9", "#5") if "b9" not in alterations else ("#9",)
        else:
            quality = "dom7"
            alterations = ("b9",)
    elif operation == "decrease_tension":
        quality = "min" if quality in {"min7", "min7b5", "dim", "dim7"} else "maj"
        alterations = ()
        bass = None
    elif operation == "simplify_harmony":
        quality = "min" if quality.startswith("min") or quality.startswith("dim") else "maj"
        alterations = ()
        bass = None
    elif operation == "use_secondary_dominants":
        target = parse_chord_symbol(next_chord).root_pc if next_chord and parse_chord_symbol(next_chord).parseable else (active_key.tonic_pc + 7) % 12
        root = (target + 7) % 12
        quality = "dom7"
        alterations = ()
    elif operation == "use_modal_interchange":
        if active_key.mode == "major":
            # Borrow bVI / bVII color on even spans.
            root = (active_key.tonic_pc + (8 if span_index % 2 == 0 else 10)) % 12
            quality = "maj"
        else:
            root = (active_key.tonic_pc + 3) % 12
            quality = "maj"
        alterations = ()
    elif operation == "strengthen_cadence":
        if is_last:
            root = active_key.tonic_pc
            quality = "maj" if active_key.mode == "major" else "min"
            alterations = ()
        else:
            root = (active_key.tonic_pc + 7) % 12
            quality = "dom7"
            alterations = ()
    elif operation == "tonicize_target":
        target = parse_chord_symbol(tonal_context.target_chord)
        if is_last:
            root = target.root_pc
            quality = target.quality if target.quality != "unknown" else "maj"
            alterations = ()
        else:
            root = (target.root_pc + 7) % 12
            quality = "dom7"
            alterations = ()
    elif operation in {"reharmonize", "suggest_progression"}:
        # Mild reharmonization: diatonic neighbor with optional 7th.
        options = [
            (active_key.tonic_pc, "maj" if active_key.mode == "major" else "min"),
            ((active_key.tonic_pc + 5) % 12, "maj" if active_key.mode == "major" else "min"),
            ((active_key.tonic_pc + 7) % 12, "dom7"),
            ((active_key.tonic_pc + 9) % 12, "min"),
        ]
        root, quality = options[span_index % len(options)]
        alterations = ()
    else:
        return chord

    return _spell_chord(root, quality, alterations=alterations, bass_pc=bass, key=active_key)


def _spell_chord(
    root_pc: int,
    quality: str,
    *,
    alterations: Sequence[str] = (),
    bass_pc: int | None = None,
    key: ParsedKey | None = None,
) -> str:
    prefer_flats = bool(key and ("b" in key.label or key.mode == "minor" and key.tonic_pc in {0, 5, 10, 3, 8}))
    names = _PC_NAMES_FLAT if prefer_flats else _PC_NAMES_SHARP
    root = names[root_pc % 12]
    suffix = {
        "maj": "",
        "min": "m",
        "dom7": "7",
        "maj7": "maj7",
        "min7": "m7",
        "min7b5": "m7b5",
        "dim": "dim",
        "dim7": "dim7",
        "aug": "aug",
        "sus": "sus4",
    }.get(quality, "")
    alt = "".join(f"({token})" for token in alterations)
    bass = f"/{names[bass_pc % 12]}" if bass_pc is not None else ""
    return f"{root}{suffix}{alt}{bass}"


def _apply_modulation_metadata(
    composition: CompositionV2,
    *,
    start_tick: int,
    target_key: str,
) -> CompositionV2:
    data = composition.model_dump(mode="json")
    changes = [item for item in data.get("key_changes", []) if int(item["tick"]) != start_tick]
    changes.append({"tick": start_tick, "key": target_key})
    changes.sort(key=lambda item: int(item["tick"]))
    data["key_changes"] = changes
    return CompositionV2.model_validate(data)


def _realize_target_tracks(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    track_ids: Sequence[str],
    start_tick: int,
    end_tick: int,
    content_policy: ReharmonizeContentPolicy,
    operation: ReharmonizeOperation,
    active_key_label: str,
) -> tuple[CompositionV2, list[TrackChangeSummary]]:
    harmony_index = list(candidate.harmony)
    prefer_tension = operation in {
        "increase_tension",
        "use_secondary_dominants",
        "reharmonize",
        "use_modal_interchange",
    }
    data = candidate.model_dump(mode="json")
    summaries: list[TrackChangeSummary] = []
    id_set = set(track_ids)

    for track in data["tracks"]:
        if track["id"] not in id_set:
            continue
        role = str(track.get("role") or "other")
        before_events = list(track["events"])
        new_events: list[dict[str, Any]] = []
        changed = 0
        for event in before_events:
            estart = int(event["start_tick"])
            edur = int(event["duration_ticks"])
            eend = estart + edur
            if eend <= start_tick or estart >= end_tick:
                new_events.append(event)
                continue
            # Fully inside (crossing already rejected).
            chord = _harmony_at(harmony_index, estart)
            parsed = parse_chord_symbol(chord) if chord else None
            pcs = set(parsed.pitch_classes) if parsed and parsed.parseable else set()
            if not pcs and parsed and parsed.parseable:
                pcs = {parsed.root_pc % 12, (parsed.root_pc + 4) % 12, (parsed.root_pc + 7) % 12}
            if content_policy == "preserve_harmony_adapt_melody" and role not in MELODY_ROLES:
                new_events.append(event)
                continue
            if content_policy == "adapt_accompaniment_only" and role in MELODY_ROLES:
                new_events.append(event)
                continue
            if not pcs:
                new_events.append(event)
                continue
            midi = midi_pitch_number(event["pitch"])
            new_midi = _nearest_pc_midi(midi, pcs, prefer_tension=prefer_tension and role in HARMONIC_SUPPORT_ROLES)
            if role in {"bass"} and parsed and parsed.parseable:
                expected = parsed.bass_pc if parsed.bass_pc is not None else parsed.root_pc
                new_midi = _snap_to_pc(midi, expected)
            updated = dict(event)
            updated["pitch"] = midi_number_to_pitch(new_midi, key=active_key_label)
            if updated["pitch"] != event["pitch"]:
                changed += 1
            new_events.append(updated)
        track["events"] = new_events
        summaries.append(
            TrackChangeSummary(
                track_id=track["id"],
                role=role,
                events_before=len(before_events),
                events_after=len(new_events),
                events_changed=changed,
            )
        )

    # Ensure increase_tension under preserve_melody_adapt_harmony actually changes notes.
    _ = source
    return CompositionV2.model_validate(data), summaries


def _harmony_at(spans: Sequence[CompositionV2HarmonyItem | dict[str, Any]], tick: int) -> str | None:
    for item in spans:
        start = int(item.start_tick if hasattr(item, "start_tick") else item["start_tick"])
        dur = int(item.duration_ticks if hasattr(item, "duration_ticks") else item["duration_ticks"])
        if start <= tick < start + dur:
            return str(item.chord if hasattr(item, "chord") else item["chord"])
    return None


def _nearest_pc_midi(midi: int, pcs: set[int], *, prefer_tension: bool) -> int:
    if not pcs:
        return midi
    candidates = sorted(pcs)
    if prefer_tension:
        # Bias toward upper chord tones / extensions already in pcs.
        tensionish = [pc for pc in candidates if ((pc - candidates[0]) % 12) in {1, 2, 3, 6, 8, 10}]
        pool = tensionish or candidates
    else:
        pool = candidates
    best = midi
    best_dist = 99
    for pc in pool:
        snapped = _snap_to_pc(midi, pc)
        dist = abs(snapped - midi)
        if dist < best_dist:
            best = snapped
            best_dist = dist
    # Force a change when already on a chord tone but tension preferred.
    if prefer_tension and best == midi and len(pool) > 1:
        for pc in pool:
            snapped = _snap_to_pc(midi, pc)
            if snapped != midi:
                return snapped
    return best


def _snap_to_pc(midi: int, pc: int) -> int:
    base = midi - (midi % 12)
    options = [base + (pc % 12) + offset for offset in (-12, 0, 12)]
    return min((value for value in options if 0 <= value <= 127), key=lambda value: abs(value - midi))


def _assert_audible_effect(
    *,
    request: ReharmonizePreviewRequest,
    source: CompositionV2,
    candidate: CompositionV2,
    start_tick: int,
    end_tick: int,
    harmony_changes: Sequence[HarmonyChangeSummary],
    track_changes: Sequence[TrackChangeSummary],
) -> None:
    harmony_changed = any(item.before_chord != item.after_chord for item in harmony_changes)
    tracks_changed = any(item.events_changed > 0 for item in track_changes)
    if request.content_policy == "preserve_melody_adapt_harmony":
        if request.operation == "increase_tension" and not (harmony_changed and tracks_changed):
            raise ReharmonizeError(
                "reharmonize_inaudible_success",
                message="increase_tension must change harmony and at least one targeted support track.",
            )
        if not harmony_changed and request.operation not in {"decrease_tension", "simplify_harmony"}:
            # Still require some effect for most ops.
            if not tracks_changed:
                raise ReharmonizeError("reharmonize_inaudible_success")
    elif request.content_policy == "adapt_accompaniment_only":
        if not tracks_changed:
            raise ReharmonizeError("reharmonize_inaudible_success")
        if list(source.harmony) != list(candidate.harmony):
            raise ReharmonizeError(
                "reharmonize_preservation_failed",
                message="adapt_accompaniment_only must preserve harmony spans exactly.",
            )
    elif request.content_policy == "preserve_harmony_adapt_melody":
        if list(source.harmony) != list(candidate.harmony):
            raise ReharmonizeError(
                "reharmonize_preservation_failed",
                message="preserve_harmony_adapt_melody must preserve harmony spans exactly.",
            )
        if not tracks_changed:
            raise ReharmonizeError("reharmonize_inaudible_success")
    _ = (start_tick, end_tick)


def _build_preservation_assertions(
    *,
    source: CompositionV2,
    candidate: CompositionV2,
    start_tick: int,
    end_tick: int,
    content_policy: ReharmonizeContentPolicy,
    authorized: Sequence[str],
    allow_modulation: bool,
) -> list[PreservationAssertion]:
    assertions: list[PreservationAssertion] = []
    authorized_set = set(authorized)
    source_by_id = {track.id: track for track in source.tracks}
    cand_by_id = {track.id: track for track in candidate.tracks}

    # Melody exact preservation under preserve_melody / adapt_accompaniment.
    if content_policy in {"preserve_melody_adapt_harmony", "adapt_accompaniment_only"}:
        for track in source.tracks:
            if str(track.role or "") not in MELODY_ROLES:
                continue
            same = _events_equal(track.events, cand_by_id[track.id].events)
            assertions.append(
                PreservationAssertion(
                    kind="melody_events",
                    track_id=track.id,
                    satisfied=same,
                    detail="Melody/lead events exact-preserved."
                    if same
                    else "Melody/lead events changed.",
                )
            )

    if content_policy in {"preserve_harmony_adapt_melody", "adapt_accompaniment_only"}:
        same_harmony = [
            (item.start_tick, item.duration_ticks, item.chord) for item in source.harmony
        ] == [
            (item.start_tick, item.duration_ticks, item.chord) for item in candidate.harmony
        ]
        assertions.append(
            PreservationAssertion(
                kind="harmony_spans",
                satisfied=same_harmony,
                detail="Harmony spans exact-preserved." if same_harmony else "Harmony spans changed.",
            )
        )

    outside_ok = True
    for track_id, track in source_by_id.items():
        cand = cand_by_id[track_id]
        source_outside = [
            _event_tuple(event)
            for event in track.events
            if int(event.start_tick) + int(event.duration_ticks) <= start_tick
            or int(event.start_tick) >= end_tick
        ]
        cand_outside = [
            _event_tuple(event)
            for event in cand.events
            if int(event.start_tick) + int(event.duration_ticks) <= start_tick
            or int(event.start_tick) >= end_tick
        ]
        if source_outside != cand_outside:
            outside_ok = False
            break
    assertions.append(
        PreservationAssertion(
            kind="outside_range_events",
            satisfied=outside_ok,
            detail="Outside-range events preserved." if outside_ok else "Outside-range events changed.",
        )
    )

    for track_id, track in source_by_id.items():
        if track_id in authorized_set:
            continue
        same = _events_equal(track.events, cand_by_id[track_id].events)
        assertions.append(
            PreservationAssertion(
                kind="track_events",
                track_id=track_id,
                satisfied=same,
                detail="Non-target track preserved." if same else "Non-target track changed.",
            )
        )

    key_ok = source.key == candidate.key and (
        allow_modulation
        or [
            (item.tick, item.key) for item in source.key_changes
        ]
        == [(item.tick, item.key) for item in candidate.key_changes]
    )
    assertions.append(
        PreservationAssertion(
            kind="key_metadata",
            satisfied=key_ok,
            detail="Key metadata preserved." if key_ok else "Key metadata changed without authorization.",
        )
    )

    structural_ok = (
        source.schema_version == candidate.schema_version
        and source.bar_count == candidate.bar_count
        and source.duration_ticks == candidate.duration_ticks
        and [section.id for section in source.sections] == [section.id for section in candidate.sections]
        and [track.id for track in source.tracks] == [track.id for track in candidate.tracks]
    )
    assertions.append(
        PreservationAssertion(
            kind="structural_ids",
            satisfied=structural_ok,
            detail="Structural IDs preserved." if structural_ok else "Structural identity changed.",
        )
    )
    return assertions


def _events_equal(left: Sequence[Any], right: Sequence[Any]) -> bool:
    return [_event_tuple(event) for event in left] == [_event_tuple(event) for event in right]


def _event_tuple(event: Any) -> tuple[Any, ...]:
    if isinstance(event, dict):
        return (
            event.get("type", "note"),
            event.get("id"),
            event.get("pitch"),
            int(event.get("start_tick", 0)),
            int(event.get("duration_ticks", 0)),
            int(event.get("velocity", 0) or 0),
        )
    return (
        getattr(event, "type", "note"),
        getattr(event, "id", None),
        getattr(event, "pitch", None),
        int(getattr(event, "start_tick", 0)),
        int(getattr(event, "duration_ticks", 0)),
        int(getattr(event, "velocity", 0) or 0),
    )
