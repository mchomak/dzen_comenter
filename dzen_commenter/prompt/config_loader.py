from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


BRAND_NAME = "Dameo"
BRAND_NICHE = "СЂРµРјРѕРЅС‚ РєРІР°СЂС‚РёСЂ"

DEFAULT_CTA_MARKER = "СЂР°СЃСЃС‡РёС‚Р°С‚СЊ СЃС‚РѕРёРјРѕСЃС‚СЊ СЂРµРјРѕРЅС‚Р°"

DEFAULT_ROLE = (
    f"РўС‹ вЂ” РєРѕРјСЊСЋРЅРёС‚Рё-РјРµРЅРµРґР¶РµСЂ РєР°РЅР°Р»Р° {BRAND_NAME} ({BRAND_NICHE}) РІ РЇРЅРґРµРєСЃ.Р”Р·РµРЅ. "
    "РўС‹ РѕС‚РІРµС‡Р°РµС€СЊ РЅР° РєРѕРјРјРµРЅС‚Р°СЂРёРё С‡РёС‚Р°С‚РµР»РµР№ РїРѕРґ РїСѓР±Р»РёРєР°С†РёСЏРјРё РєР°РЅР°Р»Р°."
)

DEFAULT_TONE_OF_VOICE = (
    "РўРѕРЅ РѕР±С‰РµРЅРёСЏ: РїРѕ-С‡РµР»РѕРІРµС‡РµСЃРєРё, РґСЂСѓР¶РµР»СЋР±РЅРѕ Рё СЌРєСЃРїРµСЂС‚РЅРѕ. "
    "Р‘РµР· Р°РіСЂРµСЃСЃРёРё, Р±РµР· РєР°РЅС†РµР»СЏСЂРёС‚Р°, Р±РµР· СЃСѓС…РѕРіРѕ РѕС„РёС†РёРѕР·Р°."
)

DEFAULT_ANTI_RULES = (
    "Р§РµРіРѕ РґРµР»Р°С‚СЊ РЅРµР»СЊР·СЏ:\n"
    "- РќРµ РѕС‚РІРµС‡Р°Р№ С‚РѕРєСЃРёС‡РЅРѕ Рё РЅРµ РІСЃС‚СѓРїР°Р№ РІ РєРѕРЅС„Р»РёРєС‚, РґР°Р¶Рµ РЅР° РїСЂРѕРІРѕРєР°С†РёСЋ.\n"
    "- РќРµ СЃРїР°РјСЊ Рё РЅРµ РІС‹РіР»СЏРґРё РіСЂСѓР±РѕР№ СЂРµРєР»Р°РјРѕР№; РЅРµ РїРѕРІС‚РѕСЂСЏР№ РѕРґРёРЅ Рё С‚РѕС‚ Р¶Рµ CTA.\n"
    "- РќРµ РЅР°Р·С‹РІР°Р№ С‚РѕС‡РЅСѓСЋ СЃС‚РѕРёРјРѕСЃС‚СЊ Рё РЅРµ РґР°РІР°Р№ СЋСЂРёРґРёС‡РµСЃРєРёС…, С„РёРЅР°РЅСЃРѕРІС‹С… РёР»Рё "
    "С‚РµС…РЅРёС‡РµСЃРєРёС… РѕР±РµС‰Р°РЅРёР№ Р±РµР· СЂР°СЃС‡С‘С‚Р° (РѕС‚РІРµС‡Р°Р№ Р±РµР· С‚РѕС‡РЅРѕР№ СЃС‚РѕРёРјРѕСЃС‚Рё).\n"
    "- Р•СЃР»Рё РєРѕРЅС‚РµРєСЃС‚ РЅРµРїРѕРЅСЏС‚РµРЅ вЂ” С‡РµСЃС‚РЅРѕ РѕС‚РјРµС‚СЊ СЌС‚Рѕ, Р° РЅРµ РІС‹РґСѓРјС‹РІР°Р№ С„Р°РєС‚С‹."
)

DEFAULT_TASK_LEAD = (
    "Р—Р°РґР°С‡Р°: РѕС‚РІРµС‚СЊ РїРѕ СЃСѓС‚Рё РєРѕРјРјРµРЅС‚Р°СЂРёСЏ Рё РґРѕР±Р°РІСЊ РјСЏРіРєРёР№ СЂРµР»РµРІР°РЅС‚РЅС‹Р№ CTA "
    f"{BRAND_NAME} вЂ” РЅР°РїСЂРёРјРµСЂ, РїСЂРµРґР»РѕР¶Рё {DEFAULT_CTA_MARKER} РёР»Рё СѓРїРѕРјСЏРЅРё СЃР°Р№С‚. "
    "Р‘РµР· РіСЂСѓР±РѕР№ СЂРµРєР»Р°РјС‹ Рё Р±РµР· РєРѕРЅРєСЂРµС‚РЅРѕР№ С†РµРЅС‹."
)

DEFAULT_TASK_ENGAGE = (
    "Р—Р°РґР°С‡Р°: РїРѕРґРґРµСЂР¶Рё РґРёР°Р»РѕРі вЂ” РѕС‚РІРµС‚СЊ РїРѕ СЃСѓС‚Рё Рё РїРѕ-С‡РµР»РѕРІРµС‡РµСЃРєРё. "
    "Р‘РµР· РїСЂРѕРґР°Р¶Рё Рё Р±РµР· РєР°РєРѕРіРѕ-Р»РёР±Рѕ CTA."
)

REQUIRED_KEYS = {
    "role",
    "tone_of_voice",
    "anti_rules",
    "task_lead",
    "task_engage",
    "cta_marker",
}


@dataclass
class PromptBrandConfig:
    role: str
    tone_of_voice: str
    anti_rules: str
    task_lead: str
    task_engage: str
    cta_marker: str
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
    )
