import hmac

from intake.config import Settings


def token_problem(settings: Settings, authorization: str | None) -> tuple[int, str] | None:
    """None if the request may proceed, else (http status, message). Fails closed when no token is
    configured. Compares bytes so a non-ASCII header cannot cause a server error."""
    if not settings.api_token:
        return 503, "API_TOKEN is not configured on the server"
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.encode(), settings.api_token.encode()
    ):
        return 401, "missing or invalid token"
    return None
