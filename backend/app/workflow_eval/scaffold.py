"""Empty-event scaffolds and the preservation source for one case."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.schemas import LLMMusicSection, LLMPromptParameters
from app.services.generation_constraints import build_generation_constraints
from app.workflow_eval.schemas import ArmId, BenchmarkCaseV1

logger = logging.getLogger(__name__)

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
_SUITE_DIR = _FIXTURE_DIR / "workflow_benchmark"

# Role, GM program, drum flag. Unknown names stay melody / program 0.
_INSTRUMENT_TRACKS: dict[str, tuple[str, int, bool]] = {
    "piano": ("melody", 0, False),
    "violin": ("melody", 40, False),
    "viola": ("harmony", 41, False),
    "cello": ("bass", 42, False),
    "flute": ("melody", 73, False),
    "oboe": ("melody", 68, False),
    "clarinet": ("melody", 71, False),
    "bassoon": ("bass", 70, False),
    "trumpet": ("lead", 56, False),
    "french horn": ("harmony", 60, False),
    "horn": ("harmony", 60, False),
    "trombone": ("harmony", 57, False),
    "acoustic guitar": ("harmony", 24, False),
    "strings": ("pad", 48, False),
}

# Harmonic briefs put the second track in the chordal family when the map does not.
_SECOND_TRACK_HARMONY = {"harmonic"}


def _beats_per_bar(time_signature: str) -> int:
    try:
        return max(1, int(str(time_signature).split("/", 1)[0]))
    except (TypeError, ValueError):
        return 4


def _track_spec(instrument: str, index: int, kind: str) -> tuple[str, int, bool]:
    key = instrument.strip().lower()
    role, program, drum = _INSTRUMENT_TRACKS.get(key, ("melody", 0, False))
    if index == 1 and kind in _SECOND_TRACK_HARMONY and role == "melody":
        role = "harmony"
    return role, program, drum


def prompt_parameters(case: BenchmarkCaseV1) -> LLMPromptParameters:
    sections = [LLMMusicSection.model_validate(section) for section in case.prompt.sections]
    return LLMPromptParameters(
        mood=case.prompt.mood,
        genre=case.prompt.genre,
        tempo_min=case.prompt.tempo_min,
        tempo_max=case.prompt.tempo_max,
        key=case.prompt.key,
        time_signature=case.prompt.time_signature,
        instruments=list(case.prompt.instruments),
        sections=sections,
        complexity=case.prompt.complexity,
        duration_bars=case.prompt.duration_bars,
        instructions=case.prompt.instructions,
    )


def scaffold_from_case(case: BenchmarkCaseV1) -> CompositionV2:
    """Legal V2 timeline with ``events: []``. Not a melody."""
    logger.debug("scaffold_from_case entry", extra={"case_id": case.id})
    prompt = prompt_parameters(case)
    beats = _beats_per_bar(prompt.time_signature)
    ticks_per_quarter = 480
    bar_ticks = beats * ticks_per_quarter
    sections: list[dict] = []
    if prompt.sections:
        start_bar = 1
        for index, section in enumerate(prompt.sections, start=1):
            sections.append(
                {
                    "id": f"section-{index}",
                    "type": section.type,
                    "start_bar": start_bar,
                    "bar_count": section.bars,
                    "start_tick": (start_bar - 1) * bar_ticks,
                    "duration_ticks": section.bars * bar_ticks,
                }
            )
            start_bar += section.bars
    else:
        sections.append(
            {
                "id": "section-1",
                "type": "verse",
                "start_bar": 1,
                "bar_count": prompt.duration_bars,
                "start_tick": 0,
                "duration_ticks": prompt.duration_bars * bar_ticks,
            }
        )
    tracks = []
    for index, instrument in enumerate(prompt.instruments):
        role, program, drum = _track_spec(instrument, index, case.kind)
        channel = 10 if drum else (index + 1 if index < 9 else index + 2)
        tracks.append(
            {
                "id": f"track-{index + 1}",
                "name": instrument,
                "instrument": instrument,
                "role": role,
                "midi_program": program,
                "channel": channel,
                "is_drum": drum,
                "events": [],
            }
        )
    tempo = prompt.tempo_min
    document = {
        "schema_version": "composition.v2",
        "tempo": tempo,
        "key": prompt.key or "C major",
        "time_signature": prompt.time_signature,
        "ticks_per_quarter": ticks_per_quarter,
        "duration_ticks": prompt.duration_bars * bar_ticks,
        "bar_count": prompt.duration_bars,
        "sections": sections,
        "tracks": tracks,
        "harmony": [],
    }
    composition = CompositionV2.model_validate(document)
    logger.info(
        "Scaffold built",
        extra={
            "case_id": case.id,
            "track_count": len(composition.tracks),
            "bar_count": composition.bar_count,
        },
    )
    logger.debug(
        "Scaffold sections",
        extra={"section_types": [section.type for section in composition.sections]},
    )
    return composition


def _fixture_path(name: str) -> Path:
    direct = _SUITE_DIR / name
    if direct.is_file():
        return direct
    sibling = _FIXTURE_DIR / name
    return sibling


def preservation_source(case: BenchmarkCaseV1) -> CompositionV2 | None:
    """Expressive fixture for the preservation case. Other cases have no source."""
    if not case.preservation_fixture:
        return None
    path = _fixture_path(case.preservation_fixture)
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = CompositionV2.model_validate(payload)
    logger.info(
        "Preservation source loaded",
        extra={"case_id": case.id, "bar_count": source.bar_count},
    )
    return source


def preservation_applies(case: BenchmarkCaseV1, arm: ArmId) -> bool:
    """Only the iterative arm on the preservation case can score preservation."""
    return case.kind == "revision-preservation" and arm == "v4_iterative_revision"


def case_constraints(case: BenchmarkCaseV1):
    """Hard constraints from the brief, without logging instruction text."""
    from app.schemas import LLMMusicGenerationRequest

    request = LLMMusicGenerationRequest(prompt=prompt_parameters(case))
    return build_generation_constraints(
        request,
        allow_extra_instrument_families=case.allow_extra_instrument_families,
    )
