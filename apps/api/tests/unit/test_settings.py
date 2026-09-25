from datetime import date

import pytest
from pydantic import ValidationError

from intake.config import Settings


def test_the_as_of_date_override_is_allowed_in_development_and_test() -> None:
    for env in ("development", "test"):
        assert Settings(app_env=env, validation_today=date(2026, 9, 25)).validation_today  # type: ignore[arg-type]


def test_the_as_of_date_override_is_refused_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", validation_today=date(2026, 9, 25))


def test_production_without_the_override_is_fine() -> None:
    assert Settings(app_env="production").validation_today is None


def test_the_default_environment_is_development() -> None:
    assert Settings().app_env == "development"


@pytest.mark.parametrize("name", ["dedupe_poll_s", "dedupe_max_wait_s"])
def test_duplicate_wait_settings_must_be_positive(name: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**{name: 0})  # type: ignore[arg-type]
