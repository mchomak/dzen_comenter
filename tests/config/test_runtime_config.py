import json
import logging
import os
import pathlib

import dzen_commenter.config.runtime_config as rcmod
from dzen_commenter.config.runtime_config import (
    RuntimeConfig,
    RuntimeConfigData,
    RuntimeSettings,
    ensure_runtime_config,
)
from dzen_commenter.prompt import config_loader
from dzen_commenter.prompt.config_loader import load_brand_config


def _valid_data() -> RuntimeConfigData:
    return RuntimeConfigData(
        settings=RuntimeSettings(
            auto_publish=True,
            max_comment_age_days=15,
            max_reply_length=500,
            developer_telegram_chat_ids="1,2",
            error_email_list="a@b.ru",
        ),
        prompt=load_brand_config(None),
    )


def _count_reads(monkeypatch) -> dict:
    calls = {"n": 0}
    orig = pathlib.Path.read_text

    def counting(self, *args, **kwargs):
        calls["n"] += 1
        return orig(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    return calls


def test_get_uses_mtime_cache(tmp_path, monkeypatch):
    path = tmp_path / "rc.json"
    rc = RuntimeConfig(str(path))
    rc.save(_valid_data())

    calls = _count_reads(monkeypatch)

    first = rc.get()
    assert calls["n"] == 1
    assert first.settings.auto_publish is True

    # mtime unchanged -> served from cache, no extra disk read
    rc.get()
    assert calls["n"] == 1

    # change content and bump mtime -> re-read
    path.write_text(json.dumps({"settings": {"max_reply_length": 999}}), encoding="utf-8")
    st = path.stat()
    os.utime(path, (st.st_atime + 10, st.st_mtime + 10))
    third = rc.get()
    assert calls["n"] == 2
    assert third.settings.max_reply_length == 999


def test_broken_json_falls_back_without_raising(tmp_path, caplog):
    path = tmp_path / "rc.json"
    path.write_text("{ not valid json", encoding="utf-8")
    rc = RuntimeConfig(str(path))

    with caplog.at_level(logging.WARNING):
        data = rc.get()

    # defaults returned, no exception
    assert data.prompt.role == config_loader.DEFAULT_ROLE
    assert data.settings.max_reply_length == RuntimeSettings().max_reply_length
    assert any("runtime config" in r.getMessage().lower() for r in caplog.records)


def test_broken_json_returns_last_valid(tmp_path):
    path = tmp_path / "rc.json"
    rc = RuntimeConfig(str(path))
    rc.save(_valid_data())
    good = rc.get()
    assert good.settings.max_reply_length == 500

    # corrupt the file and bump mtime
    path.write_text("broken", encoding="utf-8")
    st = path.stat()
    os.utime(path, (st.st_atime + 10, st.st_mtime + 10))

    data = rc.get()
    assert data.settings.max_reply_length == 500


def test_save_is_atomic_and_valid(tmp_path, monkeypatch):
    path = tmp_path / "rc.json"
    rc = RuntimeConfig(str(path))

    replace_calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replace_calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(rcmod.os, "replace", spy_replace)

    rc.save(_valid_data())

    assert replace_calls, "save must use os.replace for atomic write"
    assert str(replace_calls[0][1]) == str(path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "settings" in raw and "prompt" in raw
    assert raw["settings"]["auto_publish"] is True
    assert raw["prompt"]["role"] == config_loader.DEFAULT_ROLE

    # no leftover temp files in the directory
    leftovers = [p for p in tmp_path.iterdir() if p.name != path.name]
    assert leftovers == []


class _FakeSettings:
    AUTO_PUBLISH = True
    MAX_COMMENT_AGE_DAYS = 7
    MAX_REPLY_LENGTH = 250
    DEVELOPER_TELEGRAM_CHAT_ID_LIST = "10,20"
    EMAIL_FALLBACK_LIST = "dev@x.ru"


def test_ensure_creates_from_settings_and_prompt(tmp_path):
    path = tmp_path / "rc.json"
    brand = load_brand_config(None)
    ensure_runtime_config(str(path), _FakeSettings(), brand)

    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["settings"]["max_comment_age_days"] == 7
    assert raw["settings"]["developer_telegram_chat_ids"] == "10,20"
    assert raw["settings"]["error_email_list"] == "dev@x.ru"
    assert raw["prompt"]["role"] == config_loader.DEFAULT_ROLE


def test_ensure_does_not_overwrite_existing(tmp_path):
    path = tmp_path / "rc.json"
    path.write_text(json.dumps({"settings": {"max_reply_length": 42}}), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    ensure_runtime_config(str(path), _FakeSettings(), load_brand_config(None))

    assert path.read_text(encoding="utf-8") == before


def test_prompt_defaults_match_config_loader(tmp_path):
    rc = RuntimeConfig(str(tmp_path / "missing.json"))
    data = rc.get()
    assert data.prompt.role == config_loader.DEFAULT_ROLE
    assert data.prompt.tone_of_voice == config_loader.DEFAULT_TONE_OF_VOICE
    assert data.prompt.anti_rules == config_loader.DEFAULT_ANTI_RULES
    assert data.prompt.task_lead == config_loader.DEFAULT_TASK_LEAD
    assert data.prompt.task_engage == config_loader.DEFAULT_TASK_ENGAGE
    assert data.prompt.cta_marker == config_loader.DEFAULT_CTA_MARKER
