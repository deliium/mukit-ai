"""LoRA freezes the Music Transformer base. Skips when torch is absent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.personal_composer.trainer import run_torch_steps_on_snapshot
from app.personal_composer_settings import load_personal_composer_settings
from app.services.personal_composer_snapshot import persist_snapshot_documents, plan_snapshot_documents

torch = pytest.importorskip("torch")

_FIXTURES = Path(__file__).resolve().parents[1] / "app" / "fixtures"


def test_two_steps_leave_the_base_frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.music_transformer.schemas import tiny_test_config
    from app.tokenizer.schemas import default_tokenizer_config
    from app.tokenizer.vocab import build_vocab

    root = tmp_path / "personal_composers"
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(root))
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset"))
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    documents = []
    for name, project_id in (
        ("personal_composer_etude.v2.json", "etude"),
        ("personal_composer_sketch.v2.json", "sketch"),
    ):
        composition = CompositionV2.model_validate(json.loads((_FIXTURES / name).read_text(encoding="utf-8")))
        documents.append((project_id, composition))
    adapter_id = "pcomp_" + "22" * 8
    snapshot, encoded = plan_snapshot_documents(adapter_id=adapter_id, documents=documents)
    settings = load_personal_composer_settings()
    persist_snapshot_documents(snapshot=snapshot, encoded=encoded, settings=settings)
    vocab = build_vocab(default_tokenizer_config())
    config = tiny_test_config(vocab_size=vocab.size)
    model, clones = run_torch_steps_on_snapshot(root / adapter_id, config, max_steps=2)
    named = dict(model.named_parameters())
    for name, before in clones.items():
        assert torch.equal(named[name].detach().cpu(), before)
        assert named[name].requires_grad is False
    lora_tensors = [parameter for name, parameter in named.items() if "lora_" in name]
    assert lora_tensors
    assert any(float(parameter.detach().abs().sum()) != 0 for parameter in lora_tensors)
    assert all(not parameter.requires_grad for name, parameter in named.items() if "lora_" not in name)


def test_eval_loss_is_measured_and_adapter_samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.personal_composer.lora import lora_state_dict, measure_next_token
    from app.personal_composer.trainer import generate_from_personal_adapter
    from app.tokenizer.encode import encode_composition
    from app.music_transformer.schemas import MusicTransformerSampleConfigV1
    from app.tokenizer.schemas import TokenizerConditioningV1

    root = tmp_path / "personal_composers"
    monkeypatch.setenv("PERSONAL_COMPOSER_ROOT", str(root))
    monkeypatch.setenv("DATASET_ROOT", str(tmp_path / "dataset"))
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "projects.db"))
    documents = []
    for name, project_id in (
        ("personal_composer_etude.v2.json", "etude"),
        ("personal_composer_sketch.v2.json", "sketch"),
    ):
        composition = CompositionV2.model_validate(json.loads((_FIXTURES / name).read_text(encoding="utf-8")))
        documents.append((project_id, composition))
    adapter_id = "pcomp_" + "33" * 8
    snapshot, encoded = plan_snapshot_documents(adapter_id=adapter_id, documents=documents)
    settings = load_personal_composer_settings()
    persist_snapshot_documents(snapshot=snapshot, encoded=encoded, settings=settings)
    from app.music_transformer.schemas import tiny_test_config
    from app.tokenizer.schemas import default_tokenizer_config
    from app.tokenizer.vocab import build_vocab

    vocab = build_vocab(default_tokenizer_config())
    config = tiny_test_config(vocab_size=vocab.size)
    model, _clones = run_torch_steps_on_snapshot(root / adapter_id, config, max_steps=1)
    token_rows = [list(encode_composition(composition).token_ids) for _, composition in documents]
    loss, accuracy = measure_next_token(model, token_rows)
    assert loss is not None and loss > 0
    assert accuracy is not None and 0.0 <= accuracy <= 1.0
    torch.save(
        {
            "schema_version": "personal.adapter.v1",
            "base_model_id": "music_transformer.tiny.v1",
            "snapshot_version": snapshot.snapshot_version,
            "adapter_config": {"rank": 4, "alpha": 8, "dropout": 0.0},
            "lora_state_dict": lora_state_dict(model),
        },
        root / adapter_id / "adapter.pt",
    )
    etude = documents[0][1]
    music, report = generate_from_personal_adapter(
        root / adapter_id,
        base_model_id="music_transformer.tiny.v1",
        base_checkpoint_basename=None,
        conditioning=TokenizerConditioningV1(key="C major"),
        prefix_composition=etude,
        seed=1,
        sample_config=MusicTransformerSampleConfigV1(greedy=True, max_new_tokens=1),
    )
    assert music.schema_version == "composition.v2"
    assert report.generated_tokens == 1
    assert report.notes_out >= 1
