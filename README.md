# argocd-exec-mcp

Run commands in a Kubernetes pod directly through ArgoCD's terminal API. No `kubectl`, no direct cluster network access, and no cluster credentials required on your local machine.

If your ArgoCD instance allows web-based terminal access, this tool lets you do the same from your CLI. More importantly, it provides a **Model Context Protocol (MCP) server** that enables AI agents to maintain persistent, multi-command sessions. Instead of opening a fresh connection for every command, agents can preserve shell state (like directories and environment variables) across multiple tool calls.

---

## Prerequisites

Your ArgoCD administrator must have already enabled web-based terminal access. This tool does not bypass ArgoCD's security; it acts as a client for existing permissions.

* **ArgoCD Config:** `exec.enabled: "true"` must be set in `argocd-cm`.
* **RBAC Permissions:** Your user token needs the `applications, get` and `exec, create` actions for the target app.
* **Local Setup:** Python 3.10+ and an active, authenticated `argocd` CLI session.

---

## Installation

This tool reads its authentication token directly from `~/.config/argocd/config`, just like the standard `argocd` CLI. Make sure you have logged in via `argocd login <your-argocd-server>` before proceeding.

To install the CLI and MCP server globally, `pipx` is the recommended method:

```bash
git clone https://github.com/arpanjayeshkumarshukla/argocd-exec-mcp.git
cd argocd-exec-mcp
pipx install .
```

Alternatively, you can install it into a standard Python virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Only doing local development (running the test suite, linting)? See `CONTRIBUTING.md` for the `.[dev]` extra instead.

---

## Pre-Flight Check

Once installed, you should verify your setup against a target application before trying to run actual commands. The tool can check your ArgoCD server settings and your RBAC permissions automatically:

```bash
argocd-exec --app <app> --check
```

If successful, you will see output confirming that the terminal feature is enabled and your permissions are correctly configured:

```
OK   ArgoCD's terminal feature (execEnabled) is enabled
OK   applications,get on 'my-app': allowed (project='my-project')
OK   exec,create on my-project/my-app: allowed
```

---

## CLI Usage

The `--app` flag is the only required argument. The tool automatically talks to ArgoCD to resolve the project, namespace, pod, and container. If there are multiple pods, it will automatically select the first `Healthy` one and notify you of the others.

* **List available pods:**
  `argocd-exec --app <app> --list-pods`
* **Run a single one-shot command:**
  `argocd-exec --app <app> -- <command...>`
* **Run a command on a specific pod:**
  `argocd-exec --app <app> --pod <pod> -- <command...>`
* **Open a real interactive shell:**
  `argocd-exec --app <app> --interactive`

> **Note on compound commands:** If you are chaining commands together with `&&`, `;`, or pipes `|`, you must quote the entire command string (e.g., `argocd-exec --app <app> -- 'echo one && echo two'`). Otherwise, your local shell will evaluate the operators before passing the command to ArgoCD.

### Full flag reference

The examples above cover the common cases. `argocd-exec --help` is the source of truth; the full set of flags is:

| Flag | Purpose |
| --- | --- |
| `--app` | ArgoCD application name (required) |
| `--server` | ArgoCD server hostname; defaults to `argocd context`'s current-context |
| `--pod` | specific pod name; auto-resolved (first `Healthy` pod) if omitted |
| `--container` | specific container name; auto-resolved from the pod's owning Deployment/StatefulSet/DaemonSet if omitted |
| `--namespace` | Kubernetes namespace; auto-resolved if omitted |
| `--project` | ArgoCD project name; auto-resolved if omitted |
| `--shell` | shell to request (e.g. `bash`); omit to let ArgoCD fall back through its own allow-list |
| `--timeout` | seconds to wait for a one-shot command to complete (default: 20) |
| `--list-pods` | list this app's pods (namespace, name, health) and exit |
| `--check` | verify prerequisites (`execEnabled`, RBAC) for `--app` and exit; exits non-zero on any failed check |
| `--interactive` | open a real interactive shell (raw terminal), like `kubectl exec -it` |

---

## Using with AI Agents (MCP)

The `argocd-exec-mcp-server` exposes ArgoCD terminal access to AI agents via four standard tools: `open_session`, `run`, `close_session`, and `list_open_sessions`.

This design allows an agent to call `open_session` once, use the resulting `session_id` to `run` multiple commands in the same environment, and cleanly `close_session` when the task is complete.

**Adding to Claude Code:**

```bash
claude mcp add argocd-exec-mcp -s user -- argocd-exec-mcp-server
```

**General MCP Configuration (e.g., for Claude Desktop):**

```json
{
  "mcpServers": {
    "argocd-exec-mcp": {
      "command": "argocd-exec-mcp-server"
    }
  }
}
```

---

## Configuration

You can restrict which ArgoCD servers this tool is allowed to communicate with by setting an environment variable:

* **`ARGOCD_EXEC_ALLOW_SERVERS`**: A comma-separated list of allowed hostnames (e.g., `argo.example.com`). If left unset, the tool will trust whichever server your `argocd login` context is currently pointed at.

If you're logged into more than one ArgoCD server, run `argocd context` to see which one is current (marked with `*`) and switch with `argocd context <server>`. This tool defaults to that same current-context, so it's worth checking if you have several `argocd login` sessions and haven't passed `--server` explicitly.

---

## Project status

This project is under active development. See `ROADMAP.md` for what's deliberately not here yet and why (what's blocked on this repo going public, what's deferred at its current solo-maintainer scale, and known gaps in test coverage), `CHANGELOG.md` for what's shipped, and `CONTRIBUTING.md` for how to work on it.
