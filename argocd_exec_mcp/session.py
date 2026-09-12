"""Shared ArgoCD terminal-websocket session logic.

Used by both `cli.py` (one-shot / interactive CLI) and `mcp_server.py`
(persistent multi-command session exposed as an MCP server). Kept in one
module so the protocol facts below are recorded once, not duplicated:

- The channel is a real TTY. ArgoCD's own server (server/application/
  terminal.go) requests `PodExecOptions{TTY: true}` and execs a persistent
  shell, never a one-shot non-TTY command. That means stdout/stderr are
  merged at the kubelet/container-runtime level, before ArgoCD ever sees
  them, and there is no in-band exit status for any individual command run
  inside that shell — only for the shell process itself, when it
  terminates. Recovering per-command output boundaries and exit codes
  requires a sentinel the shell prints itself (see PodSession.run()).
- HTTP/1.1 only: the `/terminal` endpoint returns `400 Bad Request` over
  HTTP/2.
- `Sec-WebSocket-Protocol: v4.channel.k8s.io` must be sent as a raw header,
  not via `subprotocols=` — Argo requires it to start the session but does
  not echo it back in the 101 response, and websocket-client's
  `subprotocols=` rejects a missing echo with "Invalid WebSocket Header".
- ArgoCD's own server pings this socket every 5s (`session.StartKeepalives`
  in terminal.go) specifically to survive load-balancer idle timeouts, so a
  client-side keepalive loop is redundant.
- `shell` is validated against the server's `allowedShells` allow-list; an
  unrecognized or omitted value makes ArgoCD fall back to trying each
  allowed shell in turn rather than failing outright, so PodSession leaves
  it unset by default instead of assuming `sh` exists on every cluster.
- There's a third message shape beyond `{operation, data, rows, cols}`:
  `{"Code": 1}` (`TerminalCommand` in websocket.go), sent — preceded by a
  human-readable `operation: "stdout"` warning line — whenever
  `sessionManager.VerifyToken()` detects the auth token was rotated
  server-side. ArgoCD's own web terminal (`pod-terminal-viewer.tsx`) treats
  it as "close this socket and open a new one"; PodSession does the same via
  `_ReconnectRequested`, discarding whatever partial output the interrupted
  attempt collected and reissuing the command on the fresh connection —
  found by reading the web client's source, not by hitting it live (forcing
  a real token-rotation event on demand isn't practical), so it's covered by
  a scripted unit test, not a live one.
- Output extraction brackets each command between two markers this project
  controls (`__START_<nonce>__`, `__DONE_<nonce>_<exit code>__`) rather than
  by locating the echo of the command line itself. A long compound command
  echoed back by the terminal is exposed to being split across a line-wrap
  boundary in a way a short fixed marker mostly isn't — this replaced an
  earlier design that searched for the echo of the whole line, after an
  intermittent live failure (right after a pod restart) where that echo
  search came up empty. Root cause unconfirmed; this reduces exposure to the
  suspected cause rather than claiming to fix a bug that couldn't be
  reproduced on demand.
"""
import json
import os
import re
import ssl
import subprocess  # nosec B404 - used only for the reviewed call in _can_i_exec_create
import time
import urllib.parse
import urllib.request
import uuid

import certifi
import websocket
import yaml

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
RECV_POLL = 3  # socket recv() wakeup interval; NOT a completion signal
ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]')

ARGOCD_CONFIG = os.path.expanduser(os.environ.get('ARGOCD_CONFIG', '~/.config/argocd/config'))


def _load_config():
    return yaml.safe_load(open(ARGOCD_CONFIG))


def default_server():
    """The server `argocd` itself would use: its current-context. Pass
    `--server`/`server=` explicitly rather than relying on this in a script
    meant to run against a fixed target."""
    cfg = _load_config()
    server = cfg.get('current-context')
    if not server:
        raise RuntimeError(
            f"no current-context in {ARGOCD_CONFIG}; run `argocd context <server>` "
            "or pass --server explicitly")
    return server


