"""OAuth 2.0 authorization-code flow helpers for connecting an accounting system. Pure: the clock
and the secret are passed in; no network.

The `state` is signed, expires, and names the person who started the flow, so a callback cannot be
replayed by someone else or forged. The PKCE verifier is derived from the state with the same secret
(nothing to store, and it never appears in a URL). A session token is not a valid state, and the
other way round, because the signatures are over different labels.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

_VERSION = "o1"
STATE_TTL_S = 600


class CallbackError(StrEnum):
    DENIED = "denied"
    MISSING = "missing"
    BAD_STATE = "bad_state"
    EXPIRED = "expired"
    WRONG_USER = "wrong_user"


@dataclass(frozen=True)
class CallbackOk:
    code: str
    verifier: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(secret: str, label: str, message: str) -> bytes:
    return hmac.new(secret.encode(), f"{label}.{message}".encode(), hashlib.sha256).digest()


def make_state(secret: str, user: str, now: int, ttl_s: int = STATE_TTL_S) -> str:
    if not secret:
        raise ValueError("a secret is required to sign the OAuth state")
    body = json.dumps(
        {"u": user, "e": now + ttl_s, "n": secrets.token_urlsafe(16)}, separators=(",", ":")
    )
    payload = _b64(body.encode())
    return f"{_VERSION}.{payload}.{_b64(_mac(secret, 'oauth-state', payload))}"


def _read_state(secret: str, state: str, now: int) -> tuple[str | None, CallbackError | None]:
    parts = state.split(".")
    if not secret or len(parts) != 3 or parts[0] != _VERSION:
        return None, CallbackError.BAD_STATE
    _, payload, signature = parts
    if not hmac.compare_digest(
        signature.encode(), _b64(_mac(secret, "oauth-state", payload)).encode()
    ):
        return None, CallbackError.BAD_STATE
    try:
        data = json.loads(_unb64(payload))
    except (ValueError, UnicodeError, binascii.Error):
        return None, CallbackError.BAD_STATE
    user, expires = (data.get("u"), data.get("e")) if isinstance(data, dict) else (None, None)
    if not isinstance(user, str) or isinstance(expires, bool) or not isinstance(expires, int):
        return None, CallbackError.BAD_STATE
    if expires <= now:
        return None, CallbackError.EXPIRED
    return user, None


def pkce_verifier(secret: str, state: str) -> str:
    return _b64(_mac(secret, "oauth-pkce", state))  # 43 characters, from 32 bytes


def pkce_challenge(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def authorize_url(
    endpoint: str,
    client_id: str,
    redirect_uri: str,
    scopes: Sequence[str],
    state: str,
    challenge: str,
) -> str:
    query = urlencode(
        {
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )  # fmt: skip
    return f"{endpoint}?{query}"


def check_callback(
    params: Mapping[str, str], secret: str, now: int, expected_user: str
) -> CallbackOk | CallbackError:
    """What came back on the redirect: a code to exchange, or the reason it must be ignored."""
    if params.get("error"):
        return CallbackError.DENIED
    code, state = params.get("code"), params.get("state")
    if not code or not state:
        return CallbackError.MISSING
    user, problem = _read_state(secret, state, now)
    if problem is not None:
        return problem
    if user != expected_user:
        return CallbackError.WRONG_USER
    return CallbackOk(code=code, verifier=pkce_verifier(secret, state))
