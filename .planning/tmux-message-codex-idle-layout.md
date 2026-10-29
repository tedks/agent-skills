# Recognize idle Codex composers across footer variants

This ExecPlan follows `.planning/PLANS.md` and is the current-state record for MODAL-M2.

## Purpose / Big Picture

An idle Codex input should accept guarded `tmux-message` delivery even if the status footer changes wording or model. An unknown layout must still defer before input. The user can see the result in regression tests that classify scrubbed live Codex captures and a footer variant as ready, and in distinct diagnostic reasons for unknown layout and unstable recognized input.

## Progress

- [x] (2026-10-29) Recovered the private checkpoint, read Lead doctrine and issue, confirmed the old branch clean, and created `fix/tmux-message-codex-idle-layout` from `origin/master` at `f703451`.
- [x] (2026-10-29) Started and synced ditz `tmux-message-codex-idle-unsupported-layout`.
- [x] (2026-10-29) Inspected two read-only, scrubbed October 18 live-current captures. Their original incident panes are gone; both captures classify `empty` today and are not incident reproductions.
- [ ] Add scrubbed live fixtures with honest provenance and synthetic failing footer/stability regressions; observe red on base behavior.
- [ ] Make the smallest composer and reason change, validate mutation probes and skill/config tests.
- [ ] Push, open draft PR, review to Lead-tier council fixpoint, run final gates, merge normally, close/sync issue, and report `MODAL-CHECKPOINT` to `%321`.

## Surprises & Discoveries

- The October 18 captures are after the incidents: `%748` says Context 79% left and `%749` says 96%, not the reported 38%. The originals are unavailable; fixture names must preserve that distinction.
- `tmux-message.py:composer` requires a footer regex after a Codex prompt. `ready()` initializes `last_reason` to `unsupported-layout` and can time out for unknown samples or moving recognized samples under the same label. Output above the composer is already excluded from `Input.fingerprint`.
- Harness-control used `--max-wait 5 --stable-wait 3`, so a correctly recognized stable composer had a chance to pass. The saved operation records only the deferred reason, not raw screen samples; the exact incident failure mode cannot be proved retrospectively.

## Decision Log

- Decision: use the two scrubbed captures as real layout evidence but not as reproduced failures; construct a clearly synthetic footer variant to exercise the missing-footer branch. Rationale: the incident panes have ended and the captured states classify ready on current code. Date/Author: 2026-10-29, Codex Lead.
- Decision: keep the cursor and selected-choice guards when recognizing a new footer. Rationale: a quoted prompt or choice screen must not authorize input. Date/Author: 2026-10-29, Codex Lead.

## Outcomes & Retrospective

Pending implementation and review.

## Context and Orientation

Owned source is `skills/spawn-agent/scripts/tmux-message.py`; its `composer()` returns an `Input` and `Sender.ready()` waits for two stable samples. `skills/spawn-agent/scripts/claude-send-screen.py` is the related post-paste ownership detector. Tests live in `skills/spawn-agent/scripts/test-tmux-message.py` and `test-claude-send.sh`; fixtures live in `skills/spawn-agent/scripts/fixtures/claude-send/`. Private, already scrubbed evidence is under `/home/tedks/.local/state/org-briefs/20261018/modal-m2-evidence/` and must not be relabeled as incident captures.

## Assumptions and failure modes

- A real Codex composer has a flush-left `›` row containing the cursor; for empty input the placeholder is dim and the cursor is at x=2. The pane can have moving transcript rows above it.
- The footer below it can change model or wording. Footer text alone cannot prove a composer; a choice list or unrelated text below the prompt must defer.
- A malformed or truncated capture, cursor outside the composer, or change in draft text/cursor between observations is uncertain and must not cause paste or Enter.
- The caller can be malicious or stale: a quoted `›` in transcript, an approval menu, and an old payload in the input must not be treated as an empty composer.

## Plan of Work

First commit red tests using the two scrubbed real captures and a labeled synthetic footer variant, plus a readiness test showing the two timeout reasons. Run the focused tests against unmodified source and record the failure. Then adjust Codex composer recognition based on cursor and measured prompt structure while leaving modal and input ownership guards intact. Adjust the readiness reason only when the observed kind is known or unknown. Run focused tests, full changed-skill gates and repository config tests, then mutate the fix away to prove the regression tests are active. Push and open a draft PR. Review the whole PR and subsequent fix deltas with one native Codex Lead seat and available foreign Lead seats to a clean round.

## Concrete Steps

From `/home/tedks/Projects/agent-skills/tmux-message-codex-idle`, run `python3 skills/spawn-agent/scripts/test-tmux-message.py`, `bash skills/spawn-agent/scripts/test-claude-send.sh`, `bash skills/spawn-agent/scripts/test-tmux-message.sh`, `python3 skills/spawn-agent/scripts/test-tmux-message-live.py`, and `python3 tests/test_config.py`. The new focused test must fail before the implementation and pass after; no test may send to a live pane.

## Validation and Acceptance

An exact cursor in an idle Codex composer classifies `empty` with both captured footer shapes and the synthetic changed footer. A quoted prompt, choice row, truncated screen or mismatched cursor remains unknown/modal/occupied. A stable recognized composer reaches readiness; no stable recognized composer reports `unsupported-layout`, while a recognized composer that keeps changing reports `human-typing` or an explicit stability reason. Final gates run on the exact PR head reported for merge.

## Idempotence and Recovery

All tests use recorded or synthetic screens and local mocks. Re-running them does not send to live panes. Source changes are reversible by Git; keep the old merged branch and original private captures intact. Ditz mutations are checked before retry and synced separately from source push.

## Artifacts and Notes

Evidence: 2026-10-18 read-only `capture-pane -e -p` scrubbed captures, with placeholder transcript rows and preserved composer/footer. The fixture table in the PR must say “live current, after incident.”

## Interfaces and Dependencies

Keep `composer(Frame) -> Input` and `Sender.ready() -> (Frame, Input)` or `DeliveryError` contracts. Do not change the public JSON status or exit-code contract; only refine `reason` values when useful. `claude-send-screen.py` is a separate post-paste parser and should change only if a failing regression shows its ownership path is affected.

## Revision note

2026-10-29: Created after resuming the October 18 checkpoint; records the unavailable incident capture and the current base before edits.
