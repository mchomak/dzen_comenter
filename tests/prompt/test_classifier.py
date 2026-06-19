from dzen_commenter.prompt import classify_reply_type

# Размеченные фикстуры: (publication_title, thread_text, expected_label).
# >=3 lead (ремонтная тематика) + >=3 engage (оффтоп/спор/шутка) + граничные.
FIXTURES = [
    # --- lead: ремонтная тематика ---
    ("Как выбрать плитку для ванной", "Подскажите, сколько стоит уложить плитку?", "lead"),
    ("Дизайн-проект кухни своими руками", "Делаем ремонт кухни, нужен совет по отделке", "lead"),
    ("Смета на ремонт квартиры", "Какие материалы лучше для стяжки пола?", "lead"),
    ("Ремонт санузла под ключ", "А штукатурка нужна или сразу плитку?", "lead"),
    # граничный lead: тема нейтральная, но ремонтный сигнал в ветке
    ("Обсуждаем интерьеры", "Сколько примерно стоит ламинат на 30 квадратов?", "lead"),
    # --- engage: оффтоп / спор / шутка ---
    ("Лучшие фильмы 2025 года", "Кто что смотрел в кино на выходных?", "engage"),
    ("Спор о политике", "Вы вообще читали статью или просто спорите?", "engage"),
    ("Анекдот дня", "Ха-ха, отличная шутка, поднял настроение!", "engage"),
    ("Прогноз погоды на лето", "Жара невыносимая, когда уже дожди?", "engage"),
    # граничный engage: про дом, но без ремонтных ключевиков
    ("Как успокоить соседскую собаку", "Лает по ночам, спать невозможно", "engage"),
]


def test_classifier_accuracy_is_100_percent():
    correct = sum(
        1
        for title, thread, expected in FIXTURES
        if classify_reply_type(title, thread) == expected
    )
    total = len(FIXTURES)
    accuracy = correct / total
    print(f"classifier accuracy: {correct}/{total} = {accuracy:.0%} (threshold 100%)")
    assert total >= 8
    assert sum(1 for *_, e in FIXTURES if e == "lead") >= 3
    assert sum(1 for *_, e in FIXTURES if e == "engage") >= 3
    assert correct == total, f"accuracy {accuracy:.0%}, expected 100%"


def test_returns_literal_values():
    for title, thread, _ in FIXTURES:
        assert classify_reply_type(title, thread) in ("lead", "engage")
