from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import case, delete, exists, func, literal, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from dzen_commenter.contracts.enums import (
    CommentStatus,
    GenerationFailureOutcome,
    PublicationFailureOutcome,
    ReplyStatus,
)
from dzen_commenter.contracts.models import (
    ArticleContext,
    ClaimedGeneration,
    ClaimedPublication,
    Comment,
    Publication,
    Reply,
    WorkWindowSnapshot,
)
from dzen_commenter.db.models import (
    AICallReservationTable,
    CommentTable,
    PublicationTable,
    ReplyGenerationQueueTable,
    ReplyPublicationQueueTable,
    ReplyTable,
)


class PostgresCommentRepository:
    """PostgreSQL implementation of the frozen CommentRepository contract."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    _PUBLICATION_CLAIM_LEASE = timedelta(hours=2)
    _GENERATION_CLAIM_LEASE = timedelta(minutes=5)
    _AI_CALL_RESERVATION_LOCK_KEY = 0x445A454E414931
    _WORK_WINDOW_LOCK_KEY = 0x445A454E57494E
    _STALE_WORK_REASON = "comment exceeded the configured age limit"
    _UNKNOWN_PUBLICATION_REASON = "publication outcome unknown after worker interruption"

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

    @staticmethod
    def _upsert_comment_statement(comment: Comment):
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
                    "status": case(
                        (
                            (stmt.excluded.status == CommentStatus.NEW.value)
                            & (CommentTable.status != CommentStatus.NEW.value),
                            CommentTable.status,
                        ),
                        else_=stmt.excluded.status,
                    ),
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
        return stmt

    def upsert_comment(self, comment: Comment) -> int:
        stmt = self._upsert_comment_statement(comment)
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one()

    def upsert_eligible_comment(self, comment: Comment, *, queued_at: datetime) -> int:
        with self._engine.begin() as conn:
            comment_id = conn.execute(
                self._upsert_comment_statement(comment)
            ).scalar_one()
            conn.execute(self._enqueue_generation_statement(comment_id, queued_at))
            return comment_id

    def enqueue_pending_generations(self, *, queued_at: datetime) -> int:
        terminal_reply = exists(
            select(ReplyTable.id).where(
                ReplyTable.comment_id == CommentTable.id,
                ReplyTable.status.in_(
                    [
                        ReplyStatus.GENERATED.value,
                        ReplyStatus.PUBLISHED.value,
                        ReplyStatus.SKIPPED.value,
                        ReplyStatus.ERROR.value,
                        ReplyStatus.UNCONFIRMED.value,
                    ]
                ),
            )
        )
        stmt = (
            insert(ReplyGenerationQueueTable)
            .from_select(
                ["comment_id", "state", "attempt_count", "created_at"],
                select(
                    CommentTable.id,
                    literal("queued"),
                    literal(0),
                    literal(queued_at),
                ).where(
                    CommentTable.status == CommentStatus.NEW.value,
                    ~terminal_reply,
                ),
            )
            .on_conflict_do_nothing(
                index_elements=[ReplyGenerationQueueTable.comment_id]
            )
            .returning(ReplyGenerationQueueTable.comment_id)
        )
        with self._engine.begin() as conn:
            return len(conn.execute(stmt).scalars().all())

    @classmethod
    def _lock_work_window(cls, conn) -> None:
        if conn.dialect.name == "postgresql":
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": cls._WORK_WINDOW_LOCK_KEY},
            )

    @staticmethod
    def _restore_missing_publications(conn, now: datetime) -> None:
        missing = ~exists(
            select(ReplyPublicationQueueTable.reply_id).where(
                ReplyPublicationQueueTable.reply_id == ReplyTable.id
            )
        )
        stmt = (
            insert(ReplyPublicationQueueTable)
            .from_select(
                ["reply_id", "state", "attempt_count", "created_at"],
                select(
                    ReplyTable.id,
                    literal("queued"),
                    literal(0),
                    func.coalesce(ReplyTable.created_at, now),
                ).where(ReplyTable.status == ReplyStatus.GENERATED.value, missing),
            )
            .on_conflict_do_nothing(
                index_elements=[ReplyPublicationQueueTable.reply_id]
            )
        )
        conn.execute(stmt)

    @classmethod
    def _expire_stale_generation_work(
        cls, conn, *, now: datetime, stale_before: datetime
    ) -> int:
        lease_expired = or_(
            ReplyGenerationQueueTable.claimed_at.is_(None),
            ReplyGenerationQueueTable.claimed_at
            <= now - cls._GENERATION_CLAIM_LEASE,
        )
        age = func.coalesce(CommentTable.posted_at, CommentTable.fetched_at)
        stale_ids = conn.execute(
            select(ReplyGenerationQueueTable.comment_id)
            .join(
                CommentTable,
                CommentTable.id == ReplyGenerationQueueTable.comment_id,
            )
            .where(
                ReplyGenerationQueueTable.state.in_(("queued", "waiting", "claimed")),
                age.is_not(None),
                age < stale_before,
                or_(
                    ReplyGenerationQueueTable.state != "claimed",
                    lease_expired,
                ),
            )
            .with_for_update(skip_locked=True, of=ReplyGenerationQueueTable)
        ).scalars().all()
        stale_new_ids = conn.execute(
            select(CommentTable.id)
            .where(
                CommentTable.status == CommentStatus.NEW.value,
                age.is_not(None),
                age < stale_before,
                ~exists(
                    select(ReplyGenerationQueueTable.comment_id).where(
                        ReplyGenerationQueueTable.comment_id == CommentTable.id
                    )
                ),
                ~exists(
                    select(ReplyTable.id).where(ReplyTable.comment_id == CommentTable.id)
                ),
            )
            .with_for_update(skip_locked=True, of=CommentTable)
        ).scalars().all()
        comment_ids = set(stale_ids) | set(stale_new_ids)
        if not comment_ids:
            return 0

        conn.execute(
            update(ReplyGenerationQueueTable)
            .where(ReplyGenerationQueueTable.comment_id.in_(comment_ids))
            .values(
                state="completed",
                next_attempt_at=None,
                last_error=cls._STALE_WORK_REASON,
                claimed_at=None,
                claim_token=None,
            )
        )
        conn.execute(
            insert(ReplyTable).from_select(
                [
                    "comment_id",
                    "generated_text",
                    "ai_provider",
                    "ai_model",
                    "status",
                    "error_reason",
                    "created_at",
                    "is_cta_candidate",
                ],
                select(
                    CommentTable.id,
                    literal(""),
                    literal("system"),
                    literal("system"),
                    literal(ReplyStatus.SKIPPED.value),
                    literal(cls._STALE_WORK_REASON),
                    literal(now),
                    literal(False),
                ).where(CommentTable.id.in_(comment_ids)),
            )
        )
        conn.execute(
            update(CommentTable)
            .where(CommentTable.id.in_(comment_ids))
            .values(status=CommentStatus.SKIPPED.value)
        )
        return len(comment_ids)

    @classmethod
    def _expire_stale_publication_work(
        cls, conn, *, now: datetime, stale_before: datetime
    ) -> int:
        lease_expired = or_(
            ReplyPublicationQueueTable.claimed_at.is_(None),
            ReplyPublicationQueueTable.claimed_at
            <= now - cls._PUBLICATION_CLAIM_LEASE,
        )
        age = func.coalesce(CommentTable.posted_at, CommentTable.fetched_at)
        reply_ids = conn.execute(
            select(ReplyPublicationQueueTable.reply_id)
            .join(ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id)
            .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
            .where(
                ReplyPublicationQueueTable.state.in_(
                    ("queued", "waiting", "claimed")
                ),
                age.is_not(None),
                age < stale_before,
                or_(
                    ReplyPublicationQueueTable.state != "claimed",
                    lease_expired,
                ),
            )
            .with_for_update(skip_locked=True, of=ReplyPublicationQueueTable)
        ).scalars().all()
        if not reply_ids:
            return 0

        conn.execute(
            update(ReplyPublicationQueueTable)
            .where(ReplyPublicationQueueTable.reply_id.in_(reply_ids))
            .values(
                state="completed",
                next_attempt_at=None,
                last_error=cls._STALE_WORK_REASON,
                claimed_at=None,
                claim_token=None,
            )
        )
        conn.execute(
            update(ReplyTable)
            .where(ReplyTable.id.in_(reply_ids))
            .values(
                status=ReplyStatus.SKIPPED.value,
                error_reason=cls._STALE_WORK_REASON,
            )
        )
        conn.execute(
            update(CommentTable)
            .where(
                CommentTable.id.in_(
                    select(ReplyTable.comment_id).where(ReplyTable.id.in_(reply_ids))
                )
            )
            .values(status=CommentStatus.SKIPPED.value)
        )
        return len(reply_ids)

    @classmethod
    def _expire_unknown_submissions(cls, conn, *, now: datetime) -> None:
        reply_ids = conn.execute(
            update(ReplyPublicationQueueTable)
            .where(
                ReplyPublicationQueueTable.state == "submitting",
                or_(
                    ReplyPublicationQueueTable.claimed_at.is_(None),
                    ReplyPublicationQueueTable.claimed_at
                    <= now - cls._PUBLICATION_CLAIM_LEASE,
                ),
            )
            .values(
                state="completed",
                next_attempt_at=None,
                last_error=cls._UNKNOWN_PUBLICATION_REASON,
                claimed_at=None,
                claim_token=None,
            )
            .returning(ReplyPublicationQueueTable.reply_id)
        ).scalars().all()
        if not reply_ids:
            return
        conn.execute(
            update(ReplyTable)
            .where(ReplyTable.id.in_(reply_ids))
            .values(
                status=ReplyStatus.UNCONFIRMED.value,
                error_reason=cls._UNKNOWN_PUBLICATION_REASON,
            )
        )
        conn.execute(
            update(CommentTable)
            .where(
                CommentTable.id.in_(
                    select(ReplyTable.comment_id).where(ReplyTable.id.in_(reply_ids))
                )
            )
            .values(status=CommentStatus.PUBLICATION_UNCONFIRMED.value)
        )

    @classmethod
    def _recover_expired_claims(cls, conn, *, now: datetime) -> None:
        stale_generation = or_(
            ReplyGenerationQueueTable.claimed_at.is_(None),
            ReplyGenerationQueueTable.claimed_at
            <= now - cls._GENERATION_CLAIM_LEASE,
        )
        recovered_generation = conn.execute(
            update(ReplyGenerationQueueTable)
            .where(
                ReplyGenerationQueueTable.state == "claimed",
                stale_generation,
            )
            .values(state="queued", claimed_at=None, claim_token=None)
            .returning(ReplyGenerationQueueTable.comment_id)
        ).scalars().all()
        if recovered_generation:
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id.in_(recovered_generation))
                .values(status=CommentStatus.GENERATION_RETRY.value)
            )

        stale_publication = or_(
            ReplyPublicationQueueTable.claimed_at.is_(None),
            ReplyPublicationQueueTable.claimed_at
            <= now - cls._PUBLICATION_CLAIM_LEASE,
        )
        conn.execute(
            update(ReplyPublicationQueueTable)
            .where(
                ReplyPublicationQueueTable.state == "claimed",
                stale_publication,
            )
            .values(state="queued", claimed_at=None, claim_token=None)
        )

    @staticmethod
    def _active_work_comment_ids(conn) -> set[int]:
        generation_ids = select(
            ReplyGenerationQueueTable.comment_id.label("comment_id")
        ).where(ReplyGenerationQueueTable.state.in_(("queued", "claimed")))
        publication_ids = (
            select(ReplyTable.comment_id.label("comment_id"))
            .join(
                ReplyPublicationQueueTable,
                ReplyPublicationQueueTable.reply_id == ReplyTable.id,
            )
            .where(
                ReplyPublicationQueueTable.state.in_(
                    ("queued", "claimed", "submitting")
                )
            )
        )
        return set(conn.execute(generation_ids.union(publication_ids)).scalars())

    @classmethod
    def _assign_work_window_slots(cls, conn, *, limit: int) -> None:
        active_ids = set(
            conn.execute(
                select(ReplyGenerationQueueTable.comment_id).where(
                    ReplyGenerationQueueTable.state == "claimed"
                )
            ).scalars()
        )
        active_ids.update(
            conn.execute(
                select(ReplyTable.comment_id)
                .join(
                    ReplyPublicationQueueTable,
                    ReplyPublicationQueueTable.reply_id == ReplyTable.id,
                )
                .where(
                    ReplyPublicationQueueTable.state.in_(("claimed", "submitting"))
                )
            ).scalars()
        )
        retry_generation_ids = conn.execute(
            select(ReplyGenerationQueueTable.comment_id)
            .join(
                CommentTable,
                CommentTable.id == ReplyGenerationQueueTable.comment_id,
            )
            .where(
                ReplyGenerationQueueTable.state == "queued",
                or_(
                    ReplyGenerationQueueTable.attempt_count > 0,
                    ReplyGenerationQueueTable.next_attempt_at.is_not(None),
                ),
            )
            .order_by(
                func.coalesce(CommentTable.posted_at, CommentTable.fetched_at)
                .desc()
                .nulls_last(),
                CommentTable.dzen_comment_id.asc(),
                CommentTable.id.asc(),
            )
        ).scalars().all()
        for comment_id in retry_generation_ids:
            if comment_id in active_ids:
                continue
            if len(active_ids) < limit:
                active_ids.add(comment_id)
                continue
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(
                    ReplyGenerationQueueTable.comment_id == comment_id,
                    ReplyGenerationQueueTable.state == "queued",
                )
                .values(state="waiting", claimed_at=None, claim_token=None)
            )

        publication_candidates = conn.execute(
            select(
                ReplyTable.comment_id,
                func.min(ReplyPublicationQueueTable.created_at).label("created_at"),
            )
            .join(
                ReplyPublicationQueueTable,
                ReplyPublicationQueueTable.reply_id == ReplyTable.id,
            )
            .where(ReplyPublicationQueueTable.state.in_(("queued", "waiting")))
            .group_by(ReplyTable.comment_id)
            .order_by(func.min(ReplyPublicationQueueTable.created_at), ReplyTable.comment_id)
        ).all()
        for comment_id, _ in publication_candidates:
            activate = comment_id in active_ids or len(active_ids) < limit
            if activate:
                active_ids.add(comment_id)
                conn.execute(
                    update(ReplyPublicationQueueTable)
                    .where(
                        ReplyPublicationQueueTable.reply_id.in_(
                            select(ReplyTable.id).where(
                                ReplyTable.comment_id == comment_id
                            )
                        ),
                        ReplyPublicationQueueTable.state == "waiting",
                    )
                    .values(state="queued")
                )
            else:
                conn.execute(
                    update(ReplyPublicationQueueTable)
                    .where(
                        ReplyPublicationQueueTable.reply_id.in_(
                            select(ReplyTable.id).where(
                                ReplyTable.comment_id == comment_id
                            )
                        ),
                        ReplyPublicationQueueTable.state == "queued",
                    )
                    .values(state="waiting")
                )

        generation_candidates = conn.execute(
            select(ReplyGenerationQueueTable.comment_id)
            .join(
                CommentTable,
                CommentTable.id == ReplyGenerationQueueTable.comment_id,
            )
            .where(ReplyGenerationQueueTable.state == "queued")
            .order_by(
                func.coalesce(CommentTable.posted_at, CommentTable.fetched_at)
                .desc()
                .nulls_last(),
                CommentTable.dzen_comment_id.asc(),
                CommentTable.id.asc(),
            )
        ).scalars().all()
        for comment_id in generation_candidates:
            if comment_id in active_ids:
                continue
            if len(active_ids) < limit:
                active_ids.add(comment_id)
                continue
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(
                    ReplyGenerationQueueTable.comment_id == comment_id,
                    ReplyGenerationQueueTable.state == "queued",
                )
                .values(state="waiting", claimed_at=None, claim_token=None)
            )

    @staticmethod
    def _work_window_snapshot(
        conn, *, expired_generation: int, expired_publication: int
    ) -> WorkWindowSnapshot:
        active_generation = conn.execute(
            select(func.count(func.distinct(ReplyGenerationQueueTable.comment_id))).where(
                ReplyGenerationQueueTable.state.in_(("queued", "claimed"))
            )
        ).scalar_one()
        waiting_generation = conn.execute(
            select(func.count(func.distinct(ReplyGenerationQueueTable.comment_id))).where(
                ReplyGenerationQueueTable.state == "waiting"
            )
        ).scalar_one()
        active_publication = conn.execute(
            select(func.count(func.distinct(CommentTable.id)))
            .select_from(ReplyPublicationQueueTable)
            .join(ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id)
            .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
            .where(
                ReplyPublicationQueueTable.state.in_(("queued", "claimed", "submitting"))
            )
        ).scalar_one()
        waiting_publication_query = (
            select(func.count(func.distinct(CommentTable.id)))
            .select_from(ReplyPublicationQueueTable)
            .join(ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id)
            .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
            .where(ReplyPublicationQueueTable.state == "waiting")
        )
        waiting_publication = conn.execute(waiting_publication_query).scalar_one()
        generated_waiting_publication = conn.execute(
            select(func.count(func.distinct(CommentTable.id)))
            .select_from(ReplyPublicationQueueTable)
            .join(ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id)
            .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
            .where(
                ReplyPublicationQueueTable.state == "waiting",
                ReplyTable.status == ReplyStatus.GENERATED.value,
            )
        ).scalar_one()
        return WorkWindowSnapshot(
            active_generation=int(active_generation),
            active_publication=int(active_publication),
            waiting_generation=int(waiting_generation),
            waiting_publication=int(waiting_publication),
            generated_waiting_publication=int(generated_waiting_publication),
            expired_generation=expired_generation,
            expired_publication=expired_publication,
        )

    def _reconcile_work_window(
        self,
        conn,
        *,
        now: datetime,
        stale_before: datetime | None,
        limit: int,
    ) -> WorkWindowSnapshot:
        self._lock_work_window(conn)
        self._restore_missing_publications(conn, now)
        self._expire_unknown_submissions(conn, now=now)
        expired_generation = 0
        expired_publication = 0
        if stale_before is not None:
            expired_generation = self._expire_stale_generation_work(
                conn, now=now, stale_before=stale_before
            )
            expired_publication = self._expire_stale_publication_work(
                conn, now=now, stale_before=stale_before
            )
        self._recover_expired_claims(conn, now=now)
        self._assign_work_window_slots(conn, limit=limit)
        return self._work_window_snapshot(
            conn,
            expired_generation=expired_generation,
            expired_publication=expired_publication,
        )

    def reconcile_work_window(
        self, *, now: datetime, stale_before: datetime, limit: int
    ) -> WorkWindowSnapshot:
        if limit <= 0:
            raise ValueError("work window limit must be positive")
        with self._engine.begin() as conn:
            return self._reconcile_work_window(
                conn, now=now, stale_before=stale_before, limit=limit
            )

    @staticmethod
    def _insert_comment_if_unknown_statement(comment: Comment):
        return (
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
            .on_conflict_do_nothing(
                index_elements=[CommentTable.dzen_comment_id]
            )
            .returning(CommentTable.id)
        )

    def admit_recent_comments(
        self, comments: Sequence[Comment], *, queued_at: datetime, limit: int
    ) -> int:
        if limit <= 0:
            raise ValueError("work window limit must be positive")

        def order_key(comment: Comment):
            published = comment.posted_at or comment.fetched_at
            if published is None:
                timestamp = float("inf")
            else:
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                timestamp = -published.timestamp()
            return timestamp, comment.dzen_comment_id

        ordered = sorted(
            (comment for comment in comments if comment.status is CommentStatus.NEW),
            key=order_key,
        )
        with self._engine.begin() as conn:
            self._reconcile_work_window(
                conn, now=queued_at, stale_before=None, limit=limit
            )
            active_ids = self._active_work_comment_ids(conn)
            slots = max(0, limit - len(active_ids))
            seen: set[str] = set()
            admitted = 0
            for comment in ordered:
                if slots == 0:
                    break
                if comment.dzen_comment_id in seen:
                    continue
                seen.add(comment.dzen_comment_id)
                comment_id = conn.execute(
                    self._insert_comment_if_unknown_statement(comment)
                ).scalar_one_or_none()
                if comment_id is None:
                    continue
                conn.execute(self._enqueue_generation_statement(comment_id, queued_at))
                active_ids.add(comment_id)
                admitted += 1
                slots -= 1
            return admitted

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

    def skip_comment_if_new(self, comment_id: int) -> bool:
        stmt = (
            update(CommentTable)
            .where(
                CommentTable.id == comment_id,
                CommentTable.status == CommentStatus.NEW.value,
            )
            .values(status=CommentStatus.SKIPPED.value)
            .returning(CommentTable.id)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one_or_none() is not None

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
        with self._engine.begin() as conn:
            return self._count_ai_call_reservations(conn, since)

    @staticmethod
    def _count_ai_call_reservations(conn, since: datetime) -> int:
        stmt = select(func.count()).select_from(AICallReservationTable).where(
            AICallReservationTable.reserved_at >= since
        )
        return int(conn.execute(stmt).scalar_one())

    def reserve_ai_call(self, now: datetime, *, limit: int) -> bool:
        if limit <= 0:
            return False

        cutoff = now - timedelta(hours=1)
        with self._engine.begin() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": self._AI_CALL_RESERVATION_LOCK_KEY},
            )
            conn.execute(
                delete(AICallReservationTable).where(
                    AICallReservationTable.reserved_at < cutoff
                )
            )
            reserved = self._count_ai_call_reservations(conn, cutoff)
            if reserved >= limit:
                return False
            conn.execute(
                insert(AICallReservationTable).values(reserved_at=now)
            )
            return True

    @staticmethod
    def _enqueue_generation_statement(comment_id: int, queued_at: datetime):
        terminal_reply = exists(
            select(ReplyTable.id).where(
                ReplyTable.comment_id == CommentTable.id,
                ReplyTable.status.in_(
                    [
                        ReplyStatus.GENERATED.value,
                        ReplyStatus.PUBLISHED.value,
                        ReplyStatus.SKIPPED.value,
                        ReplyStatus.ERROR.value,
                        ReplyStatus.UNCONFIRMED.value,
                    ]
                ),
            )
        )
        eligible = select(CommentTable.id).where(
            CommentTable.id == comment_id,
            CommentTable.status == CommentStatus.NEW.value,
            ~terminal_reply,
        )
        stmt = (
            insert(ReplyGenerationQueueTable)
            .from_select(
                ["comment_id", "state", "attempt_count", "created_at"],
                select(
                    CommentTable.id,
                    literal("queued"),
                    literal(0),
                    literal(queued_at),
                ).where(CommentTable.id.in_(eligible)),
            )
            .on_conflict_do_nothing(
                index_elements=[ReplyGenerationQueueTable.comment_id]
            )
            .returning(ReplyGenerationQueueTable.comment_id)
        )
        return stmt

    def enqueue_generation(self, comment_id: int, *, queued_at: datetime) -> bool:
        stmt = self._enqueue_generation_statement(comment_id, queued_at)
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one_or_none() is not None

    def claim_next_generation(self, now: datetime) -> ClaimedGeneration | None:
        ready = (ReplyGenerationQueueTable.state == "queued") & (
            (ReplyGenerationQueueTable.next_attempt_at.is_(None))
            | (ReplyGenerationQueueTable.next_attempt_at <= now)
        )
        with self._engine.begin() as conn:
            self._lock_work_window(conn)
            stale_comment_ids = conn.execute(
                update(ReplyGenerationQueueTable)
                .where(
                    ReplyGenerationQueueTable.state == "claimed",
                    or_(
                        ReplyGenerationQueueTable.claimed_at.is_(None),
                        ReplyGenerationQueueTable.claimed_at
                        <= now - self._GENERATION_CLAIM_LEASE,
                    ),
                )
                .values(state="queued", claimed_at=None, claim_token=None)
                .returning(ReplyGenerationQueueTable.comment_id)
            ).scalars().all()
            if stale_comment_ids:
                conn.execute(
                    update(CommentTable)
                    .where(CommentTable.id.in_(stale_comment_ids))
                    .values(status=CommentStatus.GENERATION_RETRY.value)
                )

            row = conn.execute(
                select(
                    ReplyGenerationQueueTable.comment_id.label("comment_id"),
                    ReplyGenerationQueueTable.attempt_count.label("attempt_count"),
                    CommentTable.dzen_comment_id.label("dzen_comment_id"),
                    CommentTable.publication_id.label("publication_id"),
                    CommentTable.author.label("author"),
                    CommentTable.text.label("comment_text"),
                    CommentTable.parent_comment_id.label("parent_comment_id"),
                    CommentTable.posted_at.label("posted_at"),
                    CommentTable.fetched_at.label("fetched_at"),
                    CommentTable.post_title.label("publication_title"),
                    CommentTable.thread_text.label("thread_text"),
                    CommentTable.post_url.label("post_url"),
                )
                .join(
                    CommentTable,
                    CommentTable.id == ReplyGenerationQueueTable.comment_id,
                )
                .where(ready)
                .order_by(
                    func.coalesce(
                        CommentTable.posted_at, CommentTable.fetched_at
                    )
                    .desc()
                    .nulls_last(),
                    CommentTable.fetched_at.desc().nulls_last(),
                    CommentTable.id.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True, of=ReplyGenerationQueueTable)
            ).mappings().one_or_none()
            if row is None:
                return None

            attempt_count = int(row["attempt_count"]) + 1
            claim_token = uuid4().hex
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(ReplyGenerationQueueTable.comment_id == row["comment_id"])
                .values(
                    state="claimed",
                    claimed_at=now,
                    attempt_count=attempt_count,
                    claim_token=claim_token,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == row["comment_id"])
                .values(status=CommentStatus.GENERATING.value)
            )

        return ClaimedGeneration(
            comment=Comment(
                id=row["comment_id"],
                dzen_comment_id=row["dzen_comment_id"],
                publication_id=row["publication_id"],
                author=row["author"] or "",
                text=row["comment_text"] or "",
                parent_comment_id=row["parent_comment_id"],
                posted_at=row["posted_at"],
                fetched_at=row["fetched_at"],
                status=CommentStatus.GENERATING,
                publication_title=row["publication_title"] or "",
                thread_text=row["thread_text"] or "",
                post_url=row["post_url"],
            ),
            attempt_count=attempt_count,
            claim_token=claim_token,
        )

    def release_generation_claim(
        self, comment_id: int, *, claim_token: str
    ) -> None:
        with self._engine.begin() as conn:
            queue = conn.execute(
                select(ReplyGenerationQueueTable.attempt_count)
                .where(
                    ReplyGenerationQueueTable.comment_id == comment_id,
                    ReplyGenerationQueueTable.state == "claimed",
                    ReplyGenerationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if queue is None:
                return

            attempt_count = max(0, int(queue) - 1)
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(ReplyGenerationQueueTable.comment_id == comment_id)
                .values(
                    state="queued",
                    attempt_count=attempt_count,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == comment_id)
                .values(
                    status=(
                        CommentStatus.NEW.value
                        if attempt_count == 0
                        else CommentStatus.GENERATION_RETRY.value
                    )
                )
            )

    def complete_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        text: str,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        created_at: datetime,
        is_cta_candidate: bool,
    ) -> int:
        with self._engine.begin() as conn:
            queue = conn.execute(
                select(ReplyGenerationQueueTable.comment_id)
                .where(
                    ReplyGenerationQueueTable.comment_id == comment_id,
                    ReplyGenerationQueueTable.state == "claimed",
                    ReplyGenerationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).one_or_none()
            if queue is None:
                raise ValueError("Generation queue claim is no longer active")
            reply_id = conn.execute(
                insert(ReplyTable)
                .values(
                    comment_id=comment_id,
                    generated_text=text,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    status=ReplyStatus.GENERATED.value,
                    created_at=created_at,
                    article_context_status=article_context_status,
                    is_cta_candidate=is_cta_candidate,
                )
                .returning(ReplyTable.id)
            ).scalar_one()
            conn.execute(
                insert(ReplyPublicationQueueTable).values(
                    reply_id=reply_id,
                    state="queued",
                    attempt_count=0,
                    created_at=created_at,
                )
            )
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(ReplyGenerationQueueTable.comment_id == comment_id)
                .values(
                    state="completed",
                    next_attempt_at=None,
                    last_error=None,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == comment_id)
                .values(status=CommentStatus.GENERATED.value)
            )
            return reply_id

    def skip_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        reason: str,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        created_at: datetime,
    ) -> int:
        with self._engine.begin() as conn:
            queue = conn.execute(
                select(ReplyGenerationQueueTable.comment_id)
                .where(
                    ReplyGenerationQueueTable.comment_id == comment_id,
                    ReplyGenerationQueueTable.state == "claimed",
                    ReplyGenerationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).one_or_none()
            if queue is None:
                raise ValueError("Generation queue claim is no longer active")
            reply_id = conn.execute(
                insert(ReplyTable)
                .values(
                    comment_id=comment_id,
                    generated_text="",
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    status=ReplyStatus.SKIPPED.value,
                    error_reason=reason,
                    created_at=created_at,
                    article_context_status=article_context_status,
                    is_cta_candidate=False,
                )
                .returning(ReplyTable.id)
            ).scalar_one()
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(ReplyGenerationQueueTable.comment_id == comment_id)
                .values(
                    state="completed",
                    next_attempt_at=None,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == comment_id)
                .values(status=CommentStatus.SKIPPED.value)
            )
            return reply_id

    def fail_generation(
        self,
        comment_id: int,
        *,
        claim_token: str,
        error_reason: str,
        failed_at: datetime,
        ai_provider: str,
        ai_model: str,
        article_context_status: str,
        retry_cooldown_minutes: int,
        max_attempts_per_comment: int,
    ) -> GenerationFailureOutcome:
        with self._engine.begin() as conn:
            queue = conn.execute(
                select(
                    ReplyGenerationQueueTable.attempt_count.label("attempt_count")
                )
                .where(
                    ReplyGenerationQueueTable.comment_id == comment_id,
                    ReplyGenerationQueueTable.state == "claimed",
                    ReplyGenerationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if queue is None:
                raise ValueError("Generation queue claim is no longer active")

            retry = int(queue["attempt_count"]) < max_attempts_per_comment
            conn.execute(
                insert(ReplyTable).values(
                    comment_id=comment_id,
                    generated_text=None,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    status=ReplyStatus.ERROR.value,
                    error_reason=error_reason,
                    created_at=failed_at,
                    article_context_status=article_context_status,
                    is_cta_candidate=False,
                )
            )
            conn.execute(
                update(ReplyGenerationQueueTable)
                .where(ReplyGenerationQueueTable.comment_id == comment_id)
                .values(
                    state="queued" if retry else "completed",
                    next_attempt_at=(
                        failed_at + timedelta(minutes=retry_cooldown_minutes)
                        if retry
                        else None
                    ),
                    last_error=error_reason,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == comment_id)
                .values(
                    status=(
                        CommentStatus.GENERATION_RETRY.value
                        if retry
                        else CommentStatus.GENERATION_ERROR.value
                    )
                )
            )
            return (
                GenerationFailureOutcome.RETRY
                if retry
                else GenerationFailureOutcome.TERMINAL
            )

    def get_article_context(self, publication_id: int) -> ArticleContext | None:
        stmt = select(
            PublicationTable.id.label("publication_id"),
            PublicationTable.article_text.label("text"),
            PublicationTable.article_context_status.label("status"),
            PublicationTable.article_fetched_at.label("fetched_at"),
            PublicationTable.article_content_hash.label("content_hash"),
        ).where(PublicationTable.id == publication_id)
        with self._engine.begin() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        if row is None:
            return None
        return ArticleContext(
            publication_id=row["publication_id"],
            text=row["text"],
            status=row["status"],
            fetched_at=row["fetched_at"],
            content_hash=row["content_hash"],
        )

    def save_article_context(
        self,
        publication_id: int,
        *,
        text: str | None,
        status: str,
        fetched_at: datetime,
    ) -> ArticleContext:
        content_hash = sha256(text.encode("utf-8")).hexdigest() if text else None
        stmt = (
            update(PublicationTable)
            .where(PublicationTable.id == publication_id)
            .values(
                article_text=text,
                article_context_status=status,
                article_fetched_at=fetched_at,
                article_content_hash=content_hash,
            )
            .returning(
                PublicationTable.id.label("publication_id"),
                PublicationTable.article_text.label("text"),
                PublicationTable.article_context_status.label("status"),
                PublicationTable.article_fetched_at.label("fetched_at"),
                PublicationTable.article_content_hash.label("content_hash"),
            )
        )
        with self._engine.begin() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        if row is None:
            raise ValueError("Publication does not exist")
        return ArticleContext(
            publication_id=row["publication_id"],
            text=row["text"],
            status=row["status"],
            fetched_at=row["fetched_at"],
            content_hash=row["content_hash"],
        )

    def enqueue_publication(self, reply_id: int, *, created_at: datetime) -> bool:
        stmt = (
            insert(ReplyPublicationQueueTable)
            .from_select(
                ["reply_id", "state", "attempt_count", "created_at"],
                select(
                    ReplyTable.id,
                    literal("queued"),
                    literal(0),
                    literal(created_at),
                ).where(
                    ReplyTable.id == reply_id,
                    ReplyTable.status == ReplyStatus.GENERATED.value,
                ),
            )
            .on_conflict_do_nothing(
                index_elements=[ReplyPublicationQueueTable.reply_id]
            )
            .returning(ReplyPublicationQueueTable.reply_id)
        )
        with self._engine.begin() as conn:
            return conn.execute(stmt).scalar_one_or_none() is not None

    def expire_stale_publications(
        self,
        now: datetime,
        oldest_allowed_comment_fetched_at: datetime,
    ) -> int:
        """Complete queued replies whose source comments are too old to publish."""
        with self._engine.begin() as conn:
            reply_ids = conn.execute(
                select(ReplyPublicationQueueTable.reply_id)
                .join(ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id)
                .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
                .where(
                    ReplyPublicationQueueTable.state == "queued",
                    CommentTable.fetched_at < oldest_allowed_comment_fetched_at,
                )
                .with_for_update(skip_locked=True, of=ReplyPublicationQueueTable)
            ).scalars().all()
            if not reply_ids:
                return 0

            conn.execute(
                update(ReplyPublicationQueueTable)
                .where(
                    ReplyPublicationQueueTable.reply_id.in_(reply_ids),
                    ReplyPublicationQueueTable.state == "queued",
                )
                .values(state="completed", next_attempt_at=None)
            )
            conn.execute(
                update(ReplyTable)
                .where(ReplyTable.id.in_(reply_ids))
                .values(status=ReplyStatus.SKIPPED.value)
            )
            conn.execute(
                update(CommentTable)
                .where(
                    CommentTable.id.in_(
                        select(ReplyTable.comment_id).where(ReplyTable.id.in_(reply_ids))
                    )
                )
                .values(status=CommentStatus.SKIPPED.value)
            )
            return len(reply_ids)

    def claim_next_publication(self, now: datetime) -> ClaimedPublication | None:
        ready = (ReplyPublicationQueueTable.state == "queued") & (
            (ReplyPublicationQueueTable.next_attempt_at.is_(None))
            | (ReplyPublicationQueueTable.next_attempt_at <= now)
        )
        with self._engine.begin() as conn:
            self._lock_work_window(conn)
            expired_submitting = conn.execute(
                update(ReplyPublicationQueueTable)
                .where(
                    ReplyPublicationQueueTable.state == "submitting",
                    or_(
                        ReplyPublicationQueueTable.claimed_at.is_(None),
                        ReplyPublicationQueueTable.claimed_at
                        <= now - self._PUBLICATION_CLAIM_LEASE,
                    ),
                )
                .values(
                    state="completed",
                    next_attempt_at=None,
                    last_error="publication outcome unknown after worker interruption",
                    claimed_at=None,
                    claim_token=None,
                )
                .returning(ReplyPublicationQueueTable.reply_id)
            ).scalars().all()
            if expired_submitting:
                conn.execute(
                    update(ReplyTable)
                    .where(ReplyTable.id.in_(expired_submitting))
                    .values(
                        status=ReplyStatus.UNCONFIRMED.value,
                        error_reason="publication outcome unknown after worker interruption",
                    )
                )
                conn.execute(
                    update(CommentTable)
                    .where(
                        CommentTable.id.in_(
                            select(ReplyTable.comment_id).where(
                                ReplyTable.id.in_(expired_submitting)
                            )
                        )
                    )
                    .values(status=CommentStatus.PUBLICATION_UNCONFIRMED.value)
                )
            conn.execute(
                update(ReplyPublicationQueueTable)
                .where(
                    ReplyPublicationQueueTable.state == "claimed",
                    or_(
                        ReplyPublicationQueueTable.claimed_at.is_(None),
                        ReplyPublicationQueueTable.claimed_at
                        <= now - self._PUBLICATION_CLAIM_LEASE,
                    ),
                )
                .values(state="queued", claimed_at=None, claim_token=None)
            )
            row = conn.execute(
                select(
                    ReplyPublicationQueueTable.reply_id.label("reply_id"),
                    ReplyTable.generated_text.label("reply_text"),
                    CommentTable.id.label("comment_id"),
                    CommentTable.dzen_comment_id.label("dzen_comment_id"),
                    CommentTable.publication_id.label("publication_id"),
                    CommentTable.author.label("author"),
                    CommentTable.text.label("comment_text"),
                    CommentTable.parent_comment_id.label("parent_comment_id"),
                    CommentTable.posted_at.label("posted_at"),
                    CommentTable.fetched_at.label("fetched_at"),
                    CommentTable.status.label("comment_status"),
                    CommentTable.post_title.label("publication_title"),
                    CommentTable.thread_text.label("thread_text"),
                    CommentTable.post_url.label("post_url"),
                )
                .join(ReplyTable, ReplyTable.id == ReplyPublicationQueueTable.reply_id)
                .join(CommentTable, CommentTable.id == ReplyTable.comment_id)
                .where(ready)
                .order_by(
                    func.coalesce(
                        ReplyPublicationQueueTable.next_attempt_at,
                        ReplyPublicationQueueTable.created_at,
                    ),
                    ReplyPublicationQueueTable.created_at,
                    ReplyPublicationQueueTable.reply_id,
                )
                .limit(1)
                .with_for_update(skip_locked=True, of=ReplyPublicationQueueTable)
            ).mappings().one_or_none()
            if row is None:
                return None
            claim_token = uuid4().hex
            conn.execute(
                update(ReplyPublicationQueueTable)
                .where(ReplyPublicationQueueTable.reply_id == row["reply_id"])
                .values(
                    state="claimed",
                    claimed_at=now,
                    attempt_count=ReplyPublicationQueueTable.attempt_count + 1,
                    claim_token=claim_token,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(CommentTable.id == row["comment_id"])
                .values(status=CommentStatus.PUBLISHING.value)
            )
        return ClaimedPublication(
            reply_id=row["reply_id"],
            comment=Comment(
                id=row["comment_id"],
                dzen_comment_id=row["dzen_comment_id"],
                publication_id=row["publication_id"],
                author=row["author"] or "",
                text=row["comment_text"] or "",
                parent_comment_id=row["parent_comment_id"],
                posted_at=row["posted_at"],
                fetched_at=row["fetched_at"],
                status=CommentStatus(row["comment_status"]),
                publication_title=row["publication_title"] or "",
                thread_text=row["thread_text"] or "",
                post_url=row["post_url"],
            ),
            text=row["reply_text"] or "",
            claim_token=claim_token,
        )

    def mark_publication_submitting(self, reply_id: int, *, claim_token: str) -> None:
        with self._engine.begin() as conn:
            row = conn.execute(
                update(ReplyPublicationQueueTable)
                .where(
                    ReplyPublicationQueueTable.reply_id == reply_id,
                    ReplyPublicationQueueTable.state == "claimed",
                    ReplyPublicationQueueTable.claim_token == claim_token,
                )
                .values(state="submitting")
                .returning(ReplyPublicationQueueTable.reply_id)
            ).one_or_none()
            if row is None:
                raise ValueError("Publication queue claim is no longer active")

    def complete_publication(
        self,
        reply_id: int,
        *,
        claim_token: str,
        published_at: datetime | None,
    ) -> None:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(ReplyPublicationQueueTable.reply_id)
                .where(
                    ReplyPublicationQueueTable.reply_id == reply_id,
                    ReplyPublicationQueueTable.state.in_(("claimed", "submitting")),
                    ReplyPublicationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise ValueError("Publication queue claim is no longer active")
            status = (
                ReplyStatus.PUBLISHED.value
                if published_at is not None
                else ReplyStatus.GENERATED.value
            )
            conn.execute(
                update(ReplyTable)
                .where(ReplyTable.id == reply_id)
                .values(status=status, published_at=published_at)
            )
            conn.execute(
                update(ReplyPublicationQueueTable)
                .where(ReplyPublicationQueueTable.reply_id == reply_id)
                .values(
                    state="completed",
                    next_attempt_at=None,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(
                    CommentTable.id
                    == select(ReplyTable.comment_id)
                    .where(ReplyTable.id == reply_id)
                    .scalar_subquery()
                )
                .values(
                    status=(
                        CommentStatus.PUBLISHED.value
                        if published_at is not None
                        else CommentStatus.GENERATED.value
                    )
                )
            )

    def fail_publication(
        self,
        reply_id: int,
        *,
        claim_token: str,
        error_reason: str,
        failed_at: datetime,
        retry_cooldown_minutes: int,
        max_attempts_per_reply: int,
    ) -> PublicationFailureOutcome:
        with self._engine.begin() as conn:
            queue = conn.execute(
                select(
                    ReplyPublicationQueueTable.attempt_count.label("attempt_count")
                )
                .where(
                    ReplyPublicationQueueTable.reply_id == reply_id,
                    ReplyPublicationQueueTable.state.in_(("claimed", "submitting")),
                    ReplyPublicationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).mappings().one_or_none()
            if queue is None:
                raise ValueError("Publication queue claim is no longer active")
            retry = queue["attempt_count"] < max_attempts_per_reply
            conn.execute(
                update(ReplyPublicationQueueTable)
                .where(ReplyPublicationQueueTable.reply_id == reply_id)
                .values(
                    state="queued" if retry else "completed",
                    next_attempt_at=(
                        failed_at + timedelta(minutes=retry_cooldown_minutes)
                        if retry
                        else None
                    ),
                    last_error=error_reason,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            if not retry:
                conn.execute(
                    update(ReplyTable)
                    .where(ReplyTable.id == reply_id)
                    .values(status=ReplyStatus.ERROR.value, error_reason=error_reason)
                )
            conn.execute(
                update(CommentTable)
                .where(
                    CommentTable.id
                    == select(ReplyTable.comment_id)
                    .where(ReplyTable.id == reply_id)
                    .scalar_subquery()
                )
                .values(
                    status=(
                        CommentStatus.PUBLICATION_RETRY.value
                        if retry
                        else CommentStatus.PUBLICATION_ERROR.value
                    )
                )
            )
            return (
                PublicationFailureOutcome.RETRY
                if retry
                else PublicationFailureOutcome.TERMINAL
            )

    def mark_publication_unconfirmed(
        self,
        reply_id: int,
        *,
        claim_token: str,
        reason: str,
    ) -> None:
        with self._engine.begin() as conn:
            row = conn.execute(
                select(ReplyPublicationQueueTable.reply_id)
                .where(
                    ReplyPublicationQueueTable.reply_id == reply_id,
                    ReplyPublicationQueueTable.state.in_(("claimed", "submitting")),
                    ReplyPublicationQueueTable.claim_token == claim_token,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                raise ValueError("Publication queue claim is no longer active")
            conn.execute(
                update(ReplyTable)
                .where(ReplyTable.id == reply_id)
                .values(status=ReplyStatus.UNCONFIRMED.value, error_reason=reason)
            )
            conn.execute(
                update(ReplyPublicationQueueTable)
                .where(ReplyPublicationQueueTable.reply_id == reply_id)
                .values(
                    state="completed",
                    next_attempt_at=None,
                    last_error=reason,
                    claimed_at=None,
                    claim_token=None,
                )
            )
            conn.execute(
                update(CommentTable)
                .where(
                    CommentTable.id
                    == select(ReplyTable.comment_id)
                    .where(ReplyTable.id == reply_id)
                    .scalar_subquery()
                )
                .values(status=CommentStatus.PUBLICATION_UNCONFIRMED.value)
            )

    def count_cta_candidates_produced(self) -> int:
        stmt = select(func.count()).select_from(ReplyTable).where(
            ReplyTable.status.in_(
                [
                    ReplyStatus.GENERATED.value,
                    ReplyStatus.PUBLISHED.value,
                    ReplyStatus.UNCONFIRMED.value,
                ]
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
                    [
                        ReplyStatus.GENERATED.value,
                        ReplyStatus.PUBLISHED.value,
                        ReplyStatus.UNCONFIRMED.value,
                    ]
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
