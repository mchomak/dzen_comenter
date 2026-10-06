from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, select
from sqlalchemy.pool import StaticPool

from dzen_commenter.db.models import (
    Base,
    CommentTable,
    PublicationTable,
    ReplyPublicationQueueTable,
    ReplyTable,
)
from dzen_commenter.db.repository import PostgresCommentRepository


def test_unconfirmed_transition_closes_job_and_rejects_stale_token():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 5, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(insert(PublicationTable).values(id=1, dzen_publication_id="post"))
        conn.execute(
            insert(CommentTable).values(
                id=1,
                dzen_comment_id="source",
                publication_id=1,
                status="publishing",
            )
        )
        conn.execute(
            insert(ReplyTable).values(
                id=1,
                comment_id=1,
                generated_text="saved reply",
                status="generated",
            )
        )
        conn.execute(
            insert(ReplyPublicationQueueTable).values(
                reply_id=1,
                state="claimed",
                attempt_count=1,
                created_at=now,
                claimed_at=now,
                claim_token="active",
            )
        )

    repository = PostgresCommentRepository(engine)
    with pytest.raises(ValueError, match="claim"):
        repository.mark_publication_unconfirmed(
            1, claim_token="stale", reason="stale update"
        )
    repository.mark_publication_unconfirmed(
        1, claim_token="active", reason="public confirmation timed out"
    )

    with engine.connect() as conn:
        reply = conn.execute(
            select(ReplyTable.status, ReplyTable.published_at, ReplyTable.error_reason)
        ).one()
        comment_status = conn.execute(select(CommentTable.status)).scalar_one()
        queue = conn.execute(
            select(
                ReplyPublicationQueueTable.state,
                ReplyPublicationQueueTable.next_attempt_at,
                ReplyPublicationQueueTable.last_error,
                ReplyPublicationQueueTable.claim_token,
            )
        ).one()
    assert reply == ("unconfirmed", None, "public confirmation timed out")
    assert comment_status == "publication_unconfirmed"
    assert queue == ("completed", None, "public confirmation timed out", None)
    assert repository.claim_next_publication(now + timedelta(hours=3)) is None
