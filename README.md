# argocd-exec-mcp

Run commands in a Kubernetes pod through ArgoCD's own terminal API — no
`kubectl`, no direct cluster network access, no cluster credentials on your
machine. ArgoCD already holds those; this talks to the same `/terminal`
WebSocket endpoint its web UI's terminal tab uses, from the CLI, and (the
main point of this project) as a **persistent multi-command session** that
an AI agent can drive across many separate tool calls without reconnecting
every time — closer to `kubectl exec -it` staying open than to running
`kubectl exec` fresh per command.

**Prerequisite, not a limitation: your ArgoCD instance needs web-based
terminal access already enabled** (`exec.enabled: "true"` in `argocd-cm`,
the same switch that turns on the terminal tab in the web UI), and your
token needs the `applications, get` and `exec, create` RBAC actions for
the target app. This project doesn't turn that feature on or grant that
role; it's a second client for a capability your ArgoCD admin has to have
already set up.

Check both automatically instead of finding out opaquely partway through a
real exec attempt:

```
argocd-exec --app <app> --check
```

```
OK   ArgoCD's terminal feature (execEnabled) is enabled
OK   applications,get on 'my-app': allowed (project='my-project')
OK   exec,create on my-project/my-app: allowed
```

Exits non-zero if anything fails, so it's scriptable
(`argocd-exec --app <app> --check || echo "not ready"`). Each line is a
real check, not a guess: `execEnabled` reads ArgoCD's own
`/api/v1/settings`; `applications,get` is proven by actually calling the
API that needs it, not a permission dry-run; `exec,create` uses ArgoCD's
own `/api/v1/account/can-i/...` RBAC-reflection endpoint, since there's no
cheaper real call that exercises it short of opening a terminal websocket.

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

Prerequisites: Python 3.10+, and an authenticated `argocd` CLI session —
this project reads its auth token straight out of
`~/.config/argocd/config`, the same file `argocd login` writes.

```
git clone https://github.com/arpanjayeshkumarshukla/argocd-exec-mcp.git
cd argocd-exec-mcp

python3 -m venv .venv
.venv/bin/pip install -e .

# not already logged in? do this first, then re-run the check below
argocd login <your-argocd-server>

# verify: should list at least one pod for an app you have access to
.venv/bin/argocd-exec --app <an-app-you-can-see> --list-pods
```

`--server` defaults to whatever `argocd context` is currently pointed at, so
no server flag is needed if you're already logged into the right one.

Only doing local development (running the test suite, linting)? See
`CONTRIBUTING.md` for the `.[dev]` extra instead.

Want the binaries on your `PATH` without a venv to think about?
`pipx install .` from inside the cloned repo works the same way, and is the
more common way to install a small CLI tool like this one.

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
shell already tokenizes it.

**Usage note, not a limitation of this tool**: a compound command (`&&`,
`;`, pipes) has to be one pre-quoted argument —
`argocd-exec --app <app> -- 'echo one && echo two'` — because your *local*
shell, not `argocd-exec`, is what splits unquoted `&&`/`;` before this
program ever sees them. This is true of any program invoked with trailing
arguments (`ssh host cmd`, `docker exec container cmd` have the exact same
requirement); no CLI design choice here could change it.

### MCP server setup

`argocd-exec-mcp-server` is a stdio MCP server. With the Claude Code CLI:

```
claude mcp add argocd-exec-mcp -s user -- /path/to/venv/bin/argocd-exec-mcp-server
```

(`-s user` registers it for every project, not just the current one; use
`-s local` to scope it to one repo instead.) For any other MCP-speaking
client, the generic config shape is:

```json
{
  "mcpServers": {
    "argocd-exec-mcp": {
      "command": "/path/to/venv/bin/argocd-exec-mcp-server"
    }
  }
}
```

Restrict it to specific ArgoCD servers with an `env` block —
`{"ARGOCD_EXEC_ALLOW_SERVERS": "argo.example.com"}` — the same variable
described below.

### For AI agents

`open_session` takes the same `app`-alone shortcut as the CLI and returns
exactly what it resolved (`pod`, `container`, `namespace`, `project`,
`other_pods`) — read that response before assuming which pod you're talking
to, especially `other_pods`, since a silent pick among several is a worse
surprise than a named one. The intended pattern for a multi-command task:

1. `open_session(app=...)` once — reuse the same `session_id` for every
   command in the task, don't reopen per command.
2. `run(session_id, cmd)` as many times as needed. Shell state (cwd,
   exported env vars) persists across calls, same as a human typing into
   one terminal.
3. `close_session(session_id)` when done — an open session outlives the
   task it was opened for otherwise, and `list_open_sessions` exists so a
   later turn can find and reuse one instead of leaking a duplicate.

There's no separate "skill" document for this project by design: the tool
descriptions above are the whole of the agent-facing documentation, kept in
one place so they can't drift from what the code actually does.

## Restricting which servers this will touch

`ARGOCD_EXEC_ALLOW_SERVERS` — a comma-separated allow-list of hostnames.
Unset means "whatever `argocd login` already trusts." This is for pinning a
deployment to one environment on purpose, not a default restriction.

## Known limitations

Genuinely inherent (not fixable by any design choice here — see the usage
note above for the shell-quoting case):

