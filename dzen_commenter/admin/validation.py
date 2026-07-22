import re
from collections.abc import Mapping

from dzen_commenter.config.runtime_config import RuntimeConfigData, RuntimeSettings
from dzen_commenter.prompt.config_loader import PromptBrandConfig

MAX_COMMENT_AGE_DAYS = 3650
MAX_REPLY_LENGTH = 10000

_PROMPT_FIELDS = (
    "role",
    "tone_of_voice",
    "anti_rules",
    "task_lead",
    "task_engage",
    "cta_marker",
    "cta_link",
    "language",
)
_EMAIL_RE = re.compile(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+")
_TELEGRAM_IDS_RE = re.compile(r"\d+(?:\s*,\s*\d+)*")


def _value(form: Mapping[str, object], name: str) -> str:
    value = form.get(name, "")
    return str(value).strip()


def _integer(
    form: Mapping[str, object], name: str, *, minimum: int, maximum: int, errors: dict[str, str]
) -> int | None:
    raw = _value(form, name)
    try:
        value = int(raw)
    except ValueError:
        errors[name] = "Введите целое число."
        return None
    if value < minimum or value > maximum:
        errors[name] = "Введите число в допустимом диапазоне."
        return None
    return value


def validate_settings_form(
    form: Mapping[str, object],
) -> tuple[RuntimeConfigData | None, dict[str, str]]:
    errors: dict[str, str] = {}
    max_comment_age_days = _integer(
        form,
        "max_comment_age_days",
        minimum=0,
        maximum=MAX_COMMENT_AGE_DAYS,
        errors=errors,
    )
    max_reply_length = _integer(
        form,
        "max_reply_length",
        minimum=1,
        maximum=MAX_REPLY_LENGTH,
        errors=errors,
    )

    telegram_ids = _value(form, "developer_telegram_chat_ids")
    if telegram_ids and not _TELEGRAM_IDS_RE.fullmatch(telegram_ids):
        errors["developer_telegram_chat_ids"] = "Используйте только числовые Telegram ID через запятую."

    email_list = _value(form, "error_email_list")
    emails = [email.strip() for email in email_list.split(",")] if email_list else []
    if any(not _EMAIL_RE.fullmatch(email) for email in emails):
        errors["error_email_list"] = "Введите email-адреса через запятую."

    prompt_values = {name: _value(form, name) for name in _PROMPT_FIELDS}
    for name, value in prompt_values.items():
        if not value:
            errors[name] = "Поле обязательно."

    cta_link = prompt_values["cta_link"]
    if cta_link and not cta_link.startswith(("http://", "https://")):
        errors["cta_link"] = "Введите ссылку (http:// или https://)."

    if errors:
        return None, errors

    return (
        RuntimeConfigData(
            settings=RuntimeSettings(
                auto_publish=_value(form, "auto_publish").lower() in {"on", "true", "1", "yes"},
                max_comment_age_days=max_comment_age_days,
                max_reply_length=max_reply_length,
                developer_telegram_chat_ids=telegram_ids,
                error_email_list=", ".join(emails),
            ),
            prompt=PromptBrandConfig(**prompt_values),
        ),
        {},
    )
