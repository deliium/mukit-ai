"""Non-playable preference documents for an explicit candidate ballot.

These models never carry note events or genre labels. This module does not
import FastAPI, torch, stores, video, film, agent, or embedding modules.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

SETTINGS_SCHEMA = "preference.settings.v1"
PENDING_BALLOT_SCHEMA = "preference.pending_ballot.v1"
CHOICE_SCHEMA = "preference.choice.v1"
FEATURE_SCHEMA = "preference.features.v1"
RANKER_SCHEMA = "preference.ranker.v1"
RANKING_SCHEMA = "preference.ranking.v1"

PREFERENCE_FEATURE_DIMS = 16
PREFERENCE_LEARNING_RATE = 0.1
PREFERENCE_WEIGHT_LIMIT = 4.0
CHOICE_ID_PATTERN = re.compile(r"^pref_[0-9a-f]{16}$")
PROFILE_ID_PATTERN = re.compile(r"^prof_[0-9a-f]{16}$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
        "composition",
        "composition_json",
        "genre",
        "genres",
        "mood",
        "style",
        "styles",
        "artist",
        "artist_name",
        "composer",
        "composer_name",
        "prompt",
        "embedding",
        "analysis",
        "analysis_report",
        "vector",
    }
)

PreferenceSurface = Literal["development", "arrangement"]
ProfileStrength = Literal["off", "light", "normal", "strong"]

_HTTP_STATUS = {
    "preference_forbidden_payload": 422,
    "preference_invalid": 422,
    "preference_learning_disabled": 409,
    "preference_collection_disabled": 409,
    "preference_ballot_missing": 404,
    "preference_choice_limit": 422,
    "preference_not_found": 404,
}


class PreferenceLearningError(Exception):
    """Domain error mapped to a structured HTTP detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:200]
        self.http_status = int(http_status if http_status is not None else _HTTP_STATUS.get(code, 422))
        self.details = details or {}


