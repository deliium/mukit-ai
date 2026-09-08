import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn

from .composition_schemas import CompositionV2, UnsupportedSchemaVersionError
from .db import ensure_database
from .llm_settings import load_llm_settings
from .ready import build_readiness_report, configure_logging, parse_cors_allow_origins
from .routers.projects import router as projects_router
from .routers.imports import router as imports_router
from .schemas import (
    Composition,
    LLMCompositionEditRequest,
    LLMCompositionEditResponse,
    LLMMusicGenerationRequest,
    LLMMusicGenerationResponse,
    LLMModelsResponse,
    LLMProviderModel,
)
from .services.composition_migration import CompositionMigrationError
from .services.composition_midi import CompositionMidiError, render_midi_with_report
from .services.composition_normalizer import CompositionNormalizationError, normalize_composition_json
from .services.composition_wav import CompositionWavError, load_wav_renderer_config, render_wav_with_report
from .services.composition_planner import OversizedLLMGenerationRequestError
from .services.llm_composition_editor import edit_composition_region
from .services.llm_music_generator import (
    GenerationConstraintViolationError,
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    generate_music_json,
)
from .services.composition_projection import (
    PROJECTION_EXPOSE_HEADERS,
    projection_issues_as_warnings,
    projection_response_headers,
)
from .services.music_json_renderer import MusicJsonRenderError, render_musicxml


logger = logging.getLogger(__name__)

# Apply LOG_LEVEL before other modules emit startup logs.
_CONFIGURED_LOG_LEVEL = configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db_path = ensure_database()
    llm_settings = load_llm_settings()
    provider_names = [provider.provider for provider in llm_settings.providers]
    logger.info(
        "Application startup database ready",
        extra={"project_db_path": str(db_path), "log_level": _CONFIGURED_LOG_LEVEL},
    )
    logger.info(
        "Application startup LLM providers configured",
        extra={
            "providers": provider_names,
            "default_provider": llm_settings.default_provider,
            "provider_count": len(provider_names),
        },
    )
    if not provider_names:
        logger.warning("No LLM providers configured; generate/edit routes will return 503")
    yield
    logger.info("Application shutdown")


app = FastAPI(title="LLM Music Composer API", version="1.0.0", lifespan=lifespan)

_cors_origins = parse_cors_allow_origins()
logger.debug("Installing CORS middleware", extra={"origins": _cors_origins})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=list(PROJECTION_EXPOSE_HEADERS),
)

app.include_router(projects_router)
app.include_router(imports_router)


def _composition_export_summary(composition: CompositionV2) -> dict:
    return {
        "schema_version": composition.schema_version,
        "track_count": len(composition.tracks),
        "event_count": sum(len(track.events) for track in composition.tracks),
        "duration_ticks": composition.duration_ticks,
        "tempo": composition.tempo,
        "time_signature": composition.time_signature,
    }


def _safe_export_filename(composition: CompositionV2, extension: str) -> str:
    raw = f"composition-{composition.key}-{composition.tempo}bpm".lower()
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe.strip('-') or 'composition'}.{extension}"


