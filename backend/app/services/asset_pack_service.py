"""Thin facade for asset-pack generate / regenerate orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from app.services.asset_pack_generate import (
    generate_asset_pack,
    regenerate_asset_pack_slots,
)
from app.services.asset_pack_store import AssetPackRecord

logger = logging.getLogger(__name__)


async def generate_pack(
    pack_id: str,
    *,
    expected_plan_digest: str,
    expected_revision: int,
    db_path: Path | None = None,
) -> AssetPackRecord:
    logger.debug(
        "Asset pack generate facade",
        extra={
            "pack_id": pack_id[:16],
            "digest_prefix": expected_plan_digest[:12],
            "expected_revision": expected_revision,
        },
    )
    return await generate_asset_pack(
        pack_id,
        expected_plan_digest=expected_plan_digest,
        expected_revision=expected_revision,
        db_path=db_path,
    )


async def regenerate_pack_slots(
    pack_id: str,
    *,
    slot_ids: list[str],
    expected_revision: int,
    db_path: Path | None = None,
) -> AssetPackRecord:
    logger.debug(
        "Asset pack regenerate facade",
        extra={"pack_id": pack_id[:16], "slot_count": len(slot_ids)},
    )
    return await regenerate_asset_pack_slots(
        pack_id,
        slot_ids=slot_ids,
        expected_revision=expected_revision,
        db_path=db_path,
    )


__all__ = ["generate_pack", "regenerate_pack_slots"]
