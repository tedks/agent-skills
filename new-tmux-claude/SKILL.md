---
name: new-tmux-claude
description: Create a new tmux window with a Claude Code instance, optionally in a new git worktree
argument-hint: <name> <starting_prompt> [--worktree|-w]
---

# new-tmux-claude

Create a new tmux window with a Claude Code instance.

## Usage

```
/new-tmux-claude <name> <starting_prompt> [--worktree|-w]
```

## Arguments

- `<name>`: Name for the tmux window (and worktree branch if --worktree)
- `<starting_prompt>`: The prompt to pass to the new Claude instance
- `--worktree` or `-w`: Create a git worktree branched from current branch

## Instructions

When this skill is invoked, perform these steps:

### 1. Parse arguments

Extract from the skill args:
- `name`: first argument
- `starting_prompt`: second argument (may be quoted)
- `worktree_flag`: true if `--worktree` or `-w` appears anywhere in args

### 2. Validate environment

Run: `test -n "$TMUX" && echo "in-tmux" || echo "not-in-tmux"`

If not in tmux, report error and stop:
> Error: Not running inside tmux. This skill requires a tmux session.

### 3. If --worktree flag is set

Check for uncommitted changes:
```bash
git status --porcelain
```

If there is any output, report error and stop:
> Error: Uncommitted changes detected. Commit or stash changes before creating a worktree.

Get the current branch:
```bash
git branch --show-current
```

Create the worktree with a new branch:
```bash
git worktree add <name> -b <name>
```

The working directory for the new Claude will be the worktree path.

### 4. Create tmux window and start Claude

If worktree was created:
```bash
tmux new-window -n "<name>" -c "<worktree_path>" "claude '<starting_prompt>'"
```

If no worktree:
```bash
tmux new-window -n "<name>" "claude '<starting_prompt>'"
```

### 5. Report success

Tell the user:
- The tmux window name
- If a worktree was created, the path and branch name
- How to switch to it: `Ctrl-b <window-number>` or `tmux select-window -t <name>`

## Helper Scripts

Scripts bundled with this skill in `~/.claude/skills/new-tmux-claude/scripts/`:

### Multi-Agent Scripts

#### agent-query.sh (Piped Mode)

Query an agent non-interactively and get the response back:
```bash
~/.claude/skills/new-tmux-claude/scripts/agent-query.sh <agent> [options] <prompt>

# Agents: claude, codex
# Options:
#   -d, --dir <dir>      Set working directory
#   -m, --model <model>  Specify model

# Examples:
agent-query.sh claude "Explain this error"
agent-query.sh codex -d ./project "Review the auth module"
agent-query.sh codex --model o3 "Optimize this function"
```

Use this when you want to get another agent's opinion as a subagent - the response
comes back to the calling process.

#### agent-spawn.sh (Interactive Mode)

Spawn an agent in a tmux window for interactive work:
```bash
~/.claude/skills/new-tmux-claude/scripts/agent-spawn.sh <session:window> <agent> [directory] [prompt]

# Agents: claude, codex

# Examples:
agent-spawn.sh chaos:review claude ./project "Review auth module"
agent-spawn.sh chaos:codex-help codex . "Help me refactor this"
```

### Claude-Specific Scripts

#### claude-spawn.sh

Create a new tmux window with Claude (simpler interface for Claude-only):
```bash
~/.claude/skills/new-tmux-claude/scripts/claude-spawn.sh <session:window-name> [directory] [claude-args...]

# Examples:
claude-spawn.sh chaos:review           # New window in cwd
claude-spawn.sh chaos:review ./project # New window in ./project
claude-spawn.sh chaos:review . --resume abc123  # Resume session
```

#### claude-send.sh

Send a message to a running Claude instance:
```bash
~/.claude/skills/new-tmux-claude/scripts/claude-send.sh <window> <message>

# Example:
claude-send.sh chaos:review "run the tests"
```

This handles the timing issue where Enter gets swallowed if sent too quickly.
Uses a 1.5 second delay between text and Enter.

## Detecting Idle State

Claude Code has an `idle_prompt` notification hook that fires after 60+ seconds
of waiting for user input. To detect when claudes are ready for input:

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

2. Check for ready files:
   ```bash
   ls /tmp/claude-ready-* 2>/dev/null
   ```

3. Clear ready state after sending:
   ```bash
   rm -f /tmp/claude-ready-$session_id
   ```

Alternatively, session JSONL files in `~/.claude/projects/` contain all events
and can be parsed to determine state.

## Notes

- The new Claude instance runs interactively in the new window
- Use `tmux list-windows` to see all windows
- The starting prompt is passed as a positional argument to claude, not with -p, so it starts an interactive session
