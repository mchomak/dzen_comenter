from collections.abc import Callable

from dzen_commenter.contracts.interfaces import PromptContext
from dzen_commenter.prompt.config_loader import PromptBrandConfig, load_brand_config


class DameoPromptBuilder:
    """Build a complete Russian-language prompt for a Dzen comment reply."""

    def __init__(
        self,
        language: str | None = None,
        config_path: str | None = None,
        config_provider: Callable[[], PromptBrandConfig] | None = None,
    ) -> None:
        self._config_provider = config_provider
        if config_provider is not None:
            self._config: PromptBrandConfig | None = None
            self.language = language
        else:
            self._config = load_brand_config(config_path)
            self.language = language if language is not None else self._config.language

    def build(self, context: PromptContext) -> str:
        if self._config_provider is not None:
            config = self._config_provider()
        else:
            config = self._config
        task = (
            config.task_lead
            if context.reply_type == "lead"
            else config.task_engage
        )
        if context.comment_text:
            context_block = (
                "ВХОДНЫЕ ДАННЫЕ:\n"
                f"Тема статьи: {context.publication_title}\n"
                f"Ветка комментариев (предыдущие сообщения): {context.thread_text or 'нет предыдущих сообщений'}\n"
                f"Комментарий, на который нужно ответить: {context.comment_text}"
            )
        else:
            context_block = (
                "Контекст:\n"
                f"Тема публикации: {context.publication_title}\n"
                f"Ветка обсуждения: {context.thread_text}"
            )
        blocks = [
            config.role,
            config.tone_of_voice,
            config.anti_rules,
            context_block,
            task,
        ]
        text = "\n\n".join(blocks)
        return text.replace("{cta_link}", config.cta_link)
