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
import threading
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
    """Limits login attempts per client and overall (guessing a password takes many tries).

    `attempt` reserves a slot before the password is checked, atomically, so a burst of parallel
    guesses cannot exceed the limit. A good login gives its client's attempts back."""

    def __init__(self, max_attempts: int = 5, window_s: int = 60, global_max: int = 50) -> None:
        self.max_attempts, self.window_s, self.global_max = max_attempts, window_s, global_max
        self._by_client: dict[str, deque[int]] = {}
        self._all: deque[int] = deque()
        self._lock = threading.Lock()

    def _forget_old(self, now: int) -> None:
        cutoff = now - self.window_s
        while self._all and self._all[0] <= cutoff:
            self._all.popleft()
        for key in list(self._by_client):
            recent = self._by_client[key]
            while recent and recent[0] <= cutoff:
                recent.popleft()
            if not recent:
                del self._by_client[key]

    def attempt(self, client: str, now: int) -> bool:
        with self._lock:
            self._forget_old(now)
            mine = self._by_client.setdefault(client, deque())
            if len(mine) >= self.max_attempts or len(self._all) >= self.global_max:
                if not mine:
                    del self._by_client[client]
                return False
            mine.append(now)
            self._all.append(now)
            return True

    def succeeded(self, client: str) -> None:
        with self._lock:
            self._by_client.pop(client, None)

    def retry_after(self, client: str, now: int) -> int:
        """Seconds until this client may try again (0 when it may now)."""
        with self._lock:
            self._forget_old(now)
            mine = self._by_client.get(client)
            if mine and len(mine) >= self.max_attempts:
                return max(mine[0] + self.window_s - now, 1)
            if len(self._all) >= self.global_max:
                return max(self._all[0] + self.window_s - now, 1)
            return 0
