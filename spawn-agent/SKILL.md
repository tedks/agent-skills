---
name: spawn-agent
description: Spawn AI agents in steerable tmux windows for parallel or long-running work, including transcript-preserving Codex session forks with step-scoped compaction and structured final-recap handoffs. Use when the user wants an interactive agent they can enter directly, asks to fork or clone the current Codex session, or wants a parent session to stay high-level while a child completes a step.
allowed-tools: Bash(~/.claude/skills/spawn-agent/scripts/*)
---

<!-- Codex reads this same file through the .codex/skills/<name> symlink, so the
     paths below assume install-claude-config ran. Never copy this file into
     .codex/skills -- see the skills comment in scripts/install/install-codex-config. -->

# spawn-agent

Spawn AI agents in tmux windows for interactive, parallel work. Use this for
fan-out workflows where you want multiple agents working simultaneously.

## Usage

```
/spawn-agent <session:window> <agent> [directory] [prompt]
```

## Arguments

- `<session:window>`: tmux target (e.g., `chaos:review`)
- `<agent>`: The agent to spawn (`claude`, `codex`, `agy`)
- `[directory]`: Working directory (default: current)
- `[prompt]`: Initial prompt for the agent
- `--fork-session <uuid>`: Interactively fork this Codex session while
  preserving its transcript; valid only with `codex`
- `--compact-prompt-file <file>`: Before delivering the task, run a manual
  compaction using this step-specific prompt; valid only with a Codex fork
- `--rollout-path-file <file>`: Atomically record the child rollout JSONL path
  so the parent can watch structured completion without reading tmux history
- `--recap-cursor-file <file>`: Atomically record the byte offset immediately
  before task delivery, excluding all inherited final answers from the watcher
- `--compact-timeout <seconds>`: Override the 600-second compaction wait

## Instructions

When this skill is invoked, perform these steps:

### 1. Validate environment

Run: `test -n "$TMUX" && echo "in-tmux" || echo "not-in-tmux"`

If not in tmux, report error and stop:
> Error: Not running inside tmux. This skill requires a tmux session.

### 2. Check tmux context

Run `~/.claude/skills/spawn-agent/scripts/tmux-info.sh` to see the current
socket, sessions, and windows. This ensures you're targeting the right server.

### 3. Spawn the agent

**Always pass the prompt via a temp file** to avoid ARG_MAX errors:

```bash
# 1. Write prompt to a temp file
prompt_file=$(mktemp /tmp/spawn-agent-prompt.XXXXXX)
cat << 'PROMPT_DELIM' > "$prompt_file"
<your prompt here>
PROMPT_DELIM

# 2. Spawn with --prompt-file
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> <agent> [directory] --prompt-file "$prompt_file"
# Note: the script cleans up its own temp files; caller-provided files are preserved
rm -f "$prompt_file"
```

For short prompts, inline is also fine:

```bash
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> <agent> [directory] [prompt]
```

The script automatically detects the tmux socket from `$TMUX` -- no manual
`-L` or `-S` flags needed.

### Fork one Codex step and hand back a recap

Use this workflow to keep the parent session at executive level while the user
can steer a context-rich child directly:

1. Choose a **new recap marker that has never appeared in the inherited
   conversation**.
2. End the child prompt with a contract requiring its last response to begin
   with that marker and recap decisions, changes, verification, and remaining
   work. State that the user may steer the child in tmux.
3. For a large inherited session, **use step-specific compaction by default**.
   Write a compact prompt that names the durable product decisions and current
   step state to preserve, and repetitive logs or obsolete launch attempts to
   discard. Omit compaction for a short parent or when verbatim history matters.
4. Spawn `codex` with `--fork-session`, `--rollout-path-file`, and, when
   appropriate, `--compact-prompt-file`. This invokes interactive `codex fork`,
   not headless `codex exec fork`.
5. Report the window and socket immediately so the user can enter it.
6. Block with `codex-jsonl-watch.py recap` from the recorded byte cursor. It
   follows only complete JSONL records and emits only the whole matching final
   message. Do not poll or copy tmux scrollback and do not relay routine child
   messages through the parent.

```bash
set -e
step_dir=$(mktemp -d /tmp/spawn-agent-step.XXXXXX)
prompt_file="$step_dir/prompt"
compact_file="$step_dir/compact-prompt"
rollout_file="$step_dir/rollout-path"
cursor_file="$step_dir/recap-cursor"
marker="STEP-1-7f3a DESIGN COMPLETE — EXECUTIVE RECAP"

{
cat <<'PROMPT_DELIM'
Design the requested system. The user may enter this tmux window and steer you.

Your LAST action must be a final response beginning exactly:
PROMPT_DELIM
printf '%s\n\n' "$marker"
cat <<'PROMPT_DELIM'

Include decisions, risks, verification, remaining work, and the recommended
next step. Do not merely point to files or prior messages.
PROMPT_DELIM
} > "$prompt_file"

cat > "$compact_file" <<'COMPACT_DELIM'
Preserve the product gestalt, settled architecture and UX decisions, current
repository/worktree state, active step goal, user steering, and recap contract.
Compress repeated terminal captures, superseded launch attempts, and other
operational noise that is not needed to complete this step.
COMPACT_DELIM

~/.claude/skills/spawn-agent/scripts/agent-spawn.sh \
  ide:design codex ./project \
  --fork-session 01a0702a-7b5e-71e0-bb62-287d55a7f9ba \
  --compact-prompt-file "$compact_file" \
  --rollout-path-file "$rollout_file" \
  --recap-cursor-file "$cursor_file" \
  --prompt-file "$prompt_file"

read -r rollout < "$rollout_file"
read -r recap_after < "$cursor_file"
~/.claude/skills/spawn-agent/scripts/codex-jsonl-watch.py recap \
  "$rollout" "$marker" 7200 --after-byte "$recap_after"
```

The fork is a snapshot taken when it starts. Later parent messages do not flow
into it; steer the child in its tmux window. Do not use `--resume`: resuming
would append to and contend with the parent instead of creating a child.

Codex `/compact` accepts no inline argument. The launcher supplies the
step-specific policy through the process-local
`experimental_compact_prompt_file` config override, submits bare `/compact`,
and waits for the rollout's `context_compacted` event before it places the task
in the composer. A missing event, vanished process, or startup modal fails with
the task unsent. The launcher never edits global Codex configuration.

This config/event sequence was integration-tested with `codex-cli 0.146.0`.
Re-run the real fork smoke when upgrading across a Codex release that changes
compaction configuration or rollout schemas.

Keep `compact_file` in place for as long as the child remains open: the Codex
process retains that file-backed config for any later compaction. After closing
the child window, remove `step_dir`.

### 4. Report success

Tell the user:
- The tmux window name and session
- Which tmux socket/server is being used
- How to switch to it: `Ctrl-b <window-number>` or `tmux select-window -t <name>`

## tmux Socket Awareness

All scripts automatically detect the current tmux server socket from `$TMUX`.
If you're in `tmux -L personal`, spawned agents will be in the same server.

**How it works:**
- `$TMUX` contains `/path/to/socket,pid,pane` when inside tmux
- Scripts extract the socket path and use `tmux -S <path>` for all commands
- Falls back to the default socket when not inside tmux

**Override manually** (rarely needed):
```bash
# By socket path
SPAWN_TMUX_SOCKET=/tmp/tmux-1000/personal ~/.claude/skills/spawn-agent/scripts/agent-spawn.sh ...

# By -L label
SPAWN_TMUX_LABEL=personal ~/.claude/skills/spawn-agent/scripts/agent-spawn.sh ...
```

**Quick diagnostic:**
```bash
~/.claude/skills/spawn-agent/scripts/tmux-info.sh
```
This prints the current socket, server PID, all sessions, and all windows.

## Examples

```bash
# Spawn Claude to review code
/spawn-agent chaos:review claude ./project "Review the auth module"

# Spawn Codex for a different perspective
/spawn-agent chaos:codex-review codex ./project "Help refactor this"

# Spawn agy (Antigravity CLI)
/spawn-agent chaos:agy-review agy ./project "Check this design"

# Fork a Codex session so the child inherits its full conversation
/spawn-agent chaos:design codex ./project --fork-session <uuid> "Design step one"

# Recommended for a large inherited session: compact first and expose JSONL
/spawn-agent chaos:design codex ./project --fork-session <uuid> \
  --compact-prompt-file /tmp/step/compact-prompt \
  --rollout-path-file /tmp/step/rollout-path \
  --recap-cursor-file /tmp/step/recap-cursor \
  --prompt-file /tmp/step/task-prompt

# Spawn without initial prompt (interactive from start)
/spawn-agent chaos:helper claude .
```

## Helper Scripts

All scripts source `tmux-ctx.sh` for automatic socket detection.

### tmux-info.sh

Show current tmux context -- socket, sessions, windows:
```bash
~/.claude/skills/spawn-agent/scripts/tmux-info.sh
```

### agent-spawn.sh

Spawn any supported agent:
```bash
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> <agent> [directory] [prompt]
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> <agent> [directory] --prompt-file <file>
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> codex [directory] --fork-session <uuid> --prompt-file <file>
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> codex [directory] --fork-session <uuid> --compact-prompt-file <file> --rollout-path-file <file> --recap-cursor-file <file> --prompt-file <file>
```

For Codex and agy, the script identifies the live composer before sending any
input. If an update, trust, or other startup dialog is present, it fails without
pasting or pressing Enter. Resolve the operator decision in that window, then
send the prompt explicitly or start again.

All Codex processes launched here receive the process-local override
`check_for_update_on_startup=false`, so managed agent windows do not stop at the
update chooser. This does not edit global config or suppress update checks in
ordinary Codex sessions. The modal detector remains a fail-closed backstop.

### codex-jsonl-watch.py

Wait for the last-response contract of a Codex fork and print only the matching
final message:

```bash
~/.claude/skills/spawn-agent/scripts/codex-jsonl-watch.py recap \
  <rollout-path-or-session-uuid> '<unique recap marker>' [timeout-seconds] \
  --after-byte <recorded-cursor>
```

Use a short, unique, single-line marker. The one-shot watcher follows only
newline-terminated records, retaining an incomplete trailing append until its
newline arrives. It requires a `response_item` whose payload is an assistant
message in phase `final_answer` and whose joined `output_text` begins with the
marker; then it prints that whole message exactly once and exits. Marker text
in the prompt, commentary, another role, or the middle of a final answer cannot
match.

The launcher records the cursor immediately before task delivery. Passing it
mechanically excludes inherited history even if an operator accidentally
reuses an old marker; the unique-marker contract remains defense in depth.

It accepts either the recorded rollout path or a Codex session UUID. UUIDs are
resolved below `$CODEX_HOME/sessions` (default `~/.codex/sessions`).

Whole-scrollback recap monitoring is deprecated. Tmux capture remains useful
for startup composer/modal readiness only; it is not a completion protocol.

### codex-rollout-path.sh

Resolve the rollout currently held open by the Codex process below a tmux pane:

```bash
~/.claude/skills/spawn-agent/scripts/codex-rollout-path.sh \
  [--exclude-session <parent-uuid>] <session:window>
```

Normal fork workflows should use `--rollout-path-file`, which calls this
Linux `/proc` helper, excludes the parent session, and records the answer
atomically before task delivery.

### claude-spawn.sh

Claude-specific spawner with additional options:
```bash
~/.claude/skills/spawn-agent/scripts/claude-spawn.sh <session:window-name> [directory] [claude-args...]

# Examples:
~/.claude/skills/spawn-agent/scripts/claude-spawn.sh chaos:review . --resume abc123  # Resume session
```

### claude-send.sh

Send a message to a running Claude instance:
```bash
~/.claude/skills/spawn-agent/scripts/claude-send.sh <window> <message>
~/.claude/skills/spawn-agent/scripts/claude-send.sh <window> --prompt-file <file>

# Example:
~/.claude/skills/spawn-agent/scripts/claude-send.sh chaos:review "run the tests"
```

Uses tmux load-buffer/paste-buffer for the message body (avoids ARG_MAX),
then sends Enter with a 1.5 second delay to avoid the swallowed-Enter issue.

## Detecting Idle State

Claude Code has an `idle_prompt` notification hook that fires after 60+ seconds
of waiting for user input. To detect when agents are ready:

1. Configure an `idle_prompt` hook in `.claude/settings.json`:
   ```json
   {
     "hooks": {
       "idle_prompt": [{
         "matcher": "",
         "hooks": [{
           "type": "command",
           "command": "touch /tmp/claude-ready-$CLAUDE_SESSION_ID"
         }]
       }]
     }
   }
   ```

2. Check for ready files: `ls /tmp/claude-ready-* 2>/dev/null`

3. Clear after sending: `rm -f /tmp/claude-ready-$session_id`

## Notes

- Agents run interactively in tmux windows
- `--fork-session` preserves the named Codex transcript in a new interactive
  session; `codex exec fork` is non-interactive and intentionally not used
- Use `--compact-prompt-file` by default for large inherited sessions; it gates
  task delivery on the structured Codex compaction-complete event
- Completion monitoring reads the rollout JSONL, never whole tmux scrollback
- Spawned Codex processes suppress startup update checks with a process-local
  override; normal Codex launches retain their usual update behavior
- Use `tmux-info.sh` to see all sessions and windows on the current server
- Use claude-send.sh to send messages to running agents
- Socket detection is automatic -- you don't need to think about `-L` or `-S`
- Prompts are passed via temp files to avoid ARG_MAX limits

## agy and the workspace trust prompt

The **first** interactive `agy` in a directory it has not been trusted with
opens a modal before the composer exists:

```
Do you trust the contents of this project?

Antigravity CLI requires permission to read, edit, and execute files here.

> Yes, I trust this folder
  No, exit

  ↑/↓ Navigate · enter Confirm
```

The default button is *Yes* and the footer is *enter Confirm*, so a blind
`Enter` grants trust — and answering it writes the directory into
`trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json`, permanently.

**The spawner never answers it.** When that modal is on screen the spawn fails
immediately, having sent no keystroke at all, and tells you to answer it
yourself. Granting a coding agent read/edit/execute over a directory is an
operator decision, not something a script should make on your behalf while you
are looking elsewhere.

So: attach to the session, answer the prompt, then re-run the spawn.

This is also why the readiness check waits for the composer *specifically* — its
rule line **and** its `? for shortcuts` hint — rather than for any box-drawing
character. The banner draws rules before the input box is live, and the modal
draws none at all; matching on rules alone pasted into whatever happened to be
on screen.
