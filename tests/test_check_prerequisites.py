"""check_prerequisites(): the automated version of "is ExecEnabled on and
does my token have the right RBAC", instead of only finding out via an
opaque failure partway through a real exec attempt."""
import subprocess

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import check_prerequisites

from .conftest import FakeResponse


def fake_can_i_result(value):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{value}\n", stderr="")


def stub(monkeypatch, *, exec_enabled, project, can_i_value):
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")

    def fake_urlopen(req, timeout=15, context=None):
        if req.full_url.endswith("/api/v1/settings"):
            return FakeResponse({"execEnabled": exec_enabled})
        return FakeResponse({"spec": {"project": project}})

    monkeypatch.setattr(session_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        session_module.subprocess, "run",
        lambda *a, **k: fake_can_i_result(can_i_value),
    )


def test_all_checks_pass(monkeypatch):
    stub(monkeypatch, exec_enabled=True, project="proj", can_i_value="yes")
    results = check_prerequisites("app", server="fake")
    assert [ok for ok, _ in results] == [True, True, True]


def test_exec_not_enabled_is_reported_but_other_checks_still_run(monkeypatch):
    stub(monkeypatch, exec_enabled=False, project="proj", can_i_value="yes")
    results = check_prerequisites("app", server="fake")
    assert results[0][0] is False
    assert "DISABLED" in results[0][1]
    # Not fatal to the rest of the diagnosis — still worth knowing about RBAC too.
    assert [ok for ok, _ in results[1:]] == [True, True]


def test_applications_get_failure_stops_before_the_can_i_call(monkeypatch):
    monkeypatch.setattr(session_module, "token_for", lambda server: "tok")

    def fake_urlopen(req, timeout=15, context=None):
        if req.full_url.endswith("/api/v1/settings"):
            return FakeResponse({"execEnabled": True})
        raise RuntimeError("403 Forbidden")

    def fail_if_called(*a, **k):
        raise AssertionError("should not reach the can-i subprocess call")

    monkeypatch.setattr(session_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(session_module.subprocess, "run", fail_if_called)
    results = check_prerequisites("app", server="fake")
    assert results[-1][0] is False
    assert "applications,get" in results[-1][1]
    assert len(results) == 2  # never reached the can-i check


def test_exec_create_denied_is_reported(monkeypatch):
    stub(monkeypatch, exec_enabled=True, project="proj", can_i_value="no")
    results = check_prerequisites("app", server="fake")
    assert results[-1][0] is False
    assert "DENIED" in results[-1][1]


def test_can_i_subprocess_failure_is_reported_not_raised(monkeypatch):
    stub(monkeypatch, exec_enabled=True, project="proj", can_i_value="yes")

    def raise_not_found(*a, **k):
        raise FileNotFoundError("argocd not found")

    monkeypatch.setattr(session_module.subprocess, "run", raise_not_found)
    results = check_prerequisites("app", server="fake")
    assert results[-1][0] is False
    assert "FAILED to run" in results[-1][1]
