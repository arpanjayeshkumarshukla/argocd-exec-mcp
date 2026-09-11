"""PodSession.run() against a scripted fake websocket — no network, no real
ArgoCD. Covers the parts that are hardest to get right and were each wrong
at least once during development: treating idle time as completion, losing
the exit code, and not recovering from a dropped connection.

Not covered here, and not fakeable at this level: shell state (cwd, env
vars) persisting across separate run() calls. That's a property of the real
shell process on the far end of the same websocket, not of anything
PodSession itself does — see the live MCP smoke test in the project's
history for that claim, since a scripted fake has no real shell to persist
state in.
"""
import json
import re

import websocket

from argocd_exec_mcp import session as session_module
from argocd_exec_mcp.session import PodSession

MARKER_RE = re.compile(r'__DONE_[0-9a-f]+_')
TIMEOUT = object()  # sentinel meaning "recv() should raise WebSocketTimeoutException here"


class ScriptedWS:
    """Fakes just enough of websocket-client's connection object for
    PodSession: settimeout()/send()/recv()/close(). The response script is
    computed lazily from the sent stdin payload, so it can extract the real
    (random) sentinel marker PodSession generated."""

    def __init__(self, respond):
        self._respond = respond
        self._queue = []
        self.sent = []
        self.closed = False

    def settimeout(self, timeout):
        pass

    def send(self, msg):
        d = json.loads(msg)
        self.sent.append(d)
        if d.get("operation") == "stdin":
            self._queue = list(self._respond(d["data"]))

    def recv(self):
        if not self._queue:
            raise websocket.WebSocketTimeoutException()
        item = self._queue.pop(0)
        if item is TIMEOUT:
            raise websocket.WebSocketTimeoutException()
        return item

    def close(self):
        self.closed = True


def scripted_completion(output_text, exit_code=0, stalls_first=0):
    """Build a respond() function: `stalls_first` WebSocketTimeoutExceptions
    before the real output arrives — proving idle time isn't mistaken for
    completion — then the output followed by the expanded sentinel."""
    def respond(sent_data):
        marker = MARKER_RE.search(sent_data).group()
        frames = [TIMEOUT] * stalls_first
        frames.append(json.dumps({
            "operation": "stdout",
            "data": f"{output_text}\r\n{marker}{exit_code}__\r\n",
        }))
        return frames
    return respond


def make_session(monkeypatch, ws_or_factory):
    monkeypatch.setattr(session_module, "token_for", lambda server: "fake-token")
    factory = ws_or_factory if callable(ws_or_factory) else (lambda *a, **k: ws_or_factory)
    monkeypatch.setattr(session_module.websocket, "create_connection", factory)
    return PodSession("app", "pod", "container", "ns", "project", server="fake.server")


def test_recovers_output_and_exit_code(monkeypatch):
    ws = ScriptedWS(scripted_completion("hello", exit_code=0))
    ps = make_session(monkeypatch, ws)
    output, code = ps.run("echo hello")
    assert output == "hello"
    assert code == 0


def test_recovers_nonzero_exit_code(monkeypatch):
    ws = ScriptedWS(scripted_completion("", exit_code=7))
    ps = make_session(monkeypatch, ws)
    _, code = ps.run("sh -c 'exit 7'")
    assert code == 7


def test_idle_periods_longer_than_the_recv_poll_do_not_end_the_command_early(monkeypatch):
    # This is the regression test for the original bug: a recv() timeout used
    # to be treated as "command finished", so a command quiet for a while
    # before producing output came back empty-but-successful.
    ws = ScriptedWS(scripted_completion("slow_output", exit_code=0, stalls_first=5))
    ps = make_session(monkeypatch, ws)
    output, code = ps.run("sleep 4; echo slow_output", timeout=20)
    assert output == "slow_output"
    assert code == 0


def test_real_timeout_yields_none_exit_code_not_a_false_success(monkeypatch):
    def never_completes(sent_data):
        return [TIMEOUT] * 10_000  # exhausted well before the deadline below

    ws = ScriptedWS(never_completes)
    ps = make_session(monkeypatch, ws)
    output, code = ps.run("this never finishes", timeout=0.2)
    assert code is None


def test_reconnects_once_after_the_session_drops_and_retries(monkeypatch):
    ws_first = ScriptedWS(scripted_completion("first", exit_code=0))
    ws_second = ScriptedWS(scripted_completion("second", exit_code=0))
    wss = [ws_first, ws_second]

    def factory(*a, **k):
        return wss.pop(0)

    ps = make_session(monkeypatch, factory)
    out1, code1 = ps.run("echo first")
    assert (out1, code1) == ("first", 0)
    assert ps.ws is ws_first

    # Simulate the connection dying before the next command is sent.
    def dying_send(msg):
        raise websocket.WebSocketConnectionClosedException()
    ps.ws.send = dying_send

    out2, code2 = ps.run("echo second")
    assert (out2, code2) == ("second", 0)
    assert ps.ws is ws_second  # a fresh connection was actually made, not reused


def test_server_requested_reconnect_is_honored(monkeypatch):
    # ArgoCD's own web terminal treats a `{"Code": ...}` TerminalCommand frame
    # (observed cause: an auth token rotation) as "close and reopen the
    # socket" — this is the equivalent case for PodSession, discovered by
    # reading server/application/websocket.go, not by hitting it live.
    def respond_with_reconnect_signal(sent_data):
        return [
            json.dumps({
                "operation": "stdout",
                "data": "\nReconnect because the token was refreshed...\n",
            }),
            json.dumps({"Code": 1}),
        ]

    ws_first = ScriptedWS(respond_with_reconnect_signal)
    ws_second = ScriptedWS(scripted_completion("after_reconnect", exit_code=0))
    wss = [ws_first, ws_second]

    def factory(*a, **k):
        return wss.pop(0)

    ps = make_session(monkeypatch, factory)
    output, code = ps.run("echo after_reconnect")
    assert (output, code) == ("after_reconnect", 0)
    assert ps.ws is ws_second  # the reconnect signal actually triggered a fresh connection
