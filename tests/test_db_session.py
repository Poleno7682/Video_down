from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_get_session_calls_session_local():
    mock_session = MagicMock()
    with patch("app.db.session.SessionLocal", return_value=mock_session) as mock_sl:
        from app.db.session import get_session
        result = get_session()
    mock_sl.assert_called_once()
    assert result is mock_session
