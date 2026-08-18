from app.llm_settings import default_provider, load_llm_settings


def test_load_llm_settings_with_no_keys():
    settings = load_llm_settings({})

    assert settings.providers == ()
    assert settings.default_provider is None


def test_load_llm_settings_openai_only():
    settings = load_llm_settings({"OPENAI_API_KEY": "secret"})

    assert len(settings.providers) == 1
    assert settings.providers[0].provider == "openai"
    assert settings.providers[0].model == "gpt-4o-mini"
    assert settings.providers[0].is_default is True
    assert default_provider({"OPENAI_API_KEY": "secret"}).provider == "openai"


def test_load_llm_settings_prefers_requested_default():
    settings = load_llm_settings(
        {
            "OPENAI_API_KEY": "secret",
            "DEEPSEEK_API_KEY": "secret2",
            "DEFAULT_LLM_PROVIDER": "deepseek",
        }
    )

    assert [provider.provider for provider in settings.providers] == ["openai", "deepseek"]
    assert settings.default_provider == "deepseek"
    assert [provider.is_default for provider in settings.providers] == [False, True]


def test_invalid_numeric_settings_are_defaulted():
    settings = load_llm_settings({"LLM_REQUEST_TIMEOUT_SECONDS": "bad", "LLM_TEMPERATURE": "bad"})

    assert settings.request_timeout_seconds == 60
    assert settings.temperature == 0.7
