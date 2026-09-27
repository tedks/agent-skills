---
name: retire-agent
description: Preserve a private, resumable agent handoff and verify a polite exit, or transfer the handoff to a freshly spawned successor.
---

# retire-agent

Use this for an explicitly authorized retirement, waiting handoff, or migration.
A saved handoff and an ended agent process are separate receipts. Preserve
human input and leave a session open when either receipt cannot be established.
Read [the shard and recovery contract](references/protocol.md) before invoking
the script. Linux `/proc`, tmux, Git, Python 3 and the installed spawn-agent
skill are required. The sender must provide the stable ordinary draft
co-submission contract from dotfiles PR #156, plus guarded typed Claude exit. Defaults: `~/Projects/off-duty` on `master`, origin private
on GitHub, and `~/Projects/orgs/master/doctrine/ROLES.md`.

## Modes

- `scripts/retire.sh %PANE --retire`: thank the agent, preserve its handoff,
  verify the private remote copy, then request exit.
- `scripts/retire.sh %PANE --waiting`: the same sequence, with the pending
  human decision and its owner named in the shard.
- `scripts/retire.sh %PANE --handoff AGENT MODEL`: retire, then spawn a fresh
  successor through spawn-agent's published launcher. Its brief is the shard
  plus the orgs doctrine's ROLES.md. Reconstruct state from artifacts alone
  and write the first durable record before acting.

Offer thanks and off-duty time to **every** agent, including Utility. An agent
may decline; the caller then writes the same operational shard with `author:
"caller"` and `declined: true`, honestly naming the source and uncertainty.
Do not invent an agent's own-word thanks. Personal thanks are separate from
operational recovery. Off-duty is not authorization to resume product work.

The published launcher supports explicit model selection for Claude and
Codex. agy model-specific handoffs fail before retirement because its shared
task delivery is unsupported; do not improvise a second spawn mechanism.

## Evidence and limits

Pin the explicit pane and verify its identity before each send. Use the shared
`tmux-message` sender for composer protection and new application receipts.
An ambiguous send never authorizes another paste. The sender observes input
twice eight seconds apart and co-submits ordinary stable drafts with the offer;
a stable command-shaped draft follows its published clear-and-verify exception.
The typed Claude exit requires an empty composer and defers for ordinary drafts. Changing input, a modal,
missing evidence, or changed process identity defers retirement.

Shards belong only in a private repository. Preserve one file per session,
serialize Git transactions, and verify the shard on the private remote before
recording its receipt and requesting exit. Keep operational recovery separate
from personal thanks. A provider-native resume handle comes last and must not
be required to reconstruct the work.

Record attempts privately without storing pane contents or unfinished human
text. Verify the agent process ended; an exit transport result alone is
insufficient. Never kill a session. Do not stop unrelated services or delete
worktrees, branches, transcripts, or another actor's files.

The wrapper directly `exec`s Python: pending or usage failure exits nonzero
(2), and only completed retirement exits zero. If piping output through `tail`
or another command, enable shell `pipefail` so the caller retains that status.
Use `--wait 1200` for a 10–20 minute shard; the maximum is 1800 seconds.
The script emits a private state path on failure. `--resume PATH` continues
observation after a delivered offer, without resending it. After an observed
decline, author the assigned shard then use `--resume PATH --caller-shard`.
Only a proven pre-paste deferral permits `--resume PATH --retry-deferred offer`
(or `exit`); ambiguous attempts require receipt inspection and remain blocked.
There is no automatic manual relay or ambiguous-send override.

Private state records and exact outgoing payloads live under the private
repository's Git common directory, not public session records. Do not copy
them into public issues or PRs. Screens are heuristic: this cannot atomically
exclude human typing, audit the truth of recovery prose, or independently
prove a resource owner's release claim.

## Repair verification

Run `scripts/test-retire.py` and `scripts/test-mutations.py` for isolated
behavior and guard-removal tests. Set `RETIRE_TEST_SHARED_SCRIPTS` to the
published spawn-agent `scripts` directory to verify a dependency checkout;
the default is this checkout's sibling spawn-agent.

`scripts/test-retire-native.py` is an opt-in model-token test. Pass `--worktree`
naming an exclusively owned, clean disposable Git directory, `--evidence`
naming a new private directory, and `--shared-skill` naming the published
spawn-agent installation. The cwd must be separate from the skill's source
worktree. The launcher grants Claude trust only to that exact disposable cwd. Codex
receives an exact-cwd project trust table through its documented per-process
`-c` option via the launcher's `--codex-bin` wrapper; no global config is written.
The test compares cwd HEAD/status/tracked diff on success and failure, samples
fresh exit notices, checks the actual local bare remote, and releases its owned
server. These checks do not provide an OS sandbox or prove absence of writes
elsewhere: real agents retain normal host access. The rehearsal prompt permits
only assigned identity/shard files and forbids live publication or pane control.
Artifacts are retained for inspection; source/shared heads and dirty state are
recorded separately as fidelity evidence.

`--stale-offer-draft` also exercises ordinary draft co-submission through the
published sender. `--baseline-script` runs the original history-discovery
failure against real Codex before checking the repaired flow. Observation
continues with `--resume` after a delivered offer exhausts its first minute;
it never resends that offer. Run without Python optimization so assertions
remain enabled. SIGTERM unwinds the owned server cleanup.
