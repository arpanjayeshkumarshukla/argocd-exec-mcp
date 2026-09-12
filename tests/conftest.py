import json

import pytest


@pytest.fixture(autouse=True)
def no_allow_list(monkeypatch):
    """Every test starts with no ARGOCD_EXEC_ALLOW_SERVERS restriction unless
    it sets one itself — otherwise a value left in the real environment by
    whoever runs the suite would make unrelated tests fail."""
    monkeypatch.delenv("ARGOCD_EXEC_ALLOW_SERVERS", raising=False)


class FakeResponse:
    """Stand-in for what urlopen() returns: only .read() is ever called on it."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload
