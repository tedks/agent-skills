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
skill are required. Defaults: `~/Projects/off-duty` on `master`, origin private
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
An ambiguous send never authorizes another paste. Unknown input, a modal,
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
