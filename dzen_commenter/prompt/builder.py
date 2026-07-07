from dzen_commenter.contracts.interfaces import PromptContext
from dzen_commenter.prompt.config_loader import load_brand_config


class DameoPromptBuilder:
    """РЎР±РѕСЂС‰РёРє С‚РµРєСЃС‚РѕРІРѕРіРѕ РїСЂРѕРјРїС‚Р° РґР»СЏ AI-РјРѕРґРµР»Рё РІ С‚РѕРЅРµ Р±СЂРµРЅРґР° Dameo."""

    def __init__(
        self,
        language: str | None = None,
        config_path: str | None = None,
    ) -> None:
        self._config = load_brand_config(config_path)
        self.language = language if language is not None else self._config.language

    def build(self, context: PromptContext) -> str:
        task = (
            self._config.task_lead
            if context.reply_type == "lead"
            else self._config.task_engage
        )
        blocks = [
            self._config.role,
            self._config.tone_of_voice,
            self._config.anti_rules,
            (
                "РљРѕРЅС‚РµРєСЃС‚:\n"
                f"РўРµРјР° РїСѓР±Р»РёРєР°С†РёРё: {context.publication_title}\n"
                f"Р’РµС‚РєР° РѕР±СЃСѓР¶РґРµРЅРёСЏ: {context.thread_text}"
            ),
            task,
        ]
        return "\n\n".join(blocks)
