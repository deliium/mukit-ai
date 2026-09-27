"""Versioned musical workflow benchmark contracts.

``workflow.benchmark.v1`` is the committed suite. Run, packet, and judgment
documents carry ``benchmark_version`` and the suite SHA-256. Listening packets
forbid metric names, cost, latency, and arm ids. ``musical_quality_claim`` on
automated reports is always false.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

BENCHMARK_SCHEMA: Literal["workflow.benchmark.v1"] = "workflow.benchmark.v1"
CASE_METRICS_SCHEMA: Literal["workflow.case_metrics.v1"] = "workflow.case_metrics.v1"
BENCHMARK_RUN_SCHEMA: Literal["workflow.benchmark_run.v1"] = "workflow.benchmark_run.v1"
CASE_RESULT_SCHEMA: Literal["workflow.case_result.v1"] = "workflow.case_result.v1"
LISTENING_PACKET_SCHEMA: Literal["workflow.listening_packet.v1"] = "workflow.listening_packet.v1"
LISTENING_JUDGMENT_SCHEMA: Literal["workflow.listening_judgment.v1"] = (
    "workflow.listening_judgment.v1"
)
REGRESSION_SCHEMA: Literal["workflow.regression.v1"] = "workflow.regression.v1"

CaseKind = Literal[
    "melody-focused",
    "harmonic",
    "orchestral",
    "minimalist",
    "multi-section",
    "reference-conditioned",
    "profile-conditioned",
    "revision-preservation",
]
ArmId = Literal[
    "v3_direct",
    "v4_multi_agent",
    "v4_iterative_revision",
    "v4_autonomous",
]
PipelineId = Literal[
    "llm_only",
    "hybrid_plan_symbolic",
    "symbolic_continuation",
    "symbolic_variation",
]
ProfileStrength = Literal["off", "light", "normal", "strong"]
SeedStatus = Literal["honored", "ignored_by_pipeline", "unknown"]
ConditioningStatus = Literal[
    "attached",
    "skipped_missing_profile",
    "not_wired",
    "not_applicable",
]
MetricKind = Literal["hard", "observed", "counter"]
MetricStatus = Literal["pass", "fail", "not_applicable", "unavailable", "measured"]
ListeningMode = Literal["ab", "abc"]
ListeningLabel = Literal["A", "B", "C"]

COMPARISON_ARMS: tuple[ArmId, ...] = (
    "v3_direct",
    "v4_multi_agent",
    "v4_iterative_revision",
)
ALL_ARMS: tuple[ArmId, ...] = (*COMPARISON_ARMS, "v4_autonomous")

HARD_METRIC_IDS: tuple[str, ...] = (
    "hard_constraint_compliance",
    "structural_compliance",
    "instrument_range_correctness",
    "revision_preservation",
    "invalid_composition",
)
OBSERVED_METRIC_IDS: tuple[str, ...] = (
    "motif_recurrence",
    "section_contrast",
    "tonal_consistency",
    "generation_latency_ms",
    "process_rss_kb",
    "remote_cost_micros",
    "model_calls",
)
COUNTER_METRIC_IDS: tuple[str, ...] = (
    "agent_calls",
    "revision_passes",
    "failed_stages",
    "recovered_stages",
    "time_to_valid_ms",
)
METRIC_IDS: tuple[str, ...] = HARD_METRIC_IDS + OBSERVED_METRIC_IDS + COUNTER_METRIC_IDS

FORBIDDEN_PACKET_KEYS: frozenset[str] = frozenset(
    {
        *METRIC_IDS,
        "cost",
        "latency",
        "latency_ms",
        "remote_cost",
        "provider_reported_cost_micros",
        "musical_quality",
        "musical_quality_claim",
        "arm",
        "arm_id",
        "pipeline_id",
        *ALL_ARMS,
    }
)

_SEED_MAX = 2_147_483_647


def canonical_json_bytes(payload: Any) -> bytes:
    """Stable UTF-8 JSON for suite, run, and packet digests."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def canonical_sha256(payload: Any) -> str:
    """SHA-256 hex of canonical JSON."""
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    logger.debug("Canonical digest", extra={"digest_prefix": digest[:12]})
    return digest


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CasePromptV1(_Strict):
    """Prompt fields ``LLMMusicGenerationRequest`` already accepts."""

    mood: str = Field(default="lyrical", min_length=1, max_length=120)
    genre: str = Field(default="chamber", min_length=1, max_length=120)
    tempo_min: int = Field(..., ge=40, le=240)
    tempo_max: int = Field(..., ge=40, le=240)
    key: str | None = None
    time_signature: str = "4/4"
    instruments: list[str] = Field(..., min_length=1, max_length=8)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    complexity: Literal["simple", "moderate", "complex"] = "moderate"
    duration_bars: int = Field(..., ge=1, le=8)
    instructions: str | None = Field(default=None, max_length=240)


