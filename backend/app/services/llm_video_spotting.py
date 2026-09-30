"""Explicit spotting suggestion preview. It does not write cues or note events."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.ai_runtime.operations import AiOperation
from app.llm_settings import LLMSettings, load_llm_settings
from app.services.fake_llm import fake_spotting_suggestion_draft, is_fake_provider
from app.services.video_scoring_map import parse_timecode
from app.video_scoring_schemas import (
    HitPointV1,
    SpottingSuggestionDraftV1,
    VideoScoringError,
    is_closed_frame_rate,
)

logger = logging.getLogger(__name__)


async def suggest_spotting_cues(
    *,
    project_id: str,
    brief: str,
    duration_seconds: float,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
    cues: list[HitPointV1],
    settings: LLMSettings | None = None,
) -> tuple[list[SpottingSuggestionDraftV1], str | None]:
    """Return cue drafts. Invalid model items are dropped."""
    logger.info(
        "spotting suggest started",
        extra={"project_id": project_id, "brief_length": len(brief), "cue_count": len(cues)},
    )
    if not is_closed_frame_rate(frame_rate_numerator, frame_rate_denominator):
        logger.warning("video_frame_rate_required", extra={"error_code": "video_frame_rate_required"})
        raise VideoScoringError("video_frame_rate_required")
    active = settings or load_llm_settings()
    if not active.providers:
        logger.warning(
            "video_spotting_model_unavailable",
            extra={"error_code": "video_spotting_model_unavailable", "project_id": project_id},
        )
        raise VideoScoringError("video_spotting_model_unavailable")
    provider = active.providers[0]
    if is_fake_provider(provider):
        raw_items: list[Any] = [fake_spotting_suggestion_draft()]
    else:
        raw_items = await _model_items(
            brief=brief,
            duration_seconds=duration_seconds,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            timecode_mode=timecode_mode,
            start_timecode=start_timecode,
            cues=cues,
            settings=active,
        )
    kept, saw_item = _keep_usable(
        raw_items,
        frame_rate_numerator=frame_rate_numerator,
        frame_rate_denominator=frame_rate_denominator,
        timecode_mode=timecode_mode,
        start_timecode=start_timecode,
    )
    warning = None
    if not kept and (saw_item or not raw_items):
        warning = "video_spotting_unparsed"
        logger.warning("video_spotting_unparsed", extra={"error_code": warning, "project_id": project_id})
    for item in kept:
        logger.debug(
            "spotting suggestion kept",
            extra={
                "kind": item.kind,
                "timecode": item.timecode,
                "tolerance_frames": item.tolerance_frames,
                "importance": item.importance,
            },
        )
    logger.info(
        "spotting suggest finished",
        extra={
            "project_id": project_id,
            "brief_length": len(brief),
            "suggestion_count": len(kept),
            "warning_code": warning,
        },
    )
    return kept, warning


def _keep_usable(
    raw_items: list[Any],
    *,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
) -> tuple[list[SpottingSuggestionDraftV1], bool]:
    kept: list[SpottingSuggestionDraftV1] = []
    saw_item = False
    for item in raw_items:
        if not isinstance(item, dict):
            saw_item = True
            continue
        saw_item = True
        try:
            draft = SpottingSuggestionDraftV1.model_validate(item)
            frames = parse_timecode(
                draft.timecode,
                frame_rate_numerator=frame_rate_numerator,
                frame_rate_denominator=frame_rate_denominator,
                timecode_mode=timecode_mode,
            ) - parse_timecode(
                start_timecode,
                frame_rate_numerator=frame_rate_numerator,
                frame_rate_denominator=frame_rate_denominator,
                timecode_mode=timecode_mode,
            )
        except (ValidationError, VideoScoringError, ValueError):
            continue
        if frames < 0:
            continue
        kept.append(draft)
    return kept, saw_item


async def _model_items(
    *,
    brief: str,
    duration_seconds: float,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
    cues: list[HitPointV1],
    settings: LLMSettings,
) -> list[Any]:
    from app.services.llm_chat_client import ainvoke_chat_text, build_chat_openai
    from app.services.llm_music_generator import LLMGenerationError, select_llm_provider

    try:
        provider = select_llm_provider(
            provider=None,
            model=None,
            model_id=None,
            settings=settings,
            operation=AiOperation.SPOTTING_SUGGEST,
        )
        client = build_chat_openai(provider, temperature=0.2, streaming=False)
        text = await ainvoke_chat_text(client, _prompt(
            brief=brief,
            duration_seconds=duration_seconds,
            frame_rate_numerator=frame_rate_numerator,
            frame_rate_denominator=frame_rate_denominator,
            timecode_mode=timecode_mode,
            start_timecode=start_timecode,
            cues=cues,
        ), purpose="spotting_suggest")
    except (LLMGenerationError, OSError, TimeoutError, RuntimeError) as exc:
        logger.warning(
            "video_spotting_model_unavailable",
            extra={"error_code": "video_spotting_model_unavailable", "error_type": type(exc).__name__},
        )
        raise VideoScoringError("video_spotting_model_unavailable") from exc
    return _decode_items(text)


def _decode_items(text: str) -> list[Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [{}]
    if isinstance(payload, dict):
        payload = payload.get("suggestions", [])
    if not isinstance(payload, list):
        return [{}]
    return payload


def _prompt(
    *,
    brief: str,
    duration_seconds: float,
    frame_rate_numerator: int,
    frame_rate_denominator: int,
    timecode_mode: str,
    start_timecode: str,
    cues: list[HitPointV1],
) -> str:
    cue_rows = [
        {
            "kind": cue.kind,
            "timecode": cue.timecode,
            "importance": cue.importance,
            "instruction": cue.instruction,
            "label": cue.label,
        }
        for cue in cues
    ]
    body = {
        "duration_seconds": duration_seconds,
        "frame_rate_numerator": frame_rate_numerator,
        "frame_rate_denominator": frame_rate_denominator,
        "timecode_mode": timecode_mode,
        "start_timecode": start_timecode,
        "brief": brief,
        "cues": cue_rows,
    }
    return (
        "Return JSON {\"suggestions\":[{\"kind\",\"label\",\"timecode\",\"tolerance_frames\","
        "\"importance\",\"instruction\"}]}. Timecode is HH:MM:SS:FF. "
        "Do not include note events or file paths.\n"
        + json.dumps(body)
    )
