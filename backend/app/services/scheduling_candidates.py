"""Project registry descriptors and ExecutionNode snapshots into candidates.

Never logs address strings at INFO. Local ``AI_SCHEDULING_LOCAL_*`` hints fill
controller-local resources only and never override ExecutionNode heartbeats.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.ai_runtime.registry import list_models, reload_registry
from app.scheduling_schemas import (
    CONTROLLER_LOCAL_RUNTIMES,
    SchedulingCandidateV1,
    trust_boundary_for_runtime,
)
from app.scheduling_settings import SchedulingSettings, load_scheduling_settings

logger = logging.getLogger(__name__)


def build_scheduling_candidates(
    *,
    env: Mapping[str, str] | None = None,
    settings: SchedulingSettings | None = None,
    reload: bool = False,
) -> list[SchedulingCandidateV1]:
    """Build candidate snapshots from the registry and live peer projections."""
    active = settings or load_scheduling_settings(env)
    if reload:
        reload_registry(env)

    nodes_by_id = _load_projected_nodes()
    candidates: list[SchedulingCandidateV1] = []
    trust_counts: dict[str, int] = {}

    for descriptor in list_models(env=env):
        if descriptor.status != "ready":
            continue
        candidate = _project_descriptor(descriptor, nodes_by_id, active)
        if candidate is None:
            continue
        candidates.append(candidate)
        trust_counts[candidate.trust_boundary] = trust_counts.get(candidate.trust_boundary, 0) + 1

    logger.debug(
        "scheduling candidates built",
        extra={
            "candidate_count": len(candidates),
            "trust_counts": trust_counts,
        },
    )
    return candidates


def _load_projected_nodes() -> dict[str, Any]:
    try:
        from app.execution_node_settings import execution_nodes_enabled, load_execution_node_settings
        from app.services import execution_node_service as nodes
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "execution node import skipped for candidates",
            extra={"error_type": type(exc).__name__},
        )
        return {}

    if not execution_nodes_enabled():
        return {}
    try:
        cfg = load_execution_node_settings()
        if cfg.role not in {"controller", "both"}:
            return {}
        return {node.node_id: node for node in nodes.list_nodes(settings=cfg)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution node list failed for candidates",
            extra={"error_type": type(exc).__name__},
        )
        return {}


def _project_descriptor(
    descriptor: Any,
    nodes_by_id: dict[str, Any],
    settings: SchedulingSettings,
) -> SchedulingCandidateV1 | None:
    runtime = str(descriptor.runtime)
    limits = dict(descriptor.limits or {})
    node_id = limits.get("execution_node_id")
    if isinstance(node_id, str) and node_id.startswith("node_"):
        pass
    else:
        node_id = None

    has_node = runtime == "execution_node" or bool(node_id)
    trust = trust_boundary_for_runtime(
        runtime=runtime,
        locality=str(descriptor.locality),
        has_execution_node_id=bool(node_id),
    )

    secondary = [str(cap) for cap in (descriptor.secondary_capabilities or ())]
    supported = [str(op) for op in (descriptor.supported_operations or ())]

    device_class = "unknown"
    memory_available: int | None = None
    memory_total: int | None = None
    latency: int | None = None
    availability = "available"

    if has_node and node_id and node_id in nodes_by_id:
        node = nodes_by_id[node_id]
        availability = str(node.availability)
        device_class = str(getattr(node.hardware, "device_class", "unknown") or "unknown")
        memory_available = getattr(node.resources, "memory_available_mb", None)
        memory_total = getattr(node.resources, "memory_total_mb", None)
        latency = getattr(node.health, "latency_ms", None)
        # secondary_capabilities from node.capabilities excluding primary
        primary = str(descriptor.primary_capability)
        node_caps = [str(item) for item in (node.capabilities or []) if str(item) != primary]
        secondary = node_caps[:8]
    elif not has_node:
        memory_available, memory_total, device_class, latency = _local_resources(
            limits,
            settings,
        )
        if runtime not in CONTROLLER_LOCAL_RUNTIMES and str(descriptor.locality) == "remote":
            # Public remote descriptor: do not apply local resource hints.
            memory_available = _limit_int(limits, "memory_available_mb")
            memory_total = _limit_int(limits, "memory_total_mb")
            device_class = _limit_device(limits) or "unknown"
            latency = _limit_int(limits, "estimated_latency_ms")

    locality = "remote" if str(descriptor.locality) == "remote" else "local"
    try:
        return SchedulingCandidateV1(
            model_id=descriptor.id,
            runtime=runtime,
            primary_capability=str(descriptor.primary_capability),
            secondary_capabilities=secondary,
            supported_operations=supported,
            trust_boundary=trust,
            node_id=node_id,
            device_class=device_class if device_class in {"cpu", "igpu", "dgpu", "unknown"} else "unknown",
            memory_available_mb=memory_available,
            memory_total_mb=memory_total,
            estimated_latency_ms=latency,
            availability=availability if availability in {"available", "busy", "draining", "unavailable"} else "unavailable",
            status=str(descriptor.status),
            locality=locality,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "scheduling candidate projection skipped",
            extra={"model_id": getattr(descriptor, "id", None), "error_type": type(exc).__name__},
        )
        return None


def _local_resources(
    limits: dict[str, Any],
    settings: SchedulingSettings,
) -> tuple[int | None, int | None, str, int | None]:
    """Merge descriptor limits with optional AI_SCHEDULING_LOCAL_* hints."""
    memory_available = _limit_int(limits, "memory_available_mb")
    memory_total = _limit_int(limits, "memory_total_mb")
    device_class = _limit_device(limits) or "unknown"
    latency = _limit_int(limits, "estimated_latency_ms")

    if memory_available is None:
        memory_available = settings.local_memory_available_mb
    if memory_total is None:
        memory_total = settings.local_memory_total_mb
    if device_class == "unknown" and settings.local_device_class is not None:
        device_class = settings.local_device_class
    if latency is None:
        latency = settings.local_estimated_latency_ms
    return memory_available, memory_total, device_class, latency


def _limit_int(limits: dict[str, Any], key: str) -> int | None:
    value = limits.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _limit_device(limits: dict[str, Any]) -> str | None:
    value = limits.get("device_class")
    if value in {"cpu", "igpu", "dgpu", "unknown"}:
        return str(value)
    return None
