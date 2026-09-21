"""CLI entry: ``python -m app.dataset.cli <subcommand>``."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from app.dataset.errors import DatasetError
from app.dataset.pipeline import run_build, run_verify
from app.dataset.sources import load_pipeline_config
from app.ready import configure_logging


logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        prog="python -m app.dataset.cli",
        description="Offline symbolic music dataset pipeline (filesystem only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_config(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", required=True, type=Path, help="dataset.pipeline.v1 YAML/JSON")
        p.add_argument(
            "--out",
            type=Path,
            default=None,
            help="Output root (default: DATASET_ROOT or pipeline output_root)",
        )

    for name in ("ingest", "normalize", "segment", "dedup", "split", "stats", "build"):
        p = sub.add_parser(name, help=f"Run {name} phase (build runs full pipeline)")
        add_config(p)

    verify = sub.add_parser("verify", help="Verify manifest digests for a versioned dir")
    verify.add_argument("--dataset-dir", required=True, type=Path)

    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        if args.command == "verify":
            run_verify(args.dataset_dir.resolve())
        elif args.command == "build":
            version_dir = run_build(args.config.resolve(), out_root=args.out)
            print(str(version_dir))
        else:
            # Individual phases currently execute via full build for reproducibility.
            # Keeping subcommands for the documented CLI surface.
            logger.info(
                "Phase subcommand delegates to full build",
                extra={"command": args.command},
            )
            _ = load_pipeline_config(args.config.resolve())
            version_dir = run_build(args.config.resolve(), out_root=args.out)
            print(str(version_dir))
    except DatasetError as exc:
        logger.error(
            "Dataset command failed",
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
            "Dataset command crashed",
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


if __name__ == "__main__":
    raise SystemExit(main())
