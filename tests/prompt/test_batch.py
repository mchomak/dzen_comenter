import pytest

from dzen_commenter.contracts.enums import BatchOutcomeKind
from dzen_commenter.contracts.models import BatchItem
from dzen_commenter.prompt import BatchParseError, DameoBatchPromptBuilder, parse_batch


def make_item(item_no: int) -> BatchItem:
    return BatchItem(
        batch_id=10,
        comment_id=100 + item_no,
        item_no=item_no,
        post_url="https://dzen.ru/a/article",
        publication_title="Как сделать ремонт",
        thread_text=f"ветка {item_no}",
        author=f"Автор {item_no}",
        comment_text=f"комментарий {item_no}",
    )


@pytest.mark.parametrize("size", [1, 3, 5])
def test_build_batch_shares_article_context_and_labels_every_card(size):
    prompt = DameoBatchPromptBuilder().build_batch(
        tuple(make_item(item_no) for item_no in range(1, size + 1)),
        article_text="Уникальный текст статьи",
    )

    assert prompt.count("Уникальный текст статьи") == 1
    assert [f"C{item_no:02d}" in prompt for item_no in range(1, size + 1)] == [
        True
    ] * size
    assert "C06" not in prompt
    assert "Cnn | текст ответа или Cnn | SKIP" in prompt
    assert (
        "REPLY"
        not in prompt.split("ФОРМАТ ОТВЕТА:", 1)[1].split("КАРТОЧКИ КОММЕНТАРИЕВ:", 1)[
            0
        ]
    )


def test_build_batch_rejects_items_from_different_articles():
    first = make_item(1)
    second = make_item(2)
    second = BatchItem(
        **{**second.__dict__, "post_url": "https://dzen.ru/a/another-article"}
    )

    with pytest.raises(ValueError, match="article URLs"):
        DameoBatchPromptBuilder().build_batch((first, second), article_text="text")


@pytest.mark.parametrize("size", [1, 3, 5])
def test_parse_batch_returns_every_outcome_in_claimed_item_order(size):
    items = tuple(make_item(item_no) for item_no in range(1, size + 1))
    raw = "\n".join(
        f"C{item_no:02d}\t{'SKIP' if item_no == 2 else 'REPLY'}\t"
        f"{'ответ ' + str(item_no) if item_no != 2 else ''}"
        for item_no in range(1, size + 1)
    )

    outcomes = parse_batch(raw, items, max_length=100)

    assert [(outcome.comment_id, outcome.item_no, outcome.kind) for outcome in outcomes] == [
        (
            item.comment_id,
            item.item_no,
            BatchOutcomeKind.SKIP if item.item_no == 2 else BatchOutcomeKind.REPLY,
        )
        for item in items
    ]
    assert outcomes[0].text == "Автор 1, ответ 1"
    if size > 1:
        assert outcomes[1].text == ""


def test_parse_batch_accepts_two_column_protocol():
    item = make_item(1)

    outcomes = parse_batch(
        "C01 | ответ\nC02 | SKIP", (item, make_item(2)), max_length=100
    )

    assert outcomes[0].kind is BatchOutcomeKind.REPLY
    assert outcomes[0].text == "Автор 1, ответ"
    assert outcomes[1].kind is BatchOutcomeKind.SKIP


def test_parse_batch_accepts_legacy_model_skip_without_reply_type_prediction():
    item = make_item(1)

    outcome = parse_batch("C01\tSKIP\t", (item,), max_length=100)

    assert outcome[0].kind is BatchOutcomeKind.SKIP


@pytest.mark.parametrize(
    ("raw", "expected_kind"),
    [
        ("C01\t reply \tответ", BatchOutcomeKind.REPLY),
        ("C01\tAnSwEr\tответ", BatchOutcomeKind.REPLY),
        ("C01\tОТВЕТ\tответ", BatchOutcomeKind.REPLY),
        ("C01\t skip \t", BatchOutcomeKind.SKIP),
        ("C01\tПРОПУСК\t", BatchOutcomeKind.SKIP),
    ],
)
def test_parse_batch_normalizes_common_outcome_kind_aliases(raw, expected_kind):
    outcome = parse_batch(raw, (make_item(1),), max_length=100)

    assert outcome[0].kind is expected_kind


def test_parse_batch_treats_unknown_legacy_kind_with_text_as_reply():
    outcome = parse_batch("C01\tСФОРМИРОВАНО\tОтвет", (make_item(1),), max_length=100)

    assert outcome[0].kind is BatchOutcomeKind.REPLY
    assert outcome[0].text == "Автор 1, ответ"


@pytest.mark.parametrize(
    "raw",
    [
        "C01 | REPLY | ответ\nC02 | SKIP | ",
        "```\nC01 | REPLY | ответ\nC02 | SKIP |\n```",
        "C01<TAB>REPLY<TAB>ответ\nC02<TAB>SKIP<TAB>",
        "C01\\tREPLY\\tответ\nC02\\tSKIP\\t",
    ],
)
def test_parse_batch_accepts_common_machine_output_delimiters(raw):
    outcomes = parse_batch(raw, (make_item(1), make_item(2)), max_length=100)

    assert [outcome.kind for outcome in outcomes] == [
        BatchOutcomeKind.REPLY,
        BatchOutcomeKind.SKIP,
    ]
    assert outcomes[0].text == "Автор 1, ответ"


def test_parse_batch_ignores_blank_separator_lines_in_optional_fence():
    outcomes = parse_batch(
        "```\n\nC01 | ответ\n \nC02 | SKIP\n\n```",
        (make_item(1), make_item(2)),
        max_length=100,
    )

    assert [outcome.kind for outcome in outcomes] == [
        BatchOutcomeKind.REPLY,
        BatchOutcomeKind.SKIP,
    ]


def test_parse_batch_rejects_a_missing_data_row_despite_blank_lines():
    with pytest.raises(BatchParseError, match="line count"):
        parse_batch("C01 | ответ\n\n  ", (make_item(1), make_item(2)), max_length=100)


@pytest.mark.parametrize(
    "raw",
    [
        "C01\tREPLY\tодин",
        "C02\tREPLY\tодин\nC01\tREPLY\tдва",
        "C01\tREPLY\tодин\nC01\tREPLY\tдва",
        "C01\tREPLY\tодин\nC02\tREPLY\tдва\nC03\tREPLY\tтри",
        "C01\tUNKNOWN\t\nC02\tREPLY\tдва",
        "C01\tREPLY\tодин\tдва\nC02\tREPLY\tтри",
        "C01<TAB>REPLY<TAB>один<TAB>два\nC02<TAB>REPLY<TAB>три",
        "C01\tREPLY\tодин\nстрока продолжения\nC02\tREPLY\tдва",
        "C01\tSKIP\tне пусто\nC02\tREPLY\tдва",
        "C01\tREPLY\t   \nC02\tREPLY\tдва",
        "C01\tREPLY\tодин | два\nC02\tREPLY\tтри",
        "C01 | \nC02 | ответ",
        "C01 | SKIP | текст\nC02 | ответ",
        "C01 | ответ\tс tab\nC02 | ответ",
    ],
)
def test_parse_batch_rejects_the_entire_batch_for_structural_errors(raw):
    items = (make_item(1), make_item(2))

    with pytest.raises(BatchParseError):
        parse_batch(raw, items, max_length=100)


def test_parse_batch_rejects_reply_that_exceeds_limit_after_author_prefix():
    item = make_item(1)
    max_length = len("Автор 1, ответ")

    with pytest.raises(BatchParseError):
        parse_batch("C01\tREPLY\tответ длиннее", (item,), max_length=max_length)
