"""Master-target presets. Goals, not loudness guarantees."""

from __future__ import annotations

import logging

from app.mix_plan_schemas import (
    MIX_PLAN_MASTER_TARGET_UNVERIFIED,
    MixMasterTargetV1,
    MixPlanMasterTargetId,
    MixPlanWarning,
)

logger = logging.getLogger(__name__)

_TARGETS: dict[str, MixMasterTargetV1] = {
    "dynamic": MixMasterTargetV1(
        target_id="dynamic",
        label="Dynamic",
        goal="Preserve crest with a light bus compressor and headroom below 0 dBFS.",
        loudness_goal_lufs=None,
        guarantee=False,
    ),
    "streaming": MixMasterTargetV1(
        target_id="streaming",
        label="Streaming",
        goal="Even level with a stronger bus compressor. Loudness aim is -14 LUFS, not a certification.",
        loudness_goal_lufs=-14.0,
        guarantee=False,
    ),
    "cinematic": MixMasterTargetV1(
        target_id="cinematic",
        label="Cinematic",
        goal="Wider image and a higher send, with lighter limiting.",
        loudness_goal_lufs=None,
        guarantee=False,
    ),
    "demo": MixMasterTargetV1(
        target_id="demo",
        label="Demo",
        goal="Conservative sketch: moderate bus compression and a mild high-pass on non-bass stems.",
        loudness_goal_lufs=None,
        guarantee=False,
    ),
}

NOT_A_GUARANTEE = "Not a guarantee"


def master_target_document(target_id: MixPlanMasterTargetId | str) -> MixMasterTargetV1:
    doc = _TARGETS[str(target_id)]
    logger.debug(
        "Mix plan master target selected",
        extra={"master_target": doc.target_id, "guarantee": False},
    )
    return doc


def streaming_unverified_warning() -> MixPlanWarning:
    return MixPlanWarning(
        code=MIX_PLAN_MASTER_TARGET_UNVERIFIED,
        message=(
            "Streaming target aims for about -14 LUFS. "
            "This render did not measure integrated loudness, so the goal is unverified."
        ),
    )
