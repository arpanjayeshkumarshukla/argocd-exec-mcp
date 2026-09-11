# Contributing

## Setup

```
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Before sending a change

```
.venv/bin/ruff check .
.venv/bin/mypy argocd_exec_mcp
.venv/bin/bandit -r argocd_exec_mcp
.venv/bin/pytest -q --cov=argocd_exec_mcp --cov-report=term-missing
```

All four run in CI (`.github/workflows/ci.yml`) on every push and PR against
`main`, on Python 3.10 and 3.12. Coverage is reported in CI logs only — no
external service, no account to set up.

Or install the git hook once (`.venv/bin/pre-commit install`) and let it run
ruff/mypy/bandit automatically on every commit. The `system`-language hooks
need this repo's own venv active on `PATH` (they run `mypy`/`bandit`
directly, not a bundled copy) — activate `.venv` before committing, same as
you'd need to before running the commands above by hand.

## Test layers

- `tests/test_extract_output.py`, `tests/test_ownership.py`,
  `tests/test_allow_list.py`, `tests/test_default_server.py`,
  `tests/test_resolve.py`, `tests/test_container_resolution.py` — pure-logic
  unit tests, no network. These cover the parts of `session.py` that don't
  need a real ArgoCD server: sentinel/output parsing, the Pod→ReplicaSet→
  Deployment ownership walk, the allow-list, config parsing, and pod/
  container resolution logic, all against fixture data.
- `tests/test_pod_session.py` — `PodSession.run()` against a scripted fake
  websocket (`ScriptedWS`). Covers protocol-level behavior that's easy to get
  wrong: idle time vs. real completion, exit-code recovery, reconnect-on-drop.
- **What's deliberately not here**: a live ArgoCD/Kubernetes integration
  suite. CI can't reach a real cluster, so anything that needs one (the
  websocket handshake itself, `--interactive`'s raw-terminal behavior, a
  real multi-Deployment app) has to be verified by hand against a real
  ArgoCD instance before a release. If you're changing `PodSession.connect()`
  or anything in the protocol-facts list at the top of `session.py`, say so
  in your PR and describe how you verified it live.

## Scope

This project is a second client for ArgoCD's existing terminal API — it
doesn't turn on `ExecEnabled` or grant RBAC, and it isn't trying to become a
general Kubernetes client. Changes that only make sense for one
organization's environment (hardcoded hostnames, org-specific policy)
belong in that organization's own wrapper, not here — see
`ARGOCD_EXEC_ALLOW_SERVERS` for how a deployment is meant to add its own
constraints without changing this code.

## Reporting a security issue

See `SECURITY.md`.
