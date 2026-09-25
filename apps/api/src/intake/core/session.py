"""Signed reviewer sessions and a login throttle. Pure: the clock is always passed in.

A session token is `v1.<payload>.<signature>`: the payload is base64url JSON `{"sub", "exp"}` and
the signature is HMAC-SHA256 over `v1.<payload>`. Anything malformed, badly signed or expired is
simply `None`; verification never raises on hostile input.
"""

import base64
import binascii
import hashlib
import hmac
import json
from collections import deque

_VERSION = "v1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload_b64: str) -> str:
    digest = hmac.new(secret.encode(), f"{_VERSION}.{payload_b64}".encode(), hashlib.sha256)
    return _b64(digest.digest())


def sign_session(secret: str, user: str, now: int, ttl_s: int) -> str:
    if not secret:
        raise ValueError("a session secret is required")
    if not user or ttl_s <= 0:
        raise ValueError("a session needs a user and a positive lifetime")
    payload = _b64(json.dumps({"sub": user, "exp": now + ttl_s}, separators=(",", ":")).encode())
    return f"{_VERSION}.{payload}.{_sign(secret, payload)}"


def verify_session(secret: str, token: str, now: int) -> str | None:
    """The signed-in user, or None for anything that is not a valid, unexpired session."""
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _VERSION:
        return None
    _, payload, signature = parts
    try:
        expected = _sign(secret, payload)
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            return None
        data = json.loads(_unb64(payload))
    except (ValueError, UnicodeError, binascii.Error):
        return None
    if not isinstance(data, dict):
        return None
    user, expires = data.get("sub"), data.get("exp")
    if not isinstance(user, str) or not user:
        return None
    if isinstance(expires, bool) or not isinstance(expires, int) or expires <= now:
        return None
    return user


class LoginThrottle:
    """Refuses logins for a while after too many failures (guessing a password takes many tries)."""

    def __init__(self, max_failures: int = 5, window_s: int = 60) -> None:
        self.max_failures, self.window_s = max_failures, window_s
        self._failures: deque[int] = deque()

    def _forget_old(self, now: int) -> None:
        while self._failures and self._failures[0] <= now - self.window_s:
            self._failures.popleft()

    def allowed(self, now: int) -> bool:
        self._forget_old(now)
        return len(self._failures) < self.max_failures

    def record_failure(self, now: int) -> None:
        self._failures.append(now)

    def record_success(self) -> None:
        self._failures.clear()