class BenchmarkTolerancesV1(_Strict):
    """Hard-metric gates. Zero means the rate may not move in the worse direction."""

    invalid_composition_rate_max_rise: float = Field(default=0.0, ge=0)
    hard_constraint_pass_max_drop: float = Field(default=0.0, ge=0)
    structural_pass_max_drop: float = Field(default=0.0, ge=0)
    instrument_range_pass_max_drop: float = Field(default=0.0, ge=0)
    revision_preservation_pass_max_drop: float = Field(default=0.0, ge=0)


class BenchmarkCaseV1(_Strict):
    id: str = Field(..., min_length=1, max_length=64)
    kind: CaseKind
    seed: int = Field(..., ge=0, le=_SEED_MAX)
    prompt: CasePromptV1
    v3_pipeline: PipelineId = "llm_only"
    allow_extra_instrument_families: bool = False
    profile_id: str | None = Field(default=None, max_length=64)
    profile_strength: ProfileStrength = "off"
    reference_fixture: str | None = Field(default=None, max_length=120)
    reference_dimensions: list[str] = Field(default_factory=list, max_length=8)
    reference_policy: dict[str, Any] | None = None
    preservation_fixture: str | None = Field(default=None, max_length=180)


class WorkflowBenchmarkV1(_Strict):
    schema_version: Literal["workflow.benchmark.v1"] = BENCHMARK_SCHEMA
    benchmark_id: str = Field(..., min_length=1, max_length=80)
    benchmark_version: str = Field(..., min_length=1, max_length=32)
    cases: list[BenchmarkCaseV1] = Field(..., min_length=1, max_length=32)
    tolerances: BenchmarkTolerancesV1 = Field(default_factory=BenchmarkTolerancesV1)

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, value: list[BenchmarkCaseV1]) -> list[BenchmarkCaseV1]:
        ids = [case.id for case in value]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return value


class MetricReadingV1(_Strict):
    name: str = Field(..., min_length=1, max_length=64)
    kind: MetricKind
    status: MetricStatus
    value: float | int | bool | None = None


class CaseMetricsV1(_Strict):
    schema_version: Literal["workflow.case_metrics.v1"] = CASE_METRICS_SCHEMA
    musical_quality_claim: Literal[False] = False
    metrics: list[MetricReadingV1] = Field(default_factory=list)

    def reading(self, name: str) -> MetricReadingV1 | None:
        for item in self.metrics:
            if item.name == name:
                return item
        return None


class CaseResultV1(_Strict):
    """Per-arm file. Omits prompt text and the composition body."""

    schema_version: Literal["workflow.case_result.v1"] = CASE_RESULT_SCHEMA
    case_id: str = Field(..., min_length=1, max_length=64)
    arm: ArmId
    metrics: CaseMetricsV1
    seed_status: SeedStatus
    profile_conditioning: ConditioningStatus
    reference_conditioning: ConditioningStatus
    generation_latency_ms: int = Field(..., ge=0)
    composition_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    invalid_composition: bool


