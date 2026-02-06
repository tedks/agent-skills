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
- `<agent>`: The agent to spawn (`claude`, `codex`)
- `[directory]`: Working directory (default: current)
- `[prompt]`: Initial prompt for the agent

## Instructions

When this skill is invoked, perform these steps:

### 1. Validate environment

Run: `test -n "$TMUX" && echo "in-tmux" || echo "not-in-tmux"`

If not in tmux, report error and stop:
> Error: Not running inside tmux. This skill requires a tmux session.

### 2. Spawn the agent

Run the agent-spawn.sh script:

```bash
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> <agent> [directory] [prompt]
```

### 3. Report success

Tell the user:
- The tmux window name and session
- How to switch to it: `Ctrl-b <window-number>` or `tmux select-window -t <name>`

## Examples

```bash
# Spawn Claude to review code
/spawn-agent chaos:review claude ./project "Review the auth module"

# Spawn Codex for a different perspective
/spawn-agent chaos:codex-review codex ./project "Help refactor this"

# Spawn without initial prompt (interactive from start)
/spawn-agent chaos:helper claude .
```

## Helper Scripts

### agent-spawn.sh

Spawn any supported agent:
```bash
~/.claude/skills/spawn-agent/scripts/agent-spawn.sh <session:window> <agent> [directory] [prompt]
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

# Example:
~/.claude/skills/spawn-agent/scripts/claude-send.sh chaos:review "run the tests"
```

Handles the timing issue where Enter gets swallowed if sent too quickly
(uses 1.5 second delay between text and Enter).

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
- Use `tmux list-windows` to see all windows
- Use claude-send.sh to send messages to running agents
