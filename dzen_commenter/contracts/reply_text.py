from __future__ import annotations

import re


_TYPE_PREFIX = re.compile(
    r"^\s*(?:\u0442\u0438\u043f(?:\s+\u043e\u0442\u0432\u0435\u0442\u0430)?|"
    r"\u0441\u0442\u0430\u0442\u0443\u0441|type|response\s+type|status)\s*:\s*",
    re.IGNORECASE,
)
_REPLY_PREFIX = re.compile(
    r"^\s*(?:\u0442\u0435\u043a\u0441\u0442\s+\u043e\u0442\u0432\u0435\u0442\u0430|"
    r"\u043e\u0442\u0432\u0435\u0442|answer|reply)\s*:\s*",
    re.IGNORECASE,
)
_INLINE_REPLY_LABEL = re.compile(
    r"\s+(?:\u0442\u0435\u043a\u0441\u0442\s+\u043e\u0442\u0432\u0435\u0442\u0430|"
    r"\u043e\u0442\u0432\u0435\u0442|answer|reply)\s*:\s*",
    re.IGNORECASE,
)
_PROTOCOL_ONLY = {
    "skip",
    "пропуск",
    "\u043e\u0442\u0432\u0435\u0442",
    "\u0442\u0435\u043a\u0441\u0442 \u043e\u0442\u0432\u0435\u0442\u0430",
    "\u0442\u0438\u043f \u043e\u0442\u0432\u0435\u0442\u0430",
    "answer",
    "reply",
}
_BATCH_CONTROL = re.compile(r"^\s*C\d+\s*(?:\||\t)\s*SKIP\s*$", re.IGNORECASE)


def sanitize_model_reply(raw_text: str) -> str | None:
    """Return text safe to publish, ``None`` only for the exact ``SKIP`` control."""
    text = raw_text.strip()
    if text == "SKIP":
        return None
    if not text:
        return ""

    while text:
        type_match = _TYPE_PREFIX.match(text)
        if type_match is not None:
            remainder = text[type_match.end() :].strip()
            reply_label = _INLINE_REPLY_LABEL.search(remainder)
            if reply_label is not None:
                text = remainder[reply_label.end() :].strip()
                continue
            if "\n" not in remainder:
                text = remainder
                continue
            _, text = remainder.split("\n", 1)
            text = text.strip()
            continue

        reply_match = _REPLY_PREFIX.match(text)
        if reply_match is not None:
            text = text[reply_match.end() :].strip()
            continue
        break

    first_line = text.splitlines()[0].strip().casefold() if text else ""
    if not text or first_line in _PROTOCOL_ONLY or _BATCH_CONTROL.fullmatch(text):
        return ""
    return text
