---
title: ArgoCD App Events Support - Plan
type: feat
date: 2026-09-12
topic: argocd-events
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# ArgoCD App Events Support - Plan

## Goal Capsule

- **Objective:** someone debugging a broken ArgoCD-managed app through `argocd-exec-mcp` can see why it's unhealthy — `FailedScheduling`, `ImagePullBackOff`, `OOMKilled`, a failing probe — without opening the ArgoCD web UI.
- **Means:** a shared `list_events` function in `session.py`, exposed as `--events` on the CLI and `list_events` on the MCP server (KTD1).
- **Authority hierarchy:** the Product Contract (this brainstorm's dialogue, 2026-09-12) sets product scope; the Planning Contract below sets implementation mechanism. On conflict, the Product Contract wins on behavior, the Planning Contract wins on mechanism.
- **Stop conditions:** stop and report, rather than silently adjust, if a live ArgoCD call ever shows the events endpoint doesn't exist or returns a materially different shape than the Assumptions section describes.
- **Execution profile:** a standard code change, one PR, no migration or phased rollout.
- **Tail ownership:** the implementer updates documentation (U5) and resolves the Open Questions' live-verification item before treating this as production-verified, not only merged.

## Product Contract

**Product Contract preservation:** restructured, no scope change. R4 and R5 (the consistency-fix requirements) moved to R7 and R8 to make room for new Event-retrieval requirements R4-R6; R1-R3 are unchanged; R9 is new. All `Governs` links below are re-pointed to match.

### Summary

Add a read-only surface — `--events` on the CLI and a matching `list_events` MCP tool — that fetches Kubernetes Warning-type events across all of an ArgoCD app's resources, the same data ArgoCD's own web UI Events tab shows. Also close a pre-existing gap where `resolve()` skips the server allow-list check other functions in `session.py` apply, and correct a pre-existing README inaccuracy (its MCP tool count already omits `list_pods`) in the same documentation update.

### Problem Frame

`argocd-exec-mcp` already covers exec and `logs`, but a class of failures produces little or nothing in either: a pod stuck in `Pending` on a scheduling failure, an image that can't be pulled, a container repeatedly OOMKilled before it logs anything useful. ArgoCD's own web UI has an Events tab for exactly this, backed by Kubernetes Events on the app's resources — this project has no equivalent, so that failure class still forces a trip to the browser. The need is anticipated from that failure class, not from a specific incident hit so far.

### Key Decisions

- **Whole-app scope, not pod-only** (session-settled: user-directed — chosen over scoping to just the resolved pod: catches a broken sibling resource, such as a bad Service or a different pod in the app, not only the one being exec'd into). Governs R1.
- **Warning-type events by default** (session-settled: user-directed — chosen over showing every event unfiltered: surfaces what's actually broken; an opt-in widens to Normal-type events when needed). Governs R2.
- **Ship the CLI flag and MCP tool together** (session-settled: user-directed — chosen over shipping one first: matches the existing shared-function pattern behind `--list-pods` / `list_pods`). Governs R3.
- **Fix `resolve()`'s missing allow-list gate in this same change** (session-settled: user-directed — chosen over flagging it as a separate finding without fixing it). Governs R7, R8.

### Requirements

**Event retrieval**

- R1. Fetching app events requires only the app identifier (`--app` on the CLI, `app` on the MCP tool) — no pod, container, namespace, or project resolution.
- R2. The response defaults to Warning-type events only; an explicit opt-in (a CLI flag / MCP parameter) includes Normal-type events too.
- R3. The CLI exposes this as a flag and the MCP server exposes it as a tool, both backed by one shared underlying function.
- R4. Each returned event carries type, reason, message, the involved resource's kind and name, occurrence count, and last-seen time — enough to triage without falling back to `kubectl describe`.
- R5. The MCP tool's response states which type filter was applied, so an empty or short result is not misread as "nothing happened."
- R6. The MCP tool caps the number of events it returns; the CLI print path does not.

**Consistency fix**

- R7. The new events-fetching function calls `_check_allowed(server)` before its first network call, matching the pattern already used by `list_pods`, `get_project`, `check_prerequisites`, and `get_container_for_pod`.
- R8. `resolve()` calls `_check_allowed(server)` before its first network use (its call to `_resource_tree`), closing the one gap where it currently doesn't.

**Documentation**

- R9. README's flag reference, the MCP tool count, the CLI `--help` epilog, and the CHANGELOG document the new flag and tool.

### Scope Boundaries

- Not folded into `--check`'s existing preflight output — ships as its own command/tool, matching `--list-pods`'s shape.
- Not narrowed to a single resource within the app (e.g. just the pod being exec'd into) — this ships app-wide only; per-resource scoping is a possible later narrowing, not this change.
- Not adding mutual-exclusivity enforcement across `--check` / `--list-pods` / `--events` / `--interactive` — none of the existing three enforce it against each other today, so singling out the new flag would be inconsistent; fixing that repo-wide is a separate change.
- Not consolidating `_check_allowed` into `_get_json` or `default_server()` as a single enforced choke point — that would touch every function in `session.py`, not only the two this plan changes.

#### Deferred to Follow-Up Work

- Making `_check_allowed` a structural choke point so a future function cannot add a new gating gap by omission the way `resolve()` did.
- Capturing this change as a `docs/solutions/` learning once it lands — the repo currently has no learning corpus at all, so this would seed one.

### Dependencies / Assumptions

- Assumes the ArgoCD server's authenticated `/api/v1/applications/...` request path — the same one `_get_json` already uses elsewhere in `session.py` — reaches the events data without a new auth mechanism.
- The need is an anticipated gap (no logged history for the failure classes this addresses), not a specific past incident where exec + logs were insufficient.

### Success Criteria

- Calling the new CLI flag / MCP tool against a real ArgoCD app with an active Warning event (e.g. a pod in `ImagePullBackOff`) returns that event's reason and message, matching what the ArgoCD UI's Events tab shows for the same app.
- Calling it against a healthy app with no Warning events returns an empty result, not an error.
- A test confirms `resolve()` raises the same disallowed-server failure `_check_allowed` produces elsewhere, before making any network call.

### Sources / Research

- ArgoCD server RPC `ListResourceEvents`: `server/application/application.go:908` (upstream `argoproj/argo-cd`) — the endpoint this feature calls.
- ArgoCD web UI's Events tab: `ui/src/app/shared/services/applications-service.ts:474-500` (upstream `argoproj/argo-cd`) — confirms the UI calls `GET /applications/{name}/events`, both app-level and resource-scoped, which this feature mirrors at app level.
- Confirmed absent from the `argocd` CLI: no Kubernetes-Events wiring anywhere in `cmd/argocd/commands/app.go` (upstream `argoproj/argo-cd`) — every existing "event" reference there is the unrelated application-watch stream.
- `argocd_exec_mcp/session.py:304-313` (`resolve()`) — confirmed missing a `_check_allowed` call before its `_resource_tree` use, unlike `list_pods:134-135`, `get_project:171`, `check_prerequisites:218`, and `get_container_for_pod:274`, which all gate first.

---

## Planning Contract

### Key Technical Decisions

- KTD1. `list_events(app, server=None, include_normal=False)` lives in `session.py`, mirroring the `list_pods` split: one function, `cli.py` prints it, `mcp_server.py` returns it directly. Governs R3, R7.
- KTD2. Endpoint: `GET https://{server}/api/v1/applications/{app}/events?appNamespace=argocd`, matching `_resource_tree`'s query-style app-namespace scoping; no `resourceName`/`resourceNamespace`/`resourceUID` narrowing, consistent with the whole-app-scope product decision. Governs R1.
- KTD3. `list_events` gates with `server = server or default_server()` then `_check_allowed(server)` as its first two lines, before `token_for`/`_get_json` — the same order `list_pods` already uses. Governs R7.
- KTD4. `resolve()` fix: insert `_check_allowed(server)` immediately after `server = server or default_server()`, before its `_resource_tree` call — the narrowest possible fix, not a broader gating refactor (see Scope Boundaries). Governs R8.
- KTD5. CLI flag `--include-normal`; MCP parameter `include_normal: bool = False` — the same base name on both surfaces, matching the CLI/MCP name-parity precedent `open_session` already sets. Governs R2.
- KTD6. `--events` is wired as an early-return branch in `cli.py`'s `main()`, in the same position as `--check`/`--list-pods` (before the `resolve()` auto-fill block) — first-matching-flag-wins, the same precedence those two already have with each other. Governs R3.
- KTD7. The MCP `list_events` tool returns a small dict — `{"filter": "Warning"|"Warning,Normal", "count": N, "truncated": bool, "events": [...]}` — capped to the 50 most recent events by `lastTimestamp`. The `filter` and `truncated` fields carry per-call what a static docstring alone could not: what was actually filtered and whether anything was cut, for that specific response. The CLI path prints the raw event list, uncapped. Governs R5, R6.

### Assumptions

- The live event payload shape is assumed to match the standard Kubernetes Event object: `{"items": [{"type", "reason", "message", "involvedObject": {"kind", "name"}, "count", "lastTimestamp", ...}]}`, following the same wrapper-around-raw-k8s-objects precedent the `/manifests` endpoint already uses at `session.py:279-283`. This is inferred from ArgoCD's UI client calling the same endpoint, not from a live call against a running server — see Open Questions.
- Kubernetes Events are short-lived (roughly one hour, cluster-default TTL). An empty result does not mean nothing happened, only that nothing is within the current retention window. `list_events`'s docstring states this.

### Risks

- The `resolve()` bug is a credential-exposure risk, not a full bypass. `_resource_tree(app, server)` sends the bearer token to `server` over one authenticated GET before any gate fires, for a server the allow-list would reject. `get_container_for_pod`, called later in the same `resolve()` run, does gate and would eventually raise — but the token has already gone out over the wire by then.

### Open Questions

- **Deferred (non-blocking):** the live event payload shape (Assumptions, above) is inferred from a TypeScript client, not confirmed against a running ArgoCD server. Verify with one real call before relying on this in production; the filter logic itself is unit-testable now against a fabricated fixture and does not need to wait on that verification.
- **Deferred (non-blocking):** event sort order is left to the endpoint's own ordering; no client-side re-sort is required by this plan.

---

## Implementation Units

### U1. Add `list_events` to session.py

**Goal:** a gated, filterable events-fetching function other surfaces can call.
**Requirements:** R1, R2, R4, R7 (KTD1, KTD2, KTD3)
**Dependencies:** none
**Files:**
- `argocd_exec_mcp/session.py` (add `list_events`)
- `tests/test_events.py` (new)

**Approach:**
1. `server = server or default_server()`; `_check_allowed(server)`; then `token_for(server)`.
2. Build the URL per KTD2; call `_get_json(url, tok, 30)`, matching `_resource_tree`'s timeout.
3. Filter to `type == "Warning"` unless `include_normal=True`.
4. Return a `list[dict]` carrying the fields named in R4.

**Patterns to follow:** `_resource_tree` (`session.py:126-130`) for the URL and query-param shape; `get_container_for_pod` (`session.py:251-292`) for the gate-then-fetch order.

**Test scenarios:**
- Happy path: a fabricated `{"items": [...]}` fixture with one Warning and one Normal event returns only the Warning event by default.
- `include_normal=True` returns both events.
- No events (`{"items": []}`) returns an empty list, not an error.
- A disallowed server (`ARGOCD_EXEC_ALLOW_SERVERS` set, server not in it) raises `ValueError` before `urlopen` is ever called — assert the mock was not invoked, not only that the call raised. Ordering, not just outcome, is the thing under test.
- A malformed or missing `items` key follows one deliberate branch (empty list or explicit error), not an uncaught `KeyError`.

**Verification:** all five scenarios pass; `ruff`, `mypy`, and `bandit` are clean on the new function.

### U2. Fix `resolve()`'s missing allow-list gate

**Goal:** `resolve()` fails closed before any network call, like every sibling function.
**Requirements:** R8 (KTD4)
**Dependencies:** none (independent of U1)
**Files:**
- `argocd_exec_mcp/session.py` (one-line fix)
- `tests/test_resolve.py` (add a regression test)

**Approach:** Insert `_check_allowed(server)` immediately after `server = server or default_server()`, before the `_resource_tree(app, server)` call.

**Patterns to follow:** `list_pods` (`session.py:134-135`) — identical gate placement, applied one function later than it already is elsewhere.

**Test scenarios:**
- A disallowed server raises `ValueError` before `_resource_tree`/`urlopen` is called — assert non-invocation with a dedicated stub that raises if `_resource_tree` is called, following the `fail_if_called` pattern already used in `tests/test_check_prerequisites.py` (`test_applications_get_failure_stops_before_the_can_i_call`). The existing `stub_out_network` fixture is a plain swap-in with no call-tracking and cannot make this assertion as-is. A test that only checks `pytest.raises(ValueError)` already passes against the unfixed code, since `get_container_for_pod` raises downstream; this scenario must pin the ordering to be a real regression test, not a false-green one.
- All six existing `test_resolve.py` cases still pass unmodified — none of them set `ARGOCD_EXEC_ALLOW_SERVERS`, so the new gate is a no-op for them.

**Verification:** the new test fails against the pre-fix code and passes after the fix; the existing suite stays green.

### U3. Wire `--events` / `--include-normal` into the CLI

**Goal:** a human can run `argocd-exec --app <app> --events` (and `--include-normal`).
**Requirements:** R2, R3 (KTD5, KTD6)
**Dependencies:** U1
**Files:** `argocd_exec_mcp/cli.py`

**Approach:**
1. Add `--events` and `--include-normal` (both `action='store_true'`) to the parser, next to `--list-pods`.
2. Add an early-return branch calling `list_events(a.app, server, include_normal=a.include_normal)` and printing each event, positioned like the existing `--list-pods` branch, before the `resolve()` auto-fill block.

**Patterns to follow:** `cli.py:186-189` (the `--list-pods` branch) — same shape, one new flag.

**Test scenarios:** Test expectation: none beyond U1's coverage — a thin argparse-wiring layer over an already-tested function, the same treatment `--list-pods` itself gets in this codebase today (it has no dedicated CLI-level test either).

**Verification:** a manual run against a real app prints events; `--include-normal` visibly widens the result.

### U4. Wire the `list_events` MCP tool

**Goal:** an agent can call `list_events` the same way it already calls `list_pods`, and can tell from the response alone what filter applied and whether results were truncated.
**Requirements:** R2, R3, R5, R6 (KTD5, KTD7)
**Dependencies:** U1
**Files:**
- `argocd_exec_mcp/mcp_server.py`
- `tests/test_mcp_events.py` (new)

**Approach:**
1. Import `list_events` from `session.py`, aliased the same way `list_pods` already is.
2. Add `@mcp.tool() def list_events(app, server=None, include_normal=False) -> dict`, calling the shared function, sorting by `lastTimestamp`, and slicing to the 50 most recent.
3. Return `{"filter": "Warning,Normal" if include_normal else "Warning", "count": <len after cap>, "truncated": <True if more than 50 existed before slicing>, "events": [...]}`.
4. State in the docstring that the default is Warning-only and that the response's own `filter`/`truncated` fields (not just this docstring) describe what applied to a given call.

**Patterns to follow:** `mcp_server.py:25-28` (`list_pods`'s tool shape) for the thin-wrapper style. No existing tool in this repo returns a wrapper dict instead of a bare list; this is a deliberate, minimal deviation, justified by R5's per-call-legibility requirement.

**Test scenarios:**
- Happy path: a fixture with a handful of Warning events under the cap returns `truncated: False` and `count` matching the fixture length.
- `include_normal=True` is reflected in the returned `filter` field.
- A fixture with 60+ events returns exactly 50 in `events`, sorted most-recent-first by `lastTimestamp`, with `truncated: True`.

**Verification:** all three scenarios pass; a fixture with 60+ events confirms the cap holds at 50 and `truncated` reflects it.

### U5. Update documentation

**Goal:** the new flag and tool are documented where a user or agent would look.
**Requirements:** R9
**Dependencies:** U1, U3, U4
**Files:** `README.md`, `CHANGELOG.md`, `argocd_exec_mcp/cli.py` (epilog text only)

**Approach:**
1. Add `--events`/`--include-normal` to README's flag reference.
2. Correct README's MCP tool-count line — it already undercounts by omitting `list_pods`; add `list_events` at the same time.
3. Add a CHANGELOG entry.
4. Add an example to `cli.py`'s epilog.

**Test scenarios:** Test expectation: none — documentation only, no behavior change.

**Verification:** README and CHANGELOG mention `--events`, `--include-normal`, and `list_events`; the tool-count line matches the actual tool count.

---

## Verification Contract

| Command | Applies to |
| --- | --- |
| `.venv/bin/pytest -q --cov=argocd_exec_mcp --cov-report=term-missing` | U1, U2, U4 |
| `.venv/bin/ruff check .` | all units |
| `.venv/bin/mypy argocd_exec_mcp` | all units |
| `.venv/bin/bandit -r argocd_exec_mcp` | all units |

## Definition of Done

- All five units complete; `pytest`, `ruff`, `mypy`, and `bandit` are clean.
- U2's regression test demonstrably fails against the pre-fix code, confirming it is not a false-green test.
- README, CHANGELOG, and `cli.py`'s `--help` output all mention the new surface; no stale tool-count line remains.
- The Open Questions' live-payload-shape item is either confirmed against a real server or still explicitly flagged as unverified in the PR description — not silently dropped.
- No abandoned or experimental code from exploring the response-shape question remains in the diff.
