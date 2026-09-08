"""Hardened MusicXML / MXL → ParsedSourceScore conversion.

Security preflight runs before ``music21``: reject entity declarations and
unsafe MXL archives, strip external DTD declarations, and keep all work in
memory. Conversion emits parser-neutral source dataclasses for shared
canonicalization.
"""

from __future__ import annotations

import io
import logging
import math
import re
import time
import zipfile
from fractions import Fraction
from typing import Any, Iterable, Literal

from app.import_schemas import CompositionImportError, ImportReport
from app.import_settings import (
    XML_PROLOG_PREFIXES,
    ZIP_EMPTY_ARCHIVE_SIGNATURE,
    ZIP_LOCAL_FILE_SIGNATURE,
    ImportSettings,
    load_import_settings,
)
from app.services.composition_import import (
    CanonicalImportResult,
    ParsedSourceScore,
    SourceDynamicMark,
    SourceKeyChange,
    SourceMarker,
    SourceMeterChange,
    SourceNoteEvent,
    SourceSustainSpan,
    SourceTempoChange,
    SourceTrack,
    canonicalize_source_score,
)


logger = logging.getLogger(__name__)

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE\b", re.IGNORECASE)
_ENTITY_RE = re.compile(rb"<!ENTITY\b", re.IGNORECASE)
_DOCTYPE_STRIP_RE = re.compile(r"<!DOCTYPE[^>]*(?:\[.*?\]\s*)?>", re.IGNORECASE | re.DOTALL)
_SCORE_PARTWISE_RE = re.compile(rb"<score-partwise\b", re.IGNORECASE)
_SCORE_TIMEWISE_RE = re.compile(rb"<score-timewise\b", re.IGNORECASE)

_ARTICULATION_MAP = {
    "staccato": "staccato",
    "staccatissimo": "staccatissimo",
    "tenuto": "tenuto",
    "accent": "accent",
    "strongaccent": "marcato",
    "marcato": "marcato",
}

_DYNAMIC_LEVELS = frozenset({"ppp", "pp", "p", "mp", "mf", "f", "ff", "fff"})


def import_musicxml_bytes(
    data: bytes,
    *,
    display_filename: str = "upload.musicxml",
    settings: ImportSettings | None = None,
) -> CanonicalImportResult:
    """Parse MusicXML/MXL bytes and canonicalize into composition.v2."""
    cfg = settings or load_import_settings()
    parsed, meta = parse_musicxml_bytes(data, settings=cfg)
    result = canonicalize_source_score(
        parsed,
        display_filename=display_filename,
        input_bytes=len(data),
        settings=cfg,
    )
    _attach_musicxml_issues(result.import_report, meta)
    logger.info(
        "MusicXML import completed",
        extra={
            "status": result.import_report.status,
            "format": parsed.format,
            "track_count": len(result.composition.tracks),
            "note_count": sum(len(track.events) for track in result.composition.tracks),
            "parse_ms": round(meta["parse_ms"], 3),
        },
    )
    return result


