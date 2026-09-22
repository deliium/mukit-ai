"""HTTP routes for durable Composer Profiles (``/composer-profiles``)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from app.composer_profile_schemas import (
    COMPOSER_PROFILE_EXPORT_SCHEMA,
    ComposerProfileCompareRequest,
    ComposerProfileCompareResponse,
    ComposerProfileCreateRequest,
    ComposerProfileDeriveRequest,
    ComposerProfileDeriveResponse,
    ComposerProfileError,
    ComposerProfileExportV1,
    ComposerProfileFieldDiff,
    ComposerProfileImportRequest,
    ComposerProfileImportResponse,
    ComposerProfileListItem,
    ComposerProfilePreviewRequest,
    ComposerProfilePreviewResponse,
    ComposerProfilePromoteRequest,
    ComposerProfileResetRequest,
    ComposerProfileUpdateRequest,
    ComposerProfileV1,
    DerivedPreferenceFields,
    PreferenceFields,
    map_composer_profile_error_to_http,
    preference_fields_to_dict,
)
from app.composer_profile_settings import load_composer_profile_settings
from app.services import composer_profile_store as store
from app.services.composer_profile_derive import derive_composer_profile
from app.services.composer_profile_resolve import (
    preview_profile_fragment,
    promote_derived_to_explicit,
)
from app.services.persistence_secret_guard import (
    PersistenceSecretError,
    assert_no_secret_fields,
    assert_no_secret_values,
)
from app.services import project_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/composer-profiles", tags=["composer-profiles"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _raise_profile_error(exc: ComposerProfileError) -> None:
    status, detail = map_composer_profile_error_to_http(exc)
    raise HTTPException(status_code=status, detail=detail) from exc


@router.get("", response_model=list[ComposerProfileListItem])
def list_composer_profiles() -> list[ComposerProfileListItem]:
    items = store.list_profiles()
    logger.info("Composer profiles listed", extra={"count": len(items)})
    return items


@router.post("", response_model=ComposerProfileV1, status_code=201)
def create_composer_profile(body: ComposerProfileCreateRequest) -> ComposerProfileV1:
    try:
        profile = store.create_profile(
            name=body.name,
            notes=body.notes,
            explicit=body.explicit,
            derived=body.derived,
            source_projects=body.source_projects,
        )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info(
        "Composer profile create route",
        extra={"profile_id": profile.id, "name_len": len(profile.name)},
    )
    return profile


@router.get("/{profile_id}", response_model=ComposerProfileV1)
def get_composer_profile(profile_id: str) -> ComposerProfileV1:
    try:
        profile = store.get_profile(profile_id)
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info("Composer profile get route", extra={"profile_id": profile_id})
    return profile


@router.put("/{profile_id}", response_model=ComposerProfileV1)
def update_composer_profile(
    profile_id: str, body: ComposerProfileUpdateRequest
) -> ComposerProfileV1:
    try:
        profile = store.update_profile(
            profile_id,
            expected_updated_at=body.expected_updated_at,
            name=body.name,
            notes=body.notes if body.notes is not None or body.replace_body else ...,
            explicit=body.explicit,
            derived=body.derived,
            source_projects=body.source_projects,
            replace_body=body.replace_body,
        )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info("Composer profile update route", extra={"profile_id": profile_id})
    return profile


@router.post("/{profile_id}/reset", response_model=ComposerProfileV1)
def reset_composer_profile(
    profile_id: str, body: ComposerProfileResetRequest
) -> ComposerProfileV1:
    try:
        current = store.get_profile(profile_id)
        updates: dict[str, Any] = {}
        if body.clear_explicit:
            updates["explicit"] = PreferenceFields()
        if body.clear_derived:
            updates["derived"] = DerivedPreferenceFields()
        if body.clear_sources:
            updates["source_projects"] = []
        next_profile = current.model_copy(update=updates)
        profile = store.replace_profile_body(
            profile_id,
            next_profile,
            expected_updated_at=body.expected_updated_at,
        )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info(
        "Composer profile reset route",
        extra={
            "profile_id": profile_id,
            "clear_explicit": body.clear_explicit,
            "clear_derived": body.clear_derived,
            "clear_sources": body.clear_sources,
        },
    )
    return profile


@router.post("/{profile_id}/promote", response_model=ComposerProfileV1)
def promote_composer_profile(
    profile_id: str, body: ComposerProfilePromoteRequest
) -> ComposerProfileV1:
    try:
        current = store.get_profile(profile_id)
        explicit = promote_derived_to_explicit(current, fields=body.fields)
        next_profile = current.model_copy(update={"explicit": explicit})
        profile = store.replace_profile_body(
            profile_id,
            next_profile,
            expected_updated_at=body.expected_updated_at,
        )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    field_count = len(preference_fields_to_dict(profile.explicit))
    logger.info(
        "Composer profile promote route",
        extra={"profile_id": profile_id, "explicit_field_count": field_count},
    )
    return profile


@router.delete("/{profile_id}", status_code=204)
def delete_composer_profile(profile_id: str) -> Response:
    try:
        store.delete_profile(profile_id)
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info("Composer profile delete route", extra={"profile_id": profile_id})
    return Response(status_code=204)


@router.post("/derive", response_model=ComposerProfileDeriveResponse)
def derive_composer_profiles(body: ComposerProfileDeriveRequest) -> ComposerProfileDeriveResponse:
    try:
        existing = None
        if body.target_profile_id:
            existing = store.get_profile(body.target_profile_id)
        profile, warnings = derive_composer_profile(
            list(body.sources),
            name=body.save_as or (existing.name if existing else None),
            existing=existing,
        )
        persisted = False
        if body.save_as and not body.target_profile_id:
            profile = store.create_profile(
                name=body.save_as,
                notes=profile.notes,
                explicit=profile.explicit,
                derived=profile.derived,
                source_projects=list(profile.source_projects),
            )
            persisted = True
        elif body.target_profile_id:
            if not body.expected_updated_at:
                raise ComposerProfileError(
                    "composer_profile_invalid",
                    "expected_updated_at required when targeting an existing profile",
                    http_status=422,
                )
            profile = store.replace_profile_body(
                body.target_profile_id,
                profile.model_copy(update={"id": body.target_profile_id}),
                expected_updated_at=body.expected_updated_at,
            )
            persisted = True
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info(
        "Composer profile derive route",
        extra={
            "source_count": len(body.sources),
            "persisted": persisted,
            "profile_id": profile.id,
            "warning_count": len(warnings),
        },
    )
    return ComposerProfileDeriveResponse(
        profile=profile, persisted=persisted, warnings=warnings
    )


@router.post("/{profile_id}/preview", response_model=ComposerProfilePreviewResponse)
def preview_composer_profile(
    profile_id: str, body: ComposerProfilePreviewRequest
) -> ComposerProfilePreviewResponse:
    try:
        profile = store.get_profile(profile_id)
        preview = preview_profile_fragment(profile, strength=body.strength)
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info(
        "Composer profile preview route",
        extra={
            "profile_id": profile_id,
            "strength": body.strength,
            "applied_field_count": preview.applied_field_count,
        },
    )
    return preview


def _profile_field_paths(profile: ComposerProfileV1) -> dict[str, Any]:
    flat: dict[str, Any] = {
        "name": profile.name,
        "notes": profile.notes,
    }
    for prefix, fields in (
        ("explicit", preference_fields_to_dict(profile.explicit)),
        ("derived", preference_fields_to_dict(profile.derived)),
    ):
        for key, value in fields.items():
            flat[f"{prefix}.{key}"] = value
    flat["source_project_ids"] = [s.project_id for s in profile.source_projects]
    return flat


@router.post("/compare", response_model=ComposerProfileCompareResponse)
def compare_composer_profiles(
    body: ComposerProfileCompareRequest,
) -> ComposerProfileCompareResponse:
    try:
        left = body.left
        right = body.right
        if left is None and body.left_id:
            left = store.get_profile(body.left_id)
        if right is None and body.right_id:
            right = store.get_profile(body.right_id)
        if left is None or right is None:
            raise ComposerProfileError(
                "composer_profile_invalid",
                "compare requires resolvable left and right profiles",
                http_status=422,
            )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)

    left_map = _profile_field_paths(left)
    right_map = _profile_field_paths(right)
    keys = sorted(set(left_map) | set(right_map))
    diffs: list[ComposerProfileFieldDiff] = []
    for key in keys:
        lv = left_map.get(key)
        rv = right_map.get(key)
        if lv != rv:
            diffs.append(ComposerProfileFieldDiff(path=key, left=lv, right=rv))
    equal = len(diffs) == 0
    logger.info(
        "Composer profile compare route",
        extra={"equal": equal, "diff_count": len(diffs)},
    )
    return ComposerProfileCompareResponse(equal=equal, diffs=diffs[:256])


@router.get("/{profile_id}/export", response_model=ComposerProfileExportV1)
def export_composer_profile(profile_id: str) -> ComposerProfileExportV1:
    settings = load_composer_profile_settings()
    try:
        profile = store.get_profile(profile_id)
        envelope = ComposerProfileExportV1(
            schema_version=COMPOSER_PROFILE_EXPORT_SCHEMA,
            exported_at=_utc_now_iso(),
            profile=profile,
        )
        payload = envelope.model_dump(mode="json")
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(raw.encode("utf-8")) > settings.max_export_bytes:
            raise ComposerProfileError(
                "composer_profile_cap_exceeded",
                "Export exceeds configured size cap",
                http_status=422,
                details={"max_export_bytes": settings.max_export_bytes},
            )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    logger.info("Composer profile export route", extra={"profile_id": profile_id})
    return envelope


@router.post("/import", response_model=ComposerProfileImportResponse)
def import_composer_profile(body: ComposerProfileImportRequest) -> ComposerProfileImportResponse:
    settings = load_composer_profile_settings()
    warnings: list[str] = []
    try:
        raw = json.dumps(body.envelope.model_dump(mode="json"), ensure_ascii=False)
        if len(raw.encode("utf-8")) > settings.max_export_bytes:
            logger.warning(
                "Composer profile import oversized",
                extra={"code": "composer_profile_cap_exceeded"},
            )
            raise ComposerProfileError(
                "composer_profile_cap_exceeded",
                "Import exceeds configured size cap",
                http_status=422,
                details={"max_export_bytes": settings.max_export_bytes},
            )
        try:
            assert_no_secret_fields(
                body.envelope.model_dump(mode="json"), context="composer_profile.import"
            )
            assert_no_secret_values(raw, field_name="import_envelope")
        except PersistenceSecretError as exc:
            raise ComposerProfileError(
                "composer_profile_forbidden_payload",
                str(exc),
                http_status=422,
            ) from exc

        incoming = body.envelope.profile
        name = body.name or incoming.name
        for source in incoming.source_projects:
            try:
                project_store.get_project(source.project_id)
            except project_store.ProjectNotFoundError:
                warnings.append(f"source_project_missing:{source.project_id}")

        if body.replace_profile_id:
            if not body.expected_updated_at:
                raise ComposerProfileError(
                    "composer_profile_invalid",
                    "expected_updated_at required for replace import",
                    http_status=422,
                )
            current = store.get_profile(body.replace_profile_id)
            replaced = current.model_copy(
                update={
                    "name": name,
                    "notes": incoming.notes,
                    "explicit": incoming.explicit,
                    "derived": incoming.derived,
                    "source_projects": list(incoming.source_projects),
                }
            )
            profile = store.replace_profile_body(
                body.replace_profile_id,
                replaced,
                expected_updated_at=body.expected_updated_at,
            )
        else:
            profile = store.create_profile(
                name=name,
                notes=incoming.notes,
                explicit=incoming.explicit,
                derived=incoming.derived,
                source_projects=list(incoming.source_projects),
            )
    except ComposerProfileError as exc:
        _raise_profile_error(exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Composer profile import invalid",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "code": "composer_profile_import_invalid",
                "message": "Import envelope failed validation",
            },
        ) from exc

    logger.info(
        "Composer profile import route",
        extra={
            "profile_id": profile.id,
            "warning_count": len(warnings),
            "replaced": bool(body.replace_profile_id),
        },
    )
    return ComposerProfileImportResponse(profile=profile, warnings=warnings)
