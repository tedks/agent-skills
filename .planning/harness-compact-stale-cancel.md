# Settle cancelled compaction operations

This ExecPlan is maintained under `.planning/PLANS.md`. It records the current implementation and evidence so another contributor can resume from this file and the repository alone.

## Purpose / Big Picture

An agent whose self-compaction is interrupted must be able to see the cancellation receipt and request compaction again. The October 16 incident left a Claude operation in `warning_dispatching` or `watching_other`, where `cancel_operation` refused it and all new requests were rejected. A disposable fixture and native Claude/Codex rehearsal demonstrate the repaired behavior without using a live lane.

## Progress

- [x] 2026-10-16: Read project and role instructions, inspect merged PR #7, claim `harness-compact-stale-after-intervening-cancel`, and create `fix/harness-compact-stale-cancel` from master.
- [x] 2026-10-16: Add the redacted incident fixture, cancellation/second-request regressions, and the terminal-cancel transition.
- [ ] Verify the new regression fails against the prior module and mutation-check transition guards.
- [ ] Run the source gates and disposable native test for Claude and Codex.
- [ ] Push a draft PR, run council review to a clean round, merge normally, install the dotfiles Nix snapshot, and verify installed code.

## Surprises & Discoveries

- The old observer deliberately waited in `watching_other` after sending the cancellation notice. That state continued to serialize new requests despite slash dispatch remaining `not_dispatched`.
- The native transcript records a root input turn but no authenticated origin for tmux-delivered text. Text-matching a purported control message cannot safely distinguish it from an operator's identical input.
- The Nix flake has no development shell with `mcp` on `python3`'s path. `nix flake check` is the authoritative package and test gate; the offline core suite can run through `nix develop --command python3`.

## Decision Log

- Decision: Treat an intervening cancel as terminal after its single notice receipt. Observe a peer compaction only before that decision; never reopen a terminal cancel if a later event appears. Rationale: a durable terminal receipt is needed to accept another request without a stale watch. Date/Author: 2026-10-16, Codex lead.
- Decision: Permit explicit cancellation in scheduled, accepted, idle-waiting, warning, and legacy watching states only when delivery evidence says the slash was never attempted. Rationale: never turn an attempted `/compact` into a safe cancellation. Date/Author: 2026-10-16, Codex lead.
- Decision: Peer messages should conceptually be excluded when they only direct compaction, but current records cannot prove sender origin. Count every native root input turn until an authenticated peer-delivery receipt is available; keep native metadata and tool results excluded. Rationale: a textual exemption could allow operator work to bypass cancellation. Date/Author: 2026-10-16, Codex lead.

## Outcomes & Retrospective

Pending gates, review, merge, installation, and report.

## Context and Orientation

`harness-control/harness_control.py` owns the private operation records, idle parser, detached observer, notice sender, cancellation, and request serialization. `harness-control/server.py` exposes them as MCP tools. `harness-control/test_harness_control_core.py` tests state transitions without a live pane; `harness-control/fixtures/compact-stale-cancel.json` carries the redacted incident state. `harness-control/test_compact_native.py` creates its own tmux server, disposable repository, and native Claude/Codex panes. The root `README.md` states the published behavior. A "sender lease" is the `.worker` file lock that prevents two observers from sending into one pane simultaneously.

## Plan of Work

Keep the existing single-operation serialization and sender lease. Change the intervening-turn final state from `watching_other` to `cancelled` after the notice, and remove the post-cancel watch. Allow explicit cancellation of pre-dispatch warning and legacy watch records, preserving an explicit cancel if a notice sender finishes concurrently. Make a concurrent-request refusal identify the operation and next action. Check a peer compaction event before deciding to cancel. Extend both offline and native tests and document the provenance limit.

## Concrete Steps

From `/home/tedks/Projects/agent-skills/harness-compact-stale-cancel`, run `nix flake check` and `nix build .#harness-control`. Run the offline core mutation check with `nix develop --command python3 harness-control/test_harness_control_core.py --mutation-check`. Run `harness-control/test_compact_native.py --harness both` with the Nix packaged Python environment or the flake's corresponding runtime. The native test retains a private `report.json` and destroys only its own tmux server. Review `git diff`, commit, push, and create a draft PR. The council review must reach a clean round before normal merge. In the dotfiles project, run `scripts/install/install-harness-control` after updating its reference and verify the installed Python environment resolves to the merged code.

## Validation and Acceptance

The incident fixture test must fail on the previous module and pass on this branch. The core test must show `warning_dispatching` accepts cancellation, the cancelled record has a reason, and a second request is accepted. The native test must show each harness completes ordinary compaction, cancels after an intervening root message with a notice receipt, then accepts a new request. The source package and repository checks must pass on the exact commit reviewed and merged. The installed server check must find the changed cancellation branch in the resolved store path.

## Idempotence and Recovery

The offline tests use temporary directories. The native test uses a newly created private root under `~/.local/state` and records its path in stdout; if it fails, inspect that report before rerunning. It kills only the tmux server created at that root. Do not access the incident's live pane or private state. If a push is uncertain, inspect the remote branch before retrying. If the installer fails, inspect the profile link and exact store closure before retrying.

## Artifacts and Notes

The issue is `harness-compact-stale-after-intervening-cancel`. The redacted incident operation ID and state live in the fixture; no transcript content or live state is copied into the repository. Gate results and final SHAs will be appended here as work finishes.

Revision 2026-10-16: Initial plan and observed implementation state, written after inspecting the merged dispatcher and the incident issue.
