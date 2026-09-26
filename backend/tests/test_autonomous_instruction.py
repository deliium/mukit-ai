"""Closed arrangement-instruction interpreter. No store and no agents."""

from __future__ import annotations

import logging

import pytest

from app.autonomous_composer_schemas import (
    AUTONOMOUS_INSTRUCTION_UNSAFE,
    AutonomousPlanError,
    AutonomousRunStartV1,
    CreativeBriefV1,
    NarrativeBeat,
)
from app.services.autonomous_instruction import (
    THIN_STRING_EFFECT,
    UNPARSED_EFFECT,
    interpret_arrangement_instruction,
)

EXAMPLE = "Keep the melody, but use a smaller string arrangement."


def _interpret(text: str) -> str:
    return interpret_arrangement_instruction(
        text,
        forbidden_families=["drums"],
        opening_key="F# minor",
        final_section_key="F# major",
    )


def test_example_sentence_is_thin_strings() -> None:
    assert _interpret(EXAMPLE) == THIN_STRING_EFFECT


def test_add_drums_is_unsafe() -> None:
    with pytest.raises(AutonomousPlanError) as caught:
        _interpret("Add drums")
    assert caught.value.code == AUTONOMOUS_INSTRUCTION_UNSAFE


def test_new_theme_is_unsafe() -> None:
    with pytest.raises(AutonomousPlanError) as caught:
        _interpret("write a new theme")
    assert caught.value.code == AUTONOMOUS_INSTRUCTION_UNSAFE


def test_foreign_key_in_the_same_sentence_is_unsafe() -> None:
    with pytest.raises(AutonomousPlanError) as caught:
        _interpret("Write it in C major.")
    assert caught.value.code == AUTONOMOUS_INSTRUCTION_UNSAFE


def test_opening_key_mention_stays_safe() -> None:
    assert _interpret("Stay in F# minor with a quieter texture.") == UNPARSED_EFFECT


def test_unrecognized_sentence_is_unparsed() -> None:
    assert _interpret("Make the ending warmer.") == UNPARSED_EFFECT


def test_default_autonomy_mode_is_autonomous() -> None:
    start = AutonomousRunStartV1(
        brief=CreativeBriefV1(
            schema_version="creative.brief.v1",
            duration_seconds=150,
            narrative=[NarrativeBeat(intent="resolve", text="a quiet ending")],
            instrumentation=["piano"],
            opening_key="C major",
        )
    )
    assert start.autonomy_mode == "autonomous"


def test_thin_strings_log_omits_the_sentence(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    _interpret(EXAMPLE)
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert EXAMPLE not in messages
    assert "smaller string" not in messages
    assert any(getattr(record, "effect", None) == THIN_STRING_EFFECT for record in caplog.records)


def test_unsafe_log_has_code_and_length(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    with pytest.raises(AutonomousPlanError):
        _interpret("Add drums")
    warned = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warned
    assert warned[-1].code == AUTONOMOUS_INSTRUCTION_UNSAFE  # type: ignore[attr-defined]
    assert warned[-1].instruction_len == len("Add drums")  # type: ignore[attr-defined]
    assert "Add drums" not in warned[-1].getMessage()
