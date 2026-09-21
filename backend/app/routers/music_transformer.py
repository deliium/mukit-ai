"""Optional Music Transformer generate router (no torch / model imports)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.music_transformer.errors import (
    MusicTransformerCheckpointError,
    MusicTransformerDependencyError,
    MusicTransformerError,
    MusicTransformerGenerateError,
)
from app.music_transformer.settings import load_music_transformer_settings
from app.music_transformer_schemas import (
    MusicTransformerGenerateRequest,
    MusicTransformerGenerateResponse,
)
from app.services.music_transformer_generate import generate_via_music_transformer


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/music-transformer", tags=["music-transformer"])


@router.post("/generate", response_model=MusicTransformerGenerateResponse)
def post_generate(body: MusicTransformerGenerateRequest) -> MusicTransformerGenerateResponse:
    settings = load_music_transformer_settings()
    if not settings.api_enabled:
        raise HTTPException(status_code=503, detail="Music Transformer API is disabled")
    try:
        return generate_via_music_transformer(body)
    except MusicTransformerDependencyError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except MusicTransformerCheckpointError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except MusicTransformerGenerateError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    except MusicTransformerError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
