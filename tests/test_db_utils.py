from datetime import datetime, timezone

from app.db.utils import utcnow


def test_utcnow_returns_datetime():
    result = utcnow()
    assert isinstance(result, datetime)


def test_utcnow_is_utc():
    result = utcnow()
    assert result.tzinfo is not None
    assert result.utcoffset().total_seconds() == 0


def test_utcnow_is_recent():
    before = datetime.now(timezone.utc)
    result = utcnow()
    after = datetime.now(timezone.utc)
    assert before <= result <= after
