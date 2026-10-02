"""Strict ``musical.dependency`` contracts.

Edges name derived assets. They are not a score and they are not
``composition.v5``. Note events, pitch lists, and WAV bytes never belong here.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

DEPENDENCY_EDGE_SCHEMA: Literal["musical.dependency.edge.v1"] = "musical.dependency.edge.v1"
DEPENDENCY_GRAPH_SCHEMA: Literal["musical.dependency.graph.v1"] = "musical.dependency.graph.v1"
DEPENDENCY_IMPACT_SCHEMA: Literal["musical.dependency.impact.v1"] = "musical.dependency.impact.v1"
DEPENDENCY_UPDATE_OFFER_SCHEMA: Literal["musical.dependency.update_offer.v1"] = (
    "musical.dependency.update_offer.v1"
)

FORBIDDEN_NOTE_KEYS: frozenset[str] = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
        "composition",
        "composition_json",
    }
)

EDGE_ID_RE = re.compile(r"^dep_[0-9a-f]{16}$")
UNIVERSE_ID_RE = re.compile(r"^muniv_[0-9a-f]{16}$")
THEME_ID_RE = re.compile(r"^theme_[0-9a-f]{8}$")
VARIANT_ID_RE = re.compile(r"^var_[0-9a-f]{8}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DependencyType = Literal[
    "motif_derived_from",
    "arrangement_of",
    "variation_of",
    "rendered_from",
    "transcribed_from",
    "reference_conditioned_by",
]
UpstreamKind = Literal["theme", "motif_occurrence", "revision", "project", "audio_asset"]
DownstreamKind = Literal[
    "motif_occurrence",
    "revision",
    "project",
    "neural_render",
    "neural_stem_set",
]
DependencyStatus = Literal["fresh", "stale", "missing", "downstream_of_stale"]
UpdateAction = Literal[
    "reuse_theme",
    "open_motif",
    "open_arrangement",
    "open_development",
    "open_neural_render",
    "open_transcription",
    "open_reference",
    "review_only",
]
NodeKind = Literal[
    "theme",
    "motif_occurrence",
    "revision",
    "project",
    "audio_asset",
    "neural_render",
    "neural_stem_set",
]

_ENDPOINT_PAIRS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "motif_derived_from": (frozenset({"motif_occurrence", "theme"}), frozenset({"motif_occurrence"})),
    "arrangement_of": (frozenset({"revision", "motif_occurrence"}), frozenset({"revision"})),
    "variation_of": (
        frozenset({"theme", "revision"}),
        frozenset({"motif_occurrence", "revision"}),
    ),
    "rendered_from": (frozenset({"project"}), frozenset({"neural_render", "neural_stem_set"})),
    "transcribed_from": (frozenset({"audio_asset"}), frozenset({"project"})),
    "reference_conditioned_by": (frozenset({"project"}), frozenset({"project"})),
}

MUSICAL_DEPENDENCY_ERROR_CODES: dict[str, str] = {
    "embedded_note_material": "Dependency documents cannot embed note events.",
    "dependency_invalid": "Dependency document failed schema validation.",
    "dependency_endpoint_kind": "Dependency type does not match the endpoint kinds.",
    "dependency_cycle": "This edge would close a directed cycle.",
    "dependency_graph_limit": "The dependency graph is at its edge cap.",
    "dependency_not_found": "Dependency edge id was not found.",
    "dependency_refresh_conflict": "Observed downstream fingerprint does not match the live hash.",
    "dependency_reference_unresolved": "Reference project could not be resolved.",
    "dependency_fingerprint_unusable": "Stored fingerprint is not a 64-character hex digest.",
}


def log_dependency_schema_failure(model: str, code: str) -> None:
    """DEBUG schema rejection without labels or note fields."""
    logger.debug(
        "Musical dependency schema rejected",
        extra={"model": model, "code": code},
    )


class MusicalDependencyError(Exception):
    """Domain error mapped to a structured HTTP detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:200]
        self.http_status = http_status
        self.details = details or {}


