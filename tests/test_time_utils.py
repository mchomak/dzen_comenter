from datetime import datetime
from zoneinfo import ZoneInfo

from dzen_commenter.time_utils import moscow_now


def test_moscow_now_returns_naive_moscow_wall_clock(monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == ZoneInfo("Europe/Moscow")
            return cls(2026, 1, 2, 3, 4, 5, tzinfo=tz)

    monkeypatch.setattr("dzen_commenter.time_utils.datetime", FixedDatetime)

    assert moscow_now() == datetime(2026, 1, 2, 3, 4, 5)
