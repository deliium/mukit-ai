"""Compose profile names stay opt-in and off the default gate."""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str) -> dict:
    return yaml.safe_load((_ROOT / name).read_text(encoding="utf-8"))


def _profiles(document: dict) -> set[str]:
    names: set[str] = set()
    for service in (document.get("services") or {}).values():
        for name in service.get("profiles") or []:
            names.add(str(name))
    return names


def test_optional_profiles_are_not_on_the_default_compose_file() -> None:
    default = _load("docker-compose.yml")
    assert _profiles(default) == set()
    assert "profiles" not in default
    assert _profiles(_load("compose.local-ai.yml")) >= {"local-ai", "local-ai-vllm", "training"}
    assert _profiles(_load("compose.neural-audio.yml")) == {"neural-audio"}
    assert _profiles(_load("compose.audio-recovery.yml")) == {"audio-recovery"}


def test_default_test_script_does_not_start_docker_acceptance() -> None:
    script = (_ROOT / "scripts" / "run_tests.sh").read_text(encoding="utf-8")
    assert "v4_docker_acceptance.sh" not in script
    assert "RUN_DOCKER_ACCEPTANCE" not in script
