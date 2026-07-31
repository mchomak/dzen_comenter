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

CTA_CANDIDATE_TITLE_KEYWORDS: tuple[str, ...] = (
    "ремонт",
    "дизайн",
    "интерьер",
    "отделк",
    "планировк",
)


def is_cta_candidate_title(publication_title: str) -> bool:
    """Return whether an article title belongs to the repair/design CTA topic."""
    title = publication_title.lower()
    return any(keyword in title for keyword in CTA_CANDIDATE_TITLE_KEYWORDS)


def classify_reply_type(publication_title: str, thread_text: str) -> ReplyType:
    """Classify a comment as lead-generating or conversational."""
    haystack = f"{publication_title}\n{thread_text}".lower()
    for keyword in LEAD_KEYWORDS:
        if keyword in haystack:
            return "lead"
    return "engage"
