import pytest
import yaml

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import default_server


def test_reads_current_context(monkeypatch, tmp_path):
    cfg_path = tmp_path / "argocd-config"
    cfg_path.write_text(yaml.dump({"current-context": "my-server.example.com"}))
    monkeypatch.setattr(session_module, "ARGOCD_CONFIG", str(cfg_path))
    assert default_server() == "my-server.example.com"


def test_raises_a_clear_error_without_current_context(monkeypatch, tmp_path):
    cfg_path = tmp_path / "argocd-config"
    cfg_path.write_text(yaml.dump({"contexts": []}))
    monkeypatch.setattr(session_module, "ARGOCD_CONFIG", str(cfg_path))
    with pytest.raises(RuntimeError, match="current-context"):
        default_server()