def parse_musicxml_bytes(
    data: bytes,
    *,
    settings: ImportSettings | None = None,
) -> tuple[ParsedSourceScore, dict[str, Any]]:
    """Return ``(ParsedSourceScore, parse_meta)`` after hardened preflight."""
    cfg = settings or load_import_settings()
    started = time.perf_counter()
    if not data:
        raise CompositionImportError(
            "import_malformed_source",
            "MusicXML upload is empty",
            http_status=422,
        )
    if len(data) > cfg.max_upload_bytes:
        raise CompositionImportError(
            "import_payload_too_large",
            "MusicXML upload exceeds configured byte limit",
            http_status=413,
            details={"limit_bytes": cfg.max_upload_bytes, "input_bytes": len(data)},
        )

    detected: Literal["musicxml", "mxl"]
    xml_bytes: bytes
    if data.startswith(ZIP_LOCAL_FILE_SIGNATURE) or data.startswith(ZIP_EMPTY_ARCHIVE_SIGNATURE):
        detected = "mxl"
        xml_bytes = _extract_mxl_score_root(data, cfg)
    elif _looks_like_xml(data):
        detected = "musicxml"
        xml_bytes = data
    else:
        raise CompositionImportError(
            "import_unsupported_media_type",
            "Content is not MusicXML or MXL",
            http_status=415,
        )

    safe_xml = _harden_xml_document(xml_bytes)
    try:
        score = _parse_with_music21(safe_xml)
    except CompositionImportError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "MusicXML parser failed",
            extra={"error_type": type(exc).__name__, "error_code": "import_malformed_source"},
        )
        raise CompositionImportError(
            "import_malformed_source",
            "MusicXML file is malformed or unsupported",
            http_status=422,
            details={"parser": type(exc).__name__},
        ) from exc

    parsed, convert_meta = _score_to_source(score, detected_format=detected, settings=cfg)
    parse_ms = (time.perf_counter() - started) * 1000.0
    meta = {
        **convert_meta,
        "parse_ms": parse_ms,
        "detected_format": detected,
        "xml_bytes": len(safe_xml),
    }
    logger.debug(
        "MusicXML parse finished",
        extra={
            "format": detected,
            "part_count": len(parsed.tracks),
            "note_count": sum(len(track.notes) for track in parsed.tracks),
            "source_ppq": parsed.source_ppq,
            "parse_ms": round(parse_ms, 3),
            **{k: convert_meta[k] for k in ("omitted_grace", "omitted_unsupported", "repeat_expanded") if k in convert_meta},
        },
    )
    logger.info(
        "MusicXML parse succeeded",
        extra={
            "format": detected,
            "part_count": len(parsed.tracks),
            "note_count": sum(len(track.notes) for track in parsed.tracks),
            "source_ppq": parsed.source_ppq,
        },
    )
    return parsed, meta


def _looks_like_xml(data: bytes) -> bool:
    stripped = data.lstrip()
    if stripped.startswith(b"\xef\xbb\xbf"):
        stripped = stripped[3:]
    lower = stripped[:256].lower()
    return any(lower.startswith(prefix.lower()) for prefix in XML_PROLOG_PREFIXES) or b"<score-" in lower


def _harden_xml_document(data: bytes) -> bytes:
    if _ENTITY_RE.search(data):
        raise CompositionImportError(
            "import_malformed_source",
            "XML entity declarations are not allowed",
            http_status=422,
            details={"reason": "xml_entity_rejected"},
        )
    text = data.decode("utf-8", errors="strict")
    if _DOCTYPE_RE.search(data):
        # Reject external DTD references; strip a local DOCTYPE without entities.
        if re.search(br"<!DOCTYPE[^>]*(SYSTEM|PUBLIC)\b", data, re.IGNORECASE | re.DOTALL):
            raise CompositionImportError(
                "import_malformed_source",
                "External XML DTD references are not allowed",
                http_status=422,
                details={"reason": "external_dtd_rejected"},
            )
        text = _DOCTYPE_STRIP_RE.sub("", text, count=1)
        logger.debug("Stripped MusicXML DOCTYPE before parser", extra={"action": "doctype_stripped"})
    if _SCORE_TIMEWISE_RE.search(text.encode("utf-8")) and not _SCORE_PARTWISE_RE.search(text.encode("utf-8")):
        # music21 can convert some timewise scores; still prefer explicit partwise.
        # Reject pure timewise to keep behavior deterministic for V2 import.
        raise CompositionImportError(
            "import_non_representable",
            "score-timewise MusicXML is not accepted; provide score-partwise",
            http_status=422,
            details={"reason": "score_timewise_rejected"},
        )
    return text.encode("utf-8")


