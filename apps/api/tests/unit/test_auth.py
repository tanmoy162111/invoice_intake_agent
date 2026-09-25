import pytest
from pydantic import ValidationError

from intake.api.auth import Caller, credentials_match, resolve_caller
from intake.config import Settings
from intake.core.session import sign_session

NOW = 1_800_000_000


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {"api_token": "api-token", "session_secret": "sess-secret"}
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def bearer(token: str) -> str:
    return f"Bearer {token}"


def test_the_static_token_is_a_service_caller() -> None:
    assert resolve_caller(settings(), bearer("api-token"), NOW) == Caller("api", None)


def test_a_valid_session_is_a_named_user() -> None:
    token = sign_session("sess-secret", "reviewer", NOW, 3600)
    assert resolve_caller(settings(), bearer(token), NOW) == Caller("user", "reviewer")


def test_an_expired_or_forged_session_is_refused() -> None:
    old = sign_session("sess-secret", "reviewer", NOW - 7200, 3600)
    forged = sign_session("other-secret", "reviewer", NOW, 3600)
    for token in (old, forged, "v1.x.y", "nonsense"):
        assert resolve_caller(settings(), bearer(token), NOW) == (401, "missing or invalid token")


@pytest.mark.parametrize("header", [None, "", "Bearer", "Basic api-token", "api-token", "Bearer  "])
def test_a_missing_or_malformed_header_is_refused(header: str | None) -> None:
    assert resolve_caller(settings(), header, NOW) == (401, "missing or invalid token")


def test_a_non_ascii_header_is_refused_without_error() -> None:
    assert resolve_caller(settings(), "Bearer tökén-ü", NOW) == (401, "missing or invalid token")


def test_it_fails_closed_when_nothing_is_configured() -> None:
    nothing = settings(api_token="", session_secret="")
    assert resolve_caller(nothing, bearer("x"), NOW)[0] == 503  # type: ignore[index]


def test_sessions_work_without_the_static_token_and_the_reverse() -> None:
    only_session = settings(api_token="")
    token = sign_session("sess-secret", "reviewer", NOW, 60)
    assert resolve_caller(only_session, bearer(token), NOW) == Caller("user", "reviewer")
    assert resolve_caller(only_session, bearer("api-token"), NOW) == (
        401,
        "missing or invalid token",
    )
    only_token = settings(session_secret="")
    assert resolve_caller(only_token, bearer("api-token"), NOW) == Caller("api", None)
    assert resolve_caller(only_token, bearer(token), NOW) == (401, "missing or invalid token")


# ---- the reviewer login -----------------------------------------------------------------------


def test_the_reviewer_login_needs_both_the_name_and_the_password() -> None:
    s = settings(reviewer_username="reviewer", reviewer_password="correct horse")
    assert credentials_match(s, "reviewer", "correct horse")
    assert not credentials_match(s, "reviewer", "wrong")
    assert not credentials_match(s, "someone", "correct horse")
    assert not credentials_match(s, "", "")


def test_no_reviewer_password_means_nobody_can_log_in() -> None:
    s = settings(reviewer_password="")
    assert not credentials_match(s, "reviewer", "")
    assert not credentials_match(s, "reviewer", "anything")


def test_non_ascii_credentials_do_not_break_the_comparison() -> None:
    s = settings(reviewer_password="pässwörd")
    assert credentials_match(s, "reviewer", "pässwörd")
    assert not credentials_match(s, "reviewer", "passwörd")


def test_the_session_lifetime_must_be_at_least_a_minute() -> None:
    assert settings().session_ttl_s == 8 * 3600
    with pytest.raises(ValidationError):
        Settings(session_ttl_s=10)
