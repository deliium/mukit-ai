"""Category protocols. The host checks only the method for the manifest category."""

from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable

from .context import PluginContext


@runtime_checkable
class LanguageModelPlugin(Protocol):
    def complete_text(self, ctx: PluginContext, prompt: str) -> str: ...


@runtime_checkable
class SymbolicComposerPlugin(Protocol):
    def compose(self, ctx: PluginContext, request: Mapping) -> Mapping: ...


@runtime_checkable
class MusicAgentPlugin(Protocol):
    def run(self, ctx: PluginContext, request: Mapping) -> Mapping: ...


@runtime_checkable
class AnalyzerPlugin(Protocol):
    def analyze(self, ctx: PluginContext, composition: Mapping) -> Mapping: ...


@runtime_checkable
class TranscriptionModelPlugin(Protocol):
    def transcribe(self, ctx: PluginContext, audio_ref: Mapping) -> Mapping: ...


@runtime_checkable
class NeuralRendererPlugin(Protocol):
    def render(self, ctx: PluginContext, spec: Mapping) -> Mapping: ...


@runtime_checkable
class ExportFormatPlugin(Protocol):
    def export(self, ctx: PluginContext, composition: Mapping) -> tuple[bytes, str]: ...


@runtime_checkable
class PostprocessPlugin(Protocol):
    def process(self, ctx: PluginContext, document: Mapping) -> Mapping: ...
