import importlib

import pytest

import dzen_commenter.prompt as prompt
from dzen_commenter.contracts import interfaces
from dzen_commenter.prompt import sanitize_model_reply


def test_prompt_public_api_does_not_expose_batch_protocol():
    assert all("batch" not in name.casefold() for name in prompt.__all__)
    assert not hasattr(interfaces, "BatchPromptBuilder")
    assert not hasattr(interfaces, "BatchReplyParser")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("dzen_commenter.prompt.batch")


def test_exact_skip_is_the_only_skip_outcome():
    assert sanitize_model_reply("SKIP") is None


@pytest.mark.parametrize(
    "raw_text",
    ["", "Ответ:", "Тип ответа:", "Ответ: SKIP", "skip", "пропуск", "C01 | SKIP"],
)
def test_protocol_only_or_noncanonical_skip_text_is_not_publishable(raw_text):
    assert sanitize_model_reply(raw_text) == ""


@pytest.mark.parametrize(
    "raw_text",
    ["Ответ: Готовый текст", "Тип ответа: Ответ: Готовый текст"],
)
def test_recognized_legacy_labels_are_removed_before_publication(raw_text):
    assert sanitize_model_reply(raw_text) == "Готовый текст"