def map_musical_dependency_error_to_http(exc: MusicalDependencyError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.details:
        safe: dict[str, Any] = {}
        for key, value in exc.details.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                safe[key] = value[:16]
        if safe:
            detail["details"] = safe
    return int(exc.http_status), detail


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scan_embedded(payload: Any) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_NOTE_KEYS:
                found.append(key_text)
            found.extend(_scan_embedded(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_scan_embedded(item))
    return found


def reject_embedded_note_material(payload: Any, *, model: str = "MusicalDependencyEdgeV1") -> None:
    """Raise when a raw tree contains note-event keys. Does not log the tree."""
    hits = _scan_embedded(payload)
    if not hits:
        return
    log_dependency_schema_failure(model, "embedded_note_material")
    raise MusicalDependencyError(
        "embedded_note_material",
        MUSICAL_DEPENDENCY_ERROR_CODES["embedded_note_material"],
        http_status=422,
        details={"hit_count": len(hits)},
    )


def _require(edge: MusicalDependencyEdgeV1, field: str) -> str:
    value = getattr(edge, field)
    if not isinstance(value, str) or not value:
        raise ValueError("dependency_invalid")
    return value


def upstream_node_key(edge: MusicalDependencyEdgeV1) -> str:
    """Stable key for the material an edge depends on."""
    kind = edge.upstream_kind
    if kind == "theme":
        return f"theme:{_require(edge, 'universe_id')}:{_require(edge, 'upstream_theme_id')}"
    if kind == "motif_occurrence":
        return (
            f"motif:{_require(edge, 'upstream_project_id')}:"
            f"{_require(edge, 'source_motif_id')}:{_require(edge, 'source_occurrence_id')}"
        )
    if kind == "revision":
        return f"revision:{_require(edge, 'upstream_project_id')}:{_require(edge, 'upstream_revision_id')}"
    if kind == "project":
        return f"project:{_require(edge, 'upstream_project_id')}"
    if kind == "audio_asset":
        return f"audio:{_require(edge, 'downstream_asset_id')}"
    raise ValueError("dependency_invalid")


def downstream_node_key(edge: MusicalDependencyEdgeV1) -> str:
    """Stable key for the derived asset."""
    kind = edge.downstream_kind
    if kind == "motif_occurrence":
        return (
            f"motif:{_require(edge, 'downstream_project_id')}:"
            f"{_require(edge, 'motif_id')}:{_require(edge, 'occurrence_id')}"
        )
    if kind == "revision":
        return (
            f"revision:{_require(edge, 'downstream_project_id')}:"
            f"{_require(edge, 'downstream_revision_id')}"
        )
    if kind == "project":
        return f"project:{_require(edge, 'downstream_project_id')}"
    if kind == "neural_render":
        return f"render:{_require(edge, 'downstream_asset_id')}"
    if kind == "neural_stem_set":
        return f"stems:{_require(edge, 'downstream_asset_id')}"
    raise ValueError("dependency_invalid")


class MusicalDependencyEdgeV1(_Strict):
    """One directed derivation. Fingerprints and ids only."""

    schema_version: Literal["musical.dependency.edge.v1"] = DEPENDENCY_EDGE_SCHEMA
    id: str = Field(..., pattern=EDGE_ID_RE.pattern)
    dependency_type: DependencyType
    upstream_kind: UpstreamKind
    downstream_kind: DownstreamKind
    universe_id: str | None = Field(default=None, pattern=UNIVERSE_ID_RE.pattern)
    upstream_project_id: str | None = Field(default=None, min_length=1, max_length=64)
    downstream_project_id: str | None = Field(default=None, min_length=1, max_length=64)
    upstream_theme_id: str | None = Field(default=None, pattern=THEME_ID_RE.pattern)
    variant_id: str | None = Field(default=None, pattern=VARIANT_ID_RE.pattern)
    motif_id: str | None = Field(default=None, min_length=1, max_length=120)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_motif_id: str | None = Field(default=None, min_length=1, max_length=120)
    source_occurrence_id: str | None = Field(default=None, min_length=1, max_length=120)
    upstream_revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    downstream_revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    downstream_asset_id: str | None = Field(default=None, min_length=1, max_length=80)
    upstream_fingerprint: str = Field(..., pattern=SHA256_RE.pattern)
    downstream_fingerprint: str | None = Field(default=None, pattern=SHA256_RE.pattern)
    created_at: str = Field(default_factory=_utc_now_iso, min_length=1, max_length=40)

    @field_validator(
        "upstream_project_id",
        "downstream_project_id",
        "motif_id",
        "occurrence_id",
        "source_motif_id",
        "source_occurrence_id",
        "upstream_revision_id",
        "downstream_revision_id",
        "downstream_asset_id",
    )
    @classmethod
    def strip_ids(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("dependency_invalid")
        return cleaned

    @model_validator(mode="after")
    def check_endpoints(self) -> MusicalDependencyEdgeV1:
        allowed_up, allowed_down = _ENDPOINT_PAIRS[self.dependency_type]
        if self.upstream_kind not in allowed_up or self.downstream_kind not in allowed_down:
            raise ValueError("dependency_endpoint_kind")
        if (
            self.dependency_type == "reference_conditioned_by"
            and self.upstream_project_id == self.downstream_project_id
        ):
            raise ValueError("dependency_endpoint_kind")
        if (
            self.dependency_type == "variation_of"
            and self.upstream_kind == "theme"
            and self.variant_id is None
        ):
            raise ValueError("dependency_invalid")
        try:
            upstream = upstream_node_key(self)
            downstream = downstream_node_key(self)
        except ValueError as exc:
            raise ValueError("dependency_invalid") from exc
        if upstream == downstream:
            raise ValueError("dependency_cycle")
        return self


class MusicalDependencyNodeV1(_Strict):
    """One graph vertex. ``label`` is display text, never a pitch list."""

    node_key: str = Field(..., min_length=1, max_length=240)
    kind: NodeKind
    label: str = Field(default="", max_length=120)
    universe_id: str | None = Field(default=None, pattern=UNIVERSE_ID_RE.pattern)
    theme_id: str | None = Field(default=None, pattern=THEME_ID_RE.pattern)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    motif_id: str | None = Field(default=None, min_length=1, max_length=120)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=120)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    asset_id: str | None = Field(default=None, min_length=1, max_length=80)
    variant_id: str | None = Field(default=None, pattern=VARIANT_ID_RE.pattern)
    edge_id: str | None = Field(default=None, pattern=EDGE_ID_RE.pattern)


class MusicalDependencyGraphEdgeV1(_Strict):
    """One drawn edge. Status is computed; the row itself stores fingerprints."""

    edge_id: str = Field(..., pattern=EDGE_ID_RE.pattern)
    dependency_type: DependencyType
    upstream_node_key: str = Field(..., min_length=1, max_length=240)
    downstream_node_key: str = Field(..., min_length=1, max_length=240)
    status: DependencyStatus


class MusicalDependencyGraphV1(_Strict):
    schema_version: Literal["musical.dependency.graph.v1"] = DEPENDENCY_GRAPH_SCHEMA
    nodes: list[MusicalDependencyNodeV1] = Field(default_factory=list, max_length=256)
    edges: list[MusicalDependencyGraphEdgeV1] = Field(default_factory=list, max_length=256)


class MusicalDependencyUpdateOfferV1(_Strict):
    """Names an existing studio action. It does not carry notes."""

    schema_version: Literal["musical.dependency.update_offer.v1"] = DEPENDENCY_UPDATE_OFFER_SCHEMA
    dependency_type: DependencyType
    action: UpdateAction
    edge_id: str = Field(..., pattern=EDGE_ID_RE.pattern)
    universe_id: str | None = Field(default=None, pattern=UNIVERSE_ID_RE.pattern)
    theme_id: str | None = Field(default=None, pattern=THEME_ID_RE.pattern)
    variant_id: str | None = Field(default=None, pattern=VARIANT_ID_RE.pattern)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)


class MusicalDependencyDependentV1(_Strict):
    edge_id: str = Field(..., pattern=EDGE_ID_RE.pattern)
    dependency_type: DependencyType
    label: str = Field(default="", max_length=120)
    node_key: str = Field(..., min_length=1, max_length=240)
    status: DependencyStatus
    update_offer: MusicalDependencyUpdateOfferV1


class MusicalDependencyImpactV1(_Strict):
    schema_version: Literal["musical.dependency.impact.v1"] = DEPENDENCY_IMPACT_SCHEMA
    theme_id: str = Field(..., pattern=THEME_ID_RE.pattern)
    live_source_fingerprint: str = Field(..., pattern=SHA256_RE.pattern)
    dependents: list[MusicalDependencyDependentV1] = Field(default_factory=list, max_length=256)


class MusicalDependencyRecordRefreshRequest(_Strict):
    observed_downstream_fingerprint: str = Field(..., pattern=SHA256_RE.pattern)


def _validation_code(exc: ValidationError) -> str:
    text = str(exc)
    for code in (
        "dependency_endpoint_kind",
        "dependency_cycle",
        "dependency_invalid",
        "embedded_note_material",
    ):
        if code in text:
            return code
    return "dependency_invalid"


def _map_validation(model: str, exc: ValidationError) -> MusicalDependencyError:
    code = _validation_code(exc)
    log_dependency_schema_failure(model, code)
    status = 422
    return MusicalDependencyError(
        code,
        MUSICAL_DEPENDENCY_ERROR_CODES.get(code, MUSICAL_DEPENDENCY_ERROR_CODES["dependency_invalid"]),
        http_status=status,
    )


def _parse(model_name: str, model: type[BaseModel], data: dict[str, Any]) -> BaseModel:
    if not isinstance(data, dict):
        log_dependency_schema_failure(model_name, "dependency_invalid")
        raise MusicalDependencyError(
            "dependency_invalid",
            MUSICAL_DEPENDENCY_ERROR_CODES["dependency_invalid"],
            http_status=422,
        )
    reject_embedded_note_material(data, model=model_name)
    version = data.get("schema_version")
    expected = getattr(model, "model_fields", {})
    if version is not None and "schema_version" in expected:
        default = expected["schema_version"].default
        if version != default:
            log_dependency_schema_failure(model_name, "dependency_invalid")
            raise MusicalDependencyError(
                "dependency_invalid",
                MUSICAL_DEPENDENCY_ERROR_CODES["dependency_invalid"],
                http_status=422,
                details={"field": "schema_version"},
            )
    try:
        return model.model_validate(data)
    except MusicalDependencyError:
        raise
    except ValidationError as exc:
        raise _map_validation(model_name, exc) from exc


def parse_musical_dependency_edge(data: dict[str, Any]) -> MusicalDependencyEdgeV1:
    """Validate one edge. Forbidden note keys fail before field parsing."""
    parsed = _parse("MusicalDependencyEdgeV1", MusicalDependencyEdgeV1, data)
    assert isinstance(parsed, MusicalDependencyEdgeV1)
    return parsed


def parse_musical_dependency_graph(data: dict[str, Any]) -> MusicalDependencyGraphV1:
    parsed = _parse("MusicalDependencyGraphV1", MusicalDependencyGraphV1, data)
    assert isinstance(parsed, MusicalDependencyGraphV1)
    return parsed


def parse_musical_dependency_impact(data: dict[str, Any]) -> MusicalDependencyImpactV1:
    parsed = _parse("MusicalDependencyImpactV1", MusicalDependencyImpactV1, data)
    assert isinstance(parsed, MusicalDependencyImpactV1)
    return parsed


def parse_musical_dependency_update_offer(data: dict[str, Any]) -> MusicalDependencyUpdateOfferV1:
    parsed = _parse("MusicalDependencyUpdateOfferV1", MusicalDependencyUpdateOfferV1, data)
    assert isinstance(parsed, MusicalDependencyUpdateOfferV1)
    return parsed


def parse_musical_dependency_record_refresh(
    data: dict[str, Any],
) -> MusicalDependencyRecordRefreshRequest:
    parsed = _parse(
        "MusicalDependencyRecordRefreshRequest",
        MusicalDependencyRecordRefreshRequest,
        data,
    )
    assert isinstance(parsed, MusicalDependencyRecordRefreshRequest)
    return parsed


__all__ = [
    "DEPENDENCY_EDGE_SCHEMA",
    "DEPENDENCY_GRAPH_SCHEMA",
    "DEPENDENCY_IMPACT_SCHEMA",
    "DEPENDENCY_UPDATE_OFFER_SCHEMA",
    "FORBIDDEN_NOTE_KEYS",
    "MUSICAL_DEPENDENCY_ERROR_CODES",
    "DependencyStatus",
    "DependencyType",
    "DownstreamKind",
    "MusicalDependencyDependentV1",
    "MusicalDependencyEdgeV1",
    "MusicalDependencyError",
    "MusicalDependencyGraphEdgeV1",
    "MusicalDependencyGraphV1",
    "MusicalDependencyImpactV1",
    "MusicalDependencyNodeV1",
    "MusicalDependencyRecordRefreshRequest",
    "MusicalDependencyUpdateOfferV1",
    "NodeKind",
    "UpdateAction",
    "UpstreamKind",
    "downstream_node_key",
    "log_dependency_schema_failure",
    "map_musical_dependency_error_to_http",
    "parse_musical_dependency_edge",
    "parse_musical_dependency_graph",
    "parse_musical_dependency_impact",
    "parse_musical_dependency_record_refresh",
    "parse_musical_dependency_update_offer",
    "reject_embedded_note_material",
    "upstream_node_key",
]