def allowed_servers():
    """Optional caller-imposed allow-list via ARGOCD_EXEC_ALLOW_SERVERS
    (comma-separated hostnames). Empty/unset means "whatever `argocd login`
    already trusts" — this exists so a deployment can pin itself to one
    environment (e.g. only ever a specific non-prod cluster), not as a
    default restriction."""
    raw = os.environ.get('ARGOCD_EXEC_ALLOW_SERVERS', '')
    return [s.strip() for s in raw.split(',') if s.strip()]


def _check_allowed(server):
    allow = allowed_servers()
    if allow and server not in allow:
        raise ValueError(
            f"{server} is not in ARGOCD_EXEC_ALLOW_SERVERS ({', '.join(allow)})")


def token_for(server):
    cfg = _load_config()
    for u in cfg.get('users', []):
        if u.get('name', '').startswith(server):
            return u['auth-token']
    raise RuntimeError(f"no argocd auth-token for {server}; run: argocd login {server}")


def _get_json(url, token, timeout):
    """GET `url` as JSON, authenticated with a bearer token.

    `url` is always built as f"https://{server}/..." from a value that's
    already passed through `default_server()`/`_check_allowed()` or an
    explicit `--server`, never from unsanitized external input — so the
    scheme is never attacker-influenced, despite bandit's broad B310
    warning on any `urlopen()` call.
    """
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    return json.load(urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX))  # nosec B310


def _resource_tree(app, server):
    tok = token_for(server)
    url = (f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}"
           f"/resource-tree?appNamespace=argocd")
    return _get_json(url, tok, 30)


def list_pods(app, server=None):
    server = server or default_server()
    _check_allowed(server)
    tree = _resource_tree(app, server)
    return [
        {'namespace': n['namespace'], 'name': n['name'],
         'health': n.get('health', {}).get('status', '')}
        for n in tree.get('nodes', []) if n.get('kind') == 'Pod'
    ]


def _project_event(e):
    involved = e.get('involvedObject') or {}
    return {
        'type': e.get('type'),
        'reason': e.get('reason'),
        'message': e.get('message'),
        'involvedObject': {'kind': involved.get('kind'), 'name': involved.get('name')},
        'count': e.get('count'),
        'firstTimestamp': e.get('firstTimestamp'),
        'lastTimestamp': e.get('lastTimestamp'),
    }


def list_events(app, server=None, include_normal=False):
    """Kubernetes Events across all of `app`'s resources — the same data
    ArgoCD's own web UI Events tab shows via this endpoint. Defaults to
    Warning-type events only (the debugging-relevant kind: FailedScheduling,
    BackOff, Unhealthy); pass `include_normal=True` to also get routine
    events. Events are short-lived (roughly one hour, cluster-default TTL):
    an empty result means nothing is within the current retention window,
    not that nothing happened.

    Each event is projected down to the fields a caller actually needs
    (type/reason/message/involvedObject.kind+name/count/first+lastTimestamp)
    rather than returned as ArgoCD's raw Kubernetes Event object, which also
    carries metadata, source, and reporting fields nothing here reads."""
    server = server or default_server()
    _check_allowed(server)
    tok = token_for(server)
    url = (f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}"
           f"/events?appNamespace=argocd")
    data = _get_json(url, tok, 30)
    items = data.get('items') or []
    if not include_normal:
        items = [e for e in items if e.get('type') == 'Warning']
    return [_project_event(e) for e in items]


_WORKLOAD_KINDS = ('Deployment', 'StatefulSet', 'DaemonSet')


def _owning_workload(tree, pod_name, pod_namespace):
    """Walk the resource tree's own parentRefs from a pod to the workload
    that owns it, rather than assuming which one it is. A Deployment-owned
    pod is one hop further removed (through a ReplicaSet) than a
    StatefulSet- or DaemonSet-owned pod, whose parentRefs point directly at
    the owning workload. Returns (kind, name) or None."""
    by_key = {(n['kind'], n.get('namespace'), n['name']): n for n in tree.get('nodes', [])}
    pod = by_key.get(('Pod', pod_namespace, pod_name))
    if not pod:
        return None
    for ref in pod.get('parentRefs') or []:
        if ref['kind'] in ('StatefulSet', 'DaemonSet'):
            return (ref['kind'], ref['name'])
        if ref['kind'] == 'ReplicaSet':
            rs = by_key.get((ref['kind'], ref['namespace'], ref['name']))
            if rs:
                for dref in rs.get('parentRefs') or []:
                    if dref['kind'] == 'Deployment':
                        return ('Deployment', dref['name'])
    return None


