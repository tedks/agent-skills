# Make self compaction dispatch after native idle

This ExecPlan is a living document and follows `.planning/PLANS.md`. The current task is tracked as ditz issue `e33bd34f09f01e29d055f31df6d88591e07eda16` in the agent-skills metadata branch.

## Purpose / Big Picture

An agent that calls `self_compact` as its last tool action should receive a scheduled watcher receipt, finish its turn, then see the watcher type `/compact` into its own pane. Once a new native compaction event and pane marker are observed, the watcher sends one continuation containing the checkpoint path. If native idle or guarded delivery cannot be established, the pane receives one visible failure notice and the operation record explains what happened. This prevents a silent stalled lane with active obligations.

## Progress

- [x] (2026-10-16 06:15 UTC) Claimed an isolated agent-skills worktree and ditz issue. Read the two incident records without changing live panes or state.
- [x] (2026-10-16 06:20 UTC) Reproduced the Codex idle parser miss with sanitized fixtures from both incidents; the test failed before the parser fix.
- [x] (2026-10-16 06:30 UTC) Changed compact scheduling, the native idle bound, re-arming, failure notice, and transition history. Core unit tests and synthetic private tmux integration pass.
- [x] (2026-10-16 06:41 UTC) Disposable native Claude and Codex runs each completed compact, witnessed the pane marker and transcript event, delivered the continuation, released the owned tmux server, and left the disposable Git repository clean.
- [ ] Complete mutation checks and Nix quality gates at the exact branch head.
- [ ] Push a draft PR, run the provider council to a clean fixpoint, merge normally, sync and close the ditz issue, notify the dotfiles CTO, then file a private retirement shard.

## Surprises & Discoveries

The records `b353c001…` and `17f313…` ended idle observation at 155 seconds because 145 seconds of a 300 second operation were reserved for sending and compaction observation. Their native root `task_complete` events occurred 3.34 and 2.54 seconds after the request. The parser still held `turn=null`: those long turns began outside its bounded 8 MiB tail. A post-baseline completion whose `turn_id` equals `root_turn_id` is enough to identify the root turn when no newer input intervened.

Claude Code 2.1.301 emits `api-request-shape`, `api-request-blob`, and `api-request` metadata before a focus turn. The old parser classified these as unknown. Its pane shows `⎿ Compacted`, where the older observed row included a parenthetical summary hint. Both shapes must count as native completion markers. The native test initially exposed a further race: a deferred state was visible while the failure notice was still dispatching. The watcher now keeps that stage nonterminal.

The native Codex 0.163.0 `--no-daemon` launcher produced an unsupported blank startup screen in a disposable pane, with no paste attempted. A direct disposable Codex launch without that flag completed the full watcher path. The two real no-daemon incident records still exercise the root-completion parser regression. This difference is evidence about startup transport only; it does not prove no-daemon compaction end to end.

## Decision Log

- Decision: Treat `timeout_seconds` on `self_compact` as a per-turn native idle bound, defaulting to 3600 seconds, with a fresh sender budget after idle. Focus starts a new idle bound. Reason: the calling turn remains open at tool return, so a sender reserve cannot also be the observation bound. Date/author: 2026-10-16, Codex lead.
- Decision: Keep exit and ordinary send budgets at their existing default of 300 seconds. Reason: this incident concerns self compaction and the exit contract has different safety semantics. Date/author: 2026-10-16, Codex lead.
- Decision: Let a compact watcher follow new root turns and reset its idle deadline, while retaining strict focus-turn matching and guarded sender checks. Reason: a new turn should not strand the scheduled compaction, but focus must still be the exact message delivered by this watcher. Date/author: 2026-10-16, Codex lead.
- Decision: A failure notice is attempted once only when `/compact` was definitely not dispatched. Ambiguous post-input delivery never uses the text “NOT DISPATCHED.” Reason: do not tell an agent to repeat a command that may already have been sent. Date/author: 2026-10-16, Codex lead.

## Orientation and work

`harness-control/server.py` defines the MCP tool default and its user-facing contract. `harness-control/harness_control.py` creates private operation records, starts a detached `setsid` watcher, reads native transcript rows, sends through the shared `tmux-message` transport, confirms a transcript boundary plus pane marker, and sends the continuation. `harness-control/test_harness_control_core.py` contains offline tests, mutation checks, and a private synthetic tmux integration. `harness-control/test_compact_native.py` is an opt-in real Claude/Codex rehearsal: it creates a unique private tmux server and disposable Git repository, retains a report, and kills only the server it created. The sanitized incident fixture is `harness-control/fixtures/compact-stalled-records.json`.

The operation record stores transitions for state, deadlines, delivery, completion, continuation, and failure notice. It stores no pane or conversation text in the transition list. The sender rechecks the pinned process, pane, session, and native idle before typing a slash command. Unknown or replaced transcript evidence remains a refusal or deferred result.

## Validation

From `/home/tedks/Projects/agent-skills/harness-compact-dispatch`, run `python3 -m unittest -q test_harness_control_core` in `harness-control/`, `python3 test_harness_control_core.py --mutation-check`, and `python3 test_harness_control_core.py --live-integration`. Run `nix flake check` and `nix build .#harness-control` from the repository root; the Nix check supplies the MCP Python dependency and runs the package tests. Run `python3 test_compact_native.py --harness claude` and `--harness codex` only on an owned test machine with available model tokens. Successful native results print `native compact and continuation confirmed` and retain `report.json` with `completed`, `delivered`, `confirmed`, `delivered`, `server_released: true`, and an empty disposable repository status.

The regression test for the two incident fixtures failed against the previous implementation (`busy_or_unknown` instead of `idle`). Mutation checks must kill a missing root completion fallback, a shortened idle bound, a disabled new-turn rearm, and a missing failure notice. Review the branch diff and rerun gates after any rebase or council fix.

## Outcomes & Retrospective

Implementation and native evidence are in hand. The implementation is committed at `32783ef`; Nix gates, review, merge, and handoff remain. No live affected pane or operation record was modified.
