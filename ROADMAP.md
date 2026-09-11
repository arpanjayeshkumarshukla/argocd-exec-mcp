# What's not here yet, and why

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
