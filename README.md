# argocd-exec-mcp

Run commands in a Kubernetes pod through ArgoCD's own terminal API — no
`kubectl`, no direct cluster network access, no cluster credentials on your
machine. ArgoCD already holds those; this talks to the same `/terminal`
WebSocket endpoint its web UI's terminal tab uses, from the CLI, and (the
main point of this project) as a **persistent multi-command session** that
an AI agent can drive across many separate tool calls without reconnecting
every time — closer to `kubectl exec -it` staying open than to running
`kubectl exec` fresh per command.

**Assumes your ArgoCD instance already has web-based terminal access
enabled** (`exec.enabled: "true"` in `argocd-cm`, i.e. `ExecEnabled` in
ArgoCD's settings — the same switch that turns on the terminal tab in the
web UI) and that your token/RBAC role carries the `applications, get` and
`exec, create` actions for the app you're targeting. This project doesn't
turn that feature on; it's a second client for a capability your ArgoCD
admin has to have already granted.

## Why this exists

ArgoCD's terminal endpoint (`server/application/terminal.go` in
[argoproj/argo-cd](https://github.com/argoproj/argo-cd)) always execs a
**TTY** shell — `PodExecOptions{TTY: true}` — never a one-shot non-TTY
command, and there's no CLI wrapper around it (`argocd app --help` has no
`exec` subcommand). Two consequences follow directly from that, not from
anything this project chose:

- **stdout and stderr are genuinely merged** at the container-runtime level,
  before ArgoCD ever sees them — same as what a human gets from
  `kubectl exec -it`.
- **There's no in-band exit status for an individual command.** The k8s exec
  protocol reports an exit status for the *shell process*, when it
  terminates — not for each line you type into it. A raw terminal doesn't
  need one; a program driving it does.

So `PodSession.run()` appends a nonced sentinel to every command
(`; printf '\n__DONE_<nonce>_%s__\n' "$?"`) and waits for its *expanded*
form (digits, not the literal `%s` the terminal echoes back before running
anything) — that's the only way to recover a per-command exit code and a
clean output boundary from a TTY-backed shell, ArgoCD or otherwise.

## What's here

- **`argocd_exec_mcp.session.PodSession`** — the shared client: opens the
  websocket once, and `run()` can be called repeatedly, with shell state
  (cwd, exported env vars) persisting across calls, same as a real terminal.
- **`argocd-exec` CLI** — one-shot (`argocd-exec --app ... -- <cmd>`) or a
  real interactive shell (`--interactive`, raw terminal passthrough, closer
  to `kubectl exec -it`).
- **`argocd-exec-mcp-server`** — an MCP server wrapping `PodSession` as
  `open_session` / `run` / `close_session` / `list_open_sessions` tools, so
  an agent can hold one session open across many tool calls instead of
  paying a full connect+auth+handshake per command.

## Install

```
pip install -e .
```

Requires an authenticated `argocd` CLI session (`argocd login <server>`) —
this reads its auth token straight out of `~/.config/argocd/config`, same as
the `argocd` CLI itself. `--server` defaults to whatever `argocd context` is
currently pointed at.

## Usage

`--app` is the only thing you must supply — `--pod`/`--container`/
`--namespace`/`--project` are all derivable from it (ArgoCD's own `context`
concept is server-level only, unlike `kubectl`'s per-namespace default, so
this tool resolves the rest itself: the REST `applications/{app}` object for
`project`, `applications/{app}/manifests` for the first Deployment's first
container, and the resource tree for pod + namespace). When there's more
than one pod, the first `Healthy` one is picked and the tool tells you what
else was available and how to pin one with `--pod`:

```
# find pods
argocd-exec --app <app> --list-pods

# one-shot — app alone is enough
argocd-exec --app <app> -- <command...>

# same, pinned to a specific pod
argocd-exec --app <app> --pod <pod> -- <command...>

# interactive (real terminal)
argocd-exec --app <app> --interactive
```

The remote command's argv is reconstructed with proper shell quoting
(`shlex.join`), so ordinary invocations don't need any extra quoting layer:
`argocd-exec --app <app> -- node -e "console.log(1)"` works as your local
shell already tokenizes it. A compound command (`&&`, `;`, pipes) has to be
one pre-quoted argument instead, since your *local* shell — not this tool —
is what splits unquoted `&&`/`;` before this program ever sees them:
`argocd-exec --app <app> -- 'echo one && echo two'`.

For the MCP server, point your MCP client config at
`argocd-exec-mcp-server` (stdio transport). Its `open_session` tool takes
the same `app`-alone shortcut and returns what it resolved.

## Restricting which servers this will touch

`ARGOCD_EXEC_ALLOW_SERVERS` — a comma-separated allow-list of hostnames.
Unset means "whatever `argocd login` already trusts." This is for pinning a
deployment to one environment on purpose, not a default restriction.

## Known limitations

- **Compound shell syntax needs one pre-quoted argument.** `&&`/`;`/pipes
  between unquoted argv elements are split by your *local* shell before this
  program runs, not by `argocd-exec` — see the quoting note above.
- **`--interactive` needs a real TTY** (`tty.setraw` on `sys.stdin`). It's
  been exercised programmatically against a real pod via a `pty`-driven test
  (multiple sequential commands, a `SIGWINCH`, and a clean remote `exit`),
  which caught and fixed a real hang — `select()` on the TLS-wrapped
  websocket socket doesn't reliably report readability once SSL starts
  buffering decrypted data internally, so the reader now runs on its own
  thread with a short recv timeout instead of sharing a `select()` call with
  the raw stdin fd. What a `pty` test can't stand in for: whether it's
  actually pleasant to type into from a real terminal emulator — verify that
  yourself before relying on it day to day.
- **Pod/container auto-resolution is a heuristic**, not a guarantee: first
  `Healthy` pod, first container of the app's first Deployment. Apps with
  more than one Deployment, or where you need a specific pod (not just any
  healthy replica), should pass `--pod`/`--container` explicitly.
