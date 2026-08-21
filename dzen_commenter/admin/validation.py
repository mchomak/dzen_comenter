import re
from collections.abc import Mapping
from urllib.parse import urlparse

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
_TELEGRAM_ID_RE = re.compile(r"\d+")
_COOLDOWN_RE = re.compile(r"([1-9]\d*)([mh])")
_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}


def split_csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _value(form: Mapping[str, object], name: str) -> str:
    value = form.get(name, "")
    return str(value).strip()


def _list_values(form: Mapping[str, object], name: str) -> list[str]:
    getlist = getattr(form, "getlist", None)
    if callable(getlist):
        raw = getlist(name)
    else:
        value = form.get(name, [])
        raw = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in raw if str(item).strip()]


def _integer(
    form: Mapping[str, object], name: str, *, minimum: int, maximum: int | None, errors: dict[str, str]
) -> int | None:
    raw = _value(form, name)
    try:
        value = int(raw)
    except ValueError:
        errors[name] = "Введите целое число."
        return None
    if value < minimum or (maximum is not None and value > maximum):
        errors[name] = "Введите число в допустимом диапазоне."
        return None
    return value


def _notification_cooldown(form: Mapping[str, object], errors: dict[str, str]) -> int | None:
    match = _COOLDOWN_RE.fullmatch(_value(form, "error_notification_cooldown"))
    if match is None:
        errors["error_notification_cooldown"] = "Введите положительное целое число с m или h."
        return None
    value = int(match.group(1)) * (60 if match.group(2) == "m" else 3600)
    if value > 24 * 3600:
        errors["error_notification_cooldown"] = "Интервал не может быть больше 24 часов."
        return None
    return value


def _telegram_proxy_url(form: Mapping[str, object], errors: dict[str, str]) -> str:
    value = _value(form, "telegram_proxy_url")
    parsed = urlparse(value)
    if value and (parsed.scheme not in _PROXY_SCHEMES or not parsed.hostname):
        errors["telegram_proxy_url"] = "Введите URL proxy с host и схемой http, https, socks5 или socks5h."
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
    cta_every_n_comments = _integer(
        form,
        "cta_every_n_comments",
        minimum=1,
        maximum=None,
        errors=errors,
    )
    max_comments_per_hour = _integer(
        form,
        "max_comments_per_hour",
        minimum=1,
        maximum=None,
        errors=errors,
    )
    error_notification_cooldown_seconds = _notification_cooldown(form, errors)
    telegram_proxy_url = _telegram_proxy_url(form, errors)

    telegram_ids = _list_values(form, "developer_telegram_chat_ids")
    if any(not _TELEGRAM_ID_RE.fullmatch(item) for item in telegram_ids):
        errors["developer_telegram_chat_ids"] = "Используйте только числовые Telegram ID через запятую."

    emails = _list_values(form, "error_email_list")
    if any(not _EMAIL_RE.fullmatch(email) for email in emails):
        errors["error_email_list"] = "Введите email-адреса через запятую."

    prompt_values = {name: _value(form, name) for name in _PROMPT_FIELDS}
    for name, value in prompt_values.items():
        if not value:
            errors[name] = "Поле обязательно."

    if errors:
        return None, errors

    return (
        RuntimeConfigData(
            settings=RuntimeSettings(
                auto_publish=_value(form, "auto_publish").lower() in {"on", "true", "1", "yes"},
                max_comment_age_days=max_comment_age_days,
                max_reply_length=max_reply_length,
                cta_every_n_comments=cta_every_n_comments,
                max_comments_per_hour=max_comments_per_hour,
                developer_telegram_chat_ids=", ".join(telegram_ids),
                error_email_list=", ".join(emails),
                error_notification_cooldown_seconds=error_notification_cooldown_seconds,
                telegram_proxy_url=telegram_proxy_url,
            ),
            prompt=PromptBrandConfig(**prompt_values),
        ),
        {},
    )