def get_project(app, server=None):
    server = server or default_server()
    _check_allowed(server)
    tok = token_for(server)
    url = f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}"
    data = _get_json(url, tok, 15)
    project = data.get('spec', {}).get('project')
    if not project:
        raise RuntimeError(f"app {app!r} has no spec.project in its ArgoCD Application object")
    return project


def _can_i_exec_create(server, project, app):
    """`argocd account can-i create exec <project>/<app>` already wraps the
    RBAC-reflection endpoint this needs — reimplementing that one HTTP call
    ourselves would duplicate a capability the official CLI already
    provides. Requires the `argocd` binary on PATH, same as `argocd login`
    already did for this whole project's auth to exist in the first
    place."""
    # Deliberately resolved via PATH, not an absolute path (bandit B607): the
    # whole point is to use whatever `argocd` the user already authenticated
    # with via `argocd login`, the same way that command itself was found.
    # No shell=True and every argument is a plain string in a list (bandit
    # B603/B404): there's no shell metacharacter interpretation for `project`
    # or `app` to exploit, since execve() never parses this as a shell line.
    result = subprocess.run(  # nosec B603 B607
        ['argocd', 'account', 'can-i', 'create', 'exec', f"{project}/{app}",
         '--server', server],
        capture_output=True, text=True, timeout=15, check=True,
    )
    return result.stdout.strip() == 'yes'


def check_prerequisites(app, server=None):
    """Diagnose the two things this project doesn't grant itself and can
    only fail against opaquely otherwise: ArgoCD's terminal feature
    (`execEnabled`) and this token's RBAC for the target app. Returns a
    list of (ok: bool, message: str) in check order, stopping early once a
    check that a later one depends on has failed — no point calling
    can-i(exec,create) with a project name you couldn't actually resolve.

    `applications,get` is checked by actually calling get_project() rather
    than a can-i dry-run: a real API call that requires the permission is a
    stronger signal that enforcement will behave the same way at exec time
    than a permission-reflection endpoint is. `exec,create` shells out to
    `argocd account can-i`, which already wraps the RBAC-reflection endpoint
    this needs — see _can_i_exec_create().
    """
    server = server or default_server()
    _check_allowed(server)
    tok = token_for(server)
    results = []

    url = f"https://{server}/api/v1/settings"
    settings = _get_json(url, tok, 15)
    exec_enabled = bool(settings.get('execEnabled'))
    results.append((exec_enabled, (
        "ArgoCD's terminal feature (execEnabled) is enabled" if exec_enabled else
        "ArgoCD's terminal feature (execEnabled) is DISABLED — ask your ArgoCD "
        "admin to set exec.enabled: \"true\" in argocd-cm"
    )))

    try:
        project = get_project(app, server)
    except Exception as e:
        results.append((False, f"applications,get on {app!r}: FAILED — {e}"))
        return results
    results.append((True, f"applications,get on {app!r}: allowed (project={project!r})"))

    try:
        exec_allowed = _can_i_exec_create(server, project, app)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        results.append((False, f"exec,create check FAILED to run — {e}"))
        return results
    results.append((exec_allowed, (
        f"exec,create on {project}/{app}: allowed" if exec_allowed else
        f"exec,create on {project}/{app}: DENIED — ask your ArgoCD admin to grant "
        f"the exec,create RBAC action for this app/project"
    )))
    return results


