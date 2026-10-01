"""Pure edits for a musical universe document.

No SQLite and no FastAPI. Creating a theme does not read a score; the caller
marks ``source_checked`` only after bind has accepted the occurrence.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.musical_universe_schemas import (
    MUSICAL_UNIVERSE_ERROR_CODES,
    AddVariantCommand,
    CreateEntityCommand,
    CreateThemeCommand,
    DeleteEntityCommand,
    DeleteVariantCommand,
    EditEntityCommand,
    MusicalUniverseCommand,
    MusicalUniverseEntityV1,
    MusicalUniverseError,
    MusicalUniverseThemeV1,
    MusicalUniverseV1,
    MusicalUniverseVariantV1,
    UniverseTransformParameters,
    parse_musical_universe,
    parse_musical_universe_command,
)

logger = logging.getLogger(__name__)


def apply_command_payload(universe: MusicalUniverseV1, payload: dict[str, Any]) -> MusicalUniverseV1:
    """Parse one command, then return a new document. A bad payload returns nothing."""
    command = parse_musical_universe_command(payload)
    return apply_musical_universe_command(universe, command)


def apply_musical_universe_command(
    universe: MusicalUniverseV1,
    command: MusicalUniverseCommand,
) -> MusicalUniverseV1:
    """Return a new document. The input universe is not mutated."""
    logger.debug(
        "Applying musical universe command",
        extra={
            "command": command.op,
            "entity_count": len(universe.entities),
            "theme_count": len(universe.themes),
        },
    )
    document = universe.model_copy(deep=True)
    entity_id: str | None = None
    theme_id: str | None = None
    if isinstance(command, CreateEntityCommand):
        entity_id = _create_entity(document, command)
    elif isinstance(command, EditEntityCommand):
        entity_id = _edit_entity(document, command)
    elif isinstance(command, DeleteEntityCommand):
        entity_id = _delete_entity(document, command)
    elif isinstance(command, CreateThemeCommand):
        theme_id = _create_theme(document, command)
    elif isinstance(command, AddVariantCommand):
        theme_id = _add_variant(document, command)
    elif isinstance(command, DeleteVariantCommand):
        theme_id = _delete_variant(document, command)
    else:
        logger.debug(
            "Musical universe command rejected",
            extra={"command": getattr(command, "op", None), "code": "musical_universe_invalid"},
        )
        raise MusicalUniverseError(
            "musical_universe_invalid",
            MUSICAL_UNIVERSE_ERROR_CODES["musical_universe_invalid"],
            http_status=422,
        )
    parsed = parse_musical_universe(document.model_dump(mode="json"))
    logger.debug(
        "Musical universe command applied",
        extra={
            "command": command.op,
            "entity_id": entity_id,
            "theme_id": theme_id,
            "entity_count": len(parsed.entities),
            "theme_count": len(parsed.themes),
            "variant_count": sum(len(theme.variants) for theme in parsed.themes),
        },
    )
    return parsed


def _create_entity(document: MusicalUniverseV1, command: CreateEntityCommand) -> str:
    if len(document.entities) >= 64:
        _invalid("entity cap")
    entity_id = _fresh_id("ent_", {item.id for item in document.entities})
    document.entities.append(
        MusicalUniverseEntityV1(
            id=entity_id,
            kind=command.kind,
            label=command.label,
            subject_entity_id=command.subject_entity_id,
            object_entity_id=command.object_entity_id,
            motif_refs=list(command.motif_refs),
            harmony_refs=list(command.harmony_refs),
            track_refs=list(command.track_refs),
            keys=list(command.keys),
            time_signature=command.time_signature,
            texture=command.texture,
            catalog_instrument_ids=list(command.catalog_instrument_ids),
            known_operations=list(command.known_operations),
        )
    )
    return entity_id


def _edit_entity(document: MusicalUniverseV1, command: EditEntityCommand) -> str:
    index = _entity_index(document, command.entity_id)
    current = document.entities[index]
    document.entities[index] = MusicalUniverseEntityV1(
        id=current.id,
        kind=current.kind,
        label=command.label,
        subject_entity_id=command.subject_entity_id,
        object_entity_id=command.object_entity_id,
        motif_refs=list(command.motif_refs),
        harmony_refs=list(command.harmony_refs),
        track_refs=list(command.track_refs),
        keys=list(command.keys),
        time_signature=command.time_signature,
        texture=command.texture,
        catalog_instrument_ids=list(command.catalog_instrument_ids),
        known_operations=list(command.known_operations),
    )
    return current.id


def _delete_entity(document: MusicalUniverseV1, command: DeleteEntityCommand) -> str:
    if any(theme.entity_id == command.entity_id for theme in document.themes):
        logger.debug(
            "Musical universe command rejected",
            extra={"command": command.op, "entity_id": command.entity_id, "code": "universe_entity_in_use"},
        )
        raise MusicalUniverseError(
            "universe_entity_in_use",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_entity_in_use"],
            http_status=409,
            details={"entity_id": command.entity_id},
        )
    index = _entity_index(document, command.entity_id)
    document.entities.pop(index)
    return command.entity_id


def _create_theme(document: MusicalUniverseV1, command: CreateThemeCommand) -> str:
    if command.source_checked is not True:
        _invalid("source_checked")
    if command.entity_id not in {item.id for item in document.entities}:
        _invalid("entity_id")
    if len(document.themes) >= 64:
        _invalid("theme cap")
    theme_id = _fresh_id("theme_", {item.id for item in document.themes})
    document.themes.append(
        MusicalUniverseThemeV1(
            id=theme_id,
            entity_id=command.entity_id,
            label=command.label,
            source=command.source,
            source_fingerprint=command.source_fingerprint,
        )
    )
    return theme_id


def _add_variant(document: MusicalUniverseV1, command: AddVariantCommand) -> str:
    theme = _theme(document, command.theme_id)
    if len(theme.variants) >= 32:
        _invalid("variant cap")
    variant_id = _fresh_id("var_", {item.id for item in theme.variants})
    theme.variants.append(
        MusicalUniverseVariantV1(
            id=variant_id,
            label=command.label,
            operation=command.operation,
            parameters=command.parameters,
            source_project_id=theme.source.project_id,
            source_motif_id=theme.source.motif_id,
            source_occurrence_id=theme.source.occurrence_id,
        )
    )
    return theme.id


def _delete_variant(document: MusicalUniverseV1, command: DeleteVariantCommand) -> str:
    theme = _theme(document, command.theme_id)
    if any(usage.variant_id == command.variant_id for usage in theme.usages):
        logger.debug(
            "Musical universe command rejected",
            extra={
                "command": command.op,
                "theme_id": command.theme_id,
                "code": "universe_variant_in_use",
            },
        )
        raise MusicalUniverseError(
            "universe_variant_in_use",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_variant_in_use"],
            http_status=409,
            details={"variant_id": command.variant_id},
        )
    index = next((i for i, item in enumerate(theme.variants) if item.id == command.variant_id), None)
    if index is None:
        _invalid("variant_id")
    theme.variants.pop(index)
    return theme.id


def _entity_index(document: MusicalUniverseV1, entity_id: str) -> int:
    index = next((i for i, item in enumerate(document.entities) if item.id == entity_id), None)
    if index is None:
        _invalid("entity_id")
    return index


def _theme(document: MusicalUniverseV1, theme_id: str) -> MusicalUniverseThemeV1:
    theme = next((item for item in document.themes if item.id == theme_id), None)
    if theme is None:
        _invalid("theme_id")
    return theme


def _fresh_id(prefix: str, existing: set[str]) -> str:
    for _ in range(8):
        candidate = f"{prefix}{secrets.token_hex(4)}"
        if candidate not in existing:
            return candidate
    _invalid("id")
    return ""


def _invalid(field: str) -> None:
    logger.debug(
        "Musical universe command rejected",
        extra={"code": "musical_universe_invalid", "field": field},
    )
    raise MusicalUniverseError(
        "musical_universe_invalid",
        MUSICAL_UNIVERSE_ERROR_CODES["musical_universe_invalid"],
        http_status=422,
        details={"field": field},
    )


def empty_parameters() -> UniverseTransformParameters:
    return UniverseTransformParameters()
