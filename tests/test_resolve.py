import pytest

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import resolve


def mixed_health_tree():
    return {"nodes": [
        {"kind": "Pod", "namespace": "ns", "name": "pod-unhealthy",
         "health": {"status": "Progressing"}},
        {"kind": "Pod", "namespace": "ns", "name": "pod-healthy-1",
         "health": {"status": "Healthy"}},
        {"kind": "Pod", "namespace": "ns", "name": "pod-healthy-2",
         "health": {"status": "Healthy"}},
    ]}


def stub_out_network(monkeypatch, tree):
    monkeypatch.setattr(session_module, "_resource_tree", lambda app, server: tree)
    monkeypatch.setattr(session_module, "get_project", lambda app, server=None: "proj")
    monkeypatch.setattr(
        session_module, "get_container_for_pod",
        lambda app, pod, ns, server=None, tree=None: ("container", []),
    )


def test_picks_first_healthy_pod_when_none_specified(monkeypatch):
    stub_out_network(monkeypatch, mixed_health_tree())
    result = resolve("app", server="fake")
    assert result["pod"] == "pod-healthy-1"
    assert result["namespace"] == "ns"
    assert result["project"] == "proj"
    assert result["container"] == "container"
    assert result["other_containers"] == []
    assert set(result["candidates"]) == {"pod-unhealthy", "pod-healthy-1", "pod-healthy-2"}


def test_propagates_other_containers_from_get_container_for_pod(monkeypatch):
    monkeypatch.setattr(session_module, "_resource_tree",
                         lambda app, server: mixed_health_tree())
    monkeypatch.setattr(session_module, "get_project", lambda app, server=None: "proj")
    monkeypatch.setattr(
        session_module, "get_container_for_pod",
        lambda app, pod, ns, server=None, tree=None: ("main", ["sidecar-a", "sidecar-b"]),
    )
    result = resolve("app", server="fake")
    assert result["container"] == "main"
    assert result["other_containers"] == ["sidecar-a", "sidecar-b"]


def test_honors_an_explicitly_requested_pod_regardless_of_health(monkeypatch):
    stub_out_network(monkeypatch, mixed_health_tree())
    result = resolve("app", server="fake", pod="pod-unhealthy")
    assert result["pod"] == "pod-unhealthy"


def test_raises_on_unknown_explicit_pod(monkeypatch):
    stub_out_network(monkeypatch, mixed_health_tree())
    with pytest.raises(RuntimeError, match="not found"):
        resolve("app", server="fake", pod="does-not-exist")


def test_raises_when_app_has_no_pods_at_all(monkeypatch):
    stub_out_network(monkeypatch, {"nodes": []})
    with pytest.raises(RuntimeError, match="no pods found"):
        resolve("app", server="fake")


def test_falls_back_to_any_pod_when_none_are_healthy(monkeypatch):
    tree = {"nodes": [
        {"kind": "Pod", "namespace": "ns", "name": "pod-degraded",
         "health": {"status": "Degraded"}},
    ]}
    stub_out_network(monkeypatch, tree)
    result = resolve("app", server="fake")
    assert result["pod"] == "pod-degraded"


def test_disallowed_server_raises_before_any_network_call(monkeypatch):
    monkeypatch.setenv("ARGOCD_EXEC_ALLOW_SERVERS", "allowed.example.com")

    def fail_if_called(app, server):
        raise AssertionError("should not reach _resource_tree")

    monkeypatch.setattr(session_module, "_resource_tree", fail_if_called)
    with pytest.raises(ValueError, match="not in ARGOCD_EXEC_ALLOW_SERVERS"):
        resolve("app", server="not-allowed.example.com")
