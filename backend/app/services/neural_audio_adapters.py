"""Explicit composition.v2 → neural engine adapters.

Adapters never invent playable notes for the score. They only prepare
engine inputs (MIDI bytes, melody guide, or text). ``preserves_notes``
is True only for midi_projection; generative engines must not claim it.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.composition_midi import render_midi_with_report


logger = logging.getLogger(__name__)


@dataclass
class AdapterArtifact:
    adapter_kind: str
    preserves_notes: bool
    warnings: list[str] = field(default_factory=list)
    midi_bytes: bytes | None = None
    melody_midi_bytes: bytes | None = None
    prompt_text: str = ""
    instrumentation_summary: str | None = None
    tempo_bpm: float | None = None


def derive_instrumentation_summary(composition: dict[str, Any]) -> str:
    tracks = composition.get("tracks") if isinstance(composition, dict) else None
    if not isinstance(tracks, list):
        return ""
    parts: list[str] = []
    for track in tracks[:16]:
        if not isinstance(track, dict):
            continue
        name = str(track.get("name") or track.get("id") or "track").strip()
        instrument = str(track.get("instrument") or "unknown").strip()
        parts.append(f"{name}:{instrument}")
    return "; ".join(parts)[:2000]


def derive_tempo_bpm(composition: dict[str, Any]) -> float | None:
    raw = composition.get("tempo") if isinstance(composition, dict) else None
    if isinstance(raw, (int, float)) and 20 <= float(raw) <= 400:
        return float(raw)
    return None


def build_text_prompt_bundle(
    composition: dict[str, Any],
    *,
    instructions: str,
    genre: str | None,
    mood: str | None,
    instrumentation_summary: str | None,
    tempo_bpm: float | None,
) -> str:
    """Structured text only — never dumps event arrays."""
    sections = composition.get("sections") if isinstance(composition, dict) else None
    section_names: list[str] = []
    if isinstance(sections, list):
        for section in sections[:12]:
            if isinstance(section, dict) and section.get("name"):
                section_names.append(str(section["name"])[:80])
    bits = [
        f"tempo_bpm={tempo_bpm}" if tempo_bpm is not None else None,
        f"instruments={instrumentation_summary}" if instrumentation_summary else None,
        f"sections={','.join(section_names)}" if section_names else None,
        f"genre={genre}" if genre else None,
        f"mood={mood}" if mood else None,
        f"instructions={instructions.strip()}" if instructions and instructions.strip() else None,
    ]
    return " | ".join(bit for bit in bits if bit)[:4000]


def adapt_midi_projection(composition: dict[str, Any]) -> AdapterArtifact:
    from app.composition_schemas import CompositionV2

    parsed = CompositionV2.model_validate(composition)
    result = render_midi_with_report(parsed)
    warnings = ["midi_projection_lossy"]
    logger.info(
        "Neural audio adapter midi_projection",
        extra={
            "adapter_kind": "midi_projection",
            "preserves_notes": True,
            "warning_codes": warnings,
            "midi_bytes": len(result.midi_bytes),
        },
    )
    return AdapterArtifact(
        adapter_kind="midi_projection",
        preserves_notes=True,
        warnings=warnings,
        midi_bytes=result.midi_bytes,
        instrumentation_summary=derive_instrumentation_summary(composition),
        tempo_bpm=derive_tempo_bpm(composition),
    )


def adapt_melody_conditioning(
    composition: dict[str, Any],
    *,
    instructions: str = "",
    genre: str | None = None,
    mood: str | None = None,
    instrumentation_summary: str | None = None,
    tempo_bpm: float | None = None,
) -> AdapterArtifact:
    """Flatten to a monophonic guide MIDI + text bundle for MusicGen-style engines."""
    mono = _monophonic_composition_view(composition)
    midi_artifact = adapt_midi_projection(mono)
    summary = instrumentation_summary or derive_instrumentation_summary(composition)
    tempo = tempo_bpm if tempo_bpm is not None else derive_tempo_bpm(composition)
    prompt = build_text_prompt_bundle(
        composition,
        instructions=instructions,
        genre=genre,
        mood=mood,
        instrumentation_summary=summary,
        tempo_bpm=tempo,
    )
    warnings = ["generative_approximation", "polyphony_flattened"]
    logger.info(
        "Neural audio adapter melody_conditioning",
        extra={
            "adapter_kind": "melody_conditioning",
            "preserves_notes": False,
            "warning_codes": warnings,
            "prompt_chars": len(prompt),
        },
    )
    logger.debug(
        "Neural audio adapter instruction length",
        extra={"instruction_chars": len(instructions or "")},
    )
    return AdapterArtifact(
        adapter_kind="melody_conditioning",
        preserves_notes=False,
        warnings=warnings,
        melody_midi_bytes=midi_artifact.midi_bytes,
        prompt_text=prompt,
        instrumentation_summary=summary,
        tempo_bpm=tempo,
    )


def adapt_text_prompt(
    composition: dict[str, Any],
    *,
    instructions: str = "",
    genre: str | None = None,
    mood: str | None = None,
    instrumentation_summary: str | None = None,
    tempo_bpm: float | None = None,
) -> AdapterArtifact:
    summary = instrumentation_summary or derive_instrumentation_summary(composition)
    tempo = tempo_bpm if tempo_bpm is not None else derive_tempo_bpm(composition)
    prompt = build_text_prompt_bundle(
        composition,
        instructions=instructions,
        genre=genre,
        mood=mood,
        instrumentation_summary=summary,
        tempo_bpm=tempo,
    )
    warnings = ["generative_approximation", "text_only_conditioning"]
    logger.info(
        "Neural audio adapter text_prompt",
        extra={
            "adapter_kind": "text_prompt",
            "preserves_notes": False,
            "warning_codes": warnings,
            "prompt_chars": len(prompt),
        },
    )
    logger.debug(
        "Neural audio adapter instruction length",
        extra={"instruction_chars": len(instructions or "")},
    )
    return AdapterArtifact(
        adapter_kind="text_prompt",
        preserves_notes=False,
        warnings=warnings,
        prompt_text=prompt,
        instrumentation_summary=summary,
        tempo_bpm=tempo,
    )


def resolve_adapter_kind(
    *,
    requested: str | None,
    model_preferred: str | None,
    fidelity_class: str | None,
) -> str:
    if requested in {"midi_projection", "melody_conditioning", "text_prompt"}:
        return requested
    if model_preferred in {"midi_projection", "melody_conditioning", "text_prompt"}:
        return model_preferred
    if fidelity_class == "neural_instrument":
        return "midi_projection"
    return "text_prompt"


def run_adapter(
    adapter_kind: str,
    composition: dict[str, Any],
    *,
    instructions: str = "",
    genre: str | None = None,
    mood: str | None = None,
    instrumentation_summary: str | None = None,
    tempo_bpm: float | None = None,
) -> AdapterArtifact:
    if adapter_kind == "midi_projection":
        artifact = adapt_midi_projection(composition)
        if tempo_bpm is not None and artifact.tempo_bpm != tempo_bpm:
            artifact.tempo_bpm = tempo_bpm
            artifact.warnings = list(artifact.warnings) + ["tempo_override_applied"]
        if instrumentation_summary:
            artifact.instrumentation_summary = instrumentation_summary
        return artifact
    if adapter_kind == "melody_conditioning":
        return adapt_melody_conditioning(
            composition,
            instructions=instructions,
            genre=genre,
            mood=mood,
            instrumentation_summary=instrumentation_summary,
            tempo_bpm=tempo_bpm,
        )
    return adapt_text_prompt(
        composition,
        instructions=instructions,
        genre=genre,
        mood=mood,
        instrumentation_summary=instrumentation_summary,
        tempo_bpm=tempo_bpm,
    )


def artifact_to_engine_spec(artifact: AdapterArtifact, *, seed: int | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "adapter_kind": artifact.adapter_kind,
        "preserves_notes": artifact.preserves_notes,
        "prompt": artifact.prompt_text,
        "instructions": artifact.prompt_text,
        "tempo_bpm": artifact.tempo_bpm,
        "instrumentation_summary": artifact.instrumentation_summary,
        "seed": seed,
    }
    if artifact.midi_bytes:
        spec["midi_base64"] = base64.b64encode(artifact.midi_bytes).decode("ascii")
    if artifact.melody_midi_bytes:
        spec["melody_midi_base64"] = base64.b64encode(artifact.melody_midi_bytes).decode(
            "ascii"
        )
    return spec


def _monophonic_composition_view(composition: dict[str, Any]) -> dict[str, Any]:
    """Keep highest-pitch concurrent notes per track as a rough melody guide."""
    import copy

    view = copy.deepcopy(composition)
    tracks = view.get("tracks")
    if not isinstance(tracks, list):
        return view
    for track in tracks:
        if not isinstance(track, dict):
            continue
        events = track.get("events")
        if not isinstance(events, list):
            continue
        by_start: dict[int, dict[str, Any]] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            start = int(event.get("start_tick") or 0)
            pitch = event.get("pitch")
            pitch_val = _pitch_sort_key(pitch)
            existing = by_start.get(start)
            if existing is None or pitch_val > _pitch_sort_key(existing.get("pitch")):
                by_start[start] = event
        track["events"] = [by_start[k] for k in sorted(by_start.keys())]
    return view


def _pitch_sort_key(pitch: Any) -> int:
    if isinstance(pitch, int):
        return pitch
    if isinstance(pitch, str) and len(pitch) >= 2:
        # Rough ordering by trailing octave digit when present.
        try:
            return int(pitch[-1]) * 12
        except ValueError:
            return 0
    return 0