class ArmRollupV1(_Strict):
    arm: ArmId
    cases_scored: int = Field(..., ge=0)
    invalid_cases: int = Field(..., ge=0)
    invalid_composition_rate: float = Field(..., ge=0, le=1)
    hard_constraint_pass_rate: float | None = None
    structural_pass_rate: float | None = None
    instrument_range_pass_rate: float | None = None
    revision_preservation_pass_rate: float | None = None
    motif_recurrence_mean: float | None = None
    section_contrast_mean: float | None = None
    tonal_consistency_mean: float | None = None
    generation_latency_ms_mean: float | None = None
    process_rss_kb_mean: float | None = None
    remote_cost_micros_mean: float | None = None


class BenchmarkRunV1(_Strict):
    schema_version: Literal["workflow.benchmark_run.v1"] = BENCHMARK_RUN_SCHEMA
    run_id: str = Field(..., min_length=8, max_length=64)
    benchmark_id: str
    benchmark_version: str
    suite_sha256: str = Field(..., min_length=64, max_length=64)
    created_at: str
    arms: list[ArmId] = Field(..., min_length=1)
    case_ids: list[str] = Field(..., min_length=1)
    rollups: list[ArmRollupV1]
    musical_quality_claim: Literal[False] = False
    case_paths: list[str] = Field(default_factory=list)


class ListeningClipV1(_Strict):
    label: ListeningLabel
    composition: dict[str, Any]


class ListeningPacketV1(_Strict):
    schema_version: Literal["workflow.listening_packet.v1"] = LISTENING_PACKET_SCHEMA
    benchmark_id: str
    benchmark_version: str
    suite_sha256: str = Field(..., min_length=64, max_length=64)
    case_id: str
    mode: ListeningMode
    packet_sha256: str = Field(..., min_length=64, max_length=64)
    clips: list[ListeningClipV1] = Field(..., min_length=2, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def _reject_metric_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _assert_no_forbidden_keys(data)
        return data

    @model_validator(mode="after")
    def _label_count(self) -> ListeningPacketV1:
        labels = [clip.label for clip in self.clips]
        expected = 3 if self.mode == "abc" else 2
        if len(labels) != expected or len(set(labels)) != expected:
            raise ValueError("listening packet label count does not match mode")
        return self


class ListeningJudgmentV1(_Strict):
    schema_version: Literal["workflow.listening_judgment.v1"] = LISTENING_JUDGMENT_SCHEMA
    benchmark_id: str
    benchmark_version: str
    suite_sha256: str = Field(..., min_length=64, max_length=64)
    packet_sha256: str = Field(..., min_length=64, max_length=64)
    case_id: str
    winner: ListeningLabel
    comment: str | None = Field(default=None, max_length=240)


class RegressionMetricV1(_Strict):
    name: str
    status: Literal["pass", "fail", "not_applicable"]
    gate: bool
    baseline: float | None = None
    current: float | None = None


class RegressionReportV1(_Strict):
    schema_version: Literal["workflow.regression.v1"] = REGRESSION_SCHEMA
    benchmark_id: str
    benchmark_version: str
    suite_sha256: str
    run_id: str
    baseline_run_id: str | None = None
    status: Literal["pass", "fail", "baseline_missing", "suite_digest_mismatch"]
    metrics: list[RegressionMetricV1] = Field(default_factory=list)
    musical_quality_claim: Literal[False] = False


def _assert_no_forbidden_keys(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key) in FORBIDDEN_PACKET_KEYS:
                raise ValueError(f"listening packet forbids key {key}")
            _assert_no_forbidden_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_forbidden_keys(item)


def suite_digest(suite: WorkflowBenchmarkV1) -> str:
    """Canonical SHA-256 of a validated suite."""
    return canonical_sha256(suite.model_dump(mode="json"))


def load_benchmark_suite(path: str | Any) -> tuple[WorkflowBenchmarkV1, str]:
    """Load ``workflow.benchmark.v1`` and return the document plus its digest."""
    from pathlib import Path

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    suite = WorkflowBenchmarkV1.model_validate(raw)
    digest = suite_digest(suite)
    logger.info(
        "Suite loaded",
        extra={
            "benchmark_version": suite.benchmark_version,
            "case_count": len(suite.cases),
        },
    )
    logger.debug("Suite digest", extra={"digest_prefix": digest[:12]})
    return suite, digest
