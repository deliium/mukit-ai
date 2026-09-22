"""Deterministic multi-project aggregate → ``derived`` preference fields.

Never writes ``DATASET_ROOT``. Never stores note events, analysis reports, or
embedding vectors in the profile body — only abstract bands / histograms / labels.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.composer_profile_schemas import (
    ComposerProfileDeriveSource,
    ComposerProfileError,
    ComposerProfileSourceProject,
    ComposerProfileV1,
    DerivedPreferenceFields,
    IntervalHistogram,
    PreferenceBand,
    PreferenceFields,
    SectionTypeHistogram,
    TensionShapeCode,
    empty_composer_profile,
    preference_fields_to_dict,
)
from app.composer_profile_settings import load_composer_profile_settings
from app.composition_schemas import CompositionV2
from app.embeddings.errors import EmbeddingScopeError
from app.embeddings.features import (
    CONCURRENT_BINS,
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
from app.embeddings.schemas import EmbedScopeComposition
from app.embeddings.settings import EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services import project_store
from app.services.project_history import ProjectHistoryNotFoundError, get_revision_detail
from app.services.project_history_store import ProjectHistoryError

logger = logging.getLogger(__name__)

_PC_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# Feature layout offsets matching embeddings/features.py symbolic.features.v1
_OFF_RANGE = PC_DIMS
_OFF_DUR = _OFF_RANGE + RANGE_DIMS
_OFF_ONSET = _OFF_DUR + DUR_BINS
_OFF_DENSITY = _OFF_ONSET + ONSET_BINS
_OFF_INTERVAL = _OFF_DENSITY + DENSITY_DIMS
_OFF_CONTOUR = _OFF_INTERVAL + INTERVAL_BINS
_OFF_CONCURRENT = _OFF_CONTOUR + 3
_OFF_ROLE = _OFF_CONCURRENT + CONCURRENT_BINS
_OFF_TRACK = _OFF_ROLE + ROLE_DIMS
_OFF_FORM = _OFF_TRACK + TRACK_COUNT_DIMS
_OFF_SECTION = _OFF_FORM + 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scalar_to_band(value: float, *, low: float, high: float) -> PreferenceBand:
    """Map a 0..1-ish scalar into a five-level band."""
    if value <= low * 0.5:
        return "very_low"
    if value <= low:
        return "low"
    if value < high:
        return "moderate"
    if value < high + (1.0 - high) * 0.5:
        return "high"
    return "very_high"


def _midi_norm_to_band(norm: float) -> PreferenceBand:
    # MIDI / 127: very_low <36, low <48, moderate <72, high <96, else very_high
    midi = norm * 127.0
    if midi < 36:
        return "very_low"
    if midi < 48:
        return "low"
    if midi < 72:
        return "moderate"
    if midi < 96:
        return "high"
    return "very_high"


def _mode_band(bands: list[PreferenceBand]) -> PreferenceBand | None:
    if not bands:
        return None
    counts = Counter(bands)
    # Deterministic tie-break: prefer moderate, then lexical.
    best = sorted(counts.items(), key=lambda item: (-item[1], item[0] != "moderate", item[0]))
    return best[0][0]


def _mean(values: list[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _load_composition(
    source: ComposerProfileDeriveSource,
    *,
    db_path: Path | None,
) -> tuple[CompositionV2, str | None] | None:
    """Return (composition, revision_id) or None when unusable."""
    project_id = source.project_id
    revision_id = source.revision_id

    if revision_id:
        try:
            detail = get_revision_detail(project_id, revision_id, db_path=db_path)
        except (LookupError, ProjectHistoryError, ProjectHistoryNotFoundError) as exc:
            logger.debug(
                "Derive skip: revision missing",
                extra={
                    "project_id": project_id,
                    "revision_id": revision_id,
                    "reason": type(exc).__name__,
                },
            )
            return None
        if detail.composition is None:
            logger.debug(
                "Derive skip: revision empty composition",
                extra={"project_id": project_id, "revision_id": revision_id},
            )
            return None
        return detail.composition, revision_id

    try:
        record = project_store.get_project(project_id, db_path=db_path)
    except project_store.ProjectNotFoundError:
        logger.debug(
            "Derive skip: project missing",
            extra={"project_id": project_id, "reason": "not_found"},
        )
        return None

    if not record.composition_json:
        logger.debug(
            "Derive skip: empty composition",
            extra={"project_id": project_id, "reason": "empty_composition"},
        )
        return None
    try:
        composition = CompositionV2.model_validate(json.loads(record.composition_json))
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Derive skip: invalid V2",
            extra={"project_id": project_id, "error_type": type(exc).__name__},
        )
        return None
    return composition, None


def _extract_source_stats(
    composition: CompositionV2,
    *,
    max_list_items: int,
) -> dict[str, Any] | None:
    try:
        features, _window = extract_symbolic_features_v1(
            composition, EmbedScopeComposition()
        )
    except EmbeddingScopeError as exc:
        logger.debug(
            "Derive skip: empty embed scope",
            extra={"code": exc.code if hasattr(exc, "code") else "embed_empty"},
        )
        return None

    midi_min = features[_OFF_RANGE]
    midi_max = features[_OFF_RANGE + 1]
    midi_mean = features[_OFF_RANGE + 2]
    density = features[_OFF_DENSITY]
    interval = features[_OFF_INTERVAL : _OFF_INTERVAL + INTERVAL_BINS]
    onset = features[_OFF_ONSET : _OFF_ONSET + ONSET_BINS]
    concurrent = features[_OFF_CONCURRENT : _OFF_CONCURRENT + CONCURRENT_BINS]
    track_count_norm = features[_OFF_TRACK]
    section_vec = features[_OFF_SECTION : _OFF_SECTION + SECTION_TYPE_DIMS]
    pc = features[0:PC_DIMS]

    # Offbeat onset fraction: bins away from 0 and mid-bar (heuristic).
    offbeat = sum(onset[i] for i in range(ONSET_BINS) if i not in {0, ONSET_BINS // 2})
    concurrent_peak = max(concurrent) if concurrent else 0.0
    range_semitones = max(0.0, (midi_max - midi_min) * 127.0)

    # Instruments from tracks (bounded labels).
    instruments: list[str] = []
    for track in composition.tracks:
        label = (track.instrument or track.name or track.role or "").strip()
        if label:
            instruments.append(label[:80])

    # Declared section types.
    section_counts: Counter[str] = Counter()
    for section in composition.sections or []:
        stype = (getattr(section, "type", None) or "other")
        stype = str(stype).strip().lower() or "other"
        if stype not in SECTION_TYPE_ORDER:
            stype = "other"
        section_counts[stype] += 1
    if not section_counts:
        # Fall back to embedding one-hot if present.
        for idx, weight in enumerate(section_vec):
            if weight > 0.01:
                section_counts[SECTION_TYPE_ORDER[idx]] += weight

    # Top pitch classes as chord-vocabulary-ish labels.
    ranked_pc = sorted(enumerate(pc), key=lambda item: item[1], reverse=True)
    chord_vocab = [
        _PC_NAMES[idx] for idx, weight in ranked_pc[:max_list_items] if weight > 0.02
    ]

    # Motif development heuristic from composition.motifs metadata when present.
    motif_count = len(composition.motifs or [])
    motif_band: PreferenceBand | None = None
    if motif_count <= 0:
        motif_band = "very_low"
    elif motif_count == 1:
        motif_band = "low"
    elif motif_count <= 3:
        motif_band = "moderate"
    elif motif_count <= 6:
        motif_band = "high"
    else:
        motif_band = "very_high"

    # Simple tension shape from section count (coarse).
    tension: TensionShapeCode = "flat"
    if len(section_counts) >= 3:
        tension = "arch"
    elif density >= 0.55:
        tension = "plateau_high"
    elif density <= 0.15:
        tension = "plateau_low"

    # Velocity / dynamics if expression present on events.
    velocities: list[float] = []
    for track in composition.tracks:
        for event in track.events or []:
            vel = getattr(event, "velocity", None)
            if isinstance(vel, (int, float)):
                velocities.append(float(vel) / 127.0)
    dynamics_band: PreferenceBand | None = None
    velocity_band: PreferenceBand | None = None
    if velocities:
        mean_v = _mean(velocities)
        velocity_band = _scalar_to_band(mean_v, low=0.35, high=0.7)
        dynamics_band = velocity_band

    return {
        "midi_min": midi_min,
        "midi_max": midi_max,
        "midi_mean": midi_mean,
        "range_semitones": range_semitones,
        "density": density,
        "offbeat": offbeat,
        "concurrent_peak": concurrent_peak,
        "track_count_norm": track_count_norm,
        "interval": list(interval),
        "instruments": instruments[:max_list_items],
        "section_counts": dict(section_counts),
        "chord_vocab": chord_vocab,
        "motif_band": motif_band,
        "tension": tension,
        "dynamics_band": dynamics_band,
        "velocity_band": velocity_band,
        "note_count": sum(len(t.events or []) for t in composition.tracks),
    }


def _aggregate_stats(
    per_source: list[dict[str, Any]],
    *,
    max_list_items: int,
) -> DerivedPreferenceFields:
    midi_min_bands = [_midi_norm_to_band(s["midi_min"]) for s in per_source]
    midi_max_bands = [_midi_norm_to_band(s["midi_max"]) for s in per_source]
    midi_mean_bands = [_midi_norm_to_band(s["midi_mean"]) for s in per_source]
    range_bands = [
        _scalar_to_band(min(1.0, s["range_semitones"] / 36.0), low=0.25, high=0.65)
        for s in per_source
    ]
    density_bands = [
        _scalar_to_band(s["density"], low=0.2, high=0.55) for s in per_source
    ]
    sync_bands = [_scalar_to_band(s["offbeat"], low=0.25, high=0.55) for s in per_source]
    arr_bands = [
        _scalar_to_band(s["concurrent_peak"], low=0.2, high=0.55) for s in per_source
    ]
    track_bands = [
        _scalar_to_band(s["track_count_norm"], low=0.2, high=0.55) for s in per_source
    ]

    # Mean interval histogram
    interval_acc = [0.0] * INTERVAL_BINS
    for s in per_source:
        for i, value in enumerate(s["interval"]):
            interval_acc[i] += float(value)
    n = len(per_source)
    interval_mean = [round(v / n, 6) for v in interval_acc]

    instrument_counter: Counter[str] = Counter()
    for s in per_source:
        for label in s["instruments"]:
            instrument_counter[label] += 1
    preferred_instruments = [label for label, _ in instrument_counter.most_common(max_list_items)]

    chord_counter: Counter[str] = Counter()
    for s in per_source:
        for label in s["chord_vocab"]:
            chord_counter[label] += 1
    chord_vocabulary = [label for label, _ in chord_counter.most_common(max_list_items)]

    form_acc: Counter[str] = Counter()
    for s in per_source:
        for key, weight in s["section_counts"].items():
            form_acc[key] += float(weight)
    preferred_forms = SectionTypeHistogram(
        counts={k: round(v, 6) for k, v in form_acc.most_common(max_list_items)}
    )

    motif_bands = [s["motif_band"] for s in per_source if s.get("motif_band")]
    tension_counter = Counter(s["tension"] for s in per_source)
    tension_shape = tension_counter.most_common(1)[0][0] if tension_counter else None

    dynamics = [s["dynamics_band"] for s in per_source if s.get("dynamics_band")]
    velocities = [s["velocity_band"] for s in per_source if s.get("velocity_band")]

    # Harmonic complexity / modulation: heuristics from density + pitch-class spread.
    # Without full harmony report we approximate from density + range.
    chord_change = density_bands  # proxy
    extension = [
        _scalar_to_band(min(1.0, s["range_semitones"] / 48.0), low=0.2, high=0.55)
        for s in per_source
    ]
    modulation = [
        _scalar_to_band(min(1.0, len(s["chord_vocab"]) / 8.0), low=0.25, high=0.6)
        for s in per_source
    ]
    repetition = [
        _scalar_to_band(1.0 - min(1.0, s["density"]), low=0.3, high=0.7) for s in per_source
    ]

    derived = DerivedPreferenceFields(
        midi_min_band=_mode_band(midi_min_bands),
        midi_max_band=_mode_band(midi_max_bands),
        midi_mean_band=_mode_band(midi_mean_bands),
        range_semitones_band=_mode_band(range_bands),
        interval_histogram=IntervalHistogram(bins=interval_mean),
        chord_change_rate_band=_mode_band(chord_change),
        extension_density_band=_mode_band(extension),
        chord_vocabulary=chord_vocabulary,
        modulation_frequency_band=_mode_band(modulation),
        rhythmic_density_band=_mode_band(density_bands),
        syncopation_band=_mode_band(sync_bands),
        preferred_instruments=preferred_instruments,
        arrangement_density_band=_mode_band(arr_bands),
        track_count_band=_mode_band(track_bands),
        repetition_amount_band=_mode_band(repetition),
        motif_development_band=_mode_band(motif_bands),
        preferred_forms=preferred_forms if preferred_forms.counts else None,
        tension_shape=tension_shape,  # type: ignore[arg-type]
        dynamics_band=_mode_band(dynamics),
        velocity_tendency_band=_mode_band(velocities),
        stats_meta={
            "source_count": n,
            "mean_note_count": round(_mean([float(s["note_count"]) for s in per_source]), 2),
            "feature_profile": "symbolic.features.v1",
        },
    )
    return derived


def derive_composer_profile(
    sources: list[ComposerProfileDeriveSource],
    *,
    name: str | None = None,
    profile_id: str | None = None,
    existing: ComposerProfileV1 | None = None,
    db_path: Path | None = None,
) -> tuple[ComposerProfileV1, list[str]]:
    """Aggregate abstract prefs from selected projects.

    Returns ``(profile, warnings)``. Leaves ``explicit`` empty unless ``existing``
    already has explicit fields (preserved).
    """
    settings = load_composer_profile_settings()
    started = time.perf_counter()
    warnings: list[str] = []

    if len(sources) > settings.max_source_projects:
        raise ComposerProfileError(
            "composer_profile_cap_exceeded",
            f"Too many source projects (max {settings.max_source_projects})",
            http_status=422,
            details={
                "max_source_projects": settings.max_source_projects,
                "requested": len(sources),
            },
        )

    per_source: list[dict[str, Any]] = []
    source_rows: list[ComposerProfileSourceProject] = []
    now = _utc_now_iso()

    for source in sources:
        loaded = _load_composition(source, db_path=db_path)
        if loaded is None:
            warnings.append(f"source_skipped:{source.project_id}")
            continue
        composition, revision_id = loaded
        # Skip empty event material.
        event_count = sum(len(t.events or []) for t in composition.tracks)
        if event_count <= 0:
            warnings.append(f"source_empty:{source.project_id}")
            logger.debug(
                "Derive skip: zero events",
                extra={"project_id": source.project_id},
            )
            continue
        stats = _extract_source_stats(composition, max_list_items=settings.max_list_items)
        if stats is None:
            warnings.append(f"source_unembeddable:{source.project_id}")
            continue
        fingerprint = composition_source_fingerprint(composition)
        prefix = fingerprint[:EMBEDDING_FINGERPRINT_LOG_PREFIX_LEN]
        per_source.append(stats)
        source_rows.append(
            ComposerProfileSourceProject(
                project_id=source.project_id,
                revision_id=revision_id,
                fingerprint_prefix=prefix,
                included_at=now,
            )
        )
        logger.debug(
            "Derive source usable",
            extra={
                "project_id": source.project_id,
                "revision_id": revision_id,
                "fingerprint_prefix": prefix,
                "note_count": stats["note_count"],
            },
        )

    if not per_source:
        raise ComposerProfileError(
            "composer_profile_derive_empty",
            "No usable source projects remained after derive",
            http_status=422,
            details={"requested": len(sources), "warnings": warnings[:16]},
        )

    derived = _aggregate_stats(per_source, max_list_items=settings.max_list_items)
    # Cap list fields again via settings.
    if len(derived.chord_vocabulary) > settings.max_list_items:
        derived = derived.model_copy(
            update={"chord_vocabulary": derived.chord_vocabulary[: settings.max_list_items]}
        )
    if len(derived.preferred_instruments) > settings.max_list_items:
        derived = derived.model_copy(
            update={
                "preferred_instruments": derived.preferred_instruments[
                    : settings.max_list_items
                ]
            }
        )

    if existing is not None:
        profile = existing.model_copy(
            update={
                "derived": derived,
                "source_projects": source_rows,
                "updated_at": now,
                "name": name or existing.name,
            }
        )
    else:
        profile = empty_composer_profile(
            profile_id=profile_id or "prof_preview",
            name=name or "Derived Profile",
            created_at=now,
            updated_at=now,
        )
        profile = profile.model_copy(
            update={"derived": derived, "source_projects": source_rows}
        )

    profile = ComposerProfileV1.model_validate(profile.model_dump())
    duration_ms = int((time.perf_counter() - started) * 1000)
    field_keys = sorted(preference_fields_to_dict(PreferenceFields.model_validate(
        {k: v for k, v in derived.model_dump().items() if k != "stats_meta"}
    )).keys())
    logger.info(
        "Composer profile derive complete",
        extra={
            "project_count": len(sources),
            "usable_count": len(per_source),
            "duration_ms": duration_ms,
            "field_keys_count": len(field_keys),
            "warning_count": len(warnings),
        },
    )
    return profile, warnings
