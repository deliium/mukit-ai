from pydantic import BaseModel
from typing import List, Optional

class MusicGenerationRequest(BaseModel):
    length: int = 100  # Number of notes to generate
    temperature: float = 0.8  # Creativity level (0.1 = conservative, 1.5 = very creative)
    seed_notes: Optional[List[int]] = None  # Optional starting notes
    format: str = "midi"  # Output format: "midi" or "musicxml"

class MusicGenerationResponse(BaseModel):
    message: str
    filename: str
    file_path: str
