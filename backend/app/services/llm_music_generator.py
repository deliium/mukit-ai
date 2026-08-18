import json
import logging
from typing import Any, TypedDict

from pydantic import ValidationError

from ..llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from ..schemas import LLMMusicGenerationRequest, LLMMusicJson


logger = logging.getLogger(__name__)


class LLMGenerationError(RuntimeError):
    pass


class NoLLMProviderConfiguredError(LLMGenerationError):
    pass


class UnsupportedLLMProviderError(LLMGenerationError):
    pass


class InvalidLLMOutputError(LLMGenerationError):
    pass


class _GenerationState(TypedDict, total=False):
    request: LLMMusicGenerationRequest
    provider: LLMProviderSettings
    raw_output: str
    parsed_json: dict[str, Any]
    music: LLMMusicJson
    retry_count: int
    warnings: list[str]


async def generate_music_json(
    request: LLMMusicGenerationRequest,
    settings: LLMSettings | None = None,
) -> tuple[LLMMusicJson, list[str], LLMProviderSettings]:
    active_settings = settings or load_llm_settings()
    provider = _select_provider(request, active_settings)
    retry_limit = request.options.max_retries

    logger.info(
        "LLM music generation started",
        extra={"provider": provider.provider, "model": _selected_model(request, provider)},
    )
    logger.debug("LLM prompt parameters", extra={"prompt": _sanitized_prompt(request)})

    state: _GenerationState = {
        "request": request,
        "provider": provider,
        "retry_count": 0,
        "warnings": [],
    }

    while True:
        try:
            graph = _build_generation_graph()
            result = await graph.ainvoke(state)
            music = result["music"]
            warnings = result.get("warnings", [])
            logger.info(
                "LLM music generation completed",
                extra={
                    "provider": provider.provider,
                    "model": _selected_model(request, provider),
                    "validation_retry_count": state["retry_count"],
                },
            )
            return music, warnings, provider
        except InvalidLLMOutputError as exc:
            if state["retry_count"] >= retry_limit:
                logger.error(
                    "LLM music generation validation failed",
                    extra={"error_type": type(exc).__name__, "retry_count": state["retry_count"]},
                )
                raise
            state["retry_count"] += 1
            state.setdefault("warnings", []).append("LLM returned invalid JSON; retried with correction prompt.")
            logger.warning("Retrying invalid LLM JSON output", extra={"retry_count": state["retry_count"]})
        except LLMGenerationError:
            raise
        except Exception as exc:
            logger.error(
                "LLM provider/API failure",
                extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
            )
            raise LLMGenerationError(f"LLM provider request failed: {type(exc).__name__}") from exc


def _select_provider(request: LLMMusicGenerationRequest, settings: LLMSettings) -> LLMProviderSettings:
    if not settings.providers:
        logger.warning("LLM generation requested without configured providers")
        raise NoLLMProviderConfiguredError("No LLM providers are configured")

    requested_provider = request.selection.provider or settings.default_provider
    requested_model = request.selection.model
    for provider in settings.providers:
        if provider.provider == requested_provider:
            if requested_model and requested_model != provider.model:
                return LLMProviderSettings(
                    provider=provider.provider,
                    model=requested_model,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    is_default=provider.is_default,
                )
            return provider

    logger.warning("Unsupported LLM provider requested", extra={"provider": requested_provider})
    raise UnsupportedLLMProviderError(f"Unsupported or unavailable LLM provider: {requested_provider}")


def _build_generation_graph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise LLMGenerationError("LangGraph dependencies are not installed") from exc

    workflow = StateGraph(_GenerationState)
    workflow.add_node("call_llm", _call_llm)
    workflow.add_node("parse_and_validate", _parse_and_validate)
    workflow.set_entry_point("call_llm")
    workflow.add_edge("call_llm", "parse_and_validate")
    workflow.add_edge("parse_and_validate", END)
    return workflow.compile()


async def _call_llm(state: _GenerationState) -> _GenerationState:
    request = state["request"]
    provider = state["provider"]
    prompt = _build_prompt(request, state.get("raw_output"))

    logger.debug(
        "Calling LLM for music JSON",
        extra={"provider": provider.provider, "model": _selected_model(request, provider)},
    )

    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMGenerationError("LangChain OpenAI dependencies are not installed") from exc

    timeout_seconds = request.options.timeout_seconds or load_llm_settings().request_timeout_seconds
    temperature = request.options.temperature
    if temperature is None:
        temperature = load_llm_settings().temperature

    client = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=_selected_model(request, provider),
        temperature=temperature,
        timeout=timeout_seconds,
    )
    response = await client.ainvoke(prompt)
    raw_output = getattr(response, "content", str(response))
    logger.debug("LLM raw response received", extra={"response_length": len(raw_output)})
    return {**state, "raw_output": raw_output}


