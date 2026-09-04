from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import case, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from dzen_commenter.contracts.enums import BatchOutcomeKind, CommentStatus, ReplyStatus
from dzen_commenter.contracts.models import (
    BatchItem,
    BatchOutcome,
    ClaimedBatch,
    Comment,
    Publication,
    Reply,
)
from dzen_commenter.db.models import (
    CommentBatchQueueTable,
    CommentTable,
    PublicationTable,
    ReplyBatchItemTable,
    ReplyBatchTable,
    ReplyTable,
)


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
                post_title=comment.publication_title or None,
                post_url=comment.post_url,
                thread_text=comment.thread_text,
            )
        )
        stmt = (
            stmt.on_conflict_do_update(
                index_elements=[CommentTable.dzen_comment_id],
                set_={
                    "publication_id": comment.publication_id,
                    "author": comment.author,
                    "text": comment.text,
                    "parent_comment_id": comment.parent_comment_id,
                    "posted_at": comment.posted_at,
                    "status": comment.status.value,
                    "post_title": case(
                        (
                            stmt.excluded.post_title.is_(None),
                            CommentTable.post_title,
                        ),
                        else_=stmt.excluded.post_title,
                    ),
                    "post_url": case(
                        (
                            stmt.excluded.post_url.is_(None),
                            CommentTable.post_url,
                        ),
                        else_=stmt.excluded.post_url,
                    ),
                    "thread_text": case(
                        (
                            CommentTable.thread_text.is_(None)
                            & (stmt.excluded.thread_text == ""),
                            CommentTable.thread_text,
                        ),
                        else_=stmt.excluded.thread_text,
                    ),
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
                article_context_status=reply.article_context_status,
                is_cta_candidate=reply.is_cta_candidate,
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
        self,
        reply_id: int,
        status: ReplyStatus,
        error_reason: str | None = None,
        published_at: datetime | None = None,
    ) -> None:
        values: dict[str, object] = {"status": status.value}
        if error_reason is not None:
            values["error_reason"] = error_reason
        if published_at is not None:
            values["published_at"] = published_at
        stmt = update(ReplyTable).where(ReplyTable.id == reply_id).values(**values)
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def count_published_replies_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(ReplyTable).where(
            ReplyTable.status == ReplyStatus.PUBLISHED.value,
            ReplyTable.published_at >= since,
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).scalar_one())

    def count_ai_attempts_since(self, since: datetime) -> int:
        stmt = select(func.count()).select_from(ReplyTable).where(
            ReplyTable.status.in_(
                [
                    ReplyStatus.GENERATED.value,
                    ReplyStatus.PUBLISHED.value,
                    ReplyStatus.ERROR.value,
                    ReplyStatus.SKIPPED.value,
                ]
            ),
            ReplyTable.created_at >= since,
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).scalar_one())

    def enqueue_batch_comment(
        self,
        comment_id: int,
        post_url: str,
        *,
        queued_at: datetime,
        cutover_at: datetime,
    ) -> bool:
        """Queue a fresh, eligible comment once without reviving historical rows."""
        successful_reply = exists(
            select(ReplyTable.id).where(
                ReplyTable.comment_id == CommentTable.id,
                ReplyTable.status.in_(
                    [ReplyStatus.GENERATED.value, ReplyStatus.PUBLISHED.value]
                ),
            )
        )
        eligible = select(CommentTable.id).where(
            CommentTable.id == comment_id,
            CommentTable.post_url == post_url,
            CommentTable.status == CommentStatus.NEW.value,
            CommentTable.fetched_at >= cutover_at,
            ~successful_reply,
        )
        stmt = (
            insert(CommentBatchQueueTable)
            .from_select(
                ["comment_id", "post_url", "queued_at", "state", "attempt_count"],
                select(
                    CommentTable.id,
                    CommentTable.post_url,
                    literal(queued_at),
                    literal("queued"),
                    literal(0),
                ).where(CommentTable.id.in_(eligible)),
            )
            .on_conflict_do_nothing(index_elements=[CommentBatchQueueTable.comment_id])
            .returning(CommentBatchQueueTable.comment_id)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one_or_none() is not None

    def claim_next_batch(
        self,
        now: datetime,
        *,
        max_comments: int,
        wait_hours: int,
        quota_remaining: int,
        available_comment_ids: Collection[int] | None = None,
    ) -> ClaimedBatch | None:
        """Claim one post-local batch under row locks in a stable item order."""
        limit = min(max_comments, quota_remaining)
        if limit <= 0:
            return None

        ready = (
            (CommentBatchQueueTable.state == "queued")
            & (CommentBatchQueueTable.claimed_batch_id.is_(None))
            & (
                (CommentBatchQueueTable.next_attempt_at.is_(None))
                | (CommentBatchQueueTable.next_attempt_at <= now)
            )
        )
        if available_comment_ids is not None:
            ready &= CommentBatchQueueTable.comment_id.in_(available_comment_ids)
        with self._engine.begin() as conn:
            first_post_url = conn.execute(
                select(CommentBatchQueueTable.post_url)
                .where(ready)
                .order_by(
                    CommentBatchQueueTable.queued_at,
                    CommentBatchQueueTable.comment_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True, of=CommentBatchQueueTable)
            ).scalar_one_or_none()
            if first_post_url is None:
                return None

            rows = conn.execute(
                select(
                    CommentBatchQueueTable.queued_at.label("queue_queued_at"),
                    CommentTable.id.label("comment_id"),
                    CommentTable.post_title.label("post_title"),
                    CommentTable.thread_text.label("thread_text"),
                    CommentTable.author.label("author"),
                    CommentTable.text.label("comment_text"),
                )
                .join(
                    CommentTable,
                    CommentTable.id == CommentBatchQueueTable.comment_id,
                )
                .where(ready, CommentBatchQueueTable.post_url == first_post_url)
                .order_by(
                    CommentBatchQueueTable.queued_at,
                    CommentBatchQueueTable.comment_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True, of=CommentBatchQueueTable)
            ).mappings().all()
            if not rows:
                return None
            oldest = rows[0]["queue_queued_at"]
            if len(rows) < limit and oldest > now - timedelta(hours=wait_hours):
                return None

            batch_id = conn.execute(
                insert(ReplyBatchTable)
                .values(
                    post_url=first_post_url,
                    created_at=now,
                    status="claimed",
                    item_count=len(rows),
                )
                .returning(ReplyBatchTable.id)
            ).scalar_one()
            items = tuple(
                BatchItem(
                    batch_id=batch_id,
                    comment_id=row["comment_id"],
                    item_no=index,
                    post_url=first_post_url,
                    publication_title=row["post_title"] or "",
                    thread_text=row["thread_text"] or "",
                    author=row["author"] or "",
                    comment_text=row["comment_text"] or "",
                )
                for index, row in enumerate(rows, start=1)
            )
            conn.execute(
                insert(ReplyBatchItemTable),
                [
                    {
                        "batch_id": batch_id,
                        "comment_id": item.comment_id,
                        "item_no": item.item_no,
                        "status": "claimed",
                    }
                    for item in items
                ],
            )
            comment_ids = [item.comment_id for item in items]
            conn.execute(
                update(CommentBatchQueueTable)
                .where(CommentBatchQueueTable.comment_id.in_(comment_ids))
                .values(
                    state="claimed",
                    claimed_batch_id=batch_id,
                    attempt_count=CommentBatchQueueTable.attempt_count + 1,
                )
            )
            return ClaimedBatch(
                id=batch_id,
                post_url=first_post_url,
                created_at=now,
                items=items,
            )

    def save_batch_outcomes(
        self,
        batch_id: int,
        outcomes: tuple[BatchOutcome, ...],
        *,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        created_at: datetime,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        retry_cooldown_minutes: int,
        max_attempts_per_comment: int,
    ) -> tuple[int, ...]:
        """Persist every item outcome in one transaction or persist none of them."""
        with self._engine.begin() as conn:
            items = conn.execute(
                select(
                    ReplyBatchItemTable.comment_id.label("comment_id"),
                    ReplyBatchItemTable.item_no.label("item_no"),
                )
                .where(ReplyBatchItemTable.batch_id == batch_id)
                .order_by(ReplyBatchItemTable.item_no)
                .with_for_update()
            ).mappings().all()
            expected = [(item["comment_id"], item["item_no"]) for item in items]
            actual = [(outcome.comment_id, outcome.item_no) for outcome in outcomes]
            if not items or actual != expected:
                raise ValueError("Batch outcomes do not match the claimed item order")
            if any(
                outcome.kind not in set(BatchOutcomeKind) for outcome in outcomes
            ):
                raise ValueError("Unknown batch outcome kind")

            queue_rows = conn.execute(
                select(
                    CommentBatchQueueTable.comment_id.label("comment_id"),
                    CommentBatchQueueTable.attempt_count.label("attempt_count"),
                )
                .where(
                    CommentBatchQueueTable.comment_id.in_(
                        [outcome.comment_id for outcome in outcomes]
                    ),
                    CommentBatchQueueTable.claimed_batch_id == batch_id,
                    CommentBatchQueueTable.state == "claimed",
                )
                .with_for_update()
            ).mappings().all()
            queues = {queue["comment_id"]: queue for queue in queue_rows}
            if len(queues) != len(outcomes):
                raise ValueError("Batch queue claim is no longer active")

            reply_ids: list[int] = []
            has_error = False
            batch_error_reason: str | None = None
            for item, outcome in zip(items, outcomes, strict=True):
                if outcome.kind is BatchOutcomeKind.REPLY:
                    reply_status = ReplyStatus.GENERATED
                    comment_status = CommentStatus.ANSWERED
                elif outcome.kind is BatchOutcomeKind.SKIP:
                    reply_status = ReplyStatus.SKIPPED
                    comment_status = CommentStatus.SKIPPED
                else:
                    reply_status = ReplyStatus.ERROR
                    comment_status = CommentStatus.ERROR
                    has_error = True
                    batch_error_reason = batch_error_reason or outcome.error_reason

                reply_id = conn.execute(
                    insert(ReplyTable)
                    .values(
                        comment_id=outcome.comment_id,
                        generated_text=outcome.text,
                        ai_provider=ai_provider,
                        ai_model=ai_model,
                        status=reply_status.value,
                        error_reason=outcome.error_reason,
                        created_at=created_at,
                        article_context_status=article_context_status,
                        is_cta_candidate=False,
                    )
                    .returning(ReplyTable.id)
                ).scalar_one()
                reply_ids.append(reply_id)
                conn.execute(
                    update(ReplyBatchItemTable)
                    .where(
                        ReplyBatchItemTable.batch_id == batch_id,
                        ReplyBatchItemTable.comment_id == outcome.comment_id,
                    )
                    .values(status=reply_status.value, reply_id=reply_id)
                )
                conn.execute(
                    update(CommentTable)
                    .where(CommentTable.id == outcome.comment_id)
                    .values(status=comment_status.value)
                )

                queue = queues[outcome.comment_id]
                retry = (
                    outcome.kind is BatchOutcomeKind.ERROR
                    and queue["attempt_count"] < max_attempts_per_comment
                )
                conn.execute(
                    update(CommentBatchQueueTable)
                    .where(CommentBatchQueueTable.comment_id == outcome.comment_id)
                    .values(
                        state="queued" if retry else "completed",
                        claimed_batch_id=None if retry else batch_id,
                        next_attempt_at=(
                            created_at + timedelta(minutes=retry_cooldown_minutes)
                            if retry
                            else None
                        ),
                    )
                )

            conn.execute(
                update(ReplyBatchTable)
                .where(ReplyBatchTable.id == batch_id)
                .values(
                    status="error" if has_error else "completed",
                    article_context_status=article_context_status,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    error_reason=batch_error_reason,
                )
            )
            return tuple(reply_ids)

    def count_cta_candidates_produced(self) -> int:
        stmt = select(func.count()).select_from(ReplyTable).where(
            ReplyTable.status.in_(
                [ReplyStatus.GENERATED.value, ReplyStatus.PUBLISHED.value]
            ),
            ReplyTable.is_cta_candidate.is_(True),
        )
        with self._engine.begin() as conn:
            return int(conn.execute(stmt).scalar_one())

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

    def is_own_reply(self, post_url: str | None, text: str) -> bool:
        """True if `text` is a reply we already published under this post.

        Dzen re-renders our own published replies as regular comment nodes on
        the next scrape, so without this check the bot would treat its own
        reply as a fresh comment from someone else and reply to itself.

        Matches by post_url rather than the scraped parent_comment_id: Dzen's
        DOM never actually exposes a distinct parent for a comment node (it
        resolves to either null or the node's own id), so a parent-based match
        can never fire.
        """
        if not post_url:
            return False
        stmt = select(
            select(ReplyTable.id)
            .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
            .where(
                CommentTable.post_url == post_url,
                ReplyTable.generated_text == text,
                ReplyTable.status == ReplyStatus.PUBLISHED.value,
            )
            .exists()
        )
        with self._engine.begin() as conn:
            return bool(conn.execute(stmt).scalar_one())
