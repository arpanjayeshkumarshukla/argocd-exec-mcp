# Changelog

All notable changes to this project are documented here. The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Kubernetes Events**: `argocd-exec --events` and the `list_events` MCP tool surface Kubernetes events (`FailedScheduling`, `ImagePullBackOff`, `OOMKilled`, and the like) across an ArgoCD app's resources — the same data ArgoCD's own web UI Events tab shows. Defaults to Warning-type events; `--include-normal` / `include_normal=True` also includes routine events.

### Fixed

- **`resolve()` allow-list gate**: `resolve()` now checks `ARGOCD_EXEC_ALLOW_SERVERS` before its first network call, matching every other function in `session.py`. Previously it could send a request to a disallowed server before anything rejected it.

## [0.1.0] — 2026-09-11

Initial release.

### Added

- **`argocd-exec` CLI**: A new command-line tool for running commands in ArgoCD applications. Supports one-off commands, discovering available pods (`--list-pods`), and launching fully interactive, real-time shells (`--interactive`, similar to `kubectl exec -it`).
- **MCP Server for AI Agents**: Added `argocd-exec-mcp-server`, exposing standard tools (`open_session`, `run`, `close_session`, `list_open_sessions`). This allows AI agents to maintain persistent shell state (like directories and environment variables) across multiple interactions without the overhead of reconnecting per command.
- **Smart Resource Resolution**: Supplying just the `--app` flag automatically discovers the correct project, namespace, pod, and container for your application, eliminating the need to manually look up Kubernetes resource names.
- **Persistent Sessions**: Built-in session management ensures connections stay alive across multiple commands, accurately capturing output boundaries and exit codes for every command run.
- **Resilient Connections**: Sessions automatically recover from unexpected network drops or server-side disconnections (such as ArgoCD authentication token rotations).
- **Server Allow-listing**: Introduced the `ARGOCD_EXEC_ALLOW_SERVERS` environment variable to strictly limit which ArgoCD environments the tool is permitted to communicate with.
