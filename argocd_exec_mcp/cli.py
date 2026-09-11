#!/usr/bin/env python3
"""Run a command in a pod via ArgoCD's terminal WebSocket, one-shot or
interactive. See `argocd-exec --help` for full usage."""
import argparse
import json
import os
import select
import shlex
import shutil
import signal
import ssl
import sys
import termios
import threading
import tty
import urllib.parse

import certifi
import websocket

from .session import (
    PodSession,
    allowed_servers,
    check_prerequisites,
    default_server,
    list_pods,
    resolve,
    token_for,
)


def interactive(app, pod, container, namespace, project, server, shell=None):
    """Raw passthrough session — a human's keystrokes and the pod's own PTY
    output, untouched. Deliberately not sharing extract_output()/the sentinel
    machinery in PodSession: those exist to recover structure a human doesn't
    need (the human sees the real prompt, real echo, real ANSI codes).

    Uses a dedicated reader thread with its own short recv timeout, not
    select() on the raw fd + the TLS-wrapped websocket socket together:
    select() only sees the OS-level socket, but SSL buffers decrypted
    application data internally, so a socket can hold unread data and still
    not be reported readable — select() then blocks forever waiting for a
    read event that already happened below the layer it can see. Confirmed
    live: the session went unresponsive after a couple of commands with the
    select()-based version.
    """
    tok = token_for(server)
    params = {
        'pod': pod, 'container': container, 'appName': app,
        'appNamespace': 'argocd', 'projectName': project,
        'namespace': namespace,
    }
    if shell:
        params['shell'] = shell
    q = urllib.parse.urlencode(params)
    ws = websocket.create_connection(
        f"wss://{server}/terminal?{q}",
        header=[f"Cookie: argocd.token={tok}", "Sec-WebSocket-Protocol: v4.channel.k8s.io"],
        sslopt={"cert_reqs": ssl.CERT_REQUIRED, "ca_certs": certifi.where()},
        origin=f"https://{server}", host=server,
    )
    ws.settimeout(1)  # recv() wakeup interval for the reader thread, not a completion signal

    def send_resize(*_):
        cols, rows = shutil.get_terminal_size()
        try:
            ws.send(json.dumps({"operation": "resize", "cols": cols, "rows": rows}))
        except Exception:
            pass

    send_resize()
    try:
        signal.signal(signal.SIGWINCH, send_resize)
    except (ValueError, AttributeError):
        pass  # not the main thread, or no SIGWINCH on this platform

    done = threading.Event()

    def reader():
        while not done.is_set():
            try:
                m = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketConnectionClosedException, OSError):
                break
            if not m:
                break
            try:
                d = json.loads(m)
            except (ValueError, TypeError):
                continue
            if d.get("operation") == "stdout":
                sys.stdout.write(d.get("data", ""))
                sys.stdout.flush()
        done.set()

    threading.Thread(target=reader, daemon=True).start()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        while not done.is_set():
            r, _, _ = select.select([fd], [], [], 0.5)  # plain OS fd only — safe to select() on
            if fd in r:
                data = os.read(fd, 1024)
                if not data:
                    break
                try:
                    payload = {"operation": "stdin", "data": data.decode(errors='replace')}
                    ws.send(json.dumps(payload))
                except Exception:
                    break
    finally:
        done.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        try:
            ws.close()
        except Exception:
            pass


