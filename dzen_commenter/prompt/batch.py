from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from dzen_commenter.contracts.enums import BatchOutcomeKind
from dzen_commenter.contracts.exceptions import BatchParseError
from dzen_commenter.contracts.models import BatchItem, BatchOutcome
from dzen_commenter.prompt.classifier import classify_reply_type
from dzen_commenter.prompt.config_loader import PromptBrandConfig, load_brand_config

_OUTCOME_KIND_ALIASES = {
    "reply": "REPLY",
    "answer": "REPLY",
    "ответ": "REPLY",
    "skip": "SKIP",
    "пропуск": "SKIP",
}


class DameoBatchPromptBuilder:
    """Build one-article prompts for a strictly ordered batch of comments."""

    def __init__(
        self,
        config_path: str | None = None,
        config_provider: Callable[[], PromptBrandConfig] | None = None,
    ) -> None:
        self._config_provider = config_provider
        self._config = None if config_provider is not None else load_brand_config(config_path)

    def build_batch(
        self, items: Sequence[BatchItem], *, article_text: str
    ) -> str:
        if not items:
            raise ValueError("A batch prompt requires at least one item")
        post_url = items[0].post_url
        if any(item.post_url != post_url for item in items):
            raise ValueError("A batch prompt cannot mix article URLs")
        if any(item.item_no != position for position, item in enumerate(items, start=1)):
            raise ValueError("Batch item order must be contiguous and stable")
        config = self._config_provider() if self._config_provider else self._config
        assert config is not None
        cards = "\n\n".join(
            self._build_card(item, position, config)
            for position, item in enumerate(items, start=1)
        )
        article_block = (
            "КОНТЕКСТ СТАТЬИ:\n"
            f"Ссылка на статью: {post_url}\n"
            "Перед ответом внимательно прочитай текст статьи и отвечай с опорой на него.\n"
            f"Текст статьи:\n{article_text}"
            if article_text
            else "КОНТЕКСТ СТАТЬИ:\nТекст статьи недоступен. Не выдумывай факты."
        )
        protocol = (
            "ФОРМАТ ОТВЕТА:\n"
            "Верни ровно одну строку без пояснений: только текст ответа или SKIP.\n"
            "Для запрещённой, непонятной или небезопасной темы используй SKIP. "
            "В тексте ответа запрещены символы |, tab и перевод строки."
            if len(items) == 1
            else "ФОРМАТ ОТВЕТА:\n"
            f"Верни ровно {len(items)} строк в том же порядке, без пояснений.\n"
            "Каждая строка: Cnn | текст ответа или Cnn | SKIP.\n"
            "Для запрещённой, непонятной или небезопасной темы используй SKIP. "
            "В тексте ответа запрещены символы |, tab и перевод строки."
        )
        return "\n\n".join(
            (
                config.role,
                config.tone_of_voice,
                config.anti_rules,
                article_block,
                protocol,
                "КАРТОЧКИ КОММЕНТАРИЕВ:\n" + cards,
            )
        ).replace("{cta_link}", config.cta_link)

    @staticmethod
    def _build_card(item: BatchItem, position: int, config: PromptBrandConfig) -> str:
        reply_type = classify_reply_type(
            item.publication_title, f"{item.thread_text}\n{item.comment_text}"
        )
        task = config.task_lead if reply_type == "lead" else config.task_engage
        thread_context = item.thread_text or "нет предыдущих сообщений"
        return (
            f"C{position:02d}\n"
            f"Тема статьи: {item.publication_title}\n"
            f"Ветка комментариев (предыдущие сообщения): {thread_context}\n"
            f"Автор: {item.author}\n"
            f"Комментарий: {item.comment_text}\n"
            f"{task}"
        )


def parse_batch(
    raw: str, items: Sequence[BatchItem], max_length: int
) -> tuple[BatchOutcome, ...]:
    """Return all ordered outcomes or raise without returning a partial batch."""
    if not items:
        raise BatchParseError("Batch has no expected items")
    if max_length < 1:
        raise BatchParseError("Maximum reply length must be positive")

    lines = _strip_optional_code_fence(raw)
    if len(items) == 1:
        return (_parse_single_item_output(lines, items[0], max_length),)
    if len(lines) != len(items):
        raise BatchParseError("Batch line count does not match claimed items")
    return _parse_labeled_outcomes(lines, items, max_length)


def _strip_optional_code_fence(raw: str) -> list[str]:
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) >= 2 and lines[0].strip().startswith("```"):
        if lines[-1].strip() != "```":
            raise BatchParseError("Batch code fence is not closed")
        return lines[1:-1]
    return lines


