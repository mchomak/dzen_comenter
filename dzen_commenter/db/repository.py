from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from dzen_commenter.contracts.enums import CommentStatus, ReplyStatus
from dzen_commenter.contracts.models import Comment, Publication, Reply
from dzen_commenter.db.models import CommentTable, PublicationTable, ReplyTable


class PostgresCommentRepository:
    """PostgreSQL implementation of the frozen CommentRepository contract."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert_publication(self, pub: Publication) -> int:
        stmt = (
            insert(PublicationTable)
            .values(
                dzen_publication_id=pub.dzen_publication_id,
                title=pub.title,
                url=pub.url,
            )
            .on_conflict_do_update(
                index_elements=[PublicationTable.dzen_publication_id],
                set_={"title": pub.title, "url": pub.url},
            )
            .returning(PublicationTable.id)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one()

    def upsert_comment(self, comment: Comment) -> int:
        stmt = (
            insert(CommentTable)
            .values(
                dzen_comment_id=comment.dzen_comment_id,
                publication_id=comment.publication_id,
                author=comment.author,
                text=comment.text,
                parent_comment_id=comment.parent_comment_id,
                posted_at=comment.posted_at,
                fetched_at=comment.fetched_at,
                status=comment.status.value,
            )
            .on_conflict_do_update(
                index_elements=[CommentTable.dzen_comment_id],
                set_={
                    "publication_id": comment.publication_id,
                    "author": comment.author,
                    "text": comment.text,
                    "parent_comment_id": comment.parent_comment_id,
                    "posted_at": comment.posted_at,
                    "fetched_at": comment.fetched_at,
                    "status": comment.status.value,
                },
            )
            .returning(CommentTable.id)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one()

    def save_reply(self, reply: Reply) -> int:
        stmt = (
            insert(ReplyTable)
            .values(
                comment_id=reply.comment_id,
                generated_text=reply.generated_text,
                ai_provider=reply.ai_provider,
                ai_model=reply.ai_model,
                status=reply.status.value,
                published_at=reply.published_at,
                error_reason=reply.error_reason,
                created_at=reply.created_at,
            )
            .returning(ReplyTable.id)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one()

    def set_comment_status(self, comment_id: int, status: CommentStatus) -> None:
        stmt = (
            update(CommentTable)
            .where(CommentTable.id == comment_id)
            .values(status=status.value)
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def set_reply_status(
        self, reply_id: int, status: ReplyStatus, error_reason: str | None = None
    ) -> None:
        values: dict[str, object] = {"status": status.value}
        if error_reason is not None:
            values["error_reason"] = error_reason
        stmt = update(ReplyTable).where(ReplyTable.id == reply_id).values(**values)
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def has_published_reply(self, comment_id: int) -> bool:
        stmt = select(
            select(ReplyTable.id)
            .where(
                ReplyTable.comment_id == comment_id,
                ReplyTable.status == ReplyStatus.PUBLISHED.value,
            )
            .exists()
        )
        with self._engine.begin() as conn:
            return bool(conn.execute(stmt).scalar_one())

    def has_generated_reply(self, comment_id: int) -> bool:
        stmt = select(
            select(ReplyTable.id)
            .where(
                ReplyTable.comment_id == comment_id,
                ReplyTable.status.in_(
                    [ReplyStatus.GENERATED.value, ReplyStatus.PUBLISHED.value]
                ),
            )
            .exists()
        )
        with self._engine.begin() as conn:
            return bool(conn.execute(stmt).scalar_one())
