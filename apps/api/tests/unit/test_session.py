import base64
import json

import pytest

from intake.core.session import LoginThrottle, sign_session, verify_session

SECRET = "s3cret-key-for-tests"
NOW = 1_800_000_000


def test_a_signed_session_verifies_for_its_user_until_it_expires() -> None:
    token = sign_session(SECRET, "reviewer", NOW, ttl_s=3600)
    assert verify_session(SECRET, token, NOW) == "reviewer"
    assert verify_session(SECRET, token, NOW + 3599) == "reviewer"


def test_a_session_is_expired_at_its_expiry_time() -> None:
    token = sign_session(SECRET, "reviewer", NOW, ttl_s=3600)
    assert verify_session(SECRET, token, NOW + 3600) is None
    assert verify_session(SECRET, token, NOW + 10**6) is None


def test_a_session_signed_with_another_secret_is_refused() -> None:
    token = sign_session(SECRET, "reviewer", NOW, ttl_s=60)
    assert verify_session("another-secret", token, NOW) is None


def _parts(token: str) -> list[str]:
    return token.split(".")


def _b64(obj: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def test_a_tampered_payload_is_refused() -> None:
    token = sign_session(SECRET, "reviewer", NOW, ttl_s=60)
    version, _payload, sig = _parts(token)
    forged = ".".join([version, _b64({"sub": "admin", "exp": NOW + 10**6}), sig])
    assert verify_session(SECRET, forged, NOW) is None


@pytest.mark.parametrize(
    "token",
    ["", "x", "a.b", "a.b.c.d", "v1..", "v1.!!!.???", "v2.abc.def", "v1.é.é", " ", "Bearer x"],
)
def test_malformed_tokens_are_refused_without_error(token: str) -> None:
    assert verify_session(SECRET, token, NOW) is None


def test_a_correctly_signed_but_odd_payload_is_refused() -> None:
    from intake.core.session import _sign  # the signature is right; the content is not

    for junk in ("!!!!", _b64("not json")[:-2], "bm90IGpzb24"):  # signed, but not JSON
        token = f"v1.{junk}.{_sign(SECRET, junk)}"
        assert verify_session(SECRET, token, NOW) is None, junk
    for payload in (
        {"sub": "", "exp": NOW + 60},
        {"sub": 5, "exp": NOW + 60},
        {"sub": "reviewer", "exp": "soon"},
        {"sub": "reviewer", "exp": True},
        {"sub": "reviewer"},
        ["reviewer"],
    ):
        body = _b64(payload)
        token = f"v1.{body}.{_sign(SECRET, body)}"
        assert verify_session(SECRET, token, NOW) is None, payload


def test_an_empty_secret_cannot_sign_or_verify() -> None:
    with pytest.raises(ValueError):
        sign_session("", "reviewer", NOW, ttl_s=60)
    token = sign_session(SECRET, "reviewer", NOW, ttl_s=60)
    assert verify_session("", token, NOW) is None


def test_a_session_needs_a_user_and_a_positive_lifetime() -> None:
    with pytest.raises(ValueError):
        sign_session(SECRET, "", NOW, ttl_s=60)
    with pytest.raises(ValueError):
        sign_session(SECRET, "reviewer", NOW, ttl_s=0)


# ---- login throttle ---------------------------------------------------------------------------


def test_the_throttle_blocks_after_too_many_failures_and_recovers() -> None:
    t = LoginThrottle(max_failures=3, window_s=60)
    assert t.allowed(NOW)
    for i in range(3):
        t.record_failure(NOW + i)
    assert not t.allowed(NOW + 3)
    assert t.allowed(NOW + 61 + 2)  # the failures have aged out of the window


def test_a_success_clears_the_failures() -> None:
    t = LoginThrottle(max_failures=2, window_s=60)
    t.record_failure(NOW)
    t.record_success()
    t.record_failure(NOW + 1)
    assert t.allowed(NOW + 2)
