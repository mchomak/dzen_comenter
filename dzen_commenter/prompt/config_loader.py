from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ROLE = (
    "Ты — комьюнити-менеджер бренда DOMEO (Dameo) в Яндекс.Дзен. "
    "DOMEO делает ремонт квартир под ключ. Ты отвечаешь на комментарии "
    "читателей от имени бренда: по-человечески, тепло и коротко."
)
DEFAULT_TONE_OF_VOICE = (
    "Тон: дружелюбный сосед-профи, а не отдел продаж. Пиши на «вы», живо, "
    "спокойно и уважительно; допустимы лёгкий юмор и один уместный эмодзи."
)
DEFAULT_ANTI_RULES = (
    "Запрещено:\n"
    "- грубить, спорить на эмоциях, отвечать токсично или свысока;\n"
    "- звучать как реклама или спам и повторять один шаблон;\n"
    "- называть точную стоимость или давать юридические, финансовые и технические обещания;\n"
    "- выдумывать факты, если смысл комментария непонятен. В таком случае верни тип «пропуск»."
)
DEFAULT_TASK_LEAD = (
    "Тип: лидогенерационный. Ответь по сути комментария. Мягкий CTA допустим "
    "только если он органично продолжает разговор: «если интересно, можем бесплатно "
    "прикинуть смету под ваш бюджет — вот ссылка: {cta_link}». CTA — максимум один "
    "и не в каждом ответе, ссылку добавляй только вместе с самим CTA."
)
DEFAULT_TASK_ENGAGE = (
    "Тип: вовлекающий / нейтральный. Поддержи диалог и ответь по сути, без продаж "
    "и без CTA. При критике спокойно признай право человека на своё мнение."
)
DEFAULT_CTA_MARKER = "бесплатно прикинуть смету"
DEFAULT_CTA_LINK = "https://l.domeo.ru/remont"

REQUIRED_KEYS = {
    "role",
    "tone_of_voice",
    "anti_rules",
    "task_lead",
    "task_engage",
    "cta_marker",
    "cta_link",
}


@dataclass
class PromptBrandConfig:
    role: str
    tone_of_voice: str
    anti_rules: str
    task_lead: str
    task_engage: str
    cta_marker: str
    cta_link: str
    language: str = "ru"


def load_brand_config(path: str | None) -> PromptBrandConfig:
    if path is None:
        return _default_config()

    config_path = Path(path)
    if not config_path.exists():
        return _default_config()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid prompt config JSON: {config_path}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Prompt config must be a JSON object")

    missing = REQUIRED_KEYS - raw.keys()
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise ValueError(f"Prompt config missing required keys: {missing_keys}")

    values = {key: raw[key] for key in REQUIRED_KEYS}
    values["language"] = raw.get("language", "ru")
    for key, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"Prompt config key must be a string: {key}")

    return PromptBrandConfig(
        role=values["role"],
        tone_of_voice=values["tone_of_voice"],
        anti_rules=values["anti_rules"],
        task_lead=values["task_lead"],
        task_engage=values["task_engage"],
        cta_marker=values["cta_marker"],
        cta_link=values["cta_link"],
        language=values["language"],
    )


def _default_config() -> PromptBrandConfig:
    return PromptBrandConfig(
        role=DEFAULT_ROLE,
        tone_of_voice=DEFAULT_TONE_OF_VOICE,
        anti_rules=DEFAULT_ANTI_RULES,
        task_lead=DEFAULT_TASK_LEAD,
        task_engage=DEFAULT_TASK_ENGAGE,
        cta_marker=DEFAULT_CTA_MARKER,
        cta_link=DEFAULT_CTA_LINK,
    )