def map_preference_error_to_http(exc: PreferenceLearningError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    safe: dict[str, Any] = {}
    for key, value in exc.details.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            safe[key] = value[:16]
    if safe:
        detail["details"] = safe
    return int(exc.http_status), detail


def _log_schema_rejection(model_name: str, code: str) -> None:
    logger.debug(
        "Preference schema rejected",
        extra={"model_name": model_name, "code": code},
    )


def scan_forbidden_preference_keys(payload: Any) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_PAYLOAD_KEYS:
                found.append(key_text)
            found.extend(scan_forbidden_preference_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_forbidden_preference_keys(item))
    return found


def reject_preference_forbidden_payload(payload: Any, *, model_name: str) -> None:
    """Raise ``preference_forbidden_payload`` when a key is forbidden."""
    found = scan_forbidden_preference_keys(payload)
    if not found:
        return
    _log_schema_rejection(model_name, "preference_forbidden_payload")
    raise PreferenceLearningError(
        "preference_forbidden_payload",
        "Preference documents cannot carry that field.",
        details={"field_name": found[0]},
    )


def _reject(model_name: str, code: str, message: str) -> None:
    _log_schema_rejection(model_name, code)
    raise ValueError(code if not message else f"{code}: {message}")


def _finite_unit(value: Any, *, model_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(model_name, "preference_invalid", "feature element is not a number")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        _reject(model_name, "preference_invalid", "feature element out of range")
    return number


def _finite_weight(value: Any, *, model_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(model_name, "preference_invalid", "weight is not a number")
    number = float(value)
    if not math.isfinite(number) or number < -PREFERENCE_WEIGHT_LIMIT or number > PREFERENCE_WEIGHT_LIMIT:
        _reject(model_name, "preference_invalid", "weight out of range")
    return number


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_keys(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                reject_preference_forbidden_payload(value, model_name=cls.__name__)
            except PreferenceLearningError as exc:
                raise ValueError(exc.code) from exc
        return value


class PreferenceSettingsV1(_Strict):
    """User switches. Both default off. The deployment flag is not stored here."""

    schema_version: Literal["preference.settings.v1"] = SETTINGS_SCHEMA
    collection_enabled: bool = False
    ranking_enabled: bool = False
    updated_at: str | None = Field(default=None, max_length=40)


class PreferenceSettingsResponse(_Strict):
    """GET settings view. ``feature_available`` mirrors the deployment flag."""

    schema_version: Literal["preference.settings.v1"] = SETTINGS_SCHEMA
    collection_enabled: bool = False
    ranking_enabled: bool = False
    feature_available: bool = False
    updated_at: str | None = Field(default=None, max_length=40)


class PreferenceSettingsUpdate(_Strict):
    """PUT body. Both booleans are required so an omitted switch cannot flip on."""

    collection_enabled: bool
    ranking_enabled: bool


class PreferenceContextV1(_Strict):
    """Ballot context. Profile id is stored and is not a ranker dimension."""

    surface: PreferenceSurface
    operation: str = Field(min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=80)
    source_fingerprint: str = Field(min_length=16, max_length=128)
    request_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: str | None = Field(default=None, pattern=r"^prof_[0-9a-f]{16}$")
    profile_strength: ProfileStrength | None = None


class PreferenceCandidateFeaturesV1(_Strict):
    """One pending alternative. ``chosen`` is absent until a choice is stored."""

    candidate_id: str = Field(min_length=16, max_length=128)
    candidate_fingerprint: str = Field(min_length=16, max_length=128)
    original_index: int = Field(ge=0, le=3)
    feature_vector: list[float]

    @field_validator("feature_vector")
    @classmethod
    def validate_feature_vector(cls, value: list[float]) -> list[float]:
        if len(value) != PREFERENCE_FEATURE_DIMS:
            _reject(cls.__name__, "preference_invalid", "feature vector length")
        return [_finite_unit(item, model_name=cls.__name__) for item in value]


class PreferenceChoiceCandidateV1(PreferenceCandidateFeaturesV1):
    """Feature row on a stored choice. Exactly one candidate is chosen."""

    chosen: bool


class PreferencePendingBallotV1(_Strict):
    """Feature snapshot taken at preview, before any choice."""

    schema_version: Literal["preference.pending_ballot.v1"] = PENDING_BALLOT_SCHEMA
    context: PreferenceContextV1
    candidates: list[PreferenceCandidateFeaturesV1] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def unique_candidate_ids(self) -> PreferencePendingBallotV1:
        ids = [item.candidate_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            _reject(self.__class__.__name__, "preference_invalid", "duplicate candidate id")
        return self


class PreferenceChoiceV1(_Strict):
    """One stored ballot with the applied candidate marked chosen."""

    schema_version: Literal["preference.choice.v1"] = CHOICE_SCHEMA
    id: str = Field(pattern=r"^pref_[0-9a-f]{16}$")
    context: PreferenceContextV1
    candidates: list[PreferenceChoiceCandidateV1] = Field(min_length=2, max_length=4)
    chosen_candidate_id: str = Field(min_length=16, max_length=128)
    created_at: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def one_chosen_candidate(self) -> PreferenceChoiceV1:
        ids = [item.candidate_id for item in self.candidates]
        if len(ids) != len(set(ids)):
            _reject(self.__class__.__name__, "preference_invalid", "duplicate candidate id")
        chosen = [item for item in self.candidates if item.chosen]
        if len(chosen) != 1:
            _reject(self.__class__.__name__, "preference_invalid", "choice needs one chosen candidate")
        if chosen[0].candidate_id != self.chosen_candidate_id:
            _reject(self.__class__.__name__, "preference_invalid", "chosen id mismatch")
        return self


class PreferenceRankerV1(_Strict):
    """Linear weight vector. Not a Music Transformer and not a personal adapter."""

    schema_version: Literal["preference.ranker.v1"] = RANKER_SCHEMA
    feature_schema: Literal["preference.features.v1"] = FEATURE_SCHEMA
    dims: Literal[16] = PREFERENCE_FEATURE_DIMS
    weights: list[float]
    learning_rate: float = PREFERENCE_LEARNING_RATE
    pair_count: int = Field(ge=0)
    updated_at: str = Field(min_length=1, max_length=40)

    @field_validator("learning_rate")
    @classmethod
    def validate_learning_rate(cls, value: float) -> float:
        if abs(float(value) - PREFERENCE_LEARNING_RATE) > 1e-12:
            _reject(cls.__name__, "preference_invalid", "learning rate is fixed")
        return PREFERENCE_LEARNING_RATE

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, value: list[float]) -> list[float]:
        if len(value) != PREFERENCE_FEATURE_DIMS:
            _reject(cls.__name__, "preference_invalid", "weight vector length")
        return [_finite_weight(item, model_name=cls.__name__) for item in value]


class PreferenceRankingV1(_Strict):
    """Ordered candidate ids for one ballot. Ranking does not apply a score."""

    schema_version: Literal["preference.ranking.v1"] = RANKING_SCHEMA
    ranking_applied: bool
    ordered_candidate_ids: list[str] = Field(min_length=1, max_length=4)
    scores: list[float] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def scores_match_order(self) -> PreferenceRankingV1:
        if len(self.scores) != len(self.ordered_candidate_ids):
            _reject(self.__class__.__name__, "preference_invalid", "score count mismatch")
        cleaned: list[float] = []
        for score in self.scores:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                _reject(self.__class__.__name__, "preference_invalid", "score is not a number")
            number = float(score)
            if not math.isfinite(number):
                _reject(self.__class__.__name__, "preference_invalid", "score is not finite")
            cleaned.append(number)
        if not self.ranking_applied and any(score != 0.0 for score in cleaned):
            _reject(self.__class__.__name__, "preference_invalid", "inactive ranking scores are zero")
        self.scores = cleaned
        return self


class PreferenceChoiceSummaryV1(_Strict):
    """Inspect list item. Feature numbers stay on the detail document."""

    id: str = Field(pattern=r"^pref_[0-9a-f]{16}$")
    surface: PreferenceSurface
    operation: str = Field(min_length=1, max_length=64)
    chosen_candidate_id: str = Field(min_length=16, max_length=128)
    candidate_count: int = Field(ge=2, le=4)
    created_at: str = Field(min_length=1, max_length=40)
    source_fingerprint_prefix: str = Field(min_length=12, max_length=12)


class PreferenceChoiceRecordRequest(_Strict):
    """Explicit choice of one id already present on the pending ballot."""

    surface: PreferenceSurface
    chosen_candidate_id: str = Field(min_length=16, max_length=128)


class PreferenceRankRequest(_Strict):
    """Score the pending features for these ids. Order is a suggestion only."""

    surface: PreferenceSurface
    candidate_ids: list[str] = Field(min_length=1, max_length=4)

    @field_validator("candidate_ids")
    @classmethod
    def validate_candidate_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item)
            if len(text) < 16 or len(text) > 128:
                _reject(cls.__name__, "preference_invalid", "candidate id length")
            cleaned.append(text)
        if len(cleaned) != len(set(cleaned)):
            _reject(cls.__name__, "preference_invalid", "duplicate candidate id")
        return cleaned
