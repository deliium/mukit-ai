"""Blinded listening packets. The packet has no metric fields; the map is sealed."""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

from app.workflow_eval.schemas import (
    ALL_ARMS,
    FORBIDDEN_PACKET_KEYS,
    ListeningJudgmentV1,
    ListeningLabel,
    ListeningMode,
    ListeningPacketV1,
    canonical_sha256,
)
from app.workflow_eval.store import WorkflowEvalStoreError, read_case_composition, read_case_result, read_run
from app.workflow_eval_settings import WorkflowEvalSettings

logger = logging.getLogger(__name__)

_LABELS: tuple[ListeningLabel, ...] = ("A", "B", "C")


class ListeningError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _ordered_arms(mode: ListeningMode) -> tuple[str, ...]:
    if mode == "ab":
        return ("v3_direct", "v4_multi_agent")
    return ("v3_direct", "v4_multi_agent", "v4_iterative_revision")


def pack_listening(
    run_id: str,
    case_id: str,
    mode: ListeningMode,
    *,
    settings: WorkflowEvalSettings | None = None,
) -> Path:
    """Write a packet and a mode-0600 label map. Does not copy metric numbers."""
    _run, run_dir = read_run(run_id, settings)
    needed = _ordered_arms(mode)
    clips_source: list[tuple[str, dict]] = []
    for arm in needed:
        if arm not in ALL_ARMS:
            continue
        try:
            result = read_case_result(run_dir, case_id, arm)
        except (OSError, ValueError) as exc:
            logger.warning(
                "listening_arm_missing",
                extra={"code": "listening_arm_missing", "case_id": case_id, "error_type": type(exc).__name__},
            )
            raise ListeningError("listening_arm_missing") from exc
        composition = read_case_composition(run_dir, case_id, arm)
        if result.invalid_composition or composition is None:
            logger.warning(
                "listening_arm_missing",
                extra={"code": "listening_arm_missing", "case_id": case_id},
            )
            raise ListeningError("listening_arm_missing")
        clips_source.append((arm, composition))
    if len(clips_source) != len(needed):
        logger.warning("listening_arm_missing", extra={"code": "listening_arm_missing", "case_id": case_id})
        raise ListeningError("listening_arm_missing")

    order = list(range(len(clips_source)))
    secrets.SystemRandom().shuffle(order)
    labels = _LABELS[: len(clips_source)]
    clips = []
    label_map: dict[str, str] = {}
    for slot, source_index in enumerate(order):
        label = labels[slot]
        arm, composition = clips_source[source_index]
        clips.append({"label": label, "composition": composition})
        label_map[label] = arm
        logger.debug("Listening label assigned", extra={"label": label})

    body = {
        "schema_version": "workflow.listening_packet.v1",
        "benchmark_id": _run.benchmark_id,
        "benchmark_version": _run.benchmark_version,
        "suite_sha256": _run.suite_sha256,
        "case_id": case_id,
        "mode": mode,
        "clips": clips,
    }
    digest = canonical_sha256(body)
    body["packet_sha256"] = digest
    _assert_packet_keys(body)
    packet = ListeningPacketV1.model_validate(body)
    packet_dir = run_dir / "listening" / case_id / digest[:12]
    packet_dir.mkdir(parents=True, exist_ok=True)
    packet_path = packet_dir / "packet.json"
    packet_path.write_text(
        json.dumps(packet.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    map_path = packet_dir / "label_map.json"
    _write_sealed(
        map_path,
        {
            "packet_sha256": digest,
            "case_id": case_id,
            "labels": label_map,
        },
    )
    logger.info(
        "Listening packet written",
        extra={"case_id": case_id, "mode": mode, "digest_prefix": digest[:12]},
    )
    return packet_dir


def _assert_packet_keys(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key) in FORBIDDEN_PACKET_KEYS:
                raise ListeningError("packet_metric_forbidden")
            _assert_packet_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_packet_keys(item)


def _write_sealed(path: Path, payload: dict) -> None:
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.debug("Sealed map mode unchanged", extra={"code": "map_mode"})


def write_judgment(
    packet_dir: Path,
    winner: ListeningLabel,
    comment: str | None = None,
) -> Path:
    packet = _load_packet(packet_dir)
    labels = {clip.label for clip in packet.clips}
    if winner not in labels:
        raise ListeningError("winner_not_in_packet")
    judgment = ListeningJudgmentV1(
        benchmark_id=packet.benchmark_id,
        benchmark_version=packet.benchmark_version,
        suite_sha256=packet.suite_sha256,
        packet_sha256=packet.packet_sha256,
        case_id=packet.case_id,
        winner=winner,
        comment=(comment or None),
    )
    path = packet_dir / "judgment.json"
    path.write_text(
        json.dumps(judgment.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info(
        "Listening judgment written",
        extra={"winner": winner, "digest_prefix": packet.packet_sha256[:12]},
    )
    return path


def unblind(packet_dir: Path) -> dict:
    """Return the sealed map plus the judgment. Refuses when the judgment is missing."""
    packet = _load_packet(packet_dir)
    judgment_path = packet_dir / "judgment.json"
    if not judgment_path.is_file():
        logger.warning("judgment_missing", extra={"code": "judgment_missing"})
        raise ListeningError("judgment_missing")
    try:
        judgment = ListeningJudgmentV1.model_validate(
            json.loads(judgment_path.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("judgment_missing", extra={"code": "judgment_missing", "error_type": type(exc).__name__})
        raise ListeningError("judgment_missing") from exc
    if judgment.packet_sha256 != packet.packet_sha256:
        logger.warning("judgment_missing", extra={"code": "judgment_missing"})
        raise ListeningError("judgment_missing")
    map_path = packet_dir / "label_map.json"
    if not map_path.is_file():
        raise ListeningError("judgment_missing")
    sealed = json.loads(map_path.read_text(encoding="utf-8"))
    logger.info(
        "Listening unblinded",
        extra={"digest_prefix": packet.packet_sha256[:12], "winner": judgment.winner},
    )
    return {
        "label_map": sealed,
        "judgment": judgment.model_dump(mode="json"),
    }


def _load_packet(packet_dir: Path) -> ListeningPacketV1:
    path = packet_dir / "packet.json"
    if not path.is_file():
        raise WorkflowEvalStoreError("packet_missing")
    return ListeningPacketV1.model_validate(json.loads(path.read_text(encoding="utf-8")))
