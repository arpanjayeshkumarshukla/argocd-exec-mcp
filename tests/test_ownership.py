"""Pod -> ReplicaSet -> Deployment ownership resolution.

This is the fixture-based version of the scenario that couldn't be
constructed against a live cluster during development: an app with more
than one Deployment, where "just take the first Deployment found" would
silently return the wrong container for one of the two pods.
"""
from argocd_exec_mcp.session import _owning_deployment


def node(kind, name, namespace=None, parent_refs=None):
    n = {"kind": kind, "name": name}
    if namespace is not None:
        n["namespace"] = namespace
    if parent_refs is not None:
        n["parentRefs"] = parent_refs
    return n


def two_deployment_tree():
    return {"nodes": [
        node("Deployment", "dep-a", "ns"),
        node("Deployment", "dep-b", "ns"),
        node("ReplicaSet", "dep-a-111", "ns",
             parent_refs=[{"kind": "Deployment", "namespace": "ns", "name": "dep-a"}]),
        node("ReplicaSet", "dep-b-222", "ns",
             parent_refs=[{"kind": "Deployment", "namespace": "ns", "name": "dep-b"}]),
        node("Pod", "pod-from-a", "ns",
             parent_refs=[{"kind": "ReplicaSet", "namespace": "ns", "name": "dep-a-111"}]),
        node("Pod", "pod-from-b", "ns",
             parent_refs=[{"kind": "ReplicaSet", "namespace": "ns", "name": "dep-b-222"}]),
    ]}


def test_each_pod_resolves_to_its_own_deployment_not_the_first_one_found():
    tree = two_deployment_tree()
    assert _owning_deployment(tree, "pod-from-a", "ns") == "dep-a"
    assert _owning_deployment(tree, "pod-from-b", "ns") == "dep-b"


def test_unknown_pod_returns_none():
    tree = two_deployment_tree()
    assert _owning_deployment(tree, "no-such-pod", "ns") is None


def test_pod_owned_by_a_bare_replicaset_with_no_deployment_parent_returns_none():
    tree = {"nodes": [
        node("ReplicaSet", "standalone-rs", "ns"),  # no parentRefs — not Deployment-owned
        node("Pod", "orphan-pod", "ns",
             parent_refs=[{"kind": "ReplicaSet", "namespace": "ns", "name": "standalone-rs"}]),
    ]}
    assert _owning_deployment(tree, "orphan-pod", "ns") is None


def test_namespace_mismatch_is_not_treated_as_a_match():
    tree = two_deployment_tree()
    assert _owning_deployment(tree, "pod-from-a", "wrong-namespace") is None
