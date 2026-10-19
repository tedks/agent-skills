# Publish off-duty handoffs without committing in a shared checkout

This ExecPlan is a living document maintained under `.planning/PLANS.md`.

## Purpose / Big Picture

Retirement can leave a shard on one machine while another process advances the private remote. A local file check can then claim handoff success even though the remote lacks the shard. After this change, callers publish named shard and receipt files from their own private staging directory through a disposable worktree based on fetched `origin/master`. A fetch and byte comparison establish the remote handoff before exit.

## Progress

- [x] (2026-10-19 02:20Z) Confirm assigned branch, read the issue, and deliver the lead acknowledgement.
- [x] (2026-10-19 02:24Z) Add offline helper tests, observe them fail before implementation, and implement bounded publication retry in draft PR #12.
- [x] (2026-10-19 02:28Z) Add a remote-presence regression test, observe its false positive, and require fetched remote commit and bytes.
- [x] (2026-10-19 02:31Z) Move retire-agent's publication files into per-event private state and add an index builder that runs on every retry.
- [x] (2026-10-19 02:33Z) Open draft orgs PR #31 with the retirement rule.
- [ ] Bring retirement and helper tests to green, including mutation checks.
- [ ] Run repository configuration and harness-control gates, review the full diff, and complete council review to fixpoint.
- [ ] Push both PR heads, record issue status, and prepare a private retirement shard through the helper.

## Surprises & Discoveries

The current shared sender requires a measured Claude composer with a nonbreaking space after its prompt glyph. The retire-agent fixture used an ordinary space, so Claude fixture tests failed even against the previous retire-agent implementation. The fixture now renders the measured prompt shape.

The old retirement tests also expected canonical files and the generated index to appear in the shared checkout. Those expectations must move to the private staging directory or fetched remote. The shared checkout's dirty files become negative assertions: they stay unchanged.

## Decision Log

- Decision: Keep each existing receipt state and publish each transition. Rationale: downstream readers already consume these states, and retaining them avoids a receipt contract change. Date/Author: 2026-10-19, off-duty write-path lead.
- Decision: Build the index inside each fresh temporary worktree after copying the event files. Rationale: a static index copied from an older checkout could erase a row added by a concurrent writer during a non-fast-forward retry. Date/Author: 2026-10-19, off-duty write-path lead.
- Decision: Keep private event staging files in the Git common directory for resume. Rationale: a failed publication must not strand a receipt in the shared checkout or lose the source bytes needed for an exact retry. Date/Author: 2026-10-19, off-duty write-path lead.

## Outcomes & Retrospective

In progress. The helper tests exercise a dirty and ahead shared checkout, path rejection, and a remote race that adds another index entry. The retirement happy path and drifted checkout regression pass; the larger retirement suite is being reconciled with the new source location.

## Context and Orientation

`skills/_shared/offduty-push.sh` is the publication entry point. Its `--repo` argument names an existing private Git working tree, `--source-root` names a caller-owned tree containing repository-relative files, and each positional file path is copied from that tree. By default it accepts only `waiting/`, `receipts/`, `lane-notes/`, and `writing/`; `--allow-path` is an explicit exception. The helper fetches `origin/master`, creates a detached worktree under XDG or TMPDIR scratch, commits only the requested files, and pushes `HEAD:master`. On a remote advance it removes that worktree and tries again, at most three times.

`skills/retire-agent/scripts/retire.py` controls the lifecycle. It stores mutable private state under the repository Git common directory. It now stages each event's shard and receipt under `<event>.publication/` there and calls the helper. `skills/retire-agent/scripts/build-index.py` regenerates the bounded section of `waiting/INDEX.md` in the temporary worktree on each attempt, preserving the steward's surrounding text.

`harness-control/harness_control.py` checks checkpoint evidence. A pushed-remote attestation now triggers a fetch and verifies that the named commit is on `origin/master` and that the remote shard bytes match the recorded SHA-256 digest. `skills/retire-agent/README.md` documents repair of an already drifted shared checkout. The orgs rule is a separate public documentation PR.

## Plan of Work

Maintain the helper's source and destination path guards and test each one against a throwaway bare repository. Keep all publication content outside the shared checkout and preserve it after a failed push. Verify a successful push by fetching and comparing Git blob IDs. Update retirement fixture assertions to distinguish private staging, the remote, and unrelated local shared content. Re-run focused tests after each adjustment, then run the complete skill, harness-control, and repository configuration gates. Review the exact final diff and repeat council review on any fix delta until clean.

## Concrete Steps

From this worktree, run `python3 skills/_shared/test-offduty-push.py`, `python3 skills/retire-agent/scripts/test-retire.py`, `python3 skills/retire-agent/scripts/test-mutations.py`, and the harness-control tests under `harness-control/`. Run `python3 tests/test_config.py` and `python3 tests/test_setup_leaks.py` for configuration. Use `bash -n skills/_shared/offduty-push.sh` and `git diff --check` before committing. The tests use temporary bare repositories and private tmux fixtures; they do not publish to the real off-duty remote.

## Validation and Acceptance

The helper test must show one pushed commit whose shard bytes match its source while the shared checkout's HEAD, dirty tracked file, staged local commit, and untracked file remain unchanged. A forced concurrent push must produce a remote history containing both writers' files and both generated index rows. The retirement test must show a shard and final receipt on the bare remote and no new `waiting/` or `receipts/` files in the shared checkout. A local-only checkpoint commit must be rejected; pushing it to the bare remote must make the same evidence verifiable.

## Idempotence and Recovery

The helper prints the existing remote commit when requested bytes are already present. A failed push keeps the caller's staging files and removes only the temporary worktree it created. Retirement resumes from its pinned private event state and verifies remote evidence before any exit send. The shared checkout recovery procedure requires a reviewed per-file disposition before a steward resets it.

## Artifacts and Notes

The first helper test run failed because the helper did not exist. The first checkpoint regression run failed because `verify_checkpoint` accepted a local-only commit. Both are explicit red-before-green evidence. The draft PRs are agent-skills #12 and orgs #31.

## Interfaces and Dependencies

The helper uses Bash, Git, and a caller-provided XDG or TMPDIR scratch directory. It accepts `--repo REPO --source-root ROOT FILE... -m MESSAGE`, with optional `--allow-path` and the retire-agent-owned `--build-index EXECUTABLE` hook. It prints one commit hash on stdout. The index builder accepts exactly one argument, the temporary worktree path. Python 3.11 or later is required by retire-agent and its tests. No real network or private remote is needed for the local gates.

Plan update 2026-10-19: recorded the implemented architecture and the fixture compatibility finding so the current suite reconciliation can resume from this file alone.
