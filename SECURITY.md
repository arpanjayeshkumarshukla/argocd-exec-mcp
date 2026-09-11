# Security

## Why this matters more than a typical CLI

This project reads your ArgoCD auth token straight out of
`~/.config/argocd/config` and uses it to open a shell inside a live pod. A
bug here doesn't just misbehave locally — it can misuse a credential that
grants `exec, create` on real workloads. Treat findings accordingly.

## Reporting a vulnerability

Open a private report via this repo's **Security** tab
("Report a vulnerability"), or open an issue if that's unavailable to you —
please don't include exploit details in a public issue; a short description
of the class of problem is enough to start a conversation.

This is a solo-maintained project. There's no SLA, but security reports get
priority over everything else in the backlog.

## Scope

In scope: the CLI (`argocd-exec`), the MCP server
(`argocd-exec-mcp-server`), and `argocd_exec_mcp.session`. Out of scope:
ArgoCD itself, and anything about a specific organization's RBAC or network
configuration — those are the deploying organization's responsibility, not
this project's.