def _split_batch_row(line: str) -> tuple[str, str, str, bool]:
    """Accept the two-column protocol and strictly delimited legacy rows."""
    if line.count("\t") == 2:
        label, kind, text = line.split("\t")
        return label, kind, text, True

    if line.count("<TAB>") == 2:
        label, kind, text = line.split("<TAB>")
        return label, kind, text, True

    if line.count("\\t") == 2:
        label, kind, text = line.split("\\t")
        return label, kind, text, True

    if line.count("|") == 1:
        label, text = (part.strip() for part in line.split("|"))
        return label, "", text, False

    if line.count("|") == 2:
        label, kind, text = (part.strip() for part in line.split("|", maxsplit=2))
        return label, kind, text, True

    raise BatchParseError("Batch row has an invalid column separator count")


def _looks_like_labeled_batch_row(line: str) -> bool:
    return bool(re.match(r"^C\d+\s*(?:\||\t|<TAB>|\\t)", line))


def _parse_single_outcome(
    line: str, item: BatchItem, max_length: int
) -> BatchOutcome:
    if line == "SKIP":
        return BatchOutcome(item.comment_id, item.item_no, BatchOutcomeKind.SKIP)
    return _reply_outcome(item, line, max_length)


def _parse_single_item_output(
    lines: list[str], item: BatchItem, max_length: int
) -> BatchOutcome:
    if not lines:
        raise BatchParseError("Batch line count does not match claimed items")
    if len(lines) == 1:
        line = lines[0]
        if not _looks_like_labeled_batch_row(line):
            return _parse_single_outcome(line, item, max_length)
        return _parse_labeled_outcomes((line,), (item,), max_length)[0]

    if any(re.match(r"^C\d+\b", line) for line in lines):
        raise BatchParseError("Batch line count does not match claimed items")
    if lines[0].strip().casefold() in {"ответ:", "текст ответа:"}:
        lines = lines[1:]
    return _parse_single_outcome(" ".join(line.strip() for line in lines), item, max_length)


def _parse_labeled_outcomes(
    lines: Sequence[str], items: Sequence[BatchItem], max_length: int
) -> tuple[BatchOutcome, ...]:
    outcomes: list[BatchOutcome] = []
    for position, (line, item) in enumerate(zip(lines, items, strict=True), start=1):
        if item.item_no != position:
            raise BatchParseError("Claimed item order is invalid")
        label, kind, text, is_legacy = _split_batch_row(line)
        expected_label = f"C{position:02d}"
        if label != expected_label:
            raise BatchParseError("Batch item IDs do not match the claimed order")
        normalized_kind = _normalize_outcome_kind(kind)
        if not is_legacy and text == "SKIP":
            outcomes.append(
                BatchOutcome(item.comment_id, item.item_no, BatchOutcomeKind.SKIP)
            )
            continue
        if is_legacy and normalized_kind == "SKIP":
            if text:
                raise BatchParseError("SKIP rows must have an empty text column")
            outcomes.append(
                BatchOutcome(item.comment_id, item.item_no, BatchOutcomeKind.SKIP)
            )
            continue
        if is_legacy and normalized_kind != "REPLY" and not text.strip():
            raise BatchParseError("Batch row has an unknown outcome kind")
        outcomes.append(_reply_outcome(item, text, max_length))
    return tuple(outcomes)


def _reply_outcome(item: BatchItem, text: str, max_length: int) -> BatchOutcome:
    if not text.strip():
        raise BatchParseError("REPLY rows must have non-empty text")
    if "|" in text or "\t" in text or "\n" in text or "\r" in text:
        raise BatchParseError("REPLY text must not contain pipes, tabs or newlines")

    formatted_text = _format_reply_text(text, item.author)
    if len(formatted_text) > max_length:
        raise BatchParseError("REPLY text exceeds the maximum length after prefix")
    return BatchOutcome(
        item.comment_id,
        item.item_no,
        BatchOutcomeKind.REPLY,
        text=formatted_text,
    )


def _normalize_outcome_kind(kind: str) -> str:
    return _OUTCOME_KIND_ALIASES.get(kind.strip().casefold(), kind)


def _format_reply_text(text: str, author: str) -> str:
    for index, character in enumerate(text):
        if character.isalpha():
            text = text[:index] + character.lower() + text[index + 1 :]
            break
    author_prefix = f"{author.strip()}, " if author.strip() else ""
    return f"{author_prefix}{text}"
