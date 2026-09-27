"""``python -m app.workflow_eval.cli`` for suite runs, regression, and listening."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from app.workflow_eval.autonomous_arm import run_autonomous_arm
from app.workflow_eval.listening import ListeningError, pack_listening, unblind, write_judgment
from app.workflow_eval.pipelines import run_arm
from app.workflow_eval.regress import regress
from app.workflow_eval.schemas import (
    ALL_ARMS,
    COMPARISON_ARMS,
    ArmId,
    load_benchmark_suite,
)
from app.workflow_eval.store import (
    WorkflowEvalStoreError,
    set_baseline,
    write_run,
)
from app.workflow_eval_settings import WorkflowEvalConfigError, load_workflow_eval_settings

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_BASELINE = 2
EXIT_LISTENING = 3

_DEFAULT_SUITE = str(
    Path(__file__).resolve().parents[1] / "fixtures" / "workflow_benchmark" / "suite.v1.json"
)


def _fake_mode() -> bool:
    raw = os.environ.get("LLM_FAKE_MODE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_arms(raw: str | None, include_autonomous: bool) -> list[ArmId]:
    if raw:
        arms = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        arms = list(COMPARISON_ARMS)
    if include_autonomous and "v4_autonomous" not in arms:
        arms.append("v4_autonomous")
    unknown = [arm for arm in arms if arm not in ALL_ARMS]
    if unknown:
        raise SystemExit(EXIT_LISTENING)
    return arms  # type: ignore[return-type]


async def _execute_run(args: argparse.Namespace) -> int:
    if not _fake_mode() and not args.real_models:
        logger.warning(
            "real_models_required",
            extra={"code": "real_models_required"},
        )
        return EXIT_BASELINE
    suite, digest = load_benchmark_suite(args.suite)
    wanted_cases = None
    if args.cases:
        wanted_cases = {part.strip() for part in args.cases.split(",") if part.strip()}
    cases = [case for case in suite.cases if wanted_cases is None or case.id in wanted_cases]
    if not cases:
        logger.warning("listening_arm_missing", extra={"code": "case_missing"})
        return EXIT_LISTENING
    arms = _parse_arms(args.arms, args.include_autonomous)
    logger.info(
        "Benchmark run start",
        extra={
            "benchmark_id": suite.benchmark_id,
            "benchmark_version": suite.benchmark_version,
            "suite_sha256_prefix": digest[:12],
            "arms": arms,
        },
    )
    settings = load_workflow_eval_settings()
    outcomes = []
    for case in cases:
        for arm in arms:
            if arm == "v4_autonomous":
                outcome = await run_autonomous_arm(case, benchmark_root=settings.benchmark_root)
            else:
                outcome = await run_arm(case, arm)
            outcomes.append(outcome)
    run = write_run(
        suite,
        digest,
        outcomes,
        arms=arms,
        case_ids=[case.id for case in cases],
        settings=settings,
    )
    logger.info("CLI run stored", extra={"subcommand": "run", "run_id_prefix": run.run_id[:12]})
    print(run.run_id)
    return EXIT_OK


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.workflow_eval.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    suite = sub.add_parser("suite")
    suite_sub = suite.add_subparsers(dest="suite_command", required=True)
    suite_sub.add_parser("digest")

    run = sub.add_parser("run")
    run.add_argument("--suite", default=_DEFAULT_SUITE)
    run.add_argument("--cases", default=None)
    run.add_argument("--arms", default=None)
    run.add_argument("--include-autonomous", action="store_true")
    run.add_argument("--real-models", action="store_true")

    baseline = sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    baseline_set = baseline_sub.add_parser("set")
    baseline_set.add_argument("--run", required=True)
    baseline_set.add_argument("--force", action="store_true")

    regress_cmd = sub.add_parser("regress")
    regress_cmd.add_argument("--run", required=True)
    regress_cmd.add_argument("--suite", default=_DEFAULT_SUITE)

    listen = sub.add_parser("listen")
    listen_sub = listen.add_subparsers(dest="listen_command", required=True)
    pack = listen_sub.add_parser("pack")
    pack.add_argument("--run", required=True)
    pack.add_argument("--cases", required=True)
    pack.add_argument("--mode", choices=("ab", "abc"), required=True)
    judge = listen_sub.add_parser("judge")
    judge.add_argument("--packet", required=True)
    judge.add_argument("--winner", required=True, choices=("A", "B", "C"))
    judge.add_argument("--comment", default=None)
    reveal = listen_sub.add_parser("unblind")
    reveal.add_argument("--packet", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = _build_parser()
    args = parser.parse_args(argv)
    logger.info("CLI subcommand", extra={"subcommand": args.command})
    try:
        if args.command == "suite" and args.suite_command == "digest":
            _suite, digest = load_benchmark_suite(_DEFAULT_SUITE)
            print(digest)
            return EXIT_OK
        if args.command == "run":
            return asyncio.run(_execute_run(args))
        if args.command == "baseline" and args.baseline_command == "set":
            logger.info("CLI baseline", extra={"subcommand": "baseline", "run_id_prefix": args.run[:12]})
            set_baseline(args.run, force=args.force)
            return EXIT_OK
        if args.command == "regress":
            logger.info("CLI regress", extra={"subcommand": "regress", "run_id_prefix": args.run[:12]})
            suite, _digest = load_benchmark_suite(args.suite)
            _report, code = regress(args.run, suite)
            return code
        if args.command == "listen" and args.listen_command == "pack":
            case_id = args.cases.split(",")[0].strip()
            logger.info("CLI listen pack", extra={"subcommand": "listen", "run_id_prefix": args.run[:12]})
            directory = pack_listening(args.run, case_id, args.mode)
            print(directory)
            return EXIT_OK
        if args.command == "listen" and args.listen_command == "judge":
            write_judgment(Path(args.packet), args.winner, args.comment)
            return EXIT_OK
        if args.command == "listen" and args.listen_command == "unblind":
            payload = unblind(Path(args.packet))
            print(json.dumps(payload, indent=2, sort_keys=True))
            return EXIT_OK
    except ListeningError as exc:
        logger.warning(exc.code, extra={"code": exc.code})
        if exc.code == "listening_arm_missing":
            return EXIT_LISTENING
        return EXIT_LISTENING
    except (WorkflowEvalStoreError, WorkflowEvalConfigError) as exc:
        logger.warning(exc.code, extra={"code": exc.code})
        if exc.code in {"baseline_missing", "suite_digest_mismatch", "benchmark_root_rejected"}:
            return EXIT_BASELINE
        return EXIT_LISTENING
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
