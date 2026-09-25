"""The web app's generated types come from a committed snapshot of the API schema.

If the API changes without `make gen-api`, this fails, so the screen never talks to an API it has
stale types for."""

import json
from pathlib import Path

from intake.config import Settings
from intake.main import create_app

SNAPSHOT = Path(__file__).resolve().parents[4] / "apps/web/src/lib/api/openapi.json"


def test_the_committed_api_schema_is_current() -> None:
    app = create_app(Settings(api_token="x", session_secret="", reviewer_password=""))
    current = json.dumps(app.openapi(), indent=2, sort_keys=True)
    assert SNAPSHOT.read_text().strip() == current.strip(), (
        "the API changed: run `make gen-api` and commit apps/web/src/lib/api/"
    )
