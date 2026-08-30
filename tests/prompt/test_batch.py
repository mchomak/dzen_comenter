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


def test_parse_batch_accepts_model_skip_without_reply_type_prediction():
    item = make_item(1)

    outcome = parse_batch("C01\tSKIP\t", (item,), max_length=100)

    assert outcome[0].kind is BatchOutcomeKind.SKIP


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


@pytest.mark.parametrize(
    "raw",
    [
        "C01\tREPLY\tодин",
        "C02\tREPLY\tодин\nC01\tREPLY\tдва",
        "C01\tREPLY\tодин\nC01\tREPLY\tдва",
        "C01\tREPLY\tодин\nC02\tREPLY\tдва\nC03\tREPLY\tтри",
        "C01\tUNKNOWN\tодин\nC02\tREPLY\tдва",
        "C01\tREPLY\tодин\tдва\nC02\tREPLY\tтри",
        "C01<TAB>REPLY<TAB>один<TAB>два\nC02<TAB>REPLY<TAB>три",
        "C01\tREPLY\tодин\nстрока продолжения\nC02\tREPLY\tдва",
        "C01\tSKIP\tне пусто\nC02\tREPLY\tдва",
        "C01\tREPLY\t   \nC02\tREPLY\tдва",
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
