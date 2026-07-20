from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.utils.proxy_check import ProxyCheckError, check_proxy


def test_check_proxy_passes_when_extract_succeeds():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.return_value = {"title": "Me at the zoo"}
    with patch("app.utils.proxy_check.YoutubeDL", return_value=mock_ydl) as mock_cls:
        check_proxy("socks5h://good:1080")
    opts_used = mock_cls.call_args[0][0]
    assert opts_used["proxy"] == "socks5h://good:1080"


def test_check_proxy_raises_on_anti_bot_block():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = Exception("Sign in to confirm you're not a bot")
    with patch("app.utils.proxy_check.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(ProxyCheckError, match="заблокировал"):
            check_proxy("socks5h://blocked:1080")


def test_check_proxy_raises_on_scheme_mismatch():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = Exception(
        "[Errno 0] Invalid response version from server. Expected 05 got 48"
    )
    with patch("app.utils.proxy_check.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(ProxyCheckError, match="не SOCKS5"):
            check_proxy("socks5h://actually-http:1080")


def test_check_proxy_raises_on_generic_failure():
    mock_ydl = MagicMock()
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)
    mock_ydl.extract_info.side_effect = Exception("Connection refused")
    with patch("app.utils.proxy_check.YoutubeDL", return_value=mock_ydl):
        with pytest.raises(ProxyCheckError, match="не отвечает"):
            check_proxy("socks5h://dead:1080")