- **`--interactive` needs a real TTY** (`tty.setraw` on `sys.stdin`) —
  that's what "raw interactive terminal" means, not a gap. It's been
  exercised both programmatically (a `pty`-driven test: multiple sequential
  commands, a `SIGWINCH`, a clean remote `exit`, which caught and fixed a
  real hang — see CHANGELOG) and **live, by hand, in a real terminal**:
  arrow-key history navigation, mid-line cursor movement, and a clean
  `exit` all confirmed working.
- **Pod auto-resolution picks the first `Healthy` pod** among several —
  not solvable beyond that, since interchangeable replicas are the point of
  a Deployment/StatefulSet/DaemonSet; there's no signal that would make one
  "more correct" to pick than another. Pass `--pod` when you need a
  specific one, not just any healthy replica.

Actually fixable, and fixed:

- Container resolution now covers **Deployment, StatefulSet, and
  DaemonSet** (previously Deployment-only) by walking the resource tree's
  own ownership chain — directly for a StatefulSet/DaemonSet-owned pod, one
  hop further through a ReplicaSet for a Deployment-owned one — rather than
  assuming which workload a pod belongs to. Reads containers from the
  *manifests* (declared spec), not the live pod: a live pod can carry
  containers injected outside the declared spec (e.g. an `istio-proxy`
  sidecar) that would otherwise be picked ahead of the real workload
  container — confirmed live, where a pod's actual container order was
  `[istio-proxy, <app container>]`.
- **A workload that itself declares more than one container** no longer
  silently picks the first — `other_containers` in the response says what
  else was there, the same transparency multiple pods already got.
- A workload kind this project doesn't recognize (a bare Pod, a Job, or
  anything not Deployment/StatefulSet/DaemonSet) still can't resolve a
  container automatically — pass `--container` explicitly for those.

Not root-cause-confirmed, mitigated anyway:

- **The echo-boundary detection rarely failed right after a pod restart** —
  observed once live, immediately after an unrelated deployment rollout,
  where the very first command against a freshly spawned shell came back
  with the echoed input still attached. Plausibly a race between the shell
  echoing at its default terminal width and the `resize` sent in
  `connect()` landing — a long echoed line is exposed to a wrap boundary in
  a way a short one isn't. Mitigated by no longer depending on the echo at
  all: output is now bracketed between two short markers this project
  controls (see CHANGELOG), rather than by searching for the echo of a
  potentially long command line. Root cause unconfirmed, since the original
  failure couldn't be reproduced on demand to verify against directly.

## What's not here yet, and why

Not a backlog of forgotten work — a record of what was deliberately
deferred, and the condition under which each item would become worth
doing, checked directly rather than guessed at where that was possible.

**Blocked on this repo being public, not on effort:**

- **PyPI publishing** (`pip install argocd-exec-mcp`). A private package
  has no public-PyPI equivalent; going public would need a
  `publish.yml` + trusted-publisher setup.
- **CodeQL.** Verified, not assumed: `cookiecutter-pypackage` — the same
  template this repo's hygiene was diffed against — gates its own CodeQL
  workflow behind `repository.private == false || GitHub Advanced
  Security`. Free once public; requires a paid GHAS plan otherwise.
- **Dependabot auto-merge.** `allow_auto_merge` cannot be enabled on this
  repo — confirmed by repeatedly calling the GitHub API directly and
  observing the setting silently stay `false`, not assumed from docs.
  Free once public or on GitHub Team/Enterprise.

**Deliberately deferred at solo-maintainer scale — revisit if that
changes:**

- **`CODE_OF_CONDUCT.md`, issue templates, PR template.** GitHub's own
  Community Standards check
  (`/repos/{owner}/{repo}/community/profile`) currently scores this repo
  **71%**; these three are exactly what's missing from 100% — checked live,
  not estimated.
- **A docs/ site** (MkDocs/Sphinx) + its own publish workflow + ReadTheDocs
  — the README covers this project's actual size today.
- **A `justfile` or release-automation script** — `CONTRIBUTING.md`'s plain
  commands are enough for a solo maintainer's cadence so far.
- **Codecov** — needs an external account and a token; `pytest-cov`'s
  CI-log-only report gets the same visibility with nothing to sign up for
  (its private-repo terms also weren't confirmed free, unlike everything
  actually added).
- **release-drafter / PR auto-labeling** — pay off once PRs come from
  people other than the maintainer; no signal for that yet.
- **Poetry as the build backend, or a `src/` layout** — both legitimate
  alternatives to what's here (plain `pip`+`setuptools`, a flat package
  dir), but switching now would be a disruptive re-platform for a
  stylistic difference, not a functional gap.

**Known gaps in what's actually tested:**

- **`cli.py` and `mcp_server.py` sit at 0% direct unit-test coverage** (see
  CI's `pytest --cov` output) — exercised so far by live testing against a
  real ArgoCD instance, not by anything in `tests/`. `session.py`'s core
  logic — the part with the actual protocol complexity — is at 89%.
- **No live ArgoCD/Kubernetes suite runs in CI** — it can't reach a real
  cluster. Anything touching `PodSession.connect()` or the protocol-facts
  list at the top of `session.py` needs manual verification before a
  release; see `CONTRIBUTING.md`.
