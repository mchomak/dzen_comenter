from dzen_commenter.config.settings import Settings
from dzen_commenter.contracts.interfaces import AIProvider

from .gigachat import GigaChatProvider
from .openai_compatible import OpenAICompatibleProvider
from .yandexgpt import YandexGPTProvider

_SUPPORTED = ("openai", "gigachat", "yandexgpt")


def create_provider(settings: Settings) -> AIProvider:
    name = settings.AI_PROVIDER.strip().lower()
    if name == "openai":
        return OpenAICompatibleProvider(
            api_key=settings.AI_API_KEY,
            base_url=settings.AI_BASE_URL,
            model=settings.AI_MODEL,
        )
    if name == "gigachat":
        return GigaChatProvider(
            api_key=settings.GIGACHAT_AUTH_KEY or settings.AI_API_KEY,
            base_url=settings.GIGACHAT_BASE_URL or settings.AI_BASE_URL,
            model=settings.GIGACHAT_MODEL or settings.AI_MODEL,
            scope=settings.GIGACHAT_SCOPE,
            oauth_url=settings.GIGACHAT_OAUTH_URL,
            verify_ssl_certs=settings.GIGACHAT_VERIFY_SSL_CERTS,
        )
    if name == "yandexgpt":
        return YandexGPTProvider(
            api_key=settings.AI_API_KEY,
            base_url=settings.AI_BASE_URL,
            model=settings.AI_MODEL,
        )
    raise ValueError(
        f"Неизвестный AI_PROVIDER={settings.AI_PROVIDER!r}. "
        f"Поддерживаются: {', '.join(_SUPPORTED)}."
    )
