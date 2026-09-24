import pytest


@pytest.fixture(autouse=True)
def _never_call_the_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md: never call the model API in tests. With no key, extraction is 'not configured'
    (jobs pause), so even a developer with a real key in their environment can't spend money here.
    Tests that need extraction inject a recorded or scripted client instead."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
