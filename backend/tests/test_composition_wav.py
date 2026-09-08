"""Unit tests for Composition V1 → WAV rendering (mocked FluidSynth)."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from app.schemas import CompositionV2
from app.services import composition_wav as wav_module
from app.services.composition_midi import MidiRenderResult
from app.services.composition_projection import empty_projection_report
from app.services.composition_wav import (
    CompositionWavError,
    expected_duration_seconds,
    load_wav_renderer_config,
    render_wav,
)
from tests.test_export_fidelity import build_export_fidelity_composition


def _fake_midi_result(_composition) -> MidiRenderResult:
    return MidiRenderResult(midi_bytes=b"MThd-fake-midi", report=empty_projection_report())


def _silent_composition() -> CompositionV2:
    composition = build_export_fidelity_composition()
    data = composition.model_dump()
    for track in data["tracks"]:
        track["events"] = []
    return CompositionV2.model_validate(data)


def _minimal_wav_bytes(*, duration_seconds: float = 0.5, sample_rate: int = 44100) -> bytes:
    n_frames = max(1, int(round(duration_seconds * sample_rate)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * (n_frames * 2 * 2))
    return buffer.getvalue()


def test_load_wav_renderer_config_from_env(monkeypatch, tmp_path, caplog):
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"SF2")

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(soundfont))
    monkeypatch.setenv("COMPOSITION_WAV_SAMPLE_RATE", "48000")
    monkeypatch.setenv("COMPOSITION_WAV_GAIN", "0.4")
    monkeypatch.setenv("COMPOSITION_WAV_TIMEOUT_SECONDS", "12")

    with caplog.at_level("DEBUG"):
        config = load_wav_renderer_config()

    assert config.fluidsynth_exists is True
    assert config.soundfont_exists is True
    assert config.sample_rate == 48000
    assert config.gain == 0.4
    assert config.timeout_seconds == 12.0
    assert "Resolved WAV renderer config" in caplog.text


def test_render_wav_silence_path_without_fluidsynth(caplog):
    composition = _silent_composition()
    expected = expected_duration_seconds(composition)

    with caplog.at_level("DEBUG"):
        wav_bytes = render_wav(composition)

    assert wav_bytes[:4] == b"RIFF"
    assert wav_bytes[8:12] == b"WAVE"
    measured = wav_module._wav_duration_seconds(wav_bytes)
    assert measured == pytest.approx(expected, abs=0.02)
    assert "WAV silence path selected" in caplog.text
    assert "WAV render completed" in caplog.text


def test_render_wav_invokes_fluidsynth_without_shell(monkeypatch, tmp_path, caplog):
    composition = build_export_fidelity_composition()
    expected = expected_duration_seconds(composition)
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"SF2")

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(soundfont))
    monkeypatch.setattr(wav_module, "render_midi_with_report", _fake_midi_result)

    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": list(command), "kwargs": kwargs})
        # Command writes -F <wav_path>; create a short WAV that needs padding.
        wav_path = Path(command[command.index("-F") + 1])
        wav_path.write_bytes(_minimal_wav_bytes(duration_seconds=max(0.05, expected * 0.5)))

        class Result:
            returncode = 0
            stdout = b""
            stderr = b""

        return Result()

    monkeypatch.setattr(wav_module.subprocess, "run", fake_run)

    with caplog.at_level("INFO"):
        wav_bytes = render_wav(composition)

    assert calls, "FluidSynth subprocess.run was not called"
    call = calls[0]
    assert call["kwargs"].get("shell") is False
    assert call["kwargs"].get("timeout") == wav_module.DEFAULT_RENDER_TIMEOUT_SECONDS
    command = call["command"]
    assert command[0] == str(fake_bin)
    assert "-ni" in command
    assert "-F" in command
    assert soundfont.name in " ".join(command) or str(soundfont) in command
    assert any(part.endswith(".mid") for part in command)
    assert wav_bytes[:4] == b"RIFF"
    measured = wav_module._wav_duration_seconds(wav_bytes)
    assert measured == pytest.approx(expected, abs=0.05)
    assert "WAV render completed" in caplog.text
    assert "Padding short FluidSynth WAV" in caplog.text


def test_render_wav_missing_soundfont_is_unavailable(monkeypatch, tmp_path):
    composition = build_export_fidelity_composition()
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    missing_sf = tmp_path / "missing.sf2"

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(missing_sf))
    monkeypatch.setattr(wav_module, "render_midi_with_report", _fake_midi_result)

    with pytest.raises(CompositionWavError) as exc_info:
        render_wav(composition)

    assert exc_info.value.unavailable is True
    assert "SoundFont" in str(exc_info.value)


def test_render_wav_timeout(monkeypatch, tmp_path, caplog):
    composition = build_export_fidelity_composition()
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"SF2")

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(soundfont))
    monkeypatch.setenv("COMPOSITION_WAV_TIMEOUT_SECONDS", "3")
    monkeypatch.setattr(wav_module, "render_midi_with_report", _fake_midi_result)

    def boom(*_args, **_kwargs):
        raise wav_module.subprocess.TimeoutExpired(cmd="fluidsynth", timeout=3)

    monkeypatch.setattr(wav_module.subprocess, "run", boom)

    with caplog.at_level("WARNING"):
        with pytest.raises(CompositionWavError, match="timed out"):
            render_wav(composition)

    assert "timed out" in caplog.text.lower()


def test_render_wav_nonzero_exit(monkeypatch, tmp_path, caplog):
    composition = build_export_fidelity_composition()
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"SF2")

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(soundfont))
    monkeypatch.setattr(wav_module, "render_midi_with_report", _fake_midi_result)

    class Result:
        returncode = 7
        stdout = b"out"
        stderr = b"synth failed hard"

    monkeypatch.setattr(wav_module.subprocess, "run", lambda *_a, **_k: Result())

    with caplog.at_level("ERROR"):
        with pytest.raises(CompositionWavError, match="exit code 7"):
            render_wav(composition)

    assert "exit_code" in caplog.text or "non-zero" in caplog.text.lower()


def test_render_wav_rejects_invalid_output(monkeypatch, tmp_path, caplog):
    composition = build_export_fidelity_composition()
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"SF2")

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(soundfont))
    monkeypatch.setattr(wav_module, "render_midi_with_report", _fake_midi_result)

    def fake_run(command, **_kwargs):
        wav_path = Path(command[command.index("-F") + 1])
        wav_path.write_bytes(b"not-a-wav")

        class Result:
            returncode = 0
            stdout = b""
            stderr = b""

        return Result()

    monkeypatch.setattr(wav_module.subprocess, "run", fake_run)

    with caplog.at_level("ERROR"):
        with pytest.raises(CompositionWavError, match="invalid WAV"):
            render_wav(composition)

    assert "not a valid RIFF/WAVE" in caplog.text


def test_temp_directory_cleaned_after_render(monkeypatch, tmp_path):
    composition = build_export_fidelity_composition()
    fake_bin = tmp_path / "fluidsynth"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    soundfont = tmp_path / "FluidR3_GM.sf2"
    soundfont.write_bytes(b"SF2")

    monkeypatch.setenv("FLUIDSYNTH_BIN", str(fake_bin))
    monkeypatch.setenv("COMPOSITION_WAV_SOUNDFONT", str(soundfont))
    monkeypatch.setattr(wav_module, "render_midi_with_report", _fake_midi_result)

    observed_dirs: list[Path] = []

    def fake_run(command, **_kwargs):
        wav_path = Path(command[command.index("-F") + 1])
        observed_dirs.append(wav_path.parent)
        wav_path.write_bytes(_minimal_wav_bytes(duration_seconds=expected_duration_seconds(composition)))

        class Result:
            returncode = 0
            stdout = b""
            stderr = b""

        return Result()

    monkeypatch.setattr(wav_module.subprocess, "run", fake_run)
    render_wav(composition)

    assert observed_dirs
    assert not observed_dirs[0].exists()


def test_expected_duration_uses_tempo_map():
    import json
    from pathlib import Path

    from app.composition_schemas import CompositionV2

    raw = json.loads((Path(__file__).parent / "fixtures" / "timeline_mixed_meter_tempo.json").read_text())
    raw.pop("expectations")
    composition = CompositionV2.model_validate(raw)
    assert expected_duration_seconds(composition) == pytest.approx(6.0, abs=0.001)
