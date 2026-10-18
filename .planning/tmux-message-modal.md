# Narrow Claude modal detection to dialog frames

This ExecPlan follows `.planning/PLANS.md` and is a living record of the modal false-positive repair.

## Purpose / Big Picture

`tmux-message` must send into a live Claude composer even when a usage, update, or feedback-count footer is visible. It must defer on a real dialog. The visible proof is a fixture table, regression tests that fail before the repair, and a draft PR containing the exact tested commit.

## Progress

- [x] (2026-10-18) Read the Lead brief, role, binding, and issue; confirmed branch `tmux-message-modal` in the assigned worktree; started the ditz issue.
- [x] (2026-10-18) Started a private tmux server and scratch Claude 2.1.302 session. Isolated config reaches login before the trust prompt.
- [ ] Capture real ANSI screens for the specified footers and dialogs from scratch panes.
- [ ] Add fixture assertions and verify they fail on the old classifier.
- [ ] Narrow both senders' modal classification without changing exit codes or status contract.
- [ ] Run affected tests and repository checks; mutation-check the tests.
- [ ] Push a draft PR, review to fixpoint, and hand off to the CTO for merge.

## Surprises & Discoveries

- With `CLAUDE_CONFIG_DIR` and `XDG_STATE_HOME` in a scratch directory, interactive Claude 2.1.302 opens its login selector before the trust prompt. `--no-session-persistence` cannot be used in interactive mode. Capturing authenticated screens therefore needs a credential decision or an equivalent isolated authentication method.
- The existing `tmux-message.py` classifier searches any of the last 25 rows for `Esc to cancel`, so a usage-limit footer can become `modal` despite an intact composer. The legacy sender has a second, separate broad detector.

## Decision Log

- Decision: keep the scratch Claude session on a separate `tmux -L modal-fixture-751` server with cwd and state inside this worktree. Rationale: no control of live panes and no new shared session state. Date/author: 2026-10-18, Codex lead.
- Decision: do not copy OAuth credentials into scratch config without a ruling. Rationale: the brief explicitly stops at keys. Date/author: 2026-10-18, Codex lead.

## Outcomes & Retrospective

Pending.

## Context and Orientation

`skills/spawn-agent/scripts/tmux-message.py` classifies live input and receipts for the public sender. `claude-send.sh` is a legacy transport with its own `screen_state`. `claude-pane-detect.sh` shares Claude dialog/composer checks with the spawner. `claude-send-screen.py` parses composer content. Fixtures and tests live under `skills/spawn-agent/scripts/`. A modal is a Claude dialog, not arbitrary text that happens to contain a button phrase. An ambiguous frame remains deferred.

## Plan of Work

Capture each requested screen with `tmux capture-pane -e -p` in an isolated Claude Code 2.1.302 pane, naming each fixture with that version. Add a table-driven test for the public and legacy detectors, paired with a positive composer assertion so a negative modal check cannot pass vacuously. Confirm old behavior is red. Make the smallest shared frame classifier that considers box glyphs, choices, and known dialog text, and explicitly excludes the observed status footers. Unknown or mixed input must never permit paste. Run mutation checks by temporarily restoring the old detector or deleting the footer exclusions, then restore the implementation and run affected gates.

## Concrete Steps

Work in `/home/tedks/Projects/agent-skills/tmux-message-modal`. Use the private tmux socket label `modal-fixture-751` only for scratch Claude. Run `bash skills/spawn-agent/scripts/test-claude-send.sh`, `python3 skills/spawn-agent/scripts/test-tmux-message.py`, `bash skills/spawn-agent/scripts/test-tmux-message.sh`, and `python3 skills/spawn-agent/scripts/test-tmux-message-live.py`. Run the repository configuration checks identified by the repository's test scripts. Commit and push coherent milestones, then create a draft PR.

## Validation and Acceptance

Every captured footer must classify as a ready or occupied Claude composer according to its actual input, never as `modal`. Every captured dialog must classify as `modal`, with zero paste or Enter actions in the sender test. Removing the footer exclusions or weakening the frame test must make a regression test fail. The final gate commands must pass on the pushed SHA.

## Idempotence and Recovery

Tests must use private tmux or fake tmux state and never send to live panes. If an authentic fixture cannot be captured, record the missing condition and seek the named CTO's ruling before changing acceptance evidence. Retain an ambiguous message payload privately; do not replay it.

## Artifacts and Notes

The private scratch pane is `modal-fixture-751:fixture:0.0`; it is not a fixture yet. The first `MODAL-ACK` send to `%321` returned `mixed-input-after-paste`; a subsequent read showed the exact acknowledgement in the queued input with the Claude `ctrl+x ctrl+s to send now` hint. It has not been repasted.

## Interfaces and Dependencies

Keep the public `tmux-message.sh` JSON status, exit codes, and reason contract. A new reason for ambiguous dialog frames would require a product ruling. Python 3.11+, Bash, tmux 3.6a, and Claude Code 2.1.302 are installed on this host.

Revision 2026-10-18: Initial plan records the login-first discovery and the credential stop condition before modifying classifiers.
