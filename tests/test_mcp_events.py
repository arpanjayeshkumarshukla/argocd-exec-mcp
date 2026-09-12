from argocd_exec_mcp import mcp_server


def make_events(n):
    return [
        {"type": "Warning", "reason": "FailedScheduling", "message": f"event {i}",
         "involvedObject": {"kind": "Pod", "name": "my-pod"}, "count": 1,
         "lastTimestamp": f"2026-09-12T{i:02d}:00:00Z"}
        for i in range(n)
    ]


def test_happy_path_under_cap_is_not_truncated(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "_list_events",
        lambda app, server=None, include_normal=False: make_events(5))
    result = mcp_server.list_events("app")
    assert result["truncated"] is False
    assert result["count"] == 5
    assert len(result["events"]) == 5


def test_include_normal_is_reflected_in_filter_field(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "_list_events",
        lambda app, server=None, include_normal=False: make_events(1))
    result = mcp_server.list_events("app", include_normal=True)
    assert result["filter"] == "Warning,Normal"
    result_default = mcp_server.list_events("app")
    assert result_default["filter"] == "Warning"


def test_over_cap_is_truncated_to_50_most_recent(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "_list_events",
        lambda app, server=None, include_normal=False: make_events(60))
    result = mcp_server.list_events("app")
    assert result["truncated"] is True
    assert result["count"] == 50
    assert len(result["events"]) == 50
    # most-recent-first by lastTimestamp: event 59 has the latest timestamp
    assert result["events"][0]["message"] == "event 59"
