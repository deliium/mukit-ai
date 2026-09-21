"""Acceptance: train → save → load → greedy generate → valid Composition V2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from app.music_transformer.inference import generate_composition  # noqa: E402
from app.music_transformer.schemas import (  # noqa: E402
    MusicTransformerSampleConfigV1,
    MusicTransformerTrainConfigV1,
    tiny_test_config,
)
from app.music_transformer.train import train_model  # noqa: E402
from app.tokenizer.schemas import default_tokenizer_config  # noqa: E402
from app.tokenizer.vocab import build_vocab  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "music_transformer"


def test_acceptance_train_save_load_generate_valid_v2(tmp_path):
    vocab = build_vocab(default_tokenizer_config())
    arch = tiny_test_config(vocab_size=vocab.size)
    train_cfg = MusicTransformerTrainConfigV1.model_validate(
        json.loads((FIXTURES / "tiny_train.json").read_text(encoding="utf-8"))
    )
    seed_path = FIXTURES / "seed_one_bar.json"
    ckpt = tmp_path / "accept.pt"
    train_model(
        train_cfg,
        architecture=arch,
        inputs=[seed_path],
        out_checkpoint=ckpt,
        device="cpu",
    )
    assert ckpt.is_file()

    prefix = json.loads(seed_path.read_text(encoding="utf-8"))
    sample = MusicTransformerSampleConfigV1(
        greedy=True,
        max_new_tokens=48,
        temperature=0.0,
        ban_pad=True,
        family_transition_hook=True,
        on_invalid="repair",
    )
    composition, report = generate_composition(
        ckpt,
        prefix_composition=prefix,
        sample_config=sample,
        require_tokenizer_version=True,
        device="cpu",
        seed=7,
    )
    assert composition.schema_version == "composition.v2"
    assert report.status in {"ok", "repaired"}
    assert report.notes_out >= 1
    assert sum(len(t.events) for t in composition.tracks) >= 1
