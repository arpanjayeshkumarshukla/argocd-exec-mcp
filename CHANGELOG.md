# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] — 2026-09-11

Initial release.

### Added

- `PodSession`: a persistent shell session over ArgoCD's `/terminal`
  WebSocket. Recovers per-command output boundaries and exit codes via a
  nonced sentinel, since the underlying channel is a TTY exec with no
  in-band exit status and merged stdout/stderr.
- `argocd-exec` CLI: one-shot commands, `--list-pods`, and `--interactive`
  (a real raw-terminal shell, closer to `kubectl exec -it`).
- `argocd-exec-mcp-server`: an MCP server exposing `open_session` / `run` /
  `close_session` / `list_open_sessions`, so an agent can hold one session
  open across many tool calls instead of reconnecting per command.
- App-only auto-resolution: `--app` alone is enough for the CLI and
  `open_session` — pod, container, namespace, and project are all derived,
  with container resolution walking the real `Pod -> ReplicaSet ->
  Deployment` ownership chain rather than guessing "the first Deployment
  found."
- Reconnect handling for both a hard socket drop and a server-requested
  reconnect (`{"Code": ...}`, sent when ArgoCD detects the auth token was
  rotated — found by reading `pod-terminal-viewer.tsx` and
  `server/application/websocket.go`, not hit live).
- `ARGOCD_EXEC_ALLOW_SERVERS` — an opt-in allow-list for pinning a
  deployment to specific ArgoCD servers.
- Test suite (unit tests against fixtures + mocked-websocket integration
  tests — see `CONTRIBUTING.md` for what's covered and what isn't), CI,
  Dependabot, `ruff`/`mypy`.

### Fixed during development, before this first tagged release

- Argv reconstruction now uses `shlex.join` instead of `' '.join`, so a
  command like `-- node -e "console.log(1)"` survives to the remote shell
  correctly instead of hitting a syntax error on the unquoted `(`.
- `--interactive` no longer hangs after a couple of commands: `select()` on
  the TLS-wrapped websocket socket doesn't reliably report readability once
  SSL starts buffering decrypted data internally; the reader now runs on its
  own thread instead of sharing a `select()` call with the raw stdin fd.
- Container resolution now correlates to the specific pod chosen (via the
  ownership chain) instead of returning "the first Deployment found
  anywhere in the app," which silently returned the wrong container for any
  app with more than one Deployment.