def get_container_for_pod(app, pod_name, pod_namespace, server=None, tree=None):
    """The container of *this specific pod's owning workload* (Deployment,
    StatefulSet, or DaemonSet) — not just "the first Deployment found
    anywhere in the app's manifests", which silently returns the wrong
    container for an app with more than one Deployment, and never resolved
    anything for a StatefulSet/DaemonSet-owned pod at all. Ownership is read
    from the resource tree's own parentRefs, not assumed.

    Deliberately reads containers from the *manifests* (declared spec), not
    the live pod: a live pod can carry containers injected outside the
    declared spec (e.g. an `istio-proxy` sidecar) that would otherwise be
    picked as "first container" ahead of the actual workload container —
    confirmed live against a real cluster, where a sidecar-injected pod's
    actual container order put the injected sidecar first, ahead of the
    container the Deployment's own spec declared.

    Returns (container, other_containers): the first declared container of
    the owning workload, and the rest, if that workload itself declares more
    than one — same transparency as `resolve()` gives for multiple pods,
    since silently picking among several containers is as much a guess as
    silently picking among several pods.
    """
    server = server or default_server()
    _check_allowed(server)
    tree = tree if tree is not None else _resource_tree(app, server)
    owner = _owning_workload(tree, pod_name, pod_namespace)

    tok = token_for(server)
    url = f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}/manifests"
    data = _get_json(url, tok, 30)
    workloads = []  # (kind, name, [container names in declared order])
    for raw in data.get('manifests', []):
        doc = json.loads(raw)
        if doc.get('kind') in _WORKLOAD_KINDS:
            pod_spec = doc.get('spec', {}).get('template', {}).get('spec', {})
            containers = [c['name'] for c in pod_spec.get('containers', [])]
            if containers:
                workloads.append((doc['kind'], doc['metadata']['name'], containers))
    if not workloads:
        raise RuntimeError(
            f"no Deployment/StatefulSet/DaemonSet with a container "
            f"found in app {app!r}'s manifests")
    if owner:
        owner_kind, owner_name = owner
        for kind, name, containers in workloads:
            if kind == owner_kind and name == owner_name:
                return containers[0], containers[1:]
    # Couldn't resolve ownership (pod not owned by a recognized workload, or
    # the tree didn't have it) — fall back to the old heuristic, but this
    # path is now the exception, not the default.
    return workloads[0][2][0], workloads[0][2][1:]


def resolve(app, server=None, pod=None):
    """Fill in project/namespace/pod/container from just an app name — closes
    the gap ArgoCD's own CLI leaves open (its `context` is server-level only,
    unlike kubectl's per-namespace default). Picks the first Healthy pod when
    `pod` isn't given; returns every candidate too, since picking silently
    among several would be a worse surprise than naming the choice. The
    container is resolved from *this specific pod's* owning Deployment, not
    just any Deployment in the app — see get_container_for_pod()."""
    server = server or default_server()
    _check_allowed(server)
    tree = _resource_tree(app, server)
    pods = [
        {'namespace': n['namespace'], 'name': n['name'],
         'health': n.get('health', {}).get('status', '')}
        for n in tree.get('nodes', []) if n.get('kind') == 'Pod'
    ]
    if not pods:
        raise RuntimeError(f"no pods found for app {app!r}")
    if pod:
        matches = [p for p in pods if p['name'] == pod]
        if not matches:
            raise RuntimeError(
                f"pod {pod!r} not found in app {app!r}'s pods: {[p['name'] for p in pods]}")
        chosen = matches[0]
    else:
        healthy = [p for p in pods if p['health'] == 'Healthy'] or pods
        chosen = healthy[0]
    container, other_containers = get_container_for_pod(
        app, chosen['name'], chosen['namespace'], server, tree=tree)
    return {
        'project': get_project(app, server),
        'namespace': chosen['namespace'],
        'pod': chosen['name'],
        'container': container,
        'other_containers': other_containers,
        'candidates': [p['name'] for p in pods],
    }


def extract_output(raw, start_marker, end_marker):
    """Isolate a command's own output between two markers this project
    controls, rather than by locating the echo of the (potentially long)
    full command line.

    The terminal always echoes the input line, literal text and all, before
    the shell begins executing any of it — so `start_marker`'s literal text
    appears twice: once in that echo, once as the real output of the
    `printf` that emits it. The *first* occurrence is always the echo and
    the *last* is always the real one, regardless of how long the rest of
    the compound command is — which matters because the echo of a long
    line is more exposed to being split across a terminal wrap boundary
    than a short, fixed marker is. `end_marker` still needs the
    echo-vs-expanded distinction `%s` vs `\\d+` gives it, since its own
    value (the exit code) isn't known until the command finishes.
    """
    start = raw.rfind(start_marker)
    body_start = raw.find('\n', start) + 1 if start != -1 else 0
    end = re.search(re.escape(end_marker) + r'\d+__', raw[body_start:])
    body_end = body_start + end.start() if end else len(raw)
    return raw[body_start:body_end].strip('\n')


