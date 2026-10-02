"""Copy selected scores into ``PERSONAL_COMPOSER_ROOT/<id>/snapshot/``.

The trainer reads that directory only. This module does not start a thread
and does not import torch. The copy is not a ``DATASET_ROOT`` corpus.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.personal_composer_schemas import (
    PersonalDatasetSnapshotItemV1,
    PersonalDatasetSnapshotV1,
    PersonalComposerError,
    canonical_personal_json,
)
from app.personal_composer_settings import (
    PersonalComposerSettings,
    assert_personal_composer_storage_root,
)
from app.services.composition_snapshot_encoding import (
    composition_snapshot_fingerprint,
    snapshot_fingerprint_log_prefix,
)
from app.storage_root_policy import StorageRootError

logger = logging.getLogger(__name__)

_FILENAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


def snapshot_relative_path(project_id: str, used: set[str]) -> str:
    """File name under ``snapshot/`` with no directories."""
    cleaned = re.sub(r"[^a-z0-9._-]", "-", project_id.strip().lower())
    cleaned = cleaned.strip("-.") or "project"
    if not cleaned[0].isalnum():
        cleaned = f"p{cleaned}"
    cleaned = cleaned[:70]
    if _FILENAME.fullmatch(cleaned) is None:
        cleaned = "project"
    candidate = f"{cleaned}.json"
    suffix = 2
    while candidate in used:
        candidate = f"{cleaned[:60]}-{suffix}.json"
        suffix += 1
    used.add(candidate)
    return candidate


def snapshot_version_for_items(items: list[dict[str, str]]) -> str:
    """SHA-256 of the canonical item index, before the version field is added."""
    body = canonical_personal_json({"items": items})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def event_count(composition: CompositionV2) -> int:
    return sum(len(track.events) for track in composition.tracks)


def plan_snapshot_documents(
    *,
    adapter_id: str,
    documents: list[tuple[str, CompositionV2]],
) -> tuple[PersonalDatasetSnapshotV1, list[tuple[str, str]]]:
    """Build the index and score text in memory. Does not create directories."""
    if not documents:
        raise PersonalComposerError(
            "personal_snapshot_empty",
            "A selected score has no note events.",
            http_status=422,
        )
    used: set[str] = set()
    item_payloads: list[dict[str, str]] = []
    encoded: list[tuple[str, str]] = []
    for project_id, composition in documents:
        if event_count(composition) == 0:
            logger.warning(
                "Personal composer snapshot empty",
                extra={"code": "personal_snapshot_empty", "project_id": project_id},
            )
            raise PersonalComposerError(
                "personal_snapshot_empty",
                "A selected score has no note events.",
                http_status=422,
                details={"project_id": project_id},
            )
        relative = snapshot_relative_path(project_id, used)
        fingerprint = composition_snapshot_fingerprint(composition)
        text = json.dumps(composition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        item_payloads.append(
            {
                "project_id": project_id,
                "composition_fingerprint": fingerprint,
                "relative_path": relative,
            }
        )
        encoded.append((relative, text))
    version = snapshot_version_for_items(item_payloads)
    snapshot = PersonalDatasetSnapshotV1(
        adapter_id=adapter_id,
        snapshot_version=version,
        items=[PersonalDatasetSnapshotItemV1.model_validate(item) for item in item_payloads],
    )
    return snapshot, encoded


def persist_snapshot_documents(
    *,
    snapshot: PersonalDatasetSnapshotV1,
    encoded: list[tuple[str, str]],
    settings: PersonalComposerSettings,
    env: dict[str, str] | None = None,
) -> PersonalDatasetSnapshotV1:
    """Write the snapshot directory after ``reject_storage_root`` succeeds."""
    try:
        root = assert_personal_composer_storage_root(env, root=settings.root)
    except StorageRootError as exc:
        logger.warning(
            "Personal composer storage root rejected",
            extra={"code": "personal_storage_root_rejected", "reason": exc.reason},
        )
        raise PersonalComposerError(
            "personal_storage_root_rejected",
            "Personal composer storage root was rejected.",
            http_status=500,
        ) from exc
    snapshot_dir = root / snapshot.adapter_id / "snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for relative, text in encoded:
        (snapshot_dir / relative).write_text(text, encoding="utf-8")
    (snapshot_dir / "index.json").write_text(
        json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    logger.info(
        "Personal composer snapshot written",
        extra={
            "adapter_id": snapshot.adapter_id,
            "project_count": len(encoded),
            "snapshot_fingerprint_log_prefix": snapshot_fingerprint_log_prefix(snapshot.snapshot_version),
        },
    )
    return snapshot
