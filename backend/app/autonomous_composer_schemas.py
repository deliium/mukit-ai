"""creative.brief.v1 and project.plan.v1 — non-playable autonomous contracts.

The brief is the user goal. The project plan is the locked structure the
director may only rephrase. Neither document is a score.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import KEY_PATTERN, SUPPORTED_SECTION_TYPES

CREATIVE_BRIEF_SCHEMA_VERSION: Literal["creative.brief.v1"] = "creative.brief.v1"
PROJECT_PLAN_SCHEMA_VERSION: Literal["project.plan.v1"] = "project.plan.v1"

BRIEF_TITLE_MAX = 120
BRIEF_SOFT_FIELD_MAX = 64
NARRATIVE_TEXT_MAX = 240
SECTION_LABEL_MAX = 64
GOAL_SUMMARY_MAX = 240
MOTIF_LABEL_MAX = 64
INSTRUMENT_LABEL_MAX = 80
MAX_NARRATIVE_BEATS = 8
MAX_INSTRUMENTS = 8
DURATION_SECONDS_MIN = 30
DURATION_SECONDS_MAX = 600
DEFAULT_TEMPO_MIN = 60
DEFAULT_TEMPO_MAX = 84
BAR_MATH_DEFAULT_BPM = 72
DURATION_BARS_MAX = 512

FORBIDDEN_PLAYABLE_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "tracks",
        "events",
        "notes",
        "note_events",
        "musicxml",
        "midi",
        "wav",
        "composition",
    }
)

NarrativeIntent = Literal[
    "sparse_opening",
    "establish_theme",
    "build",
    "climax",
    "resolve",
]
DensityBand = Literal["sparse", "moderate", "dense"]
SectionType = Literal[
    "intro",
    "verse",
    "pre_chorus",
    "chorus",
    "bridge",
    "solo",
    "breakdown",
    "outro",
    "unsectioned",
]
StageId = Literal[
    "plan",
    "harmony_plan",
    "motif_plan",
    "symbolic",
    "critique",
    "revision",
    "arrangement",
    "expression",
    "render",
]
StageOperation = Literal["plan", "realize", "critique", "propose", "advise"]
StageApproval = Literal["auto", "required"]
ScoreCommitPolicy = Literal["no", "yes", "when_changed"]

COMPLETION_CODES: frozenset[str] = frozenset(
    {
        "project_plan_valid",
        "harmony_plan_key",
        "motif_plan_present",
        "composition_v2_ok",
        "hard_constraints_ok",
        "no_forbidden_instruments",
        "motif_identity_ok",
        "final_section_mode_ok",
        "critique_stored",
        "revision_contained",
        "arrangement_preserved",
        "expression_pitch_stable",
        "render_dispatched",
    }
)

STAGE_IDS: tuple[StageId, ...] = (
    "plan",
    "harmony_plan",
    "motif_plan",
    "symbolic",
    "critique",
    "revision",
    "arrangement",
    "expression",
    "render",
)

AUTONOMOUS_INSTRUMENT_UNKNOWN = "autonomous_instrument_unknown"
AUTONOMOUS_CONSTRAINT_FAILED = "autonomous_constraint_failed"
AUTONOMOUS_BRIEF_INVALID = "autonomous_brief_invalid"


class AutonomousPlanError(ValueError):
    """Compile or merge failure with a public error code and no note text."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _reject_playable_keys(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    forbidden = sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
    if forbidden:
        raise ValueError("plan must not include playable score fields")
    return data


def _normalize_key_label(value: str) -> str:
    key = " ".join(value.strip().split())
    if not KEY_PATTERN.match(key):
        raise ValueError("Key must use format like 'C minor' or 'F# major'")
    return key


class NarrativeBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: NarrativeIntent
    text: str = Field(..., min_length=1, max_length=NARRATIVE_TEXT_MAX)


