import pytest

from backend.app.web_retrieval import WebSecurityError, validate_web_url


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/page",
        "https://user:secret@example.com/page",
        "https://localhost/page",
        "https://127.0.0.1/page",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_web_url_security_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises(WebSecurityError):
        validate_web_url(url, ("example.com",))


def test_web_url_security_accepts_https_allowlisted_subdomain(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.web_retrieval.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    normalized = validate_web_url("https://docs.example.com/guide", ("example.com",))

    assert normalized == "https://docs.example.com/guide"


def test_web_url_security_rejects_domain_suffix_confusion(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.web_retrieval.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    with pytest.raises(WebSecurityError):
        validate_web_url("https://example.com.attacker.test/page", ("example.com",))
