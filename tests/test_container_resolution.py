"""get_container_for_pod(): disambiguating a multi-Deployment app by walking
the real ownership chain instead of taking the first Deployment found."""
import json

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import get_container_for_pod

from .test_ownership import two_deployment_tree


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload


def deployment_manifest(name, container_name):
    return json.dumps({
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {"template": {"spec": {"containers": [{"name": container_name}]}}},
    })


def test_resolves_the_container_of_the_pods_own_deployment_not_the_first_one(monkeypatch):
    tree = two_deployment_tree()
    manifests_payload = {"manifests": [
        deployment_manifest("dep-a", "container-a"),
        deployment_manifest("dep-b", "container-b"),
    ]}
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")
    monkeypatch.setattr(
        session_module.urllib.request, "urlopen",
        lambda req, timeout=30, context=None: FakeResponse(manifests_payload),
    )

    container_a = get_container_for_pod("app", "pod-from-a", "ns", server="fake", tree=tree)
    container_b = get_container_for_pod("app", "pod-from-b", "ns", server="fake", tree=tree)
    assert container_a == "container-a"
    assert container_b == "container-b"


def test_falls_back_to_first_deployment_when_ownership_cannot_be_resolved(monkeypatch):
    # A pod with no parentRefs at all — e.g. a bare Pod, not Deployment-managed.
    tree = {"nodes": [{"kind": "Pod", "namespace": "ns", "name": "bare-pod"}]}
    manifests_payload = {"manifests": [deployment_manifest("only-dep", "only-container")]}
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")
    monkeypatch.setattr(
        session_module.urllib.request, "urlopen",
        lambda req, timeout=30, context=None: FakeResponse(manifests_payload),
    )

    container = get_container_for_pod("app", "bare-pod", "ns", server="fake", tree=tree)
    assert container == "only-container"


def test_raises_when_app_has_no_deployment_at_all(monkeypatch):
    tree = {"nodes": [{"kind": "Pod", "namespace": "ns", "name": "bare-pod"}]}
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")
    monkeypatch.setattr(
        session_module.urllib.request, "urlopen",
        lambda req, timeout=30, context=None: FakeResponse({"manifests": []}),
    )

    import pytest
    with pytest.raises(RuntimeError, match="no Deployment"):
        get_container_for_pod("app", "bare-pod", "ns", server="fake", tree=tree)
