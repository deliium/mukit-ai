"""Public plugin SDK. Plugins may import this package and nothing else under ``app``."""

from .config import validate_config
from .context import PluginContext
from .errors import PluginError
from .manifest import (
    CATEGORY_CAPABILITIES,
    CATEGORY_METHOD,
    PluginCategory,
    PluginDependencyV1,
    PluginManifestV1,
    parse_manifest,
)
from .protocols import (
    AnalyzerPlugin,
    ExportFormatPlugin,
    LanguageModelPlugin,
    MusicAgentPlugin,
    NeuralRendererPlugin,
    PostprocessPlugin,
    SymbolicComposerPlugin,
    TranscriptionModelPlugin,
)
from .version import PLUGIN_API_VERSION, accepts_api_compatibility, api_major

__all__ = [
    "AnalyzerPlugin",
    "CATEGORY_CAPABILITIES",
    "CATEGORY_METHOD",
    "ExportFormatPlugin",
    "LanguageModelPlugin",
    "MusicAgentPlugin",
    "NeuralRendererPlugin",
    "PLUGIN_API_VERSION",
    "PluginCategory",
    "PluginContext",
    "PluginDependencyV1",
    "PluginError",
    "PluginManifestV1",
    "PostprocessPlugin",
    "SymbolicComposerPlugin",
    "TranscriptionModelPlugin",
    "accepts_api_compatibility",
    "api_major",
    "parse_manifest",
    "validate_config",
]
