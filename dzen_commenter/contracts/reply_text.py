from __future__ import annotations

import re


_TYPE_PREFIX = re.compile(
    r"^\s*(?:\u0442\u0438\u043f(?:\s+\u043e\u0442\u0432\u0435\u0442\u0430)?|"
    r"\u0441\u0442\u0430\u0442\u0443\u0441|type|response\s+type|status)\s*:\s*"
    r"(?P<value>.*)$",
    re.IGNORECASE,
)
_REPLY_LABEL = re.compile(
    r"\s+(?:\u0442\u0435\u043a\u0441\u0442\s+\u043e\u0442\u0432\u0435\u0442\u0430|"
    r"\u043e\u0442\u0432\u0435\u0442|answer|reply)\s*:\s*",
    re.IGNORECASE,
)
_LEADING_REPLY_LABEL = re.compile(
    r"^\s*(?:\u0442\u0435\u043a\u0441\u0442\s+\u043e\u0442\u0432\u0435\u0442\u0430|"
    r"\u043e\u0442\u0432\u0435\u0442|answer|reply)\s*:\s*",
    re.IGNORECASE,
)
_SKIP_VALUES = {"skip", "\u043f\u0440\u043e\u043f\u0443\u0441\u043a"}
_SKIP_DECLARATION = re.compile(
    r"^(?:skip|\u043f\u0440\u043e\u043f\u0443\u0441\u043a)\b", re.IGNORECASE
)


def sanitize_model_reply(raw_text: str) -> str | None:
    """Return publishable model text, or ``None`` for an explicit skip outcome.

    Older prompts asked the model for a type and an answer. Treat those leading
    transport labels as metadata so they cannot leak into a public reply.
    """
    text = raw_text.strip()
    if not text:
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    type_match = _TYPE_PREFIX.match(lines[0])
    if type_match is not None:
        value = type_match.group("value").strip()
        reply_label = _REPLY_LABEL.search(value)
        declared_kind = value[: reply_label.start()].strip() if reply_label else value
        if _SKIP_DECLARATION.match(declared_kind):
            return None
        if reply_label is not None:
            lines[0] = value[reply_label.end() :].strip()
        else:
            lines.pop(0)
        text = "\n".join(lines).strip()

    text = _LEADING_REPLY_LABEL.sub("", text, count=1).strip()
    if text.casefold() in _SKIP_VALUES:
        return None
    return text
