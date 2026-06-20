import pytest

from app.utils.url_tools import (
    domain_name,
    extract_url,
    is_valid_url,
    normalize_url,
    url_hash,
)


class TestExtractUrl:
    def test_plain_url(self):
        assert extract_url("Check this https://youtube.com/watch?v=abc") == "https://youtube.com/watch?v=abc"

    def test_no_url(self):
        assert extract_url("hello world") is None

    def test_empty_string(self):
        assert extract_url("") is None

    def test_strips_trailing_punctuation(self):
        assert extract_url("See https://example.com.") == "https://example.com"
        assert extract_url("(https://example.com)") == "https://example.com"
        assert extract_url("https://example.com,") == "https://example.com"
        assert extract_url("https://example.com;") == "https://example.com"
        assert extract_url("https://example.com]") == "https://example.com"

    def test_http_url(self):
        assert extract_url("http://example.com") == "http://example.com"

    def test_url_in_sentence(self):
        result = extract_url("Visit https://example.com for more")
        assert result == "https://example.com"


class TestNormalizeUrl:
    def test_removes_utm_params(self):
        url = "https://example.com/page?utm_source=email&id=1"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "id=1" in result

    def test_removes_tracking_params(self):
        url = "https://example.com/?fbclid=abc&igshid=xyz&si=123&feature=share"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "igshid" not in result
        assert "si" not in result
        assert "feature" not in result

    def test_lowercases_scheme_and_host(self):
        result = normalize_url("HTTPS://EXAMPLE.COM/path")
        assert result.startswith("https://example.com")

    def test_removes_fragment(self):
        result = normalize_url("https://example.com/page#section")
        assert "#" not in result

    def test_preserves_non_tracking_params(self):
        result = normalize_url("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert "v=dQw4w9WgXcQ" in result


class TestIsValidUrl:
    def test_http_valid(self):
        assert is_valid_url("http://example.com") is True

    def test_https_valid(self):
        assert is_valid_url("https://example.com/path?q=1") is True

    def test_ftp_invalid(self):
        assert is_valid_url("ftp://example.com") is False

    def test_no_netloc_invalid(self):
        assert is_valid_url("https://") is False

    def test_empty_invalid(self):
        assert is_valid_url("") is False


class TestUrlHash:
    def test_same_url_same_hash(self):
        assert url_hash("https://example.com/") == url_hash("https://example.com/")

    def test_different_urls_different_hashes(self):
        assert url_hash("https://example.com/a") != url_hash("https://example.com/b")

    def test_hash_is_64_chars(self):
        assert len(url_hash("https://example.com/")) == 64

    def test_normalizes_before_hashing(self):
        h1 = url_hash("https://example.com/?utm_source=x")
        h2 = url_hash("https://example.com/")
        assert h1 == h2


class TestDomainName:
    def test_basic_domain(self):
        assert domain_name("https://example.com/path") == "example.com"

    def test_strips_www(self):
        assert domain_name("https://www.youtube.com/watch") == "youtube.com"

    def test_subdomain_preserved(self):
        assert domain_name("https://music.youtube.com/") == "music.youtube.com"

    def test_lowercase(self):
        assert domain_name("https://EXAMPLE.COM/") == "example.com"
