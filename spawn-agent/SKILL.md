---
name: spawn-agent
description: Spawn AI agents in steerable tmux windows for parallel or long-running work, including transcript-preserving Codex session forks with step-scoped compaction and structured final-recap handoffs. Use to spawn interactive agents, steer running tmux agents, or fork a Codex session for a separate step.
allowed-tools: Bash(~/.claude/skills/spawn-agent/scripts/*)
---

<!-- Codex reads this same file through the .codex/skills/<name> symlink, so the
     paths below assume install-claude-config ran. Never copy this file into
     .codex/skills -- see the skills comment in scripts/install/install-codex-config. -->

# spawn-agent

Launch interactive agents and send live messages through the shared tmux
interface. Use actual message text; files may carry large prompts or optional
supporting reports. Agents can receive steering between tool calls without
waiting for their turn to finish.

## Launch

1. Run `scripts/tmux-info.sh` from this skill to confirm the socket and targets.
   Scripts use `$TMUX`; override with `SPAWN_TMUX_LABEL` or `SPAWN_TMUX_SOCKET`
   when operating from outside the intended server.
2. Use `scripts/agent-spawn.sh SESSION:WINDOW AGENT DIRECTORY [PROMPT]`.
   `AGENT` is `claude`, `codex`, or `agy`. For larger prompts pass
   `--prompt-file FILE`; the launcher preserves caller files.
3. Report the window, pane and socket. A successful task send reports transport
   success with receipt unverified. Inspect its new receipt through tmux before
   claiming delivery. If delivery fails, the child remains running: inspect it,
   rather than blindly creating another child or sending again.

```bash
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh \
  chaos:review codex ./project 'Review the auth module.'
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh \
  chaos:review claude ./project --prompt-file /tmp/review-prompt
# Interactive start without a task is supported for every harness.
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh chaos:helper agy ./project
```

Codex options: `--model MODEL`, `--codex-bin EXECUTABLE`, or environment
`SPAWN_CODEX_MODEL` / `SPAWN_CODEX_BIN`. Its startup update check is suppressed
for that process only. Claude `--trust` grants directory trust before launch;
read [Claude trust](references/claude-trust.md) when using it or diagnosing a
trust modal. Never answer a modal with a task's Enter.

## Send and read

Use `tmux-message` for running-agent messages and pane reads. The installed
alias is `~/.bin/tmux-message`; the skill-local entrypoint is
`~/.claude/skills/spawn-agent/scripts/tmux-message.sh`.

```bash
tmux-message read chaos:review
tmux-message send chaos:review 'MSG-unique Please prioritize the failing test.'
# A file is a way to pass the actual task bytes, not a filename-only message.
tmux-message send chaos:review --prompt-file /tmp/task-prompt
```

`send` defaults to submit-delay 8, render-wait 3, max-wait 10 seconds. The
launcher uses the same sender with a 30-second startup composer wait. Timing
options before the message override those defaults. Claude and Codex share the
measured baseline; add a different harness timing only with receipt evidence.
Agy launches interactively, but its task composer is not yet supported by the
shared guard. Unknown layouts defer; do not bypass the guard with a local fork.

`read` resolves the target to a stable pane and shows 200 history rows plus the
visible pane. `send` waits for an empty recognized composer, pastes once under a
shared pane lock, verifies its exact payload, then sends Enter once. Busy agent
output is allowed. No agent name, transcript, process registry, native message
queue or daemon is required for ordinary messaging.

Before reporting delivery, inspect a NEW exact submitted user-history row or
explicit pending-message row outside the composer. Transport exit 0,
disappearing input, and quoted output are insufficient. If the UI hides
acceptance, request an explicit acknowledgement and leave receipt unverified
until it arrives. Receipt and task completion are separate.

Exit 2 means deferred before paste; exit 3 means input may have changed and is
ambiguous. Preserve the private outgoing payload for deliberate recovery.
Never clear unrelated/mixed drafts, repaste an ambiguous attempt, or replay an
archived pending file. Inspect prior receipt evidence first. Ted authorizes a
bounded manual relay when needed: paste once into a verified empty agent input,
settle eight seconds, then Enter; or one Enter-only continuation for provably
exact own unsubmitted input. Recheck immediately before Enter and verify the
new receipt. Stop on unknown/mixed input or a modal. Screen polling cannot
atomically exclude concurrent human typing.

Read [transport details](references/tmux-messaging.md) for byte constraints,
UI limitations and retained-payload handling. The old `claude-send.sh` name is
an internal compatibility backend; examples and callers use `tmux-message`.

## Fork and completion

For `codex --fork-session UUID`, read [Codex forks](references/codex-forks.md).
It preserves inherited context and optionally compacts it for one step. The
launcher sends both `/compact` and the assignment through `tmux-message`.
Fork ancestry and optional structured completion watching remain Codex-specific
helpers; they are not prerequisites for sending live messages.

Use a unique final-recap marker to distinguish completion from acceptance.
`codex-jsonl-watch.py recap` is an optional structured completion watcher for a
known Codex fork. Live steering and receipt checks use tmux. Do not wait for an
idle hook or final response before delivering a message to an empty composer.

## Other helpers

- `tmux-info.sh`: socket and target inventory.
- `claude-spawn.sh`: interactive Claude launch with resume/CLI arguments;
  use `tmux-message` afterward to send a task.
- `claude-trust-dir.sh`: inspect/grant workspace trust and verify trust behavior.
- `codex-rollout-path.sh`: optional fork preparation and completion metadata.
- [Reusable tools](references/reusable-tools.md): surrounding orchestration.
