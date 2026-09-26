import hmac
import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from intake.config import Settings
from intake.core.session import verify_session

_INVALID = (401, "missing or invalid token")


@dataclass(frozen=True)
class Caller:
    """Who is calling: a script with the static API token, or a signed-in reviewer."""

    kind: Literal["api", "user"]
    user: str | None


def _bearer(authorization: str | None) -> str | None:
    scheme, _, supplied = (authorization or "").partition(" ")
    supplied = supplied.strip()
    if scheme.lower() != "bearer" or not supplied:
        return None
    return supplied


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())  # bytes: a non-ASCII value cannot raise


def resolve_caller(
    settings: Settings, authorization: str | None, now: int
) -> Caller | tuple[int, str]:
    """The caller, or (http status, message). Fails closed when neither a static token nor a
    session secret is configured. A session is a reviewer's signed login; the static token is for
    scripts (upload, health of the queue) and has no user identity."""
    if not settings.api_token and not settings.session_secret:
        return 503, "API_TOKEN is not configured on the server"
    supplied = _bearer(authorization)
    if supplied is None:
        return _INVALID
    if settings.api_token and _same(supplied, settings.api_token):
        return Caller("api", None)
    user = verify_session(settings.session_secret, supplied, now)
    # A session is only as good as today's configuration: switching login off (no password) or
    # renaming the reviewer ends every session that was issued before.
    if user is not None and settings.reviewer_password and _same(user, settings.reviewer_username):
        return Caller("user", user)
    return _INVALID


Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(text: str) -> list[Network]:
    """Addresses or networks, comma separated ("10.0.0.0/8, 192.168.1.5"). Bad input is an error."""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return [ipaddress.ip_network(part, strict=False) for part in parts]


def _in(address: str, networks: Sequence[Network]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in n for n in networks)


def client_address(peer: str | None, forwarded_for: str | None, trusted: Sequence[Network]) -> str:
    """Who is really calling. The connection's address, unless it is one of our own proxies (the web
    server), which reports the visitor in X-Forwarded-For: then the last address in that header that
    is not ours. Anyone else's forwarded header is ignored, since anyone can send one."""
    if not peer:
        return "unknown"
    if not forwarded_for or not _in(peer, trusted):
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer  # a header we cannot read is not evidence
        if not _in(hop, trusted):
            return hop
    return peer


def credentials_match(settings: Settings, username: str, password: str) -> bool:
    """The demo reviewer's login. No password configured means nobody can log in."""
    if not settings.reviewer_password:
        return False
    user_ok = _same(username, settings.reviewer_username)
    password_ok = _same(password, settings.reviewer_password)
    return user_ok and password_ok
