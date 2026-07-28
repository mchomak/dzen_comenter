from datetime import datetime
from zoneinfo import ZoneInfo


def moscow_now() -> datetime:
    return datetime.now(ZoneInfo("Europe/Moscow")).replace(tzinfo=None)
