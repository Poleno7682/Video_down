from __future__ import annotations

from app.utils.magnet import extract_magnet_uri, is_magnet_uri


# ---------------------------------------------------------------------------
# extract_magnet_uri
# ---------------------------------------------------------------------------

def test_extract_magnet_uri_bare():
    text = "magnet:?xt=urn:btih:1234567890ABCDEF1234567890ABCDEF12345678"
    assert extract_magnet_uri(text) == text


def test_extract_magnet_uri_embedded_in_sentence():
    text = "Check this out: magnet:?xt=urn:btih:1234567890ABCDEF and enjoy"
    assert extract_magnet_uri(text) == "magnet:?xt=urn:btih:1234567890ABCDEF"


def test_extract_magnet_uri_preserves_optional_params():
    text = "magnet:?xt=urn:btih:HASH1234&dn=Some+Movie&tr=udp://tracker.example:80"
    assert extract_magnet_uri(text) == text


def test_extract_magnet_uri_strips_trailing_paren_and_dot():
    text = "(magnet:?xt=urn:btih:1234567890ABCDEF)."
    assert extract_magnet_uri(text) == "magnet:?xt=urn:btih:1234567890ABCDEF"


def test_extract_magnet_uri_strips_trailing_comma():
    text = "see magnet:?xt=urn:btih:1234567890ABCDEF, thanks"
    assert extract_magnet_uri(text) == "magnet:?xt=urn:btih:1234567890ABCDEF"


def test_extract_magnet_uri_strips_trailing_semicolon():
    text = "see magnet:?xt=urn:btih:1234567890ABCDEF; thanks"
    assert extract_magnet_uri(text) == "magnet:?xt=urn:btih:1234567890ABCDEF"


def test_extract_magnet_uri_strips_trailing_bracket():
    text = "[magnet:?xt=urn:btih:1234567890ABCDEF]"
    assert extract_magnet_uri(text) == "magnet:?xt=urn:btih:1234567890ABCDEF"


def test_extract_magnet_uri_case_insensitive_scheme():
    text = "MAGNET:?XT=urn:btih:abc123"
    assert extract_magnet_uri(text) == "MAGNET:?XT=urn:btih:abc123"


def test_extract_magnet_uri_no_match_returns_none():
    assert extract_magnet_uri("just some random text") is None


def test_extract_magnet_uri_empty_string_returns_none():
    assert extract_magnet_uri("") is None


def test_extract_magnet_uri_none_input_returns_none():
    assert extract_magnet_uri(None) is None


def test_extract_magnet_uri_ignores_non_magnet_urls():
    assert extract_magnet_uri("https://example.com/torrent.magnet") is None


def test_extract_magnet_uri_requires_btih():
    assert extract_magnet_uri("magnet:?dn=NoHashHere") is None


def test_extract_magnet_uri_picks_first_of_multiple():
    text = "magnet:?xt=urn:btih:FIRST111 then magnet:?xt=urn:btih:SECOND222"
    assert extract_magnet_uri(text) == "magnet:?xt=urn:btih:FIRST111"


# ---------------------------------------------------------------------------
# is_magnet_uri
# ---------------------------------------------------------------------------

def test_is_magnet_uri_true_for_bare_link():
    assert is_magnet_uri("magnet:?xt=urn:btih:1234567890ABCDEF") is True


def test_is_magnet_uri_true_when_embedded():
    assert is_magnet_uri("here it is: magnet:?xt=urn:btih:1234567890ABCDEF thanks") is True


def test_is_magnet_uri_false_for_plain_text():
    assert is_magnet_uri("hello, how are you?") is False


def test_is_magnet_uri_false_for_empty_string():
    assert is_magnet_uri("") is False
