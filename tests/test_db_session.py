from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_get_session_calls_session_local():
    mock_session = MagicMock()
    with patch("app.db.session.SessionLocal", return_value=mock_session) as mock_sl:
        from app.db.session import get_session
        result = get_session()
    mock_sl.assert_called_once()
    assert result is mock_session


class TestScopedRepository:
    def _make_session(self):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        return session

    def test_delegates_call_to_repository_method(self):
        from app.db.session import ScopedRepository

        session = self._make_session()
        mock_repo_instance = MagicMock()
        mock_repo_instance.get_request.return_value = "the-request"

        with patch("app.db.session.get_session", return_value=session), \
             patch("app.db.session.Repository", return_value=mock_repo_instance) as mock_repo_cls:
            result = ScopedRepository().get_request(42)

        mock_repo_cls.assert_called_once_with(session)
        mock_repo_instance.get_request.assert_called_once_with(42)
        assert result == "the-request"

    def test_each_call_opens_and_closes_its_own_session(self):
        """The whole point: consecutive calls must NOT share one session —
        each gets its own, so a long gap between calls (e.g. minutes of
        ffmpeg work) can't leave one connection idle the entire time."""
        from app.db.session import ScopedRepository

        sessions = [self._make_session(), self._make_session()]

        with patch("app.db.session.get_session", side_effect=sessions), \
             patch("app.db.session.Repository", return_value=MagicMock()):
            repo = ScopedRepository()
            repo.get_request(1)
            repo.update_request_status(1, "done")

        for session in sessions:
            session.__enter__.assert_called_once()
            session.__exit__.assert_called_once()

    def test_propagates_exception_from_underlying_call(self):
        from app.db.session import ScopedRepository

        session = self._make_session()
        mock_repo_instance = MagicMock()
        mock_repo_instance.get_request.side_effect = RuntimeError("db blip")

        with patch("app.db.session.get_session", return_value=session), \
             patch("app.db.session.Repository", return_value=mock_repo_instance):
            with pytest.raises(RuntimeError, match="db blip"):
                ScopedRepository().get_request(1)

        # The session context manager must still have been exited (closed),
        # which is what triggers SQLAlchemy's implicit rollback-on-close.
        session.__exit__.assert_called_once()
