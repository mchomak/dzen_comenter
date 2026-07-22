from datetime import datetime

from sqlalchemy.dialects import postgresql

from dzen_commenter.contracts.enums import CommentStatus
from dzen_commenter.contracts.models import Comment
from dzen_commenter.db.repository import PostgresCommentRepository


class _Result:
    def scalar_one(self) -> int:
        return 1


class _Connection:
    def __init__(self) -> None:
        self.statement = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, statement):
        self.statement = statement
        return _Result()


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def begin(self):
        return self.connection


def test_empty_repeat_upsert_preserves_legacy_null_thread_text():
    engine = _Engine()
    repository = PostgresCommentRepository(engine)
    repository.upsert_comment(
        Comment(
            id=None,
            dzen_comment_id="legacy-comment",
            publication_id=1,
            author="author",
            text="comment",
            parent_comment_id=None,
            posted_at=None,
            fetched_at=datetime(2026, 7, 22),
            status=CommentStatus.NEW,
            thread_text="",
        )
    )

    statement = engine.connection.statement.compile(dialect=postgresql.dialect())
    query = str(statement)

    assert "CASE WHEN" in query
    assert "comments.thread_text IS NULL" in query
    assert "excluded.thread_text = %(thread_text_1)s" in query
    assert statement.params["thread_text_1"] == ""