def _normalize_export_composition(payload: dict[str, Any]) -> CompositionV2:
    try:
        return normalize_composition_json(payload)
    except (
        CompositionNormalizationError,
        CompositionMigrationError,
        UnsupportedSchemaVersionError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc


def _export_response_headers(*extra: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in extra:
        headers.update(item)
    return headers


@app.get("/")
async def root():
    return {"message": "Music Composer API is running!"}

@app.get("/health")
async def health_check():
    """Liveness probe — process is up."""
    return {"status": "healthy"}


@app.get("/ready")
async def readiness_check():
    """Readiness probe — DB openable; LLM/WAV reported as non-secret flags."""
    report = build_readiness_report()
    if not report.get("ready"):
        raise HTTPException(status_code=503, detail=report)
    return report

@app.get("/llm/models", response_model=LLMModelsResponse)
async def get_llm_models():
    """Return configured LLM provider/model options without exposing API keys."""
    logger.info("LLM model discovery requested")
    settings = load_llm_settings()
    logger.debug(
        "LLM provider availability summary",
        extra={"providers": [provider.provider for provider in settings.providers]},
    )

    from .services.fake_llm import FAKE_DISPLAY_NAME, is_fake_provider

    models = [
        LLMProviderModel(
            provider=provider.provider,
            model=provider.model,
            display_name=(
                FAKE_DISPLAY_NAME
                if is_fake_provider(provider)
                else f"{provider.provider.title()} ({provider.model})"
            ),
            is_default=provider.is_default,
        )
        for provider in settings.providers
    ]
    default_model = next((provider.model for provider in settings.providers if provider.is_default), None)
    warnings = (
        []
        if models
        else [
            "No LLM providers configured. Set OPENAI_API_KEY or DEEPSEEK_API_KEY, "
            "or enable LLM_FAKE_MODE=1 for credit-free demos/tests."
        ]
    )

    return LLMModelsResponse(
        models=models,
        default_provider=settings.default_provider,
        default_model=default_model,
        warnings=warnings,
    )

@app.post("/llm/generate-music-json", response_model=LLMMusicGenerationResponse)
async def generate_llm_music_json(request: LLMMusicGenerationRequest):
    """Generate validated structured music JSON and derived MusicXML with an LLM."""
    logger.info(
        "LLM music JSON request started",
        extra={"provider": request.selection.provider, "model": request.selection.model},
    )
    logger.debug(
        "LLM music JSON request parameters",
        extra={
            "genre": request.prompt.genre,
            "mood": request.prompt.mood,
            "tempo_min": request.prompt.tempo_min,
            "tempo_max": request.prompt.tempo_max,
            "track_count": len(request.prompt.instruments),
        },
    )

    try:
        settings = load_llm_settings()
        music, warnings, provider, validation = await generate_music_json(request, settings)
        musicxml, render_report = render_musicxml(music)
        all_warnings = [*warnings, *projection_issues_as_warnings(render_report)]
        logger.info(
            "LLM music JSON request completed",
            extra={
                "provider": provider.provider,
                "model": provider.model,
                "schema_version": music.schema_version,
                "warning_count": len(all_warnings),
                "validation_status": validation.status if validation else None,
            },
        )
        logger.debug(
            "LLM music JSON response shape",
            extra={
                "schema_version": music.schema_version,
                "sections": len(music.sections),
                "tracks": len(music.tracks),
                "harmony": len(music.harmony),
                "track_ids": [track.id for track in music.tracks],
                "events": sum(len(track.events) for track in music.tracks),
                "duration_ticks": music.duration_ticks,
                "musicxml_length": len(musicxml),
                "validation_error_count": len(validation.errors) if validation else 0,
            },
        )
        return LLMMusicGenerationResponse(
            music=music,
            provider=provider.provider,
            model=provider.model,
            musicxml=musicxml,
            warnings=all_warnings,
            validation=validation,
        )
    except OversizedLLMGenerationRequestError as exc:
        logger.warning(
            "LLM generation request rejected as oversized",
            extra={"code": "oversized_generation_request", "detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning("LLM provider unavailable", extra={"reason": "no_configured_providers"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning("LLM provider unavailable", extra={"reason": "unsupported_provider"})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GenerationConstraintViolationError as exc:
        detail = {
            "message": str(exc)[:500],
            "codes": [item.code for item in exc.diagnostics if item.severity == "error"],
            "errors": [
                {
                    "code": item.code,
                    "message": item.message,
                    "expected": item.context.get("expected"),
                    "actual": item.context.get("actual"),
                    "stage": item.context.get("stage"),
                }
                for item in exc.diagnostics
                if item.severity == "error"
            ][:20],
            "validation": exc.report.model_dump() if exc.report else None,
        }
        logger.warning(
            "Generation constraint violation returned as structured 502",
            extra={
                "codes": detail["codes"],
                "error_count": len(detail["errors"]),
            },
        )
        raise HTTPException(status_code=502, detail=detail) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Invalid or non-playable LLM output could not be corrected",
            extra={"detail": str(exc)[:300]},
        )
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    except MusicJsonRenderError as exc:
        logger.error("MusicXML rendering failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Generated JSON could not be rendered as MusicXML") from exc
    except LLMGenerationError as exc:
        logger.error(
            "LLM music JSON request failed",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/llm/edit-composition-region", response_model=LLMCompositionEditResponse)
async def edit_llm_composition_region(request: LLMCompositionEditRequest):
    """Apply an AI replace_region edit to a selected Composition V1 bar/track range."""
    selection = request.edit.selection
    logger.info(
        "LLM composition region edit request started",
        extra={
            "provider": request.selection.provider,
            "model": request.selection.model,
            "start_bar": selection.start_bar,
            "end_bar": selection.end_bar,
            "target_track_count": len(selection.track_ids or []),
            "bar_count": request.composition.bar_count,
            "track_count": len(request.composition.tracks),
            "instruction_length": len(request.edit.instruction),
        },
    )
    logger.debug(
        "LLM composition region edit request shape",
        extra={
            "schema_version": request.composition.schema_version,
            "track_ids": selection.track_ids,
            "section_type": selection.section_type,
            "allow_harmony_changes": request.edit.allow_harmony_changes,
            "allow_added_tracks": request.edit.allow_added_tracks,
        },
    )

    try:
        settings = load_llm_settings()
        composition, patch, warnings, provider = await edit_composition_region(request, settings)
        musicxml, render_report = render_musicxml(composition)
        all_warnings = [*warnings, *projection_issues_as_warnings(render_report), *patch.warnings]
        logger.info(
            "LLM composition region edit request completed",
            extra={
                "provider": provider.provider,
                "model": provider.model,
                "schema_version": composition.schema_version,
                "operation": patch.operation,
                "start_bar": patch.start_bar,
                "end_bar": patch.end_bar,
                "warning_count": len(all_warnings),
                "event_count": sum(len(track.events) for track in composition.tracks),
            },
        )
        logger.debug(
            "LLM composition region edit response shape",
            extra={
                "schema_version": composition.schema_version,
                "replace_track_count": len(patch.replace_tracks),
                "added_track_count": len(patch.added_tracks),
                "has_harmony_patch": patch.harmony_patch is not None,
                "musicxml_length": len(musicxml),
                "track_ids": [track.id for track in composition.tracks],
            },
        )
        return LLMCompositionEditResponse(
            composition=composition,
            patch=patch,
            provider=provider.provider,
            model=provider.model,
            musicxml=musicxml,
            warnings=all_warnings,
        )
    except NoLLMProviderConfiguredError as exc:
        logger.warning(
            "LLM region edit provider unavailable",
            extra={"reason": "no_configured_providers"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning(
            "LLM region edit provider unavailable",
            extra={"reason": "unsupported_provider"},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Invalid LLM region edit patch could not be corrected",
            extra={"detail": str(exc)[:300]},
        )
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML rendering failed after successful region edit",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=500, detail="Edited composition could not be rendered as MusicXML") from exc
    except LLMGenerationError as exc:
        logger.error(
            "LLM composition region edit request failed",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/export/musicxml")
async def export_musicxml(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to a downloadable MusicXML file."""
    composition = _normalize_export_composition(payload)
    summary = _composition_export_summary(composition)
    logger.info("MusicXML export request started", extra={"format": "musicxml", **summary})
    try:
        musicxml, report = render_musicxml(composition)
        filename = _safe_export_filename(composition, "musicxml")
        if report.issues:
            logger.warning(
                "MusicXML export completed with projection issues",
                extra={"format": "musicxml", **report.summary_extra(), **summary},
            )
        logger.info(
            "MusicXML export request completed",
            extra={"format": "musicxml", "byte_length": len(musicxml.encode("utf-8")), **summary, **report.summary_extra()},
        )
        logger.debug(
            "MusicXML export response metadata",
            extra={"filename": filename, "musicxml_length": len(musicxml), **report.summary_extra()},
        )
        return Response(
            content=musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
            headers=_export_response_headers(
                {"Content-Disposition": f'attachment; filename="{filename}"'},
                projection_response_headers(report),
            ),
        )
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML export failed",
            extra={"format": "musicxml", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MusicXML") from exc


@app.post("/export/musicxml/preview")
async def export_musicxml_preview(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to MusicXML text without download headers."""
    composition = _normalize_export_composition(payload)
    summary = _composition_export_summary(composition)
    logger.info("MusicXML preview render started", extra={"format": "musicxml_preview", **summary})
    try:
        musicxml, report = render_musicxml(composition)
        if report.issues:
            logger.warning(
                "MusicXML preview completed with projection issues",
                extra={"format": "musicxml_preview", **report.summary_extra(), **summary},
            )
        logger.info(
            "MusicXML preview render completed",
            extra={
                "format": "musicxml_preview",
                "musicxml_length": len(musicxml),
                **summary,
                **report.summary_extra(),
            },
        )
        return Response(
            content=musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
            headers=projection_response_headers(report),
        )
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML preview render failed",
            extra={"format": "musicxml_preview", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MusicXML") from exc


@app.post("/export/midi")
async def export_midi(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to a downloadable Standard MIDI File."""
    composition = _normalize_export_composition(payload)
    summary = _composition_export_summary(composition)
    logger.info("MIDI export request started", extra={"format": "midi", **summary})
    try:
        result = render_midi_with_report(composition)
        midi_bytes = result.midi_bytes
        report = result.report
        filename = _safe_export_filename(composition, "mid")
        if report.issues:
            logger.warning(
                "MIDI export completed with projection issues",
                extra={"format": "midi", **report.summary_extra(), **summary},
            )
        logger.info(
            "MIDI export request completed",
            extra={"format": "midi", "byte_length": len(midi_bytes), **summary, **report.summary_extra()},
        )
        logger.debug(
            "MIDI export response metadata",
            extra={"filename": filename, "byte_length": len(midi_bytes), **report.summary_extra()},
        )
        return Response(
            content=midi_bytes,
            media_type="audio/midi",
            headers=_export_response_headers(
                {"Content-Disposition": f'attachment; filename="{filename}"'},
                projection_response_headers(report),
            ),
        )
    except CompositionMidiError as exc:
        logger.error(
            "MIDI export failed",
            extra={"format": "midi", "error_type": type(exc).__name__, "detail": str(exc)[:200], **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MIDI") from exc


@app.post("/export/wav")
async def export_wav(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to a downloadable WAV file via FluidSynth."""
    composition = _normalize_export_composition(payload)
    summary = _composition_export_summary(composition)
    logger.info("WAV export request started", extra={"format": "wav", **summary})
    config = load_wav_renderer_config()
    logger.debug(
        "WAV export renderer config",
        extra={
            "format": "wav",
            "fluidsynth": config.fluidsynth_basename,
            "soundfont": config.soundfont_basename,
            "sample_rate": config.sample_rate,
            "gain": config.gain,
            "timeout_seconds": config.timeout_seconds,
            "fluidsynth_exists": config.fluidsynth_exists,
            "soundfont_exists": config.soundfont_exists,
        },
    )
    try:
        result = render_wav_with_report(composition)
        wav_bytes = result.wav_bytes
        report = result.report
        filename = _safe_export_filename(composition, "wav")
        if report.issues:
            logger.warning(
                "WAV export completed with projection issues",
                extra={"format": "wav", **report.summary_extra(), **summary},
            )
        logger.info(
            "WAV export request completed",
            extra={"format": "wav", "byte_length": len(wav_bytes), **summary, **report.summary_extra()},
        )
        logger.debug(
            "WAV export response metadata",
            extra={"filename": filename, "byte_length": len(wav_bytes), **report.summary_extra()},
        )
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers=_export_response_headers(
                {"Content-Disposition": f'attachment; filename="{filename}"'},
                projection_response_headers(report),
            ),
        )
    except CompositionWavError as exc:
        if exc.unavailable:
            logger.warning(
                "WAV export unavailable",
                extra={"format": "wav", "error_type": type(exc).__name__, "detail": str(exc)[:200], **summary},
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        logger.error(
            "WAV export failed",
            extra={"format": "wav", "error_type": type(exc).__name__, "detail": str(exc)[:200], **summary},
        )
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
