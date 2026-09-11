import pytest


@pytest.fixture(autouse=True)
def no_allow_list(monkeypatch):
    """Every test starts with no ARGOCD_EXEC_ALLOW_SERVERS restriction unless
    it sets one itself — otherwise a value left in the real environment by
    whoever runs the suite would make unrelated tests fail."""
    monkeypatch.delenv("ARGOCD_EXEC_ALLOW_SERVERS", raising=False)
