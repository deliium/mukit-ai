from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging
from .llm_settings import load_llm_settings
from .schemas import (
    LLMMusicGenerationRequest,
    LLMMusicGenerationResponse,
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
        musicxml, render_warnings = render_musicxml(music)
        all_warnings = [*warnings, *render_warnings]
        logger.info(
            "LLM music JSON request completed",
            extra={"provider": provider.provider, "model": provider.model, "warning_count": len(all_warnings)},
        )
        logger.debug(
            "LLM music JSON response shape",
            extra={
                "sections": len(music.sections),
                "tracks": len(music.tracks),
                "harmony": len(music.harmony),
                "notes": len(music.notes),
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
        logger.warning("Invalid LLM output could not be corrected")
        raise HTTPException(status_code=502, detail="LLM returned invalid music JSON") from exc
    except MusicJsonRenderError as exc:
        logger.error("MusicXML rendering failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Generated JSON could not be rendered as MusicXML") from exc
    except LLMGenerationError as exc:
        logger.error(
            "LLM music JSON request failed",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
