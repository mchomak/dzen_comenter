from dataclasses import FrozenInstanceError

import pytest

from dzen_commenter.contracts.interfaces import CommentRepository
from dzen_commenter.contracts.models import WorkWindowSnapshot


def test_work_window_snapshot_is_immutable_and_has_the_public_fields():
    snapshot = WorkWindowSnapshot(
        active_generation=1,
        active_publication=2,
        waiting_generation=3,
        waiting_publication=4,
        generated_waiting_publication=4,
        expired_generation=5,
        expired_publication=6,
    )

    assert set(WorkWindowSnapshot.__dataclass_fields__) == {
        "active_generation",
        "active_publication",
        "waiting_generation",
        "waiting_publication",
        "generated_waiting_publication",
        "expired_generation",
        "expired_publication",
    }
    with pytest.raises(FrozenInstanceError):
        snapshot.active_generation = 0
    assert callable(getattr(CommentRepository, "reconcile_work_window"))
    assert callable(getattr(CommentRepository, "admit_recent_comments"))