class _ReconnectRequested(Exception):
    """Internal signal: the server sent a `{"Code": ...}` TerminalCommand
    frame, asking the client to close and reopen the socket (observed cause:
    an auth token rotation). Never raised past run() — it's caught and
    turned into a reconnect-and-retry there, the same as a hard drop."""


class PodSession:
    """One persistent shell inside a pod, reused across multiple commands.

    Each `run()` call appends a nonced sentinel to the command and blocks
    until the shell prints its *expanded* form, recovering the exit code the
    TTY protocol otherwise discards. The underlying websocket is opened once
    in `connect()` and kept alive across calls — that reuse, not the sentinel
    trick alone, is what makes this a session rather than a one-shot client.
    """

    def __init__(self, app, pod, container, namespace, project, server=None, shell=None):
        self.server = server or default_server()
        _check_allowed(self.server)
        self.app = app
        self.pod = pod
        self.container = container
        self.namespace = namespace
        self.project = project
        self.shell = shell  # None lets ArgoCD fall back through its own allow-list
        self.ws = None

    def connect(self):
        tok = token_for(self.server)
        params = {
            'pod': self.pod, 'container': self.container, 'appName': self.app,
            'appNamespace': 'argocd', 'projectName': self.project,
            'namespace': self.namespace,
        }
        if self.shell:
            params['shell'] = self.shell
        q = urllib.parse.urlencode(params)
        self.ws = websocket.create_connection(
            f"wss://{self.server}/terminal?{q}",
            header=[
                f"Cookie: argocd.token={tok}",
                "Sec-WebSocket-Protocol: v4.channel.k8s.io",
            ],
            sslopt={"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()},
            timeout=RECV_POLL,
            origin=f"https://{self.server}",
            host=self.server,
        )
        self.ws.settimeout(RECV_POLL)
        self.ws.send(json.dumps({"operation": "resize", "cols": 200, "rows": 50}))

    def run(self, cmd, timeout=20):
        """Run one command, reconnecting once if the session has dropped —
        including a server-*requested* reconnect (see _ReconnectRequested),
        not just a socket-level drop.

        Returns (output: str, exit_code: int | None). exit_code is None only
        if the command never completed within `timeout` — a real timeout, not
        a dropped connection (that raises after the retry).
        """
        if self.ws is None:
            self.connect()
        try:
            return self._run_once(cmd, timeout)
        except (websocket.WebSocketConnectionClosedException, ConnectionError,
                OSError, _ReconnectRequested):
            self.connect()
            return self._run_once(cmd, timeout)

    def _run_once(self, cmd, timeout):
        nonce = uuid.uuid4().hex[:8]
        start_marker = f"__START_{nonce}__"
        end_marker = f"__DONE_{nonce}_"
        payload = f'printf \'{start_marker}\\n\'; {cmd}; printf \'\\n{end_marker}%s__\\n\' "$?"\n'
        self.ws.send(json.dumps({"operation": "stdin", "data": payload}))

        done_re = re.compile(re.escape(end_marker) + r'(\d+)__')
        chunks = []
        exit_code = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                m = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not m:
                continue
            try:
                d = json.loads(m)
            except (ValueError, TypeError):
                continue
            # ArgoCD's own web terminal treats this as "close and reopen the
            # socket": server/application/websocket.go sends
            # TerminalCommand{Code: 1} (preceded by a human-readable stdout
            # warning) when it detects the auth token was rotated
            # server-side. Whatever partial output this attempt collected is
            # discarded — run() reconnects and reissues the whole command,
            # the same as it does for a hard socket drop.
            if d.get("Code") is not None:
                raise _ReconnectRequested(d.get("Code"))
            if d.get("operation") == "stdout":
                chunks.append(d.get("data", ""))
                match = done_re.search(ANSI.sub('', ''.join(chunks)))
                if match:
                    exit_code = int(match.group(1))
                    break

        raw = ANSI.sub('', ''.join(chunks)).replace('\r\n', '\n').replace('\r', '\n')
        return extract_output(raw, start_marker, end_marker), exit_code

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except (websocket.WebSocketException, OSError):
                pass  # already closing; nothing left to do with a close-time error
            self.ws = None
