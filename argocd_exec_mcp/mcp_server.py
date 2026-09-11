#!/usr/bin/env python3
"""MCP server exposing a persistent ArgoCD terminal session per pod.

Solves the problem a one-shot exec client can't: each tool call an agent
makes is typically a fresh process, so a session has to live outside any
single call to feel like "one shell, many commands" rather than "reconnect
every time." This server holds the websocket(s) open for its own process
lifetime; the agent drives them via `open_session` / `run` / `close_session`.

Set ARGOCD_EXEC_ALLOW_SERVERS to a comma-separated list of hostnames to
restrict which ArgoCD servers this server will connect to.
"""
import uuid

from mcp.server.mcpserver import MCPServer

from .session import PodSession, list_pods as _list_pods

mcp = MCPServer("argocd-exec-mcp")
_sessions: dict[str, PodSession] = {}


@mcp.tool()
def list_pods(app: str, server: str | None = None) -> list[dict]:
    """List pods for an ArgoCD app, with namespace and health."""
    return _list_pods(app, server)


@mcp.tool()
def open_session(app: str, pod: str, container: str, namespace: str, project: str,
                  server: str | None = None, shell: str | None = None) -> str:
    """Open a persistent shell session in a pod. Returns a session_id to pass
    to run()/close_session(). The underlying shell and websocket stay open
    across multiple run() calls — commands share state (cwd, exported env
    vars) the way they would in one real terminal."""
    session = PodSession(app, pod, container, namespace, project, server, shell)
    session.connect()
    session_id = uuid.uuid4().hex[:12]
    _sessions[session_id] = session
    return session_id


@mcp.tool()
def run(session_id: str, cmd: str, timeout: int = 20) -> dict:
    """Run one command in an already-open session. Returns {output, exit_code}.
    exit_code is null only if the command didn't complete within `timeout` —
    a real timeout, not a dropped connection (that reconnects once and
    retries transparently)."""
    session = _sessions.get(session_id)
    if session is None:
        raise ValueError(f"no open session {session_id!r} — call open_session first")
    output, exit_code = session.run(cmd, timeout)
    return {"output": output, "exit_code": exit_code}


@mcp.tool()
def close_session(session_id: str) -> str:
    """Close a session and free its websocket. Always do this when done with
    a pod — an open session outlives the task it was opened for otherwise."""
    session = _sessions.pop(session_id, None)
    if session is None:
        return f"no open session {session_id!r} (already closed?)"
    session.close()
    return f"closed {session_id}"


@mcp.tool()
def list_open_sessions() -> list[dict]:
    """List sessions currently held open by this server, so a new agent turn
    can discover and reuse one instead of opening a duplicate."""
    return [
        {"session_id": sid, "app": s.app, "pod": s.pod, "container": s.container}
        for sid, s in _sessions.items()
    ]


def main():
    try:
        mcp.run(transport='stdio')
    finally:
        for s in list(_sessions.values()):
            s.close()


if __name__ == '__main__':
    main()