def _parse_and_validate(state: _GenerationState) -> _GenerationState:
    raw_output = state.get("raw_output", "")
    try:
        parsed_json = _extract_json(raw_output)
        music = LLMMusicJson.model_validate(parsed_json)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        logger.debug(
            "Invalid LLM music JSON",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:300]},
        )
        raise InvalidLLMOutputError("LLM returned invalid music JSON") from exc

    logger.debug("Validated LLM music JSON", extra={"json_keys": sorted(parsed_json.keys())})
    return {**state, "parsed_json": parsed_json, "music": music}


def _extract_json(raw_output: str) -> dict[str, Any]:
    content = raw_output.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in LLM output")

    parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM output JSON must be an object")
    return parsed


def _build_prompt(request: LLMMusicGenerationRequest, invalid_output: str | None = None) -> str:
    prompt = request.prompt
    sections = [section.model_dump() for section in prompt.sections]
    correction = ""
    if invalid_output:
        correction = (
            "\nThe previous response was invalid. Return corrected JSON only. "
            "Do not include markdown fences, comments, or explanatory text."
        )

    return f"""
You are a music composition JSON generator. Return only valid JSON matching this schema:
{{
  "tempo": 92,
  "key": "C minor",
  "time_signature": "4/4",
  "sections": [{{"type": "intro", "bars": 4}}],
  "tracks": [{{"instrument": "piano", "role": "harmony"}}],
  "harmony": [{{"bar": 1, "chord": "Cm"}}],
  "notes": [
    {{"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "C4", "duration": 1}},
    {{"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "Eb4", "duration": 1}},
    {{"track": 1, "staff": "treble", "bar": 1, "beat": 1, "pitch": "G4", "duration": 1}},
    {{"track": 1, "staff": "bass", "bar": 1, "beat": 1, "pitch": "C3", "duration": 2}}
  ]
}}

Constraints:
- tempo must be between {prompt.tempo_min} and {prompt.tempo_max}
- key must be formatted like "C minor" or "F# major"
- time_signature is the composition dimension/meter and must be "numerator/denominator", for example "3/4" or "4/4"
- sections must use common roles: intro, verse, pre_chorus, chorus, bridge, solo, breakdown, outro
- tracks must be non-empty and use roles: melody, harmony, bass, drums, percussion, countermelody, pad, lead, rhythm
- harmony bars must fit within the total section length
- for piano, use one track only: {{"instrument": "piano", "role": "harmony"}}; do not create separate melody and harmony tracks for right and left hands
- for piano, notes must use staff "treble" for right hand and staff "bass" for left hand
- piano notation is rendered as two staves, treble clef and bass clef, joined by one brace with one piano title
- harmony must contain chord names for the notation preview, one chord per harmonic change; these render as chord letters above the treble staff, not as noteheads
- notes must contain the actual playable notes for the notation preview
- every note must reference an existing 1-based track number from tracks
- every note pitch must use scientific pitch notation with octave, for example C4, F#3, Bb4
- spell note pitches according to the selected key signature/tonality; use accidentals only for notes outside the key
- keep piano treble staff notes mostly C4 through C6 and bass staff notes mostly C2 through B3 unless requested otherwise
- duration is measured in quarter-note units: 1 = quarter, 2 = half, 4 = whole, 0.5 = eighth
- beat is 1-based within the bar; notes with the same track, bar, beat, and duration are rendered as a chord
- notes must fit inside each bar according to time_signature
- each bar on each piano staff must be rhythmically complete: in 4/4 every staff must total exactly 4 quarter-note units, in 3/4 exactly 3, in 6/8 exactly 3
- if a staff has silence for part of a bar, include no note for that span; the renderer will show rests, but plan the rhythm as if rests fill the full bar
- do not leave a 4/4 bar with only 2 beats of notes unless the remaining 2 beats are intentional silence/rests
- for each piano bar, provide treble staff events and bass staff events whose beats/durations make a complete measure when rests are included
- include enough notes to make every non-percussion track visible in notation, not only chord names
- return JSON only

User parameters:
- genre: {prompt.genre}
- mood: {prompt.mood}
- key: {prompt.key or "choose one that fits"}
- time_signature: {prompt.time_signature}
- instruments: {", ".join(prompt.instruments)}
- sections: {json.dumps(sections) if sections else "choose a coherent structure"}
- complexity: {prompt.complexity}
- duration_bars: {prompt.duration_bars}
- instructions: {prompt.instructions or "none"}
{correction}
""".strip()


def _selected_model(request: LLMMusicGenerationRequest, provider: LLMProviderSettings) -> str:
    return request.selection.model or provider.model


def _sanitized_prompt(request: LLMMusicGenerationRequest) -> dict[str, Any]:
    data = request.prompt.model_dump()
    if data.get("instructions"):
        data["instructions"] = str(data["instructions"])[:200]
    return data
