"""get_container_for_pod(): disambiguating a multi-workload app by walking
the real ownership chain instead of taking the first workload found, across
all three supported kinds, plus surfacing (not hiding) a workload that
itself declares more than one container."""
import json

import pytest

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import get_container_for_pod

from .test_ownership import node, two_deployment_tree


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload


def workload_manifest(kind, name, container_names):
    return json.dumps({
        "kind": kind,
        "metadata": {"name": name},
        "spec": {"template": {"spec": {
            "containers": [{"name": c} for c in container_names],
        }}},
    })


def stub_manifests(monkeypatch, manifests_payload):
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")
    monkeypatch.setattr(
        session_module.urllib.request, "urlopen",
        lambda req, timeout=30, context=None: FakeResponse(manifests_payload),
    )


def test_resolves_the_container_of_the_pods_own_deployment_not_the_first_one(monkeypatch):
    tree = two_deployment_tree()
    stub_manifests(monkeypatch, {"manifests": [
        workload_manifest("Deployment", "dep-a", ["container-a"]),
        workload_manifest("Deployment", "dep-b", ["container-b"]),
    ]})

    container_a, _ = get_container_for_pod("app", "pod-from-a", "ns", server="fake", tree=tree)
    container_b, _ = get_container_for_pod("app", "pod-from-b", "ns", server="fake", tree=tree)
    assert container_a == "container-a"
    assert container_b == "container-b"


def test_resolves_statefulset_owned_pod(monkeypatch):
    tree = {"nodes": [
        node("StatefulSet", "my-sts", "ns"),
        node("Pod", "my-sts-0", "ns",
             parent_refs=[{"kind": "StatefulSet", "namespace": "ns", "name": "my-sts"}]),
    ]}
    stub_manifests(monkeypatch, {"manifests": [
        workload_manifest("StatefulSet", "my-sts", ["sts-container"]),
    ]})

    container, others = get_container_for_pod("app", "my-sts-0", "ns", server="fake", tree=tree)
    assert container == "sts-container"
    assert others == []


def test_resolves_daemonset_owned_pod(monkeypatch):
    tree = {"nodes": [
        node("DaemonSet", "my-ds", "ns"),
        node("Pod", "my-ds-xyz", "ns",
             parent_refs=[{"kind": "DaemonSet", "namespace": "ns", "name": "my-ds"}]),
    ]}
    stub_manifests(monkeypatch, {"manifests": [
        workload_manifest("DaemonSet", "my-ds", ["ds-container"]),
    ]})

    container, others = get_container_for_pod("app", "my-ds-xyz", "ns", server="fake", tree=tree)
    assert container == "ds-container"
    assert others == []


def test_surfaces_other_containers_instead_of_hiding_them(monkeypatch):
    dep_ref = {"kind": "Deployment", "namespace": "ns", "name": "multi-container-dep"}
    rs_ref = {"kind": "ReplicaSet", "namespace": "ns", "name": "multi-container-dep-111"}
    tree = {"nodes": [
        node("Deployment", "multi-container-dep", "ns"),
        node("ReplicaSet", "multi-container-dep-111", "ns", parent_refs=[dep_ref]),
        node("Pod", "multi-container-pod", "ns", parent_refs=[rs_ref]),
    ]}
    stub_manifests(monkeypatch, {"manifests": [
        workload_manifest("Deployment", "multi-container-dep", ["main", "sidecar-a", "sidecar-b"]),
    ]})

    container, others = get_container_for_pod(
        "app", "multi-container-pod", "ns", server="fake", tree=tree)
    assert container == "main"
    assert others == ["sidecar-a", "sidecar-b"]


def test_falls_back_to_first_workload_when_ownership_cannot_be_resolved(monkeypatch):
    # A pod with no parentRefs at all — e.g. a bare Pod, not workload-managed.
    tree = {"nodes": [{"kind": "Pod", "namespace": "ns", "name": "bare-pod"}]}
    stub_manifests(monkeypatch, {"manifests": [
        workload_manifest("Deployment", "only-dep", ["only-container"]),
    ]})

    container, _ = get_container_for_pod("app", "bare-pod", "ns", server="fake", tree=tree)
    assert container == "only-container"


def test_raises_when_app_has_no_recognized_workload_at_all(monkeypatch):
    tree = {"nodes": [{"kind": "Pod", "namespace": "ns", "name": "bare-pod"}]}
    stub_manifests(monkeypatch, {"manifests": []})

    with pytest.raises(RuntimeError, match="Deployment/StatefulSet/DaemonSet"):
        get_container_for_pod("app", "bare-pod", "ns", server="fake", tree=tree)
