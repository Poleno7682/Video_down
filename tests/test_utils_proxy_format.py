from __future__ import annotations

from app.utils.proxy_format import parse_proxy_input


def test_ip_port():
    assert parse_proxy_input("1.2.3.4:1080", "socks5h") == "socks5h://1.2.3.4:1080"


def test_ip_port_at_login_password():
    assert (
        parse_proxy_input("1.2.3.4:1080@user:pass", "socks5h")
        == "socks5h://user:pass@1.2.3.4:1080"
    )


def test_ip_port_colon_login_password():
    assert (
        parse_proxy_input("1.2.3.4:1080:user:pass", "https")
        == "https://user:pass@1.2.3.4:1080"
    )


def test_ip_port_semicolon_login_password():
    assert (
        parse_proxy_input("1.2.3.4:1080;user:pass", "socks5h")
        == "socks5h://user:pass@1.2.3.4:1080"
    )


def test_hostname_instead_of_ip():
    assert parse_proxy_input("proxy.example.com:8080", "https") == "https://proxy.example.com:8080"


def test_already_schemed_url_passthrough():
    assert (
        parse_proxy_input("socks5h://user:pass@host:1080", "https")
        == "socks5h://user:pass@host:1080"
    )


def test_unknown_scheme_url_rejected():
    assert parse_proxy_input("ftp://host:21", "https") is None


def test_garbage_input_rejected():
    assert parse_proxy_input("not a proxy", "socks5h") is None


def test_missing_port_rejected():
    assert parse_proxy_input("1.2.3.4", "socks5h") is None


def test_too_many_parts_rejected():
    assert parse_proxy_input("1.2.3.4:1080:user:pass:extra", "socks5h") is None


def test_whitespace_trimmed():
    assert parse_proxy_input("  1.2.3.4:1080  ", "socks5h") == "socks5h://1.2.3.4:1080"