class CreativeBriefV1(BaseModel):
    """User goal, narrative beats, instruments, and hard constraints."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["creative.brief.v1"]
    title: str | None = Field(default=None, max_length=BRIEF_TITLE_MAX)
    duration_seconds: int = Field(..., ge=DURATION_SECONDS_MIN, le=DURATION_SECONDS_MAX)
    narrative: list[NarrativeBeat] = Field(..., min_length=1, max_length=MAX_NARRATIVE_BEATS)
    instrumentation: list[str] = Field(..., min_length=1, max_length=MAX_INSTRUMENTS)
    forbidden_instrument_families: list[str] = Field(default_factory=list, max_length=MAX_INSTRUMENTS)
    opening_key: str
    final_section_key: str | None = None
    motif_label: str = Field(default="Theme A", min_length=1, max_length=MOTIF_LABEL_MAX)
    motif_must_remain_recognizable: bool = True
    time_signature: str = "4/4"
    tempo_min: int | None = Field(default=None, ge=1, le=400)
    tempo_max: int | None = Field(default=None, ge=1, le=400)
    mood: str | None = Field(default=None, max_length=BRIEF_SOFT_FIELD_MAX)
    genre: str | None = Field(default=None, max_length=BRIEF_SOFT_FIELD_MAX)

    @model_validator(mode="before")
    @classmethod
    def reject_playable(cls, data: Any) -> Any:
        return _reject_playable_keys(data)

    @field_validator("title", "motif_label", "mood", "genre", mode="before")
    @classmethod
    def strip_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            stripped = " ".join(value.strip().split())
            return stripped or None
        return value

    @field_validator("opening_key", "final_section_key")
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_key_label(value)

    @field_validator("time_signature")
    @classmethod
    def validate_meter(cls, value: str) -> str:
        from app.services.composition_timing import parse_time_signature

        meter = value.strip()
        parse_time_signature(meter)
        return meter

    @field_validator("instrumentation", "forbidden_instrument_families")
    @classmethod
    def strip_labels(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.strip().split()) for item in value]
        if any(not item or len(item) > INSTRUMENT_LABEL_MAX for item in cleaned):
            raise ValueError("instrument label is empty or too long")
        return cleaned

    @model_validator(mode="after")
    def tempo_order(self) -> "CreativeBriefV1":
        if (
            self.tempo_min is not None
            and self.tempo_max is not None
            and self.tempo_min > self.tempo_max
        ):
            raise ValueError("tempo_min must be <= tempo_max")
        return self


class ProjectPlanGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=32)
    summary: str = Field(..., min_length=1, max_length=GOAL_SUMMARY_MAX)
    section_id: str = Field(..., min_length=1, max_length=32)


class ProjectPlanConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opening_key: str
    final_section_key: str | None = None
    time_signature: str
    tempo_min: int = Field(..., ge=1, le=400)
    tempo_max: int = Field(..., ge=1, le=400)
    opening_tempo: int = Field(..., ge=1, le=400)
    duration_bars: int = Field(..., ge=1, le=DURATION_BARS_MAX)
    instruments: list[str] = Field(..., min_length=1, max_length=MAX_INSTRUMENTS)
    forbidden_instrument_families: list[str] = Field(default_factory=list)
    motif_label: str = Field(..., min_length=1, max_length=MOTIF_LABEL_MAX)
    motif_must_remain_recognizable: bool = True
    motif_section_id: str = Field(..., min_length=1, max_length=32)

    @field_validator("opening_key", "final_section_key")
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_key_label(value)

    @model_validator(mode="after")
    def tempo_order(self) -> "ProjectPlanConstraints":
        if self.tempo_min > self.tempo_max:
            raise ValueError("tempo_min must be <= tempo_max")
        return self


class ProjectPlanSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=32)
    type: str
    label: str = Field(..., min_length=1, max_length=SECTION_LABEL_MAX)
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1)
    density: DensityBand
    narrative: str = Field(..., min_length=1, max_length=NARRATIVE_TEXT_MAX)
    key: str

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_SECTION_TYPES:
            raise ValueError("unsupported section type")
        return normalized

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _normalize_key_label(value)


class ProjectPlanStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: StageId
    agent_id: str | None = None
    operation: StageOperation
    depends_on: list[str] = Field(default_factory=list)
    score_commit: ScoreCommitPolicy
    approval: StageApproval
    completion_codes: list[str] = Field(..., min_length=1)

    @field_validator("completion_codes")
    @classmethod
    def validate_codes(cls, value: list[str]) -> list[str]:
        unknown = [code for code in value if code not in COMPLETION_CODES]
        if unknown:
            raise ValueError("unknown completion code")
        return value


class ProjectPlanV1(BaseModel):
    """Locked goals, constraints, sections, and the nine-stage graph."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["project.plan.v1"]
    goals: list[ProjectPlanGoal] = Field(..., min_length=1, max_length=MAX_NARRATIVE_BEATS)
    constraints: ProjectPlanConstraints
    sections: list[ProjectPlanSection] = Field(..., min_length=1, max_length=MAX_NARRATIVE_BEATS)
    stages: list[ProjectPlanStage] = Field(..., min_length=9, max_length=9)

    @model_validator(mode="before")
    @classmethod
    def reject_playable(cls, data: Any) -> Any:
        return _reject_playable_keys(data)

    @model_validator(mode="after")
    def sections_cover_duration(self) -> "ProjectPlanV1":
        ordered = sorted(self.sections, key=lambda section: section.start_bar)
        if ordered[0].start_bar != 1:
            raise ValueError("sections must start at bar 1")
        cursor = 1
        for section in ordered:
            if section.start_bar != cursor:
                raise ValueError("sections must be contiguous")
            cursor += section.bar_count
        if cursor - 1 != self.constraints.duration_bars:
            raise ValueError("section bars must equal duration_bars")
        stage_ids = [stage.stage_id for stage in self.stages]
        if stage_ids != list(STAGE_IDS):
            raise ValueError("stage graph order is fixed")
        return self


class AutonomousRunStartV1(BaseModel):
    """Start body. The brief is validated before any agent runs."""

    model_config = ConfigDict(extra="forbid")

    brief: CreativeBriefV1
    project_id: str | None = None
    include_rendering: bool = False
    render_approval: Literal["required", "auto"] = "required"
    seed: int = 0
    operation_run_id: str | None = None
    expected_working_version: int | None = None
    expected_head_revision_id: str | None = None
    expected_source_fingerprint: str | None = None
    max_agent_operations: int | None = Field(default=None, ge=0)


class AutonomousStageViewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str
    agent_id: str | None = None
    status: str
    revision_id: str | None = None
    failure_code: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class AutonomousRunViewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["autonomous.run.v1"] = "autonomous.run.v1"
    run_id: str
    project_id: str
    status: str
    head_revision_id: str | None = None
    composition_fingerprint: str | None = None
    budget_code: str | None = None
    failure_code: str | None = None
    stages: list[AutonomousStageViewV1]
    operation_summary: dict[str, Any] | None = None
