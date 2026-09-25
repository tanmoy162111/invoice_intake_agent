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


def test_a_client_is_blocked_after_too_many_attempts_and_recovers() -> None:
    t = LoginThrottle(max_attempts=3, window_s=60)
    assert [t.attempt("1.2.3.4", NOW + i) for i in range(4)] == [True, True, True, False]
    assert not t.attempt("1.2.3.4", NOW + 30)
    assert t.attempt("1.2.3.4", NOW + 61 + 2)  # the attempts have aged out of the window


def test_one_clients_failures_do_not_lock_out_another() -> None:
    t = LoginThrottle(max_attempts=2, window_s=60)
    assert (
        t.attempt("attacker", NOW) and t.attempt("attacker", NOW) and not t.attempt("attacker", NOW)
    )
    assert t.attempt("reviewer-pc", NOW)


def test_a_good_login_clears_that_clients_attempts() -> None:
    t = LoginThrottle(max_attempts=2, window_s=60)
    assert t.attempt("c", NOW) and t.attempt("c", NOW)
    t.succeeded("c")
    assert t.attempt("c", NOW + 1)


def test_a_global_cap_stops_many_clients_together() -> None:
    t = LoginThrottle(max_attempts=5, window_s=60, global_max=10)
    allowed = [t.attempt(f"client-{i}", NOW) for i in range(20)]
    assert allowed.count(True) == 10 and not any(allowed[10:])


def test_the_throttle_holds_under_parallel_requests() -> None:
    import threading

    t = LoginThrottle(max_attempts=5, window_s=60, global_max=1000)
    results: list[bool] = []
    lock = threading.Lock()

    def guess() -> None:
        ok = t.attempt("one-client", NOW)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=guess) for _ in range(200)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count(True) == 5  # not one more, however many arrive at once


def test_the_retry_after_time_is_reported() -> None:
    t = LoginThrottle(max_attempts=1, window_s=60)
    assert t.attempt("c", NOW) and not t.attempt("c", NOW + 10)
    assert t.retry_after("c", NOW + 10) == 50
    assert t.retry_after("someone-else", NOW + 10) == 0


def test_the_retry_time_covers_the_global_cap_too() -> None:
    t = LoginThrottle(max_attempts=5, window_s=60, global_max=2)
    assert t.attempt("a", NOW) and t.attempt("b", NOW) and not t.attempt("c", NOW + 5)
    assert t.retry_after("c", NOW + 5) == 55
