"""Confine operator-supplied model / checkpoint paths to configured roots.

Rejects ``..`` traversal and absolute paths outside allowlisted roots.
Never logs absolute home paths at INFO — basenames only.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL_PATH_REJECTED = "model_path_rejected"


class ModelPathRejectedError(ValueError):
    """Raised when a model/checkpoint path escapes allowed roots."""

    def __init__(self, message: str = "Model path rejected") -> None:
        super().__init__(message)
        self.code = MODEL_PATH_REJECTED


def _resolved_root(raw: str | Path) -> Path | None:
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve(strict=False)
    except OSError:
        return None


def default_music_transformer_roots(
    env: Mapping[str, str] | None = None,
    *,
    cwd: Path | None = None,
) -> list[Path]:
    """Allowlist roots for Music Transformer checkpoints.

    Includes ``MUSIC_TRANSFORMER_CHECKPOINT_DIR``, optional
    ``MUSIC_TRANSFORMER_ALLOWED_ROOTS`` (comma-separated), and common Compose
    mount points ``models/`` / ``models/llm`` under the process cwd when present
    as directories (or always as resolved candidates for confinement checks).
    """
    source = env if env is not None else os.environ
    work = (cwd or Path.cwd()).resolve(strict=False)
    roots: list[Path] = []

    ckpt_dir = (source.get("MUSIC_TRANSFORMER_CHECKPOINT_DIR") or "").strip()
    if not ckpt_dir:
        ckpt_dir = "checkpoints/music_transformer"
    resolved_ckpt = _resolved_root(work / ckpt_dir if not Path(ckpt_dir).is_absolute() else ckpt_dir)
    if resolved_ckpt is not None:
        roots.append(resolved_ckpt)

    extra = (source.get("MUSIC_TRANSFORMER_ALLOWED_ROOTS") or "").strip()
    if extra:
        for part in extra.split(","):
            candidate = _resolved_root(part.strip())
            if candidate is not None:
                roots.append(candidate)

    for relative in ("models", "models/llm", "checkpoints"):
        candidate = _resolved_root(work / relative)
        if candidate is not None:
            roots.append(candidate)

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    logger.debug(
        "Music Transformer path allowlist roots",
        extra={"root_count": len(unique), "root_basenames": [r.name for r in unique]},
    )
    return unique


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_model_path(
    user_path: str | Path,
    *,
    allowed_roots: Sequence[Path | str],
    relative_base: Path | str | None = None,
) -> Path:
    """Resolve ``user_path`` under ``allowed_roots``; raise ``ModelPathRejectedError`` on escape.

    Relative paths are joined with ``relative_base`` (typically checkpoint_dir) when set,
    otherwise with the first allowed root. Symlinks are followed via ``resolve()`` so
    link escapes outside roots are rejected.
    """
    raw = str(user_path).strip()
    if not raw:
        logger.warning(
            "Model path rejected",
            extra={"code": MODEL_PATH_REJECTED, "basename": None, "reason": "empty"},
        )
        raise ModelPathRejectedError("Model path is empty")

    roots = [r for r in (_resolved_root(root) for root in allowed_roots) if r is not None]
    if not roots:
        logger.warning(
            "Model path rejected",
            extra={"code": MODEL_PATH_REJECTED, "basename": Path(raw).name, "reason": "no_roots"},
        )
        raise ModelPathRejectedError("No allowed model path roots configured")

    candidate = Path(raw).expanduser()
    if ".." in candidate.parts:
        # Soft pre-check; final resolve still decides after normalization.
        pass

    if not candidate.is_absolute():
        base = Path(relative_base) if relative_base is not None else roots[0]
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve(strict=False)
        else:
            base = base.resolve(strict=False)
        candidate = (base / candidate).resolve(strict=False)
    else:
        candidate = candidate.resolve(strict=False)

    if any(_is_under_root(candidate, root) for root in roots):
        logger.info(
            "Model path accepted",
            extra={"code": "model_path_ok", "basename": candidate.name},
        )
        return candidate

    logger.warning(
        "Model path rejected",
        extra={
            "code": MODEL_PATH_REJECTED,
            "basename": candidate.name,
            "reason": "outside_roots",
        },
    )
    raise ModelPathRejectedError(
        f"Model path rejected: {candidate.name} is outside allowed roots"
    )


def resolve_music_transformer_checkpoint(
    user_path: str | Path | None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve a Music Transformer checkpoint against default allowlisted roots."""
    if user_path is None or not str(user_path).strip():
        logger.warning(
            "Model path rejected",
            extra={"code": MODEL_PATH_REJECTED, "basename": None, "reason": "missing"},
        )
        raise ModelPathRejectedError("Music Transformer checkpoint path is missing")

    source = env if env is not None else os.environ
    roots = default_music_transformer_roots(source, cwd=cwd)
    ckpt_dir = (source.get("MUSIC_TRANSFORMER_CHECKPOINT_DIR") or "").strip() or (
        "checkpoints/music_transformer"
    )
    work = (cwd or Path.cwd()).resolve(strict=False)
    relative_base = Path(ckpt_dir)
    if not relative_base.is_absolute():
        relative_base = work / relative_base
    return resolve_model_path(
        user_path,
        allowed_roots=roots,
        relative_base=relative_base,
    )


def model_path_error_detail(exc: BaseException) -> dict[str, Any]:
    """Stable HTTP-ish detail payload (no absolute paths)."""
    code = getattr(exc, "code", MODEL_PATH_REJECTED) or MODEL_PATH_REJECTED
    return {"code": code, "message": str(exc)[:200]}


__all__ = [
    "MODEL_PATH_REJECTED",
    "ModelPathRejectedError",
    "default_music_transformer_roots",
    "model_path_error_detail",
    "resolve_model_path",
    "resolve_music_transformer_checkpoint",
]
