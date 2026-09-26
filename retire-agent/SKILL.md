---
name: retire-agent
description: Preserve a private, resumable agent handoff and verify a polite exit, or transfer the handoff to a freshly spawned successor.
---

# retire-agent

Use this for an explicitly authorized retirement, waiting handoff, or migration.
A saved handoff and an ended agent process are separate receipts. Preserve
human input and leave a session open when either receipt cannot be established.

## Modes

- `scripts/retire.sh %PANE --retire`: thank the agent, preserve its handoff,
  verify the private remote copy, then request exit.
- `scripts/retire.sh %PANE --waiting`: the same sequence, with the pending
  human decision and its owner named in the shard.
- `scripts/retire.sh %PANE --handoff AGENT MODEL`: retire, then spawn a fresh
  successor through spawn-agent's published launcher. Its brief is the shard
  plus the orgs doctrine's ROLES.md. Reconstruct state from artifacts alone
  and write the first durable record before acting.

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