def _extract_mxl_score_root(data: bytes, cfg: ImportSettings) -> bytes:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise CompositionImportError(
            "import_malformed_source",
            "MXL archive is malformed",
            http_status=422,
        ) from exc

    infos = archive.infolist()
    if len(infos) > cfg.max_archive_entries:
        raise CompositionImportError(
            "import_payload_too_large",
            "MXL archive entry count exceeds configured limit",
            http_status=413,
            details={"limit": cfg.max_archive_entries, "count": len(infos)},
        )

    total_compressed = 0
    total_expanded = 0
    names: list[str] = []
    for info in infos:
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or name.startswith("../") or "/../" in f"/{name}/":
            raise CompositionImportError(
                "import_malformed_source",
                "MXL archive contains unsafe entry paths",
                http_status=422,
                details={"reason": "zip_slip"},
            )
        if info.flag_bits & 0x1:
            raise CompositionImportError(
                "import_malformed_source",
                "Encrypted MXL entries are not allowed",
                http_status=422,
                details={"reason": "encrypted_entry"},
            )
        lower = name.lower()
        if lower.endswith((".zip", ".mxl")) and not name.endswith("/"):
            raise CompositionImportError(
                "import_malformed_source",
                "Nested archives inside MXL are not allowed",
                http_status=422,
                details={"reason": "nested_archive"},
            )
        total_compressed += max(info.compress_size, 1)
        total_expanded += max(info.file_size, 0)
        if total_expanded > cfg.max_expanded_bytes:
            raise CompositionImportError(
                "import_payload_too_large",
                "MXL expanded size exceeds configured limit",
                http_status=413,
                details={"limit_bytes": cfg.max_expanded_bytes},
            )
        names.append(name)

    ratio = total_expanded / float(max(total_compressed, 1))
    if ratio > cfg.max_compression_ratio:
        raise CompositionImportError(
            "import_payload_too_large",
            "MXL compression ratio exceeds configured limit",
            http_status=413,
            details={"ratio": round(ratio, 2), "limit": cfg.max_compression_ratio},
        )

    root_path = _mxl_rootfile_path(archive, names)
    try:
        payload = archive.read(root_path)
    except KeyError as exc:
        raise CompositionImportError(
            "import_malformed_source",
            "MXL root score file is missing",
            http_status=422,
        ) from exc
    if len(payload) > cfg.max_expanded_bytes:
        raise CompositionImportError(
            "import_payload_too_large",
            "MXL score root exceeds configured expanded byte limit",
            http_status=413,
        )
    logger.debug(
        "MXL container accepted",
        extra={
            "entry_count": len(infos),
            "expanded_bytes": total_expanded,
            "compression_ratio": round(ratio, 2),
        },
    )
    return payload


def _mxl_rootfile_path(archive: zipfile.ZipFile, names: list[str]) -> str:
    container_name = next((n for n in names if n.lower() == "meta-inf/container.xml"), None)
    if container_name is None:
        # Fallback: exactly one xml/musicxml score at archive root.
        candidates = [n for n in names if n.lower().endswith((".xml", ".musicxml")) and "/" not in n.rstrip("/")]
        if len(candidates) == 1:
            return candidates[0]
        raise CompositionImportError(
            "import_malformed_source",
            "MXL container.xml is missing and score root is ambiguous",
            http_status=422,
        )
    try:
        import defusedxml.ElementTree as Det

        root = Det.fromstring(archive.read(container_name))
    except Exception as exc:  # noqa: BLE001
        raise CompositionImportError(
            "import_malformed_source",
            "MXL container.xml is malformed",
            http_status=422,
        ) from exc

    full_paths = [
        node.attrib.get("full-path")
        for node in root.iter()
        if str(node.tag).endswith("rootfile") and node.attrib.get("full-path")
    ]
    if len(full_paths) != 1:
        raise CompositionImportError(
            "import_malformed_source",
            "MXL container must declare exactly one score rootfile",
            http_status=422,
            details={"rootfile_count": len(full_paths)},
        )
    return full_paths[0]


def _parse_with_music21(xml_bytes: bytes):
    try:
        from music21 import converter
    except ImportError as exc:
        raise CompositionImportError(
            "import_dependency_unavailable",
            "music21 is not available for MusicXML import",
            http_status=503,
        ) from exc
    return converter.parse(xml_bytes.decode("utf-8"), format="musicxml")


