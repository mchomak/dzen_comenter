import inspect

import httpx
import pytest

import dzen_commenter.ai  # noqa: F401
from dzen_commenter.ai import (
    GigaChatProvider,
    OpenAICompatibleProvider,
    YandexGPTProvider,
    create_provider,
)
from dzen_commenter.config.settings import Settings

ADAPTERS = [OpenAICompatibleProvider, GigaChatProvider, YandexGPTProvider]


def make_settings(**overrides) -> Settings:
    base = dict(
        DATABASE_URL="postgresql://x",
        AI_PROVIDER="openai",
        AI_MODEL="test-model",
        AI_API_KEY="secret-key",
        AI_BASE_URL="https://api.example.com/v1",
        AI_TEMPERATURE=0.3,
        AI_MAX_TOKENS=64,
        AI_PROMPT_LANGUAGE="ru",
        USER_DATA_DIR="/tmp/u",
        STORAGE_STATE_PATH="/tmp/s.json",
        HEADLESS=True,
        COMMENTS_URL="https://dzen.ru/x",
        POLL_INTERVAL=30,
        KEEPALIVE_INTERVAL=300,
        AUTO_PUBLISH=False,
        MAX_REPLIES_PER_CYCLE=5,
    )
    base.update(overrides)
    # _env_file=None — не подмешивать .env, брать только переданные значения.
    return Settings(_env_file=None, **base)


# 2 + регистр
@pytest.mark.parametrize(
    "provider_value,expected",
    [
        ("openai", OpenAICompatibleProvider),
        ("OpenAI", OpenAICompatibleProvider),
        ("OPENAI", OpenAICompatibleProvider),
        ("gigachat", GigaChatProvider),
        ("yandexgpt", YandexGPTProvider),
    ],
)
def test_factory_returns_expected_adapter(provider_value, expected):
    settings = make_settings(AI_PROVIDER=provider_value)
    provider = create_provider(settings)
    assert type(provider) is expected


# 3
def test_factory_unknown_provider_raises():
    settings = make_settings(AI_PROVIDER="unknown")
    with pytest.raises(ValueError):
        create_provider(settings)


# 4
@pytest.mark.parametrize("adapter", ADAPTERS)
def test_generate_signature(adapter):
    sig = inspect.signature(adapter.generate)
    params = list(sig.parameters.values())
    # params[0] == self
    names = [p.name for p in params]
    assert names == ["self", "prompt", "temperature", "max_tokens"]
    assert params[2].kind is inspect.Parameter.KEYWORD_ONLY  # temperature
    assert params[3].kind is inspect.Parameter.KEYWORD_ONLY  # max_tokens


# 5
def test_openai_compatible_mock_http():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["json"] = request.read()
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ответ модели"}}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    settings = make_settings()
    provider = OpenAICompatibleProvider(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        model=settings.AI_MODEL,
        client=client,
    )

    result = provider.generate("привет", temperature=0.3, max_tokens=64)
    assert result == "ответ модели"

    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == f"Bearer {settings.AI_API_KEY}"

    import json

    body = json.loads(captured["json"])
    assert body["model"] == settings.AI_MODEL
    assert body["messages"][0] == {"role": "user", "content": "привет"}
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 64


# 6 — смена модели = только .env (AI_MODEL), код не правится
@pytest.mark.parametrize("model_name", ["model-alpha", "model-beta"])
def test_model_change_via_settings_only(model_name):
    import json

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    settings = make_settings(AI_MODEL=model_name)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        api_key=settings.AI_API_KEY,
        base_url=settings.AI_BASE_URL,
        model=settings.AI_MODEL,
        client=client,
    )
    provider.generate("hi", temperature=0.5, max_tokens=10)
    body = json.loads(captured["json"])
    assert body["model"] == model_name


# 7
def test_http_error_propagates():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        api_key="k", base_url="https://api.example.com/v1", model="m", client=client
    )
    with pytest.raises(httpx.HTTPStatusError):
        provider.generate("hi", temperature=0.1, max_tokens=8)


# 8
def test_stubs_raise_not_implemented():
    with pytest.raises(NotImplementedError):
        GigaChatProvider().generate("x", temperature=0.1, max_tokens=8)
    with pytest.raises(NotImplementedError):
        YandexGPTProvider().generate("x", temperature=0.1, max_tokens=8)
