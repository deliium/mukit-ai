"""Versioned practical GM arrangement instrument catalog.

Loads a packaged curated palette (optional path override), validates strictly,
and resolves profiles without mutating persisted/imported composition semantics.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from app.composition_schemas import SUPPORTED_TRACK_ROLES
from app.services.import_instruments import GM_PROGRAM_BY_NUMBER
from app.services.instrument_identity import (
    normalize_instrument_identity,
    normalize_label_text,
)


logger = logging.getLogger(__name__)

CATALOG_VERSION = "arrangement.instruments.v1"
RANGE_POLICY_VERSION = "arrangement.ranges.v1"
ENV_CATALOG_PATH = "ARRANGEMENT_INSTRUMENT_CATALOG_PATH"

RangePolicy = Literal["absolute", "unbounded", "unknown"]
PathCategory = Literal[
    "packaged",
    "override",
    "relative_rejected",
    "missing",
    "unreadable",
    "invalid",
]

_PACKAGED_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "arrangement_instruments.v1.json"
)

_REQUIRED_PROFILE_KEYS = frozenset(
    {
        "instrument_id",
        "display_name",
        "aliases",
        "midi_program",
        "gm_family",
        "compatibility_identity",
        "compatibility_family",
        "is_drum",
        "range_policy",
        "playable_low",
        "playable_high",
        "preferred_low",
        "preferred_high",
        "suggested_roles",
    }
)

_RANGE_FIELDS = (
    "playable_low",
    "playable_high",
    "preferred_low",
    "preferred_high",
)

_VALIDATION_CODES = frozenset(
    {
        "catalog_invalid_json",
        "catalog_invalid_schema",
        "catalog_invalid_version",
        "catalog_duplicate_id",
        "catalog_duplicate_alias",
        "catalog_duplicate_program",
        "catalog_invalid_program",
        "catalog_invalid_range",
        "catalog_invalid_role",
        "catalog_invalid_path",
        "catalog_missing_file",
        "catalog_unreadable",
    }
)


class InstrumentCatalogError(ValueError):
    """Raised when the arrangement instrument catalog cannot be loaded or validated."""

    def __init__(self, message: str, *, code: str, path_category: PathCategory | None = None):
        super().__init__(message)
        if code not in _VALIDATION_CODES:
            code = "catalog_invalid_schema"
        self.code = code
        self.path_category = path_category


@dataclass(frozen=True)
class InstrumentProfile:
    """Immutable curated arrangement instrument profile."""

    instrument_id: str
    display_name: str
    aliases: tuple[str, ...]
    midi_program: int
    gm_family: str
    compatibility_identity: str
    compatibility_family: str
    is_drum: bool
    range_policy: RangePolicy
    playable_low: int | None
    playable_high: int | None
    preferred_low: int | None
    preferred_high: int | None
    suggested_roles: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class ArrangementInstrumentCatalog:
    """Loaded, validated arrangement instrument catalog."""

    catalog_version: str
    range_policy_version: str
    profiles: tuple[InstrumentProfile, ...]
    fingerprint: str
    source_path_category: PathCategory
    source_path: str


@dataclass(frozen=True)
class CatalogMeta:
    """Public catalog metadata (no profile payloads)."""

    catalog_version: str
    range_policy_version: str
    fingerprint: str
    profile_count: int
    source_path_category: PathCategory


@dataclass(frozen=True)
class BaselineInstrumentMismatch:
    """Non-mutating baseline mismatch between a V2 track and the catalog."""

    track_id: str
    code: str
    message: str
    resolved_instrument_id: str | None = None
    expected_midi_program: int | None = None
    actual_midi_program: int | None = None
    expected_is_drum: bool | None = None
    actual_is_drum: bool | None = None
    expected_channel: int | None = None
    actual_channel: int | None = None


_cached_catalog: ArrangementInstrumentCatalog | None = None
_cached_env_path: str | None = None


def clear_catalog_cache() -> None:
    """Clear the module-level catalog cache (tests / reload)."""
    global _cached_catalog, _cached_env_path
    _cached_catalog = None
    _cached_env_path = None


def reload_catalog(*, env: Mapping[str, str] | None = None) -> ArrangementInstrumentCatalog:
    """Force-reload and cache the catalog from packaged fixture or env override."""
    clear_catalog_cache()
    return load_catalog(env=env)


def load_catalog(*, env: Mapping[str, str] | None = None) -> ArrangementInstrumentCatalog:
    """Load and cache the arrangement instrument catalog."""
    global _cached_catalog, _cached_env_path
    source = env if env is not None else os.environ
    override = (source.get(ENV_CATALOG_PATH) or "").strip() or None
    cache_key = override or ""
    if _cached_catalog is not None and _cached_env_path == cache_key and env is None:
        return _cached_catalog

    catalog = _load_catalog_uncached(override_path=override)
    if env is None:
        _cached_catalog = catalog
        _cached_env_path = cache_key
    return catalog


def get_catalog(*, env: Mapping[str, str] | None = None) -> ArrangementInstrumentCatalog:
    """Return the cached catalog, loading it if needed."""
    return load_catalog(env=env)


def list_profiles(*, env: Mapping[str, str] | None = None) -> tuple[InstrumentProfile, ...]:
    """Return curated profiles in catalog order."""
    return get_catalog(env=env).profiles


def get_catalog_meta(*, env: Mapping[str, str] | None = None) -> CatalogMeta:
    """Return catalog version/fingerprint metadata without profile payloads."""
    catalog = get_catalog(env=env)
    return CatalogMeta(
        catalog_version=catalog.catalog_version,
        range_policy_version=catalog.range_policy_version,
        fingerprint=catalog.fingerprint,
        profile_count=len(catalog.profiles),
        source_path_category=catalog.source_path_category,
    )


def canonical_track_roles() -> list[str]:
    """Return the full sorted SUPPORTED_TRACK_ROLES vocabulary (not suggestions)."""
    return sorted(SUPPORTED_TRACK_ROLES)


def resolve_profile(
    *,
    instrument_id: str | None = None,
    alias: str | None = None,
    midi_program: int | None = None,
    instrument_label: str | None = None,
    is_drum: bool | None = None,
    catalog: ArrangementInstrumentCatalog | None = None,
) -> InstrumentProfile | None:
    """Resolve a curated profile.

    Order: exact profile ID → exact normalized alias → exact midi_program among
    curated (drum-aware) → coarse identity via ``normalize_instrument_identity``.
    """
    catalog = catalog or get_catalog()
    by_id = {profile.instrument_id: profile for profile in catalog.profiles}
    if instrument_id:
        exact = by_id.get(instrument_id.strip())
        if exact is not None:
            return exact

    if alias is not None:
        normalized_alias = normalize_label_text(alias)
        if normalized_alias:
            for profile in catalog.profiles:
                if normalized_alias in profile.aliases:
                    return profile

    if midi_program is not None:
        program_match = _resolve_by_program(
            catalog.profiles,
            midi_program=midi_program,
            is_drum=is_drum,
        )
        if program_match is not None:
            return program_match

    label = instrument_label if instrument_label is not None else alias
    if label:
        identity = normalize_instrument_identity(label)
        if identity:
            identity_matches = [
                profile
                for profile in catalog.profiles
                if profile.compatibility_identity == identity
                and (is_drum is None or profile.is_drum is is_drum)
            ]
            if identity_matches:
                return min(
                    identity_matches,
                    key=lambda item: (item.midi_program, item.instrument_id),
                )
    return None


def resolve_profile_for_track(
    track: Any,
    *,
    catalog: ArrangementInstrumentCatalog | None = None,
) -> InstrumentProfile | None:
    """Resolve a catalog profile for a V2-like track using source-program precedence."""
    catalog = catalog or get_catalog()
    instrument = getattr(track, "instrument", None) or ""
    midi_program = getattr(track, "midi_program", None)
    is_drum = bool(getattr(track, "is_drum", False))
    # Prefer exact id / alias from instrument label before program only when the
    # label is an exact profile id or alias; otherwise source program wins.
    by_id = {profile.instrument_id: profile for profile in catalog.profiles}
    if instrument in by_id:
        return by_id[instrument]
    normalized = normalize_label_text(str(instrument))
    for profile in catalog.profiles:
        if normalized and normalized in profile.aliases:
            return profile
    if midi_program is not None:
        program_hit = _resolve_by_program(
            catalog.profiles,
            midi_program=int(midi_program),
            is_drum=is_drum,
        )
        if program_hit is not None:
            return program_hit
    return resolve_profile(
        instrument_label=str(instrument),
        is_drum=is_drum,
        catalog=catalog,
    )


def find_baseline_mismatches(
    tracks: Sequence[Any],
    *,
    catalog: ArrangementInstrumentCatalog | None = None,
) -> tuple[BaselineInstrumentMismatch, ...]:
    """Report instrument/program/channel mismatches vs catalog without mutating."""
    catalog = catalog or get_catalog()
    findings: list[BaselineInstrumentMismatch] = []
    for track in tracks:
        track_id = str(getattr(track, "id", "") or "")
        instrument = str(getattr(track, "instrument", "") or "")
        midi_program = getattr(track, "midi_program", None)
        channel = getattr(track, "channel", None)
        is_drum = bool(getattr(track, "is_drum", False))
        profile = resolve_profile_for_track(track, catalog=catalog)
        if profile is None:
            findings.append(
                BaselineInstrumentMismatch(
                    track_id=track_id,
                    code="catalog_unresolved_instrument",
                    message="Track instrument/program is outside the curated arrangement palette",
                    actual_midi_program=int(midi_program) if midi_program is not None else None,
                    actual_is_drum=is_drum,
                    actual_channel=int(channel) if channel is not None else None,
                )
            )
            continue

        if midi_program is not None and int(midi_program) != profile.midi_program and not (
            profile.is_drum and is_drum
        ):
            # Drum kits ignore program timbre; non-drum program must match profile.
            if not profile.is_drum:
                findings.append(
                    BaselineInstrumentMismatch(
                        track_id=track_id,
                        code="catalog_program_mismatch",
                        message="Track midi_program does not match resolved catalog profile",
                        resolved_instrument_id=profile.instrument_id,
                        expected_midi_program=profile.midi_program,
                        actual_midi_program=int(midi_program),
                    )
                )

        if is_drum != profile.is_drum:
            findings.append(
                BaselineInstrumentMismatch(
                    track_id=track_id,
                    code="catalog_drum_flag_mismatch",
                    message="Track is_drum does not match resolved catalog profile",
                    resolved_instrument_id=profile.instrument_id,
                    expected_is_drum=profile.is_drum,
                    actual_is_drum=is_drum,
                )
            )

        if channel is not None:
            expected_channel = 10 if profile.is_drum else None
            if profile.is_drum and int(channel) != 10:
                findings.append(
                    BaselineInstrumentMismatch(
                        track_id=track_id,
                        code="catalog_channel_mismatch",
                        message="Drum profile expects MIDI channel 10",
                        resolved_instrument_id=profile.instrument_id,
                        expected_channel=10,
                        actual_channel=int(channel),
                    )
                )
            elif not profile.is_drum and int(channel) == 10:
                findings.append(
                    BaselineInstrumentMismatch(
                        track_id=track_id,
                        code="catalog_channel_mismatch",
                        message="Non-drum catalog profile should not use channel 10",
                        resolved_instrument_id=profile.instrument_id,
                        expected_channel=expected_channel,
                        actual_channel=int(channel),
                    )
                )

        # Label vs resolved profile display/identity advisory (non-mutating).
        normalized_label = normalize_label_text(instrument)
        label_ok = (
            instrument == profile.instrument_id
            or normalized_label in profile.aliases
            or normalize_instrument_identity(instrument) == profile.compatibility_identity
        )
        if instrument and not label_ok:
            findings.append(
                BaselineInstrumentMismatch(
                    track_id=track_id,
                    code="catalog_instrument_label_mismatch",
                    message="Track instrument label does not match resolved catalog profile labels",
                    resolved_instrument_id=profile.instrument_id,
                    actual_midi_program=int(midi_program) if midi_program is not None else None,
                )
            )
    return tuple(findings)


def _resolve_by_program(
    profiles: Sequence[InstrumentProfile],
    *,
    midi_program: int,
    is_drum: bool | None,
) -> InstrumentProfile | None:
    matches = [
        profile
        for profile in profiles
        if profile.midi_program == midi_program
        and (is_drum is None or profile.is_drum is is_drum)
    ]
    if not matches and is_drum is None:
        # Prefer non-drum when both a melodic program-0 piano and drum kit exist.
        non_drum = [profile for profile in profiles if profile.midi_program == midi_program and not profile.is_drum]
        if non_drum:
            return non_drum[0]
        drum = [profile for profile in profiles if profile.midi_program == midi_program and profile.is_drum]
        return drum[0] if drum else None
    if not matches:
        return None
    return matches[0]


def _load_catalog_uncached(*, override_path: str | None) -> ArrangementInstrumentCatalog:
    path, path_category = _resolve_catalog_path(override_path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error(
            "Arrangement instrument catalog unreadable",
            extra={
                "path_category": "unreadable",
                "validation_code": "catalog_unreadable",
            },
        )
        raise InstrumentCatalogError(
            "Arrangement instrument catalog could not be read",
            code="catalog_unreadable",
            path_category="unreadable",
        ) from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error(
            "Arrangement instrument catalog JSON invalid",
            extra={
                "path_category": path_category,
                "validation_code": "catalog_invalid_json",
            },
        )
        raise InstrumentCatalogError(
            "Arrangement instrument catalog JSON is invalid",
            code="catalog_invalid_json",
            path_category=path_category,
        ) from exc

    if not isinstance(payload, dict):
        raise _schema_error("Catalog root must be an object", path_category)

    catalog_version = payload.get("catalog_version")
    range_policy_version = payload.get("range_policy_version")
    instruments = payload.get("instruments")
    if catalog_version != CATALOG_VERSION:
        logger.error(
            "Arrangement instrument catalog version mismatch",
            extra={
                "path_category": path_category,
                "validation_code": "catalog_invalid_version",
            },
        )
        raise InstrumentCatalogError(
            f"Expected catalog_version {CATALOG_VERSION!r}",
            code="catalog_invalid_version",
            path_category=path_category,
        )
    if range_policy_version != RANGE_POLICY_VERSION:
        raise InstrumentCatalogError(
            f"Expected range_policy_version {RANGE_POLICY_VERSION!r}",
            code="catalog_invalid_version",
            path_category=path_category,
        )
    if not isinstance(instruments, list) or not instruments:
        raise _schema_error("Catalog instruments must be a non-empty array", path_category)

    profiles: list[InstrumentProfile] = []
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()
    seen_programs: set[tuple[int, bool]] = set()

    for index, raw_profile in enumerate(instruments):
        if not isinstance(raw_profile, dict):
            raise _schema_error(f"Instrument at index {index} must be an object", path_category)
        missing = _REQUIRED_PROFILE_KEYS - set(raw_profile)
        if missing:
            raise _schema_error(
                f"Instrument at index {index} missing keys: {sorted(missing)}",
                path_category,
            )

        instrument_id = raw_profile["instrument_id"]
        if not isinstance(instrument_id, str) or not instrument_id.strip():
            raise _schema_error(f"Invalid instrument_id at index {index}", path_category)
        instrument_id = instrument_id.strip()
        if instrument_id in seen_ids:
            raise InstrumentCatalogError(
                f"Duplicate instrument_id {instrument_id!r}",
                code="catalog_duplicate_id",
                path_category=path_category,
            )
        seen_ids.add(instrument_id)

        display_name = raw_profile["display_name"]
        if not isinstance(display_name, str) or not display_name.strip():
            raise _schema_error(f"Invalid display_name for {instrument_id}", path_category)

        aliases_raw = raw_profile["aliases"]
        if not isinstance(aliases_raw, list):
            raise _schema_error(f"aliases must be an array for {instrument_id}", path_category)
        aliases: list[str] = []
        for alias in aliases_raw:
            if not isinstance(alias, str):
                raise _schema_error(f"Alias must be a string for {instrument_id}", path_category)
            normalized = normalize_label_text(alias)
            if not normalized:
                raise _schema_error(f"Empty alias for {instrument_id}", path_category)
            if normalized in seen_aliases:
                raise InstrumentCatalogError(
                    f"Duplicate alias {normalized!r}",
                    code="catalog_duplicate_alias",
                    path_category=path_category,
                )
            seen_aliases.add(normalized)
            aliases.append(normalized)

        is_drum = raw_profile["is_drum"]
        if not isinstance(is_drum, bool):
            raise _schema_error(f"is_drum must be bool for {instrument_id}", path_category)

        midi_program = raw_profile["midi_program"]
        if not isinstance(midi_program, int) or isinstance(midi_program, bool):
            raise _schema_error(f"midi_program must be int for {instrument_id}", path_category)
        if midi_program < 0 or midi_program > 127 or midi_program not in GM_PROGRAM_BY_NUMBER:
            raise InstrumentCatalogError(
                f"Invalid midi_program {midi_program} for {instrument_id}",
                code="catalog_invalid_program",
                path_category=path_category,
            )
        program_key = (midi_program, is_drum)
        if program_key in seen_programs:
            raise InstrumentCatalogError(
                f"Duplicate midi_program/is_drum {program_key} for {instrument_id}",
                code="catalog_duplicate_program",
                path_category=path_category,
            )
        seen_programs.add(program_key)

        range_policy = raw_profile["range_policy"]
        if range_policy not in {"absolute", "unbounded", "unknown"}:
            raise _schema_error(f"Invalid range_policy for {instrument_id}", path_category)

        ranges = {field: raw_profile[field] for field in _RANGE_FIELDS}
        for field, value in ranges.items():
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 127
            ):
                raise InstrumentCatalogError(
                    f"Invalid {field} for {instrument_id}",
                    code="catalog_invalid_range",
                    path_category=path_category,
                )

        if range_policy == "absolute":
            if any(ranges[field] is None for field in _RANGE_FIELDS):
                raise InstrumentCatalogError(
                    f"Absolute range policy requires all range fields for {instrument_id}",
                    code="catalog_invalid_range",
                    path_category=path_category,
                )
            playable_low = ranges["playable_low"]
            preferred_low = ranges["preferred_low"]
            preferred_high = ranges["preferred_high"]
            playable_high = ranges["playable_high"]
            assert playable_low is not None and preferred_low is not None
            assert preferred_high is not None and playable_high is not None
            if not (0 <= playable_low <= preferred_low <= preferred_high <= playable_high <= 127):
                raise InstrumentCatalogError(
                    f"Range ordering violated for {instrument_id}",
                    code="catalog_invalid_range",
                    path_category=path_category,
                )
        else:
            # unbounded / unknown may use null ranges; if any provided, all must be.
            present = [value is not None for value in ranges.values()]
            if any(present) and not all(present):
                raise InstrumentCatalogError(
                    f"Partial ranges not allowed for {instrument_id}",
                    code="catalog_invalid_range",
                    path_category=path_category,
                )

        gm_family = raw_profile["gm_family"]
        compatibility_identity = raw_profile["compatibility_identity"]
        compatibility_family = raw_profile["compatibility_family"]
        for field_name, value in (
            ("gm_family", gm_family),
            ("compatibility_identity", compatibility_identity),
            ("compatibility_family", compatibility_family),
        ):
            if not isinstance(value, str) or not value.strip():
                raise _schema_error(f"Invalid {field_name} for {instrument_id}", path_category)

        roles_raw = raw_profile["suggested_roles"]
        if not isinstance(roles_raw, list):
            raise _schema_error(f"suggested_roles must be an array for {instrument_id}", path_category)
        suggested_roles: list[str] = []
        for role in roles_raw:
            if not isinstance(role, str) or role not in SUPPORTED_TRACK_ROLES:
                raise InstrumentCatalogError(
                    f"Invalid suggested role {role!r} for {instrument_id}",
                    code="catalog_invalid_role",
                    path_category=path_category,
                )
            if role not in suggested_roles:
                suggested_roles.append(role)

        profile_payload = {
            "instrument_id": instrument_id,
            "display_name": display_name.strip(),
            "aliases": aliases,
            "midi_program": midi_program,
            "gm_family": gm_family.strip(),
            "compatibility_identity": compatibility_identity.strip(),
            "compatibility_family": compatibility_family.strip(),
            "is_drum": is_drum,
            "range_policy": range_policy,
            "playable_low": ranges["playable_low"],
            "playable_high": ranges["playable_high"],
            "preferred_low": ranges["preferred_low"],
            "preferred_high": ranges["preferred_high"],
            "suggested_roles": suggested_roles,
        }
        fingerprint = _fingerprint_payload(profile_payload)
        profiles.append(
            InstrumentProfile(
                instrument_id=instrument_id,
                display_name=display_name.strip(),
                aliases=tuple(aliases),
                midi_program=midi_program,
                gm_family=gm_family.strip(),
                compatibility_identity=compatibility_identity.strip(),
                compatibility_family=compatibility_family.strip(),
                is_drum=is_drum,
                range_policy=range_policy,  # type: ignore[arg-type]
                playable_low=ranges["playable_low"],
                playable_high=ranges["playable_high"],
                preferred_low=ranges["preferred_low"],
                preferred_high=ranges["preferred_high"],
                suggested_roles=tuple(suggested_roles),
                fingerprint=fingerprint,
            )
        )

    catalog_payload = {
        "catalog_version": catalog_version,
        "range_policy_version": range_policy_version,
        "instruments": [
            {
                "instrument_id": profile.instrument_id,
                "display_name": profile.display_name,
                "aliases": list(profile.aliases),
                "midi_program": profile.midi_program,
                "gm_family": profile.gm_family,
                "compatibility_identity": profile.compatibility_identity,
                "compatibility_family": profile.compatibility_family,
                "is_drum": profile.is_drum,
                "range_policy": profile.range_policy,
                "playable_low": profile.playable_low,
                "playable_high": profile.playable_high,
                "preferred_low": profile.preferred_low,
                "preferred_high": profile.preferred_high,
                "suggested_roles": list(profile.suggested_roles),
            }
            for profile in profiles
        ],
    }
    catalog_fingerprint = _fingerprint_payload(catalog_payload)
    catalog = ArrangementInstrumentCatalog(
        catalog_version=catalog_version,
        range_policy_version=range_policy_version,
        profiles=tuple(profiles),
        fingerprint=catalog_fingerprint,
        source_path_category=path_category,
        source_path=str(path),
    )
    logger.info(
        "Loaded arrangement instrument catalog",
        extra={
            "catalog_version": catalog.catalog_version,
            "range_policy_version": catalog.range_policy_version,
            "path_category": path_category,
            "profile_count": len(profiles),
            "catalog_fingerprint": catalog_fingerprint,
        },
    )
    logger.debug(
        "Arrangement instrument catalog diagnostics",
        extra={
            "profile_count": len(profiles),
            "alias_count": len(seen_aliases),
            "program_count": len(seen_programs),
        },
    )
    return catalog


def _resolve_catalog_path(override_path: str | None) -> tuple[Path, PathCategory]:
    if not override_path:
        return _PACKAGED_FIXTURE, "packaged"

    candidate = Path(override_path)
    if not candidate.is_absolute():
        logger.error(
            "Arrangement instrument catalog override path rejected",
            extra={
                "path_category": "relative_rejected",
                "validation_code": "catalog_invalid_path",
            },
        )
        raise InstrumentCatalogError(
            "ARRANGEMENT_INSTRUMENT_CATALOG_PATH must be an absolute path",
            code="catalog_invalid_path",
            path_category="relative_rejected",
        )
    if not candidate.exists() or not candidate.is_file():
        logger.error(
            "Arrangement instrument catalog override missing",
            extra={
                "path_category": "missing",
                "validation_code": "catalog_missing_file",
            },
        )
        raise InstrumentCatalogError(
            "ARRANGEMENT_INSTRUMENT_CATALOG_PATH does not point to a readable file",
            code="catalog_missing_file",
            path_category="missing",
        )
    return candidate, "override"


def _schema_error(message: str, path_category: PathCategory) -> InstrumentCatalogError:
    logger.error(
        "Arrangement instrument catalog schema invalid",
        extra={
            "path_category": path_category,
            "validation_code": "catalog_invalid_schema",
        },
    )
    return InstrumentCatalogError(
        message,
        code="catalog_invalid_schema",
        path_category=path_category,
    )


def _fingerprint_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
