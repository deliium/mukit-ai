"""Tests for model/checkpoint path confinement."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.model_path_resolve import (
    MODEL_PATH_REJECTED,
    ModelPathRejectedError,
    default_music_transformer_roots,
    resolve_model_path,
    resolve_music_transformer_checkpoint,
)


def test_resolve_relative_under_checkpoint_dir(tmp_path: Path):
    root = tmp_path / "checkpoints" / "music_transformer"
    root.mkdir(parents=True)
    weights = root / "tiny.pt"
    weights.write_bytes(b"x")

    resolved = resolve_model_path(
        "tiny.pt",
        allowed_roots=[root],
        relative_base=root,
    )
    assert resolved == weights.resolve()
    assert resolved.name == "tiny.pt"


def test_resolve_rejects_absolute_outside_roots(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside" / "escape.pt"
    outside.parent.mkdir()
    outside.write_bytes(b"x")

    with pytest.raises(ModelPathRejectedError) as exc_info:
        resolve_model_path(str(outside), allowed_roots=[allowed])
    assert exc_info.value.code == MODEL_PATH_REJECTED


def test_resolve_rejects_dotdot_traversal(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "ok.pt").write_bytes(b"x")
    secret = tmp_path / "secret.pt"
    secret.write_bytes(b"x")

    with pytest.raises(ModelPathRejectedError):
        resolve_model_path("../secret.pt", allowed_roots=[allowed], relative_base=allowed)


def test_resolve_rejects_symlink_escape(tmp_path: Path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "leak.pt"
    target.write_bytes(b"x")
    link = allowed / "link.pt"
    link.symlink_to(target)

    with pytest.raises(ModelPathRejectedError):
        resolve_model_path("link.pt", allowed_roots=[allowed], relative_base=allowed)


def test_default_roots_include_checkpoint_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    env = {
        "MUSIC_TRANSFORMER_CHECKPOINT_DIR": "checkpoints/mt",
        "MUSIC_TRANSFORMER_ALLOWED_ROOTS": str(tmp_path / "extra"),
    }
    (tmp_path / "extra").mkdir()
    roots = default_music_transformer_roots(env, cwd=tmp_path)
    names = {r.name for r in roots}
    assert "mt" in names or any(r.name == "mt" for r in roots)
    assert any(r.name == "extra" for r in roots)
    assert any(r.name == "models" for r in roots)


def test_resolve_music_transformer_checkpoint_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    ckpt_dir = tmp_path / "checkpoints" / "music_transformer"
    ckpt_dir.mkdir(parents=True)
    weights = ckpt_dir / "card.bin"
    weights.write_bytes(b"ok")
    env = {
        "MUSIC_TRANSFORMER_CHECKPOINT_DIR": str(ckpt_dir),
        "MUSIC_TRANSFORMER_CHECKPOINT": "card.bin",
    }
    resolved = resolve_music_transformer_checkpoint("card.bin", env=env, cwd=tmp_path)
    assert resolved == weights.resolve()

    with pytest.raises(ModelPathRejectedError):
        resolve_music_transformer_checkpoint(
            "/etc/passwd",
            env=env,
            cwd=tmp_path,
        )
