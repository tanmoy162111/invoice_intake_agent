"""OAuth helpers for connecting an accounting system: signed expiring state bound to the person who
started it, a PKCE verifier derived from it, and strict checks of what comes back."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from intake.core.oauth import (
    CallbackError,
    CallbackOk,
    authorize_url,
    check_callback,
    make_state,
    pkce_challenge,
    pkce_verifier,
)

SECRET = "s" * 32
NOW = 1_800_000_000


def test_state_is_random_per_call_and_signed() -> None:
    a, b = make_state(SECRET, "reviewer", NOW), make_state(SECRET, "reviewer", NOW)
    assert a != b and a.startswith("o1.")


def test_a_valid_callback_returns_the_code_and_the_verifier() -> None:
    state = make_state(SECRET, "reviewer", NOW)
    got = check_callback({"code": "abc", "state": state}, SECRET, NOW + 5, "reviewer")
    assert got == CallbackOk(code="abc", verifier=pkce_verifier(SECRET, state))


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"error": "access_denied", "state": "x"}, CallbackError.DENIED),
        ({"state": "x"}, CallbackError.MISSING),
        ({"code": "abc"}, CallbackError.MISSING),
        ({"code": "", "state": "x"}, CallbackError.MISSING),
        ({"code": "abc", "state": "garbage"}, CallbackError.BAD_STATE),
        ({"code": "abc", "state": "o1.e30.AAAA"}, CallbackError.BAD_STATE),
    ],
)
def test_a_callback_that_is_not_ours_is_refused(
    params: dict[str, str], reason: CallbackError
) -> None:
    assert check_callback(params, SECRET, NOW, "reviewer") is reason


def test_state_signed_with_another_secret_is_refused() -> None:
    state = make_state("x" * 32, "reviewer", NOW)
    assert (
        check_callback({"code": "c", "state": state}, SECRET, NOW, "reviewer")
        is CallbackError.BAD_STATE
    )


def test_state_expires() -> None:
    state = make_state(SECRET, "reviewer", NOW, ttl_s=600)
    assert (
        check_callback({"code": "c", "state": state}, SECRET, NOW + 601, "reviewer")
        is CallbackError.EXPIRED
    )


def test_state_cannot_be_used_by_someone_else() -> None:
    state = make_state(SECRET, "reviewer", NOW)
    assert (
        check_callback({"code": "c", "state": state}, SECRET, NOW, "mallory")
        is CallbackError.WRONG_USER
    )


def test_a_session_token_is_not_accepted_as_state() -> None:
    from intake.core.session import sign_session

    token = sign_session(SECRET, "reviewer", NOW, 600)
    assert (
        check_callback({"code": "c", "state": token}, SECRET, NOW, "reviewer")
        is CallbackError.BAD_STATE
    )


def test_no_secret_means_no_state() -> None:
    with pytest.raises(ValueError):
        make_state("", "reviewer", NOW)
    assert (
        check_callback({"code": "c", "state": "o1.a.b"}, "", NOW, "reviewer")
        is CallbackError.BAD_STATE
    )


def test_pkce_challenge_is_s256_of_the_verifier() -> None:
    v = pkce_verifier(SECRET, "o1.x.y")
    assert 43 <= len(v) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    assert pkce_challenge(v) == expected
    assert pkce_verifier(SECRET, "o1.x.z") != v


def test_authorize_url_carries_state_challenge_and_only_the_scopes_asked_for() -> None:
    url = authorize_url(
        "https://login.example/authorize", "client-1", "https://app.example/cb",
        ["offline_access", "accounting.contacts.read"], "STATE", "CHAL",
    )  # fmt: skip
    q = parse_qs(urlsplit(url).query)
    assert q["response_type"] == ["code"] and q["state"] == ["STATE"]
    assert q["code_challenge"] == ["CHAL"] and q["code_challenge_method"] == ["S256"]
    assert q["scope"] == ["offline_access accounting.contacts.read"]
    assert q["redirect_uri"] == ["https://app.example/cb"]
