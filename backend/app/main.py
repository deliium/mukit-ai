import logging
from fractions import Fraction

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .llm_settings import load_llm_settings
from .schemas import (
    Composition,
    LLMMusicGenerationRequest,
    LLMMusicGenerationResponse,
    LLMMusicJson,
    LLMModelsResponse,
    LLMProviderModel,
)
from .services.llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    generate_music_json,
)
from .services.music_json_renderer import MusicJsonRenderError, render_musicxml
from .services.composition_timing import bar_duration_ticks


logger = logging.getLogger(__name__)

app = FastAPI(title="LLM Music Composer API", version="1.0.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        renderer_music = _composition_to_legacy_renderer_music(music)
        musicxml, render_warnings = render_musicxml(renderer_music)
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
    except NoLLMProviderConfiguredError as exc:
        logger.warning("LLM provider unavailable", extra={"reason": "no_configured_providers"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning("LLM provider unavailable", extra={"reason": "unsupported_provider"})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidLLMOutputError as exc:
        logger.warning("Invalid or non-playable LLM output could not be corrected")
        raise HTTPException(status_code=502, detail="LLM returned invalid or non-playable music JSON") from exc
    except MusicJsonRenderError as exc:
        logger.error("MusicXML rendering failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Generated JSON could not be rendered as MusicXML") from exc
    except LLMGenerationError as exc:
        logger.error(
            "LLM music JSON request failed",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _composition_to_legacy_renderer_music(composition: Composition) -> LLMMusicJson:
    logger.debug(
        "Adapting canonical composition for legacy MusicXML renderer",
        extra={
            "schema_version": composition.schema_version,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
            "duration_ticks": composition.duration_ticks,
        },
    )
    bar_ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    notes = []
    for track_index, track in enumerate(composition.tracks, start=1):
        for event in track.events:
            bar = (event.start_tick // bar_ticks) + 1
            beat_offset_ticks = event.start_tick % bar_ticks
            beat = Fraction(beat_offset_ticks, composition.ticks_per_quarter) + 1
            duration = Fraction(event.duration_ticks, composition.ticks_per_quarter)
            notes.append(
                {
                    "track": track_index,
                    "staff": event.staff if event.staff in {"treble", "bass"} else "treble",
                    "bar": bar,
                    "beat": float(beat),
                    "pitch": event.pitch,
                    "duration": float(duration),
                }
            )

    logger.debug(
        "Adapted canonical composition for legacy MusicXML renderer",
        extra={"note_count": len(notes), "track_ids": [track.id for track in composition.tracks]},
    )
    return LLMMusicJson.model_validate(
        {
            "tempo": composition.tempo,
            "key": composition.key,
            "time_signature": composition.time_signature,
            "sections": [
                {"type": section.type, "bars": section.bar_count}
                for section in composition.sections
            ],
            "tracks": [
                {"instrument": track.instrument, "role": track.role}
                for track in composition.tracks
            ],
            "harmony": [item.model_dump() for item in composition.harmony],
            "notes": notes,
        }
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
