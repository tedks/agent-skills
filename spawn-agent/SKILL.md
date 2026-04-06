---
name: spawn-agent
description: Spawn AI agents in tmux windows for parallel/interactive work
argument-hint: <session:window> <agent> [directory] [prompt]
allowed-tools: Bash(~/.claude/skills/spawn-agent/scripts/*)
---

# spawn-agent

Spawn AI agents in tmux windows for interactive, parallel work. Use this for
fan-out workflows where you want multiple agents working simultaneously.

## Usage

```
/spawn-agent <session:window> <agent> [directory] [prompt]
```

## Arguments

- `<session:window>`: tmux target (e.g., `chaos:review`)
- `<agent>`: The agent to spawn (`claude`, `codex`, `gemini`)
- `[directory]`: Working directory (default: current)
- `[prompt]`: Initial prompt for the agent

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

# Spawn Gemini
/spawn-agent chaos:gemini-review gemini ./project "Check this design"

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
```

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
- Use `tmux-info.sh` to see all sessions and windows on the current server
- Use claude-send.sh to send messages to running agents
- Socket detection is automatic -- you don't need to think about `-L` or `-S`
- Prompts are passed via temp files to avoid ARG_MAX limits
