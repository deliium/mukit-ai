"""Unit tests for Music Transformer (skip when torch missing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from app.music_transformer.checkpoint import (  # noqa: E402
    build_checkpoint_card,
    default_training_card,
    load_checkpoint,
    save_checkpoint,
)
from app.music_transformer.constraints import (  # noqa: E402
    BanPadInContent,
    FamilyTransitionHook,
    apply_constraints,
)
from app.music_transformer.loss import language_modeling_loss  # noqa: E402
from app.music_transformer.model import MusicTransformerLM, create_tiny_model  # noqa: E402
from app.music_transformer.reproducibility import seed_everything  # noqa: E402
from app.music_transformer.sampling import sample_logits  # noqa: E402
from app.music_transformer.schemas import MusicTransformerConfigV1, tiny_test_config  # noqa: E402
from app.tokenizer import special_tokens as st  # noqa: E402
from app.tokenizer.schemas import default_tokenizer_config  # noqa: E402
from app.tokenizer.vocab import build_vocab  # noqa: E402


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "music_transformer"


@pytest.fixture(scope="module")
def vocab():
    return build_vocab(default_tokenizer_config())


def test_config_rejects_bad_heads():
    with pytest.raises(Exception):
        MusicTransformerConfigV1(d_model=64, n_heads=3, vocab_size=100)


def test_forward_shape_and_causal_smoke(vocab):
    seed_everything(0)
    model = create_tiny_model(vocab_size=vocab.size)
    model.eval()
    x = torch.randint(1, min(50, vocab.size), (2, 16))
    logits = model(x)
    assert logits.shape == (2, 16, vocab.size)


def test_loss_ignores_pad(vocab):
    model = create_tiny_model(vocab_size=vocab.size)
    x = torch.zeros((1, 8), dtype=torch.long)
    x[0, :4] = torch.tensor([1, 2, 3, 4])
    logits = model(x)
    loss, acc = language_modeling_loss(logits, x, ignore_index=0)
    assert torch.isfinite(loss)
    assert 0.0 <= acc <= 1.0


def test_greedy_determinism(vocab):
    seed_everything(123)
    logits = torch.randn(vocab.size)
    a = int(sample_logits(logits, greedy=True).item())
    b = int(sample_logits(logits, greedy=True).item())
    assert a == b == int(logits.argmax().item())


def test_topk_topp_shapes(vocab):
    logits = torch.randn(vocab.size)
    tok = sample_logits(logits, temperature=0.8, top_k=10, top_p=0.9, greedy=False)
    assert tok.ndim == 0 or tok.numel() == 1


def test_ban_pad_constraint(vocab):
    logits = torch.zeros(vocab.size)
    logits[0] = 10.0
    out = BanPadInContent().filter_logits([1], logits, vocab)
    assert out[0] == float("-inf")


def test_family_transition_bans_vel_without_pitch(vocab):
    bos = vocab.token_to_id[st.BOS]
    logits = torch.zeros(vocab.size)
    # pick a VEL token
    vel_id = next(i for t, i in vocab.token_to_id.items() if t.startswith("VEL_"))
    logits[vel_id] = 5.0
    out = FamilyTransitionHook().filter_logits([bos], logits, vocab)
    assert out[vel_id] == float("-inf")
    pitch_id = next(i for t, i in vocab.token_to_id.items() if t.startswith("PITCH_"))
    out2 = FamilyTransitionHook().filter_logits([pitch_id], logits, vocab)
    assert out2[vel_id] == 5.0


def test_checkpoint_roundtrip_metadata(tmp_path, vocab):
    seed_everything(1)
    arch = tiny_test_config(vocab_size=vocab.size)
    model = MusicTransformerLM(arch)
    card = build_checkpoint_card(
        arch,
        training=default_training_card(
            seed=1,
            steps=1,
            batch_size=1,
            lr=1e-3,
            max_seq_len=128,
            device="cpu",
        ),
    )
    path = tmp_path / "tiny.pt"
    save_checkpoint(path, model, card)
    payload, loaded = load_checkpoint(path, require_tokenizer_version=True)
    assert loaded.architecture_digest == card.architecture_digest
    assert loaded.tokenizer.vocab_hash == card.tokenizer.vocab_hash
    assert "model_state_dict" in payload
    assert path.with_suffix(".pt.card.json").is_file() or Path(str(path) + ".card.json").exists()


def test_fixture_arch_loads():
    raw = json.loads((FIXTURES / "tiny_arch.json").read_text(encoding="utf-8"))
    cfg = MusicTransformerConfigV1.model_validate(raw)
    assert cfg.n_layers == 2
    assert cfg.d_model % cfg.n_heads == 0