def _score_to_source(
    score: Any,
    *,
    detected_format: Literal["musicxml", "mxl"],
    settings: ImportSettings,
) -> tuple[ParsedSourceScore, dict[str, Any]]:
    from music21 import chord as m21_chord
    from music21 import dynamics as m21_dynamics
    from music21 import key as m21_key
    from music21 import meter as m21_meter
    from music21 import note as m21_note
    from music21 import stream as m21_stream
    from music21 import tempo as m21_tempo

    working = score
    repeat_expanded = False
    try:
        if hasattr(score, "expandRepeats"):
            expanded = score.expandRepeats()
            if expanded is not None:
                working = expanded
                repeat_expanded = True
    except Exception:  # noqa: BLE001 — treat failed expansion as omit/keep original
        repeat_expanded = False

    if not isinstance(working, m21_stream.Score):
        # Flatten single parts into a score-like iterable of parts.
        parts = [working] if isinstance(working, m21_stream.Part) else list(getattr(working, "parts", []))
    else:
        parts = list(working.parts)

    if not parts:
        raise CompositionImportError(
            "import_malformed_source",
            "MusicXML contains no parts",
            http_status=422,
        )
    if len(parts) > settings.max_tracks:
        raise CompositionImportError(
            "import_complexity_exceeded",
            "MusicXML part count exceeds configured import limit",
            http_status=413,
            details={"limit": settings.max_tracks, "count": len(parts)},
        )

    omitted_grace = 0
    omitted_unsupported = 0
    quarter_lengths: list[Fraction] = []

    # First pass: collect durations for PPQ selection.
    for part in parts:
        for el in part.recurse().notesAndRests:
            if getattr(el, "duration", None) is None:
                continue
            if bool(getattr(el, "duration", None) and getattr(el.duration, "isGrace", False)):
                continue
            ql = Fraction(el.duration.quarterLength).limit_denominator(10_000)
            if ql > 0:
                quarter_lengths.append(ql)
            if isinstance(el, m21_chord.Chord):
                for _ in el.notes:
                    pass

    source_ppq, quantized = _choose_ppq(quarter_lengths, settings.max_ppq, settings.default_target_ppq)

    root_tempo: float | None = None
    root_meter: str | None = None
    root_key: str | None = None
    tempo_changes: list[SourceTempoChange] = []
    meter_changes: list[SourceMeterChange] = []
    key_changes: list[SourceKeyChange] = []
    markers: list[SourceMarker] = []
    tracks: list[SourceTrack] = []
    content_end = 0
    note_count = 0

    # Conductor-like elements from score/parts.
    for el in working.recurse():
        offset = Fraction(el.getOffsetInHierarchy(working)).limit_denominator(10_000)
        tick = _ql_to_tick(offset, source_ppq, quantize=quantized)
        if isinstance(el, m21_tempo.MetronomeMark):
            bpm = float(el.number) if el.number is not None else None
            if bpm is None:
                continue
            if tick == 0 and root_tempo is None:
                root_tempo = bpm
            elif tick > 0:
                tempo_changes.append(SourceTempoChange(tick=tick, bpm=bpm))
        elif isinstance(el, m21_meter.TimeSignature):
            meter = f"{int(el.numerator)}/{int(el.denominator)}"
            if tick == 0 and root_meter is None:
                root_meter = meter
            elif tick > 0:
                meter_changes.append(SourceMeterChange(tick=tick, time_signature=meter))
        elif isinstance(el, m21_key.KeySignature) or isinstance(el, m21_key.Key):
            key_name = _key_to_v2(el)
            if key_name is None:
                omitted_unsupported += 1
                continue
            if tick == 0 and root_key is None:
                root_key = key_name
            elif tick > 0:
                key_changes.append(SourceKeyChange(tick=tick, key=key_name))
        elif el.classes and "RehearsalMark" in el.classes:
            label = str(getattr(el, "content", None) or getattr(el, "text", None) or "").strip()
            if label:
                markers.append(SourceMarker(tick=tick, kind="rehearsal", label=label[:200]))
        elif el.classes and "TextExpression" in el.classes:
            label = str(getattr(el, "content", None) or getattr(el, "text", None) or "").strip()
            if label:
                markers.append(SourceMarker(tick=tick, kind="text", label=label[:200]))

    for part_index, part in enumerate(parts):
        instrument_name = None
        explicit_role = None
        try:
            inst = part.getInstrument(returnDefault=False)
            if inst is not None:
                instrument_name = (inst.instrumentName or inst.instrumentAbbreviation or type(inst).__name__).strip()
        except Exception:  # noqa: BLE001
            instrument_name = None
        part_name = (getattr(part, "partName", None) or getattr(part, "id", None) or f"Part {part_index + 1}")
        part_name = str(part_name)[:120]

        notes: list[SourceNoteEvent] = []
        dynamics_marks: list[SourceDynamicMark] = []
        sustains: list[SourceSustainSpan] = []
        ordinal = 0

        for el in part.recurse():
            offset = Fraction(el.getOffsetInHierarchy(working)).limit_denominator(10_000)
            start_tick = _ql_to_tick(offset, source_ppq, quantize=quantized)

            if isinstance(el, m21_dynamics.Dynamic):
                level = str(el.value or "").strip().lower()
                if level in _DYNAMIC_LEVELS:
                    dynamics_marks.append(SourceDynamicMark(tick=start_tick, level=level))  # type: ignore[arg-type]
                else:
                    omitted_unsupported += 1
                continue

            if el.classes and "PedalMark" in el.classes:
                # music21 pedal marks vary; approximate as short spans when end is unknown.
                omitted_unsupported += 1
                continue

            is_grace = bool(getattr(getattr(el, "duration", None), "isGrace", False))
            if is_grace:
                omitted_grace += 1
                continue

            if isinstance(el, m21_note.Rest):
                continue

            note_like_items: list[Any]
            if isinstance(el, m21_chord.Chord):
                note_like_items = list(el.notes)
                ql = Fraction(el.duration.quarterLength).limit_denominator(10_000)
            elif isinstance(el, m21_note.Note):
                note_like_items = [el]
                ql = Fraction(el.duration.quarterLength).limit_denominator(10_000)
            else:
                # Lyrics, ornaments, wedges, etc.
                if any(cls in getattr(el, "classes", ()) for cls in ("Lyric", "Ornament", "Crescendo", "Diminuendo")):
                    omitted_unsupported += 1
                continue

            if ql <= 0:
                omitted_unsupported += 1
                continue
            duration_ticks = _ql_to_tick(ql, source_ppq, quantize=quantized, minimum_one=True)
            for n in note_like_items:
                if note_count >= settings.max_notes:
                    raise CompositionImportError(
                        "import_complexity_exceeded",
                        "MusicXML note count exceeds configured import limit",
                        http_status=413,
                        details={"limit": settings.max_notes},
                    )
                midi_pitch = int(n.pitch.midi)
                spelling = f"{n.pitch.step}{n.pitch.accidental.modifier if n.pitch.accidental else ''}{n.pitch.octave}"
                velocity = 90
                if n.volume is not None and n.volume.velocity is not None:
                    velocity = int(n.volume.velocity)
                articulations = _map_articulations(getattr(n, "articulations", ()) or ())
                tie_type = None
                tie_group = None
                if n.tie is not None:
                    tie_type = str(n.tie.type)
                    if tie_type not in {"start", "continue", "stop"}:
                        tie_type = None
                    else:
                        tie_group = f"p{part_index}-{midi_pitch}-{spelling}"
                staff = None
                voice = None
                if getattr(n, "stemDirection", None) == "down":
                    pass
                notes.append(
                    SourceNoteEvent(
                        onset_tick=start_tick,
                        duration_ticks=duration_ticks,
                        pitch_midi=midi_pitch,
                        velocity=max(1, min(127, velocity)),
                        source_ordinal=ordinal,
                        pitch_spelling=spelling,
                        staff=staff,
                        voice=voice,
                        articulations=tuple(articulations),
                        tie_type=tie_type,  # type: ignore[arg-type]
                        tie_group_key=tie_group,
                    )
                )
                ordinal += 1
                note_count += 1
                content_end = max(content_end, start_tick + duration_ticks)

        # Collect pedal spanners attached to the part when present.
        try:
            from music21 import spanner as m21_spanner

            for sp in part.recurse().getElementsByClass(m21_spanner.Spanner):
                if "Pedal" in type(sp).__name__ or "PedalMark" in type(sp).__name__:
                    try:
                        start = Fraction(sp.getFirst().getOffsetInHierarchy(working)).limit_denominator(10_000)
                        end = Fraction(sp.getLast().getOffsetInHierarchy(working)).limit_denominator(10_000)
                        start_tick = _ql_to_tick(start, source_ppq, quantize=quantized)
                        end_tick = _ql_to_tick(end, source_ppq, quantize=quantized)
                        if end_tick > start_tick:
                            sustains.append(
                                SourceSustainSpan(start_tick=start_tick, duration_ticks=end_tick - start_tick)
                            )
                    except Exception:  # noqa: BLE001
                        omitted_unsupported += 1
        except Exception:  # noqa: BLE001
            pass

        tracks.append(
            SourceTrack(
                source_track_index=part_index,
                channel=part_index + 1 if part_index + 1 != 10 else 11,
                program=None,
                name=part_name,
                instrument_name=instrument_name or part_name,
                explicit_role=explicit_role,
                notes=notes,
                dynamic_marks=dynamics_marks,
                sustain_pedals=sustains,
            )
        )

    score_out = ParsedSourceScore(
        format=detected_format,
        source_ppq=source_ppq,
        source_divisions=source_ppq,
        content_end_tick=content_end,
        root_tempo=root_tempo,
        root_time_signature=root_meter,
        root_key=root_key,
        tempo_changes=tempo_changes,
        time_signature_changes=meter_changes,
        key_changes=key_changes,
        markers=markers,
        tracks=tracks,
    )
    meta = {
        "omitted_grace": omitted_grace,
        "omitted_unsupported": omitted_unsupported,
        "repeat_expanded": repeat_expanded,
        "timing_quantized": quantized,
        "source_ppq": source_ppq,
    }
    return score_out, meta


