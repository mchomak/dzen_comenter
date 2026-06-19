from dzen_commenter.contracts.interfaces import ReplyType

# Лид-ключевики (рус., нижний регистр). Матчинг регистронезависимый,
# по подстроке в publication_title + thread_text. Корни слов, чтобы
# покрыть словоформы ("ремонт", "ремонта", "ремонтируем").
LEAD_KEYWORDS: tuple[str, ...] = (
    "ремонт",
    "квартир",
    "дизайн",
    "смет",
    "отделк",
    "плитк",
    "ламинат",
    "стоимост",
    "цена",
    "цены",
    "материал",
    "штукатур",
    "санузел",
    "кухн",
    "ванн",
    "обои",
    "потолок",
    "стяжк",
)


def classify_reply_type(publication_title: str, thread_text: str) -> ReplyType:
    """Эвристика лидген vs вовлечение.

    Если тема (заголовок публикации + текст ветки) затрагивает ремонт/
    квартиру/дизайн/смету/материалы/стоимость — это лидген (`"lead"`).
    Иначе (общая дискуссия, спор, шутка, оффтоп) — вовлечение (`"engage"`).
    """
    haystack = f"{publication_title}\n{thread_text}".lower()
    for keyword in LEAD_KEYWORDS:
        if keyword in haystack:
            return "lead"
    return "engage"
