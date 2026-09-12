import json

import pytest

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import list_events


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload


WARNING_EVENT = {
    "type": "Warning", "reason": "FailedScheduling", "message": "0/3 nodes available",
    "involvedObject": {"kind": "Pod", "name": "my-pod"}, "count": 5,
    "lastTimestamp": "2026-09-12T10:00:00Z",
}
NORMAL_EVENT = {
    "type": "Normal", "reason": "Scheduled", "message": "Successfully assigned",
    "involvedObject": {"kind": "Pod", "name": "my-pod"}, "count": 1,
    "lastTimestamp": "2026-09-12T09:59:00Z",
}


def stub_urlopen(monkeypatch, items):
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")

    def fake_urlopen(req, timeout=30, context=None):
        return FakeResponse({"items": items})

    monkeypatch.setattr(session_module.urllib.request, "urlopen", fake_urlopen)


def test_defaults_to_warning_type_only(monkeypatch):
    stub_urlopen(monkeypatch, [WARNING_EVENT, NORMAL_EVENT])
    events = list_events("app", server="fake")
    assert len(events) == 1
    assert events[0]["type"] == "Warning"
    assert events[0]["reason"] == "FailedScheduling"


def test_include_normal_returns_both_types(monkeypatch):
    stub_urlopen(monkeypatch, [WARNING_EVENT, NORMAL_EVENT])
    events = list_events("app", server="fake", include_normal=True)
    assert {e["type"] for e in events} == {"Warning", "Normal"}


def test_no_events_returns_empty_list_not_an_error(monkeypatch):
    stub_urlopen(monkeypatch, [])
    assert list_events("app", server="fake") == []


def test_missing_items_key_returns_empty_list(monkeypatch):
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")

    def fake_urlopen(req, timeout=30, context=None):
        return FakeResponse({})

    monkeypatch.setattr(session_module.urllib.request, "urlopen", fake_urlopen)
    assert list_events("app", server="fake") == []


def test_disallowed_server_raises_before_urlopen_is_called(monkeypatch):
    monkeypatch.setenv("ARGOCD_EXEC_ALLOW_SERVERS", "allowed.example.com")

    def fail_if_called(*a, **k):
        raise AssertionError("should not reach urlopen")

    monkeypatch.setattr(session_module.urllib.request, "urlopen", fail_if_called)
    with pytest.raises(ValueError, match="not in ARGOCD_EXEC_ALLOW_SERVERS"):
        list_events("app", server="not-allowed.example.com")
