import logging
import os
from dataclasses import dataclass
from typing import Mapping


logger = logging.getLogger(__name__)

OPENAI_PROVIDER = "openai"
DEEPSEEK_PROVIDER = "deepseek"
SUPPORTED_PROVIDERS = {OPENAI_PROVIDER, DEEPSEEK_PROVIDER}


@dataclass(frozen=True)
class LLMProviderSettings:
    provider: str
    model: str
    api_key: str
    base_url: str | None = None
    is_default: bool = False


@dataclass(frozen=True)
class LLMSettings:
    providers: tuple[LLMProviderSettings, ...]
    default_provider: str | None
    request_timeout_seconds: int
    temperature: float


def load_llm_settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    source = env if env is not None else os.environ
    requested_default = _normalized_provider(source.get("DEFAULT_LLM_PROVIDER"))

    providers = []
    openai_key = source.get("OPENAI_API_KEY")
    if openai_key:
        providers.append(
            LLMProviderSettings(
                provider=OPENAI_PROVIDER,
                model=source.get("OPENAI_MODEL", "gpt-4o-mini"),
                api_key=openai_key,
            )
        )
    else:
        logger.debug("LLM provider key missing", extra={"provider": OPENAI_PROVIDER})

    deepseek_key = source.get("DEEPSEEK_API_KEY")
    if deepseek_key:
        providers.append(
            LLMProviderSettings(
                provider=DEEPSEEK_PROVIDER,
                model=source.get("DEEPSEEK_MODEL", "deepseek-chat"),
                api_key=deepseek_key,
                base_url=source.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )
        )
    else:
        logger.debug("LLM provider key missing", extra={"provider": DEEPSEEK_PROVIDER})

    default_provider = _select_default_provider(tuple(providers), requested_default)
    providers_with_default = tuple(
        LLMProviderSettings(
            provider=provider.provider,
            model=provider.model,
            api_key=provider.api_key,
            base_url=provider.base_url,
            is_default=provider.provider == default_provider,
        )
        for provider in providers
    )

    available_names = [provider.provider for provider in providers_with_default]
    logger.info("Available LLM providers loaded", extra={"providers": available_names})

    return LLMSettings(
        providers=providers_with_default,
        default_provider=default_provider,
        request_timeout_seconds=_int_env(source, "LLM_REQUEST_TIMEOUT_SECONDS", 60, 1, 300),
        temperature=_float_env(source, "LLM_TEMPERATURE", 0.7, 0, 2),
    )


def available_providers(env: Mapping[str, str] | None = None) -> tuple[LLMProviderSettings, ...]:
    settings = load_llm_settings(env)
    return settings.providers


def default_provider(env: Mapping[str, str] | None = None) -> LLMProviderSettings | None:
    settings = load_llm_settings(env)
    for provider in settings.providers:
        if provider.is_default:
            return provider
    return None


def get_provider_settings(provider_name: str, env: Mapping[str, str] | None = None) -> LLMProviderSettings | None:
    normalized = _normalized_provider(provider_name)
    if normalized is None:
        return None

    for provider in available_providers(env):
        if provider.provider == normalized:
            return provider
    return None


def _select_default_provider(
    providers: tuple[LLMProviderSettings, ...], requested_default: str | None
) -> str | None:
    available = {provider.provider for provider in providers}
    if requested_default in available:
        return requested_default
    if requested_default and requested_default not in SUPPORTED_PROVIDERS:
        logger.debug("Ignoring unsupported default LLM provider", extra={"provider": requested_default})
    if providers:
        return providers[0].provider
    return None


def _normalized_provider(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if normalized in SUPPORTED_PROVIDERS:
        return normalized
    return normalized


def _int_env(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = env.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.debug("Invalid integer LLM setting", extra={"setting_name": name, "raw_value": raw_value})
        return default
    return min(max(value, minimum), maximum)


def _float_env(env: Mapping[str, str], name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = env.get(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.debug("Invalid float LLM setting", extra={"setting_name": name, "raw_value": raw_value})
        return default
    return min(max(value, minimum), maximum)
