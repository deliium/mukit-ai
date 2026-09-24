"""Orchestrate mix analysis: active-head stems → DSP → observations → interpret."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.db.connection import get_connection, get_project_db_path
from app.mix_analysis_schemas import (
    MIX_ANALYSIS_DIMENSIONS,
    MIX_ANALYSIS_DIMENSION_UNKNOWN,
    MIX_ANALYSIS_INPUT_TOO_LARGE,
    MIX_ANALYSIS_INTERNAL_ERROR,
    MIX_ANALYSIS_NOT_FOUND,
    MIX_ANALYSIS_STEM_NOT_READY,
    MIX_ANALYSIS_STEM_SET_INCOMPLETE,
    MIX_ANALYSIS_TIMEOUT,
    MixAnalysisAnalyzeRequest,
    MixAnalysisError,
    MixAnalysisReport,
    MixAnalysisStemFingerprint,
    MixAnalysisWarning,
)
from app.mix_analysis_settings import MixAnalysisSettings, load_mix_analysis_settings
from app.services.mix_analysis.active_stems import select_active_head_stems
from app.services.mix_analysis.fake_metrics import fake_measure_stem
from app.services.mix_analysis.interpret import run_mix_interpretation
from app.services.mix_analysis.metrics import measure_stem_file, measure_stem_pair_masking
from app.services.mix_analysis.observations import build_observations
from app.services.mix_analysis_store import insert_report, load_settings_and_root
from app.services.neural_audio_render_store import absolute_audio_path, load_settings_and_root as load_neural_root
from app.services.neural_audio_stem_store import (
    get_stem_set_row,
    list_stem_rows_for_set,
    parse_stem_track_ids,
)

logger = logging.getLogger(__name__)


def analyze_mix(
    request: MixAnalysisAnalyzeRequest,
    *,
    env: Mapping[str, str] | None = None,
    db_path: Path | str | None = None,
) -> tuple[MixAnalysisReport, bool]:
    """Run synchronous mix analysis. Returns (report, persisted)."""
    started = time.perf_counter()
    settings = load_mix_analysis_settings(env)
    neural_settings = load_neural_root(env)
    path = Path(db_path) if db_path is not None else get_project_db_path()

    dimensions = _resolve_dimensions(request.dimensions)
    logger.info(
        "Mix analysis start",
        extra={
            "stem_set_id": request.stem_set_id,
            "project_id": request.project_id,
            "persist": request.persist,
            "include_ai": request.include_ai_interpretation,
            "dimension_count": len(dimensions),
        },
    )

    with get_connection(path) as conn:
        stem_set = get_stem_set_row(conn, request.stem_set_id)
        if stem_set is None:
            raise MixAnalysisError(
                "Stem set not found",
                code=MIX_ANALYSIS_NOT_FOUND,
                http_status=404,
                details={"stem_set_id": request.stem_set_id},
            )
        members = list_stem_rows_for_set(conn, request.stem_set_id)
        if not members:
            raise MixAnalysisError(
                "Stem set has no members",
                code=MIX_ANALYSIS_STEM_SET_INCOMPLETE,
                http_status=422,
                details={"stem_set_id": request.stem_set_id},
            )

        # Incomplete if any non-superseded expected role is not complete — for v1:
        # selected heads must all be complete and readable; if filter empty and
        # set status is not complete, reject.
        set_status = str(stem_set.get("status") or "")
        selected = select_active_head_stems(members, stem_ids=request.stem_ids)
        if not selected:
            raise MixAnalysisError(
                "No complete active-head stems available for analysis",
                code=MIX_ANALYSIS_STEM_SET_INCOMPLETE,
                http_status=422,
                details={"stem_set_id": request.stem_set_id, "set_status": set_status},
            )
        if request.stem_ids is not None:
            wanted = {str(s).strip() for s in request.stem_ids if str(s).strip()}
            got = {str(r.get("id")) for r in selected}
            missing = wanted - got
            if missing:
                raise MixAnalysisError(
                    "One or more requested stems are not complete/ready",
                    code=MIX_ANALYSIS_STEM_NOT_READY,
                    http_status=422,
                    details={"missing_stem_ids": ",".join(sorted(missing)[:8])},
                )
        elif set_status != "complete":
            incomplete = [
                str(r.get("id"))
                for r in members
                if str(r.get("status") or "") != "complete"
            ]
            if incomplete:
                raise MixAnalysisError(
                    "Stem set members are not all complete",
                    code=MIX_ANALYSIS_STEM_SET_INCOMPLETE,
                    http_status=422,
                    details={"incomplete_count": len(incomplete)},
                )

        # Quota on total input bytes
        total_bytes = 0
        for row in selected:
            total_bytes += int(row.get("byte_size") or 0)
        if total_bytes > settings.max_total_input_bytes:
            raise MixAnalysisError(
                "Total stem input bytes exceed mix analysis cap",
                code=MIX_ANALYSIS_INPUT_TOO_LARGE,
                http_status=422,
                details={
                    "total_bytes": total_bytes,
                    "limit": settings.max_total_input_bytes,
                },
            )

        composition_fp = _composition_fingerprint(request.composition)
        tempo_bpm = stem_set.get("tempo_bpm")
        origin_tick = int(stem_set.get("origin_tick") or 0)

        all_measurements = []
        all_series = []
        stem_results = []
        analyzed_stems: list[MixAnalysisStemFingerprint] = []
        dsp_backend = "fake" if settings.fake_mode else "stdlib"

        for row in selected:
            if time.perf_counter() - started > settings.job_timeout_seconds:
                raise MixAnalysisError(
                    "Mix analysis exceeded wall-clock timeout",
                    code=MIX_ANALYSIS_TIMEOUT,
                    http_status=504,
                )
            relpath = row.get("audio_relpath")
            if not relpath:
                raise MixAnalysisError(
                    "Stem audio path missing",
                    code=MIX_ANALYSIS_STEM_NOT_READY,
                    http_status=422,
                    details={"stem_id": row.get("id")},
                )
            abs_path = absolute_audio_path(neural_settings, str(relpath))
            # sha before (assert untouched in tests)
            before_sha = _sha256_file(abs_path)
            track_ids = parse_stem_track_ids(row.get("source_track_ids_json"))
            stem_id = str(row["id"])
            role = str(row.get("stem_role") or "other")

            if settings.fake_mode:
                result = fake_measure_stem(
                    abs_path,
                    settings,
                    stem_id=stem_id,
                    stem_role=role,
                    source_track_ids=track_ids,
                )
            else:
                result = measure_stem_file(
                    abs_path,
                    settings,
                    stem_id=stem_id,
                    stem_role=role,
                    source_track_ids=track_ids,
                    dimensions=dimensions,
                    tempo_bpm=float(tempo_bpm) if tempo_bpm else None,
                    origin_tick=origin_tick,
                    composition=request.composition,
                )
            after_sha = _sha256_file(abs_path)
            if after_sha != before_sha:
                logger.error(
                    "Stem audio mutated during mix analysis",
                    extra={"stem_id": stem_id},
                )
                raise MixAnalysisError(
                    "Stem audio changed during analysis (refusing)",
                    code=MIX_ANALYSIS_INTERNAL_ERROR,
                    http_status=500,
                )

            dsp_backend = result.dsp_backend
            all_measurements.extend(result.measurements)
            all_series.extend(result.series)
            stem_results.append(
                {
                    "result": result,
                    "stem_id": stem_id,
                    "role": role,
                    "tracks": track_ids,
                }
            )
            analyzed_stems.append(
                MixAnalysisStemFingerprint(
                    stem_id=stem_id,
                    stem_role=role,
                    sha256_prefix=result.sha256_prefix or before_sha[:16],
                    source_track_ids=track_ids,
                )
            )

        # Pairwise masking for first overlapping band pairs
        if "masking_proxy" in dimensions and len(stem_results) >= 2:
            for i in range(len(stem_results)):
                for j in range(i + 1, len(stem_results)):
                    a = stem_results[i]
                    b = stem_results[j]
                    # Prefer bass+strings style pairs; still measure all pairs capped
                    if len(all_measurements) > 400:
                        break
                    masking = measure_stem_pair_masking(
                        a["result"],
                        b["result"],
                        stem_id_a=a["stem_id"],
                        stem_id_b=b["stem_id"],
                        role_a=a["role"],
                        role_b=b["role"],
                        tracks_a=a["tracks"],
                        tracks_b=b["tracks"],
                    )
                    all_measurements.append(masking)

        observations = build_observations(all_measurements, settings)
        interpretations, interp_warnings, _status = run_mix_interpretation(
            measurements=all_measurements,
            observations=observations,
            include_ai_interpretation=request.include_ai_interpretation,
            env=env,
        )

        warnings: list[MixAnalysisWarning] = list(interp_warnings)
        # Surface reverb unavailable once
        if any(m.unavailable_code == "reverb_estimate_unavailable" for m in all_measurements):
            warnings.append(
                MixAnalysisWarning(
                    code="reverb_estimate_unavailable",
                    severity="info",
                    message="Reverb/ambience estimate is unavailable in this DSP backend.",
                )
            )

        report = MixAnalysisReport(
            stem_set_id=request.stem_set_id,
            mix_render_id=request.mix_render_id,
            project_id=request.project_id,
            dsp_backend=dsp_backend,  # type: ignore[arg-type]
            source_stem_set_fingerprint=str(stem_set.get("source_fingerprint") or ""),
            source_composition_fingerprint=composition_fp,
            analyzed_stems=analyzed_stems,
            dimensions=list(dimensions),
            measurements=all_measurements,
            observations=observations,
            interpretations=interpretations,
            series=all_series[:32],
            warnings=warnings,
        )

        persisted = False
        if request.persist and request.project_id:
            report_settings = load_settings_and_root(env)
            meta = insert_report(
                conn, report_settings, report, project_id=request.project_id
            )
            report = report.model_copy(
                update={"report_id": meta.report_id, "created_at": meta.created_at}
            )
            persisted = True

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "Mix analysis complete",
            extra={
                "stem_set_id": request.stem_set_id,
                "report_id": report.report_id,
                "active_head_count": len(selected),
                "dsp_backend": dsp_backend,
                "measurement_count": len(all_measurements),
                "observation_count": len(observations),
                "interpretation_count": len(interpretations),
                "persisted": persisted,
                "duration_ms": duration_ms,
            },
        )
        return report, persisted


def _resolve_dimensions(raw: Sequence[str] | None) -> list[str]:
    if not raw:
        return sorted(MIX_ANALYSIS_DIMENSIONS)
    out: list[str] = []
    unknown: list[str] = []
    for item in raw:
        key = str(item).strip().lower()
        if key in MIX_ANALYSIS_DIMENSIONS:
            if key not in out:
                out.append(key)
        else:
            unknown.append(key)
    if unknown:
        raise MixAnalysisError(
            "Unknown mix analysis dimension",
            code=MIX_ANALYSIS_DIMENSION_UNKNOWN,
            http_status=422,
            details={"unknown": ",".join(unknown[:8])},
        )
    return out


def _composition_fingerprint(composition: dict[str, Any] | None) -> str | None:
    if not composition:
        return None
    try:
        from app.composition_schemas import CompositionV2
        from app.services.composition_snapshot_encoding import composition_snapshot_fingerprint

        comp = CompositionV2.model_validate(composition)
        return composition_snapshot_fingerprint(comp)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Composition fingerprint skipped",
            extra={"error_type": type(exc).__name__},
        )
        # Stable fallback without claiming snapshot identity
        raw = json.dumps(composition, sort_keys=True, separators=(",", ":")).encode("utf-8")
        import hashlib

        return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
