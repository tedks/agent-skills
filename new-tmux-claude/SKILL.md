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

## Notes

- The new Claude instance runs interactively in the new window
- Use `tmux list-windows` to see all windows
- The starting prompt is passed as a positional argument to claude, not with -p, so it starts an interactive session
