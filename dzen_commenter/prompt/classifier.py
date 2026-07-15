from dzen_commenter.contracts.interfaces import ReplyType


LEAD_KEYWORDS: tuple[str, ...] = (
    "ремонт",
    "квартир",
    "дом",
    "дизайн",
    "смет",
    "отделк",
    "плитк",
    "ламинат",
    "стоимост",
    "цен",
    "материал",
    "штукатур",
    "санузел",
    "кухн",
    "ванн",
    "обои",
    "потолок",
    "стяжк",
    "планировк",
)


def classify_reply_type(publication_title: str, thread_text: str) -> ReplyType:
    """Classify a comment as lead-generating or conversational."""
    haystack = f"{publication_title}\n{thread_text}".lower()
    for keyword in LEAD_KEYWORDS:
        if keyword in haystack:
            return "lead"
    return "engage"
