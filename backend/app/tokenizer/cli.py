"""CLI entry: ``python -m app.tokenizer.cli <subcommand>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from app.ready import configure_logging
from app.tokenizer.decode import decode_tokens
from app.tokenizer.encode import encode_composition
from app.tokenizer.errors import TokenizerError, TokenizerVerifyError
from app.tokenizer.manifest import build_manifest, verify_manifest, write_manifest
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConfigV1,
    TokenizerSequenceV1,
    default_tokenizer_config,
)
from app.tokenizer.stats import compute_stats_from_dataset_dir, compute_stats_from_paths
from app.tokenizer.vocab import build_vocab
from app.tokenizer.viz import write_visualization


logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.tokenizer.cli",
        description="Composition V2 symbolic tokenizer (filesystem/CLI only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    encode_p = sub.add_parser("encode", help="Encode V2 / dataset.example.v1 → tokens")
    encode_p.add_argument("--input", required=True, type=Path)
    encode_p.add_argument("--out", required=True, type=Path)
    encode_p.add_argument("--config", type=Path, default=None)
    encode_p.add_argument("--profile", choices=["core", "core_harmony"], default=None)
    encode_p.add_argument(
        "--require-version",
        action="store_true",
        help="Fail if --expectation sidecar version/hash mismatches current tokenizer",
    )
    encode_p.add_argument(
        "--expectation",
        type=Path,
        default=None,
        help="Optional tokenizer.model_expectation.v1 JSON to check with --require-version",
    )

    decode_p = sub.add_parser("decode", help="Decode tokens → Composition V2")
    decode_p.add_argument("--input", required=True, type=Path)
    decode_p.add_argument("--out", required=True, type=Path)
    decode_p.add_argument("--config", type=Path, default=None)
    decode_p.add_argument("--on-invalid", choices=["repair", "reject"], default=None)
    decode_p.add_argument(
        "--require-version",
        action="store_true",
        help="Fail if tokens.tokenizer_version / vocab_hash mismatch current tokenizer",
    )

    rt_p = sub.add_parser("roundtrip", help="Encode then decode; print summary JSON")
    rt_p.add_argument("--input", required=True, type=Path)
    rt_p.add_argument("--config", type=Path, default=None)
    rt_p.add_argument("--out", type=Path, default=None)

    stats_p = sub.add_parser("stats", help="Compute tokenizer.stats.v1")
    stats_p.add_argument("--dataset-dir", type=Path, default=None)
    stats_p.add_argument("--inputs", nargs="*", type=Path, default=None)
    stats_p.add_argument("--out", type=Path, default=None)
    stats_p.add_argument("--config", type=Path, default=None)

    viz_p = sub.add_parser("viz", help="Visualize a token sequence")
    viz_p.add_argument("--input", required=True, type=Path)
    viz_p.add_argument("--out", required=True, type=Path)
    viz_p.add_argument("--config", type=Path, default=None)

    vocab_p = sub.add_parser("vocab", help="Write vocab.json + optional manifest")
    vocab_p.add_argument("--out", required=True, type=Path)
    vocab_p.add_argument("--config", type=Path, default=None)
    vocab_p.add_argument("--manifest-out", type=Path, default=None)

    verify_p = sub.add_parser("verify", help="Verify tokenizer.manifest.v1")
    verify_p.add_argument("--manifest", required=True, type=Path)
    verify_p.add_argument("--config", type=Path, default=None)
    verify_p.add_argument("--require-version", action="store_true")

    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        config = _load_config(args)
        if args.command == "encode":
            _cmd_encode(args, config)
        elif args.command == "decode":
            _cmd_decode(args, config)
        elif args.command == "roundtrip":
            _cmd_roundtrip(args, config)
        elif args.command == "stats":
            _cmd_stats(args, config)
        elif args.command == "viz":
            _cmd_viz(args, config)
        elif args.command == "vocab":
            _cmd_vocab(args, config)
        elif args.command == "verify":
            _cmd_verify(args, config)
        else:
            parser.error(f"unknown command {args.command}")
    except TokenizerError as exc:
        logger.error(
            "Tokenizer command failed",
            extra={
                "command": args.command,
                "error_code": exc.code,
                "error_type": type(exc).__name__,
            },
        )
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        logger.error(
            "Tokenizer command crashed",
            extra={"command": args.command, "error_type": type(exc).__name__},
        )
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    logger.info(
        "CLI command finished",
        extra={
            "command": args.command,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        },
    )
    return 0


def _load_config(args: argparse.Namespace) -> TokenizerConfigV1:
    config_path = getattr(args, "config", None)
    if config_path:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
        # Allow YAML-like keys via JSON only for v1 CLI; YAML optional later.
        if isinstance(raw, dict) and "schema_version" not in raw:
            raw["schema_version"] = "tokenizer.config.v1"
        config = TokenizerConfigV1.model_validate(raw)
    else:
        config = default_tokenizer_config()
    profile = getattr(args, "profile", None)
    if profile:
        config = config.model_copy(
            update={"profile": profile, "emit_harmony": profile == "core_harmony"}
        )
    return config


def _read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _cmd_encode(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    if getattr(args, "require_version", False):
        _enforce_expectation(getattr(args, "expectation", None), config)
    doc = _read_json(args.input)
    seq = encode_composition(doc, config)
    _write_json(args.out, seq)
    print(json.dumps({"tokens": len(seq.token_ids), "vocab_hash": seq.vocab_hash[:12]}))


def _cmd_decode(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    raw = _read_json(args.input)
    seq = TokenizerSequenceV1.model_validate(raw)
    if getattr(args, "require_version", False):
        _enforce_sequence_version(seq, config)
    on_invalid = getattr(args, "on_invalid", None)
    composition, report = decode_tokens(seq, config, on_invalid=on_invalid)
    _write_json(args.out, composition)
    print(json.dumps({"result": report.result, "notes_out": report.notes_out}))


def _cmd_roundtrip(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    doc = _read_json(args.input)
    seq = encode_composition(doc, config)
    composition, report = decode_tokens(seq, config)
    summary = {
        "tokenizer_version": seq.tokenizer_version,
        "vocab_hash_prefix": seq.vocab_hash[:12],
        "tokens": len(seq.token_ids),
        "encode_notes": seq.encode_report.notes_emitted if seq.encode_report else None,
        "decode_notes": report.notes_out,
        "decode_result": report.result,
        "bars": composition.bar_count,
    }
    if args.out:
        _write_json(args.out, composition)
    print(json.dumps(summary, indent=2, sort_keys=True))


def _cmd_stats(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    if args.dataset_dir:
        stats = compute_stats_from_dataset_dir(args.dataset_dir, config)
    elif args.inputs:
        stats = compute_stats_from_paths(args.inputs, config)
    else:
        raise TokenizerError("missing_inputs", "provide --dataset-dir or --inputs")
    if args.out:
        _write_json(args.out, stats)
    print(json.dumps(stats.model_dump(mode="json"), indent=2, sort_keys=True))


def _cmd_viz(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    raw = _read_json(args.input)
    seq = TokenizerSequenceV1.model_validate(raw)
    vocab = build_vocab(config)
    write_visualization(seq, args.out, vocab)


def _cmd_vocab(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    vocab = build_vocab(config)
    _write_json(args.out, vocab.to_schema())
    if args.manifest_out:
        write_manifest(args.manifest_out, build_manifest(config, vocab))
    print(json.dumps({"vocab_size": vocab.size, "vocab_hash": vocab.vocab_hash[:12]}))


def _cmd_verify(args: argparse.Namespace, config: TokenizerConfigV1) -> None:
    require_version = bool(getattr(args, "require_version", False))
    manifest = verify_manifest(
        args.manifest,
        config,
        require_version=require_version,
    )
    logger.info(
        "CLI verify completed",
        extra={
            "require_version": require_version,
            "tokenizer_version": manifest.tokenizer_version,
            "vocab_hash_prefix": manifest.vocab_hash[:12],
        },
    )
    print(
        json.dumps(
            {
                "ok": True,
                "require_version": require_version,
                "tokenizer_version": manifest.tokenizer_version,
                "vocab_hash_prefix": manifest.vocab_hash[:12],
            }
        )
    )


def _enforce_sequence_version(seq: TokenizerSequenceV1, config: TokenizerConfigV1) -> None:
    vocab = build_vocab(config)
    if seq.tokenizer_version != TOKENIZER_VERSION:
        logger.error(
            "Token sequence version mismatch",
            extra={
                "sequence_version": seq.tokenizer_version,
                "expected": TOKENIZER_VERSION,
            },
        )
        raise TokenizerVerifyError(
            "version_mismatch",
            "tokens.tokenizer_version does not match TOKENIZER_VERSION",
            details={
                "sequence_version": seq.tokenizer_version,
                "expected": TOKENIZER_VERSION,
            },
        )
    if seq.vocab_hash != vocab.vocab_hash:
        logger.error(
            "Token sequence vocab hash mismatch",
            extra={
                "sequence_prefix": seq.vocab_hash[:12],
                "expected_prefix": vocab.vocab_hash[:12],
            },
        )
        raise TokenizerVerifyError(
            "vocab_hash_mismatch",
            "tokens.vocab_hash does not match current vocabulary",
        )


def _enforce_expectation(expectation_path: Path | None, config: TokenizerConfigV1) -> None:
    if expectation_path is None:
        raise TokenizerError(
            "expectation_required",
            "encode --require-version needs --expectation <model_expectation.json>",
        )
    from app.tokenizer.schemas import TokenizerModelExpectationV1
    from app.tokenizer.versioning import verify_expectation

    raw = _read_json(expectation_path)
    expectation = TokenizerModelExpectationV1.model_validate(raw)
    verify_expectation(expectation, config, require_version=True)


if __name__ == "__main__":
    raise SystemExit(main())
