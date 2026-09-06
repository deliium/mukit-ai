import logging
import os
from dataclasses import dataclass
from typing import Mapping


logger = logging.getLogger(__name__)

OPENAI_PROVIDER = "openai"
DEEPSEEK_PROVIDER = "deepseek"
FAKE_PROVIDER = "fake"
SUPPORTED_PROVIDERS = {OPENAI_PROVIDER, DEEPSEEK_PROVIDER, FAKE_PROVIDER}
FAKE_MODE_ENV = "LLM_FAKE_MODE"
FAKE_MODEL_DEFAULT = "fake-deterministic"


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


def fake_mode_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    raw = (source.get(FAKE_MODE_ENV) or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def load_llm_settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    source = env if env is not None else os.environ
    requested_default = _normalized_provider(source.get("DEFAULT_LLM_PROVIDER"))
    fake_enabled = fake_mode_enabled(source)

    logger.debug(
        "LLM env key presence (booleans only)",
        extra={
            "OPENAI_API_KEY": bool(source.get("OPENAI_API_KEY")),
            "DEEPSEEK_API_KEY": bool(source.get("DEEPSEEK_API_KEY")),
            "DEFAULT_LLM_PROVIDER": bool(source.get("DEFAULT_LLM_PROVIDER")),
            "OPENAI_MODEL": bool(source.get("OPENAI_MODEL")),
            "DEEPSEEK_MODEL": bool(source.get("DEEPSEEK_MODEL")),
            "DEEPSEEK_BASE_URL": bool(source.get("DEEPSEEK_BASE_URL")),
            "LLM_REQUEST_TIMEOUT_SECONDS": bool(source.get("LLM_REQUEST_TIMEOUT_SECONDS")),
            "LLM_TEMPERATURE": bool(source.get("LLM_TEMPERATURE")),
            FAKE_MODE_ENV: fake_enabled,
        },
    )

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

    if fake_enabled:
        # Placeholder key never used for network calls; required by LLMProviderSettings shape.
        providers.insert(
            0,
            LLMProviderSettings(
                provider=FAKE_PROVIDER,
                model=source.get("LLM_FAKE_MODEL", FAKE_MODEL_DEFAULT),
                api_key="fake",
            ),
        )
        logger.info(
            "Fake LLM mode is active (deterministic, no API credits)",
            extra={"provider": FAKE_PROVIDER, "model": source.get("LLM_FAKE_MODEL", FAKE_MODEL_DEFAULT)},
        )
        if openai_key or deepseek_key:
            logger.warning(
                "LLM_FAKE_MODE is set while real provider keys are present; "
                "fake provider wins as default for tests/demos unless DEFAULT_LLM_PROVIDER "
                "explicitly selects a real provider that remains configured",
                extra={
                    "fake_mode": True,
                    "has_openai_key": bool(openai_key),
                    "has_deepseek_key": bool(deepseek_key),
                },
            )

    # When fake mode is on and the user did not request a specific default, prefer fake.
    effective_requested = requested_default
    if fake_enabled and requested_default is None:
        effective_requested = FAKE_PROVIDER
    elif fake_enabled and requested_default not in {p.provider for p in providers}:
        effective_requested = FAKE_PROVIDER

    default_provider = _select_default_provider(tuple(providers), effective_requested)
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
    # Preserve unknown tokens so callers can surface UnsupportedLLMProviderError.
    return normalized if normalized else None


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
