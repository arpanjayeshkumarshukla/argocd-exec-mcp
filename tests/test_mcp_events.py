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
    assert result["returned_count"] == 5
    assert len(result["events"]) == 5


def test_include_normal_is_reflected_in_filter_field(monkeypatch):
    received = []

    def fake_list_events(app, server=None, include_normal=False):
        received.append(include_normal)
        return make_events(1)

    monkeypatch.setattr(mcp_server, "_list_events", fake_list_events)
    result = mcp_server.list_events("app", include_normal=True)
    assert result["filter"] == "Warning,Normal"
    result_default = mcp_server.list_events("app")
    assert result_default["filter"] == "Warning"
    # the tool's include_normal argument must actually reach session.list_events,
    # not just drive the independently-computed filter label
    assert received == [True, False]


def test_over_cap_is_truncated_to_50_most_recent(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "_list_events",
        lambda app, server=None, include_normal=False: make_events(60))
    result = mcp_server.list_events("app")
    assert result["truncated"] is True
    assert result["returned_count"] == 50
    assert len(result["events"]) == 50
    # most-recent-first by lastTimestamp: event 59 has the latest timestamp
    assert result["events"][0]["message"] == "event 59"


def test_warning_events_are_not_crowded_out_by_normal_events_under_the_cap(monkeypatch):
    # 3 old Warnings + 3 newer Normals, cap set low enough that a pure
    # recency sort would drop a Warning -- Warnings must still all survive.
    warnings = [
        {"type": "Warning", "reason": "BackOff", "message": f"warn {i}",
         "involvedObject": {"kind": "Pod", "name": "my-pod"}, "count": 1,
         "lastTimestamp": f"2026-09-12T0{i}:00:00Z"}
        for i in range(3)
    ]
    normals = [
        {"type": "Normal", "reason": "Scheduled", "message": f"normal {i}",
         "involvedObject": {"kind": "Pod", "name": "my-pod"}, "count": 1,
         "lastTimestamp": f"2026-09-12T1{i}:00:00Z"}
        for i in range(3)
    ]
    monkeypatch.setattr(
        mcp_server, "_list_events",
        lambda app, server=None, include_normal=False: normals + warnings)
    monkeypatch.setattr(mcp_server, "_EVENTS_CAP", 4)
    result = mcp_server.list_events("app", include_normal=True)
    returned_types = [e["type"] for e in result["events"]]
    assert returned_types.count("Warning") == 3
    assert result["truncated"] is True


def test_null_last_timestamp_does_not_crash_the_sort(monkeypatch):
    # A Series-aggregated Warning event can have lastTimestamp present but
    # null -- Kubernetes' zero-valued metav1.Time serializes that way. This
    # must not raise when sorted alongside a normally-timestamped event.
    events = make_events(1) + [
        {"type": "Warning", "reason": "BackOff", "message": "aggregated event",
         "involvedObject": {"kind": "Pod", "name": "my-pod"}, "count": 9,
         "lastTimestamp": None},
    ]
    monkeypatch.setattr(
        mcp_server, "_list_events",
        lambda app, server=None, include_normal=False: events)
    result = mcp_server.list_events("app")
    assert result["returned_count"] == 2
