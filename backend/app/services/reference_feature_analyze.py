"""Deterministic reference feature analysis (read-only V2 → ``reference.features.v1``).

Never mutates the reference composition. Never writes ``DATASET_ROOT``.
Never stores note events, motif pitch sequences, analysis reports, or embedding
vectors in the report — only abstract summaries / bands / histograms.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any

from app.composition_schemas import CompositionV2
from app.embeddings.errors import EmbeddingScopeError, ReferenceResolveError
from app.embeddings.features import (
    CONCURRENT_BINS,
    CONTOUR_DIMS,
    DENSITY_DIMS,
    DUR_BINS,
    INTERVAL_BINS,
    ONSET_BINS,
    PC_DIMS,
    RANGE_DIMS,
    ROLE_DIMS,
    SECTION_TYPE_DIMS,
    SECTION_TYPE_ORDER,
    TRACK_COUNT_DIMS,
    extract_symbolic_features_v1,
)
from app.embeddings.schemas import (
    CompositionEmbedScope,
    EmbedScopeComposition,
    StyleReferenceRequest,
    embed_scope_digest,
)
from app.embeddings.settings import EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
from app.reference_feature_schemas import (
    DimensionId,
    ImportOrigin,
    PreferenceBand,
    ReferenceFeatureAnalyzeRequest,
    ReferenceFeatureBands,
    ReferenceFeatureDimensionPayload,
    ReferenceFeatureError,
    ReferenceFeatureEvidence,
    ReferenceFeaturesV1,
    ReferenceFeatureSource,
    ReferenceFeatureUnavailableItem,
    ReferenceFeatureWarningCode,
    dimension_ui_label,
    normalize_requested_dimensions,
)
from app.reference_feature_settings import load_reference_feature_settings
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services.composition_style_conditioning import load_style_reference_composition

logger = logging.getLogger(__name__)

_PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_OFF_RANGE = PC_DIMS
_OFF_DUR = _OFF_RANGE + RANGE_DIMS
_OFF_ONSET = _OFF_DUR + DUR_BINS
_OFF_DENSITY = _OFF_ONSET + ONSET_BINS
_OFF_INTERVAL = _OFF_DENSITY + DENSITY_DIMS
_OFF_CONTOUR = _OFF_INTERVAL + INTERVAL_BINS
_OFF_CONCURRENT = _OFF_CONTOUR + CONTOUR_DIMS
_OFF_ROLE = _OFF_CONCURRENT + CONCURRENT_BINS
_OFF_TRACK = _OFF_ROLE + ROLE_DIMS
_OFF_FORM = _OFF_TRACK + TRACK_COUNT_DIMS
_OFF_SECTION = _OFF_FORM + 1

_ANTI_MELODY_LINE = (
    "Use these abstract properties only; do not copy melodies or note sequences "
    "from the reference."
)


def analyze_reference_features(
    request: ReferenceFeatureAnalyzeRequest,
    *,
    enforce_rights: bool = True,
) -> ReferenceFeaturesV1:
    """Analyze a musical reference into a derived feature report (no V2 writeback).

    ``enforce_rights`` defaults on for HTTP analyze. Internal preserve/borrow
    assembly over the working composition may pass ``False`` and gate project
    bindings separately.
    """
    started = time.perf_counter()
    settings = load_reference_feature_settings()
    requested = normalize_requested_dimensions(
        request.requested_dimensions,
        default_all=True,
    )

    style_ref = StyleReferenceRequest(
        project_id=request.project_id,
        revision_id=request.revision_id,
        composition=request.composition,
        scope=request.scope,
        expected_fingerprint=request.expected_fingerprint,
    )
    try:
        composition = load_style_reference_composition(style_ref)
    except ReferenceResolveError as exc:
        code = (
            "reference_feature_fingerprint_mismatch"
            if exc.code == "reference_fingerprint_mismatch"
            else "reference_feature_not_found"
        )
        raise ReferenceFeatureError(
            code,  # type: ignore[arg-type]
            http_status=404 if code == "reference_feature_not_found" else 422,
            details=getattr(exc, "details", {}) or {},
        ) from exc

    if enforce_rights:
        from app.services.reference_rights_gate import assert_reference_rights_allowed

        assert_reference_rights_allowed(
            project_id=request.project_id,
            revision_id=request.revision_id,
            rights=request.rights,
        )

    fingerprint = composition_source_fingerprint(composition)
    if (
        request.expected_fingerprint is not None
        and request.expected_fingerprint != fingerprint
    ):
        raise ReferenceFeatureError(
            "reference_feature_fingerprint_mismatch",
            details={
                "expected_prefix": request.expected_fingerprint[
                    :EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
                ],
                "actual_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            },
        )

    source = _build_source(
        request,
        fingerprint=fingerprint,
        import_origin=request.import_origin,
    )
    scope_digest = embed_scope_digest(request.scope)

    try:
        features, window = extract_symbolic_features_v1(composition, request.scope)
    except EmbeddingScopeError as exc:
        raise ReferenceFeatureError(
            "reference_feature_scope_empty",
            details={
                "cause": getattr(exc, "code", "embed_empty_scope"),
                "scope_kind": getattr(request.scope, "kind", None),
            },
        ) from exc

    shared = _extract_shared_stats(composition, features, window)
    dimensions: dict[DimensionId, ReferenceFeatureDimensionPayload] = {}
    unavailable: list[ReferenceFeatureUnavailableItem] = []
    warning_codes: list[ReferenceFeatureWarningCode] = []

    for dim in requested:
        payload = _extract_dimension(
            dim,
            composition=composition,
            features=features,
            shared=shared,
            summary_max_chars=settings.summary_max_chars,
        )
        dimensions[dim] = payload
        if payload.status == "unavailable":
            code = payload.warning_codes[0] if payload.warning_codes else "reference_feature_degraded"
            unavailable.append(
                ReferenceFeatureUnavailableItem(
                    dimension=dim,
                    code=str(code),
                    message=payload.summary[:400],
                )
            )
        for code in payload.warning_codes:
            if code not in warning_codes:
                warning_codes.append(code)

    # Affinity only when compare_to is provided (Task 5).
    embedding_affinity = None
    if request.compare_to is None:
        if "reference_feature_compare_omitted" not in warning_codes:
            warning_codes.append("reference_feature_compare_omitted")
    else:
        from app.services.reference_feature_affinity import (
            compute_reference_feature_affinity,
        )

        embedding_affinity, affinity_warnings = compute_reference_feature_affinity(
            reference_composition=composition,
            reference_scope=request.scope,
            compare_to=request.compare_to,
            requested_dimensions=requested,
        )
        for code in affinity_warnings:
            if code not in warning_codes:
                warning_codes.append(code)  # type: ignore[arg-type]

    report = ReferenceFeaturesV1(
        source=source,
        scope=request.scope,
        scope_digest=scope_digest,
        requested_dimensions=requested,
        dimensions=dimensions,
        unavailable=unavailable,
        embedding_affinity=embedding_affinity,
        warning_codes=warning_codes,
    )

    duration_ms = int((time.perf_counter() - started) * 1000)
    status_counts = Counter(p.status for p in dimensions.values())
    logger.info(
        "Reference features analyzed",
        extra={
            "fingerprint_prefix": fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN],
            "scope_kind": getattr(request.scope, "kind", None),
            "requested_count": len(requested),
            "ok_count": status_counts.get("ok", 0),
            "degraded_count": status_counts.get("degraded", 0),
            "unavailable_count": status_counts.get("unavailable", 0),
            "note_count": shared.get("note_count", 0),
            "duration_ms": duration_ms,
            "has_compare_to": request.compare_to is not None,
        },
    )
    for dim, payload in dimensions.items():
        logger.debug(
            "Reference feature dimension status",
            extra={
                "dimension": dim,
                "status": payload.status,
                "warning_count": len(payload.warning_codes),
                "summary_chars": len(payload.summary),
            },
        )
    return report


def analyze_reference_features_from_composition(
    composition: CompositionV2,
    *,
    scope: CompositionEmbedScope | None = None,
    requested_dimensions: list[DimensionId] | None = None,
    project_id: str | None = None,
    revision_id: str | None = None,
    import_origin: ImportOrigin | None = None,
) -> ReferenceFeaturesV1:
    """In-process analyze helper for generate/develop conditioning (no HTTP)."""
    dump = composition.model_dump(mode="json")
    request = ReferenceFeatureAnalyzeRequest(
        project_id=project_id,
        revision_id=revision_id,
        composition=dump if project_id is None else None,
        scope=scope or EmbedScopeComposition(),
        requested_dimensions=requested_dimensions,
        import_origin=import_origin,
    )
    # When project_id is set without composition, loader uses store; for in-process
    # path always pass inline to avoid DB round-trip on already-loaded V2.
    if project_id is not None:
        request = ReferenceFeatureAnalyzeRequest(
            project_id=project_id,
            revision_id=revision_id,
            composition=dump,
            scope=scope or EmbedScopeComposition(),
            requested_dimensions=requested_dimensions,
            import_origin=import_origin,
        )
    # In-process conditioning helper: callers gate named project sources separately.
    return analyze_reference_features(request, enforce_rights=False)


def _build_source(
    request: ReferenceFeatureAnalyzeRequest,
    *,
    fingerprint: str,
    import_origin: ImportOrigin | None,
) -> ReferenceFeatureSource:
    if request.revision_id and request.project_id:
        kind = "revision"
    elif request.project_id and request.composition is None:
        kind = "project"
    else:
        kind = "inline"
    return ReferenceFeatureSource(
        kind=kind,  # type: ignore[arg-type]
        project_id=request.project_id,
        revision_id=request.revision_id,
        source_fingerprint=fingerprint,
        import_origin=import_origin or ("unknown" if kind == "inline" else None),
    )


def _scalar_to_band(value: float, *, low: float, high: float) -> PreferenceBand:
    if value <= low * 0.5:
        return "very_low"
    if value <= low:
        return "low"
    if value < high:
        return "moderate"
    if value < high + (1.0 - high) * 0.5:
        return "high"
    return "very_high"


def _extract_shared_stats(
    composition: CompositionV2,
    features: list[float],
    window: Any,
) -> dict[str, Any]:
    midi_min = features[_OFF_RANGE]
    midi_max = features[_OFF_RANGE + 1]
    midi_mean = features[_OFF_RANGE + 2]
    density = features[_OFF_DENSITY]
    interval = features[_OFF_INTERVAL : _OFF_INTERVAL + INTERVAL_BINS]
    onset = features[_OFF_ONSET : _OFF_ONSET + ONSET_BINS]
    contour = features[_OFF_CONTOUR : _OFF_CONTOUR + CONTOUR_DIMS]
    concurrent = features[_OFF_CONCURRENT : _OFF_CONCURRENT + CONCURRENT_BINS]
    roles = features[_OFF_ROLE : _OFF_ROLE + ROLE_DIMS]
    track_count_norm = features[_OFF_TRACK]
    section_vec = features[_OFF_SECTION : _OFF_SECTION + SECTION_TYPE_DIMS]
    pc = features[0:PC_DIMS]
    offbeat = sum(onset[i] for i in range(ONSET_BINS) if i not in {0, ONSET_BINS // 2})
    concurrent_peak = max(concurrent) if concurrent else 0.0

    instruments: list[str] = []
    for track in composition.tracks:
        label = (track.instrument or track.name or track.role or "").strip()
        if label:
            instruments.append(label[:80])

    section_counts: Counter[str] = Counter()
    for section in composition.sections or []:
        stype = str(getattr(section, "type", None) or "other").strip().lower() or "other"
        if stype not in SECTION_TYPE_ORDER:
            stype = "other"
        section_counts[stype] += 1
    if not section_counts:
        for idx, weight in enumerate(section_vec):
            if weight > 0.01:
                section_counts[SECTION_TYPE_ORDER[idx]] += weight

    ranked_pc = sorted(enumerate(pc), key=lambda item: item[1], reverse=True)
    chord_vocab = [_PC_NAMES[idx] for idx, weight in ranked_pc[:8] if weight > 0.02]

    harmony_spans = list(composition.harmony or [])
    motif_count = len(composition.motifs or [])

    velocities: list[float] = []
    has_expression = False
    note_count = 0
    for track in composition.tracks:
        for event in track.events or []:
            note_count += 1
            vel = getattr(event, "velocity", None)
            if isinstance(vel, (int, float)):
                velocities.append(float(vel) / 127.0)
            if getattr(event, "expression", None) is not None:
                has_expression = True

    return {
        "midi_min": midi_min,
        "midi_max": midi_max,
        "midi_mean": midi_mean,
        "range_semitones": max(0.0, (midi_max - midi_min) * 127.0),
        "density": density,
        "offbeat": offbeat,
        "concurrent_peak": concurrent_peak,
        "track_count_norm": track_count_norm,
        "interval": list(interval),
        "onset": list(onset),
        "contour": list(contour),
        "roles": list(roles),
        "instruments": instruments[:16],
        "section_counts": dict(section_counts),
        "chord_vocab": chord_vocab,
        "harmony_span_count": len(harmony_spans),
        "motif_count": motif_count,
        "velocities": velocities,
        "has_expression": has_expression,
        "note_count": note_count,
        "track_count": len(composition.tracks),
        "section_count": len(composition.sections or []),
        "window_scope_kind": getattr(window, "scope_kind", None),
        "window_start_bar": getattr(window, "start_bar", None),
        "window_end_bar": getattr(window, "end_bar_inclusive", None),
    }


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)] + "..."


def _payload(
    *,
    status: str,
    summary: str,
    soft_fragment: str,
    bands: ReferenceFeatureBands | None,
    evidence: ReferenceFeatureEvidence,
    warning_codes: list[ReferenceFeatureWarningCode],
    summary_max_chars: int,
) -> ReferenceFeatureDimensionPayload:
    return ReferenceFeatureDimensionPayload(
        status=status,  # type: ignore[arg-type]
        summary=_clip(summary, summary_max_chars),
        soft_fragment=_clip(soft_fragment, summary_max_chars),
        bands=bands,
        evidence=evidence,
        warning_codes=warning_codes,
    )


def _extract_dimension(
    dim: DimensionId,
    *,
    composition: CompositionV2,
    features: list[float],
    shared: dict[str, Any],
    summary_max_chars: int,
) -> ReferenceFeatureDimensionPayload:
    evidence = ReferenceFeatureEvidence(
        note_count=int(shared["note_count"]),
        track_count=int(shared["track_count"]),
        section_count=int(shared["section_count"]),
        chord_span_count=int(shared["harmony_span_count"]),
        motif_count=int(shared["motif_count"]),
        has_velocity=bool(shared["velocities"]),
        has_expression=bool(shared["has_expression"]),
    )
    label = dimension_ui_label(dim)

    if dim == "harmony":
        return _dim_harmony(shared, evidence, label, summary_max_chars)
    if dim == "harmonic_rhythm":
        return _dim_harmonic_rhythm(shared, evidence, label, summary_max_chars)
    if dim == "rhythm":
        return _dim_rhythm(shared, evidence, label, summary_max_chars)
    if dim == "melodic_contour":
        return _dim_melodic_contour(shared, evidence, label, summary_max_chars)
    if dim == "texture":
        return _dim_texture(shared, evidence, label, summary_max_chars)
    if dim == "instrumentation":
        return _dim_instrumentation(shared, evidence, label, summary_max_chars)
    if dim == "density":
        return _dim_density(shared, evidence, label, summary_max_chars)
    if dim == "dynamics":
        return _dim_dynamics(shared, evidence, label, summary_max_chars)
    if dim == "form":
        return _dim_form(shared, evidence, label, summary_max_chars)
    if dim == "tension_curve":
        return _dim_tension(shared, evidence, label, summary_max_chars)
    if dim == "motif_characteristics":
        return _dim_motifs(composition, shared, evidence, label, summary_max_chars)
    if dim == "performance_characteristics":
        return _dim_performance(shared, evidence, label, summary_max_chars)

    return _payload(
        status="unavailable",
        summary=f"{label} is not supported.",
        soft_fragment="",
        bands=None,
        evidence=evidence,
        warning_codes=["reference_feature_degraded"],
        summary_max_chars=summary_max_chars,
    )


def _dim_harmony(shared, evidence, label, max_chars):
    spans = shared["harmony_span_count"]
    vocab = shared["chord_vocab"]
    if spans <= 0 and not vocab:
        return _payload(
            status="unavailable",
            summary=f"{label}: no chord or pitch-class evidence in scope.",
            soft_fragment="",
            bands=None,
            evidence=evidence,
            warning_codes=["reference_feature_insufficient_harmony"],
            summary_max_chars=max_chars,
        )
    warnings: list[ReferenceFeatureWarningCode] = []
    status = "ok"
    if spans <= 0:
        status = "degraded"
        warnings.append("reference_feature_insufficient_harmony")
    complexity = _scalar_to_band(min(1.0, spans / 8.0), low=0.2, high=0.55)
    bands = ReferenceFeatureBands(
        harmonic_complexity_band=complexity,
        top_chord_labels=list(vocab)[:8],
    )
    summary = (
        f"{label}: complexity={complexity}; "
        f"top pitch-class buckets={', '.join(vocab[:4]) or 'n/a'}; "
        f"declared_chord_spans={spans}."
    )
    soft = (
        f"[ref:{label}] harmonic complexity {complexity}; "
        f"prefer pitch-class colors {', '.join(vocab[:4]) or 'balanced'}."
    )
    return _payload(
        status=status,
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=warnings,
        summary_max_chars=max_chars,
    )


def _dim_harmonic_rhythm(shared, evidence, label, max_chars):
    spans = shared["harmony_span_count"]
    if spans <= 0:
        return _payload(
            status="unavailable",
            summary=f"{label}: no declared harmony spans for change-rate.",
            soft_fragment="",
            bands=None,
            evidence=evidence,
            warning_codes=["reference_feature_insufficient_harmony"],
            summary_max_chars=max_chars,
        )
    rate_band = _scalar_to_band(min(1.0, spans / 12.0), low=0.2, high=0.55)
    bands = ReferenceFeatureBands(chord_change_rate_band=rate_band)
    summary = f"{label}: chord-change rate band={rate_band} from {spans} spans."
    soft = f"[ref:{label}] prefer chord-change rate {rate_band}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_rhythm(shared, evidence, label, max_chars):
    sync = _scalar_to_band(shared["offbeat"], low=0.25, high=0.55)
    dens = _scalar_to_band(shared["density"], low=0.2, high=0.55)
    bands = ReferenceFeatureBands(
        syncopation_band=sync,
        density_band=dens,
        onset_histogram_bins=[round(float(v), 4) for v in shared["onset"][:8]],
    )
    summary = f"{label}: syncopation={sync}; density_band={dens} (onset histogram only)."
    soft = f"[ref:{label}] syncopation {sync}; rhythmic feel density {dens}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_melodic_contour(shared, evidence, label, max_chars):
    # Guard: histograms only — never event lists.
    contour = shared["contour"]
    up, down, same = (contour + [0.0, 0.0, 0.0])[:3]
    interval_bins = [round(float(v), 4) for v in shared["interval"][:25]]
    range_band = _scalar_to_band(
        min(1.0, shared["range_semitones"] / 36.0), low=0.25, high=0.65
    )
    bands = ReferenceFeatureBands(
        range_semitones_band=range_band,
        interval_histogram_bins=interval_bins,
    )
    summary = (
        f"{label}: contour up/down/same="
        f"{round(up, 3)}/{round(down, 3)}/{round(same, 3)}; "
        f"range_band={range_band} (interval histogram only; no pitches)."
    )
    soft = (
        f"[ref:{label}] contour tendency up={round(up, 2)} down={round(down, 2)}; "
        f"range {range_band}."
    )
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_texture(shared, evidence, label, max_chars):
    warnings: list[ReferenceFeatureWarningCode] = []
    status = "ok"
    if shared["track_count"] <= 1 or shared["concurrent_peak"] < 0.15:
        status = "degraded"
        warnings.append("reference_feature_monophonic_texture")
    arr = _scalar_to_band(shared["concurrent_peak"], low=0.2, high=0.55)
    bands = ReferenceFeatureBands(arrangement_density_band=arr)
    summary = (
        f"{label}: concurrent-voice band={arr}; "
        f"tracks={shared['track_count']}."
    )
    soft = f"[ref:{label}] orchestration texture {arr}; track breadth {shared['track_count']}."
    return _payload(
        status=status,
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=warnings,
        summary_max_chars=max_chars,
    )


def _dim_instrumentation(shared, evidence, label, max_chars):
    instruments = list(shared["instruments"])
    if not instruments:
        return _payload(
            status="degraded",
            summary=f"{label}: no instrument labels; roles only.",
            soft_fragment=f"[ref:{label}] keep instrumentation sparse/unspecified.",
            bands=ReferenceFeatureBands(top_instruments=[]),
            evidence=evidence,
            warning_codes=["reference_feature_degraded"],
            summary_max_chars=max_chars,
        )
    bands = ReferenceFeatureBands(top_instruments=instruments[:8])
    summary = f"{label}: preferred instruments={', '.join(instruments[:6])}."
    soft = f"[ref:{label}] instrumentation lean toward {', '.join(instruments[:4])}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_density(shared, evidence, label, max_chars):
    dens = _scalar_to_band(shared["density"], low=0.2, high=0.55)
    arr = _scalar_to_band(shared["concurrent_peak"], low=0.2, high=0.55)
    bands = ReferenceFeatureBands(density_band=dens, arrangement_density_band=arr)
    summary = f"{label}: notes-per-bar band={dens}; arrangement density={arr}."
    soft = f"[ref:{label}] rhythmic density {dens}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_dynamics(shared, evidence, label, max_chars):
    velocities = shared["velocities"]
    if not velocities:
        return _payload(
            status="unavailable",
            summary=f"{label}: no velocity/expression evidence.",
            soft_fragment="",
            bands=None,
            evidence=evidence,
            warning_codes=["reference_feature_insufficient_dynamics"],
            summary_max_chars=max_chars,
        )
    mean_v = sum(velocities) / len(velocities)
    band = _scalar_to_band(mean_v, low=0.35, high=0.7)
    status = "ok" if len(velocities) >= 4 else "degraded"
    warnings: list[ReferenceFeatureWarningCode] = []
    if status == "degraded":
        warnings.append("reference_feature_insufficient_dynamics")
    bands = ReferenceFeatureBands()
    summary = f"{label}: velocity tendency band={band} (n={len(velocities)})."
    soft = f"[ref:{label}] dynamics tendency {band}."
    return _payload(
        status=status,
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=warnings,
        summary_max_chars=max_chars,
    )


def _dim_form(shared, evidence, label, max_chars):
    counts = shared["section_counts"]
    if not counts:
        return _payload(
            status="degraded",
            summary=f"{label}: no section types declared; form unknown.",
            soft_fragment=f"[ref:{label}] form unspecified.",
            bands=ReferenceFeatureBands(top_section_types=[]),
            evidence=evidence,
            warning_codes=["reference_feature_degraded"],
            summary_max_chars=max_chars,
        )
    top = [k for k, _ in Counter(counts).most_common(8)]
    bands = ReferenceFeatureBands(top_section_types=top)
    summary = f"{label}: section-type preference={', '.join(top)}."
    soft = f"[ref:{label}] favor form sections {', '.join(top[:4])}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_tension(shared, evidence, label, max_chars):
    dens = shared["density"]
    sections = shared["section_count"]
    if sections >= 3:
        shape = "arch"
    elif dens >= 0.55:
        shape = "plateau_high"
    elif dens <= 0.15:
        shape = "plateau_low"
    else:
        shape = "flat"
    bands = ReferenceFeatureBands(tension_shape=shape)
    summary = f"{label}: coarse tension shape={shape}."
    soft = f"[ref:{label}] tension curve shape {shape}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_motifs(composition, shared, evidence, label, max_chars):
    # Guard: metadata only — never motif note payloads.
    motif_count = shared["motif_count"]
    if motif_count <= 0:
        return _payload(
            status="unavailable",
            summary=f"{label}: no motifs metadata present.",
            soft_fragment="",
            bands=None,
            evidence=evidence,
            warning_codes=["reference_feature_insufficient_motifs"],
            summary_max_chars=max_chars,
        )
    transform_kinds: set[str] = set()
    for motif in composition.motifs or []:
        for occ in getattr(motif, "occurrences", None) or []:
            transform = getattr(occ, "transform", None) or getattr(occ, "kind", None)
            if transform:
                transform_kinds.add(str(transform)[:40])
        # Also check definition-level transforms if present
        for key in ("transforms", "variants"):
            raw = getattr(motif, key, None)
            if isinstance(raw, list):
                for item in raw:
                    transform_kinds.add(str(item)[:40])
    variety = _scalar_to_band(min(1.0, len(transform_kinds) / 4.0), low=0.25, high=0.6)
    bands = ReferenceFeatureBands(repetition_band=variety)
    summary = (
        f"{label}: motif_count={motif_count}; transform_variety={variety} "
        f"(metadata only; no note sequences)."
    )
    soft = f"[ref:{label}] motif development variety {variety}; count {motif_count}."
    return _payload(
        status="ok",
        summary=summary,
        soft_fragment=soft,
        bands=bands,
        evidence=evidence,
        warning_codes=[],
        summary_max_chars=max_chars,
    )


def _dim_performance(shared, evidence, label, max_chars):
    if not shared["velocities"] and not shared["has_expression"]:
        return _payload(
            status="unavailable",
            summary=f"{label}: no performance/expression fields.",
            soft_fragment="",
            bands=None,
            evidence=evidence,
            warning_codes=["reference_feature_insufficient_dynamics"],
            summary_max_chars=max_chars,
        )
    status = "ok" if shared["velocities"] else "degraded"
    warnings: list[ReferenceFeatureWarningCode] = []
    if status == "degraded":
        warnings.append("reference_feature_insufficient_dynamics")
    mean_v = (
        sum(shared["velocities"]) / len(shared["velocities"])
        if shared["velocities"]
        else 0.5
    )
    band = _scalar_to_band(mean_v, low=0.35, high=0.7)
    summary = f"{label}: performance dynamics tendency={band}."
    soft = f"[ref:{label}] performance dynamics {band}."
    return _payload(
        status=status,
        summary=summary,
        soft_fragment=soft,
        bands=ReferenceFeatureBands(),
        evidence=evidence,
        warning_codes=warnings,
        summary_max_chars=max_chars,
    )


# Re-export for conditioning modules (anti-melody instruction shared).
ANTI_MELODY_INSTRUCTION = _ANTI_MELODY_LINE
