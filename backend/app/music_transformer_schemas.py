"""HTTP DTOs for optional Music Transformer generate API (no torch imports)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MusicTransformerGenerateRequest(StrictModel):
    conditioning: dict[str, Any] | None = None
    prefix_composition: dict[str, Any] | None = None
    temperature: float = Field(default=1.0, ge=0.0, le=5.0)
    top_k: int | None = Field(default=None, ge=0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    greedy: bool = False
    max_new_tokens: int = Field(default=64, ge=1, le=2048)
    seed: int | None = None
    checkpoint: str | None = None


class MusicTransformerGenerateResponse(StrictModel):
    composition: dict[str, Any]
    status: Literal["ok", "repaired", "rejected"]
    stop_reason: str
    notes_out: int
    bar_count: int | None = None
    tokenizer_version: str | None = None
    vocab_hash_prefix: str | None = None
    repair_result: str | None = None
