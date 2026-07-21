"""Runtime-конфиг: несекретные настройки в JSON с hot-reload по mtime.

Панель пишет файл, бот перечитывает каждый цикл без рестарта. Битый или
отсутствующий файл никогда не роняет бота — возвращаются последние валидные
значения либо дефолты.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from dzen_commenter.prompt.config_loader import (
    DEFAULT_ANTI_RULES,
    DEFAULT_CTA_MARKER,
    DEFAULT_ROLE,
    DEFAULT_TASK_ENGAGE,
    DEFAULT_TASK_LEAD,
    DEFAULT_TONE_OF_VOICE,
    PromptBrandConfig,
)

logger = logging.getLogger(__name__)


@dataclass
class RuntimeSettings:
    auto_publish: bool = False
    max_comment_age_days: int = 30
    max_reply_length: int = 1000
    developer_telegram_chat_ids: str = ""
    error_email_list: str = ""


@dataclass
class RuntimeConfigData:
    settings: RuntimeSettings
    prompt: PromptBrandConfig


def _default_prompt() -> PromptBrandConfig:
    return PromptBrandConfig(
        role=DEFAULT_ROLE,
        tone_of_voice=DEFAULT_TONE_OF_VOICE,
        anti_rules=DEFAULT_ANTI_RULES,
        task_lead=DEFAULT_TASK_LEAD,
        task_engage=DEFAULT_TASK_ENGAGE,
        cta_marker=DEFAULT_CTA_MARKER,
        language="ru",
    )


def _defaults() -> RuntimeConfigData:
    return RuntimeConfigData(settings=RuntimeSettings(), prompt=_default_prompt())


def _parse_settings(raw: dict) -> RuntimeSettings:
    base = RuntimeSettings()
    return RuntimeSettings(
        auto_publish=bool(raw.get("auto_publish", base.auto_publish)),
        max_comment_age_days=int(raw.get("max_comment_age_days", base.max_comment_age_days)),
        max_reply_length=int(raw.get("max_reply_length", base.max_reply_length)),
        developer_telegram_chat_ids=str(
            raw.get("developer_telegram_chat_ids", base.developer_telegram_chat_ids)
        ),
        error_email_list=str(raw.get("error_email_list", base.error_email_list)),
    )


def _parse_prompt(raw: dict) -> PromptBrandConfig:
    base = _default_prompt()
    return PromptBrandConfig(
        role=str(raw.get("role", base.role)),
        tone_of_voice=str(raw.get("tone_of_voice", base.tone_of_voice)),
        anti_rules=str(raw.get("anti_rules", base.anti_rules)),
        task_lead=str(raw.get("task_lead", base.task_lead)),
        task_engage=str(raw.get("task_engage", base.task_engage)),
        cta_marker=str(raw.get("cta_marker", base.cta_marker)),
        language=str(raw.get("language", base.language)),
    )


def _parse(raw: object) -> RuntimeConfigData:
    if not isinstance(raw, dict):
        raise ValueError("Runtime config must be a JSON object")
    settings_raw = raw.get("settings") or {}
    prompt_raw = raw.get("prompt") or {}
    if not isinstance(settings_raw, dict) or not isinstance(prompt_raw, dict):
        raise ValueError("Runtime config sections must be objects")
    return RuntimeConfigData(
        settings=_parse_settings(settings_raw),
        prompt=_parse_prompt(prompt_raw),
    )


class RuntimeConfig:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._data: RuntimeConfigData | None = None
        self._mtime: float | None = None

    def get(self) -> RuntimeConfigData:
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            if self._data is not None:
                return self._data
            return _defaults()

        if self._data is not None and mtime == self._mtime:
            return self._data

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            data = _parse(raw)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read runtime config %s: %s", self._path, exc)
            if self._data is not None:
                return self._data
            return _defaults()

        self._data = data
        self._mtime = mtime
        return data

    def save(self, data: RuntimeConfigData) -> None:
        payload = {
            "settings": asdict(data.settings),
            "prompt": asdict(data.prompt),
        }
        directory = self._path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

        # Инвалидируем кэш: следующий get() перечитает свежий файл.
        self._data = None
        self._mtime = None


def ensure_runtime_config(path: str, settings: object, brand_config: PromptBrandConfig) -> None:
    if Path(path).exists():
        return

    data = RuntimeConfigData(
        settings=RuntimeSettings(
            auto_publish=settings.AUTO_PUBLISH,
            max_comment_age_days=settings.MAX_COMMENT_AGE_DAYS,
            max_reply_length=settings.MAX_REPLY_LENGTH,
            developer_telegram_chat_ids=settings.DEVELOPER_TELEGRAM_CHAT_ID_LIST,
            error_email_list=settings.EMAIL_FALLBACK_LIST,
        ),
        prompt=brand_config,
    )
    RuntimeConfig(path).save(data)
