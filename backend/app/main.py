import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn

from .db import ensure_database
from .llm_settings import load_llm_settings
from .routers.projects import router as projects_router
from .schemas import (
    Composition,
    LLMCompositionEditRequest,
    LLMCompositionEditResponse,
    LLMMusicGenerationRequest,
    LLMMusicGenerationResponse,
    LLMModelsResponse,
    LLMProviderModel,
)
from .services.composition_midi import CompositionMidiError, render_midi
from .services.composition_wav import CompositionWavError, load_wav_renderer_config, render_wav
from .services.composition_planner import OversizedLLMGenerationRequestError
from .services.llm_composition_editor import edit_composition_region
from .services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    generate_music_json,
)
from .services.music_json_renderer import MusicJsonRenderError, render_musicxml


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db_path = ensure_database()
    logger.info(
        "Application startup database ready",
        extra={"project_db_path": str(db_path)},
    )
    yield


app = FastAPI(title="LLM Music Composer API", version="1.0.0", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router)


def _composition_export_summary(composition: Composition) -> dict:
    return {
        "schema_version": composition.schema_version,
        "track_count": len(composition.tracks),
        "event_count": sum(len(track.events) for track in composition.tracks),
        "duration_ticks": composition.duration_ticks,
        "tempo": composition.tempo,
        "time_signature": composition.time_signature,
    }


def _safe_export_filename(composition: Composition, extension: str) -> str:
    raw = f"composition-{composition.key}-{composition.tempo}bpm".lower()
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{safe.strip('-') or 'composition'}.{extension}"


@app.get("/")
async def root():
    return {"message": "Music Composer API is running!"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/llm/models", response_model=LLMModelsResponse)
async def get_llm_models():
    """Return configured LLM provider/model options without exposing API keys."""
    logger.info("LLM model discovery requested")
    settings = load_llm_settings()
    logger.debug(
        "LLM provider availability summary",
        extra={"providers": [provider.provider for provider in settings.providers]},
    )

    models = [
        LLMProviderModel(
            provider=provider.provider,
            model=provider.model,
            display_name=f"{provider.provider.title()} ({provider.model})",
            is_default=provider.is_default,
        )
        for provider in settings.providers
    ]
    default_model = next((provider.model for provider in settings.providers if provider.is_default), None)
    warnings = [] if models else ["No LLM providers configured. Set OPENAI_API_KEY or DEEPSEEK_API_KEY."]

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
        music, warnings, provider = await generate_music_json(request, settings)
        musicxml, render_warnings = render_musicxml(music)
        all_warnings = [*warnings, *render_warnings]
        logger.info(
            "LLM music JSON request completed",
            extra={
                "provider": provider.provider,
                "model": provider.model,
                "schema_version": music.schema_version,
                "warning_count": len(all_warnings),
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
            },
        )
        return LLMMusicGenerationResponse(
            music=music,
            provider=provider.provider,
            model=provider.model,
            musicxml=musicxml,
            warnings=all_warnings,
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
        musicxml, render_warnings = render_musicxml(composition)
        all_warnings = [*warnings, *render_warnings, *patch.warnings]
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
async def export_musicxml(composition: Composition):
    """Render canonical Composition V1 JSON to a downloadable MusicXML file."""
    summary = _composition_export_summary(composition)
    logger.info("MusicXML export request started", extra={"format": "musicxml", **summary})
    try:
        musicxml, warnings = render_musicxml(composition)
        filename = _safe_export_filename(composition, "musicxml")
        if warnings:
            logger.warning(
                "MusicXML export completed with notation warnings",
                extra={"format": "musicxml", "warning_count": len(warnings), **summary},
            )
        logger.info(
            "MusicXML export request completed",
            extra={"format": "musicxml", "byte_length": len(musicxml.encode("utf-8")), **summary},
        )
        logger.debug(
            "MusicXML export response metadata",
            extra={"filename": filename, "musicxml_length": len(musicxml), "warning_count": len(warnings)},
        )
        return Response(
            content=musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML export failed",
            extra={"format": "musicxml", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MusicXML") from exc


@app.post("/export/musicxml/preview")
async def export_musicxml_preview(composition: Composition):
    """Render canonical Composition V1 JSON to MusicXML text without download headers."""
    summary = _composition_export_summary(composition)
    logger.info("MusicXML preview render started", extra={"format": "musicxml_preview", **summary})
    try:
        musicxml, warnings = render_musicxml(composition)
        if warnings:
            logger.warning(
                "MusicXML preview completed with notation warnings",
                extra={"format": "musicxml_preview", "warning_count": len(warnings), **summary},
            )
        logger.info(
            "MusicXML preview render completed",
            extra={
                "format": "musicxml_preview",
                "musicxml_length": len(musicxml),
                "warning_count": len(warnings),
                **summary,
            },
        )
        return Response(
            content=musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
        )
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML preview render failed",
            extra={"format": "musicxml_preview", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MusicXML") from exc


@app.post("/export/midi")
async def export_midi(composition: Composition):
    """Render canonical Composition V1 JSON to a downloadable Standard MIDI File."""
    summary = _composition_export_summary(composition)
    logger.info("MIDI export request started", extra={"format": "midi", **summary})
    try:
        midi_bytes = render_midi(composition)
        filename = _safe_export_filename(composition, "mid")
        logger.info(
            "MIDI export request completed",
            extra={"format": "midi", "byte_length": len(midi_bytes), **summary},
        )
        logger.debug(
            "MIDI export response metadata",
            extra={"filename": filename, "byte_length": len(midi_bytes)},
        )
        return Response(
            content=midi_bytes,
            media_type="audio/midi",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except CompositionMidiError as exc:
        logger.error(
            "MIDI export failed",
            extra={"format": "midi", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MIDI") from exc


@app.post("/export/wav")
async def export_wav(composition: Composition):
    """Render canonical Composition V1 JSON to a downloadable WAV file via FluidSynth."""
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
        wav_bytes = render_wav(composition)
        filename = _safe_export_filename(composition, "wav")
        logger.info(
            "WAV export request completed",
            extra={"format": "wav", "byte_length": len(wav_bytes), **summary},
        )
        logger.debug(
            "WAV export response metadata",
            extra={"filename": filename, "byte_length": len(wav_bytes)},
        )
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