def _choose_ppq(
    quarter_lengths: Iterable[Fraction],
    max_ppq: int,
    default_ppq: int,
) -> tuple[int, bool]:
    dens = [max(1, frac.limit_denominator(10_000).denominator) for frac in quarter_lengths]
    if not dens:
        return min(default_ppq, max_ppq), False
    needed = 1
    for denom in dens:
        needed = math.lcm(needed, denom)
        if needed > max_ppq:
            return max_ppq, True
    return needed, False


def _ql_to_tick(
    ql: Fraction,
    ppq: int,
    *,
    quantize: bool,
    minimum_one: bool = False,
) -> int:
    raw = Fraction(ql) * ppq
    if raw.denominator == 1:
        value = int(raw)
    elif quantize:
        value = int(round(float(raw)))
    else:
        # Exact representation expected when not quantizing.
        value = int(raw)
    if minimum_one:
        return max(1, value)
    return max(0, value)


def _map_articulations(items: Iterable[Any]) -> list[str]:
    found: list[str] = []
    for item in items:
        name = type(item).__name__.lower()
        mapped = _ARTICULATION_MAP.get(name)
        if mapped and mapped not in found:
            found.append(mapped)
    return found


def _key_to_v2(el: Any) -> str | None:
    try:
        if hasattr(el, "tonic") and hasattr(el, "mode"):
            tonic = el.tonic.name.replace("-", "b")
            mode = str(el.mode).lower()
            if mode in {"major", "minor"}:
                return f"{tonic} {mode}"
        # KeySignature sharps-only → major tonic approximation.
        if hasattr(el, "asKey"):
            k = el.asKey()
            tonic = k.tonic.name.replace("-", "b")
            mode = str(k.mode).lower()
            if mode in {"major", "minor"}:
                return f"{tonic} {mode}"
    except Exception:  # noqa: BLE001
        return None
    return None


def _attach_musicxml_issues(report: ImportReport, meta: dict[str, Any]) -> None:
    if meta.get("repeat_expanded"):
        report.add_issue(code="repeat_expanded", action="normalized", severity="info")
    if meta.get("timing_quantized"):
        report.add_issue(code="timing_quantized", action="quantized", severity="warning")
    grace = int(meta.get("omitted_grace") or 0)
    if grace:
        report.add_issue(code="grace_note_omitted", action="omitted", count=grace, severity="warning")
    unsupported = int(meta.get("omitted_unsupported") or 0)
    if unsupported:
        report.add_issue(
            code="unsupported_notation_omitted",
            action="omitted",
            count=unsupported,
            severity="warning",
        )
