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
"""
import json
import os
import re
import ssl
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


def _resource_tree(app, server):
    tok = token_for(server)
    url = (f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}"
           f"/resource-tree?appNamespace=argocd")
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {tok}'})
    return json.load(urllib.request.urlopen(req, timeout=30, context=SSL_CTX))


def list_pods(app, server=None):
    server = server or default_server()
    _check_allowed(server)
    tree = _resource_tree(app, server)
    return [
        {'namespace': n['namespace'], 'name': n['name'],
         'health': n.get('health', {}).get('status', '')}
        for n in tree.get('nodes', []) if n.get('kind') == 'Pod'
    ]


def _owning_deployment(tree, pod_name, pod_namespace):
    """Walk Pod -> ReplicaSet -> Deployment via the resource tree's own
    parentRefs, rather than assuming which Deployment a pod belongs to."""
    by_key = {(n['kind'], n.get('namespace'), n['name']): n for n in tree.get('nodes', [])}
    pod = by_key.get(('Pod', pod_namespace, pod_name))
    if not pod:
        return None
    for ref in pod.get('parentRefs') or []:
        rs = by_key.get((ref['kind'], ref['namespace'], ref['name']))
        if rs and rs['kind'] == 'ReplicaSet':
            for dref in rs.get('parentRefs') or []:
                if dref['kind'] == 'Deployment':
                    return dref['name']
    return None


def get_project(app, server=None):
    server = server or default_server()
    _check_allowed(server)
    tok = token_for(server)
    url = f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}"
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {tok}'})
    data = json.load(urllib.request.urlopen(req, timeout=15, context=SSL_CTX))
    project = data.get('spec', {}).get('project')
    if not project:
        raise RuntimeError(f"app {app!r} has no spec.project in its ArgoCD Application object")
    return project


def get_container_for_pod(app, pod_name, pod_namespace, server=None, tree=None):
    """The first container of *this specific pod's owning Deployment* — not
    just "the first Deployment found anywhere in the app's manifests", which
    silently returns the wrong container for an app with more than one
    Deployment. Ownership is read from the resource tree's own parentRefs
    (Pod -> ReplicaSet -> Deployment), not assumed.

    Deliberately reads containers from the *manifests* (declared spec), not
    the live pod: a live pod can carry containers injected outside the
    declared spec (e.g. an `istio-proxy` sidecar) that would otherwise be
    picked as "first container" ahead of the actual workload container —
    confirmed live: a pod here has containers
    `['istio-proxy', 'pm-performance-test-chart']` in that order, while the
    Deployment's own spec only ever declared the second."""
    server = server or default_server()
    _check_allowed(server)
    tree = tree if tree is not None else _resource_tree(app, server)
    deployment_name = _owning_deployment(tree, pod_name, pod_namespace)

    tok = token_for(server)
    url = f"https://{server}/api/v1/applications/{urllib.parse.quote(app)}/manifests"
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {tok}'})
    data = json.load(urllib.request.urlopen(req, timeout=30, context=SSL_CTX))
    deployments = []
    for raw in data.get('manifests', []):
        doc = json.loads(raw)
        if doc.get('kind') == 'Deployment':
            containers = doc.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
            if containers:
                deployments.append((doc['metadata']['name'], containers[0]['name']))
    if not deployments:
        raise RuntimeError(f"no Deployment with a container found in app {app!r}'s manifests")
    if deployment_name:
        for name, container in deployments:
            if name == deployment_name:
                return container
    # Couldn't resolve ownership (pod not owned by a ReplicaSet/Deployment,
    # or the tree didn't have it) — fall back to the old heuristic, but this
    # path is now the exception, not the default.
    return deployments[0][1]


def resolve(app, server=None, pod=None):
    """Fill in project/namespace/pod/container from just an app name — closes
    the gap ArgoCD's own CLI leaves open (its `context` is server-level only,
    unlike kubectl's per-namespace default). Picks the first Healthy pod when
    `pod` isn't given; returns every candidate too, since picking silently
    among several would be a worse surprise than naming the choice. The
    container is resolved from *this specific pod's* owning Deployment, not
    just any Deployment in the app — see get_container_for_pod()."""
    server = server or default_server()
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
    return {
        'project': get_project(app, server),
        'namespace': chosen['namespace'],
        'pod': chosen['name'],
        'container': get_container_for_pod(app, chosen['name'], chosen['namespace'], server, tree=tree),
        'candidates': [p['name'] for p in pods],
    }


def extract_output(raw, marker):
    """Isolate a command's own output from the TTY's echo and sentinel noise.

    The terminal echoes the exact bytes sent (unexpanded — literal `%s`, not a
    digit) before the shell runs anything, marking where real output begins.
    The *expanded* sentinel (`marker` + digits) marks where it ends.
    """
    echo = marker + '%s__'
    start = raw.find(echo)
    if start != -1:
        nl = raw.find('\n', start)
        body_start = nl + 1 if nl != -1 else start + len(echo)
    else:
        body_start = 0
    end = re.search(re.escape(marker) + r'\d+__', raw[body_start:])
    body_end = body_start + end.start() if end else len(raw)
    return raw[body_start:body_end].strip('\n')


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
        """Run one command, reconnecting once if the session has dropped.

        Returns (output: str, exit_code: int | None). exit_code is None only
        if the command never completed within `timeout` — a real timeout, not
        a dropped connection (that raises after the retry).
        """
        if self.ws is None:
            self.connect()
        try:
            return self._run_once(cmd, timeout)
        except (websocket.WebSocketConnectionClosedException, ConnectionError, OSError):
            self.connect()
            return self._run_once(cmd, timeout)

    def _run_once(self, cmd, timeout):
        nonce = uuid.uuid4().hex[:8]
        marker = f"__DONE_{nonce}_"
        payload = f'{cmd}; printf \'\\n{marker}%s__\\n\' "$?"\n'
        self.ws.send(json.dumps({"operation": "stdin", "data": payload}))

        done_re = re.compile(re.escape(marker) + r'(\d+)__')
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
            if d.get("operation") == "stdout":
                chunks.append(d.get("data", ""))
                match = done_re.search(ANSI.sub('', ''.join(chunks)))
                if match:
                    exit_code = int(match.group(1))
                    break

        raw = ANSI.sub('', ''.join(chunks)).replace('\r\n', '\n').replace('\r', '\n')
        return extract_output(raw, marker), exit_code

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
