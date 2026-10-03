"""Ingest an Ardour exchange package into a session-only preview.

Uses existing MIDI import canonicalization. Never writes ``composition.v2``
to the project store — Apply is SPA-owned via ``completeImport``.
"""

from __future__ import annotations

import logging
from typing import Mapping

from app.ardour_exchange_schemas import (
    ArdourExchangeAlignmentEcho,
    ArdourExchangeError,
    ArdourExchangeImportReportSummary,
    ArdourExchangePreviewV1,
    ArdourExchangeSessionContextV1,
)
from app.ardour_exchange_settings import ArdourExchangeSettings
from app.import_schemas import CompositionImportError
from app.services.ardour_exchange_align import (
    expected_duration_ticks,
    length_samples_matches,
)
from app.services.ardour_exchange_store import (
    PackageReadResult,
    allocate_preview_id,
    ingest_zip_bytes,
    read_package,
    set_exchange_preview,
)
from app.services.composition_midi_import import import_midi_bytes

logger = logging.getLogger(__name__)


def ingest_package_id(
    settings: ArdourExchangeSettings,
    package_id: str,
    *,
    session_context: ArdourExchangeSessionContextV1 | None = None,
) -> ArdourExchangePreviewV1:
    """Load a package from the exchange root and build a session preview."""
    packed = read_package(settings, package_id)
    return build_preview_from_package(packed, session_context=session_context)


def ingest_package_zip(
    settings: ArdourExchangeSettings,
    zip_bytes: bytes,
    *,
    session_context: ArdourExchangeSessionContextV1 | None = None,
) -> ArdourExchangePreviewV1:
    """Expand zip bytes into the exchange root, then build a preview."""
    packed = ingest_zip_bytes(settings, zip_bytes)
    return build_preview_from_package(packed, session_context=session_context)


def build_preview_from_package(
    packed: PackageReadResult,
    *,
    session_context: ArdourExchangeSessionContextV1 | None = None,
) -> ArdourExchangePreviewV1:
    """Validate alignment, import MIDI, stash process-memory preview."""
    manifest = packed.manifest
    if not length_samples_matches(
        length_samples=manifest.length_samples,
        bar_count=manifest.bar_count,
        tempo_bpm=manifest.tempo_bpm,
        sample_rate=manifest.sample_rate,
        time_signature=manifest.time_signature,
    ):
        logger.warning(
            "Ardour exchange alignment length mismatch",
            extra={
                "code": "ardour_exchange_alignment_invalid",
                "package_id": manifest.package_id,
            },
        )
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "length_samples"},
        )

    try:
        imported = import_midi_bytes(
            packed.midi_bytes,
            display_filename="material.mid",
        )
    except CompositionImportError as exc:
        logger.warning(
            "Ardour exchange MIDI import failed",
            extra={
                "code": "ardour_exchange_midi_invalid",
                "package_id": manifest.package_id,
                "import_code": getattr(exc, "code", "import_error"),
            },
        )
        raise ArdourExchangeError(
            "ardour_exchange_midi_invalid",
            details={"import_code": str(getattr(exc, "code", "import_error"))[:64]},
        ) from exc
    except Exception as exc:
        logger.warning(
            "Ardour exchange MIDI import unexpected",
            extra={
                "code": "ardour_exchange_midi_invalid",
                "package_id": manifest.package_id,
                "error_type": type(exc).__name__,
            },
        )
        raise ArdourExchangeError("ardour_exchange_midi_invalid") from exc

    composition = imported.composition
    expected_ticks = expected_duration_ticks(
        bar_count=manifest.bar_count,
        time_signature=manifest.time_signature,
        ticks_per_quarter=composition.ticks_per_quarter,
    )
    # Refuse when imported timeline is shorter than the declared bar span.
    if composition.duration_ticks < expected_ticks:
        logger.warning(
            "Ardour exchange bar_count vs timeline",
            extra={
                "code": "ardour_exchange_alignment_invalid",
                "package_id": manifest.package_id,
                "bar_count": manifest.bar_count,
            },
        )
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "bar_count_vs_timeline"},
        )

    note_count = sum(len(track.events) for track in composition.tracks)
    issue_codes = [issue.code for issue in imported.import_report.issues][:64]
    warning_codes = [
        issue.code
        for issue in imported.import_report.issues
        if getattr(issue, "severity", None) == "warning"
    ][:64]

    preview = ArdourExchangePreviewV1(
        preview_id=allocate_preview_id(),
        package_id=manifest.package_id,
        manifest=manifest,
        session_context=session_context,
        draft_composition=composition,
        import_report=ArdourExchangeImportReportSummary(
            issue_codes=issue_codes,
            warning_codes=warning_codes,
            note_count=note_count,
            track_count=len(composition.tracks),
        ),
        alignment=ArdourExchangeAlignmentEcho(
            start_bar=manifest.start_bar,
            bar_count=manifest.bar_count,
            tempo_bpm=manifest.tempo_bpm,
            time_signature=manifest.time_signature,
        ),
    )
    set_exchange_preview(preview)
    logger.info(
        "Ardour exchange ingest complete",
        extra={
            "package_id": manifest.package_id,
            "bar_count": manifest.bar_count,
            "tempo_bpm": manifest.tempo_bpm,
            "track_name": manifest.track_name[:40],
            "note_count": note_count,
        },
    )
    if warning_codes:
        logger.warning(
            "Ardour exchange import warnings",
            extra={"package_id": manifest.package_id, "warning_codes": warning_codes[:8]},
        )
    return preview


def apply_preview_payload() -> Mapping[str, object]:
    """Return composition + manifest for SPA ``completeImport`` (no project write)."""
    from app.services.ardour_exchange_store import get_exchange_preview

    preview = get_exchange_preview()
    if preview is None:
        raise ArdourExchangeError("ardour_exchange_preview_missing")
    logger.info(
        "Ardour exchange apply payload",
        extra={"package_id": preview.package_id, "preview_id": preview.preview_id},
    )
    return {
        "composition": preview.draft_composition,
        "manifest": preview.manifest,
        "import_report": preview.import_report,
    }
