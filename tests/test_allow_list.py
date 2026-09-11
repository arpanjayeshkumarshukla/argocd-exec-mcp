import pytest

from argocd_exec_mcp.session import _check_allowed, allowed_servers


def test_unset_means_no_restriction(monkeypatch):
    monkeypatch.delenv("ARGOCD_EXEC_ALLOW_SERVERS", raising=False)
    assert allowed_servers() == []
    _check_allowed("literally.anything.example.com")  # must not raise


def test_set_list_restricts_and_trims_whitespace(monkeypatch):
    monkeypatch.setenv("ARGOCD_EXEC_ALLOW_SERVERS", "a.example.com, b.example.com")
    assert allowed_servers() == ["a.example.com", "b.example.com"]
    _check_allowed("a.example.com")  # must not raise
    _check_allowed("b.example.com")  # must not raise
    with pytest.raises(ValueError):
        _check_allowed("c.example.com")


def test_empty_string_env_var_means_no_restriction(monkeypatch):
    monkeypatch.setenv("ARGOCD_EXEC_ALLOW_SERVERS", "")
    assert allowed_servers() == []
    _check_allowed("anything")  # must not raise
