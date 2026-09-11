# argocd-exec-mcp

Run commands in a Kubernetes pod through ArgoCD's own terminal API — no
`kubectl`, no direct cluster network access, no cluster credentials on your
machine. ArgoCD already holds those; this talks to the same `/terminal`
WebSocket endpoint its web UI's terminal tab uses, from the CLI, and (the
main point of this project) as a **persistent multi-command session** that
an AI agent can drive across many separate tool calls without reconnecting
every time — closer to `kubectl exec -it` staying open than to running
`kubectl exec` fresh per command.

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

```
# find pods
argocd-exec --app <app> --list-pods

# one-shot
argocd-exec --app <app> --pod <pod> --container <c> \
            --namespace <ns> --project <proj> -- <command...>

# interactive (real terminal)
argocd-exec --app <app> --pod <pod> --container <c> \
            --namespace <ns> --project <proj> --interactive
```

For the MCP server, point your MCP client config at
`argocd-exec-mcp-server` (stdio transport).

## Restricting which servers this will touch

`ARGOCD_EXEC_ALLOW_SERVERS` — a comma-separated allow-list of hostnames.
Unset means "whatever `argocd login` already trusts." This is for pinning a
deployment to one environment on purpose, not a default restriction.

## Known limitations

- **Argument quoting**: the CLI joins `cmd` argv with spaces
  (`' '.join(...)`) before sending it to the remote shell, so a locally
  pre-quoted compound command survives (`argocd-exec ... -- 'sh -c "..."'`)
  but shell metacharacters split across separate argv elements by your local
  shell (e.g. unquoted parentheses) won't reconstruct correctly remotely.
  Pre-quote the whole remote command as one argument.
- **`--interactive` needs a real TTY** (`tty.setraw` on `sys.stdin`) and
  hasn't been exercised through an automated test for that reason — verify
  it in your own terminal before relying on it.
- Requires the ArgoCD server's terminal feature to be enabled
  (`ExecEnabled` in ArgoCD's settings) and your token to carry the
  `applications, get` and `exec, create` RBAC actions for the target app.
