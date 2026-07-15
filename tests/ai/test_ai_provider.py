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


def test_gigachat_factory_uses_custom_ca_bundle(monkeypatch):
    captured = {}

    class FakeGigaChatProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(
        "dzen_commenter.ai.factory.GigaChatProvider", FakeGigaChatProvider
    )
    settings = make_settings(
        AI_PROVIDER="gigachat",
        GIGACHAT_CA_BUNDLE="/certs/russian-root.crt",
    )

    create_provider(settings)

    assert captured["verify_ssl_certs"] == "/certs/russian-root.crt"


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
def test_yandex_stub_still_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        YandexGPTProvider().generate("x", temperature=0.1, max_tokens=8)


# 9
def test_gigachat_reuses_access_token():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/oauth"):
            return httpx.Response(200, json={"access_token": "token-1", "expires_at": 4102444800})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    provider = GigaChatProvider(
        api_key="authorization-key",
        base_url="https://gigachat.example/api",
        model="GigaChat-Pro",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert provider.generate("one", temperature=0.1, max_tokens=8) == "ok"
    assert provider.generate("two", temperature=0.1, max_tokens=8) == "ok"
    assert sum(request.url.path.endswith("/oauth") for request in calls) == 1
    assert all(
        request.headers.get("Authorization") == "Bearer token-1"
        for request in calls
        if request.url.path.endswith("/chat/completions")
    )


# 10
def test_gigachat_refreshes_token_near_expiry(monkeypatch):
    now = 0.0
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("/oauth"):
            token_calls += 1
            return httpx.Response(
                200, json={"access_token": f"token-{token_calls}", "expires_at": 100}
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    monkeypatch.setattr("dzen_commenter.ai.gigachat.time.time", lambda: now)
    provider = GigaChatProvider(
        api_key="authorization-key",
        base_url="https://gigachat.example/api",
        model="GigaChat-Pro",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provider.generate("one", temperature=0.1, max_tokens=8)
    now = 20.0
    provider.generate("two", temperature=0.1, max_tokens=8)
    now = 41.0
    provider.generate("three", temperature=0.1, max_tokens=8)
    assert token_calls == 2


# 11
def test_gigachat_refreshes_token_after_401():
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("/oauth"):
            token_calls += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{token_calls}", "expires_at": 4102444800},
            )
        if request.headers.get("Authorization") == "Bearer token-1":
            return httpx.Response(401, json={"error": "expired token"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "recovered"}}]}
        )

    provider = GigaChatProvider(
        api_key="authorization-key",
        base_url="https://gigachat.example/api",
        model="GigaChat-Pro",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert provider.generate("prompt", temperature=0.1, max_tokens=8) == "recovered"
    assert token_calls == 2
