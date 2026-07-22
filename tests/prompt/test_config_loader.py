import json

import pytest

from dzen_commenter.prompt import PromptBrandConfig, load_brand_config


def _valid_config():
    return {
        "role": "custom role",
        "tone_of_voice": "custom tone",
        "anti_rules": "custom anti rules",
        "task_lead": "custom lead task with custom CTA",
        "task_engage": "custom engage task",
        "cta_marker": "custom CTA",
        "cta_link": "https://custom.example/offer",
        "language": "en",
    }


def test_load_brand_config_missing_path_uses_defaults(tmp_path):
    default = load_brand_config(None)
    missing = load_brand_config(str(tmp_path / "missing.json"))

    assert isinstance(default, PromptBrandConfig)
    assert missing == default


def test_load_brand_config_reads_json_override(tmp_path):
    config_path = tmp_path / "prompt.json"
    config_path.write_text(json.dumps(_valid_config()), encoding="utf-8")

    config = load_brand_config(str(config_path))

    assert config.role == "custom role"
    assert config.tone_of_voice == "custom tone"
    assert config.anti_rules == "custom anti rules"
    assert config.task_lead == "custom lead task with custom CTA"
    assert config.task_engage == "custom engage task"
    assert config.cta_marker == "custom CTA"
    assert config.cta_link == "https://custom.example/offer"
    assert config.language == "en"


def test_load_brand_config_defaults_optional_language(tmp_path):
    raw = _valid_config()
    raw.pop("language")
    config_path = tmp_path / "prompt.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    assert load_brand_config(str(config_path)).language == "ru"


def test_load_brand_config_invalid_json_raises_value_error(tmp_path):
    config_path = tmp_path / "prompt.json"
    config_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid prompt config JSON"):
        load_brand_config(str(config_path))


def test_load_brand_config_missing_required_key_raises_value_error(tmp_path):
    raw = _valid_config()
    raw.pop("role")
    config_path = tmp_path / "prompt.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required keys: role"):
        load_brand_config(str(config_path))


def test_load_brand_config_missing_cta_link_raises_value_error(tmp_path):
    raw = _valid_config()
    raw.pop("cta_link")
    config_path = tmp_path / "prompt.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="missing required keys.*cta_link"):
        load_brand_config(str(config_path))