def main():
    p = argparse.ArgumentParser(
        prog='argocd-exec',
        description=(
            "Run a command in a pod via ArgoCD's terminal WebSocket, one-shot "
            "or interactive. --app alone is enough — pod/container/namespace/"
            "project are auto-resolved; pass them explicitly to override."
        ),
        epilog=(
            "examples:\n"
            "  argocd-exec --app myapp --list-pods\n"
            "  argocd-exec --app myapp -- echo hello\n"
            "  argocd-exec --app myapp --pod myapp-abc123 -- echo hello\n"
            "  argocd-exec --app myapp --interactive\n"
            "\n"
            "A compound command (&&, ;, pipes) needs one pre-quoted argument, "
            "since your local shell splits unquoted operators before this "
            "program ever sees them:\n"
            "  argocd-exec --app myapp -- 'echo one && echo two'\n"
            "\n"
            "Set ARGOCD_EXEC_ALLOW_SERVERS (comma-separated hostnames) to "
            "restrict which ArgoCD servers this will ever connect to."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('--server', default=None,
                    help="ArgoCD server hostname; defaults to `argocd context`'s current-context")
    p.add_argument('--app', required=True, help="ArgoCD application name")
    p.add_argument('--pod', help="specific pod name; auto-resolved (first Healthy pod) if omitted")
    p.add_argument('--container',
                    help="specific container name; auto-resolved from the pod's owning "
                         "Deployment if omitted")
    p.add_argument('--namespace', help="Kubernetes namespace; auto-resolved if omitted")
    p.add_argument('--project', help="ArgoCD project name; auto-resolved if omitted")
    p.add_argument('--shell', default=None,
                    help="shell to request (e.g. bash); omit to let ArgoCD fall back "
                         "through its own allow-list")
    p.add_argument('--timeout', type=int, default=20,
                    help="seconds to wait for a one-shot command to complete (default: 20)")
    p.add_argument('--list-pods', action='store_true',
                    help="list this app's pods (namespace, name, health) and exit")
    p.add_argument('--check', action='store_true',
                    help="verify prerequisites (ExecEnabled, RBAC) for --app and exit; "
                         "exits non-zero on any failed check")
    p.add_argument('--interactive', action='store_true',
                    help="open a real interactive shell (raw terminal), like `kubectl exec -it`")
    p.add_argument('cmd', nargs='*',
                    help="the remote command to run (omit with --list-pods, --check, "
                         "or --interactive)")
    a = p.parse_args()

    server = a.server or default_server()
    allow = allowed_servers()
    if allow and server not in allow:
        sys.exit(f"refusing: {server} not in ARGOCD_EXEC_ALLOW_SERVERS ({', '.join(allow)})")

    if a.check:
        results = check_prerequisites(a.app, server)
        for ok, message in results:
            print(("OK  " if ok else "FAIL") + " " + message)
        sys.exit(0 if all(ok for ok, _ in results) else 1)

    if a.list_pods:
        for pod in list_pods(a.app, server):
            print(pod['namespace'], pod['name'], pod['health'])
        return

    pod, container, namespace, project = a.pod, a.container, a.namespace, a.project
    if not (pod and container and namespace and project):
        # --app alone is enough: pod/container/namespace/project are all
        # derivable from it. ArgoCD's own `context` is server-level only —
        # there's no per-app default the way kubectl has a default namespace —
        # so this is this tool's own convenience, not something argocd gives us.
        resolved = resolve(a.app, server, pod=a.pod)
        filled = []
        if not pod:
            pod = resolved['pod']
            filled.append(f"pod={pod}")
        if not namespace:
            namespace = resolved['namespace']
            filled.append(f"namespace={namespace}")
        container_was_explicit = bool(container)
        if not container:
            container = resolved['container']
            filled.append(f"container={container}")
        if not project:
            project = resolved['project']
            filled.append(f"project={project}")
        other_pods = [p for p in resolved['candidates'] if p != pod]
        note = f"[auto-resolved {', '.join(filled)}"
        if not a.pod and other_pods:
            note += f" — other pods available: {', '.join(other_pods)} (pass --pod to pick one)"
        if not container_was_explicit and resolved['other_containers']:
            note += (f"; other containers in this pod: "
                     f"{', '.join(resolved['other_containers'])} (pass --container to pick one)")
        note += "]"
        print(note, file=sys.stderr)

    if a.interactive:
        return interactive(a.app, pod, container, namespace, project, server, a.shell)

    if not a.cmd:
        sys.exit("missing required: command (or pass --interactive)")

    # shlex.join, not ' '.join: argparse's nargs='*' has already split cmd into
    # argv elements per the *local* shell's quoting, so a bare join here would
    # silently drop that quoting before the string reaches the *remote* shell —
    # e.g. ['node', '-e', 'console.log(x)'] rejoined as
    # "node -e console.log(x)" hits a remote syntax error on the unquoted '('.
    session = PodSession(a.app, pod, container, namespace, project, server, a.shell)
    output, exit_code = session.run(shlex.join(a.cmd), a.timeout)
    session.close()
    print(output)
    if exit_code is None:
        print(f"[timeout: no completion marker after {a.timeout}s]", file=sys.stderr)
        sys.exit(124)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
