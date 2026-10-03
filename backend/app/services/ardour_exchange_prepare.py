"""Prepare outbound Ardour exchange packages from working V2 + alignment.

Slices with ``filter_composition_tracks`` then ``filter_composition_bar_range``,
renders SMF via ``render_midi_with_report``, and optionally copies a completed
neural stem WAV. Does not mutate Ardour session files or ``composition.v2``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from app.ardour_exchange_schemas import (
    ArdourExchangeError,
    ArdourExchangeManifestV1,
    ArdourExchangePrepareRequestV1,
    ArdourExchangePrepareResultV1,
)
from app.ardour_exchange_settings import ArdourExchangeSettings
from app.composition_schemas import CompositionV2
from app.services.ardour_exchange_align import bars_to_samples
from app.services.ardour_exchange_store import (
    allocate_package_id,
    get_exchange_preview,
    new_manifest_payload,
    package_to_zip_bytes,
    write_package,
)
from app.services.composition_midi import render_midi_with_report
from app.services.neural_audio_stem_partition import (
    filter_composition_bar_range,
    filter_composition_tracks,
)

logger = logging.getLogger(__name__)


def prepare_outbound_package(
    settings: ArdourExchangeSettings,
    request: ArdourExchangePrepareRequestV1,
    *,
    composition: CompositionV2 | Mapping[str, Any] | None = None,
) -> ArdourExchangePrepareResultV1:
    """Write an outbound package under the exchange root."""
    preview = get_exchange_preview()
    source_composition = composition
    if source_composition is None:
        if preview is None:
            raise ArdourExchangeError("ardour_exchange_preview_missing")
        source_composition = preview.draft_composition

    if isinstance(source_composition, CompositionV2):
        comp_dict = source_composition.model_dump(mode="json")
        validated = source_composition
    else:
        validated = CompositionV2.model_validate(source_composition)
        comp_dict = validated.model_dump(mode="json")

    if request.use_preview_alignment:
        if preview is None:
            raise ArdourExchangeError("ardour_exchange_preview_missing")
        start_bar = preview.alignment.start_bar
        bar_count = preview.alignment.bar_count
        tempo_bpm = preview.alignment.tempo_bpm
        time_signature = preview.alignment.time_signature
        track_name = preview.manifest.track_name
        start_samples = preview.manifest.start_samples
        sample_rate = preview.manifest.sample_rate
        source_fingerprint = preview.manifest.source_fingerprint
        ticks_per_quarter = preview.manifest.ticks_per_quarter
        source_track_ssid = preview.manifest.source_track_ssid
    else:
        start_bar = request.start_bar or 1
        bar_count = request.bar_count or validated.bar_count
        tempo_bpm = int(validated.tempo)
        time_signature = validated.time_signature
        track_name = request.track_name or "Exchange"
        start_samples = 0
        sample_rate = 48000
        source_fingerprint = "outbound00000001"
        ticks_per_quarter = validated.ticks_per_quarter
        source_track_ssid = None

    if request.start_bar is not None:
        start_bar = request.start_bar
    if request.bar_count is not None:
        bar_count = request.bar_count
    if request.track_name is not None:
        track_name = request.track_name

    track_ids = list(request.track_ids) if request.track_ids else [t.id for t in validated.tracks]
    sliced = filter_composition_tracks(comp_dict, track_ids)
    end_bar = start_bar + bar_count - 1
    sliced = filter_composition_bar_range(
        sliced,
        {"start_bar": start_bar, "end_bar": end_bar},
    )
    sliced_model = CompositionV2.model_validate(sliced)

    try:
        midi_result = render_midi_with_report(sliced_model)
        midi_bytes = midi_result.midi_bytes
    except Exception as exc:
        logger.warning(
            "Ardour exchange prepare MIDI render failed",
            extra={"error_type": type(exc).__name__},
        )
        raise ArdourExchangeError("ardour_exchange_midi_invalid") from exc

    audio_files: dict[str, bytes] = {}
    audio_relpaths: list[str] = []
    if request.stem_id:
        audio_files, audio_relpaths = _copy_stem_wav(request.stem_id)

    length_samples = bars_to_samples(
        bar_count=bar_count,
        tempo_bpm=tempo_bpm,
        sample_rate=sample_rate,
        time_signature=time_signature,
    )
    package_id = allocate_package_id()
    manifest = new_manifest_payload(
        direction="outbound",
        track_name=track_name,
        tempo_bpm=tempo_bpm,
        time_signature=time_signature,
        start_bar=start_bar,
        bar_count=bar_count,
        start_samples=start_samples,
        sample_rate=sample_rate,
        source_fingerprint=source_fingerprint,
        ticks_per_quarter=ticks_per_quarter,
        source_track_ssid=source_track_ssid,
        length_samples=length_samples,
        audio_relpaths=audio_relpaths,
        package_id=package_id,
    )
    packed = write_package(
        settings,
        manifest=manifest,
        midi_bytes=midi_bytes,
        audio_files=audio_files,
    )
    download_path = f"/ardour/exchange/packages/{package_id}/download"
    logger.info(
        "Ardour exchange prepare complete",
        extra={
            "package_id": package_id,
            "bar_count": bar_count,
            "has_audio": bool(audio_files),
            "byte_size": packed.byte_size,
        },
    )
    return ArdourExchangePrepareResultV1(
        package_id=package_id,
        manifest=manifest,
        download_path=download_path,
        has_audio=bool(audio_files),
        byte_size=packed.byte_size,
    )


def prepare_zip_bytes(
    settings: ArdourExchangeSettings,
    package_id: str,
) -> bytes:
    """Return zip bytes for a stored package."""
    from app.services.ardour_exchange_store import read_package

    packed = read_package(settings, package_id)
    return package_to_zip_bytes(packed)


def _copy_stem_wav(stem_id: str) -> tuple[dict[str, bytes], list[str]]:
    try:
        from app.services.neural_audio_stems import resolve_stem_audio_file_path
    except Exception as exc:
        raise ArdourExchangeError("ardour_exchange_stem_unavailable") from exc

    try:
        path, _content_type, stem = resolve_stem_audio_file_path(stem_id)
    except Exception as exc:
        logger.warning(
            "Ardour exchange stem unavailable",
            extra={"code": "ardour_exchange_stem_unavailable", "stem_id": stem_id[:40]},
        )
        raise ArdourExchangeError(
            "ardour_exchange_stem_unavailable",
            details={"stem_id": stem_id[:40]},
        ) from exc

    if getattr(stem, "status", None) != "complete":
        raise ArdourExchangeError(
            "ardour_exchange_stem_unavailable",
            details={"stem_id": stem_id[:40]},
        )

    safe_name = f"stem_{stem_id[:32]}.wav".replace("/", "_")
    relpath = f"audio/{safe_name}"
    payload = Path(path).read_bytes()
    logger.info(
        "Ardour exchange stem copied",
        extra={"stem_id": stem_id[:40], "byte_size": len(payload)},
    )
    return {relpath: payload}, [relpath]
