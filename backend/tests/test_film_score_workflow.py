"""Mocked agents for the film-score preview order and bar counts."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from app.ai_agents import registry as agent_registry
from app.ai_agents.artifact_schemas import (
    AgentArrangementPlanV1,
    AgentFormPlanV1,
    AgentHarmonyPlanV1,
    AgentMotifPlanV1,
    AgentOrchestrationPlanV1,
    FormPlanSection,
)
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactProvenance,
    AgentArtifactV1,
    AgentBriefV1,
    AgentCritiqueV1,
    AgentDescriptor,
    AgentOperation,
    AgentRunResult,
    AgentStatus,
    CritiqueRecommendation,
    MusicAgentCapability,
)
from app.composition_schemas import CompositionV2
from app.film_score_schemas import FilmCueSnapshot, FilmScoreError, FilmScorePreviewRequest
from app.services.film_score_workflow import run_film_score_preview


class _RecordingAgent:
    def __init__(self, agent_id: str, calls: list[tuple[str, str]], *, fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail
        self._id = agent_id
        self.descriptor = AgentDescriptor(
            id=agent_id,
            display_name=agent_id,
            capability=MusicAgentCapability(agent_id),
            supported_operations=[
                AgentOperation.PLAN,
                AgentOperation.PROPOSE,
                AgentOperation.CRITIQUE,
            ],
            status=AgentStatus.READY,
        )

    async def run(self, request):
        self.calls.append((request.agent_id, request.operation.value))
        if self.fail:
            raise RuntimeError("boom")
        artifact = _artifact_for(request.agent_id, request.context.source_fingerprint)
        recommendation = CritiqueRecommendation.REVISE if request.agent_id == "critic" else None
        return AgentRunResult(
            agent_id=request.agent_id,
            operation=request.operation,
            artifacts=[artifact] if artifact is not None else [],
            recommendation=recommendation,
        )


def _artifact_for(agent_id: str, fingerprint: str) -> AgentArtifactV1 | None:
    payload: dict | None
    content_type: str
    kind = AgentArtifactKind.PLAN
    if agent_id == "creative_director":
        content_type = "agent.brief.v1"
        kind = AgentArtifactKind.BRIEF
        payload = AgentBriefV1(intent="Score the scene").model_dump(mode="json")
    elif agent_id == "structure_form":
        content_type = "agent.form_plan.v1"
        payload = AgentFormPlanV1(
            sections=[FormPlanSection(label="Stub", start_bar=1, bar_count=4)]
        ).model_dump(mode="json")
    elif agent_id == "harmony":
        content_type = "agent.harmony_plan.v1"
        payload = AgentHarmonyPlanV1().model_dump(mode="json")
    elif agent_id == "melody_motif":
        content_type = "agent.motif_plan.v1"
        payload = AgentMotifPlanV1().model_dump(mode="json")
    elif agent_id == "arrangement":
        content_type = "agent.arrangement_plan.v1"
        payload = AgentArrangementPlanV1().model_dump(mode="json")
    elif agent_id == "orchestration":
        content_type = "agent.orchestration_plan.v1"
        payload = AgentOrchestrationPlanV1().model_dump(mode="json")
    elif agent_id == "critic":
        content_type = "agent.critique.v1"
        kind = AgentArtifactKind.CRITIQUE
        payload = AgentCritiqueV1(recommendation=CritiqueRecommendation.REVISE).model_dump(mode="json")
    else:
        return None
    return AgentArtifactV1(
        kind=kind,
        producer_agent_id=agent_id,
        content_type=content_type,
        payload=payload,
        source_fingerprint=fingerprint,
        provenance=AgentArtifactProvenance(operation="plan", agent_id=agent_id),
    )


def _cue(hit_id: str, seconds: float) -> FilmCueSnapshot:
    return FilmCueSnapshot(
        id=hit_id,
        kind="hit_point",
        importance="critical",
        video_seconds=seconds,
        tolerance_frames=0,
    )


def _source(*, motif: bool = False) -> CompositionV2:
    events = [
        {
            "type": "note",
            "id": f"note-{index}",
            "pitch": "C4",
            "start_tick": index * 480,
            "duration_ticks": 240,
            "velocity": 80,
        }
        for index in range(3)
    ]
    body: dict = {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "duration_ticks": 8 * 1920,
        "bar_count": 8,
        "sections": [
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 8,
                "start_tick": 0,
                "duration_ticks": 8 * 1920,
            }
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "acoustic_grand_piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": events,
            }
        ],
    }
    if motif:
        body["motifs"] = [
            {
                "id": "theme-a",
                "label": "Theme",
                "occurrences": [
                    {
                        "id": "occ-1",
                        "track_id": "melody-1",
                        "event_ids": ["note-0", "note-1", "note-2"],
                        "relationship": "original",
                    }
                ],
            }
        ]
    return CompositionV2.model_validate(body)


def _request(**overrides) -> FilmScorePreviewRequest:
    body = {
        "brief": "Score the chase",
        "instruments": ["acoustic_grand_piano"],
        "opening_tempo": 120,
        "tempo_min": 96,
        "tempo_max": 132,
        "target_duration_seconds": 180,
    }
    body.update(overrides)
    return FilmScorePreviewRequest.model_validate(body)


def _register(calls: list[tuple[str, str]], *, fail_critic: bool = False) -> None:
    agent_registry.clear_registry_for_tests()
    for agent_id in (
        "creative_director",
        "structure_form",
        "harmony",
        "melody_motif",
        "arrangement",
        "orchestration",
        "critic",
    ):
        agent_registry.register_agent(
            _RecordingAgent(agent_id, calls, fail=fail_critic and agent_id == "critic")
        )


def _preview(**kwargs):
    return run_film_score_preview(
        project_id="proj_film",
        source_fingerprint="source-fingerprint",
        scoring_document_revision=3,
        cues=[
            _cue("hit_0000000a", 10),
            _cue("hit_0000000b", 40),
            _cue("hit_0000000c", 60),
        ],
        frame_rate_numerator=24,
        frame_rate_denominator=1,
        asset_duration_seconds=180,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _registry():
    agent_registry.clear_registry_for_tests()
    yield
    agent_registry.clear_registry_for_tests()


def test_call_order_and_compiled_bar_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    calls: list[tuple[str, str]] = []
    _register(calls)
    preview = asyncio.run(_preview(source=_source(), request=_request()))
    assert calls == [
        ("creative_director", "plan"),
        ("structure_form", "plan"),
        ("harmony", "propose"),
        ("arrangement", "propose"),
        ("orchestration", "propose"),
        ("critic", "critique"),
    ]
    assert "melody_motif" not in [item[0] for item in calls]
    motif = preview.artifact_role_map["motif_plan"]
    assert motif is not None
    assert motif["content_type"] == "agent.motif_plan.v1"
    assert preview.candidate is not None
    assert preview.candidate["bar_count"] == 90
    assert preview.candidate["tempo_changes"] == []
    assert preview.plan.committed is False
    assert preview.committed is False
    assert preview.candidate["sections"][0]["type"] == "intro"
    assert preview.candidate["sections"][-1]["type"] == "outro"


def test_motif_call_is_included_when_ids_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    calls: list[tuple[str, str]] = []
    _register(calls)
    asyncio.run(
        _preview(
            source=_source(motif=True),
            request=_request(motif_ids=["theme-a"]),
        )
    )
    assert [item[0] for item in calls] == [
        "creative_director",
        "structure_form",
        "harmony",
        "melody_motif",
        "arrangement",
        "orchestration",
        "critic",
    ]


def test_missing_motif_fails_before_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    calls: list[tuple[str, str]] = []
    _register(calls)
    with pytest.raises(FilmScoreError) as caught:
        asyncio.run(
            _preview(
                source=_source(),
                request=_request(motif_ids=["missing-theme"]),
            )
        )
    assert caught.value.code == "film_motif_missing"
    assert calls == []


def test_agent_failure_keeps_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    calls: list[tuple[str, str]] = []
    _register(calls, fail_critic=True)
    with pytest.raises(FilmScoreError) as caught:
        asyncio.run(_preview(source=_source(), request=_request()))
    assert caught.value.code == "film_agent_failed"
    preview = caught.value.preview
    assert preview is not None
    assert preview.candidate is None
    assert preview.plan.schema_version == "film.score.plan.v1"
    assert any(item["content_type"] == "agent.motif_plan.v1" for item in preview.artifact_log)


def test_profile_off_keeps_compiled_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    _register([])
    preview = asyncio.run(
        _preview(
            source=_source(),
            request=_request(profile_id="profile-1", profile_strength="off"),
        )
    )
    assert preview.candidate is not None
    assert preview.candidate["bar_count"] == 90


def test_ai_agents_do_not_import_film_or_video_modules() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "ai_agents"
    banned = ("video_scoring", "video_spotting", "llm_video_spotting", "film_score_", "film_score_adapt")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                for token in banned:
                    assert token not in module, f"{path} imports {module}"
